"""The `studio` command: setup, start, stop, status, doctor (CONTRACT.md, "Install and first run").

`python -m studio2_server` is still the server itself and behaves as it always did; this is the
front door a person uses: it picks the port, runs the server in the background with a pid file
and a log, opens the browser, and asks the three setup questions.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, gateway, setup
from .access import check_settings
from .config import DEFAULT_PORT, STUDIO2_ROOT, Settings, now_iso, range_for

PORT_STEP = 200         # a studio on P uses P+1…P+99 for pages and P+101…P+199 for their back ends
PORT_TRIES = 20
STOP_WAIT = 10.0        # after the stop: how long `studio stop` waits for the process and its ports
START_WAIT = 45.0
PRIVATE_NODE_BIN = STUDIO2_ROOT / ".tools" / "node" / "bin"
PRIVATE_UV = STUDIO2_ROOT / ".tools" / "uv" / "uv"


def say(text: str = "") -> None:
    print(text, flush=True)


# -- the running studio's record ------------------------------------------------------------------
def state_path(settings: Settings) -> Path:
    return settings.data_dir / "studio.pid"


def log_path(settings: Settings) -> Path:
    return settings.data_dir / "logs" / "studio.log"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_studio_process(pid: int) -> bool:
    """A pid file can outlive its process and the number can be given to another program, so
    nothing is ever stopped on the number alone."""
    try:
        done = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "studio2_server" in done.stdout


def read_state(settings: Settings) -> dict | None:
    """What `studio start` recorded, when that process is still a running studio; else None
    (and a stale record is removed)."""
    path = state_path(settings)
    try:
        state = json.loads(path.read_text("utf-8"))
        pid = int(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if _alive(pid) and _is_studio_process(pid):
        return state
    path.unlink(missing_ok=True)
    return None


def studio_at(port: int, timeout: float = 2.0) -> dict | None:
    """The health answer of a studio on this computer's own address at `port`, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            data = json.loads(response.read(200_000))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return data if isinstance(data, dict) and data.get("ok") is True and "connections" in data else None


def port_free(port: int, hosts: tuple[str, ...] = ()) -> bool:
    for host in ("127.0.0.1", *hosts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # A socket in TIME_WAIT (a studio that has just closed) must not count as "in use".
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return False
    return True


def block_free(port: int, hosts: tuple[str, ...] = ()) -> bool:
    """The studio's port and the first project page after it: enough to tell a free block from
    one another studio is using."""
    return port_free(port, hosts) and port_free(port + 1)


def choose_port(preferred: int, hosts: tuple[str, ...] = (), free=block_free) -> int | None:
    """`preferred` when it is free, otherwise the next free block (200 ports further each time)."""
    for step in range(PORT_TRIES):
        port = preferred + step * PORT_STEP
        if port + PORT_STEP > 65535:
            break
        if free(port, hosts):
            return port
    return None


def should_open_browser(args) -> bool:
    if getattr(args, "no_open", False) or os.environ.get("STUDIO_NO_OPEN") == "1":
        return False
    if not sys.stdout.isatty() or os.environ.get("SSH_CONNECTION"):
        return False        # a script, a service or a remote shell: say the address, open nothing
    if sys.platform != "darwin" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def _addresses(settings: Settings, port: int) -> list[str]:
    scheme = "https" if settings.tls else "http"
    how = "no key for devices on your tailnet" if settings.open_tailnet else "access key required"
    return [f"{scheme}://{host}:{port}/  ({how})" for host in settings.hosts]


def _child_env(settings: Settings) -> dict:
    env = dict(os.environ)
    if not env.get("STUDIO2_PORT_RANGE"):
        env["STUDIO2_PORT_RANGE"] = "{}-{}".format(*settings.port_range)
    if PRIVATE_NODE_BIN.is_dir() and str(PRIVATE_NODE_BIN) not in env.get("PATH", "").split(os.pathsep):
        from .doctor import node
        path, _version = node()
        if path and Path(path).parent == PRIVATE_NODE_BIN:   # only when PATH has no usable Node of its own
            env["PATH"] = f"{PRIVATE_NODE_BIN}{os.pathsep}{env.get('PATH', '')}"
    if PRIVATE_UV.is_file() and not shutil.which("uv", path=env.get("PATH")):
        # The installer fetched uv and nothing put it on PATH: apps and the agent's scripts run `uv run …`.
        env["PATH"] = f"{PRIVATE_UV.parent}{os.pathsep}{env.get('PATH', '')}"
    return env


# -- start, stop, status ---------------------------------------------------------------------------
def cmd_start(args) -> int:
    if args.fake:
        os.environ["STUDIO2_FAKE"] = "1"
    settings = Settings.from_env(port=args.port, data=args.data)
    running = read_state(settings)
    if running:
        url = running.get("url") or f"http://127.0.0.1:{running['port']}/"
        say(f"The studio is already running at {url} (pid {running['pid']}).")
        if should_open_browser(args):
            webbrowser.open(url)
        return 0
    if args.port is None:
        other = studio_at(settings.port)
        if other is not None:
            url = f"http://127.0.0.1:{settings.port}/"
            say(f"A studio is already running at {url}. It was not started with `studio start`, so "
                "`studio stop` will not stop it.")
            say("To run a second one, give it its own port and data folder:  studio start --port N --data DIR")
            if should_open_browser(args):
                webbrowser.open(url)
            return 0
        port = choose_port(settings.port, settings.hosts)
        if port is None:
            say(f"No free port was found from {settings.port} upwards. Name one:  studio start --port N")
            return 1
        if port != settings.port:
            say(f"Port {settings.port} is in use by another program; using {port}.")
    else:
        port = args.port
        if not port_free(port, settings.hosts):
            say(f"Port {port} is in use. Choose another, or leave --port out and the studio takes a free one.")
            return 1
    settings.port = port
    if not os.environ.get("STUDIO2_PORT_RANGE"):
        settings.port_range = range_for(port)
    try:
        check_settings(settings.hosts, settings.access_key, settings.open_tailnet, settings.shared)
    except SystemExit as exc:
        say(str(exc))
        say("Run `studio setup` to choose where the studio runs and who uses it.")
        return 1
    if not (settings.web_dist / "index.html").is_file():
        say(f"The interface has not been built yet. Run {STUDIO2_ROOT / 'install.sh'} first.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    environment = _child_env(settings)

    if args.foreground:
        os.environ.update(environment)      # this process becomes the server
        if should_open_browser(args):
            threading.Thread(target=_open_when_up, args=(port, url), daemon=True).start()
        from .__main__ import serve
        serve(settings)
        return 0

    log_file = log_path(settings)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "studio2_server", "--port", str(port), "--data", str(settings.data_dir)]
    with open(log_file, "ab") as log:
        log.write(f"\n--- studio start {now_iso()} port {port} ---\n".encode())
        log.flush()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True, cwd=str(STUDIO2_ROOT), env=environment)
    state = {"pid": process.pid, "port": port, "url": url, "started_at": now_iso(), "log": str(log_file),
             "data": str(settings.data_dir), "version": __version__}
    state_path(settings).write_text(json.dumps(state, indent=1), "utf-8")
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            state_path(settings).unlink(missing_ok=True)
            say(f"The studio stopped while starting (exit {process.returncode}). The end of {log_file}:")
            say(_tail(log_file))
            return 1
        if studio_at(port, timeout=1.0):
            break
        time.sleep(0.25)
    else:
        say(f"The studio did not answer within {int(START_WAIT)} seconds. It is still starting or stuck; see {log_file}.")
        return 1
    say(f"Studio is running at {url}")
    for address in _addresses(settings, port):
        say(f"  also at {address}")
    if settings.shared:
        say("  shared with other people: the Claude plan is off; everyone signs in with the access key")
    low, high = settings.port_range
    say(f"  project pages on ports {low}–{high}, their back ends on {low + 100}–{high + 100}")
    say(f"  pid {process.pid} · log {log_file} · stop it with `studio stop`")
    if should_open_browser(args):
        webbrowser.open(url)
    return 0


def _open_when_up(port: int, url: str) -> None:
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        if studio_at(port, timeout=1.0):
            webbrowser.open(url)
            return
        time.sleep(0.3)


def _tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return ""


def cmd_stop(args) -> int:
    settings = Settings.from_env(data=args.data)
    state = read_state(settings)
    if not state:
        other = studio_at(settings.port)
        if other is not None:
            say(f"A studio answers at http://127.0.0.1:{settings.port}/, but it was not started with "
                "`studio start`, so it is left alone. Stop it the way it was started.")
            return 1
        say("No studio is running.")
        return 0
    pid = int(state["pid"])
    port = int(state.get("port") or settings.port)
    busy = _running_requests(settings)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    # Return only when it has gone and its ports can be taken again (CONTRACT.md, "Ports and
    # processes, cleanly"): a `studio start` straight after must find the same port free.
    deadline = time.monotonic() + STOP_WAIT
    while time.monotonic() < deadline and (_alive(pid) or not block_free(port)):
        time.sleep(0.1)
    if _alive(pid):
        say(f"The studio (pid {pid}) did not stop within {int(STOP_WAIT)} seconds of being killed. "
            f"It still holds port {port}; stop it with  kill -9 {pid}")
        return 1
    state_path(settings).unlink(missing_ok=True)
    if not block_free(port):
        from .apps import describe_holder, port_holders
        held = [p for p in (port, port + 1) if not port_free(p)]
        who = ", ".join(describe_holder(h) for p in held for h in port_holders(p)) or "a program that could not be named"
        say(f"Stopped the studio (pid {pid}), but port {held[0]} is still held by {who}.")
        return 1
    if busy:
        say(f"Stopped the studio (pid {pid}). A request that was still running was ended ({', '.join(busy)}).")
    else:
        say(f"Stopped the studio (pid {pid}).")
    return 0


def _running_requests(settings: Settings) -> list[str]:
    """Names of the projects that had a request running (their record's `running_turn`)."""
    try:
        from .store import Store
        return [str(p.get("name") or p.get("id")) for p in Store(settings).list() if p.get("running_turn")]
    except Exception:       # an unreadable data folder must not stop `studio stop`
        return []


def cmd_status(args) -> int:
    settings = Settings.from_env(data=args.data)
    state = read_state(settings)
    port = int(state["port"]) if state else settings.port
    health = studio_at(port)
    report = {"running": bool(state or health), "started_by_studio_command": bool(state), "port": port,
              "url": f"http://127.0.0.1:{port}/" if (state or health) else None,
              "pid": state.get("pid") if state else None, "started_at": state.get("started_at") if state else None,
              "log": state.get("log") if state else None, "data": str(settings.data_dir),
              "config": str(settings.config_path), "answers": health is not None,
              "version": (health or {}).get("version") or __version__,
              "shared": bool((health or {}).get("shared", settings.shared)),
              "also_on": [a.split("  ")[0] for a in _addresses(settings, port)]}
    if args.json:
        say(json.dumps(report, indent=1))
        return 0 if report["running"] else 3
    if state:
        say(f"Running at {report['url']} (pid {state['pid']}, since {state.get('started_at')}).")
        if health is None:
            say("  It does not answer yet: still starting, or stuck. See the log.")
        say(f"  log {state.get('log')}")
    elif health is not None:
        say(f"A studio answers at {report['url']}. It was not started with `studio start`.")
    else:
        say("Not running. Start it with `studio start`.")
    for address in _addresses(settings, port):
        say(f"  also at {address}")
    say(f"  data {settings.data_dir}")
    say(f"  config {settings.config_path}{'' if settings.config_path.is_file() else ' (none yet)'}")
    return 0 if report["running"] else 3


def cmd_doctor(args) -> int:
    from . import doctor
    settings = Settings.from_env(data=args.data)
    report = doctor.collect(settings, online=args.online, state=read_state(settings))
    say(json.dumps(report, indent=1) if args.json else doctor.render(report))
    return 1 if report["status"] == doctor.FAIL else 0


def litellm_report(settings: Settings) -> dict:
    """What `studio litellm status` and the doctor say about the studio's own LiteLLM, read from
    outside the running studio: whether it is installed, and whether the one this studio started
    still answers."""
    from .config import load_connections
    from dataclasses import replace
    found = gateway.installed()
    needs = gateway.managed(load_connections(replace(settings, fake=False)))
    state = gateway.read_state(settings.data_dir / "litellm")
    answering = bool(state and gateway.answers(int(state.get("port") or 0)))
    return {"installed": found["installed"], "version": found["version"], "pinned": gateway.VERSION,
            "detail": found["detail"], "home": str(gateway.home()), "requirements": str(gateway.REQUIREMENTS),
            "needed_by": [str(c["id"]) for c in needs], "running": answering,
            "port": state.get("port") if state else None, "pid": state.get("pid") if state else None,
            "left_behind": bool(state) and not answering,
            "log": str(settings.data_dir / "litellm" / "litellm.log")}


def cmd_litellm(args, ask=input) -> int:
    settings = Settings.from_env(data=args.data)
    if args.action == "install":
        found = gateway.installed()
        if found["installed"]:
            say(found["detail"])
            return 0
        say(f"LiteLLM {gateway.VERSION} lets the studio use OpenAI, OpenRouter, Azure OpenAI and Gemini keys.")
        say(f"It goes into {gateway.home()} ({gateway.SIZE_NOTE}); every package is checked against a hash in")
        say(f"{gateway.REQUIREMENTS}. It runs on this computer only, when a request needs it.")
        if not args.yes:
            if not sys.stdin.isatty() and ask is input:
                say("Nothing was installed; add --yes to install without a question.")
                return 2
            if not _yes(ask, "Install it now?", True):
                say("Nothing was installed.")
                return 0
        try:
            gateway.install(say)
        except gateway.GatewayError as exc:
            say(str(exc))
            return 1
        return 0
    report = litellm_report(settings)
    say(report["detail"])
    if report["needed_by"]:
        say(f"  used by the model account{'s' if len(report['needed_by']) > 1 else ''}: {', '.join(report['needed_by'])}")
    else:
        say("  no model account needs it")
    if report["running"]:
        say(f"  running on this computer only: 127.0.0.1:{report['port']} (pid {report['pid']})")
    elif report["left_behind"]:
        say(f"  a LiteLLM started by this studio (pid {report['pid']}) no longer answers; the next request starts a new one")
    elif report["installed"]:
        say("  not running: it starts when a request needs it and stops with the studio")
    say(f"  log {report['log']}")
    return 0


def cmd_access_key(args) -> int:
    settings = Settings.from_env()
    if not settings.access_key:
        from .config import read_config
        from .keystore import ACCESS
        name = str(read_config(settings.config_path).get("access_key_secret") or "")
        settings.access_key = settings.secret_store().get(ACCESS, name) if name else None
    if not settings.access_key:
        say("No access key is set. One is made when setup puts the studio on a network address or shares it.")
        return 1
    say(settings.access_key)
    return 0


def cmd_keys(args, secret=getpass.getpass) -> int:
    """Keys for other services (CONTRACT.md): list them, save one, remove one. A key is never printed."""
    from . import services

    settings = Settings.from_env()
    action = getattr(args, "action", None) or "list"
    if action == "list":
        for item in services.listing(settings):
            where = {"env": f"set, from the environment variable {item['env']}", "store": f"set, kept in {settings.secret_store().label}",
                     None: f"not set  (studio keys set {item['name']}, or the variable {item['env']})"}[item["source"]]
            say(f"  {item['name']:10} {where}")
            say(f"  {'':10} {item['used_for']}  {item['site']}")
        return 0
    try:
        if action == "set":
            value = _read_secret_file(args.key_file) if getattr(args, "key_file", None) else secret(f"{args.name} key: ")
            services.save(settings, args.name, value)
            say(f"Saved in {settings.secret_store().label}. The running studio uses it from the next search.")
        else:
            services.remove(settings, args.name)
            left = services.source(settings, args.name)
            say("Removed." + (f" The environment variable {services.SERVICES[args.name]['env']} is still set, and is still used." if left == "env" else ""))
    except services.UnknownService:
        say(f"There is no service called {args.name}. Known: {', '.join(services.SERVICES)}.")
        return 2
    except ValueError as exc:
        say(str(exc))
        return 2
    return 0


def cmd_login(args) -> int:
    """Sign this computer in to a Claude plan with the agent program that ships with the studio."""
    settings = Settings.from_env()
    if settings.shared:
        say(setup.SHARED_NO_PLAN)
        return 1
    from .harness.claude import find_cli
    cli = find_cli()
    if not cli:
        say("The Claude Code program was not found. Run install.sh again.")
        return 1
    os.execv(cli, [cli, "auth", "login"])
    return 0   # not reached


# -- setup -----------------------------------------------------------------------------------------
def _read_secret_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text("utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        raise setup.SetupError(f"No key could be read from {path}.") from None


def answers_from_flags(args, stdin=None) -> dict:
    """The answers a non-interactive `studio setup` gives: an answers file, flags, or both
    (flags win). A key never comes from a flag: it is read from a file, standard input or the
    environment, so it does not land in the shell history or the process list."""
    answers: dict = {}
    if args.answers:
        text = (stdin or sys.stdin).read() if args.answers == "-" else Path(args.answers).expanduser().read_text("utf-8")
        try:
            answers = json.loads(text)
        except ValueError:
            raise setup.SetupError("The answers file is not valid JSON.") from None
        if not isinstance(answers, dict):
            raise setup.SetupError("The answers file must hold a JSON object.")
    account = dict(answers.get("account") or {}) if isinstance(answers.get("account"), dict) else {}
    for flag, name in (("account", "account"), ("id", "id"), ("label", "label"), ("key_env", "api_key_env"),
                       ("base_url", "base_url"), ("region", "region"), ("project", "project"),
                       ("resource", "resource"), ("small_model", "small_model"), ("models", "models"),
                       ("default_model", "default_model"), ("api_version", "api_version")):
        value = getattr(args, flag, None)
        if value is not None:
            account[name] = value
    if getattr(args, "disable_betas", None) is not None:
        account["disable_betas"] = args.disable_betas
    if args.key_file:
        account["api_key"] = _read_secret_file(args.key_file)
    elif args.key_stdin:
        account["api_key"] = (stdin or sys.stdin).readline().strip()
    if isinstance(account.get("api_key_file"), str):
        account["api_key"] = _read_secret_file(account.pop("api_key_file"))
    if account:
        answers["account"] = account
    where = dict(answers.get("where") or {}) if isinstance(answers.get("where"), dict) else {}
    if args.port is not None:
        where["port"] = args.port
    if args.local:
        where["network"] = False
    if args.network is not None:
        where.update(network=True, host=args.network)
    if args.open_tailnet is not None:
        where["open_tailnet"] = args.open_tailnet
    if where:
        answers["where"] = where
    who = dict(answers.get("who") or {}) if isinstance(answers.get("who"), dict) else {}
    if args.shared is not None:
        who["shared"] = args.shared
    if args.access_key_file:
        who["access_key"] = _read_secret_file(args.access_key_file)
    if isinstance(who.get("access_key_file"), str):
        who["access_key"] = _read_secret_file(who.pop("access_key_file"))
    if who:
        answers["who"] = who
    if args.remove:
        answers["remove"] = [*(answers.get("remove") or []), *args.remove]
    return answers


def _choose(ask, question: str, options: list[tuple[str, str]], default: int) -> int:
    """Print numbered options, → the index chosen."""
    say(question)
    for index, (label, note) in enumerate(options, 1):
        say(f"  [{index}] {label}" + (f"\n      {note}" if note else ""))
    while True:
        raw = ask(f"Choice [{default + 1}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        say(f"  Type a number from 1 to {len(options)}.")


def _yes(ask, question: str, default: bool) -> bool:
    raw = ask(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not raw else raw in ("y", "yes")


def say_other_models() -> None:
    """Said wherever a model that is not Claude's is chosen."""
    note = setup.other_models()
    say("")
    say(f"   {note['headline']}")
    for limit in note["limits"]:
        say(f"   - {limit}")
    say("")


def fetch_models(provider: str, key: str | None, base_url: str | None) -> tuple[list[dict], str | None]:
    """→ (models, why not): the provider's own list, asked for with the key just typed.
    `provider` "gateway" asks a gateway the person runs (`GET <base_url>/v1/models`)."""
    import asyncio
    try:
        if provider == "gateway":
            return asyncio.run(gateway.list_gateway_models(base_url or "", key)), None
        return asyncio.run(gateway.list_models(provider, key, base_url)), None
    except gateway.GatewayError as exc:
        return [], str(exc)


SHOW_AT_MOST = 30


def _numbered(names: list[str]) -> None:
    for number, name in enumerate(names, 1):
        say(f"   {number:>3}. {name}")


def pick_from_list(ask, known: list[str], what: str) -> list[str]:
    """Choose models from a fetched list (or type them when there is none). A long list is never
    taken whole: the suggestion is offered, `/word` searches, numbers pick from what was shown
    last, and a name the list lacks is used only after a confirmation."""
    picked = gateway.pick_models(known, [])
    suggested = picked["suggested"]
    shown = list(suggested)
    if known:
        say(f"   {gateway.found_note(len(known), len(suggested))}")
        if shown:
            _numbered(shown)
        if len(known) > gateway.LONG_LIST:
            say("   Type names or numbers, or /word to search the whole list.")
    while True:
        hint = f" [{', '.join(suggested)}]" if suggested else ""
        typed = ask(f"{what} (exact names, comma separated){hint}: ").strip()
        if typed.startswith("/"):
            words = typed[1:].lower().split()
            matching = [n for n in known if all(w in n.lower() for w in words)]
            shown = matching[:SHOW_AT_MOST]
            if not shown:
                say("   Nothing in the list matches that.")
                continue
            _numbered(shown)
            if len(matching) > len(shown):
                say(f"   … and {len(matching) - len(shown)} more; search with more words.")
            continue
        if not typed and suggested:
            return list(suggested)
        names: list[str] = []
        bad_number = False
        for part in typed.replace(",", " ").split():
            if part.isdigit():
                if 1 <= int(part) <= len(shown):
                    names.append(shown[int(part) - 1])
                else:
                    bad_number = True
            elif part not in names:
                names.append(part)
        if bad_number:
            say("   A number is one of those shown just above.")
            continue
        if not names:
            say("   Name at least one.")
            continue
        unknown = gateway.pick_models(known, names)["unknown"]
        if unknown and not _yes(ask, f"   Not in the list: {', '.join(unknown)}. Use them anyway?", False):
            continue
        return list(dict.fromkeys(names))


def offer_litellm(ask, install=gateway.install) -> bool:
    """Ask before installing the studio's own LiteLLM. → whether it is installed afterwards."""
    found = gateway.installed()
    if found["installed"]:
        return True
    say("")
    say(f"   This account runs through LiteLLM, which the studio starts beside itself on this computer only.")
    say(f"   It is not installed yet: version {gateway.VERSION}, {gateway.SIZE_NOTE}, every package checked")
    say("   against a hash written in the studio's own files.")
    if not _yes(ask, "   Install it now?", True):
        say(f"   Not installed. The account is saved but stays unavailable until you run: {gateway.INSTALL_COMMAND}")
        return False
    try:
        install(lambda line: say(f"   {line}"))
    except gateway.GatewayError as exc:
        say(f"   {exc}")
        return False
    return True


def _ask_managed(ask, secret, name: str, meta: dict, account: dict) -> None:
    """The questions of an account that runs through the studio's own LiteLLM: the models (from
    the provider's list when the key allows, typed otherwise) and the one for background work."""
    provider = meta["provider"]
    listed, why = fetch_models(provider, account.get("api_key"), account.get("base_url")) \
        if meta.get("lists_models") and (account.get("api_key") or provider == "openrouter") else ([], None)
    known = [m["id"] for m in listed]
    prices = {m["id"]: m["price"] for m in listed if "price" in m}
    if known:
        say(f"   {gateway.PROVIDERS[provider]['label']} lists {len(known)} models for this key.")
    elif why:
        say(f"   {why}")
    what = "Deployments to offer" if provider == "azure-openai" else "Models to offer"
    names = pick_from_list(ask, known, what)
    account["models"] = names
    if len(names) > 1:
        default_model = ask(f"Default model [{names[0]}]: ").strip()
        if default_model:
            account["default_model"] = default_model
    guess = gateway.cheapest(names, prices) or account.get("default_model") or names[0]
    say("   The assistant also does small background jobs (titles, summaries). They can go to a cheaper model.")
    small = ask(f"Model for background work [{guess}]: ").strip() or guess
    account["small_model"] = small


def _ask_account(ask, secret, current: dict, login: dict, shared: bool) -> dict | None:
    names = [name for name, meta in setup.ACCOUNTS.items()
             if not (shared and meta["kind"] == "claude-subscription")]
    options = [(setup.ACCOUNTS[name]["label"]
                + (" (experimental)" if name in setup.OTHER_GROUP else ""), setup.ACCOUNTS[name]["summary"])
               for name in names]
    existing = current["connections"]
    if existing:
        options.append(("Keep what is set up now", ", ".join(f"{c['label']} ({c['id']})" for c in existing)))
    say("")
    say("1. Which model account should the assistant use?")
    say(f"   {login['detail']}")
    say(f"   {setup.OTHER_MODELS_HEADLINE}")
    if shared:
        default = len(options) - 1 if existing and any(c["kind"] != "claude-subscription" for c in existing) \
            else names.index("api-key")
    elif existing:
        default = len(options) - 1
    else:
        default = names.index("plan") if login["found"] else names.index("api-key")
    picked = _choose(ask, "", options, default)
    if picked >= len(names):
        return None
    name = names[picked]
    meta = setup.ACCOUNTS[name]
    account: dict = {"account": name}
    managed = meta["kind"] == gateway.KIND
    if name in setup.OTHER_GROUP:
        say_other_models()
    address = {"openai": "Address (empty for OpenAI itself; or an OpenAI-compatible service): ",
               "azure-openai": "Address of your Azure OpenAI resource (for example https://my-resource.openai.azure.com): "
               }.get(name, "Address (for example http://127.0.0.1:4000): ")
    for field_name, prompt in (("region", "Region (for example us-east-1): "), ("project", "Google Cloud project id: "),
                               ("resource", "Foundry resource name (or leave empty to give an address): "),
                               ("base_url", address),
                               ("api_version", "API version (for example 2024-10-21): "),
                               ("small_model", "Cheap model for background work and picking skills, as the gateway "
                                               "calls it (empty: the cheapest-looking of the models you list): ")):
        if field_name in meta["fields"]:
            if field_name == "base_url" and name == "foundry" and account.get("resource"):
                continue
            if field_name == "small_model" and (managed or meta["kind"] == "anthropic-gateway"):
                continue    # asked after the models are known
            value = ask(prompt).strip()
            if value:
                account[field_name] = value
    if meta["key"] != "none":
        optional = " (empty: use the machine's Entra sign-in)" if meta["key"] == "optional" else ""
        key = secret(f"Key{optional} (it is not shown as you type): ").strip()
        if key:
            account["api_key"] = key
    if managed:
        _ask_managed(ask, secret, name, meta, account)
        offer_litellm(ask)
        return account
    if meta["kind"] == "anthropic-gateway":
        listed_models, why = fetch_models("gateway", account.get("api_key"), account.get("base_url"))
        if why:
            say(f"   {why}")
        listed = pick_from_list(ask, [m["id"] for m in listed_models], "Models to offer")
        account["models"] = listed
        if not account.get("small_model") and len(listed) > 1:
            guess = gateway.cheapest(listed)
            small = ask(f"Cheap model for background work{f' [{guess}]' if guess else ' (empty: none)'}: ").strip()
            if small or guess:
                account["small_model"] = small or guess
    else:
        offered = ", ".join(meta["models"])
        models = ask(f"Models to offer{f' [{offered}]' if offered else ''}: ").strip()
        if models:
            account["models"] = models
        listed = [m for m in models.replace(",", " ").split()] if models else meta["models"]
    if len(listed) > 1:
        suggested = meta["default_model"] if meta["default_model"] in listed else listed[0]
        default_model = ask(f"Default model [{suggested}]: ").strip()
        if default_model:
            account["default_model"] = default_model
    return account


def interactive_answers(settings: Settings, ask=input, secret=getpass.getpass,
                        login=setup.detect_claude_login, tailscale=setup.detect_tailscale) -> dict:
    """Ask the three questions in the terminal. Enter keeps what is in brackets."""
    current = setup.configured(settings)
    store = settings.secret_store()
    say("Studio setup")
    say(f"  Settings go to {settings.config_path}" + (" (it exists; you will see what changes before it is written)."
                                                       if settings.config_path.is_file() else "."))
    say(f"  Keys you type are kept in {store.label}, never in that file.")
    found_login = login()
    found_tailscale = tailscale()
    answers: dict = {}

    account = _ask_account(ask, secret, current, found_login, shared=False)
    if account:
        answers["account"] = account

    say("")
    network = _choose(ask, "2. Where should the studio run?", [
        ("This computer only", "Nothing else can reach it."),
        ("Also on a private network address", found_tailscale["detail"]),
    ], 1 if current["hosts"] else 0)
    where: dict = {"network": bool(network)}
    if network:
        say(f"   {setup.NETWORK_WARNING}")
        suggested = (current["hosts"] or [found_tailscale.get("address") or ""])[0]
        host = ask(f"Address to listen on{f' [{suggested}]' if suggested else ''}: ").strip() or suggested
        where["host"] = host
        from .access import on_tailnet
        if on_tailnet(host):
            where["open_tailnet"] = bool(_choose(ask, "Devices on your Tailscale network:", [
                ("Ask each device for the access key once", ""),
                ("Let them in without a key", "Then your Tailscale network is the only boundary."),
            ], 1 if current["open_tailnet"] else 0))
    port = ask(f"Port [{current['port']}]: ").strip()
    if port:
        where["port"] = port
    answers["where"] = where

    say("")
    shared = bool(_choose(ask, "3. Who uses it?", [
        ("Just me", "Your own Claude plan may be used."),
        ("Shared with other people", "The Claude plan is switched off; everyone signs in with the access key."),
    ], 1 if current["shared"] else 0))
    answers["who"] = {"shared": shared}
    if shared:
        where.pop("open_tailnet", None)
        kinds = [setup.ACCOUNTS[account["account"]]["kind"]] if account else [c["kind"] for c in current["connections"]]
        if not any(kind != "claude-subscription" for kind in kinds):
            say("")
            say(f"   {setup.SHARED_NO_PLAN}")
            again = _ask_account(ask, secret, {**current, "connections": []}, found_login, shared=True)
            if again:
                answers["account"] = again
    return answers


def cmd_setup(args, ask=input, secret=getpass.getpass) -> int:
    settings = Settings.from_env()
    if args.show:
        say(json.dumps({"config": str(settings.config_path), "secrets": settings.secret_store().label,
                        **setup.configured(settings)}, indent=1))
        return 0
    flags = ("answers", "account", "key_env", "key_file", "key_stdin", "port", "local", "network", "remove",
             "models", "default_model", "access_key_file", "base_url", "region", "project", "resource",
             "small_model", "id", "label", "api_version")
    scripted = any(getattr(args, flag, None) not in (None, False, []) for flag in flags) or \
        args.shared is not None or args.open_tailnet is not None or args.disable_betas is not None
    try:
        if scripted:
            answers = answers_from_flags(args)
        elif sys.stdin.isatty() or ask is not input:
            answers = interactive_answers(settings, ask, secret)
        else:
            say("`studio setup` asks its questions in a terminal. Without one, give the answers:")
            say("  studio setup --answers answers.json        (see studio2/README.md for the shape)")
            say("  studio setup --account plan --local --single")
            return 2
        result = setup.plan(answers, settings)
    except setup.SetupError as exc:
        say(f"Setup stopped: {exc}")
        return 2
    except (EOFError, KeyboardInterrupt):
        say("\nSetup was left; nothing was written.")
        return 130

    say("")
    if not result.changed and result.exists:
        say("Nothing changes.")
        return 0
    for change in result.changes or ["Write the default settings."]:
        say(f"  • {change}")
    if result.exists:
        say("")
        say(result.diff(settings.config_path) or "(the config file itself does not change)")
    if args.dry_run:
        say("\nDry run: nothing was written.")
        return 0
    if result.exists and not args.yes:
        if scripted or not (sys.stdin.isatty() or ask is not input):
            say(f"\n{settings.config_path} exists and would change. Nothing was written; add --yes to write it.")
            return 2
        if not _yes(ask, "\nWrite this?", True):
            say("Nothing was written.")
            return 0
    setup.apply(result, settings)
    say(f"\nWrote {settings.config_path}." + (f" The previous one is {settings.config_path}.bak." if result.exists else ""))
    if result.generated_access_key:
        say("Access key for other devices (shown once here; `studio access-key` shows it again):")
        say(f"  {result.generated_access_key}")
    state = read_state(settings)
    if result.restart_needed and state:
        say("The running studio keeps its old address and access rules until it is restarted:  studio stop && studio start")
    elif not state:
        say("Start the studio with:  studio start")
    return 0


# -- the command line ------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="Studio: a page and an assistant that builds it.")
    parser.add_argument("--version", action="version", version=f"studio {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    start = commands.add_parser("start", help="start the studio and open it in the browser")
    start.add_argument("--port", type=int, help="port (default: the one from setup, else 8200, else the next free one)")
    start.add_argument("--data", help="data folder (default: $STUDIO2_DATA or <repo>/data/studio2)")
    start.add_argument("--foreground", action="store_true", help="stay in this terminal instead of the background")
    start.add_argument("--no-open", action="store_true", help="do not open the browser")
    start.add_argument("--fake", action="store_true", help="use the scripted agent (no model calls)")
    start.set_defaults(run=cmd_start)

    stop = commands.add_parser("stop", help="stop the studio that `studio start` started")
    stop.add_argument("--data", help="data folder of the studio to stop")
    stop.set_defaults(run=cmd_stop)

    status = commands.add_parser("status", help="say whether it is running, and where")
    status.add_argument("--data", help="data folder of the studio to look at")
    status.add_argument("--json", action="store_true")
    status.set_defaults(run=cmd_status)

    doctor = commands.add_parser("doctor", help="check what is installed and which model accounts work")
    doctor.add_argument("--data", help="data folder to check")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--online", action="store_true",
                        help="also ask each key's provider for its model list (free; no model is called)")
    doctor.set_defaults(run=cmd_doctor)

    config = commands.add_parser("setup", help="choose the model account, where it runs and who uses it")
    config.add_argument("--answers", metavar="FILE", help="a JSON file of answers (“-” reads standard input)")
    config.add_argument("--account", choices=[*setup.ACCOUNTS],
                        help="the model account to add or change")
    config.add_argument("--id", help="the connection's id (default: one per kind of account)")
    config.add_argument("--label")
    config.add_argument("--key-file", metavar="FILE", help="read the key from the first line of this file")
    config.add_argument("--key-stdin", action="store_true", help="read the key from standard input")
    config.add_argument("--key-env", metavar="NAME", help="leave the key in this environment variable instead")
    config.add_argument("--base-url", help="address of a gateway or a Foundry resource")
    config.add_argument("--region")
    config.add_argument("--project")
    config.add_argument("--resource")
    config.add_argument("--small-model", help="the model for background work (titles, summaries)")
    config.add_argument("--api-version", help="Azure OpenAI's API version")
    config.add_argument("--disable-betas", dest="disable_betas", action="store_true", default=None,
                        help="for a provider that rejects Anthropic's beta request fields (also turns off tool search)")
    config.add_argument("--models", help="models to offer, comma separated")
    config.add_argument("--default-model")
    config.add_argument("--remove", action="append", metavar="ID", help="remove a model account")
    config.add_argument("--port", type=int)
    config.add_argument("--local", action="store_true", help="this computer only")
    config.add_argument("--network", nargs="?", const="tailscale", metavar="ADDRESS",
                        help="also listen on a private address (default: this computer's Tailscale address)")
    config.add_argument("--open-tailnet", dest="open_tailnet", action="store_true", default=None,
                        help="devices on your Tailscale network need no access key")
    config.add_argument("--ask-key", dest="open_tailnet", action="store_false",
                        help="every other device signs in with the access key")
    config.add_argument("--shared", dest="shared", action="store_true", default=None,
                        help="shared with other people (the Claude plan is switched off)")
    config.add_argument("--single", dest="shared", action="store_false", help="used by you alone")
    config.add_argument("--access-key-file", metavar="FILE", help="use the access key in this file")
    config.add_argument("--yes", action="store_true", help="write an existing config file without asking")
    config.add_argument("--dry-run", action="store_true", help="show what would change and write nothing")
    config.add_argument("--show", action="store_true", help="print what is set up now")
    config.set_defaults(run=cmd_setup)

    lite = commands.add_parser("litellm", help="the LiteLLM the studio runs for OpenAI, OpenRouter, Azure and Gemini keys")
    lite.add_argument("action", choices=["install", "status"], nargs="?", default="status")
    lite.add_argument("--data", help="data folder of the studio to look at")
    lite.add_argument("--yes", action="store_true", help="install without asking")
    lite.set_defaults(run=cmd_litellm)

    key = commands.add_parser("access-key", help="print the access key other devices sign in with")
    key.set_defaults(run=cmd_access_key)
    keys = commands.add_parser("keys", help="keys for other services the studio calls (web search): list, set, remove")
    keys.set_defaults(run=cmd_keys)
    keys.add_argument("action", nargs="?", choices=("list", "set", "remove"), default="list")
    keys.add_argument("name", nargs="?", help="the service, for set and remove")
    keys.add_argument("--key-file", metavar="FILE", help="read the key from this file instead of asking")
    login = commands.add_parser("login", help="sign this computer in to your Claude plan")
    login.set_defaults(run=cmd_login)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 0
    return int(args.run(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
