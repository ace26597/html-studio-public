"""What a guest may not do on a shared studio (CONTRACT.md, "What a guest may not do on a shared
studio"): every route that changes something is either open to a guest on purpose or the owner's."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

ME, GUEST = "owner@example.org", "guest@example.org"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# What a guest may do on purpose: work in projects. Each line was judged (CONTRACT.md).
GUESTS_MAY = {
    ("POST", "/api/projects"),
    ("PATCH", "/api/projects/{project_id}/context/{item_id}"),     # a label or a note only
    ("DELETE", "/api/projects/{project_id}/context/{item_id}"),
    ("PATCH", "/api/projects/{project_id}"),                       # the signed-in look is refused inside
    ("POST", "/api/projects/{project_id}/app/{action}"),           # start or stop the project's own app
    ("POST", "/api/projects/{project_id}/look"),
    ("POST", "/api/projects/{project_id}/drawings"),
    ("PUT", "/api/projects/{project_id}/uses-template"),
    ("DELETE", "/api/projects/{project_id}/uses-template"),
    ("POST", "/api/transcribe"),
    ("POST", "/api/projects/{project_id}/model/v1/chat/completions"),   # the project's token, capped per day
    ("POST", "/api/skills/copy"),                                  # refused inside for a guest (ownskills)
    ("POST", "/api/library/skills/ask"),                           # "change" is refused inside for a guest
    ("PUT", "/api/projects/{project_id}/library"),
    ("POST", "/api/library/refresh"),
    ("DELETE", "/api/projects/{project_id}/data-sources/{source_id}"),
    ("POST", "/api/projects/{project_id}/data/{name}/query"),      # the project's token
    ("DELETE", "/api/projects/{project_id}"),
    ("PUT", "/api/projects/{project_id}/file"),
    ("POST", "/api/projects/{project_id}/upload"),
    ("POST", "/api/projects/{project_id}/dom-edits"),
    ("POST", "/api/projects/{project_id}/restore"),
    ("POST", "/api/projects/{project_id}/turns"),
    ("POST", "/api/projects/{project_id}/cost/recover"),
    ("DELETE", "/api/projects/{project_id}/queue/{queued_id}"),
    ("POST", "/api/projects/{project_id}/turns/{turn_id}/approvals/{approval_id}"),   # guarded "allow": owner_only
    ("POST", "/api/projects/{project_id}/turns/{turn_id}/questions/{question_id}"),
    ("POST", "/api/projects/{project_id}/turns/{turn_id}/stop"),
}

# Owner-only, checked inside the handler (they were so before the owner_only marker existed).
CHECKED_INSIDE = {
    ("POST", "/api/setup"), ("POST", "/api/litellm/install"), ("POST", "/api/setup/models"),     # setup_is_local
    ("POST", "/api/models/check"), ("POST", "/api/models/check/stop"),
    ("PUT", "/api/instructions"), ("POST", "/api/skills"), ("POST", "/api/skills/import"),
    ("PUT", "/api/skills/{name}"), ("DELETE", "/api/skills/{name}"),
    ("PUT", "/api/library/places"),
}


def through_the_gate(app, email: str):
    """The app as the access guard hands it a request the gate signed in as `email`."""
    async def signed_in(received, receive, send):
        if received["type"] == "http":
            received = {**received, "state": {**(received.get("state") or {}), "gate_email": email}}
        await app(received, receive, send)
    return signed_in


@pytest_asyncio.fixture
async def shared(app, settings):
    settings.shared, settings.owners = True, (ME,)
    made = {name: httpx.AsyncClient(transport=httpx.ASGITransport(app=through_the_gate(app, email)),
                                    base_url="http://127.0.0.1:8200")
            for name, email in (("owner", ME), ("guest", GUEST))}
    try:
        yield made
    finally:
        for client in made.values():
            await client.aclose()
        settings.shared, settings.owners = False, ()


# Every GET, judged: a guest may read it (key files are left out where it returns file contents).
# Those that act, not only read, say what they do.
GUESTS_READ = {
    "/api/setup", "/api/models", "/api/litellm", "/api/health", "/api/projects", "/api/templates",
    "/api/examples", "/api/examples/{example_id}/preview", "/api/projects/{project_id}/context",
    "/api/projects/{project_id}",                       # acts: opens the project's page port
    "/api/instructions",                                # owner only, checked inside
    "/api/projects/{project_id}/app", "/api/projects/{project_id}/app/logs",
    "/api/projects/{project_id}/look",                  # acts: a browser on the project's own page
    "/api/templates/{template_id}/preview", "/api/templates/{template_id}/export", "/api/generation",
    "/api/projects/{project_id}/model/v1/models",       # the project's token
    "/api/library", "/api/library/skills/export", "/api/library/skills/detail", "/api/library/skills/file",
    "/api/skills/{name}", "/api/projects/{project_id}/library", "/api/library/items", "/api/data-sources",
    "/api/projects/{project_id}/data-sources", "/api/projects/{project_id}/data",
    "/api/projects/{project_id}/data/{name}/schema",    # acts: SQL on an attached database, the project's token
    "/api/projects/{project_id}/download",              # key files left out for a guest
    "/api/library/places", "/api/services", "/api/projects/{project_id}/export",
    "/api/projects/{project_id}/export/page", "/api/projects/{project_id}/export/folder",
    "/api/projects/{project_id}/files", "/api/projects/{project_id}/file",     # key files refused to a guest
    "/api/projects/{project_id}/versions", "/api/projects/{project_id}/versions/{sha}/diff",
    "/api/projects/{project_id}/cost", "/api/projects/{project_id}/ship", "/api/projects/{project_id}/ships",
    "/api/projects/{project_id}/ships/{ship_id}",
    "/api/projects/{project_id}/events",                # acts: opens the page port, watches the folder
    "/api/find", "/api/projects/{project_id}/find",     # what the list and the files route show; no key files
    "/no-page/{rest:path}", "/site/{path:path}", "/site", "/{path:path}",
}
# Not an APIRoute, and judged: FastAPI's own description of the routes (no secrets in it).
OTHER_ROUTES = {"/openapi.json"}


def owner_check(dep) -> bool:
    """The studio's own owner check (api.py owner_only), not merely something marked like it."""
    call = dep.call
    return (getattr(call, "owner_only", False) is True and call.__module__ == "studio2_server.api"
            and call.__qualname__ == "create_app.<locals>.owner_only.<locals>.check")


def owner_marked(route: APIRoute) -> bool:
    return any(owner_check(dep) for dep in route.dependant.dependencies)


def mutating_routes(app) -> list[tuple[str, str, APIRoute]]:
    return [(method, route.path, route) for route in app.routes if isinstance(route, APIRoute)
            for method in sorted(route.methods & MUTATING)]


# -- no route slips in unjudged -----------------------------------------------------------------
def test_every_route_was_judged(app):
    from starlette.routing import Route
    unknown, unjudged, stale, reads = [], [], [], set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            # A plain Starlette route, a websocket or a mounted app: only the ones judged here.
            if not (type(route) is Route and route.path in OTHER_ROUTES and route.methods <= {"GET", "HEAD"}):
                unknown.append((type(route).__name__, getattr(route, "path", None)))
            continue
        methods = route.methods - {"HEAD", "OPTIONS"}
        if methods - MUTATING - {"GET"}:
            unknown.append((sorted(methods), route.path))
        if "GET" in methods:
            reads.add(route.path)
            if route.path not in GUESTS_READ and not owner_marked(route):
                unjudged.append(("GET", route.path))
        for method in sorted(methods & MUTATING):
            key = (method, route.path)
            if not (owner_marked(route) or key in GUESTS_MAY or key in CHECKED_INSIDE):
                unjudged.append(key)
            if owner_marked(route) and (key in GUESTS_MAY or key in CHECKED_INSIDE):
                stale.append(key)
    assert not unknown, f"Routes of a kind this test does not know (judge them here): {unknown}"
    assert not unjudged, ("These routes are neither owner-only nor on a list of what a guest may do "
                          f"(tests/test_guest_routes.py): {unjudged}")
    assert not stale, f"Owner-only routes still listed here: {stale}"
    every = {(m, p) for m, p, _ in mutating_routes(app)}
    assert (GUESTS_MAY | CHECKED_INSIDE) <= every, f"Listed but gone: {(GUESTS_MAY | CHECKED_INSIDE) - every}"
    assert GUESTS_READ <= reads, f"Listed but gone: {GUESTS_READ - reads}"


def test_the_route_check_fails_on_what_it_does_not_know(settings):
    """The walk above must fail on a route added some other way, and on a look-alike owner mark."""
    from fastapi import Depends
    from starlette.responses import PlainTextResponse
    from studio2_server.api import create_app
    made = create_app(settings)

    async def plain(_request):
        return PlainTextResponse("")
    made.add_route("/api/plain", plain, methods=["POST"])
    with pytest.raises(AssertionError, match="does not know"):
        test_every_route_was_judged(made)
    made = create_app(settings)

    @made.websocket("/api/socket")
    async def socket(_ws):
        pass
    with pytest.raises(AssertionError, match="does not know"):
        test_every_route_was_judged(made)
    made = create_app(settings)
    made.mount("/api/sub", create_app(settings))
    with pytest.raises(AssertionError, match="does not know"):
        test_every_route_was_judged(made)
    made = create_app(settings)

    @made.get("/api/acts")
    async def acts():
        return {}
    with pytest.raises(AssertionError, match="neither owner-only"):
        test_every_route_was_judged(made)
    made = create_app(settings)

    async def fake(_request=None):
        return None
    fake.owner_only = True

    @made.post("/api/looks-owner-only", dependencies=[Depends(fake)])
    async def looks():
        return {}
    with pytest.raises(AssertionError, match="neither owner-only"):
        test_every_route_was_judged(made)


# -- a guest is refused every owner-only route, and nothing changes ----------------------------
def fill(path: str, project_id: str) -> str:
    return (path.replace("{project_id}", project_id).replace("{server_id}", "yours:none")
            .replace("{source_id}", "ds_none").replace("{template_id}", "t_none").replace("{name}", "brave")
            .replace("{ship_id}", "s_00000000"))


def state(studio, project_id: str) -> str:
    project = studio.store.get(project_id)
    return json.dumps({"servers": [s["id"] for s in studio.lib.servers()], "sources": studio.data.list(),
                       "prefs": studio.picker.prefs(), "templates": [t["id"] for t in studio.templates()],
                       "projects": sorted(p["id"] for p in studio.store.list()),
                       "context": project.get("context"), "data_sources": project.get("data_sources"),
                       "ship": project.get("ship"), "app_config": project.get("app_config"),
                       "paused": studio.generator.halted(), "checking": bool(studio.checks.running)},
                      sort_keys=True, default=str)


async def test_a_guest_is_refused_every_owner_only_route(app, shared, studio, project):
    pid = project["id"]
    before = state(studio, pid)
    owner_only = [(m, p) for m, p, route in mutating_routes(app) if owner_marked(route)]
    owner_only += [("POST", "/api/models/check"), ("POST", "/api/models/check/stop")]
    assert len(owner_only) >= 30
    for method, path in owner_only:
        url = fill(path, pid)
        body = {"connection": "fake", "model": "fake-model"} if path == "/api/models/check" else {}
        if path == "/api/templates/import":
            answer = await shared["guest"].post(url, files={"file": ("t.zip", b"PK")})
        else:
            answer = await shared["guest"].request(method, url, json=body)
        assert answer.status_code == 403, (method, path, answer.status_code, answer.text)
        assert answer.json()["error"]["code"] == "not_for_shared", (method, path)
        assert "owner" in answer.json()["error"]["message"]
    assert state(studio, pid) == before


async def test_the_owner_still_does_these(shared, studio, project):
    owner = shared["owner"]
    pid = project["id"]
    source = await owner.post("/api/data-sources", json={"name": "shop", "kind": "postgres", "host": "db.example.org",
                                                         "database": "shop", "user": "reader"})
    assert source.status_code == 201, source.text
    sid = source.json()["id"]
    assert (await owner.put(f"/api/projects/{pid}/data-sources/{sid}")).status_code == 204
    assert (await owner.patch(f"/api/data-sources/{sid}", json={"name": "shop2"})).status_code == 200
    assert (await owner.delete(f"/api/data-sources/{sid}")).status_code == 204
    assert (await owner.put("/api/library/prefs", json={"skills": {}})).status_code == 200
    assert (await owner.delete("/api/generation/pause")).status_code == 204
    saved = await owner.post(f"/api/projects/{pid}/template", json={"name": "Starter"})
    assert saved.status_code == 201, saved.text
    tid = saved.json()["id"]
    assert (await owner.patch(f"/api/templates/{tid}", json={"label": "Starter two"})).status_code == 200
    assert (await owner.delete(f"/api/templates/{tid}")).status_code == 204
    looked = await owner.post("/api/context/inspect", json={"value": str(studio.settings.home)})
    assert looked.status_code == 200, looked.text
    # A guest reaches these as before.
    guest = shared["guest"]
    made = await guest.post("/api/projects", json={"name": "Mine"})
    assert made.status_code == 201
    gid = made.json()["id"]
    assert (await guest.put(f"/api/projects/{gid}/file", json={"path": "index.html", "content": "<p>hi</p>"})).status_code == 200
    assert (await guest.get(f"/api/projects/{gid}/file", params={"path": "index.html"})).status_code == 200
    assert (await guest.get("/api/library")).status_code == 200
    assert (await guest.get("/api/examples")).status_code == 200
    assert (await guest.get(f"/api/projects/{gid}/export/page")).status_code == 200


# -- the case that started this: a connector is a command, and its test starts it --------------
async def test_a_guest_cannot_add_or_test_a_command_connector(shared, studio, tmp_path):
    marker = tmp_path / "ran"
    command = {"name": "probe", "transport": "stdio", "command": sys.executable,
               "args": ["-c", f"open({str(marker)!r}, 'w').write('ran')"]}
    refused = await shared["guest"].post("/api/mcp-servers", json=command)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
    assert not [s for s in studio.lib.servers() if s.get("name") == "probe" or s.get("label") == "probe"]
    added = await shared["owner"].post("/api/mcp-servers", json=command)
    assert added.status_code == 201, added.text
    sid = added.json()["id"]
    for method, url, body in (("POST", f"/api/mcp-servers/{sid}/test", None),
                              ("PATCH", f"/api/mcp-servers/{sid}", {"args": ["-c", "print(1)"]}),
                              ("DELETE", f"/api/mcp-servers/{sid}", None)):
        answer = await shared["guest"].request(method, url, json=body)
        assert answer.status_code == 403, (method, answer.text)
    assert not marker.exists()                                   # nothing ran for the guest
    assert studio.lib.server(sid)["args"] == command["args"]     # and nothing changed
    tested = await shared["owner"].post(f"/api/mcp-servers/{sid}/test")
    assert tested.status_code == 200, tested.text
    assert marker.read_text() == "ran"                           # the owner's test does start it
    assert (await shared["owner"].delete(f"/api/mcp-servers/{sid}")).status_code == 204


async def test_a_studio_that_is_not_shared_is_unchanged(client, studio, tmp_path):
    made = await client.post("/api/mcp-servers", json={"name": "plain", "transport": "stdio", "command": "/usr/bin/true"})
    assert made.status_code == 201, made.text
    assert (await client.put("/api/library/prefs", json={"skills": {}})).status_code == 200
    assert (await client.delete(f"/api/mcp-servers/{made.json()['id']}")).status_code == 204


# -- the agent's own studio tools in a request a guest started ---------------------------------
from contextlib import contextmanager


@contextmanager
def a_request_by(studio, pid: str, owner: bool):
    """A request of this project held as running, started by the owner or by a guest (turns.start
    records the owner's in by_owner; a fake request ends too quickly to call a tool in it)."""
    turn = "t_0000beef"
    studio.turns.active[pid] = turn
    if owner:
        studio.turns.by_owner.add(turn)
    try:
        yield turn
    finally:
        studio.turns.active.pop(pid, None)
        studio.turns.by_owner.discard(turn)


async def test_a_guests_request_cannot_save_a_template_a_skill_or_make_an_app_live(shared, studio, project):
    from studio2_server import tools
    pid = project["id"]
    before = [t["id"] for t in studio.templates()]
    with a_request_by(studio, pid, owner=False):
        assert tools.guest_request(studio, pid) is True
        for name, args, said in (
                ("save_template", {"name": "Guest starter"}, "Only the studio's owner can save a template"),
                ("propose_ship_recipe", {"recipe": {}}, "Only the studio's owner can set how this app is made live"),
                ("make_live", {}, "Only the studio's owner can make this app live"),
                ("save_skill", {"name": "guest-skill", "description": "Use when.", "body": "Do it."},
                 "only its owner adds skills")):
            result = await tools.call(studio, pid, name, args)
            assert result.get("is_error") is True, (name, result)
            assert said in result["content"][0]["text"], (name, result)
    assert [t["id"] for t in studio.templates()] == before
    assert pid not in studio.ships.from_agent and not (studio.store.get(pid).get("ship") or {}).get("recipe")
    assert not (studio.settings.data_dir / "skills" / "guest-skill").exists()


async def test_the_owners_request_still_saves_a_template(shared, studio, project):
    from studio2_server import tools
    pid = project["id"]
    with a_request_by(studio, pid, owner=True):
        assert tools.guest_request(studio, pid) is False
        saved = await tools.call(studio, pid, "save_template", {"name": "Owner starter"})
        assert not saved.get("is_error"), saved
        live = await tools.call(studio, pid, "make_live", {})
        assert "owner" not in live["content"][0]["text"], live      # refused for another reason: not an attached app
    assert any(t["label"] == "Owner starter" for t in studio.templates())


async def test_a_studio_that_is_not_shared_has_no_guest_requests(studio, project):
    from studio2_server import tools
    assert tools.guest_request(studio, project["id"]) is False


# -- cards that need the owner, whoever started the request ------------------------------------
def a_card(studio, pid: str, turn: str, tool: str) -> str:
    """A card waiting in `turn`, as the turn's approver puts one (turns.py)."""
    import asyncio as aio
    approval_id = f"a_{tool[:4]}0000"[:10]
    studio.turns.pending.setdefault(turn, {})[approval_id] = aio.get_running_loop().create_future()
    studio.log(pid).append("approval_request", turn, approval_id=approval_id, tool=tool, title="t", detail="d",
                           reason="r")
    return approval_id


async def test_a_guest_cannot_allow_the_owners_cards_or_a_card_for_what_is_the_owners(shared, studio, project):
    pid = project["id"]
    with a_request_by(studio, pid, owner=True) as turn:
        card = a_card(studio, pid, turn, "Bash")      # any card of the owner's own request
        url = f"/api/projects/{pid}/turns/{turn}/approvals/{card}"
        refused = await shared["guest"].post(url, json={"decision": "allow"})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "owner_only"
        assert not studio.turns.pending[turn][card].done()
        assert (await shared["owner"].post(url, json={"decision": "allow"})).status_code == 204
        denied = a_card(studio, pid, turn, "generate_media")
        assert (await shared["guest"].post(f"/api/projects/{pid}/turns/{turn}/approvals/{denied}",
                                           json={"decision": "deny"})).status_code == 204     # deny: anyone
    for tool in ("save_skill", "save_template", "ship_recipe", "make_live"):
        with a_request_by(studio, pid, owner=False) as turn:
            card = a_card(studio, pid, turn, tool)     # a guest's own request, a card for the owner's
            answer = await shared["guest"].post(f"/api/projects/{pid}/turns/{turn}/approvals/{card}",
                                                json={"decision": "allow"})
            assert answer.status_code == 403, tool
    with a_request_by(studio, pid, owner=False) as turn:
        card = a_card(studio, pid, turn, "Bash")       # a guest's own ordinary card stays theirs
        assert (await shared["guest"].post(f"/api/projects/{pid}/turns/{turn}/approvals/{card}",
                                           json={"decision": "allow"})).status_code == 204


async def shown_card(studio, pid: str, turn: str, tool: str, guarded: bool = False) -> dict:
    """A card as the turn's own approver shows it (turns.py): its `approval_request` event."""
    import asyncio as aio
    approve = studio.turns._approver(pid, turn)
    waiting = aio.ensure_future(approve(tool, "t", "d", "r", always=True, guarded=guarded))
    for _ in range(50):
        if studio.turns.pending.get(turn):
            break
        await aio.sleep(0)
    approval_id = next(iter(studio.turns.pending[turn]))
    studio.turns.answer(turn, approval_id, "deny")
    assert await waiting is False
    return next(e for e in reversed(studio.log(pid).read())
                if e.get("type") == "approval_request" and e.get("approval_id") == approval_id)


async def test_the_card_says_when_only_the_owner_may_allow_it(shared, studio, project):
    pid = project["id"]
    with a_request_by(studio, pid, owner=True) as turn:
        assert (await shown_card(studio, pid, turn, "Bash")).get("owner_only") is True
    with a_request_by(studio, pid, owner=False) as turn:
        for tool in ("save_skill", "save_template", "ship_recipe", "make_live"):
            assert (await shown_card(studio, pid, turn, tool)).get("owner_only") is True, tool
        assert (await shown_card(studio, pid, turn, "Bash", guarded=True)).get("owner_only") is True
        assert "owner_only" not in await shown_card(studio, pid, turn, "Bash")     # the guest's own card


async def test_a_studio_that_is_not_shared_never_marks_a_card(studio, project):
    pid = project["id"]
    with a_request_by(studio, pid, owner=True) as turn:
        for tool in ("Bash", "save_skill", "make_live"):
            assert "owner_only" not in await shown_card(studio, pid, turn, tool), tool
        assert "owner_only" not in await shown_card(studio, pid, turn, "Bash", guarded=True)


# -- allow everything is the owner's to switch on -----------------------------------------------
async def test_only_the_owner_sets_a_project_to_allow_everything(shared, studio, project):
    pid = project["id"]
    refused = await shared["guest"].patch(f"/api/projects/{pid}", json={"approvals": "allow"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
    assert studio.store.get(pid)["approvals"] == "ask"
    assert (await shared["owner"].patch(f"/api/projects/{pid}", json={"approvals": "allow"})).status_code == 200
    back = await shared["guest"].patch(f"/api/projects/{pid}", json={"approvals": "ask"})
    assert back.status_code == 200 and studio.store.get(pid)["approvals"] == "ask"


# -- key files are not handed to a guest --------------------------------------------------------
async def test_key_files_are_not_handed_to_a_guest(shared, studio, project, tmp_path):
    import io
    import zipfile
    from studio2_server.preview import PreviewApp
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / ".env").write_text("SECRET=owner-only-value\n")
    (workspace / "config").mkdir()
    (workspace / "config" / ".env.local").write_text("SECRET=owner-only-value\n")
    (workspace / "index.html").write_text("<p>hello</p>")
    await studio.checkpoint(pid, "user", "keys")
    for path in (".env", "config/.env.local", "./.env"):
        refused = await shared["guest"].get(f"/api/projects/{pid}/file", params={"path": path})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared", path
    assert (await shared["owner"].get(f"/api/projects/{pid}/file", params={"path": ".env"})).status_code == 200
    names = [f["path"] for f in (await shared["guest"].get(f"/api/projects/{pid}/files")).json()]
    assert ".env" in names                                   # still listed by name
    for who, has in (("guest", False), ("owner", True)):
        zipped = await shared[who].get(f"/api/projects/{pid}/download")
        inside = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
        assert (".env" in inside) is has and ("config/.env.local" in inside) is has, (who, inside)
        assert "index.html" in inside
    sha = (await shared["owner"].get(f"/api/projects/{pid}/versions")).json()[0]["sha"]
    diff = (await shared["guest"].get(f"/api/projects/{pid}/versions/{sha}/diff")).json()["diff"]
    assert "owner-only-value" not in diff and "hello" in diff
    # The page port: no key file for a guest, as a page or through /__studio/files.
    for hide, status in ((True, 404), (False, 200)):
        page = PreviewApp(workspace, hide_keys=lambda _scope, hide=hide: hide)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=page), base_url="http://127.0.0.1:8401") as http:
            assert (await http.get("/.env")).status_code == status
            assert (await http.get("/__studio/files/config/.env.local")).status_code == status
            assert (await http.get("/index.html")).status_code == 200


# -- a project's app runs only a run line the owner has started ---------------------------------
async def until(check, seconds: float = 15.0) -> bool:
    import asyncio as aio
    for _ in range(int(seconds / 0.1)):
        if check():
            return True
        await aio.sleep(0.1)
    return check()


async def test_a_guest_cannot_get_a_run_line_started_that_the_owner_has_not(shared, studio, project, tmp_path):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    marker = tmp_path / "ran"
    run = json.dumps({"run": f"{sys.executable} -c \"open({str(marker)!r}, 'a').write('x'); "
                             "import time; time.sleep(60)\""})
    refused = await shared["guest"].put(f"/api/projects/{pid}/file", json={"path": "studio.json", "content": run})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
    upload = await shared["guest"].post(f"/api/projects/{pid}/upload", files={"file": ("studio.json", run.encode())})
    assert upload.status_code == 403
    (workspace / "studio.json").write_text(run)          # as the agent of a guest's request would write it
    with a_request_by(studio, pid, owner=False):
        await studio.sync_app(pid, restart=True)          # the start at the end of a guest's request
    assert studio.app(pid).state == "stopped" and "owner" in (studio.app(pid).error or "")
    said = [e["message"] for e in studio.log(pid).read() if e["type"] == "notice"]
    assert any("run line the studio's owner has started" in line for line in said)
    start = await shared["guest"].post(f"/api/projects/{pid}/app/start")
    assert start.status_code == 403 and start.json()["error"]["code"] == "run_not_seen"
    assert not marker.exists()
    # The owner starts it: from then on the same run line is the guest's to start and restart.
    assert (await shared["owner"].post(f"/api/projects/{pid}/app/start")).status_code == 200
    assert await until(marker.exists)
    assert studio.store.get(pid)["run_seen"] is not None
    assert (await shared["guest"].post(f"/api/projects/{pid}/app/stop")).status_code == 200
    assert (await shared["guest"].post(f"/api/projects/{pid}/app/start")).status_code == 200
    assert await until(lambda: marker.read_text() == "xx")
    # A request the owner started may change it, and its end starts the new line.
    (workspace / "studio.json").write_text(run.replace("'x'", "'y'"))
    with a_request_by(studio, pid, owner=True):
        await studio.sync_app(pid, restart=True)
    assert await until(lambda: marker.read_text().endswith("y"))
    await studio.app(pid).stop()


async def test_a_template_or_example_brings_its_run_line_and_a_plain_studio_checks_nothing(studio, project):
    from studio2_server.apps import read_config
    from studio2_server.studio import RUN_NOT_SEEN
    for example in studio.examples.list():
        made = await studio.create_project("From an example", example=example["id"])
        assert made["run_seen"] == read_config(studio.store.workspace(made["id"]))[0], example["id"]
    made = await studio.create_project("A back end", "fastapi-app")
    assert made["run_seen"] and made["run_seen"] == read_config(studio.store.workspace(made["id"]))[0]
    assert studio.run_refusal(project["id"], "python3 server.py") is None        # not shared: nothing checked
    studio.settings.shared = True
    try:
        assert studio.run_refusal(project["id"], "python3 server.py") == RUN_NOT_SEEN
        assert studio.store.get(project["id"])["run_seen"] is None            # a new project starts with none
    finally:
        studio.settings.shared = False
