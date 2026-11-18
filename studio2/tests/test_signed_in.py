"""Seeing an app signed in as the person (CONTRACT.md, "Seeing an app signed in as the person")."""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import threading

import httpx
import pytest

from studio2_server import session as signin
from studio2_server import tools
from studio2_server.look import Looker

needs_browser = pytest.mark.skipif(Looker().reason() is not None, reason="no cached Chromium")

COOKIE = "good-c00kie-7f3a9e"          # values that must never show up anywhere but the browser
TOKEN = "good-t0ken-41bd02"
SESSION_TOKEN = "good-sess-9c1e55"
SECRETS = (COOKIE, TOKEN, SESSION_TOKEN, "agent-made-5e2d")

APP = '''
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
PAGE = """<!doctype html><html><head><title>App</title></head><body><h1 id="who">%s</h1>%s
<p id="js"></p><form method="post" action="/login"><input name="name" id="name"><button id="go">Go</button></form>
<script>
var who = document.getElementById("who");
document.getElementById("js").textContent = "script sees: " + document.cookie;
if (who.textContent === "Sign in") {
  var t = localStorage.getItem("token"), s = sessionStorage.getItem("token");
  if (t && t.indexOf("good") === 0) who.textContent = "Welcome storage";
  else if (s && s.indexOf("good") === 0) who.textContent = "Welcome session";
}
</script></body></html>"""
class H(BaseHTTPRequestHandler):
    def record(self):
        with open("seen.jsonl", "a") as f:
            f.write(json.dumps({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}) + "\\n")
    def do_GET(self):
        self.record()
        if self.path == "/person-login":      # how the person's own sign-in sets its cookie
            self.send_response(303)
            self.send_header("Set-Cookie", "sid=" + open("person.txt").read().strip() + "; Path=/; HttpOnly; Secure")   # as an app reached over HTTPS sets it
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/hop"):
            self.send_response(302)
            self.send_header("Location", open("target.txt").read().strip())
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        c = SimpleCookie(self.headers.get("cookie") or "")
        sid = c["sid"].value if "sid" in c else ""
        who = "Welcome cookie" if sid.startswith("good") else "Welcome agent" if sid.startswith("agent") else "Sign in"
        pixel = open("pixel.txt").read().strip() if os.path.exists("pixel.txt") else ""
        extra = '<img src="%s" width="1" height="1">' % pixel if pixel else ""
        if os.path.exists("target.txt"):
            extra += '<img src="/hop-img" width="1" height="1"><script>fetch("/hop-fetch").catch(function () {})</script>'
        body = (PAGE % (who, extra)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        self.record()
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.send_response(303)
        if self.path == "/login":
            self.send_header("Set-Cookie", "sid=agent-made-5e2d; Path=/")
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


async def wait_for(predicate, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


async def with_app(studio, pid: str):
    workspace = studio.store.workspace(pid)
    (workspace / "app.py").write_text(APP)
    (workspace / "person.txt").write_text(COOKIE)
    (workspace / "studio.json").write_text(json.dumps({"run": "python3 app.py", "restart": "never"}))
    await studio.sync_app(pid)
    await wait_for(lambda: studio.app(pid).state == "running")
    return workspace


def seen(workspace) -> list[dict]:
    path = workspace / "seen.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def app_sets(studio, pid: str, *cookies: str) -> None:
    """As if the project's own app had answered with these Set-Cookie headers (no back end needed)."""
    studio.sessions.note_set_cookies(pid, {"path": "/", "headers": []}, list(cookies) or ["sid=x; Path=/; HttpOnly"])


async def person_signs_in(project) -> None:
    """The person signs in in their own browser: the app sets its cookie through the preview."""
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"] + "person-login", headers=page_load(""))


def page_load(cookie: str) -> dict:
    """What the person's browser sends when it loads the page in the studio's frame."""
    return {"cookie": cookie, "sec-fetch-dest": "iframe", "sec-fetch-site": "same-site", "accept": "text/html"}


def report_headers(origin: str, **extra) -> dict:
    return {"origin": origin, "content-type": "application/json", "sec-fetch-site": "same-origin", **extra}


async def look(studio, pid: str, **args) -> dict:
    return await tools.t_look_at_page(studio, pid, {"path": "/", "wait_ms": 200, **args})


# -- the setting ---------------------------------------------------------------------------------------

async def test_the_setting_is_on_for_the_studios_own_apps_and_off_for_a_brought_in_folder(client, studio, project):
    pid = project["id"]
    assert project["signed_in_looks"] is True
    studio.store.update(pid, attach={"mode": "copy", "path": "/tmp/somewhere", "kind": "folder"})
    assert (await client.get(f"/api/projects/{pid}")).json()["signed_in_looks"] is False
    switched = await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": True})
    assert switched.status_code == 200 and switched.json()["signed_in_looks"] is True
    assert studio.store.get(pid)["signed_in_looks"] is True          # remembered in the record
    off = await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": False})
    assert off.json()["signed_in_looks"] is False
    assert "Let the agent use my sign-in" in studio.agent_context(pid)


async def test_a_shared_studio_refuses_it_and_keeps_nothing(client, studio, project):
    pid = project["id"]
    studio.settings.shared = True
    try:
        assert (await client.get(f"/api/projects/{pid}")).json()["signed_in_looks"] is False
        refused = await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": True})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
        origin = project["preview_url"].rstrip("/")
        async with httpx.AsyncClient() as web:
            await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
            await web.post(origin + signin.REPORT_PATH, headers=report_headers(origin),
                           content=json.dumps({"local": [["token", TOKEN]]}))
        assert studio.sessions.holds(pid) == {"person": False, "kept": False, "seen": True}
        assert "shared" in studio.sessions.plan(f"http://127.0.0.1:{project['port']}/").note
    finally:
        studio.settings.shared = False


# -- the report, as an attacker would try it ---------------------------------------------------------------

async def test_the_report_takes_only_what_the_projects_own_page_sends(client, studio, project):
    pid, origin = project["id"], project["preview_url"].rstrip("/")
    app_sets(studio, pid)
    url = origin + signin.REPORT_PATH
    body = json.dumps({"local": [["token", TOKEN]], "session": [["s", SESSION_TOKEN]]})
    async with httpx.AsyncClient() as web:
        assert (await web.get(url, headers={"origin": origin})).status_code == 405       # never read back
        assert (await web.post(url, content=body, headers={"content-type": "application/json"})).status_code == 403
        other = report_headers("http://127.0.0.1:9", **{"sec-fetch-site": "same-site"})
        assert (await web.post(url, content=body, headers=other)).status_code == 403     # another project's page
        cross = report_headers(origin, **{"sec-fetch-site": "cross-site"})
        assert (await web.post(url, content=body, headers=cross)).status_code == 403
        plain = {**report_headers(origin), "content-type": "text/plain"}
        assert (await web.post(url, content=body, headers=plain)).status_code == 415
        assert (await web.post(url, content="[1]", headers=report_headers(origin))).status_code == 400
        bad = json.dumps({"local": [["token", 5]]})
        assert (await web.post(url, content=bad, headers=report_headers(origin))).status_code == 400
        studio_browser = report_headers(origin, **{signin.MARKER: studio.sessions.secret})
        assert (await web.post(url, content=body, headers=studio_browser)).status_code == 204
        assert studio.sessions.holds(pid)["person"] is False                             # the studio's own: ignored
        forged = report_headers(origin, **{signin.MARKER: "a-guess"})
        taken = await web.post(url, content=body, headers={**forged, "cookie": f"sid={COOKIE}; studio2_access=k"})
        assert taken.status_code == 204 and taken.content == b""
        held = studio.sessions._person[pid]
        assert held.cookies == [("sid", COOKIE)]                                          # never the studio's own
        assert held.local == [("token", TOKEN)] and held.session == [("s", SESSION_TOKEN)]
        too_big = json.dumps({"local": [["x", "y" * (signin.STORAGE_LIMIT + 10)]]})
        assert (await web.post(url, content=too_big, headers=report_headers(origin))).status_code == 204
        assert studio.sessions._person[pid].local == [] and studio.sessions._person[pid].too_large
        assert "64 KB" in studio.sessions.plan(f"http://127.0.0.1:{project['port']}/").note
        await web.post(url, content=too_big, headers={**report_headers(origin), "cookie": f"sid={COOKIE}"})
    plan = studio.sessions.plan(f"http://127.0.0.1:{project['port']}/")
    assert plan.person is not None and plan.person.cookies and "only its cookies" in plan.note


async def test_page_loads_from_the_persons_browser_keep_their_cookies(client, studio, project):
    pid = project["id"]
    app_sets(studio, pid)
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers={"cookie": f"sid={COOKIE}"})       # not a browser's page load
        assert studio.sessions.holds(pid)["person"] is False
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
        assert studio.sessions._person[pid].cookies == [("sid", COOKIE)]
        await web.get(project["preview_url"], headers={**page_load("sid=other"), signin.MARKER: studio.sessions.secret})
        assert studio.sessions._person[pid].cookies == [("sid", COOKIE)]                  # the studio's own browser
    # Switched off: forgotten at once, and not kept again while it is off.
    await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": False})
    assert studio.sessions.holds(pid) == {"person": False, "kept": False, "seen": True}
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
    assert pid not in studio.sessions._person
    plan = studio.sessions.plan(f"http://127.0.0.1:{project['port']}/")
    assert plan.person is None and "Let the agent use my sign-in" in plan.note
    assert "guess" in plan.note and "project's menu" in plan.note
    # Deleting the project forgets everything about it.
    await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": True})
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
    assert studio.sessions.holds(pid)["person"]
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 204
    assert studio.sessions.holds(pid) == {"person": False, "kept": False, "seen": False}


async def test_the_studio_stopping_forgets_everything(settings):
    from studio2_server.api import create_app
    application = create_app(settings)
    studio = application.state.studio
    await studio.start()
    project = await studio.create_project("x")
    app_sets(studio, project["id"])
    async with httpx.AsyncClient() as web:
        await web.get(f"http://127.0.0.1:{project['port']}/", headers=page_load(f"sid={COOKIE}"))
    assert studio.sessions.holds(project["id"])["person"]
    await studio.stop()
    assert studio.sessions.holds(project["id"]) == {"person": False, "kept": False, "seen": False}


def test_a_plan_is_made_only_for_a_projects_own_loopback_address():
    sessions = signin.Sessions(lambda: False, lambda pid: {"id": pid}, lambda port: "p_1" if port == 8201 else None)
    sessions._person["p_1"] = signin.Snapshot(cookies=[("sid", COOKIE)])
    assert sessions.plan("http://127.0.0.1:8201/x").person is not None
    for url in ("http://127.0.0.1:8202/", "http://localhost:8201/", "https://example.com/", "http://127.0.0.1.nip.io:8201/",
                None, "file:///etc/hosts"):
        plan = sessions.plan(url)
        assert plan.person is None and plan.storage_state() is None and plan.project_id is None


# -- in a real browser ---------------------------------------------------------------------------------

@needs_browser
async def test_a_look_sees_the_app_signed_in_only_when_the_person_allows_it(client, studio, project, caplog):
    caplog.set_level(logging.DEBUG)
    pid = project["id"]
    workspace = await with_app(studio, pid)
    origin = f"http://127.0.0.1:{project['port']}"
    first = await look(studio, pid)
    assert "Sign in" in first["text_start"] and first["signed_in"] is False and first["sign_in_note"] is None

    # A cookie session, from the person's own page load, after the app set it (HttpOnly).
    await person_signs_in(project)
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}; studio2_access=zzz"))
    signed = await look(studio, pid)
    assert "Welcome cookie" in signed["text_start"] and signed["signed_in"] is True
    assert "act in the app as them" in signed["next"]
    assert "script sees:" in signed["text_start"] and "sid=" not in signed["text_start"]   # still HttpOnly
    # Off: the same page shows the sign-in, and the agent is told why and what to do.
    await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": False})
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
    off = await look(studio, pid)
    assert "Sign in" in off["text_start"] and off["signed_in"] is False
    assert "Let the agent use my sign-in" in off["sign_in_note"] and "guess" in off["next"]

    # A token in localStorage, and one in sessionStorage, from the bridge's report.
    await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": True})
    async with httpx.AsyncClient() as web:
        await web.post(origin + signin.REPORT_PATH, headers=report_headers(origin),
                       content=json.dumps({"local": [["token", TOKEN]], "session": []}))
        stored = await look(studio, pid)
        assert "Welcome storage" in stored["text_start"] and stored["signed_in"] is True
        await web.post(origin + signin.REPORT_PATH, headers=report_headers(origin),
                       content=json.dumps({"local": [], "session": [["token", SESSION_TOKEN]]}))
        in_session = await look(studio, pid)
        assert "Welcome session" in in_session["text_start"]

    # The app never saw the studio's secret header, and the report never reached it.
    requests = seen(workspace)
    assert requests and all(signin.MARKER not in item["headers"] for item in requests)
    assert all(not item["path"].startswith("/__studio") for item in requests)
    assert all("studio2_access" not in item["headers"].get("cookie", "") for item in requests)
    names = json.loads((studio.store.dir(pid) / signin.NAMES_FILE).read_text())
    assert names["sid"]["http_only"] is True and names["sid"]["secure"] is True and names["sid"]["path"] == "/"

    # Nothing of the values in the look's results, the events, the project record or the logs.
    said = json.dumps([first, signed, off, stored, in_session])
    record = (studio.store.dir(pid) / "project.json")
    files = [record.read_text() if record.is_file() else json.dumps(studio.store.get(pid)),
             studio.store.events_path(pid).read_text() if studio.store.events_path(pid).is_file() else "",
             json.dumps((await client.get(f"/api/projects/{pid}")).json()), said,
             # The studio's own log, at its most detailed. (httpcore's DEBUG trace prints every header
             # it carries, for every proxied request; the studio never switches that on.)
             "\n".join(r.getMessage() for r in caplog.records if r.name.startswith(("studio2", "uvicorn")))]
    for value in SECRETS:
        assert [index for index, text in enumerate(files) if value in text] == [], value


@needs_browser
async def test_the_bridge_reports_the_persons_storage_from_a_real_browser(client, studio, project):
    pid = project["id"]
    await with_app(studio, pid)
    browser = await studio.looker.browser()
    # The person's browser: a plain context, not the studio's (and not marked as automated).
    context = await browser.new_context()
    try:
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
        page = await context.new_page()
        await page.goto(project["preview_url"])
        await page.evaluate(f"() => localStorage.setItem('token', {json.dumps(TOKEN)})")
        await page.click("#who")                     # a click: the bridge reports two seconds later
        await wait_for(lambda: studio.sessions.holds(pid)["person"], timeout=8)
    finally:
        await context.close()
    assert "Welcome storage" in (await look(studio, pid))["text_start"]


@needs_browser
async def test_nothing_reaches_an_outside_page_or_another_project(client, studio, project):
    pid = project["id"]
    workspace = await with_app(studio, pid)
    other = (await client.post("/api/projects", json={"name": "B"})).json()
    other_workspace = await with_app(studio, other["id"])
    await person_signs_in(project)
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
        await web.post(f"http://127.0.0.1:{project['port']}" + signin.REPORT_PATH,
                       headers={**report_headers(f"http://127.0.0.1:{project['port']}"), "cookie": f"sid={COOKIE}"},
                       content=json.dumps({"local": [["token", TOKEN]]}))
    # Project A's page loads a picture from project B's address while it carries A's sign-in.
    (workspace / "pixel.txt").write_text(f"http://127.0.0.1:{other['port']}/pixel.png")
    signed = await look(studio, pid)
    assert "Welcome cookie" in signed["text_start"]
    pixel = [item for item in seen(other_workspace) if item["path"] == "/pixel.png"]
    assert pixel and all("cookie" not in item["headers"] for item in pixel)
    # Project B's own look: nothing of A's.
    looked = await look(studio, other["id"])
    assert "Sign in" in looked["text_start"] and looked["signed_in"] is False
    assert all("cookie" not in item["headers"] for item in seen(other_workspace))

    # An outside page fetched afterwards gets nothing, even one on this machine.
    got: list = []

    class Outside(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.append(dict(self.headers))
            body = b"<html><body>outside</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Outside)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        final, markup = await studio.looker.rendered(f"http://127.0.0.1:{server.server_port}/")
        _final, project_markup = await studio.looker.rendered(project["preview_url"])
    finally:
        server.shutdown()
    assert "outside" in markup and got and all("Cookie" not in item and "cookie" not in item for item in got)
    # The project's own address, fetched as an outside page: signed out.
    assert '<h1 id="who">Sign in</h1>' in project_markup and TOKEN not in project_markup


@needs_browser
async def test_a_sign_in_the_agent_makes_lasts_across_looks_and_never_mixes_with_the_persons(client, studio, project):
    pid = project["id"]
    await with_app(studio, pid)
    first = await look(studio, pid, actions=[{"fill": "#name", "value": "test"}, {"click": "#go"},
                                             {"wait_for": "text=Welcome agent"}])
    assert all(item["ok"] for item in first["actions"]) and "Welcome agent" in first["text_start"]
    again = await look(studio, pid)
    assert "Welcome agent" in again["text_start"] and again["kept_sign_in"] is True and again["signed_in"] is False
    # The person's sign-in, when shared, wins and is not kept as the agent's.
    await person_signs_in(project)
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
    person = await look(studio, pid)
    assert "Welcome cookie" in person["text_start"] and person["kept_sign_in"] is False
    await client.patch(f"/api/projects/{pid}", json={"signed_in_looks": False})
    back = await look(studio, pid)
    assert "Welcome agent" in back["text_start"] and back["kept_sign_in"] is True   # the person's never became the agent's
    assert all(COOKIE not in json.dumps(item) for item in studio.sessions._kept[pid].cookies)


# -- every hop decided by the studio (redirects), and where the secret header goes -----------------------------

def listener(host: str):
    got: list = []

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
            body = b"<html><body>caught</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "planted=from-elsewhere; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer((host, 0), Catch)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, got


def lan_address() -> str | None:
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))          # no packet is sent; it only picks the interface
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") else address


@needs_browser
@pytest.mark.parametrize("where", ["loopback", "lan"])
async def test_a_redirect_never_carries_the_sign_in_or_the_secret_header_anywhere_else(client, studio, project, where):
    host = "127.0.0.1" if where == "loopback" else lan_address()
    if host is None:
        pytest.skip("this machine has no address besides loopback")
    pid = project["id"]
    workspace = await with_app(studio, pid)
    server, got = listener(host)
    try:
        (workspace / "target.txt").write_text(f"http://{host}:{server.server_port}/catch")
        await person_signs_in(project)
        async with httpx.AsyncClient() as web:
            await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
        # A sub-resource and a fetch of the page redirect elsewhere...
        signed = await look(studio, pid, wait_ms=800)
        assert "Welcome cookie" in signed["text_start"]
        # ...and so does a navigation, which still arrives where it was sent.
        moved = await look(studio, pid, path="/hop-nav", wait_ms=300)
        assert "caught" in moved["text_start"]
        context = await studio.looker.new_context(f"http://127.0.0.1:{project['port']}/")
        try:
            page = await context.new_page()
            await page.goto(f"http://127.0.0.1:{project['port']}/hop-nav")
            await page.wait_for_timeout(500)
            assert page.url.endswith("/catch")
            await page.goto(f"http://127.0.0.1:{project['port']}/")
            await page.wait_for_timeout(500)
            # An outside site the page navigated to keeps its own cookie for its own address, as any
            # browser would; nothing of it may land on the loopback host the project is served on.
            planted = [c for c in await context.cookies() if c["name"] == "planted" and c["domain"] in signin.LOOPBACK]
        finally:
            await context.close()
    finally:
        server.shutdown()
    paths = {item["path"] for item in got}
    assert {"/catch"} == paths and len(got) >= 3          # the picture, the fetch and the navigations got there
    for item in got:
        assert "cookie" not in item["headers"], item
        assert signin.MARKER not in item["headers"], item
    assert planted == []                                  # its Set-Cookie never landed in the context


@needs_browser
async def test_the_secret_header_goes_only_to_project_page_ports(client, studio, project):
    pid = project["id"]
    server, got = listener("127.0.0.1")
    try:
        workspace = studio.store.workspace(pid)
        (workspace / "index.html").write_text(f'<img src="http://127.0.0.1:{server.server_port}/direct">'
                                              '<script>fetch("http://127.0.0.1:%d/fetched")</script>' % server.server_port)
        await look(studio, pid, wait_ms=400)
        app_sets(studio, pid)
        async with httpx.AsyncClient() as web:
            await web.get(project["preview_url"], headers=page_load("sid=abc"))
        assert (await look(studio, pid, wait_ms=400))["signed_in"] is True
    finally:
        server.shutdown()
    assert got and all(signin.MARKER not in item["headers"] and "cookie" not in item["headers"] for item in got)


@needs_browser
async def test_a_page_cannot_reach_a_project_under_another_name_to_plant_a_session(client, studio, project, monkeypatch):
    pid, port = project["id"], project["port"]
    monkeypatch.setattr(studio.sessions, "_own_names", lambda: frozenset({"studio-own.invalid"}))
    (studio.store.workspace(pid) / "index.html").write_text("<p>hello</p>")
    context = await studio.looker.new_context(f"http://127.0.0.1:{port}/")
    try:
        page = await context.new_page()
        await page.goto(f"http://127.0.0.1:{port}/")
        said = await page.evaluate("""(url) => fetch(url, {method: "POST", headers: {"content-type": "application/json"},
            body: JSON.stringify({local: [["token", "planted"]]})}).then(r => "sent " + r.status, e => "refused")""",
                                   f"http://studio-own.invalid:{port}{signin.REPORT_PATH}")
        with pytest.raises(Exception):
            await page.goto(f"http://studio-own.invalid:{port}/")
    finally:
        await context.close()
    assert said == "refused" and studio.sessions.holds(pid)["person"] is False


# -- the cookies the app sets: names and attributes, merged by path ----------------------------------------------

def test_only_cookies_the_app_set_are_kept_and_replayed_as_it_set_them(tmp_path):
    (tmp_path / "p_1").mkdir()
    sessions = signin.Sessions(lambda: False, lambda pid: {"id": pid}, lambda port: "p_1" if port == 8201 else None,
                               folder=lambda pid: tmp_path / pid)
    scope = {"path": "/app/login", "headers": []}
    sessions.note_set_cookies("p_1", scope, ["sid=v; Path=/; HttpOnly; SameSite=Strict", "sub=w",
                                             f"{signin.ACCESS_COOKIE}=k; Path=/"])
    written = json.loads((tmp_path / "p_1" / signin.NAMES_FILE).read_text())
    assert set(written) == {"sid", "sub"} and written["sub"]["path"] == "/app"     # the default path of /app/login
    assert "v" not in json.dumps(written).replace('"', " ").split() and "w" not in json.dumps(list(written.values()))

    sessions._take_cookies("p_1", b"sid=1; sub=2; other=3", "/app/x")
    assert dict(sessions._person["p_1"].cookies) == {"sid": "1", "sub": "2"}       # never `other`
    cookies = {c["name"]: c for c in sessions.plan("http://127.0.0.1:8201/").cookies()}
    assert cookies["sid"]["httpOnly"] is True and cookies["sid"]["sameSite"] == "Strict"
    assert cookies["sub"]["path"] == "/app" and cookies["sub"]["httpOnly"] is False
    # The bridge's report and a page load at / do not carry `sub` (its path does not cover them): kept.
    sessions._take_cookies("p_1", b"sid=1", signin.REPORT_PATH)
    sessions._take_cookies("p_1", b"sid=4", "/")
    assert dict(sessions._person["p_1"].cookies) == {"sid": "4", "sub": "2"}
    # A page load on a path it covers, without it: the app or the browser dropped it.
    sessions._take_cookies("p_1", b"sid=4", "/app")
    assert dict(sessions._person["p_1"].cookies) == {"sid": "4"}
    # The app deletes `sid` in an answer to the person's browser: dropped; one to the studio's own: not.
    sessions.note_set_cookies("p_1", {"path": "/logout", "headers": [(signin.MARKER.encode(), sessions.secret.encode())]},
                              ["sid=; Path=/; Max-Age=0"])
    assert dict(sessions._person["p_1"].cookies) == {"sid": "4"}
    sessions.note_set_cookies("p_1", {"path": "/logout", "headers": []}, ["sid=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
    assert sessions._person["p_1"].cookies == []
    # The names outlive the studio: a new one reads them back.
    again = signin.Sessions(lambda: False, lambda pid: {"id": pid}, lambda port: "p_1", folder=lambda pid: tmp_path / pid)
    assert set(again.names("p_1")) == {"sid", "sub"}


def test_cookies_the_app_never_set_are_not_carried_and_the_agent_is_told(tmp_path):
    (tmp_path / "p_1").mkdir()
    sessions = signin.Sessions(lambda: False, lambda pid: {"id": pid}, lambda port: "p_1", folder=lambda pid: tmp_path / pid)
    sessions._take_cookies("p_1", f"theirs={COOKIE}".encode(), "/")      # a static page, or another app's cookie
    plan = sessions.plan("http://127.0.0.1:8201/")
    assert plan.person is None and plan.storage_state() is None and "never set" in plan.note


async def test_a_static_project_keeps_no_cookies_but_its_storage(client, studio, project):
    pid, origin = project["id"], f"http://127.0.0.1:{project['port']}"
    async with httpx.AsyncClient() as web:
        await web.get(project["preview_url"], headers=page_load(f"sid={COOKIE}"))
        await web.post(origin + signin.REPORT_PATH, headers={**report_headers(origin), "cookie": f"sid={COOKIE}"},
                       content=json.dumps({"local": [["token", TOKEN]]}))
    plan = studio.sessions.plan(origin + "/")
    assert plan.person is not None and plan.person.cookies == [] and plan.person.local == [("token", TOKEN)]
    assert not (studio.store.dir(pid) / signin.NAMES_FILE).exists()


class FakeContext:
    async def storage_state(self):
        return {"cookies": [{"name": "s", "value": "v", "domain": "127.0.0.1", "path": "/"}], "origins": []}


async def test_a_look_that_ends_after_its_project_was_forgotten_keeps_nothing():
    alive = {"p_1": True}
    sessions = signin.Sessions(lambda: False, lambda pid: {"id": pid} if alive.get(pid) else None, lambda port: "p_1")
    plan = sessions.plan("http://127.0.0.1:8201/")
    sessions.forget("p_1")                                       # deleted, or its data forgotten, mid-look
    await sessions.keep(plan, FakeContext(), None)
    assert sessions.holds("p_1")["kept"] is False
    plan = sessions.plan("http://127.0.0.1:8201/")
    alive["p_1"] = False
    await sessions.keep(plan, FakeContext(), None)
    assert sessions.holds("p_1")["kept"] is False
    alive["p_1"] = True
    await sessions.keep(sessions.plan("http://127.0.0.1:8201/"), FakeContext(), None)
    assert sessions.holds("p_1")["kept"] is True


async def test_the_preview_never_keeps_an_apps_cookie_for_the_next_browser(client, studio, project):
    """The proxy's own HTTP client must not keep the app's Set-Cookie: it would hand one browser's
    session to every later request, from anyone (found while fixing this lane)."""
    pid = project["id"]
    workspace = await with_app(studio, pid)
    await person_signs_in(project)
    async with httpx.AsyncClient() as someone_else:
        await someone_else.get(project["preview_url"], headers={"accept": "text/html", "cookie": "studio2_access=k"})
    last = [item for item in seen(workspace) if item["path"] == "/"][-1]
    assert "cookie" not in last["headers"]          # neither the app's cookie nor the studio's access key
