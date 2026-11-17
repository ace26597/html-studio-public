"""A project's own back end: the `run` command from studio.json, supervised by the studio.

One process group per project, listening on 127.0.0.1:(project port + 100). The preview
server on the project port proxies to it (see preview.py). CONTRACT.md, "studio.json".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
from urllib.parse import urlsplit
from collections import deque
from pathlib import Path
from typing import Callable

from .config import now_iso, outside_python

log = logging.getLogger("studio2")
APP_PORT_OFFSET = 100
TAIL_LINES, LINE_CAP, LOG_FILE_CAP = 2_000, 2_000, 5 * 1024 * 1024
READY_TIMEOUT, STOP_TIMEOUT = 90.0, 5.0
# After READY_TIMEOUT the port is still checked, less often: a first start that downloads packages or
# compiles for minutes is slow, not crashed.
SLOW_POLL = 2.0
# Alive and never answering this long after the start: said once, plainly. The app is not stopped
# (it may still be compiling), but most likely it listens on another port or does not serve at all.
NOT_ANSWERING_AFTER = 300.0
LOCK_TIMEOUT = 180.0
REQUIREMENTS, LOCKED = "requirements.txt", "requirements.lock"
BARE_PYTHON = re.compile(r"^\s*(?:exec\s+)?python3?(?:\.\d+)?(?:\s|$)")
VENV_PYTHON = re.compile(r"^\s*(?:exec\s+)?\.venv/bin/")
SETUP_TIMEOUT = 900.0   # a first start that installs packages first (an attached app)


def read_config(workspace: Path) -> tuple[str | None, str | None]:
    """→ (run command, error). (None, None) when the project has no studio.json."""
    path = workspace / "studio.json"
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"studio.json is not valid JSON ({exc})."
    run = data.get("run") if isinstance(data, dict) else None
    if not isinstance(run, str) or not run.strip():
        return None, 'studio.json needs a "run" command, for example {"run": "python server.py"}.'
    return run.strip(), None


def reloads_itself(workspace: Path) -> bool:
    """True when studio.json says `"restart": "never"`: a development server that picks up file
    changes by itself (Vite, Next, Nuxt, uvicorn --reload) and should be left running after a turn."""
    try:
        data = json.loads((workspace / "studio.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("restart") == "never"


async def port_answers(port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 0.5)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


def kill_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def port_bindable(port: int) -> bool:
    """True when 127.0.0.1:port can be bound (SO_REUSEADDR: a TIME_WAIT socket does not count)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _find(name: str) -> str | None:
    """A system tool by name, also in the admin folders a plain user's PATH often lacks."""
    return shutil.which(name) or next(
        (path for path in (f"/usr/sbin/{name}", f"/sbin/{name}", f"/usr/bin/{name}") if os.path.exists(path)), None)


def pids_from_ss(out: str) -> list[int]:
    """The pids in `ss -ltnp` output: `… users:(("python3",pid=1234,fd=3),("python3",pid=1240,fd=3))`."""
    return sorted({int(pid) for pid in re.findall(r"\bpid=(\d+)", out)})


def port_holders(port: int) -> list[int]:
    """Pids listening on TCP `port`: lsof where the computer has it, otherwise `ss` (Linux; a small
    Debian or Red Hat install has no lsof, but always has ss). [] when neither is here."""
    lsof = _find("lsof")
    if lsof:
        out = _run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        return sorted({int(word) for word in out.split() if word.isdigit()})
    ss = _find("ss")
    if ss:
        return pids_from_ss(_run([ss, "-H", "-ltnp", "sport", "=", f":{port}"]))
    return []


def process_command(pid: int) -> str:
    return _run(["ps", "-p", str(pid), "-o", "command="]).strip()[:120]


def process_cwd(pid: int) -> Path | None:
    """The working folder of `pid` (/proc on Linux, lsof elsewhere), or None when unknown."""
    with contextlib.suppress(OSError):
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    lsof = _find("lsof")
    if not lsof:
        return None
    for line in _run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines():
        if line.startswith("n/"):
            return Path(line[1:])
    return None


def describe_holder(pid: int) -> str:
    command = process_command(pid)
    return f"pid {pid}" + (f" ({command})" if command else "")


# What a project's app may inherit from the studio's own environment.
APP_ENV = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "TERM", "TZ",
           "UV_CACHE_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "NVM_DIR", "PNPM_HOME"}


class App:
    """State of one project's app. All mutation happens on the event loop."""

    def __init__(self, project_id: str, workspace: Path, directory: Path, port: int, url: str,
                 on_status: Callable[[str, dict], None], extra_env: dict | None = None):
        self.extra_env = extra_env or {}
        self.project_id, self.workspace, self.directory = project_id, workspace, directory
        self.port, self.url, self.on_status = port, url, on_status
        self.own_port = port
        self.run: str | None = None
        self.error: str | None = None
        self.state = "none"
        self.slow = False   # starting, alive, and nothing has answered within READY_TIMEOUT
        self.not_answering = False   # slow, and still nothing NOT_ANSWERING_AFTER after the start
        self.process: asyncio.subprocess.Process | None = None
        self.exit_code: int | None = None
        self.started_at: str | None = None
        self.lines: deque[tuple[int, str]] = deque(maxlen=TAIL_LINES)
        self.last_line = 0
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._lock = asyncio.Lock()
        # An attached app (CONTRACT.md, "The app of an attached project"): the declaration from the
        # project record replaces studio.json; `address` is a server the studio did not start.
        self.declared: dict | None = None
        self.address: str | None = None
        self.host = "127.0.0.1"
        self.setup: str | None = None
        self._install_began: float | None = None

    def configure(self, declared: dict | None, setup: str | None) -> None:
        self.declared = declared if isinstance(declared, dict) else None
        self.setup = setup or None

    @property
    def setup_marker(self) -> Path:
        return self.directory / "setup.done"

    @property
    def installing(self) -> bool:
        return bool(self.setup and self.state == "starting" and not self.setup_marker.exists())

    def self_reloading(self) -> bool:
        if self.address:
            return True
        if self.declared is not None:
            return self.declared.get("restart") == "never"
        return reloads_itself(self.workspace)

    # -- views ------------------------------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self.directory / "app.log"

    @property
    def pid_path(self) -> Path:
        return self.directory / "app.pid"

    def describe(self) -> dict:
        return {"configured": self.run is not None or self.address is not None, "run": self.run,
                "state": self.state, "address": self.address, "installing": self.installing,
                "pid": self.process.pid if self.process and self.process.returncode is None else None,
                "port": self.port, "exit_code": self.exit_code, "started_at": self.started_at,
                "error": self.error, "last_line": self.last_line, "slow": self.slow,
                "not_answering": self.not_answering}

    def logs(self, after: int = 0) -> dict:
        return {"lines": [{"n": n, "text": text} for n, text in self.lines if n > after], "last": self.last_line}

    def _set(self, state: str, slow: bool = False) -> None:
        if state != self.state or slow != self.slow:
            self.state, self.slow = state, slow
            self.not_answering = self.not_answering and slow
            self.on_status(self.project_id, {"state": state, "exit_code": self.exit_code, "run": self.run,
                                             "slow": slow})

    def _line(self, text: str, handle=None) -> None:
        self.last_line += 1
        self.lines.append((self.last_line, text[:LINE_CAP]))
        if handle and handle.tell() < LOG_FILE_CAP:
            handle.write(text + "\n")
            handle.flush()

    # -- lifecycle --------------------------------------------------------------------------
    def refresh_config(self) -> bool:
        """Re-read the declaration (studio.json, or the record's app_config). → True when it changed."""
        address = None
        if self.declared is not None:
            run = self.declared.get("run") if isinstance(self.declared.get("run"), str) else None
            run, self.error = (run.strip() or None) if run else None, None
            address = self.declared.get("address") or None
        else:
            run, self.error = read_config(self.workspace)
        if address:
            run = None
            parts = urlsplit(address)
            self.host, self.port = parts.hostname or "127.0.0.1", parts.port or self.own_port
        else:
            self.host, self.port = "127.0.0.1", self.own_port
        changed = run != self.run or address != self.address
        self.run, self.address = run, address
        return changed

    async def sync(self, restart: bool = False) -> None:
        """Bring the process in line with studio.json: start, restart or stop as needed."""
        async with self._lock:
            changed = self.refresh_config()
            if self.address:
                await self._stop()   # a command the studio started before the address was set
                self._set("external")
            elif self.run is None:
                await self._stop()
                self._set("none")
            elif changed or self.state in ("none", "external") or (
                    restart and not (self.state in ("starting", "running") and self.self_reloading())):
                await self._stop()
                await self._start()

    async def hold(self) -> None:
        """The person stopped this app: know what it is, and do not start it."""
        async with self._lock:
            self.refresh_config()
            if self.address:
                self._set("external")
            elif self.run is None:
                await self._stop()
                self._set("none")
            elif self.state == "none":
                self._set("stopped")

    async def start(self) -> None:
        async with self._lock:
            self.refresh_config()
            if self.run and not self.address and self.state not in ("starting", "running"):
                await self._start()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()
            if self.run is not None:
                self._set("stopped")

    async def shutdown(self) -> None:
        """Studio is exiting: stop the process without announcing anything."""
        self.on_status = lambda *_: None
        async with self._lock:
            await self._stop()

    def _kill_stale(self) -> None:
        """A studio that was killed hard leaves its app holding the port; clear it first."""
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            return
        kill_group(pid, signal.SIGKILL)
        self.pid_path.unlink(missing_ok=True)

    async def _port_held(self) -> bool:
        return not port_bindable(self.port) or await port_answers(self.port)

    async def _wait_port_free(self, seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + seconds
        while await self._port_held():
            if asyncio.get_running_loop().time() > deadline:
                return False
            await asyncio.sleep(0.1)
        return True

    def _inside_project(self, folder: Path | None) -> bool:
        if folder is None:
            return False
        for root in (self.workspace, self.directory):
            with contextlib.suppress(OSError, ValueError):
                folder.resolve().relative_to(root.resolve())
                return True
        return False

    async def _clear_port(self, handle) -> bool:
        """Before a start: the app's port must be free. A leftover of this same app (a process whose
        working folder is inside the project) is stopped; anything else is left alone and the start
        is refused. CONTRACT.md, "Ports and processes, cleanly"."""
        if not await self._port_held():
            return True
        if await self._wait_port_free(1.0):      # a previous run of this app still letting go
            return True
        holders = await asyncio.to_thread(port_holders, self.port)
        strangers = []
        for pid in holders:
            if self._inside_project(await asyncio.to_thread(process_cwd, pid)):
                self._line(f"[studio] Stopping {await asyncio.to_thread(describe_holder, pid)}, a leftover "
                           f"of this app that still held port {self.port}.", handle)
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGTERM)
            else:
                strangers.append(pid)
        if not strangers and holders:
            if not await self._wait_port_free(3.0):
                for pid in holders:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(pid, signal.SIGKILL)
            if await self._wait_port_free(2.0):
                return True
        if strangers:
            who = ", ".join([await asyncio.to_thread(describe_holder, pid) for pid in strangers])
            self._line(f"[studio] Port {self.port} is in use by {who}, which the studio did not start. "
                       "Stop that program, then start the app again.", handle)
        else:
            self._line(f"[studio] Port {self.port} is in use by a program the studio could not identify, "
                       "so the app was not started.", handle)
        return False

    async def _check_released(self) -> None:
        """After a stop: nothing should answer on the app's port any more."""
        if await self._wait_port_free(2.0):
            return
        holders = await asyncio.to_thread(port_holders, self.port)
        who = ", ".join([await asyncio.to_thread(describe_holder, pid) for pid in holders]) or "a program that could not be named (no lsof or ss here)"
        line = f"[studio] After the stop, port {self.port} is still held by {who}."
        log.warning("%s: %s", self.project_id, line)
        self._line(line)
        with contextlib.suppress(OSError):
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    async def _lock_packages(self, env: dict, handle) -> None:
        """requirements.txt → requirements.lock (exact versions of everything), when the app runs from
        the lock and the lock is missing or older. A lock that is there is kept when this fails."""
        source, lock = self.workspace / REQUIREMENTS, self.workspace / LOCKED
        if LOCKED not in (self.run or "") or not source.is_file():
            return
        if lock.is_file() and lock.stat().st_mtime >= source.stat().st_mtime:
            return
        uv = shutil.which("uv", path=env.get("PATH"))
        if not uv:
            self._line(f"[studio] uv was not found, so {LOCKED} could not be written from {REQUIREMENTS}.", handle)
            return
        self._line(f"[studio] Locking the app's packages: {REQUIREMENTS} → {LOCKED} (exact versions).", handle)
        fresh = self.workspace / (LOCKED + ".tmp")
        output, code = b"", 1
        try:
            process = await asyncio.create_subprocess_exec(
                uv, "pip", "compile", "--quiet", REQUIREMENTS, "-o", fresh.name, cwd=self.workspace, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                output, _ = await asyncio.wait_for(process.communicate(), LOCK_TIMEOUT)
                code = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                output = f"It did not finish within {int(LOCK_TIMEOUT)} seconds.".encode()
        except OSError as exc:
            output = str(exc).encode()
        if code == 0 and fresh.is_file():
            os.replace(fresh, lock)
            return
        fresh.unlink(missing_ok=True)
        for text in output.decode("utf-8", "replace").splitlines()[-20:]:
            self._line(text, handle)
        self._line("[studio] The packages could not be locked. " + (
            f"The {LOCKED} that was there is used." if lock.is_file() else f"There is no {LOCKED} to run from."), handle)

    async def _start(self) -> None:
        self._kill_stale()
        self._stopping, self.exit_code = False, None
        self.lines.clear()
        self.last_line = 0
        # A project's app gets a short list of harmless variables, never the studio's whole
        # environment: that holds the access key, model keys and whatever else the person's shell
        # exported. What an app needs from the studio comes through extra_env (token and addresses).
        env = {name: value for name, value in os.environ.items()
               if name in APP_ENV or name.startswith("LC_")}
        cleaned = outside_python(os.environ)      # the environment variables are not passed on at all; PATH is
        if "PATH" in cleaned:
            env["PATH"] = cleaned["PATH"]
        env.update({"PORT": str(self.port), "HOST": "127.0.0.1", "PYTHONUNBUFFERED": "1",
                    "STUDIO_PROJECT_ID": self.project_id, "STUDIO_PROJECT_URL": self.url})
        env.update(self.extra_env)
        handle = self.log_path.open("w", encoding="utf-8", errors="replace")
        if not await self._clear_port(handle):
            handle.close()
            self.exit_code = None
            self._set("crashed")
            return
        command = self.run
        if BARE_PYTHON.match(self.run or ""):
            self._line("[studio] This command uses the machine's own `python`, whatever version that is (on a Mac "
                       "often 3.9). `uv run python …` uses a current one.", handle)
        if VENV_PYTHON.match(self.run or "") and not self.setup and not (self.workspace / ".venv").exists():
            self._line("[studio] This command starts from .venv, and this folder has no .venv. A safe copy never "
                       "takes .venv, and nothing here installs the packages again. Ask the agent to write a "
                       "requirements.txt and make .venv from it (uv venv, then uv pip install -r requirements.txt), "
                       "or attach the original folder in place.", handle)
        if self.setup and not self.setup_marker.exists():
            # First start of an attached app: install its packages, in the same log, then run it.
            self._line(f"[studio] Installing packages first: {self.setup}", handle)
            command = f"{{ {self.setup} ; }} && : > {shlex.quote(str(self.setup_marker))} && {self.run}"
        await self._lock_packages(env, handle)
        try:
            self.process = await asyncio.create_subprocess_shell(
                command, cwd=self.workspace, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            self._line(f"Could not start: {exc}", handle)
            handle.close()
            self.exit_code = 127
            self._set("crashed")
            return
        self.pid_path.write_text(str(self.process.pid))
        self._install_began = asyncio.get_running_loop().time()
        self.started_at = now_iso()
        self._set("starting")
        self._tasks = [asyncio.create_task(self._pump(self.process, handle)),
                       asyncio.create_task(self._ready(self.process))]

    async def _pump(self, process: asyncio.subprocess.Process, handle) -> None:
        """Copy output to the tail and the log file, then record how the process ended."""
        try:
            while True:
                try:
                    raw = await process.stdout.readline()
                except ValueError:  # a line longer than the stream limit: drop the rest of it
                    raw = await process.stdout.read(64 * 1024)
                if not raw:
                    break
                self._line(raw.decode("utf-8", "replace").rstrip("\r\n"), handle)
            code = await process.wait()
        finally:
            handle.close()
        if process is not self.process:
            return
        self.pid_path.unlink(missing_ok=True)
        self.exit_code = code
        if not self._stopping:
            self._set("crashed")

    async def _ready(self, process: asyncio.subprocess.Process) -> None:
        """Watch for the port to answer for as long as the process lives. Past READY_TIMEOUT the app
        is `slow`, not crashed: only an exit (seen by _pump) makes it crashed, and a stop ends the
        watch because the process is no longer this app's."""
        loop = asyncio.get_running_loop()
        began = loop.time()
        deadline = began + READY_TIMEOUT
        note = None   # when to say again how long it has waited
        while process.returncode is None and process is self.process:
            if await port_answers(self.port):
                if process is self.process and not self._stopping:
                    self._set("running")
                return
            if self.installing:   # the clock for answering starts once the install is done
                deadline = max(deadline, loop.time() + READY_TIMEOUT)
                if loop.time() - (self._install_began or loop.time()) > SETUP_TIMEOUT:
                    # Stopped, so that "crashed" is true: _pump records the exit.
                    self._line(f"[studio] The install did not finish within {int(SETUP_TIMEOUT // 60)} minutes, "
                               "so it was stopped.")
                    # As a Stop does: the whole group, and SIGKILL for whatever ignored SIGTERM (an
                    # install that keeps the output open would otherwise leave the app starting for ever).
                    kill_group(process.pid, signal.SIGTERM)
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
                    kill_group(process.pid, signal.SIGKILL)
                    return
            if loop.time() > deadline and not self.slow and self.state == "starting":
                self._line(f"[studio] Nothing has answered on 127.0.0.1:{self.port} after {int(READY_TIMEOUT)} s. "
                           "The app is still running, so the studio keeps checking; a first start that downloads "
                           "packages or compiles can take minutes. If the output above shows no install or "
                           f"compile going on, check that the app listens on the port in $PORT ({self.port}).")
                self._set("starting", slow=True)
                note = loop.time() + 60
            elif self.slow and not self.not_answering and loop.time() - began > NOT_ANSWERING_AFTER:
                self._line(f"[studio] The app has been running for {int(loop.time() - began)} s and has never "
                           f"answered on 127.0.0.1:{self.port}: it is running but not answering. It has not "
                           "crashed; most likely it listens on another port or does not serve at all. It must "
                           f"listen on the port in $PORT ({self.port}). The studio does not stop it (it may still "
                           "be compiling) and keeps checking.")
                self.not_answering = True
                self.on_status(self.project_id, {"state": self.state, "exit_code": self.exit_code, "run": self.run,
                                                 "slow": True, "not_answering": True})
            elif note is not None and loop.time() > note:
                self._line(f"[studio] Still waiting for 127.0.0.1:{self.port} to answer, "
                           f"{int(loop.time() - began)} s after the start. Check that the app listens on $PORT "
                           f"({self.port}) if nothing in its output says it is still installing or compiling.")
                note += 60
            await asyncio.sleep(SLOW_POLL if self.slow else 0.25)

    async def _stop(self) -> None:
        process, self._stopping = self.process, True
        tasks, self._tasks = self._tasks, []
        if process and process.returncode is None:
            kill_group(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
            except asyncio.TimeoutError:
                kill_group(process.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
        if process:
            kill_group(process.pid, signal.SIGKILL)  # children that outlived the shell
        for task in tasks:
            if task is not asyncio.current_task():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, 2)
        self.process = None
        self.pid_path.unlink(missing_ok=True)
        if process and not self.address:
            await self._check_released()


class Apps:
    def __init__(self, on_status: Callable[[str, dict], None]):
        self.on_status = on_status
        self.apps: dict[str, App] = {}

    def get(self, project_id: str) -> App | None:
        return self.apps.get(project_id)

    def ensure(self, project: dict, workspace: Path, directory: Path, extra_env: dict | None = None) -> App:
        app = self.apps.get(project["id"])
        port = project["port"] + APP_PORT_OFFSET
        if app is None or (app.port != port and not app.address):
            app = App(project["id"], workspace, directory, port, project["preview_url"], self.on_status,
                      extra_env)
            self.apps[project["id"]] = app
        app.configure(project.get("app_config"), (project.get("attach") or {}).get("install"))
        return app

    def proxy_target(self, project_id: str) -> tuple[str, int | None, App | None]:
        """What the preview server should do: → ("static" | "proxy" | "status", port, app)."""
        app = self.apps.get(project_id)
        if app is not None and app.address:
            return "proxy", app.port, app
        if app is None or (app.run is None and not app.error):
            return "static", None, app
        if app.state == "running":
            return "proxy", app.port, app
        return "status", None, app

    async def drop(self, project_id: str) -> None:
        app = self.apps.pop(project_id, None)
        if app:
            await app.shutdown()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.drop(pid) for pid in list(self.apps)), return_exceptions=True)
