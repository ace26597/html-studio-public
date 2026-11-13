"""The LiteLLM the studio runs itself (CONTRACT.md, "Any model (step 3n)").

A connection of kind `litellm` holds a provider's own key (OpenAI, OpenRouter, Azure OpenAI,
Gemini). The agent program only speaks Anthropic's message format, so the studio runs one
LiteLLM proxy beside itself that translates, and points the agent program at it exactly as it
points it at a gateway the person runs.

What this module guarantees:

- one exact LiteLLM release, installed only on a yes, into its own environment, with every
  package checked against the hashes committed in `litellm-requirements.txt`;
- a config file generated from the connections that holds no key (keys reach the process through
  its environment, read from the key store when it starts);
- 127.0.0.1 only, a free port, a random master key kept in the key store;
- started when a request needs it, stopped with the studio, restarted when it has died or when
  the connections changed; its output goes to a capped log with every known key blanked.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets as random_secrets
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config import STUDIO2_ROOT, Settings, connection_key, now_iso

log = logging.getLogger("studio2")

KIND = "litellm"
VERSION = "1.102.1"                 # the one release that is installed; see litellm-requirements.txt
REQUIREMENTS = STUDIO2_ROOT / "litellm-requirements.txt"
SERVICE = "studio2-litellm"         # key store service for the proxy's own master key
MASTER = "master-key"
LOG_CAP = 2_000_000                 # bytes; one older file is kept beside it
SPEND_CAP = 1_000_000
START_WAIT = 90.0
INSTALL_COMMAND = "studio litellm install"
SIZE_NOTE = "about 430 MB on disk"
SPEND_MODULE = "studio_spend"
TURN_HEADER = "x-studio-turn"

# provider → how LiteLLM names its models, and where the provider lists them.
PROVIDERS: dict[str, dict] = {
    # With an address of its own the connection is "something that speaks OpenAI's chat format"
    # (a local server, another gateway): LiteLLM 1.102 sends `openai/` models to OpenAI's newer
    # Responses API, which such services rarely have, so they go out as plain chat completions.
    "openai": {"label": "OpenAI", "prefix": "openai/", "compatible_prefix": "custom_openai/"},
    "openrouter": {"label": "OpenRouter", "prefix": "openrouter/"},
    "azure-openai": {"label": "Azure OpenAI", "prefix": "azure/"},
    "gemini": {"label": "Gemini", "prefix": "gemini/"},
}

SPEND_SOURCE = '''"""Written by the studio (gateway.py). Appends what LiteLLM says each request cost to a file
the studio reads: numbers, the model and the studio's own turn id. Never a key, never a prompt."""
import json
import os
import time

from litellm.integrations.custom_logger import CustomLogger

PATH = os.environ.get("STUDIO_SPEND_FILE") or ""


def _reported(response):
    """Whether the provider itself sent a cost with the answer (OpenRouter's usage.cost)."""
    try:
        usage = getattr(response, "usage", None)
        cost = usage.get("cost") if isinstance(usage, dict) else getattr(usage, "cost", None)
        return isinstance(cost, (int, float)) and cost > 0
    except Exception:
        return False


def _cached(response):
    """The cache counts of a request ({} when the answer does not say): prompt tokens include them."""
    try:
        usage = getattr(response, "usage", None)
        details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else getattr(usage, "prompt_tokens_details", None)
        read = lambda name: (details.get(name) if isinstance(details, dict) else getattr(details, name, None)) or 0
        if details is None:
            return {}       # the answer does not say: the studio will not price it from a guess
        return {"cached": int(read("cached_tokens")), "cache_write": int(read("cache_write_tokens") or read("cache_creation_tokens"))}
    except Exception:
        return {}


class Spend(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            params = kwargs.get("litellm_params") or {}
            headers = (params.get("proxy_server_request") or {}).get("headers") or {}
            detail = kwargs.get("standard_logging_object") or {}
            row = {"at": time.time(), "turn": headers.get("''' + TURN_HEADER + '''"),
                   "model": detail.get("model_group") or kwargs.get("model"),
                   "cost": kwargs.get("response_cost"),
                   "reported": _reported(response_obj),
                   "input": detail.get("prompt_tokens"), "output": detail.get("completion_tokens"),
                   **_cached(response_obj)}
            if PATH:
                with open(PATH, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\\n")
        except Exception:
            pass


handler = Spend()
'''


class GatewayError(Exception):
    """The managed LiteLLM cannot be used; the message is a plain sentence for the person."""


@dataclass
class Endpoint:
    base_url: str
    key: str
    notice: str | None = None       # said once in the turn when the proxy had to be started again


# -- where it is installed -----------------------------------------------------------------------
def home() -> Path:
    return Path(os.environ.get("STUDIO2_LITELLM_HOME") or STUDIO2_ROOT / ".tools" / "litellm").expanduser()


def program() -> Path:
    return home() / "venv" / "bin" / "litellm"


def requirements_hash() -> str | None:
    try:
        return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    except OSError:
        return None


def installed() -> dict:
    """→ {installed, version, path, detail}. Installed means: the program is there and it was put
    there from the requirements file as it is now (a changed pin asks for a reinstall)."""
    path = program()
    try:
        record = json.loads((home() / "installed.json").read_text("utf-8"))
    except (OSError, ValueError):
        record = {}
    if not path.is_file():
        return {"installed": False, "version": None, "path": str(path),
                "detail": f"LiteLLM is not installed. `{INSTALL_COMMAND}` installs version {VERSION} "
                          f"({SIZE_NOTE}), every package checked against a known hash."}
    if record.get("requirements_sha256") != requirements_hash():
        return {"installed": False, "version": record.get("version"), "path": str(path),
                "detail": f"The LiteLLM here was installed from another list of packages than this studio "
                          f"pins. Run `{INSTALL_COMMAND}` again."}
    return {"installed": True, "version": record.get("version") or VERSION, "path": str(path), "detail":
            f"LiteLLM {record.get('version') or VERSION}, in {home()}."}


def find_uv() -> str | None:
    for candidate in (shutil.which("uv"), STUDIO2_ROOT / ".tools/uv/uv", Path.home() / ".local/bin/uv",
                      Path.home() / ".cargo/bin/uv"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def install(say: Callable[[str], None] = lambda _line: None, run=subprocess.run) -> dict:
    """Install the pinned LiteLLM into its own environment. Every package must match a hash in the
    committed requirements file or nothing is installed. Raises GatewayError with what went wrong."""
    if not REQUIREMENTS.is_file():
        raise GatewayError(f"{REQUIREMENTS} is missing, so there is no checked list of packages to install from.")
    uv = find_uv()
    if not uv:
        raise GatewayError("uv was not found. Run studio2/install.sh first: it fetches uv.")
    target = home()
    venv = target / "venv"
    target.mkdir(parents=True, exist_ok=True)
    say(f"Installing LiteLLM {VERSION} into {target} ({SIZE_NOTE}; every package is checked against a hash).")
    steps = [
        [uv, "venv", "--quiet", "--clear", "--python", "3.12", str(venv)],
        # --require-hashes: a package whose bytes are not in the committed list stops the install.
        [uv, "pip", "install", "--quiet", "--require-hashes", "--python", str(venv / "bin" / "python"),
         "-r", str(REQUIREMENTS)],
    ]
    (target / "installed.json").unlink(missing_ok=True)
    for command in steps:
        try:
            done = run(command, capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GatewayError(f"LiteLLM could not be installed ({exc.__class__.__name__}).") from None
        if done.returncode != 0:
            tail = " ".join((done.stderr or done.stdout or "").split())[-600:]
            raise GatewayError(f"LiteLLM could not be installed: {tail or 'the installer failed.'}")
    if not program().is_file():
        raise GatewayError(f"The install finished but {program()} is not there.")
    record = {"version": VERSION, "requirements_sha256": requirements_hash(), "installed_at": now_iso()}
    (target / "installed.json").write_text(json.dumps(record, indent=1), "utf-8")
    say(f"LiteLLM {VERSION} is installed.")
    return installed()


# -- connections → config ------------------------------------------------------------------------
def managed(connections: list[dict]) -> list[dict]:
    return [c for c in connections if c.get("kind") == KIND]


def routed(connection_id: str, model: str) -> str:
    """The name a model has inside the studio's LiteLLM: the connection's id, then the model as
    the provider calls it. Two connections can therefore offer a model of the same name."""
    return f"{connection_id}/{model}"


def key_env(connection_id: str) -> str:
    return "STUDIO_KEY_" + re.sub(r"[^A-Z0-9]", "_", connection_id.upper())


def model_names(connection: dict) -> list[str]:
    """Every model the proxy must route for a connection: the offered ones and the background one."""
    names = [m["id"] if isinstance(m, dict) else str(m) for m in connection.get("models") or []]
    small = str(connection.get("small_model") or "").strip()
    if small and small not in names:
        names.append(small)
    return names


def small_model(connection: dict, model: str | None = None) -> str | None:
    """The model for background work: the one chosen in setup, else the cheapest-looking one
    offered, else the request's own."""
    chosen = str(connection.get("small_model") or "").strip()
    if chosen:
        return chosen
    names = [m["id"] if isinstance(m, dict) else str(m) for m in connection.get("models") or []]
    return cheapest(names) or model


CHEAP_WORDS = (":free", "/free", "nano", "flash-lite", "lite", "mini", "flash", "haiku", "small", "fast")


def cheapest(names: list[str], prices: Mapping[str, float] | None = None) -> str | None:
    """A guess at the cheapest model of a list: by the provider's price when it gave one, otherwise
    by the words providers put in the names of their small models. None when nothing stands out.
    Setup shows this as the suggestion; the person can name another."""
    if not names:
        return None
    priced = [(prices[name], name) for name in names if prices and isinstance(prices.get(name), (int, float))]
    if priced and len(priced) == len(names):
        return min(priced)[1]
    for word in CHEAP_WORDS:
        for name in names:
            if word in name.lower():
                return name
    return None


def model_entry(connection: dict, model: str) -> dict:
    provider = str(connection.get("provider") or "")
    meta = PROVIDERS.get(provider)
    if not meta:
        raise GatewayError(f"Connection “{connection.get('id')}” names a provider the studio's LiteLLM does not "
                           f"know: {provider or '(none)'}.")
    base = str(connection.get("base_url") or "").strip().rstrip("/")
    prefix = meta.get("compatible_prefix") if base and meta.get("compatible_prefix") else meta["prefix"]
    params: dict = {"model": prefix + model, "api_key": f"os.environ/{key_env(str(connection['id']))}"}
    if base:
        params["api_base"] = base
    if provider == "azure-openai":
        if not base:
            raise GatewayError(f"Connection “{connection['id']}” needs the Azure endpoint address.")
        if connection.get("api_version"):
            params["api_version"] = str(connection["api_version"])
    return {"model_name": routed(str(connection["id"]), model), "litellm_params": params}


def render(connections: list[dict]) -> dict:
    """The proxy's config for these connections. It names where each key is read from
    (`os.environ/NAME`) and never holds one."""
    models = [model_entry(connection, model) for connection in managed(connections)
              for model in model_names(connection)]
    return {
        "model_list": models,
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
        "litellm_settings": {"drop_params": True, "modify_params": True, "telemetry": False,
                             "callbacks": [f"{SPEND_MODULE}.handler"]},
    }


def config_text(config: dict) -> str:
    # JSON is YAML: no quoting rules to get wrong for a model name somebody typed.
    return ("# Written by the studio; rewritten whenever a model account changes. It holds no key:\n"
            "# keys are read from the studio's key store when LiteLLM starts.\n"
            + json.dumps(config, indent=2, ensure_ascii=True) + "\n")


def keys_for(connections: list[dict], parent: Mapping[str, str], store) -> dict[str, str]:
    """{environment name: key} for every managed connection whose key can be found."""
    found = {}
    for connection in managed(connections):
        key = connection_key(connection, parent, store)
        if key:
            found[key_env(str(connection["id"]))] = key
    return found


PASSED_THROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
                  "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                  "no_proxy")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_of(pid: int) -> str:
    try:
        done = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout


def read_state(folder: Path) -> dict | None:
    try:
        state = json.loads((folder / "state.json").read_text("utf-8"))
        return state if isinstance(state, dict) and int(state["pid"]) > 0 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def answers(port: int, timeout: float = 2.0) -> bool:
    """Whether a LiteLLM answers on this computer's own address at `port`. `/health/liveliness`
    needs no key and calls no model (`/health` would call every model, and be billed)."""
    import httpx
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health/liveliness", timeout=timeout, trust_env=False)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


class Sidecar:
    """The one LiteLLM process of a studio. `command` builds the argv from (config file, port);
    the tests pass a stand-in program."""

    def __init__(self, settings: Settings, command: Callable[[Path, int], list[str]] | None = None,
                 start_wait: float = START_WAIT):
        self.settings = settings
        self.folder = settings.data_dir / "litellm"
        self.command = command
        self.start_wait = start_wait
        self.process: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.fingerprint: str | None = None
        self.started_at: str | None = None
        self.last_exit: dict | None = None      # {code, at, tail}: it stopped without being asked
        self._key: str | None = None
        self._secrets: tuple[str, ...] = ()
        self._lock = asyncio.Lock()
        self._pump: asyncio.Task | None = None

    # -- files ---------------------------------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.folder / "config.yaml"

    @property
    def log_path(self) -> Path:
        return self.folder / "litellm.log"

    @property
    def spend_path(self) -> Path:
        return self.folder / "spend.jsonl"

    def master_key(self) -> str:
        store = self.settings.secret_store()
        key = store.get(SERVICE, MASTER)
        if not key:
            key = "sk-studio-" + random_secrets.token_urlsafe(32)
            store.set(SERVICE, MASTER, key)
        return key

    def _argv(self, port: int) -> list[str]:
        if self.command:
            return self.command(self.config_path, port)
        return [str(program()), "--config", str(self.config_path), "--host", "127.0.0.1", "--port", str(port)]

    # -- the log ---------------------------------------------------------------------------------
    def _write_log(self, text: str) -> None:
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "[key]")
        try:
            if self.log_path.is_file() and self.log_path.stat().st_size + len(text) > LOG_CAP:
                os.replace(self.log_path, self.log_path.with_name(self.log_path.name + ".1"))
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            log.debug("Could not write %s", self.log_path)

    async def _pump_output(self, process: asyncio.subprocess.Process) -> None:
        """Everything the proxy prints goes through here: known keys are blanked and the file is capped."""
        assert process.stdout is not None
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):   # one enormous line: drop it
                continue
            if not line:
                break
            self._write_log(line.decode("utf-8", "replace"))
        code = await process.wait()
        if self.process is process:     # nobody asked it to stop
            self.last_exit = {"code": code, "at": now_iso(), "tail": self.log_tail(6)}
            log.warning("The studio's LiteLLM stopped by itself (exit code %s).", code)
            self.process = None
            (self.folder / "state.json").unlink(missing_ok=True)

    def log_tail(self, lines: int = 12) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except OSError:
            return ""

    # -- start and stop ----------------------------------------------------------------------------
    def _fingerprint(self, config: dict, keys: dict[str, str], master: str) -> str:
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
        for name in sorted(keys):
            digest.update(name.encode() + b"=" + hashlib.sha256(keys[name].encode()).digest())
        digest.update(hashlib.sha256(master.encode()).digest())
        return digest.hexdigest()

    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _kill_orphan(self) -> None:
        """A proxy left behind by a studio that was killed: stopped before a new one starts. Only
        when the pid is still a program that was started on this folder's config file."""
        state = read_state(self.folder)
        if not state:
            return
        pid = int(state["pid"])
        if _alive(pid) and str(self.config_path) in await asyncio.to_thread(_command_of, pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(25):
                if not _alive(pid):
                    break
                await asyncio.sleep(0.2)
            else:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        (self.folder / "state.json").unlink(missing_ok=True)

    async def ensure(self, connections: list[dict], parent: Mapping[str, str] | None = None) -> Endpoint:
        """Make sure the proxy runs with these connections; → where it is and its key. Starts it,
        or starts it again when it has died or the connections or their keys changed."""
        parent = os.environ if parent is None else parent
        async with self._lock:
            if not self.command and not installed()["installed"]:
                raise GatewayError(installed()["detail"])
            config = render(connections)
            if not config["model_list"]:
                raise GatewayError("No model account needs the studio's LiteLLM.")
            store = self.settings.secret_store()
            keys = await asyncio.to_thread(keys_for, connections, parent, store)
            master = await asyncio.to_thread(self.master_key)
            fingerprint = self._fingerprint(config, keys, master)
            notice = None
            if self.running() and self.fingerprint == fingerprint and self.port:
                if await asyncio.to_thread(answers, self.port):
                    return Endpoint(f"http://127.0.0.1:{self.port}", master)
                notice = "The studio's LiteLLM had stopped answering; it was started again."
                await self._stop()
            elif self.running():
                await self._stop()      # the connections or a key changed
            elif self.last_exit:
                notice = (f"The studio's LiteLLM had stopped (exit code {self.last_exit['code']}); "
                          "it was started again.")
            await self._start(config, keys, master, fingerprint)
            return Endpoint(f"http://127.0.0.1:{self.port}", master, notice)

    async def _start(self, config: dict, keys: dict[str, str], master: str, fingerprint: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.folder, 0o700)
        await self._kill_orphan()
        self.config_path.write_text(config_text(config), "utf-8")
        (self.folder / f"{SPEND_MODULE}.py").write_text(SPEND_SOURCE, "utf-8")
        with contextlib.suppress(OSError):
            if self.spend_path.stat().st_size > SPEND_CAP:
                self.spend_path.unlink()
        port = _free_port()
        env = {name: os.environ[name] for name in PASSED_THROUGH if os.environ.get(name)}
        # The bundled price list, not one fetched from the network at every start.
        env.update({"LITELLM_MASTER_KEY": master, "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                    "STUDIO_SPEND_FILE": str(self.spend_path), **keys})
        self._secrets = tuple(sorted({master, *keys.values()}, key=len, reverse=True))
        self.last_exit = None
        self._write_log(f"\n--- LiteLLM start {now_iso()} port {port}, {len(config['model_list'])} model(s) ---\n")
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv(port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, cwd=str(self.folder), env=env, start_new_session=True,
                limit=1024 * 1024)
        except OSError as exc:
            raise GatewayError(f"The studio's LiteLLM could not be started ({exc}).") from None
        self.process, self.port, self.fingerprint, self.started_at = process, port, fingerprint, now_iso()
        self._key = master
        self._pump = asyncio.create_task(self._pump_output(process))
        (self.folder / "state.json").write_text(json.dumps(
            {"pid": process.pid, "port": port, "started_at": self.started_at, "version": VERSION}), "utf-8")
        deadline = time.monotonic() + self.start_wait
        while time.monotonic() < deadline:
            if process.returncode is not None:
                await asyncio.sleep(0.05)     # let the pump write the last lines
                tail = " ".join(self.log_tail(8).split())[-500:]
                raise GatewayError(f"The studio's LiteLLM stopped while starting (exit code {process.returncode}). "
                                   f"The end of {self.log_path}: {tail}")
            if await asyncio.to_thread(answers, port, 1.0):
                return
            await asyncio.sleep(0.3)
        await self._stop()
        raise GatewayError(f"The studio's LiteLLM did not answer within {int(self.start_wait)} seconds. "
                           f"See {self.log_path}.")

    async def _stop(self) -> None:
        process, self.process = self.process, None      # cleared first: the pump then knows it was asked
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 8)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
        if self._pump is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._pump, 3)
            self._pump = None
        self.port = self.fingerprint = self.started_at = None
        (self.folder / "state.json").unlink(missing_ok=True)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    # -- what a turn cost ----------------------------------------------------------------------------
    def spend(self, turn_id: str, rates: tuple = ()) -> dict | None:
        """What LiteLLM reported for one turn's requests: {cost_usd, requests, unpriced, basis, backup},
        or None when it logged no request of the turn. A request LiteLLM had no price for is priced by
        the studio from `rates` (the backup, CONTRACT.md) when its row says how many tokens were
        cached; otherwise it is unpriced, never free. `cost_usd` is None when none was priced."""
        from . import prices

        total, count, priced, reported = 0.0, 0, 0, 0
        pricer = prices.Pricer(*rates)
        try:
            lines = self.spend_path.read_text("utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("turn") != turn_id:
                continue
            count += 1
            if isinstance(row.get("cost"), (int, float)) and row["cost"] > 0:
                total += float(row["cost"])
                priced += 1
                reported += row.get("reported") is True
            elif isinstance(row.get("cached"), int) and isinstance(row.get("input"), int) and isinstance(row.get("output"), int):
                read, write = row["cached"], row.get("cache_write") or 0
                worked = pricer.price(row.get("model"), {
                    "input": max(0, row["input"] - read - write), "cache_read": read, "cache_write": write,
                    "output": row["output"], "prompt": row["input"]})
                if worked is not None:
                    total += worked
                    priced += 1
        if not count:
            return None
        backup = pricer.summary()
        basis = None if not priced else "backup" if backup else "reported" if reported == priced else "calculated"
        return {"cost_usd": round(total, 6) if priced else None, "requests": count, "unpriced": count - priced,
                "basis": basis, "backup": backup}

    # -- what the library and the doctor show ----------------------------------------------------------
    def status(self, connections: list[dict]) -> dict:
        found = installed()
        needs = managed(connections)
        base = {"installed": found["installed"], "version": found["version"] or VERSION, "pinned": VERSION,
                "needed": bool(needs), "connections": [str(c["id"]) for c in needs],
                "port": self.port if self.running() else None,
                "pid": self.process.pid if self.running() and self.process else None,
                "started_at": self.started_at if self.running() else None,
                "last_exit": self.last_exit, "log": str(self.log_path), "install_command": INSTALL_COMMAND,
                "size": SIZE_NOTE}
        if not found["installed"] and not self.command:
            return {**base, "state": "not_installed", "detail": found["detail"]}
        if self.running():
            return {**base, "state": "running", "detail":
                    f"Running on this computer only (127.0.0.1:{self.port}), version {base['version']}."}
        if self.last_exit:
            return {**base, "state": "stopped", "detail":
                    f"It stopped by itself (exit code {self.last_exit['code']}) at {self.last_exit['at']}. "
                    "It starts again with the next request that needs it."}
        return {**base, "state": "idle", "detail":
                "Not running. It starts when a request needs it and stops with the studio." if needs else
                "Installed. No model account needs it yet."}


# -- a provider's own list of models ---------------------------------------------------------------
LIST_URLS = {
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
}
NOT_CHAT = ("embedding", "whisper", "tts", "dall-e", "moderation", "image", "audio", "realtime", "transcribe",
            "davinci", "babbage", "aqa", "imagen", "veo")


def _price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_models(provider: str, data) -> list[dict]:
    """A provider's answer → [{id, price?}], chat models only, cheapest first where prices are
    given. `price` is US dollars per million input tokens."""
    found: list[dict] = []
    if provider == "gemini":
        for item in (data.get("models") if isinstance(data, dict) else None) or []:
            name = str(item.get("name") or "").removeprefix("models/")
            if name and "generateContent" in (item.get("supportedGenerationMethods") or []):
                found.append({"id": name})
    else:
        for item in (data.get("data") if isinstance(data, dict) else None) or []:
            name = str(item.get("id") or "") if isinstance(item, dict) else ""
            if not name:
                continue
            entry: dict = {"id": name}
            price = _price((item.get("pricing") or {}).get("prompt")) if isinstance(item.get("pricing"), dict) else None
            if price is not None:
                entry["price"] = round(price * 1_000_000, 4)
            found.append(entry)
    found = [m for m in found if not any(word in m["id"].lower() for word in NOT_CHAT)]
    return sorted(found, key=lambda m: (m.get("price") is None, m.get("price") or 0, m["id"]))


async def list_models(provider: str, key: str | None, base_url: str | None = None, client=None) -> list[dict]:
    """Ask the provider which models this key may use. It is the provider's free listing call;
    no model is run. Raises GatewayError with the reason when there is no such call or it fails."""
    import httpx
    if provider == "azure-openai":
        raise GatewayError("Azure has no list of your deployments that a key can read. Type their names.")
    if provider not in LIST_URLS:
        raise GatewayError("This kind of account has no list of models to fetch. Type their names.")
    url = LIST_URLS[provider]
    if provider == "openai" and base_url:
        url = base_url.rstrip("/") + "/models"
    headers = {}
    if provider == "gemini":
        if not key:
            raise GatewayError("Enter the key first: Google lists its models only with one.")
        headers["x-goog-api-key"] = key
    elif key:
        headers["authorization"] = f"Bearer {key}"
    elif provider == "openai":
        raise GatewayError("Enter the key first: the list is fetched with it.")
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), trust_env=False)
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise GatewayError(f"{PROVIDERS[provider]['label']} could not be reached ({exc.__class__.__name__}). "
                           "Type the model names instead.") from None
    finally:
        if own:
            await client.aclose()
    if response.status_code in (401, 403):
        raise GatewayError(f"{PROVIDERS[provider]['label']} refused the key (HTTP {response.status_code}).")
    if response.status_code >= 400:
        raise GatewayError(f"{PROVIDERS[provider]['label']} answered HTTP {response.status_code} to a request "
                           "for its models. Type the model names instead.")
    try:
        models = parse_models(provider, response.json())
    except ValueError:
        models = []
    if not models:
        raise GatewayError(f"{PROVIDERS[provider]['label']} sent no list of models. Type their names.")
    return models


# -- a gateway the person runs, and narrowing a long list -------------------------------------------
LONG_LIST = 12          # above this many, setup suggests a short list instead of offering them all
SUGGEST_AT_MOST = 8
# The preference table (CONTRACT.md, "The four accounts"): families in the order they are offered.
# What setup suggests from a long list, in this order (the owner's choice, 13 November 2026): Claude's Opus and Sonnet
# first, then GPT, the newest two of each. Haiku comes last because the studio wants a cheap model for
# its background picks. Gemini and the rest are not suggested; the person can still search for them.
PREFERRED_FAMILIES = ("claude-opus", "claude-sonnet", "gpt-", "claude-haiku")


async def list_gateway_models(base_url: str, key: str | None, client=None) -> list[dict]:
    """`GET <base_url>/v1/models` on a gateway the person runs (LiteLLM and the like), with the
    key as a Bearer token. Raises GatewayError with a plain reason; the key is never in it."""
    import httpx
    address = (base_url or "").rstrip("/")
    if not address.startswith(("http://", "https://")):
        raise GatewayError("Give the gateway's address first, for example http://127.0.0.1:4000.")
    url = address if address.endswith("/v1/models") else (
        address + "/models" if address.endswith("/v1") else address + "/v1/models")
    headers = {"authorization": f"Bearer {key}"} if key else {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), trust_env=False)
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise GatewayError(f"The gateway could not be reached ({exc.__class__.__name__}). "
                           "Type the model names instead.") from None
    finally:
        if own:
            await client.aclose()
    if response.status_code in (401, 403):
        raise GatewayError(f"The gateway refused the key (HTTP {response.status_code}). "
                           "Check the key, or type the model names instead.")
    if response.status_code >= 400:
        raise GatewayError(f"The gateway answered HTTP {response.status_code} to a request for its models. "
                           "Type the model names instead.")
    try:
        data = response.json()
    except ValueError:
        data = None
    items = data.get("data") if isinstance(data, dict) else None
    seen: list[str] = []
    for item in items if isinstance(items, list) else []:
        name = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
        if name and name not in seen:
            seen.append(name)
    if not seen:
        raise GatewayError("The gateway sent no list of models. Type their names.")
    return [{"id": name} for name in sorted(seen)]


def _natural(name: str) -> list:
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", name.lower())]


def suggest_models(ids: list[str], limit: int = SUGGEST_AT_MOST) -> list[str]:
    """A short list from a fetched one: the whole list when it is short, else the ids that belong
    to the families of PREFERRED_FAMILIES, newest of each first (natural sort, descending), at
    most `limit`. Only ids from `ids`; nothing is made up, so it may be empty."""
    if len(ids) <= LONG_LIST:
        return list(ids)
    picked: list[str] = []
    for family in PREFERRED_FAMILIES:
        members = sorted((i for i in ids if family in i.lower() and not any(w in i.lower() for w in NOT_CHAT)),
                         key=_natural, reverse=True)
        for name in members[:2]:
            if name not in picked and len(picked) < limit:
                picked.append(name)
    return picked


def pick_models(ids: list[str], named: list[str]) -> dict:
    """→ {models, unknown, suggested, total}: the named models when there are some (`unknown` are
    the ones a fetched list lacks; with no list nothing is unknown), else the suggestion."""
    suggested = suggest_models(ids)
    if named:
        unknown = [n for n in named if ids and n not in ids]
        return {"models": list(named), "unknown": unknown, "suggested": suggested, "total": len(ids)}
    return {"models": suggested, "unknown": [], "suggested": suggested, "total": len(ids)}


def found_note(total: int, suggested: int) -> str:
    return (f"{total} models found; {suggested} suggested." if total > LONG_LIST else f"{total} models found.")
