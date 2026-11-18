"""The person's sign-in, carried into the studio's own browser (CONTRACT.md, "Seeing an app signed
in as the person").

The one place that decides what a browser context opened by the studio starts with: the
person's cookies and storage for a project (kept from their own browser through the preview
server), the agent's own sign-in kept from earlier looks, or nothing; and the one place that
decides where each request of such a context goes, hop by hop. Values live in memory only and
are never logged: no value kept here is ever put in a log line, an event, the project record, a
file or a tool result. What is written to disk is the names and attributes of the cookies a
project's own app sets (`app-cookies.json` beside the project record), never a value.
"""

from __future__ import annotations

import email.utils
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger("studio2")

SETTING = "Let the agent use my sign-in"
WHERE = "the project's menu (the More button at the top right, or the page menu on a narrow screen)"
REPORT_PATH = "/__studio/session"     # the bridge reports here; the preview server answers it itself
MARKER = "x-studio2-browser"          # sent by the studio's own browser to project page ports only
STORAGE_LIMIT = 64 * 1024             # a report's body, and the agent's kept sign-in
COOKIE_LIMIT = 16 * 1024
NAMES_LIMIT = 64                      # cookie names recorded per project
NAMES_FILE = "app-cookies.json"
SEEDED = "__studio2_seeded"           # sessionStorage: the person's entries were set in this tab
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
ACCESS_COOKIE = "studio2_access"      # access.COOKIE: the studio's own, never the app's
PAGE_LOADS = (b"document", b"iframe", b"frame")
SITES = (b"same-origin", b"same-site", b"none")
HOPS = 10                             # redirects followed for one sub-resource
DROPPED = ("content-length", "content-encoding", "transfer-encoding", "connection")


class ReportRefused(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Snapshot:
    """What the person's browser has for one project."""
    cookies: list[tuple[str, str]] = field(default_factory=list)   # only names the app set
    local: list[tuple[str, str]] = field(default_factory=list)
    session: list[tuple[str, str]] = field(default_factory=list)
    too_large: bool = False
    others: bool = False        # the browser also sent cookies the app never set (not kept)

    def empty(self) -> bool:
        return not (self.cookies or self.local or self.session)


@dataclass
class Kept:
    """The agent's own sign-in for one project, from the studio browser's earlier looks."""
    cookies: list[dict] = field(default_factory=list)
    local: list[tuple[str, str]] = field(default_factory=list)
    session: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Plan:
    """What one context opened by the studio starts with."""
    project_id: str | None = None
    origin: str | None = None          # http://127.0.0.1:<page port>
    person: Snapshot | None = None     # the person's sign-in is used
    kept: Kept | None = None           # the agent's own is used
    note: str | None = None
    keep: bool = False                 # what the browser holds afterwards becomes the agent's kept sign-in
    generation: int = 0                # of the project's kept state when the plan was made
    names: dict = field(default_factory=dict)   # cookie name → attributes the app set it with

    def fields(self) -> dict:
        """What a look's result says about it: never a value."""
        return {"signed_in": self.person is not None, "kept_sign_in": self.kept is not None,
                "sign_in_note": self.note}

    def cookies(self) -> list[dict]:
        if self.person is not None:
            return [_cookie(name, value, self.names.get(name)) for name, value in self.person.cookies]
        return [dict(item) for item in self.kept.cookies] if self.kept else []

    def storage_state(self) -> dict | None:
        local = self.person.local if self.person is not None else self.kept.local if self.kept else []
        cookies = self.cookies()
        if not cookies and not local:
            return None
        origins = [{"origin": self.origin, "localStorage": [{"name": k, "value": v} for k, v in local]}] if local else []
        return {"cookies": cookies, "origins": origins}

    def session_items(self) -> list[tuple[str, str]]:
        return self.person.session if self.person is not None else self.kept.session if self.kept else []


def _cookie(name: str, value: str, attributes: dict | None) -> dict:
    """A kept cookie as the app set it: its path, HttpOnly, SameSite and Secure (attributes the
    app's Set-Cookie gave; without a record, HttpOnly on path /)."""
    given = attributes or {}
    secure = bool(given.get("secure")) or name.startswith(("__Secure-", "__Host-"))
    same_site = {"strict": "Strict", "none": "None"}.get(str(given.get("same_site") or "").lower(), "Lax")
    if same_site == "None" and not secure:
        same_site = "Lax"
    return {"name": name, "value": value, "domain": "127.0.0.1", "path": given.get("path") or "/", "expires": -1,
            "httpOnly": bool(given.get("http_only", True)), "secure": secure, "sameSite": same_site}


def parse_cookies(header: str) -> list[tuple[str, str]]:
    """`a=1; b=2` → [(a, 1), (b, 2)], the first of a repeated name (the most specific path), never
    the studio's own access cookie."""
    out: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        name = name.strip()
        if not sep or not name or name == ACCESS_COOKIE or name in out:
            continue
        if any(c in name for c in " \t\r\n,;=") or any(c in value for c in "\r\n;"):
            continue
        out[name] = value.strip()
    return list(out.items())


def without_access_cookie(header: bytes) -> bytes:
    """A Cookie header without the studio's own access cookie (it is never an app's to see)."""
    parts = [p for p in header.split(b";") if p.strip().partition(b"=")[0].strip() != ACCESS_COOKIE.encode()]
    return b";".join(parts).strip()


def set_cookie_attributes(value: str, request_path: str) -> tuple[str, dict, bool] | None:
    """One Set-Cookie header → (name, {path, http_only, same_site, secure}, it deletes the cookie)."""
    parts = value.split(";")
    name, sep, cookie_value = parts[0].strip().partition("=")
    name = name.strip()
    if not sep or not name or any(c in name for c in " \t\r\n,;="):
        return None
    attrs = {"path": None, "http_only": False, "same_site": None, "secure": False}
    deletes = False
    for part in parts[1:]:
        key, _, given = part.strip().partition("=")
        key, given = key.strip().lower(), given.strip()
        if key == "path" and given.startswith("/"):
            attrs["path"] = given[:200]
        elif key == "httponly":
            attrs["http_only"] = True
        elif key == "secure":
            attrs["secure"] = True
        elif key == "samesite":
            attrs["same_site"] = given.lower()[:10] or None
        elif key == "max-age":
            try:
                deletes = deletes or int(given) <= 0
            except ValueError:
                pass
        elif key == "expires":
            try:
                deletes = deletes or email.utils.parsedate_to_datetime(given).timestamp() < time.time()
            except (TypeError, ValueError, IndexError, OverflowError):
                pass
    if attrs["path"] is None:      # RFC 6265 default-path: the request path up to its last "/"
        folder = request_path.rsplit("/", 1)[0] if request_path.count("/") > 1 else "/"
        attrs["path"] = folder or "/"
    return name, attrs, deletes


def path_covers(cookie_path: str, request_path: str) -> bool:
    """RFC 6265 path-match: would a cookie with `cookie_path` be sent for `request_path`."""
    if request_path == cookie_path:
        return True
    if request_path.startswith(cookie_path):
        return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"
    return False


def _pairs(given, limit: int = 2_000) -> list[tuple[str, str]]:
    if given is None:
        return []
    if not isinstance(given, list) or len(given) > limit:
        raise ReportRefused(400, "local and session must be lists of [key, value].")
    out = []
    for item in given:
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)):
            raise ReportRefused(400, "local and session must be lists of [key, value] strings.")
        if item[0] != SEEDED:
            out.append((item[0], item[1]))
    return out


def loopback(url: str) -> tuple[str, int] | None:
    """(host, port) when the URL is on this machine's loopback, else None."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https", "ws", "wss") or host not in LOOPBACK:
            return None
        return host, parts.port or (443 if parts.scheme in ("https", "wss") else 80)
    except ValueError:
        return None


class Sessions:
    def __init__(self, shared: Callable[[], bool], project: Callable[[str], dict | None],
                 on_port: Callable[[int], str | None], folder: Callable[[str], Path] | None = None,
                 own_names: Callable[[], frozenset] | None = None):
        self._shared = shared            # → is this a shared studio
        self._project = project          # project id → its record, or None
        self._on_port = on_port          # page port → project id, or None
        self._folder = folder            # project id → its folder (the cookie names are kept there)
        self._own_names = own_names or (lambda: frozenset())   # names and addresses this studio answers on
        self.secret = secrets.token_urlsafe(24)
        self._person: dict[str, Snapshot] = {}
        self._seen: set[str] = set()     # the person's browser sent something while it was not allowed
        self._kept: dict[str, Kept] = {}
        self._names: dict[str, dict] = {}         # project id → {cookie name → attributes}
        self._generation: dict[str, int] = {}     # bumped when what is kept for a project is forgotten

    # -- the setting ------------------------------------------------------------------------
    def allowed(self, project: dict | None) -> bool:
        """The effective setting for a project (CONTRACT.md, "The setting")."""
        if project is None or self._shared():
            return False
        value = project.get("signed_in_looks")
        return value if isinstance(value, bool) else not project.get("attach")

    def _allowed(self, project_id: str) -> bool:
        return self.allowed(self._project(project_id))

    # -- the cookies the project's app sets: names and attributes only ------------------------
    def names(self, project_id: str) -> dict:
        if project_id not in self._names:
            loaded = {}
            path = self._folder(project_id) / NAMES_FILE if self._folder else None
            try:
                given = json.loads(path.read_text("utf-8")) if path and path.is_file() else {}
                loaded = {str(k): v for k, v in given.items() if isinstance(v, dict)} if isinstance(given, dict) else {}
            except (OSError, ValueError):
                loaded = {}
            self._names[project_id] = dict(list(loaded.items())[:NAMES_LIMIT])
        return self._names[project_id]

    def note_set_cookies(self, project_id: str, scope, values: list[str]) -> None:
        """The app's answer passing through the preview server: record each cookie's name and
        attributes (never its value). A cookie the app deletes in an answer to the person's
        browser is dropped from what is kept of it."""
        if not values:
            return
        names, changed = self.names(project_id), False
        person = self._person.get(project_id)
        from_person = not self.from_studio_browser(dict(scope.get("headers") or []))
        for value in values:
            parsed = set_cookie_attributes(value, scope.get("path") or "/")
            if parsed is None or parsed[0] == ACCESS_COOKIE:
                continue
            name, attrs, deletes = parsed
            if names.get(name) != attrs and (name in names or len(names) < NAMES_LIMIT):
                names[name], changed = attrs, True
            if deletes and from_person and person is not None:
                person.cookies = [(n, v) for n, v in person.cookies if n != name]
        if changed and self._folder:
            self._write_names(project_id, names)

    def _write_names(self, project_id: str, names: dict) -> None:
        try:
            folder = self._folder(project_id)
            if not folder.is_dir():
                return
            temporary = folder / f".{NAMES_FILE}.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(names, indent=1), "utf-8")
            os.replace(temporary, folder / NAMES_FILE)
        except OSError:
            log.debug("could not record a project's cookie names")

    # -- what the person's browser sends ------------------------------------------------------
    def from_studio_browser(self, headers: dict) -> bool:
        given = headers.get(MARKER.encode())
        return given is not None and hmac.compare_digest(given, self.secret.encode())

    def note_request(self, project_id: str, scope) -> None:
        """A page load from the person's browser: take its cookies."""
        headers = dict(scope.get("headers") or [])
        if (headers.get(b"sec-fetch-dest") not in PAGE_LOADS or headers.get(b"sec-fetch-site") not in SITES
                or scope.get("method") not in ("GET", "HEAD") or self.from_studio_browser(headers)):
            return
        self._take_cookies(project_id, headers.get(b"cookie", b""), scope.get("path") or "/")

    def _take_cookies(self, project_id: str, raw: bytes, path: str) -> None:
        """Merge (CONTRACT.md, "What is kept, and where"): a cookie the app set is updated when the
        request carries it, dropped when the request's path is one it covers and the request does
        not carry it, and otherwise left as it was. Cookies the app never set are not kept."""
        if len(raw) > COOKIE_LIMIT:
            return                                   # not kept; what was kept before stays
        given = parse_cookies(raw.decode("latin-1"))
        if not self._allowed(project_id):
            if given:
                self._seen.add(project_id)
            return
        names = self.names(project_id)
        snapshot = self._person.setdefault(project_id, Snapshot())
        carried = dict(given)
        current = dict(snapshot.cookies)
        for name, attrs in names.items():
            if name in carried:
                current[name] = carried[name]
            elif path_covers(attrs.get("path") or "/", path):
                current.pop(name, None)
        snapshot.cookies = [(name, value) for name, value in current.items() if name in names]
        snapshot.others = any(name not in names for name in carried)

    def report(self, project_id: str, scope, body: bytes, too_large: bool) -> None:
        """The bridge's report (CONTRACT.md, "The report from the page"). Raises ReportRefused."""
        headers = dict(scope.get("headers") or [])
        if scope.get("method") != "POST":
            raise ReportRefused(405, "Only POST.")
        host = headers.get(b"host", b"").decode("latin-1").lower()
        origin = headers.get(b"origin", b"").decode("latin-1").lower()
        if not host or origin != f"{scope.get('scheme', 'http')}://{host}":
            raise ReportRefused(403, "Only the project's own page reports here.")
        if headers.get(b"sec-fetch-site", b"same-origin") != b"same-origin":
            raise ReportRefused(403, "Only the project's own page reports here.")
        if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
            raise ReportRefused(415, "Send JSON.")
        if self.from_studio_browser(headers):
            return                                   # the studio's own browser: never the person's
        if too_large:
            local = session = []
        else:
            try:
                given = json.loads(body or b"{}")
            except ValueError:
                raise ReportRefused(400, "Send JSON.") from None
            if not isinstance(given, dict):
                raise ReportRefused(400, "Send a JSON object.")
            local, session = _pairs(given.get("local")), _pairs(given.get("session"))
        self._take_cookies(project_id, headers.get(b"cookie", b""), REPORT_PATH)
        if not self._allowed(project_id):
            if local or session:
                self._seen.add(project_id)
            return
        snapshot = self._person.setdefault(project_id, Snapshot())
        snapshot.local, snapshot.session, snapshot.too_large = local, session, too_large

    # -- forgetting -------------------------------------------------------------------------
    def _bump(self, project_id: str) -> None:
        self._generation[project_id] = self._generation.get(project_id, 0) + 1

    def forget_person(self, project_id: str) -> None:
        """The option was switched off: what was kept of the person's browser goes at once."""
        self._bump(project_id)
        if self._person.pop(project_id, None):
            self._seen.add(project_id)

    def forget(self, project_id: str) -> None:
        self._bump(project_id)
        self._person.pop(project_id, None)
        self._kept.pop(project_id, None)
        self._names.pop(project_id, None)
        self._seen.discard(project_id)

    def clear(self) -> None:
        for project_id in {*self._person, *self._kept, *self._seen}:
            self._bump(project_id)
        self._person.clear()
        self._kept.clear()
        self._seen.clear()
        self._names.clear()

    def holds(self, project_id: str) -> dict:
        """For tests and the facts: whether anything is kept (never what)."""
        person = self._person.get(project_id)
        return {"person": bool(person and not person.empty()), "kept": project_id in self._kept,
                "seen": project_id in self._seen}

    # -- what a context starts with -----------------------------------------------------------
    def plan(self, url: str | None) -> Plan:
        """What a context opened for `url` starts with. Only a project's own loopback address gets
        anything; None gets nothing."""
        parts = urlsplit(url or "")
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
            return Plan()
        project_id = self._on_port(parts.port)
        if project_id is None:
            return Plan()
        plan = Plan(project_id=project_id, origin=f"http://127.0.0.1:{parts.port}",
                    generation=self._generation.get(project_id, 0), names=dict(self.names(project_id)))
        project = self._project(project_id)
        person = self._person.get(project_id)
        allowed = self.allowed(project)
        if allowed and person is not None and not person.empty():
            plan.person = person
            if person.too_large:
                plan.note = ("The person's browser holds more than 64 KB of stored data for this page, so only its "
                             "cookies were used; if the page still asks to sign in, say so to the person.")
            return plan
        plan.kept, plan.keep = self._kept.get(project_id), True
        if allowed and person is not None and person.too_large:
            plan.note = ("The person's browser holds more than 64 KB of stored data for this page, so it was not "
                         "kept and not used; if the page asks to sign in, say so to the person.")
        elif allowed and person is not None and person.others:
            plan.note = ("The person's browser sent cookies for this address that this project's own server never "
                         "set (set by the page's script, or by another app on the same host name); the studio does "
                         "not carry those, so this look is not signed in as the person. If the page asks to sign "
                         "in, say so to the person. Do not guess or make up credentials.")
        elif project_id in self._seen or (person is not None and not person.empty()):
            if self._shared():
                plan.note = ("The person's browser has a session for this app, but this studio is shared, so a "
                             "person's sign-in is never used in looks. Do not guess or make up credentials to get "
                             "past a sign-in; use only ones the person gives you.")
            else:
                plan.note = (f"The person's browser has a session for this app (cookies or stored data), but it was "
                             f"not used: the option “{SETTING}” is off for this project. It is in {WHERE}; ask the "
                             "person to switch it on if you need to see the app signed in. Do not guess or make up "
                             "credentials to get past a sign-in; use only ones the person gives you.")
        return plan

    def _where(self, url: str, own: tuple[str, int] | None) -> str:
        """Where a request goes: `own` (the project's page origin), `page` (a project page port of
        this studio under a loopback name), `loopback` (anything else on loopback), `blocked` (a
        project page port under a name or address the studio answers on besides loopback; the
        studio's browser reaches projects only through 127.0.0.1) or `outside`."""
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().rstrip(".")
            port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
        except ValueError:
            return "outside"
        if parts.scheme not in ("http", "https", "ws", "wss"):
            return "outside"
        if host in LOOPBACK:
            if own is not None and (host, port) == own:
                return "own"
            return "page" if self._on_port(port) is not None else "loopback"
        if self._on_port(port) is not None and host in self._own_names():
            return "blocked"
        return "outside"

    async def prepare(self, context, plan: Plan) -> None:
        """Decide every hop of the context's requests to this machine (CONTRACT.md, "How the
        studio's browser uses it"), and set the sessionStorage entries before the page's scripts."""
        own = loopback(plan.origin) if plan.origin else None
        secret, where = self.secret, self._where

        async def handle(route, request):
            first = where(request.url, own)
            if first == "blocked":
                return await route.abort("blockedbyclient")
            headers = {k: v for k, v in (await request.all_headers()).items()
                       if not k.startswith(":") and k.lower() not in ("cookie", MARKER)}
            if first == "own" and "text/event-stream" in headers.get("accept", ""):
                return await route.continue_()     # a stream cannot be held; nothing added to it
            try:
                await _follow(context, route, request, headers, own, secret, where)
            except Exception:  # noqa: BLE001 - a hop that cannot be made fails like a network error
                log.debug("a request of the studio's browser could not be made")
                await route.abort("failed")

        await context.route(lambda url: where(url, own) != "outside", handle)

        async def refuse(socket):
            await socket.close(code=1008, reason="The studio's browser opens sockets only to the project's own page.")
        await context.route_web_socket(lambda url: where(url, own) not in ("outside", "own"), refuse)
        items = plan.session_items()
        if items and plan.origin:
            await context.add_init_script(
                "(() => { try { if (location.origin !== %s) return; var s = window.sessionStorage;"
                " if (s.getItem(%s)) return; %s.forEach(function (e) { s.setItem(e[0], e[1]); });"
                " s.setItem(%s, '1'); } catch (e) {} })();"
                % (json.dumps(plan.origin), json.dumps(SEEDED), json.dumps([list(i) for i in items]), json.dumps(SEEDED)))

    async def open(self, browser, url: str | None, **options):
        """→ (context, plan): a new context for `url` with what it should start with (None: nothing)."""
        plan = self.plan(url)
        state = plan.storage_state()
        context = await browser.new_context(**options, **({"storage_state": state} if state else {}))
        try:
            await self.prepare(context, plan)
        except BaseException:
            await context.close()
            raise
        return context, plan

    async def keep(self, plan: Plan, context, page) -> None:
        """After a look without the person's sign-in: what the browser holds for the project
        becomes the agent's kept sign-in (CONTRACT.md, "What is kept, and where", 3). Not when
        the project was deleted, or what was kept for it forgotten, while the look ran."""
        if not plan.keep or plan.project_id is None:
            return
        try:
            state = await context.storage_state()
            session = []
            if page is not None and not page.is_closed():
                session = await page.evaluate(
                    "(origin) => { try { if (location.origin !== origin) return [];"
                    " return Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)]); }"
                    " catch (e) { return []; } }", plan.origin)
        except Exception:  # noqa: BLE001 - keeping is an extra; the look itself succeeded
            log.debug("could not keep the browser's sign-in for a project")
            return
        if (self._project(plan.project_id) is None
                or self._generation.get(plan.project_id, 0) != plan.generation):
            return
        cookies = [c for c in state.get("cookies") or [] if str(c.get("domain", "")).lstrip(".").lower() in LOOPBACK]
        local = [(i["name"], i["value"]) for o in state.get("origins") or [] if o.get("origin") == plan.origin
                 for i in o.get("localStorage") or []]
        session = [(k, v) for k, v in session if isinstance(k, str) and isinstance(v, str) and k != SEEDED]
        kept = Kept(cookies=cookies[:100], local=local, session=session)
        if not (kept.cookies or kept.local or kept.session):
            self._kept.pop(plan.project_id, None)
        elif len(json.dumps([kept.cookies, kept.local, kept.session])) > STORAGE_LIMIT:
            self._kept.pop(plan.project_id, None)
        else:
            self._kept[plan.project_id] = kept


async def _follow(context, route, request, headers: dict, own, secret: str, where) -> None:
    """Make one request of the studio's browser, every redirect hop decided here, never by the
    browser (it would follow a redirect itself with the context's cookies and the added header).

    The project's own origin is asked through the context (its cookies, and the answer's
    Set-Cookie kept in it) with the secret header; anything else without cookies, with the
    secret header only for a project page port, and its Set-Cookie dropped. A navigation that
    redirects becomes a page that moves the browser on, so the next hop is a request of its own
    and comes back through here; a sub-resource's redirects are followed here."""
    url, method, body = request.url, request.method, request.post_data_buffer
    navigation = request.is_navigation_request()
    for _hop in range(HOPS + 1):
        place = where(url, own)
        if place == "blocked":
            return await route.abort("blockedbyclient")
        if place == "own":
            sent = {**headers, MARKER: secret}
            cookie = await _cookie_header(context, url)
            if cookie:
                sent["cookie"] = cookie
            answer = await route.fetch(url=url, method=method, headers=sent, post_data=body, max_redirects=0)
            status, pairs, content = answer.status, answer.headers_array, await answer.body()
            pairs = [(p["name"], p["value"]) for p in pairs if p["name"].lower() != "set-cookie"]
        else:
            sent = {**headers, MARKER: secret} if place == "page" else dict(headers)
            sent = {k: v for k, v in sent.items() if k.lower() not in ("host", "content-length")}
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20.0) as client:
                reply = await client.request(method, url, headers=sent, content=body)
            status, content = reply.status_code, reply.content
            pairs = [(k, v) for k, v in reply.headers.multi_items() if k.lower() != "set-cookie"]
        location = next((v for k, v in pairs if k.lower() == "location"), None)
        pairs = [(k, v) for k, v in pairs if k.lower() not in DROPPED]
        if not (300 <= status < 400 and location):
            return await route.fulfill(status=status, headers=_headers(pairs), body=content)
        target = urljoin(url, location)
        if navigation:
            page = ("<!doctype html><meta http-equiv=\"refresh\" content=\"0;url=%s\"><script>location.replace(%s)"
                    "</script>" % (target.replace('"', "%22"), json.dumps(target).replace("</", "<\\/")))
            return await route.fulfill(status=200, headers={"content-type": "text/html; charset=utf-8",
                                                            "cache-control": "no-store"}, body=page)
        if status == 303 or (status in (301, 302) and method == "POST"):
            method, body = "GET", None
            headers = {k: v for k, v in headers.items() if not k.lower().startswith("content-")}
        url = target
    await route.abort("failed")


async def _cookie_header(context, url: str) -> str:
    """The Cookie header the browser itself would send to the project's own origin. Given
    explicitly: the context's own request client leaves out a `Secure` cookie on http, which the
    browser sends to 127.0.0.1 (a trustworthy origin), and an app reached over HTTPS sets those."""
    parts = urlsplit(url)
    host, path, now = (parts.hostname or "").lower(), parts.path or "/", time.time()
    chosen = []
    for cookie in await context.cookies():
        domain = str(cookie.get("domain") or "").lower()
        matches = host == domain.lstrip(".") or (domain.startswith(".") and host.endswith(domain))
        expires = cookie.get("expires", -1)
        if not matches or not path_covers(cookie.get("path") or "/", path) or (expires is not None and 0 <= expires < now):
            continue
        chosen.append(cookie)
    chosen.sort(key=lambda cookie: -len(cookie.get("path") or "/"))
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in chosen)


def _headers(pairs: list[tuple[str, str]]) -> dict:
    out: dict[str, str] = {}
    for key, value in pairs:
        out[key] = f"{out[key]}, {value}" if key in out else value
    return out
