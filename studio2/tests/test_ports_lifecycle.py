"""Ports and processes, cleanly (CONTRACT.md): stop frees, start takes the same port again,
strangers are never killed, pages follow the studio's range, the plan needs a sign-in."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from studio2_server import cli
from studio2_server.api import create_app
from studio2_server.apps import App, port_bindable
from studio2_server.config import Settings
from studio2_server.harness import claude
from studio2_server.store import Store

from conftest import PORT_RANGE

# Listens on $PORT, then forks: the child holds the same socket. Both pids go to pids.txt.
SERVER = r'''
import os, socket, sys, time
sock = socket.socket(); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(os.environ["PORT"]))); sock.listen(8)
child = os.fork()
if child == 0:
    while True:
        conn, _ = sock.accept(); conn.close()
open("pids.txt", "w").write(f"{os.getpid()} {child}")
print("listening", flush=True)
while True:
    time.sleep(1)
'''

LISTENER = r'''
import socket, sys, time
sock = socket.socket(); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1]))); sock.listen(8)
print("up", flush=True)
while True:
    conn, _ = sock.accept(); conn.close()
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie child of this test process counts as gone
    done = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return bool(done.stdout.strip()) and not done.stdout.strip().startswith("Z")


def make_app(tmp_path: Path, port: int) -> App:
    workspace, directory = tmp_path / "work", tmp_path / "dir"
    workspace.mkdir(exist_ok=True)
    directory.mkdir(exist_ok=True)
    (workspace / "server.py").write_text(SERVER)
    (workspace / "studio.json").write_text(json.dumps({"run": f"{sys.executable} server.py"}))
    return App("p1", workspace, directory, port, "http://127.0.0.1:1/", lambda *_: None)


async def wait_state(app: App, *states: str, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while app.state not in states:
        assert time.monotonic() < deadline, f"state {app.state}, log {[t for _, t in app.lines]}"
        await asyncio.sleep(0.05)


def spawn_listener(port: int, cwd: Path) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", LISTENER, str(port)], cwd=cwd,
                               stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert process.stdout.readline().strip() == "up"
    return process


# -- 6. restart keeps the port, and the earlier group (child included) is gone -----------------
@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
async def test_stop_then_start_comes_back_on_the_same_port_with_the_old_processes_gone(tmp_path):
    port = free_port()
    app = make_app(tmp_path, port)
    seen: list[tuple[int, int]] = []
    try:
        for _ in range(5):
            await app.start()
            await wait_state(app, "running")
            assert app.port == port
            pids = tuple(int(p) for p in (app.workspace / "pids.txt").read_text().split())
            assert not seen or pids != seen[-1]
            for earlier in seen:
                assert not any(alive(pid) for pid in earlier)
            seen.append(pids)
            await app.stop()
            assert app.state == "stopped"
            assert not any(alive(pid) for pid in pids), pids
            assert port_bindable(port)
            (app.workspace / "pids.txt").unlink()
        await app.start()                   # and the restart path (sync with restart=True)
        await wait_state(app, "running")
        first = (app.workspace / "pids.txt").read_text()
        (app.workspace / "pids.txt").unlink()
        await app.sync(restart=True)
        await wait_state(app, "running")
        deadline = time.monotonic() + 5
        while not (app.workspace / "pids.txt").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        assert app.port == port
        assert not any(alive(int(pid)) for pid in first.split())
    finally:
        await app.shutdown()
    assert port_bindable(port)


# -- 5. a stranger on the port is never killed; a leftover of the same app is ------------------
async def test_a_stranger_on_the_port_is_named_and_left_alone(tmp_path):
    port = free_port()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stranger = spawn_listener(port, elsewhere)
    app = make_app(tmp_path, port)
    try:
        await app.start()
        assert app.state == "crashed"
        text = "\n".join(t for _, t in app.lines)
        assert "which the studio did not start" in text, text
        if shutil.which("lsof"):
            assert f"pid {stranger.pid}" in text
        assert stranger.poll() is None          # still running
        assert "which the studio did not start" in app.log_path.read_text()
    finally:
        stranger.kill()
        stranger.wait()
        await app.shutdown()


@pytest.mark.skipif(not shutil.which("lsof") and not Path("/proc").is_dir(), reason="needs lsof or /proc")
async def test_a_leftover_of_the_same_app_is_stopped_and_the_app_starts(tmp_path):
    port = free_port()
    app = make_app(tmp_path, port)
    leftover = spawn_listener(port, app.workspace)
    try:
        await app.start()
        await wait_state(app, "running")
        leftover.wait(timeout=5)
        assert any("leftover of this app" in t for _, t in app.lines)
    finally:
        if leftover.poll() is None:
            leftover.kill()
        await app.shutdown()


async def test_a_holder_after_the_stop_is_written_in_the_log(tmp_path, monkeypatch):
    port = free_port()
    app = make_app(tmp_path, port)
    await app.start()
    await wait_state(app, "running")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    holder = None

    real_stop = App._check_released

    async def after_stop(self):
        nonlocal holder
        holder = spawn_listener(port, elsewhere)    # something takes the port the moment the app lets go
        await real_stop(self)

    monkeypatch.setattr(App, "_check_released", after_stop)
    try:
        await app.stop()
        assert "still held by" in app.log_path.read_text()
        assert holder.poll() is None
    finally:
        if holder:
            holder.kill()
            holder.wait()


# -- 7. deleting a project frees its ports -------------------------------------------------------
async def test_deleting_a_project_frees_its_page_and_app_ports(client, studio):
    created = (await client.post("/api/projects", json={"name": "Has an app"})).json()
    pid, page = created["id"], created["port"]
    workspace = studio.store.workspace(pid)
    (workspace / "server.py").write_text(SERVER)
    (workspace / "studio.json").write_text(json.dumps({"run": f"{sys.executable} server.py"}))
    await studio.sync_app(pid)
    app = studio.apps.get(pid)
    await wait_state(app, "running")
    pids = [int(p) for p in (workspace / "pids.txt").read_text().split()]
    assert (await client.delete(f"/api/projects/{pid}")).status_code in (200, 204)
    assert port_bindable(page) and port_bindable(page + 100)
    assert not any(alive(p) for p in pids)
    again = (await client.post("/api/projects", json={"name": "Next"})).json()
    assert again["port"] == page


# -- 3. pages follow the studio's range ----------------------------------------------------------
async def test_a_page_port_outside_the_range_moves_into_it_and_one_inside_stays(settings):
    store = Store(settings)
    inside = store.create("Inside", "fake")
    outside = store.create("Outside", "fake")
    store.update(outside["id"], port=PORT_RANGE[1] + 500, preview_url=f"http://127.0.0.1:{PORT_RANGE[1] + 500}/")
    application = create_app(settings)
    await application.state.studio.start()
    try:
        moved, kept = store.get(outside["id"]), store.get(inside["id"])
        assert PORT_RANGE[0] <= moved["port"] <= PORT_RANGE[1]
        assert moved["preview_url"] == f"http://127.0.0.1:{moved['port']}/"
        assert moved["port"] != kept["port"]
        assert kept["port"] == inside["port"]
    finally:
        await application.state.studio.stop()


# -- 1, 2. the command line ---------------------------------------------------------------------
def test_port_free_is_not_fooled_by_a_socket_in_time_wait():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)
    peer = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()            # the server side closes first: its end goes to TIME_WAIT
    server.close()
    time.sleep(0.1)
    peer.close()
    assert cli.port_free(port)


def test_the_stop_message_names_a_running_request_only_when_there_was_one(settings):
    store = Store(settings)
    record = store.create("Busy one", "fake")
    assert cli._running_requests(settings) == []
    store.update(record["id"], running_turn="t1")
    assert cli._running_requests(settings) == ["Busy one"]


def test_stop_waits_until_the_process_is_gone_and_the_port_is_free(settings, monkeypatch, capsys):
    port = free_port()
    holder = spawn_listener(port, Path("/"))
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls, **_: settings))
    monkeypatch.setattr(cli, "read_state", lambda s: {"pid": holder.pid, "port": port})
    # the "studio" here exits on SIGTERM; reap it in the background so it does not linger as a zombie
    import threading
    threading.Thread(target=holder.wait, daemon=True).start()
    assert cli.cmd_stop(type("A", (), {"data": None})()) == 0
    out = capsys.readouterr().out
    assert "Stopped the studio" in out and "still running was ended" not in out
    assert cli.port_free(port)


# -- 8. the Claude plan needs a sign-in ---------------------------------------------------------
def test_the_plan_is_unavailable_without_a_sign_in_and_the_answer_is_cached(monkeypatch):
    monkeypatch.delenv("STUDIO2_FAKE", raising=False)
    monkeypatch.setattr(claude, "find_cli", lambda: "/bin/true")
    calls = []

    def detector():
        calls.append(1)
        return {"found": False, "detail": "No Claude sign-in on this computer. Sign in with `studio login`, or use a key."}

    harness = claude.ClaudeHarness("claude-subscription")
    harness.login_detector = detector
    connection = {"id": "subscription", "label": "Claude subscription", "kind": "claude-subscription", "models": [], "default_model": None}
    first = harness.describe(connection)
    assert first["available"] is False
    deadline = time.monotonic() + 5
    while not calls or harness._login is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    second = harness.describe(connection)
    assert second["available"] is False and "No Claude sign-in" in json.dumps(second)
    harness.describe(connection)
    assert len(calls) == 1                      # cached: no second subprocess within the minute
    harness.login_detector = lambda: {"found": True, "plan": "max", "detail": "Signed in."}
    harness._login_at = 0.0                     # the minute is up
    harness.describe(connection)
    deadline = time.monotonic() + 5
    while not (harness._login or {}).get("found"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert "No Claude sign-in" not in json.dumps(harness.describe(connection))


def test_under_fake_the_plan_check_does_not_run(monkeypatch):
    monkeypatch.setenv("STUDIO2_FAKE", "1")
    monkeypatch.setattr(claude, "find_cli", lambda: "/bin/true")
    harness = claude.ClaudeHarness("claude-subscription")
    harness.login_detector = lambda: pytest.fail("must not run under STUDIO2_FAKE")
    harness.describe({"id": "subscription", "label": "Claude subscription", "kind": "claude-subscription", "models": [], "default_model": None})
    time.sleep(0.05)
    assert harness._login is None


def test_port_holders_fall_back_to_ss_where_there_is_no_lsof(monkeypatch):
    """Linux without lsof (a small Debian or Red Hat install): the holder is read from `ss`.
    The sample is ss's documented output shape; it has not been captured on a real machine here."""
    from studio2_server import apps

    sample = ('LISTEN 0      2048       127.0.0.1:8301      0.0.0.0:*    '
              'users:(("uvicorn",pid=4242,fd=6),("uvicorn",pid=4250,fd=6))\n')
    assert apps.pids_from_ss(sample) == [4242, 4250]
    assert apps.pids_from_ss("") == []
    assert apps.pids_from_ss("LISTEN 0 128 127.0.0.1:8301 0.0.0.0:*\n") == []      # another user's process: no pid shown
    asked = []
    monkeypatch.setattr(apps, "_find", lambda name: None if name == "lsof" else "/usr/sbin/ss")
    monkeypatch.setattr(apps, "_run", lambda command: asked.append(command) or sample)
    assert apps.port_holders(8301) == [4242, 4250]
    assert asked == [["/usr/sbin/ss", "-H", "-ltnp", "sport", "=", ":8301"]]
    monkeypatch.setattr(apps, "_find", lambda name: None)
    assert apps.port_holders(8301) == []
