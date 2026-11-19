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
    generate_daily_credits: float = 100.0   # what the studio may spend on generated media per day (STUDIO2_GENERATE_DAILY_CREDITS)
    home: Path = field(default_factory=Path.home)   # where this machine's own skills and MCP servers are read from
    # Shared with other people (setup's "who uses it", or STUDIO2_SHARED=1): the Claude plan is
    # switched off and every device must sign in with the access key.
    shared: bool = False
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
        settings.access_key = (env.get("STUDIO2_ACCESS_KEY") or "").strip() or None
        if not settings.access_key and file.get("access_key_secret") and (settings.hosts or settings.shared):
            from .keystore import ACCESS
            settings.access_key = settings.secret_store().get(ACCESS, str(file["access_key_secret"])) or None
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


def load_connections(settings: Settings) -> list[dict]:
    """Resolved connection configs. Read on every call so config edits need no restart."""
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
    if settings.shared:
        # Anthropic does not allow a tool to offer plan sign-in to its users, so the plan is for
        # single-owner use only. Listed, with the reason, and never run.
        connections = [{**c, "unavailable": SHARED_NO_PLAN} if c["kind"] == "claude-subscription" else c
                       for c in connections]
    return connections
