"""An app the person stopped stays stopped; what an app writes while it runs; the Python a child sees.

CONTRACT.md: "An app the person stopped stays stopped", "What an app writes while it runs: `state/`",
"Python and packages for apps and for the agent's scripts". Zero model calls.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

from studio2_server import cli, config
from studio2_server import workspace as ws
from studio2_server.api import create_app
from studio2_server.apps import BARE_PYTHON
from studio2_server.harness import claude
from studio2_server.harness.base import TurnRequest, TurnResult
from studio2_server.watcher import not_ignored

from conftest import run_turn


async def until(check, seconds=15.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.05)


# ---- an app the person stopped stays stopped ---------------------------------------------------------------
async def test_a_stopped_app_is_not_started_again_by_a_restart_of_the_studio(settings):
    first = create_app(settings)
    studio = first.state.studio
    await studio.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://127.0.0.1:8200") as client:
        pid = (await client.post("/api/projects", json={"name": "App"})).json()["id"]
        await run_turn(client, studio, pid, "add a backend")
        await until(lambda: studio.app(pid).state == "running")
        assert (await client.post(f"/api/projects/{pid}/app/stop")).json()["state"] == "stopped"
        assert studio.store.get(pid)["app_stopped"] is True
    await studio.stop()

    second = create_app(settings)           # the studio restarts on the same data
    again = second.state.studio
    await again.start()
    try:
        await until(lambda: again.app(pid).state != "none")
        await asyncio.sleep(0.4)             # long enough for a start that should not happen to show
        held = again.app(pid)
        assert held.state == "stopped" and held.process is None and held.run       # known, configured, not running
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://127.0.0.1:8200") as client:
            assert (await client.get(f"/api/projects/{pid}/app")).json()["state"] == "stopped"
            started = (await client.post(f"/api/projects/{pid}/app/start")).json()   # the person starts it again
            assert started["state"] in ("starting", "running")
            assert again.store.get(pid)["app_stopped"] is False
    finally:
        await again.stop()

    third = create_app(settings)            # and a running app does come back after a restart
    last = third.state.studio
    await last.start()
    try:
        await until(lambda: last.app(pid).state == "running")
    finally:
        await last.stop()


async def test_a_turn_that_changes_the_project_starts_a_stopped_app_and_one_that_changes_nothing_does_not(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "add a backend")
    await until(lambda: studio.app(pid).state == "running")
    await client.post(f"/api/projects/{pid}/app/stop")
    await studio.sync_app(pid)                                    # nothing changed: it stays as the person left it
    assert studio.app(pid).state == "stopped" and studio.store.get(pid)["app_stopped"] is True
    await run_turn(client, studio, pid, "Make a dashboard please")   # the person is working on it again
    await until(lambda: studio.app(pid).state == "running")
    assert studio.store.get(pid)["app_stopped"] is False


async def test_restart_from_the_api_clears_the_flag_too(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "add a backend")
    await until(lambda: studio.app(pid).state == "running")
    await client.post(f"/api/projects/{pid}/app/stop")
    await client.post(f"/api/projects/{pid}/app/restart")
    assert studio.store.get(pid)["app_stopped"] is False


# ---- what an app writes while it runs ---------------------------------------------------------------------------
async def test_what_an_app_writes_in_state_is_not_watched_versioned_or_called_a_user_change(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    assert "/state/" in (workspace / ".gitignore").read_text().split()
    (workspace / "state").mkdir()
    (workspace / "state" / "cities.json").write_text('["Tokyo"]')
    (workspace / "data").mkdir()
    (workspace / "data" / "prices.csv").write_text("a,b\n")

    assert not_ignored(workspace, ["state/cities.json", "data/prices.csv"]) == ["data/prices.csv"]   # no reload for state
    ws.checkpoint(workspace, "agent", "a turn")                     # from here on, changes are the person's
    (workspace / "state" / "cities.json").write_text('["Tokyo", "Lisbon"]')
    (workspace / "data" / "prices.csv").write_text("a,b\n1,2\n")
    saved = ws.checkpoint(workspace, "user", "an edit by hand")     # a version takes everything that is not ignored
    assert [f["path"] for f in saved["files"]] == ["data/prices.csv"]                # the app's data is not in a version
    changed = {item["path"] for item in ws.user_changes_since_agent(workspace)}
    assert changed == {"data/prices.csv"}                             # nor reported to the agent as the person's edit
    assert (workspace / "state" / "cities.json").is_file()


async def test_an_older_project_gets_the_line_when_its_next_turn_starts_and_an_attached_app_never_does(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    ignore = workspace / ".gitignore"
    ignore.write_text("node_modules/\n.venv/\n__pycache__/\n")        # as projects were made before
    ws.checkpoint(workspace, "agent", "as before")
    events = await run_turn(client, studio, pid, "hello")
    assert "/state/" in ignore.read_text().split()
    assert ignore.read_text().count("node_modules/") == 1             # nothing is written twice
    assert not ws.user_changes_since_agent(workspace)                 # and it went into the turn's own version

    ignore.write_text("node_modules/\n")
    ws.checkpoint(workspace, "agent", "the person's own ignore file")
    studio.store.update(pid, attach={"mode": "copy", "path": "/somewhere"})
    await run_turn(client, studio, pid, "hello again")
    assert "/state/" not in ignore.read_text()
    assert events


def test_ensure_ignored_keeps_what_is_there(tmp_path):
    (tmp_path / ".gitignore").write_text("dist/\n# mine")
    assert ws.ensure_ignored(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text() == "dist/\n# mine\nnode_modules/\n.venv/\n__pycache__/\n/state/\n"
    assert ws.ensure_ignored(tmp_path) is False


# ---- the Python a child sees ---------------------------------------------------------------------------------
def test_the_studios_own_python_is_kept_out_of_a_childs_shell():
    own = str(Path(sys.prefix) / "bin")
    parent = {"PATH": os.pathsep.join(["/x/venv/bin", own, "/usr/bin", "/bin"]), "VIRTUAL_ENV": "/x/venv",
              "PYTHONPATH": "/x/lib", "CONDA_PREFIX": "/x/conda", "HOME": "/home/me"}
    out = config.outside_python(parent)
    assert out == {"VIRTUAL_ENV": "", "PYTHONPATH": "", "CONDA_PREFIX": "", "PATH": "/usr/bin" + os.pathsep + "/bin"}
    assert config.outside_python({"PATH": "/usr/bin" + os.pathsep + "/bin", "HOME": "/h"}) == {}    # nothing to do


def test_a_bare_python_command_is_recognised_and_uv_is_not():
    for command in ("python3 server.py", "python server.py", "exec python3.12 -m http.server $PORT", " python3 app.py"):
        assert BARE_PYTHON.match(command), command
    for command in ("uv run python server.py", ".venv/bin/python server.py", "npx vite", "python3-config", "sh run.sh"):
        assert not BARE_PYTHON.match(command), command


async def test_an_app_is_told_when_it_runs_on_the_machines_own_python_and_never_sees_the_studios(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setenv("VIRTUAL_ENV", "/x/venv")
    monkeypatch.setenv("PYTHONPATH", "/x/lib")
    workspace = studio.store.workspace(pid)
    (workspace / "show.py").write_text(
        "import os, http.server, socketserver\n"
        "print('VENV=' + repr(os.environ.get('VIRTUAL_ENV')), 'PP=' + repr(os.environ.get('PYTHONPATH')), flush=True)\n"
        "socketserver.TCPServer.allow_reuse_address = True\n"
        "socketserver.TCPServer(('127.0.0.1', int(os.environ['PORT'])), http.server.SimpleHTTPRequestHandler).serve_forever()\n")
    (workspace / "studio.json").write_text('{"run": "python3 show.py"}')
    await studio.sync_app(pid)
    await until(lambda: studio.app(pid).state == "running")
    text = "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])
    assert "machine's own `python`" in text and "uv run python" in text
    assert "VENV=None" in text and "PP=None" in text


async def test_the_agents_shell_does_not_inherit_an_activated_environment(monkeypatch):
    seen = {}

    async def attempt(self, request, emit, env, scrub, resume):
        seen.update(env)
        return TurnResult("completed", "s", None), 1

    monkeypatch.setattr(claude.ClaudeHarness, "_attempt", attempt)
    monkeypatch.setenv("VIRTUAL_ENV", "/x/venv")
    monkeypatch.setenv("PATH", "/x/venv/bin" + os.pathsep + os.environ["PATH"])
    connection = {"id": "plan", "label": "Plan", "kind": "claude-subscription", "models": [], "default_model": "opus"}
    request = TurnRequest(project_id="p_1", turn_id="t_1", workspace=Path("."), prompt="x", session_id=None,
                          connection=connection, model="opus")
    await claude.ClaudeHarness("claude-subscription", None, None, lambda: []).run(request, lambda _e: None)
    assert seen["VIRTUAL_ENV"] == "" and "/x/venv/bin" not in seen["PATH"].split(os.pathsep)


def test_studio_start_puts_a_fetched_uv_on_the_path_only_when_there_is_no_other(settings, tmp_path, monkeypatch):
    fetched = tmp_path / "tools" / "uv" / "uv"
    fetched.parent.mkdir(parents=True)
    fetched.write_text("#!/bin/sh\n")
    fetched.chmod(0o755)
    monkeypatch.setattr(cli, "PRIVATE_UV", fetched)
    monkeypatch.setattr(cli, "PRIVATE_NODE_BIN", tmp_path / "no-node")
    monkeypatch.setenv("PATH", "/usr/bin" + os.pathsep + "/bin")                 # a machine with no uv of its own
    assert cli._child_env(settings)["PATH"].split(os.pathsep)[0] == str(fetched.parent)
    other = tmp_path / "bin"
    other.mkdir()
    (other / "uv").write_text("#!/bin/sh\n")
    (other / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", str(other) + os.pathsep + "/usr/bin")              # its own uv wins
    assert str(fetched.parent) not in cli._child_env(settings)["PATH"].split(os.pathsep)


# ---- pinned packages -----------------------------------------------------------------------------------------
def fake_uv(folder: Path, body: str) -> Path:
    """A stand-in for uv on PATH: `uv pip compile --quiet requirements.txt -o <file>` only."""
    folder.mkdir(parents=True, exist_ok=True)
    tool = folder / "uv"
    tool.write_text("#!/bin/sh\necho \"$@\" >> \"$(dirname \"$0\")/calls.log\"\n" + body)
    tool.chmod(0o755)
    return folder


PINS = 'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done; printf "fastapi==0.1.0\\nuvicorn==0.2.0\\n" > "$out"\n'
SERVE = ("python3 -c \"import os,http.server,socketserver; socketserver.TCPServer.allow_reuse_address=True; "
         "socketserver.TCPServer(('127.0.0.1',int(os.environ['PORT'])),http.server.SimpleHTTPRequestHandler).serve_forever()\" "
         "# requirements.lock")


async def start_app(studio, pid):
    await studio.sync_app(pid, restart=True)
    await until(lambda: studio.app(pid).state in ("running", "crashed"))
    return "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])


async def test_an_apps_packages_are_locked_before_it_starts_and_only_when_needed(client, studio, project, tmp_path, monkeypatch):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    tools = fake_uv(tmp_path / "bin", PINS)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    (workspace / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (workspace / "studio.json").write_text('{"run": ' + __import__("json").dumps(SERVE) + "}")

    text = await start_app(studio, pid)
    assert "Locking the app's packages" in text
    assert (workspace / "requirements.lock").read_text() == "fastapi==0.1.0\nuvicorn==0.2.0\n"
    assert not (workspace / "requirements.lock.tmp").exists()
    assert (tools / "calls.log").read_text().count("pip compile") == 1

    text = await start_app(studio, pid)                               # the lock is newer than the list: left alone
    assert "Locking" not in text and (tools / "calls.log").read_text().count("pip compile") == 1

    later = (workspace / "requirements.lock").stat().st_mtime + 5
    (workspace / "requirements.txt").write_text("fastapi\nuvicorn\nhttpx\n")
    os.utime(workspace / "requirements.txt", (later, later))          # the list changed after the lock
    await start_app(studio, pid)
    assert (tools / "calls.log").read_text().count("pip compile") == 2


async def test_a_lock_that_cannot_be_written_keeps_the_old_one_and_says_why(client, studio, project, tmp_path, monkeypatch):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    tools = fake_uv(tmp_path / "bin", 'echo "error: no network, could not find fastapi" >&2; exit 2\n')
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    (workspace / "requirements.lock").write_text("fastapi==0.0.9\n")
    later = (workspace / "requirements.lock").stat().st_mtime + 5
    (workspace / "requirements.txt").write_text("fastapi\n")
    os.utime(workspace / "requirements.txt", (later, later))
    (workspace / "studio.json").write_text('{"run": ' + __import__("json").dumps(SERVE) + "}")

    text = await start_app(studio, pid)
    assert "could not find fastapi" in text and "The requirements.lock that was there is used." in text
    assert (workspace / "requirements.lock").read_text() == "fastapi==0.0.9\n"
    assert studio.app(pid).state == "running"                         # and the app still starts on the old lock


async def test_an_app_that_does_not_run_from_a_lock_is_left_alone(client, studio, project, tmp_path, monkeypatch):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    tools = fake_uv(tmp_path / "bin", PINS)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    (workspace / "requirements.txt").write_text("fastapi\n")
    (workspace / "studio.json").write_text('{"run": ' + __import__("json").dumps(SERVE.replace(" # requirements.lock", "")) + "}")
    text = await start_app(studio, pid)
    assert "Locking" not in text and not (workspace / "requirements.lock").exists() and not (tools / "calls.log").exists()


# ---- an app that answers after the ready deadline -----------------------------------------------------------
SLOW_RUN = f"sleep 2; exec {sys.executable} -m http.server $PORT --bind 127.0.0.1"


async def test_an_app_that_answers_after_the_deadline_is_running_not_crashed(settings, monkeypatch):
    from studio2_server import apps
    monkeypatch.setattr(apps, "READY_TIMEOUT", 0.5)
    monkeypatch.setattr(apps, "SLOW_POLL", 0.2)
    application = create_app(settings)
    studio = application.state.studio
    await studio.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://127.0.0.1:8200") as client:
            project = (await client.post("/api/projects", json={"name": "Slow"})).json()
            pid = project["id"]
            (studio.store.workspace(pid) / "studio.json").write_text('{"run": "%s"}' % SLOW_RUN)
            await studio.sync_app(pid)
            app = studio.app(pid)
            await until(lambda: app.describe()["slow"], 5)
            # Past the deadline and still alive: starting, said to be slow, never crashed.
            assert app.state == "starting" and app.process.returncode is None and app.exit_code is None
            assert any("still running" in text for _n, text in app.lines)
            async with httpx.AsyncClient() as web:
                page = await web.get(project["preview_url"], headers={"accept": "text/html"})
            assert page.status_code == 503 and "Still starting" in page.text and "stopped by itself" not in page.text
            await until(lambda: app.state == "running", 10)
            assert not app.describe()["slow"]
            events = [e for e in studio.log(pid).read() if e["type"] == "app_status"]
            assert "crashed" not in [e["state"] for e in events]
            assert any(e["state"] == "starting" and e.get("slow") for e in events)
    finally:
        await studio.stop()


async def test_a_slow_app_can_be_stopped_and_is_started_again_by_a_restart_of_the_studio(settings, monkeypatch):
    from studio2_server import apps
    monkeypatch.setattr(apps, "READY_TIMEOUT", 0.3)
    monkeypatch.setattr(apps, "SLOW_POLL", 0.2)
    run = f"sleep 3; exec {sys.executable} -m http.server $PORT --bind 127.0.0.1"
    first = create_app(settings)
    studio = first.state.studio
    await studio.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://127.0.0.1:8200") as client:
        pid = (await client.post("/api/projects", json={"name": "Slow"})).json()["id"]
        (studio.store.workspace(pid) / "studio.json").write_text('{"run": "%s"}' % run)
        await studio.sync_app(pid)
        await until(lambda: studio.app(pid).describe()["slow"], 5)
        stopped = (await client.post(f"/api/projects/{pid}/app/stop")).json()
        assert stopped["state"] == "stopped" and stopped["pid"] is None and not stopped["slow"]
        await asyncio.sleep(0.5)                       # the watch ended with the process: no state after the stop
        assert studio.app(pid).state == "stopped"
        assert (await client.post(f"/api/projects/{pid}/app/start")).json()["state"] == "starting"
        await until(lambda: studio.app(pid).describe()["slow"], 5)
    await studio.stop()                                # the studio restarts while the app is slow to answer

    second = create_app(settings)
    again = second.state.studio
    await again.start()
    try:
        await until(lambda: again.app(pid) is not None and again.app(pid).state == "running", 15)
        assert not again.app(pid).describe()["slow"]
    finally:
        await again.stop()


async def test_an_app_that_never_listens_is_said_plainly_to_be_running_but_not_answering(settings, monkeypatch):
    from studio2_server import apps
    monkeypatch.setattr(apps, "READY_TIMEOUT", 0.3)
    monkeypatch.setattr(apps, "SLOW_POLL", 0.1)
    monkeypatch.setattr(apps, "NOT_ANSWERING_AFTER", 1.0)
    application = create_app(settings)
    studio = application.state.studio
    await studio.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://127.0.0.1:8200") as client:
            project = (await client.post("/api/projects", json={"name": "Quiet"})).json()
            pid = project["id"]
            (studio.store.workspace(pid) / "studio.json").write_text('{"run": "sleep 60"}')   # never serves
            await studio.sync_app(pid)
            app = studio.app(pid)
            await until(lambda: app.describe()["slow"], 5)
            assert not app.describe()["not_answering"]
            assert any("$PORT" in text for _n, text in app.lines)          # the waiting line says what to check
            await until(lambda: app.describe()["not_answering"], 5)
            await asyncio.sleep(0.5)
            described = (await client.get(f"/api/projects/{pid}/app")).json()
            # Alive, not exited: still starting, never crashed, and not stopped by the studio.
            assert described["state"] == "starting" and described["slow"] and described["pid"]
            assert app.process.returncode is None
            plain = [text for _n, text in app.lines if "not answering" in text]
            assert len(plain) == 1 and "$PORT" in plain[0]                  # said once
            async with httpx.AsyncClient() as web:
                page = await web.get(project["preview_url"], headers={"accept": "text/html"})
            assert "running but not answering" in page.text and "$PORT" in page.text
            # What the agent is told: wait on slow only briefly, then read the output and check the port.
            context = studio.agent_context(pid)
            assert "not answering on its port" in context and "minute or two" in context and "$PORT" in context
            assert "keep waiting" not in context
            stopped = (await client.post(f"/api/projects/{pid}/app/stop")).json()
            assert stopped["state"] == "stopped" and not stopped["not_answering"] and not stopped["slow"]
    finally:
        await studio.stop()


async def test_an_install_that_ignores_the_stop_signal_is_killed_after_the_install_limit(tmp_path, monkeypatch):
    from studio2_server import apps
    monkeypatch.setattr(apps, "SETUP_TIMEOUT", 0.5)
    monkeypatch.setattr(apps, "STOP_TIMEOUT", 0.5)
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (tmp_path / "ws").mkdir()
    (tmp_path / "dir").mkdir()
    states = []
    app = apps.App("p1", tmp_path / "ws", tmp_path / "dir", port, "http://127.0.0.1:1/",
                   lambda _pid, fields: states.append(fields["state"]))
    app.configure({"run": f"exec {sys.executable} -m http.server $PORT --bind 127.0.0.1"},
                  "trap '' TERM; sleep 60")                                 # an install that ignores SIGTERM
    await app.sync()
    try:
        await until(lambda: app.state == "crashed", 8)
        assert app.exit_code is not None and app.exit_code < 0 and states[-1] == "crashed"
        assert any("did not finish" in text for _n, text in app.lines)
    finally:
        await app.shutdown()
