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

import contextvars
import ipaddress
import hashlib
import hmac
import html
import time
from urllib.parse import parse_qs, quote, urlsplit

COOKIE = "studio2_access"
MAX_AGE = 30 * 24 * 3600
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
FORWARDED = (b"x-forwarded-for", b"x-real-ip", b"forwarded", b"cf-connecting-ip", b"x-forwarded-host",
             b"tailscale-user-login")
MIN_KEY = 24
# The host (with port) and scheme the current request was addressed to; project links are built from them.
REQUEST_HOST: contextvars.ContextVar[str | None] = contextvars.ContextVar("studio2_request_host", default=None)
REQUEST_SCHEME: contextvars.ContextVar[str] = contextvars.ContextVar("studio2_request_scheme", default="http")


def token_for(key: str) -> str:
    return hmac.new(key.encode("utf-8"), b"studio2-access-v1", hashlib.sha256).hexdigest()


def host_name(host: str) -> str:
    """"100.101.102.103:8200" → "100.101.102.103"; "[::1]:8200" → "[::1]"."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


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

    def __init__(self, app, key: str | None, studio_port: int, own_login: bool = True, open_tailnet: bool = False):
        self.app, self.key, self.studio_port, self.own_login = app, key, studio_port, own_login
        self.open_tailnet = open_tailnet   # the person chose "my tailnet is the boundary": no key for its devices
        self.token = token_for(key) if key else None
        self.failures: dict[str, list[float]] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        marker = REQUEST_HOST.set(host or None)
        scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
        scheme_marker = REQUEST_SCHEME.set(scheme)
        try:
            if is_local(scope) or (self.open_tailnet and is_tailnet_peer(scope)):
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
            login = f"{scheme}://{host_name(host)}:{self.studio_port}/login?next={quote(here, safe='')}"
            await send({"type": "http.response.start", "status": 302,
                        "headers": [(b"location", login.encode("latin-1")), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
        finally:
            REQUEST_HOST.reset(marker)
            REQUEST_SCHEME.reset(scheme_marker)

    async def _plain(self, scope, send, status: int, message: str):
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4403})
        body = ('{"error": {"code": "access_denied", "message": "%s"}}' % message).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
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
