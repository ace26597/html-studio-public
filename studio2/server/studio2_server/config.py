"""Settings and the connection list (see CONTRACT.md, "Connection")."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

log = logging.getLogger("studio2")

STUDIO2_ROOT = Path(__file__).resolve().parents[2]  # .../studio2
REPO_ROOT = STUDIO2_ROOT.parent

DEFAULT_CONNECTIONS = [
    {"id": "subscription", "label": "Claude subscription", "kind": "claude-subscription",
     "models": ["opus", "sonnet", "haiku"]},
]
FAKE_CONNECTION = {"id": "fake", "label": "Fake (no model calls)", "kind": "fake", "models": ["fake"]}
KINDS = {"claude-subscription", "anthropic-api", "anthropic-gateway", "litellm", "bedrock", "vertex", "foundry",
         "fake"}
# Kinds that reach a model through something that translates for it: a gateway the person runs,
# or the LiteLLM the studio runs itself (gateway.py). Models that are not Claude's live here.
TRANSLATED_KINDS = ("anthropic-gateway", "litellm")
CLAUDE_WORDS = ("claude", "opus", "sonnet", "haiku", "fable")
OTHER_MODELS_HEADLINE = "Claude models work best with the studio; other models are experimental."
OTHER_MODELS_LIMITS = [
    "Anthropic does not support running its agent program on other models. It works here by translating "
    "every request, and an update on either side can break it.",
    "The built-in web search does not work on this route. The studio's own search tool does, so the "
    "assistant can still look things up.",
    "It can cost more than the same work on a Claude model: prompt caching is not passed on, so the whole "
    "conversation is paid for again at every step.",
    "How well edits land depends on the model. “Check this model” in the Library runs a fixed request "
    "and says what worked.",
]
DEFAULT_PORT = 8200
SHARED_NO_PLAN = ("The Claude plan is switched off because this studio is shared with other people. "
                  "Use an API key or a cloud account instead.")
# A shared studio that names its owner: the plan is that one person's (CONTRACT.md, "The owner of a shared studio").
GUEST_NO_PLAN = "The Claude plan is this studio's owner's. Use another model account."


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _port_range(raw: str | None) -> tuple[int, int]:
    if raw:
        low, _, high = raw.partition("-")
        return int(low), int(high or low)
    return 8201, 8299


def range_for(port: int) -> tuple[int, int]:
    """Project pages for a studio on `port`: the 99 ports after it (8201–8299 for 8200). Each
    project's own back end is its page port + 100, so studios sit 200 ports apart."""
    return port + 1, port + 99


def library_places(from_env: str | None, from_file) -> tuple:
    """More places to look for skills or connectors: the variable (paths joined by the system's path
    separator) wins over the config file's list. Each is expanded; none is checked here."""
    if from_env and from_env.strip():
        raw = [part for part in from_env.split(os.pathsep)]
    else:
        raw = from_file if isinstance(from_file, list) else []
    seen, out = set(), []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(Path(text).expanduser())
    return tuple(out)


def studios_dir() -> Path:
    """Where every studio on this computer records itself (CONTRACT.md, "What an agent's shell may
    not reach"): one file per data folder. STUDIO2_STUDIOS_DIR moves it (tests, a throwaway studio)."""
    raw = os.environ.get("STUDIO2_STUDIOS_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".local" / "state" / "studio2" / "studios"


def studio_record(settings: "Settings") -> dict:
    """What another studio must know of this one to keep its agents out: its ports, its folders
    and the addresses and names it answers on. The config file itself is recorded, and its folder
    only when that is a folder of its own (`STUDIO2_CONFIG=~/studio.json` must not make the whole
    home folder the studio's)."""
    config_file = Path(settings.config_path).expanduser()
    folder, home = config_file.parent.resolve(), Path.home().resolve()
    own_folder = folder != home and not home.is_relative_to(folder)
    return {"port": settings.port, "port_range": list(settings.port_range), "data_dir": str(settings.data_dir),
            "config_file": str(config_file), "config_dir": str(config_file.parent) if own_folder else None,
            "records": str(studios_dir()), "hosts": list(settings.hosts), "names": list(settings.names),
            "pid": os.getpid()}


def _valid_record(record) -> dict | None:
    """A record as another studio wrote it, with every field of the right type; None otherwise."""
    if not isinstance(record, dict):
        return None
    try:
        port = record["port"]
        low, high = record["port_range"]
        if not all(isinstance(n, int) and not isinstance(n, bool) and 0 < n < 65536 for n in (port, low, high)):
            return None
        if not isinstance(record["data_dir"], str) or not record["data_dir"]:
            return None
        for key in ("config_file", "config_dir", "records"):
            if record.get(key) is not None and not isinstance(record[key], str):
                return None
        for key in ("hosts", "names"):
            if not isinstance(record.get(key, []), list) or not all(isinstance(h, str) for h in record.get(key, [])):
                return None
        if record.get("pid") is not None and (not isinstance(record["pid"], int) or isinstance(record["pid"], bool)):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return record


def _gone(record: dict) -> bool:
    """Has the studio that wrote this record stopped: its process is not there and its port does not
    answer (a short look, so a request never waits on it)?"""
    pid = record.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            return False
        except PermissionError:
            return False
        except OSError:
            pass
    import socket
    try:
        with socket.create_connection(("127.0.0.1", record["port"]), timeout=0.2):
            return False
    except OSError:
        return True


def _record_name(data_dir: Path) -> str:
    import hashlib
    return hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode()).hexdigest()[:16] + ".json"


def record_studio(settings: "Settings") -> None:
    """Called when a studio starts (__main__.serve). A failure is logged, never fatal: the studio's
    own block of ports and folders is known to it without the file."""
    folder = studios_dir()
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / _record_name(settings.data_dir)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(studio_record(settings), indent=1), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not record this studio in %s (%s).", folder, exc)


def known_studios(settings: "Settings") -> list[dict]:
    """This studio, then every other one recorded on this computer. A record with a field of the
    wrong type is skipped; one whose data folder is gone, or whose studio has stopped (no process,
    no answer on its port), is removed. This studio's own is never removed here."""
    found, mine = [studio_record(settings)], _record_name(settings.data_dir)
    folder = studios_dir()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name == mine:
            continue
        try:
            record = _valid_record(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            continue
        if record is None:
            continue
        if not Path(record["data_dir"]).exists() or _gone(record):
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        found.append(record)
    return found


def read_config(path: Path) -> dict:
    """The config file as a dict; {} when it is missing or not a JSON object."""
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s (%s).", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _flag(value: str | None) -> bool | None:
    value = (value or "").strip().lower()
    return True if value in ("1", "true", "yes") else False if value in ("0", "false", "no") else None


@dataclass
class Settings:
    data_dir: Path
    port: int = 8200
    port_range: tuple[int, int] = (8201, 8299)
    fake: bool = False
    config_path: Path = field(default_factory=lambda: Path.home() / ".config/studio2/config.json")
    web_dist: Path = STUDIO2_ROOT / "web" / "dist"
    # The guide and the benchmarks (CONTRACT.md, "The guide and the benchmarks, inside the studio").
    site_dir: Path = REPO_ROOT / "docs"
    library_dir: Path = STUDIO2_ROOT / "library"
    skill_dirs: tuple = ()          # more folders of skills on this machine (config `library.skill_dirs`)
    connector_files: tuple = ()     # more files of connectors (MCP servers), each like ~/.claude.json
    ping_seconds: float = 15.0
    hosts: tuple[str, ...] = ()        # extra addresses to listen on (STUDIO2_HOSTS); loopback is always on
    access_key: str | None = None      # required when hosts is set (STUDIO2_ACCESS_KEY), unless open_tailnet
    open_tailnet: bool = False         # Tailscale devices need no key (STUDIO2_OPEN_TAILNET=1); hosts must be tailnet addresses
    tls: tuple[str, str] | None = None  # (certificate file, key file): the extra addresses then speak HTTPS
    names: tuple[str, ...] = ()        # more DNS names the studio answers to (STUDIO2_NAMES); see access.known_names
    # A gate in front that signs people in (CONTRACT, "Access from other devices"): its signed token
    # stands in for the access key. (keys address, audiences, header name, issuer or None)
    gate: tuple[str, tuple[str, ...], str, str | None] | None = None
    # Names for project pages behind an address that carries one port (STUDIO2_PAGE_HOSTS):
    # {port: name}, and under None the name with {port} for every other port. See access.page_hosts.
    page_hosts: dict | None = None
    app_model_daily_usd: float = 2.0   # what one project's back end may spend on models per day (STUDIO2_APP_MODEL_DAILY_USD)
    # How many requests may run at once over all projects (STUDIO2_MAX_TURNS; 0: no limit). A working
    # agent's processes peak near 0.8 GB (CONTRACT.md, "Room for a few requests at once").
    max_turns: int = 4
    # Files larger than this are left out of a project's versions (STUDIO2_VERSION_MAX_FILE_MB, default
    # 100; 0: no limit). Kept in bytes so tests can use a tiny one. CONTRACT.md, "Files too large for versions".
    version_max_file_bytes: int = 100 * 1024 * 1024
    generate_daily_credits: float = 100.0   # what the studio may spend on generated media per day (STUDIO2_GENERATE_DAILY_CREDITS)
    home: Path = field(default_factory=Path.home)   # where this machine's own skills and MCP servers are read from
    # Shared with other people (setup's "who uses it", or STUDIO2_SHARED=1): the Claude plan is
    # switched off and every device must sign in with the access key.
    shared: bool = False
    # The owner of a shared studio, by the email the gate in front signs in (STUDIO2_OWNERS, or
    # `owners` in the config file). Lower case. CONTRACT.md, "The owner of a shared studio".
    owners: tuple[str, ...] = ()
    _secrets: object | None = field(default=None, repr=False, compare=False)

    def secret_store(self):
        """Where keys are kept (keystore.py). Opened on first use; nothing is read until asked for."""
        if self._secrets is None:
            from .keystore import open_store
            self._secrets = open_store(self.config_path.parent, self.fake)
        return self._secrets

    @classmethod
    def from_env(cls, port: int | None = None, data: str | None = None) -> "Settings":
        """Environment first, then what setup wrote in the config file (`run`, `shared`,
        `access_key_secret`), then the defaults. A config file without those keys, or no file,
        gives exactly what the environment alone gave before setup existed."""
        env = os.environ
        config_path = (Path(env["STUDIO2_CONFIG"]).expanduser() if env.get("STUDIO2_CONFIG")
                       else Path.home() / ".config/studio2/config.json")
        file = read_config(config_path)
        run = file.get("run") if isinstance(file.get("run"), dict) else {}
        try:
            configured_port = int(run.get("port") or 0) or None
        except (TypeError, ValueError):
            configured_port = None
        port = port or configured_port or DEFAULT_PORT
        if env.get("STUDIO2_PORT_RANGE"):
            port_range = _port_range(env["STUDIO2_PORT_RANGE"])
        else:   # a port chosen in setup brings its own block of project ports; --port alone does not
            port_range = range_for(port) if port == configured_port else _port_range(None)
        settings = cls(
            data_dir=Path(data or env.get("STUDIO2_DATA") or REPO_ROOT / "data" / "studio2").expanduser(),
            port=port,
            port_range=port_range,
            fake=env.get("STUDIO2_FAKE") == "1",
            config_path=config_path,
        )
        if "STUDIO2_HOSTS" in env:
            hosts = env["STUDIO2_HOSTS"].split(",")
        else:
            hosts = run.get("hosts") if isinstance(run.get("hosts"), list) else []
        settings.hosts = tuple(str(h).strip() for h in hosts if str(h).strip())
        settings.names = tuple(n.strip() for n in (env.get("STUDIO2_NAMES") or "").split(",") if n.strip())
        if "STUDIO2_OPEN_TAILNET" in env:
            settings.open_tailnet = env["STUDIO2_OPEN_TAILNET"] == "1"
        else:
            settings.open_tailnet = run.get("open_tailnet") is True
        places = file.get("library") if isinstance(file.get("library"), dict) else {}
        settings.skill_dirs = library_places(env.get("STUDIO2_SKILL_DIRS"), places.get("skill_dirs"))
        settings.connector_files = library_places(env.get("STUDIO2_CONNECTOR_FILES"), places.get("connector_files"))
        shared = _flag(env.get("STUDIO2_SHARED"))
        settings.shared = shared if shared is not None else file.get("shared") is True
        named = env.get("STUDIO2_OWNERS")
        listed = named.split(",") if named is not None else (file.get("owners") if isinstance(file.get("owners"), list) else [])
        settings.owners = tuple(sorted({item.strip().lower() for item in listed if isinstance(item, str) and "@" in item}))
        settings.access_key = (env.get("STUDIO2_ACCESS_KEY") or "").strip() or None
        if not settings.access_key and file.get("access_key_secret") and (settings.hosts or settings.shared):
            from .keystore import ACCESS
            settings.access_key = settings.secret_store().get(ACCESS, str(file["access_key_secret"])) or None
        try:
            settings.max_turns = max(0, int(env["STUDIO2_MAX_TURNS"])) if env.get("STUDIO2_MAX_TURNS", "").strip() else 4
        except ValueError:
            settings.max_turns = 4
        try:
            raw = (env.get("STUDIO2_VERSION_MAX_FILE_MB") or "").strip()
            settings.version_max_file_bytes = int(max(0.0, float(raw)) * 1024 * 1024) if raw else 100 * 1024 * 1024
        except ValueError:
            settings.version_max_file_bytes = 100 * 1024 * 1024
        try:
            settings.generate_daily_credits = max(0.0, float(env.get("STUDIO2_GENERATE_DAILY_CREDITS") or 100.0))
        except ValueError:
            settings.generate_daily_credits = 100.0
        try:
            settings.app_model_daily_usd = max(0.0, float(env.get("STUDIO2_APP_MODEL_DAILY_USD") or 2.0))
        except ValueError:
            settings.app_model_daily_usd = 2.0
        if (env.get("STUDIO2_PAGE_HOSTS") or "").strip():
            from .access import page_hosts
            settings.page_hosts = page_hosts(env["STUDIO2_PAGE_HOSTS"])
        gate_keys = (env.get("STUDIO2_GATE_KEYS") or "").strip()
        gate_audience = tuple(a.strip() for a in (env.get("STUDIO2_GATE_AUDIENCE") or "").split(",") if a.strip())
        if gate_keys or gate_audience:
            settings.gate = (gate_keys, gate_audience,
                             (env.get("STUDIO2_GATE_HEADER") or "").strip() or "Cf-Access-Jwt-Assertion",
                             (env.get("STUDIO2_GATE_ISSUER") or "").strip() or None)
        cert, key = (env.get("STUDIO2_TLS_CERT") or "").strip(), (env.get("STUDIO2_TLS_KEY") or "").strip()
        settings.tls = (str(Path(cert).expanduser()), str(Path(key).expanduser())) if cert and key else None
        return settings

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def allowed_origins(self) -> set[str]:
        ports = {DEFAULT_PORT, 5200, self.port}
        return {f"http://{host}:{p}" for host in ("127.0.0.1", "localhost") for p in ports}


def _normalise(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or not raw.get("id") or raw.get("kind") not in KINDS:
        return None
    models = []
    for model in raw.get("models") or []:
        if isinstance(model, str):
            models.append({"id": model, "label": model.capitalize() if model.isalpha() else model})
        elif isinstance(model, dict) and model.get("id"):
            models.append({"id": str(model["id"]), "label": str(model.get("label") or model["id"])})
    default = raw.get("default_model") or (models[0]["id"] if models else None)
    return {**raw, "id": str(raw["id"]), "label": str(raw.get("label") or raw["id"]),
            "models": models, "default_model": default}


def is_claude_model(model: str) -> bool:
    low = str(model or "").lower()
    return any(word in low for word in CLAUDE_WORDS)


def is_experimental(connection: dict, model: str | None) -> bool:
    """A model that is not Claude's, reached through a translating gateway."""
    return connection.get("kind") in TRANSLATED_KINDS and not is_claude_model(model or "")


def model_cost(connection: dict, model: str) -> str:
    """Whether a request on this model costs money, as far as the studio can tell:
    `plan` (the Claude plan's allowance), `free`, `paid`, or `unknown` (a gateway the person
    runs prices its own models). The scripted agent is `free`."""
    kind = connection.get("kind")
    if kind == "fake":
        return "free"
    if kind == "claude-subscription":
        return "plan"
    low = str(model).lower()
    if low.endswith(":free") or low.endswith("/free") or model in (connection.get("free_models") or []):
        return "free"
    if kind == "anthropic-gateway" or (kind == "litellm" and connection.get("base_url")
                                       and connection.get("provider") == "openai"):
        return "paid" if low.startswith("openrouter/") else "unknown"
    return "paid"


PYTHON_VARS = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "CONDA_DEFAULT_ENV")


def outside_python(parent) -> dict:
    """What keeps the studio's own Python out of a child's shell (the agent's, a project's app): the
    variables that point at an environment are blanked, and the folder of the studio's interpreter and
    of an activated environment comes off PATH. Otherwise `pip install` there would install into the
    studio itself. → the variables to set; PATH only when it changes."""
    own = {str(Path(sys.prefix) / "bin")}
    if parent.get("VIRTUAL_ENV"):
        own.add(str(Path(parent["VIRTUAL_ENV"]) / "bin"))
    parts = [part for part in (parent.get("PATH") or "").split(os.pathsep) if part]
    kept = [part for part in parts if part.rstrip("/") not in own]
    out = {name: "" for name in PYTHON_VARS if parent.get(name)}
    if kept != parts:
        out["PATH"] = os.pathsep.join(kept)
    return out


# Variables taken out of os.environ while an agent program is started (harness/claude.py: the SDK
# lays its own variables over the studio's environment and cannot remove one, so the endpoint
# variables are popped for the spawn). They are held here meanwhile so the rest of the studio,
# which keeps running during the seconds that takes, still sees them.
_HELD_FOR_SPAWN: dict[str, str] = {}


@contextlib.contextmanager
def without(names) -> Iterator[None]:
    """Take `names` out of os.environ for the block (one child process inherits nothing of them),
    while `environment()` keeps answering with them. Callers serialise (harness/claude._SPAWN_LOCK)."""
    saved = {name: os.environ.pop(name) for name in names if name in os.environ}
    _HELD_FOR_SPAWN.update(saved)
    try:
        yield
    finally:
        os.environ.update(saved)
        for name in saved:
            _HELD_FOR_SPAWN.pop(name, None)


def environment() -> dict[str, str]:
    """The studio's own environment, as it was given, for reading keys and endpoints. Use this,
    not os.environ, wherever a key or endpoint is read: os.environ lacks the endpoint variables
    while an agent program is being started."""
    return {**os.environ, **_HELD_FOR_SPAWN}


def connection_key(connection: dict, parent, store=None) -> str | None:
    """A connection's key: the environment variable it names (`api_key_env`) when that is set,
    otherwise the entry it names in the secret store (`api_key_secret`, written by setup)."""
    name = str(connection.get("api_key_env") or "")
    if name and parent.get(name):
        return parent[name]
    secret = str(connection.get("api_key_secret") or "")
    if secret and store is not None:
        from .keystore import MODEL_KEYS
        return store.get(MODEL_KEYS, secret) or None
    return None


def model_gateway(settings: Settings) -> dict | None:
    """The gateway a project's own back end may call through the studio: the first
    anthropic-gateway connection in the config file, as {base_url, key}. None when there is
    none or it has no key. The key never leaves the studio process."""
    raw = read_config(settings.config_path).get("connections") or []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("kind") == "anthropic-gateway" and item.get("base_url"):
            store = settings.secret_store() if item.get("api_key_secret") else None
            key = connection_key(item, environment(), store)
            if key:
                return {"base_url": str(item["base_url"]).rstrip("/"), "key": key}
    return None


def load_connections(settings: Settings, owner: bool = False) -> list[dict]:
    """Resolved connection configs. Read on every call so config edits need no restart.
    `owner`: the request is the owner's own, which on a shared studio is what the plan needs."""
    if settings.fake:
        return [_normalise(FAKE_CONNECTION)]
    raw = DEFAULT_CONNECTIONS
    if settings.config_path.is_file():
        try:
            raw = json.loads(settings.config_path.read_text("utf-8")).get("connections") or []
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("Could not read %s (%s); using the default connection.", settings.config_path, exc)
            raw = DEFAULT_CONNECTIONS
    connections = [c for c in (_normalise(item) for item in raw) if c and c["kind"] != "fake"]
    connections = connections or [_normalise(DEFAULT_CONNECTIONS[0])]
    if settings.shared and not (owner and settings.owners):
        # Anthropic does not allow a tool to offer plan sign-in to its users, so the plan is for
        # single-owner use only: on a shared studio, the owner's own requests and nobody else's.
        # Listed for the others, with the reason, and never run.
        why = GUEST_NO_PLAN if settings.owners else SHARED_NO_PLAN
        connections = [{**c, "unavailable": why} if c["kind"] == "claude-subscription" else c
                       for c in connections]
    return connections
