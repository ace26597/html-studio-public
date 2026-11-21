"""Opening the studio from another device (CONTRACT.md, "Access from other devices").

By default the studio listens on loopback only and nothing here applies. When extra
addresses are configured (STUDIO2_HOSTS, for example the Mac's Tailscale address) every
request that is not plainly local must carry the access cookie, which the person gets by
entering the access key once per device. The same guard wraps each project's preview port.

"Plainly local" is strict on purpose: a loopback client address is not enough, because a
tunnel or reverse proxy on this machine also connects from loopback. A request counts as
local only when its Host is a loopback name and it carries no forwarding header.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import ipaddress
import hashlib
import json
import logging
import re
import hmac
import html
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request as _FetchRequest, urlopen as _urlopen

log = logging.getLogger("studio2.access")

COOKIE = "studio2_access"
MAX_AGE = 30 * 24 * 3600
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
FORWARDED = (b"x-forwarded-for", b"x-real-ip", b"forwarded", b"cf-connecting-ip", b"x-forwarded-host",
             b"tailscale-user-login")
MIN_KEY = 24
# The host (with port) and scheme the current request was addressed to; project links are built from them.
REQUEST_HOST: contextvars.ContextVar[str | None] = contextvars.ContextVar("studio2_request_host", default=None)
REQUEST_SCHEME: contextvars.ContextVar[str] = contextvars.ContextVar("studio2_request_scheme", default="http")
# True when the address the request was sent to has another port than the studio listens on: a tunnel
# or proxy in between carries that one port, so "<host>:<project port>" leads nowhere from there.
REQUEST_MAPPED: contextvars.ContextVar[bool] = contextvars.ContextVar("studio2_request_mapped", default=False)
# The names project pages have behind such an address ({port: name}; under None a name with {port}).
REQUEST_PAGES: contextvars.ContextVar[dict | None] = contextvars.ContextVar("studio2_request_pages", default=None)


def token_for(key: str) -> str:
    return hmac.new(key.encode("utf-8"), b"studio2-access-v1", hashlib.sha256).hexdigest()


def host_name(host: str) -> str:
    """"100.101.102.103:8200" → "100.101.102.103"; "[::1]:8200" → "[::1]". A bracketed address
    must be the whole host (with at most a port): "[::1].evil.example" stays as it is, a name."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        inside, bracket, after = host[1:].partition("]")
        if bracket and (after == "" or re.fullmatch(r":\d{1,5}", after)):
            return f"[{inside}]"
        return host
    return host.rsplit(":", 1)[0] if ":" in host else host


def asked_port(host: str, scheme: str) -> int | None:
    """The port in the address the browser used: the one in `Host`, else the scheme's own."""
    rest = (host or "").strip()
    if rest.startswith("["):
        rest = rest.partition("]")[2]
    elif ":" not in rest:
        rest = ""
    else:
        rest = ":" + rest.rsplit(":", 1)[1]
    if not rest:
        return 443 if scheme == "https" else 80
    return int(rest[1:]) if re.fullmatch(r":\d{1,5}", rest) else None


def is_local(scope) -> bool:
    headers = dict(scope.get("headers") or [])
    client = (scope.get("client") or ("", 0))[0]
    if client not in ("127.0.0.1", "::1"):
        return False
    if any(name in headers for name in FORWARDED):
        return False
    return host_name(headers.get(b"host", b"").decode("latin-1")) in LOCAL_HOSTS


TAILNET = ipaddress.ip_network("100.64.0.0/10")     # the range Tailscale gives its devices


def on_tailnet(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in TAILNET
    except ValueError:
        return False


def is_tailnet_peer(scope) -> bool:
    """The connection itself comes from a Tailscale device (not through a proxy or tunnel on
    this machine, which would connect from loopback and carry a forwarding header)."""
    headers = dict(scope.get("headers") or [])
    client = (scope.get("client") or ("", 0))[0]
    return on_tailnet(client) and not any(name in headers for name in FORWARDED)


# -- names the studio answers to (DNS rebinding) ----------------------------------------------
# A page on a site someone else owns can point that site's name at this machine's address. The
# browser then sends `Host: their-name:8200` and treats the answers as their own page's. An
# address cannot be pointed elsewhere, so addresses pass; a name passes only when it is one the
# studio knows (CONTRACT.md, "Names the studio answers to").

def _bare(host: str) -> str:
    """"Mac.Example.:8200" → "mac.example"; "[::1]:8200" → "[::1]"."""
    return host_name(host).rstrip(".")


def is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name[1:-1] if name.startswith("[") and name.endswith("]") else name)
    except ValueError:
        return False
    return "." in name or ":" in name        # "2130706433" parses as an address, but a browser never sends it so


def name_allowed(host: str, names: frozenset[str]) -> bool:
    name = _bare(host)
    if not name:
        return False
    if is_address(name) or name == "localhost" or name.endswith(".localhost"):
        return True
    if name in names:
        return True
    first, _, rest = name.partition(".")
    return bool(first and rest) and f"*.{rest}" in names


def _bonjour_name() -> str | None:
    """A Mac's own network name (what other devices find it by as <name>.local)."""
    if sys.platform != "darwin":
        return None
    try:
        done = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


HOSTS_FILE = Path("/etc/hosts")


def _hosts_file_names(host: str) -> list[str]:
    """The names /etc/hosts lists on a line that also names this machine's host name (a Linux
    installer writes "127.0.1.1 box.lan box"). The machine's own file, not a DNS answer."""
    try:
        text = HOSTS_FILE.read_text("utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        fields = [f.lower().rstrip(".") for f in line.split("#", 1)[0].split()[1:]]
        if host in fields or host.split(".")[0] in fields:
            out.extend(f for f in fields if f and not is_address(f))
    return out


def machine_names() -> set[str]:
    """This machine's names from its own settings only. Never socket.getfqdn(): its answer can come
    from the network's DNS, which also picks the first part (it sees the name asked for)."""
    out: set[str] = set()
    host = (socket.gethostname() or "").strip().lower().rstrip(".")
    candidates = [host, *(_hosts_file_names(host) if host else [])]
    bonjour = _bonjour_name()
    if bonjour:
        candidates.append(bonjour.lower())
    for name in candidates:
        if name and not is_address(name):
            first = name.split(".")[0]
            out.update({name, first, f"{first}.local"})
    return out


def certificate_names(cert_file: str) -> set[str]:
    """The DNS names in the certificate: its alternative names, or its common name when it has none."""
    try:
        try:
            from cryptography import x509
        except ImportError:
            x509 = None
        if x509 is not None:
            with open(cert_file, "rb") as handle:
                cert = x509.load_pem_x509_certificate(handle.read())
            try:
                alt = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                names = alt.get_values_for_type(x509.DNSName)
            except x509.ExtensionNotFound:
                names = [a.value for a in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)]
        else:
            import ssl
            decoded = ssl._ssl._test_decode_cert(cert_file)     # the standard library's own reader
            names = [value for kind, value in decoded.get("subjectAltName", ()) if kind == "DNS"]
            if not decoded.get("subjectAltName"):
                names = [value for part in decoded.get("subject", ()) for key, value in part if key == "commonName"]
    except Exception as exc:    # an unreadable certificate also stops the TLS listener; say why here too
        print(f"Could not read the names in the certificate {cert_file}: {exc}", flush=True)
        return set()
    return {str(n).strip().lower().rstrip(".") for n in names if str(n).strip()}


def known_names(settings) -> frozenset[str]:
    """Every name this studio answers to besides addresses and localhost. Worked out once, at start."""
    names = {n.strip().lower().rstrip(".") for n in getattr(settings, "names", ()) if n.strip()}
    names |= {h.strip().lower().rstrip(".") for h in settings.hosts if h.strip() and not is_address(h.strip())}
    if settings.tls:
        names |= certificate_names(settings.tls[0])
    for port, name in (getattr(settings, "page_hosts", None) or {}).items():
        # A name with {port} in its first part counts as any one part in front of the rest.
        names.add(name if port is not None else "*." + name.split(".", 1)[1] if "{port}" in name.split(".", 1)[0] else name)
    names |= machine_names()
    names.discard("")
    return frozenset(names)


UNKNOWN_NAME_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Studio · unknown name</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; min-height: 100dvh; display: grid; place-items: center; font: 16px/1.5 system-ui, sans-serif;
       background: Canvas; color: CanvasText; }}
main {{ width: min(30rem, calc(100vw - 2.5rem)); display: grid; gap: .8rem; }}
h1 {{ font-size: 1.3rem; margin: 0; }} p {{ margin: 0; }}
code {{ font: .92em ui-monospace, monospace; overflow-wrap: anywhere; padding: .1em .3em; border-radius: 5px;
       background: color-mix(in srgb, CanvasText 9%, transparent); }}
</style></head><body><main>
<h1>This studio does not answer to that name</h1>
<p>The address used the name <code>{name}</code>. The studio only answers to its own names, so that
a website cannot point its name at this computer and use the studio.</p>
<p>If <code>{name}</code> is really this studio, start the studio with
<code>STUDIO2_NAMES={name}</code> (more names separated by commas).</p>
<p>The address <code>http://127.0.0.1:{port}/</code> always works on the computer the studio runs on.</p>
</main></body></html>"""


# -- a gate in front that signs people in ------------------------------------------------------
# An identity-aware proxy checks who the person is and passes the request on with a token it
# signed. The studio checks that signature itself, so a request that merely carries the header
# (from another program on this machine, say) gets nothing. CONTRACT.md, "Access from other devices".

def _fetch_keys(url: str) -> dict:
    with _urlopen(_FetchRequest(url, headers={"Accept": "application/json", "User-Agent": "studio2"}), timeout=5) as answer:
        return json.loads(answer.read(262144).decode("utf-8"))


class Gate:
    KEEP_KEYS, RETRY, LEEWAY, REMEMBER = 3600.0, 60.0, 60, 512

    def __init__(self, keys_url: str, audiences: tuple[str, ...], header: str = "Cf-Access-Jwt-Assertion",
                 issuer: str | None = None):
        parts = urlsplit(keys_url)
        self.keys_url, self.audiences = keys_url, list(audiences)
        self.header = header.strip().lower().encode("latin-1")
        self.issuer = issuer or f"{parts.scheme}://{parts.netloc}"
        self.fetch = _fetch_keys            # replaced in tests
        self.keys: dict[str, object] = {}
        self.fetched = self.tried = float("-inf")
        self.accepted: dict[str, float] = {}     # token → the time (wall clock) until which it needs no new check
        self.emails: dict[str, str] = {}       # an accepted token → the email it carried
        self.lock = asyncio.Lock()

    async def _key(self, kid: str):
        now = time.monotonic()
        stale = now - self.fetched > self.KEEP_KEYS
        if (kid not in self.keys or stale) and now - self.tried > self.RETRY:
            async with self.lock:
                if time.monotonic() - self.tried > self.RETRY:
                    self.tried = time.monotonic()
                    try:
                        import jwt
                        document = await asyncio.to_thread(self.fetch, self.keys_url)
                        keys = {}
                        for item in document.get("keys") or []:
                            if isinstance(item, dict) and item.get("kty") == "RSA" and item.get("kid"):
                                keys[str(item["kid"])] = jwt.PyJWK.from_dict(item, algorithm="RS256").key
                        self.keys, self.fetched = keys, time.monotonic()
                    except Exception as exc:     # no keys, no entry: the key sign-in still applies
                        log.warning("Could not fetch the gate's keys from %s: %s", self.keys_url, exc)
                        if stale:
                            self.keys = {}
        return self.keys.get(kid)

    def email(self, headers: dict) -> str:
        """The email in a token `accepts` has just passed ("" when it carried none)."""
        token = headers.get(self.header, b"").decode("latin-1").strip()
        return self.emails.get(token, "") if token in self.accepted else ""

    async def accepts(self, headers: dict) -> bool:
        token = headers.get(self.header, b"").decode("latin-1").strip()
        if not token or len(token) > 8192:
            return False
        until = self.accepted.get(token)
        if until is not None:
            if time.time() < until:
                return True
            self.accepted.pop(token, None)
        try:
            import jwt
            head = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
            if not isinstance(head, dict) or head.get("alg") != "RS256":
                return False
            key = await self._key(str(head.get("kid") or ""))
            if key is None:
                return False
            claims = jwt.decode(token, key=key, algorithms=["RS256"], audience=self.audiences, issuer=self.issuer,
                                leeway=self.LEEWAY, options={"require": ["exp", "aud", "iss"]})
        except Exception:
            return False
        if len(self.accepted) >= self.REMEMBER:
            self.accepted.clear()
            self.emails.clear()
        self.accepted[token] = min(float(claims["exp"]), time.time() + 60)
        email = claims.get("email")
        self.emails[token] = email.strip().lower() if isinstance(email, str) and "@" in email else ""
        # Through the server's own logger, so the line is in the studio's log beside the requests.
        logging.getLogger("uvicorn.error").info(
            "The gate signed in %s", claims.get("email") or claims.get("sub") or "someone (no email in the token)")
        return True


def is_owner(scope, settings) -> bool:
    """Whether this request is the studio owner's (CONTRACT.md, "The owner of a shared studio").
    A studio that is not shared has one person. On a shared one only the gate's signed email
    decides: through a tunnel every request arrives from this machine, a guest's too."""
    if not settings.shared:
        return True
    email = (scope.get("state") or {}).get("gate_email") or ""
    return bool(email) and email in (getattr(settings, "owners", ()) or ())


def gate_for(settings) -> Gate | None:
    wanted = getattr(settings, "gate", None)
    return Gate(*wanted) if wanted else None


def check_gate(gate: tuple | None) -> None:
    if not gate:
        return
    keys_url, audiences = gate[0], gate[1]
    if not keys_url or not audiences:
        raise SystemExit(
            "A gate in front needs both STUDIO2_GATE_KEYS (the address of its public signing keys) and "
            "STUDIO2_GATE_AUDIENCE (the audience its tokens name). Only one is set. Nothing was started.")
    if not keys_url.startswith("https://") or not urlsplit(keys_url).netloc:
        raise SystemExit("STUDIO2_GATE_KEYS must be an https:// address. Nothing was started.")


_DNS_NAME = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def page_hosts(text: str) -> dict:
    """STUDIO2_PAGE_HOSTS: "8403=site.example.com,*=p{port}.example.com" → {8403: "site.example.com",
    None: "p{port}.example.com"}. Anything else stops the server: a half-read list would send people
    to addresses that do not exist."""
    out: dict = {}
    for entry in (e.strip() for e in text.split(",")):
        if not entry:
            continue
        port, sep, name = (part.strip().lower().rstrip(".") for part in entry.partition("="))
        ok = bool(sep) and bool(name)
        if ok and port == "*":
            ok = "{port}" in name and bool(_DNS_NAME.fullmatch(name.replace("{port}", "1")))
            key = None
        elif ok:
            ok = port.isdigit() and 0 < int(port) < 65536 and bool(_DNS_NAME.fullmatch(name))
            key = int(port) if ok else None
        if not ok or key in out:
            raise SystemExit(
                f"STUDIO2_PAGE_HOSTS has an entry that cannot be read or comes twice: {entry!r}. Each entry is "
                "<port>=<name> or *=<name with {port}>, separated by commas. Nothing was started.")
        out[key] = name
    return out


def page_host_for(port: int, pages: dict | None) -> str | None:
    if not pages:
        return None
    if port in pages:
        return pages[port]
    pattern = pages.get(None)
    return pattern.replace("{port}", str(port)) if pattern else None


def cookie_value(scope) -> str | None:
    raw = dict(scope.get("headers") or []).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def preview_url_for(port: int) -> str:
    """The project's address as the current client must type it."""
    host = REQUEST_HOST.get()
    name = host_name(host) if host else "127.0.0.1"
    if not name or name in LOCAL_HOSTS:
        return f"http://127.0.0.1:{port}/"
    if REQUEST_MAPPED.get():
        # Reached through one mapped port: project ports are not carried. A project with a name of
        # its own there (STUDIO2_PAGE_HOSTS) is at that name; for the rest, say so at this address.
        page = page_host_for(port, REQUEST_PAGES.get())
        if page:
            return f"{REQUEST_SCHEME.get()}://{page}/"
        return f"{REQUEST_SCHEME.get()}://{host}/no-page/{port}/"
    return f"{REQUEST_SCHEME.get()}://{name}:{port}/"


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Studio · sign in</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; min-height: 100dvh; display: grid; place-items: center; font: 16px/1.5 system-ui, sans-serif;
       background: Canvas; color: CanvasText; }}
form {{ width: min(22rem, calc(100vw - 2.5rem)); display: grid; gap: .9rem; }}
h1 {{ font-size: 1.35rem; margin: 0; }} p {{ margin: 0; opacity: .75; }}
input, button {{ font: inherit; padding: .8rem .9rem; border-radius: 10px; border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }}
button {{ background: #4f46e5; color: #fff; border-color: transparent; font-weight: 600; }}
.error {{ color: #c0362c; opacity: 1; }}
</style></head><body>
<form method="post" action="/login">
  <h1>Studio</h1>
  <p>Enter the access key for this studio. This device is remembered for 30 days.</p>
  {error}
  <input type="password" name="key" autocomplete="current-password" placeholder="Access key" autofocus required>
  <input type="hidden" name="next" value="{next}">
  <button type="submit">Open the studio</button>
</form></body></html>"""


class AccessGuard:
    """ASGI wrapper. `studio_port` is where the sign-in page lives; `own_login` is true for
    the studio itself and false for a project's preview port (which sends people there)."""

    def __init__(self, app, key: str | None, studio_port: int, own_login: bool = True, open_tailnet: bool = False,
                 names: frozenset[str] = frozenset(), gate: Gate | None = None, pages: dict | None = None):
        self.app, self.key, self.studio_port, self.own_login = app, key, studio_port, own_login
        self.pages = pages                 # names of project pages behind a mapped address
        self.gate = gate                   # a gate in front whose signed token stands in for the key
        self.open_tailnet = open_tailnet   # the person chose "my tailnet is the boundary": no key for its devices
        self.names = names                 # the DNS names this studio answers to (known_names)
        self.token = token_for(key) if key else None
        self.failures: dict[str, list[float]] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        if not name_allowed(host, self.names):     # before anything else, loopback included
            return await self._unknown_name(scope, send, host, headers)
        marker = REQUEST_HOST.set(host or None)
        scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
        scheme_marker = REQUEST_SCHEME.set(scheme)
        mapped = self.own_login and asked_port(host, scheme) != self.studio_port
        mapped_marker = REQUEST_MAPPED.set(mapped)
        pages_marker = REQUEST_PAGES.set(self.pages)
        try:
            if is_local(scope) or (self.open_tailnet and is_tailnet_peer(scope)):
                return await self.app(scope, receive, send)
            if self.gate is not None and await self.gate.accepts(headers):
                # Who the gate signed in, for what belongs to a shared studio's owner (is_owner).
                scope.setdefault("state", {})["gate_email"] = self.gate.email(headers)
                return await self.app(scope, receive, send)
            if not self.key:  # not opened up for other devices at all
                return await self._plain(scope, send, 403, "This studio only answers on the machine it runs on.")
            if self.own_login and scope["type"] == "http" and scope["path"] == "/login":
                return await self._login(scope, receive, send)
            given = cookie_value(scope)
            if given and hmac.compare_digest(given, self.token):
                return await self.app(scope, receive, send)
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            wants_page = scope["method"] == "GET" and b"text/html" in headers.get(b"accept", b"")
            if not wants_page or scope["path"].startswith("/api/"):
                return await self._plain(scope, send, 401, "Sign in to this studio first.")
            here = f"{scheme}://{host}{scope['path']}"
            # On the studio's own port the sign-in page is at the address as given (behind a tunnel
            # that has no port); a project port sends people to the studio's port on the same host.
            place = host if self.own_login else f"{host_name(host)}:{self.studio_port}"
            login = f"{scheme}://{place}/login?next={quote(here, safe='')}"
            await send({"type": "http.response.start", "status": 302,
                        "headers": [(b"location", login.encode("latin-1")), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
        finally:
            REQUEST_HOST.reset(marker)
            REQUEST_SCHEME.reset(scheme_marker)
            REQUEST_MAPPED.reset(mapped_marker)
            REQUEST_PAGES.reset(pages_marker)

    async def _plain(self, scope, send, status: int, message: str):
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4403})
        body = ('{"error": {"code": "access_denied", "message": "%s"}}' % message).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def _unknown_name(self, scope, send, host: str, headers: dict):
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4421})
        name = _bare(host)[:200]
        if not name:
            status, code, message = 400, "no_name", "This request did not say which name it was sent to."
        else:
            status, code = 421, "unknown_name"
            message = (f"This studio does not answer to the name {name}. If that name is really this studio, "
                       f"start it with STUDIO2_NAMES={name}.")
        if name and b"text/html" in headers.get(b"accept", b""):
            body = UNKNOWN_NAME_PAGE.format(name=html.escape(name, quote=True), port=self.studio_port).encode()
            kind = b"text/html; charset=utf-8"
        else:
            body = json.dumps({"error": {"code": code, "message": message}}).encode()
            kind = b"application/json"
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", kind), (b"cache-control", b"no-store"), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _frame_policy(host: str) -> str:
        name = host_name(host)
        if not name or not all(c.isalnum() or c in ".-[]:" for c in name):
            return "frame-ancestors 'none'"
        return f"frame-ancestors 'self' https://{name} https://{name}:* http://{name} http://{name}:*"

    def _safe_next(self, target: str, host: str) -> str:
        parts = urlsplit(target or "")
        if parts.scheme in ("http", "https") and host_name(parts.netloc) == host_name(host):
            return target
        return "/"

    async def _login(self, scope, receive, send):
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        client = (scope.get("client") or ("?", 0))[0]
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        target, error, status = (query.get("next") or ["/"])[0], "", 200
        if scope["method"] == "POST":
            body = b""
            while len(body) < 8192:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            form = parse_qs(body.decode("utf-8", "replace"))
            target = (form.get("next") or ["/"])[0]
            now = time.monotonic()
            recent = [t for t in self.failures.get(client, []) if now - t < 300]
            if len(recent) >= 8:
                error, status = "Too many wrong attempts. Wait five minutes.", 429
            elif hmac.compare_digest((form.get("key") or [""])[0].strip().encode(), self.key.encode()):
                self.failures.pop(client, None)
                cookie = f"{COOKIE}={self.token}; Path=/; Max-Age={MAX_AGE}; HttpOnly; SameSite=Lax"
                if REQUEST_SCHEME.get() == "https":
                    cookie += "; Secure"
                await send({"type": "http.response.start", "status": 303, "headers": [
                    (b"location", self._safe_next(target, host).encode("latin-1")),
                    (b"set-cookie", cookie.encode()), (b"cache-control", b"no-store")]})
                return await send({"type": "http.response.body", "body": b""})
            else:
                self.failures[client] = [*recent, now]
                error, status = "That key is not right.", 401
        page = LOGIN_PAGE.format(error=f'<p class="error">{html.escape(error)}</p>' if error else "",
                                 next=html.escape(self._safe_next(target, host), quote=True)).encode()
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"),
            # May be framed only by a page on this same host (any port): the console tab on the same Mac.
            (b"content-security-policy", self._frame_policy(host).encode("latin-1"))]})
        await send({"type": "http.response.body", "body": page})


def check_settings(hosts: tuple[str, ...], key: str | None, open_tailnet: bool = False,
                   shared: bool = False) -> None:
    if shared:
        # Shared with other people: everyone signs in with the access key, on every address, and
        # the tailnet is not a boundary any more.
        if open_tailnet:
            raise SystemExit(
                "This studio is set up as shared with other people, so every device must sign in with the "
                "access key: STUDIO2_OPEN_TAILNET (no key for Tailscale devices) cannot be used with it. "
                "Nothing was started.")
        if not key or len(key) < MIN_KEY:
            raise SystemExit(
                "This studio is set up as shared with other people, so it needs an access key of at least "
                f"{MIN_KEY} characters (STUDIO2_ACCESS_KEY, or run `studio setup`). Nothing was started.")
        return
    if open_tailnet:
        # Without a key the only thing between a device and a shell on this machine is the
        # tailnet, so that mode is refused on any address that is not a Tailscale one.
        outside = [host for host in hosts if not on_tailnet(host)]
        if outside:
            raise SystemExit(
                "STUDIO2_OPEN_TAILNET lets Tailscale devices in without an access key, so every address in "
                f"STUDIO2_HOSTS must be a Tailscale address (100.64.0.0/10). Not one: {', '.join(outside)}. "
                "Nothing was started.")
        return
    if hosts and (not key or len(key) < MIN_KEY):
        raise SystemExit(
            "STUDIO2_HOSTS makes the studio reachable from other devices, so STUDIO2_ACCESS_KEY must be set "
            f"to a secret of at least {MIN_KEY} characters. Nothing was started.")
