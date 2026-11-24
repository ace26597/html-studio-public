import asyncio
import json

import httpx
import uvicorn

from conftest import run_turn

from studio2_server.api import create_app
from studio2_server.preview import BRIDGE_TAG, PreviewApp


def parse(frames: list[str]) -> list[dict]:
    events = []
    for frame in frames:
        if frame.startswith("id: "):
            head, data = frame.strip().split("\n", 1)
            event = json.loads(data.removeprefix("data: "))
            assert head == f"id: {event['seq']}"
            events.append(event)
    return events


async def collect(log, after):
    frames = []
    async for frame in log.stream(after, ping_seconds=0.05):
        if frame.startswith("event: ping"):
            # A named event the page can see (a comment would never reach it), with no id.
            assert frame == "event: ping\ndata: {}\n\n"
            break
        frames.append(frame)
    return frames


async def test_stream_replays_after_seq_then_goes_live(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "Hello")
    await studio.watchers.stop(pid)   # no late files_changed while we count
    log = studio.log(pid)
    stored = [e for e in log.read()]
    assert len(stored) > 10

    replay = parse(await collect(log, after=0))
    assert [e["seq"] for e in replay] == [e["seq"] for e in stored]
    assert parse(await collect(log, after=stored[4]["seq"]))[0]["seq"] == stored[5]["seq"]
    assert await collect(log, after=stored[-1]["seq"]) == []

    stream = log.stream(stored[-1]["seq"], ping_seconds=5)
    pending = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0.02)
    live = log.append("notice", None, level="info", message="live")
    frame = await asyncio.wait_for(pending, 2)
    assert parse([frame])[0] == live and live["seq"] == stored[-1]["seq"] + 1
    await stream.aclose()
    assert log.subscribers == set()
    assert json.loads(log.path.read_text().splitlines()[-1]) == live   # persisted, one JSON per line


async def test_sse_endpoint_over_http_with_last_event_id(settings):
    """A real uvicorn server on an ephemeral port: replay with ?after=, Last-Event-ID and pings."""
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning",
                                           timeout_graceful_shutdown=1))
    server.capture_signals = lambda: __import__("contextlib").nullcontext()
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
            pid = (await http.post("/api/projects", json={})).json()["id"]
            turn = (await http.post(f"/api/projects/{pid}/turns", json={"text": "Hi"})).json()["turn_id"]

            async def read(params=None, headers=None):
                events, pinged = [], False
                async with http.stream("GET", f"/api/projects/{pid}/events", params=params, headers=headers) as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
                        elif line == "event: ping":   # 0.2 s of silence: the turn is long over
                            pinged = True
                            break
                return events, pinged

            events, pinged = await read()
            assert pinged and events[0]["seq"] == 1 and events[0]["type"] == "turn_started"
            assert events[0]["turn_id"] == turn
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
            assert sum(e["type"] == "turn_finished" for e in events) == 1

            later, _ = await read(params={"after": 5})
            assert later[0]["seq"] == 6
            resumed, _ = await read(params={"after": 0}, headers={"Last-Event-ID": str(events[-1]["seq"] - 1)})
            assert [e["seq"] for e in resumed][0] == events[-1]["seq"]

            preview = (await http.get(f"/api/projects/{pid}")).json()["preview_url"]
            page = await http.get(preview)
            assert page.status_code == 200 and BRIDGE_TAG.decode() in page.text and "<h1>Hi</h1>" in page.text
            assert page.headers["cache-control"] == "no-store"
            bridge = await http.get(preview + "__studio/bridge.js")
            assert "studio-bridge" in bridge.text and bridge.headers["content-type"].startswith("text/javascript")
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def call(app, path, method="GET"):
    """Drive the ASGI app with a raw path (httpx would normalise `..` away)."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": method, "path": path, "query_string": b"", "headers": []}, receive, send)
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    return sent[0]["status"], headers, b"".join(m.get("body", b"") for m in sent[1:])


async def test_preview_serves_the_workspace_safely(tmp_path):
    workspace = tmp_path / "project" / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("secret")
    (tmp_path / "project" / "project.json").write_text("{}")
    (workspace / "index.html").write_text("<html><body><h1>Hi</h1></body></html>")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "index.html").write_text("<p>no body tag</p>")
    (workspace / "style.css").write_text("body{}")
    (workspace / "data.json").write_text("{}")
    (workspace / "leak").symlink_to(tmp_path / "project" / "project.json")
    app = PreviewApp(workspace)

    status, headers, body = await call(app, "/")
    assert status == 200 and headers["content-type"] == "text/html; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert body == b"<html><body><h1>Hi</h1>" + BRIDGE_TAG + b"</body></html>"

    status, _, body = await call(app, "/docs/")
    assert status == 200 and body == b"<p>no body tag</p>" + BRIDGE_TAG          # appended at the end
    status, headers, _ = await call(app, "/docs")
    assert status == 307 and headers["location"] == "/docs/"
    status, headers, body = await call(app, "/style.css")
    assert status == 200 and headers["content-type"].startswith("text/css") and BRIDGE_TAG not in body
    assert headers["cache-control"] == "no-store"
    assert (await call(app, "/data.json"))[1]["content-type"].startswith("application/json")

    status, headers, body = await call(app, "/__studio/bridge.js")
    assert status == 200 and b"studio-bridge" in body and headers["cache-control"] == "no-store"

    for blocked in ("/.git/config", "/.git/", "/.GIT/config", "/docs/../.git/config", "/../project.json",
                    "/docs/../../project.json", "/..", "/leak", "/nope.html"):
        status, _, body = await call(app, blocked)
        assert status == 404 and b"secret" not in body, blocked
    assert (await call(app, "/", method="POST"))[0] == 405

    (workspace / "index.html").unlink()
    status, _, body = await call(app, "/")
    assert status == 200 and b"no index.html yet" in body and BRIDGE_TAG in body   # friendly empty page


async def test_a_link_is_checked_where_it_ends_even_behind_a_folder_address(tmp_path):
    """A folder address appends index.html; a link there (or anywhere) to a file outside the
    workspace, or into its .git, is refused just as the file's own address is."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("<p>secret page</p>")
    (outside / "notes.txt").write_text("secret notes")
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("secret config")
    (workspace / ".git" / "index.html").write_text("<p>secret git page</p>")
    for name in ("docs", "out"):
        (workspace / name).mkdir()
    (workspace / "docs" / "index.html").symlink_to(outside / "index.html")
    (workspace / "out" / "index.html").symlink_to(workspace / ".git" / "index.html")
    (workspace / "config.txt").symlink_to(workspace / ".git" / "config")
    (workspace / "repo").symlink_to(workspace / ".git")
    (workspace / "away").symlink_to(outside)
    (workspace / "fine").mkdir()
    (workspace / "fine" / "index.html").write_text("<p>ok</p>")
    (workspace / "alias.html").symlink_to(workspace / "fine" / "index.html")
    app = PreviewApp(workspace)

    for blocked in ("/docs/", "/docs/index.html", "/out/", "/config.txt", "/repo/", "/repo/config",
                    "/away", "/away/", "/away/notes.txt", "/__studio/files/docs/", "/__studio/files/config.txt",
                    "/__studio/files/away/notes.txt"):
        status, _, body = await call(app, blocked)
        assert status == 404 and b"secret" not in body, blocked
    # A link that stays inside the workspace still works, and so do folders.
    assert (await call(app, "/alias.html"))[2].startswith(b"<p>ok</p>")
    assert (await call(app, "/fine/"))[0] == 200
    assert (await call(app, "/fine"))[0] == 307


async def test_a_socket_from_another_site_never_reaches_the_app(tmp_path):
    """The proxy hands the app the page's cookies, so a socket opened by a page on another
    site is refused; one from the project's own page, or from no browser at all, goes through."""
    import websockets

    seen = []

    async def echo(connection):
        seen.append(connection.request.headers.get("cookie"))
        async for message in connection:
            await connection.send("echo:" + message)

    upstream = await websockets.serve(echo, "127.0.0.1", 0)
    port = upstream.sockets[0].getsockname()[1]
    app = PreviewApp(tmp_path, target=lambda: ("proxy", port, None))

    async def open_socket(origin):
        sent, inbox = [], asyncio.Queue()
        for message in ({"type": "websocket.connect"}, {"type": "websocket.receive", "text": "hi"}):
            inbox.put_nowait(message)

        async def receive():
            return await inbox.get()

        async def send(message):
            sent.append(message)
            if message["type"] == "websocket.send":
                inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})

        headers = [(b"host", b"console.example.ts.net:8201"), (b"cookie", b"session=abc")]
        if origin:
            headers.append((b"origin", origin))
        scope = {"type": "websocket", "path": "/socket", "query_string": b"", "headers": headers, "subprotocols": []}
        await asyncio.wait_for(app(scope, receive, send), 5)
        return sent

    try:
        for foreign in (b"https://evil.example", b"https://console.example.ts.net:8200",
                        b"http://console.example.ts.net", b"null"):
            sent = await open_socket(foreign)
            assert [m["type"] for m in sent] == ["websocket.close"], foreign   # closed before accept
        assert seen == []                                                        # never connected upstream
        for allowed in (b"https://console.example.ts.net:8201", b"http://CONSOLE.example.ts.net:8201", None):
            sent = await open_socket(allowed)
            assert sent[0]["type"] == "websocket.accept", allowed
            assert {"type": "websocket.send", "text": "echo:hi"} in sent, allowed
        assert seen == ["session=abc"] * 3
    finally:
        upstream.close()
        await upstream.wait_closed()
        await app.client.aclose()


STARLETTE_APP = '''
import os, sys
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

PORT = int(sys.argv[1])

async def items(request):
    return Response("items", headers={"content-location": str(request.url_for("items"))})

async def go(request):
    return RedirectResponse(request.url_for("items"))

async def local(request):
    return RedirectResponse(f"http://localhost:{PORT}/items/?from=local")

async def away(request):
    return RedirectResponse(f"https://elsewhere.example/items/?not=127.0.0.1:{PORT}")

async def other_port(request):
    return RedirectResponse(f"http://127.0.0.1:{PORT + 1}/items/")

async def seen(request):
    return JSONResponse({"headers": dict(request.headers), "client": request.client.host})

async def cookie(request):
    response = Response("ok")
    response.set_cookie("a", "1", domain="127.0.0.1")
    response.set_cookie("b", "2", domain="example.org", secure=True)
    response.set_cookie("c", "3", secure=True)
    return response

app = Starlette(routes=[Route("/items/", items, name="items"), Route("/go", go), Route("/local", local),
                        Route("/away", away), Route("/other-port", other_port), Route("/seen", seen),
                        Route("/cookie", cookie)])
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
'''


async def test_the_apps_redirects_to_its_own_private_address_come_back_to_the_project_address(tmp_path):
    """A real Starlette app behind the proxy: its trailing-slash redirect and url_for name
    http://127.0.0.1:<its port>, which the browser must never be sent to."""
    import socket
    import sys

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = tmp_path / "app.py"
    script.write_text(STARLETTE_APP)
    process = await asyncio.create_subprocess_exec(sys.executable, str(script), str(port))
    preview = PreviewApp(tmp_path, target=lambda: ("proxy", port, None))
    try:
        async with httpx.AsyncClient() as probe_client:
            for _ in range(200):
                try:
                    await probe_client.get(f"http://127.0.0.1:{port}/seen")
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.05)
        for origin in ("http://127.0.0.1:8201", "http://console.example.ts.net:8201",
                       "https://console.example.ts.net:8201"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview), base_url=origin) as web:
                slash = await web.get("/items?x=1")
                assert slash.status_code == 307 and slash.headers["location"] == f"{origin}/items/?x=1"
                assert (await web.get("/go")).headers["location"] == f"{origin}/items/"
                assert (await web.get("/local")).headers["location"] == f"{origin}/items/?from=local"
                body = await web.get("/items/")
                assert body.text == "items" and body.headers["content-location"] == f"{origin}/items/"
                # Addresses that are not the app's own origin are passed on as they are.
                away = await web.get("/away")
                assert away.headers["location"] == f"https://elsewhere.example/items/?not=127.0.0.1:{port}"
                assert (await web.get("/other-port")).headers["location"] == f"http://127.0.0.1:{port + 1}/items/"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview),
                                     base_url="https://console.example.ts.net:8201") as web:
            # The app learns who asked, and is told no other host or scheme than the origin it is shown.
            seen = (await web.get("/seen", headers={"x-forwarded-host": "evil.example", "x-forwarded-proto": "https",
                                                    "forwarded": "host=evil"})).json()
            cookies = (await web.get("/cookie")).headers.get_list("set-cookie")
        assert seen["headers"]["host"] == f"127.0.0.1:{port}" and seen["headers"]["x-forwarded-for"] == "127.0.0.1"
        assert not {"x-forwarded-host", "x-forwarded-proto", "forwarded"} & set(seen["headers"])
        by_name = {c.split("=", 1)[0]: c.lower() for c in cookies}
        assert "domain" not in by_name["a"] and by_name["a"].startswith("a=1")   # the browser's own host instead
        assert "domain=example.org" in by_name["b"] and "secure" in by_name["b"] and "secure" in by_name["c"]
    finally:
        process.terminate()
        await process.wait()
        await preview.client.aclose()


async def test_preview_port_is_served_and_released(client, studio, project):
    async with httpx.AsyncClient() as http:
        page = await http.get(project["preview_url"])
        assert page.status_code == 200 and BRIDGE_TAG.decode() in page.text
        assert (await http.get(project["preview_url"] + ".git/HEAD")).status_code == 404
        await client.delete(f"/api/projects/{project['id']}")
        try:
            await http.get(project["preview_url"])
            raise AssertionError("preview still answering after delete")
        except httpx.TransportError:
            pass


async def test_watcher_emits_files_changed(client, studio, project):
    pid = project["id"]
    studio.watch(pid)
    await asyncio.sleep(0.3)   # let the OS watch settle
    (studio.store.workspace(pid) / "watched.txt").write_text("x")
    for _ in range(300):
        changed = [e for e in studio.log(pid).read() if e["type"] == "files_changed"]
        if changed:
            break
        await asyncio.sleep(0.02)
    assert changed and changed[0]["paths"] == ["watched.txt"] and changed[0]["turn_id"] is None


async def test_preview_moves_when_its_port_is_taken(studio, project, settings):
    import socket

    from studio2_server.studio import Studio
    await studio.stop()
    squatter = socket.socket()
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # the port may be in TIME_WAIT
    squatter.bind(("127.0.0.1", project["port"]))
    squatter.listen(1)
    reborn = Studio(settings)
    try:
        await reborn.start()
        moved = reborn.store.get(project["id"])
        assert moved["port"] != project["port"] and moved["preview_url"].endswith(f":{moved['port']}/")
        async with httpx.AsyncClient() as http:
            assert (await http.get(moved["preview_url"])).status_code == 200
    finally:
        squatter.close()
        await reborn.stop()


def test_a_torn_last_line_is_cut_off_so_the_next_event_is_kept(tmp_path):
    from studio2_server.events import EventLog
    path = tmp_path / "events.jsonl"
    whole = [json.dumps({"seq": 1, "ts": "x", "type": "turn_started", "turn_id": "t_1"}),
             json.dumps({"seq": 2, "ts": "x", "type": "notice", "turn_id": "t_1"})]
    path.write_text("\n".join(whole) + '\n{"seq":3,"type":"turn_star', "utf-8")   # a crash mid-write

    log = EventLog(path)
    assert log.seq == 2
    log.append("turn_finished", "t_1", status="failed")
    events = EventLog(path).read()
    assert [(e["seq"], e["type"]) for e in events] == [(1, "turn_started"), (2, "notice"), (3, "turn_finished")]
    assert path.read_text("utf-8").endswith("\n") and path.read_text("utf-8").count("\n") == 3
    assert (tmp_path / "events.jsonl.torn").read_bytes() == b'{"seq":3,"type":"turn_star'   # kept, not destroyed

    # A last event that was whole but lost only its newline is kept, not cut.
    path.write_text("\n".join(whole), "utf-8")
    log = EventLog(path)
    assert log.seq == 2
    log.append("turn_finished", "t_1")
    assert [e["seq"] for e in EventLog(path).read()] == [1, 2, 3]


def test_appends_from_threads_never_share_a_line(tmp_path):
    import threading

    from studio2_server.events import EventLog
    log = EventLog(tmp_path / "events.jsonl")
    big = "x" * 70_000      # larger than one buffered write

    def write(n):
        for _ in range(25):
            log.append("notice", None, message=f"{n}{big}")
    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = (tmp_path / "events.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 200 and sorted(json.loads(line)["seq"] for line in lines) == list(range(1, 201))


def test_the_page_helper_is_read_once_and_changes_with_a_restart(tmp_path, monkeypatch):
    """An edit to bridge.js, even a half-saved one, must not reach pages of a running studio."""
    from studio2_server import preview

    helper = tmp_path / "bridge.js"
    helper.write_text("// first")
    monkeypatch.setattr(preview, "BRIDGE_PATH", helper)
    monkeypatch.setattr(preview, "_BRIDGE", None)
    assert preview.bridge_bytes() == b"// first"
    helper.write_text("// half-saved (")
    assert preview.bridge_bytes() == b"// first"          # the running server keeps what it started with
    monkeypatch.setattr(preview, "_BRIDGE", None)         # a restart
    assert preview.bridge_bytes() == b"// half-saved ("
