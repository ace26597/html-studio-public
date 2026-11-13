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
        if frame.startswith(": ping"):
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
                        elif line.startswith(": ping"):   # 0.2 s of silence: the turn is long over
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
