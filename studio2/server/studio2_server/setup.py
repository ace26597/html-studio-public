"""Setup: which model account, where the studio runs, who uses it (CONTRACT.md, "Install and
first run").

One set of answers, three ways in: `studio setup` asking in the terminal, `studio setup` with
flags or an answers file, and the first-run screen (`POST /api/setup`). All of them end in
`plan()`, which turns answers and the present config file into the new file, the secrets to
store and a plain list of what changes, and `apply()`, which stores the secrets and then writes
the file. A key never reaches the file: the connection names where its key lives
(`api_key_secret`, or `api_key_env` as before).
"""

from __future__ import annotations

import difflib
import ipaddress
import json
import os
import re
import secrets as random_secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import gateway
from .access import MIN_KEY, on_tailnet
from .config import (DEFAULT_PORT, OTHER_MODELS_HEADLINE, OTHER_MODELS_LIMITS, SHARED_NO_PLAN, Settings, now_iso,
                     read_config)
from .keystore import ACCESS, MODEL_KEYS

VERSION = 1
ACCESS_NAME = "access-key"
MAX_PORT = 65300    # the project pages and their back ends take the 199 ports after the studio's
PORT_RULE = f"The port is a number between 1024 and {MAX_PORT}."
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,199}")

# What setup offers. `fields` are the answers each kind takes besides the models; `key` says
# whether a key is needed ("required"), may be given ("optional") or is never used ("none").
# `group` is "claude" (Claude models, however they are reached) or "other" (models that are not
# Claude's: experimental, see OTHER_MODELS_*). `provider` marks the four the studio runs its own
# LiteLLM for (kind `litellm`, gateway.py); `lists_models` says the provider can be asked for its
# model list with the key.
ACCOUNTS: dict[str, dict] = {
    "plan": {"kind": "claude-subscription", "id": "subscription", "label": "My Claude plan (this computer)",
             "summary": "The Claude sign-in already on this computer. For you alone.",
             "key": "none", "fields": [], "models": ["opus", "sonnet", "haiku"], "default_model": "opus"},
    "api-key": {"kind": "anthropic-api", "id": "api", "label": "Anthropic API key",
                "summary": "A key from console.anthropic.com. Billed per use.",
                "key": "required", "fields": [], "models": ["sonnet", "opus", "haiku"], "default_model": "sonnet"},
    "bedrock": {"kind": "bedrock", "id": "aws", "label": "Claude on Amazon Bedrock",
                "summary": "Uses the AWS sign-in the studio is started with.",
                "key": "none", "fields": ["region"], "models": ["sonnet", "haiku"], "default_model": "sonnet"},
    "vertex": {"kind": "vertex", "id": "gcp", "label": "Claude on Google Cloud Vertex AI",
               "summary": "Uses Google's application default credentials.",
               "key": "none", "fields": ["region", "project"], "models": ["sonnet", "haiku"],
               "default_model": "sonnet"},
    "foundry": {"kind": "foundry", "id": "azure", "label": "Claude on Microsoft Foundry",
                "summary": "A Foundry resource, with its key or the machine's Entra sign-in.",
                "key": "optional", "fields": ["resource", "base_url"], "models": ["sonnet", "haiku"],
                "default_model": "sonnet"},
    "openai": {"kind": "litellm", "provider": "openai", "id": "openai", "label": "OpenAI key",
               "summary": "A key from platform.openai.com, billed per use. Runs through a LiteLLM the studio "
                          "starts beside itself.",
               "key": "required", "fields": ["base_url", "small_model"], "models": [], "default_model": None,
               "lists_models": True},
    "openrouter": {"kind": "litellm", "provider": "openrouter", "id": "openrouter", "label": "OpenRouter key",
                   "summary": "A key from openrouter.ai: many providers' models behind one account, billed per "
                              "use. Runs through a LiteLLM the studio starts beside itself.",
                   "key": "required", "fields": ["small_model"], "models": [], "default_model": None,
                   "lists_models": True},
    "azure-openai": {"kind": "litellm", "provider": "azure-openai", "id": "azure-openai", "label": "Azure OpenAI",
                     "summary": "Your Azure OpenAI resource: its address, the API version and the names of your "
                                "deployments. Runs through a LiteLLM the studio starts beside itself.",
                     "key": "required", "fields": ["base_url", "api_version", "small_model"], "models": [],
                     "default_model": None, "lists_models": False},
    "gemini": {"kind": "litellm", "provider": "gemini", "id": "gemini", "label": "Gemini key",
               "summary": "A key from Google AI Studio. Runs through a LiteLLM the studio starts beside itself.",
               "key": "required", "fields": ["small_model"], "models": [], "default_model": None,
               "lists_models": True},
    "gateway": {"kind": "anthropic-gateway", "id": "gateway", "label": "A gateway I already run",
                "summary": "LiteLLM or anything else that speaks Anthropic's message format.",
                "key": "required", "fields": ["base_url", "small_model"], "models": [], "default_model": None,
                "lists_models": True},
}
OTHER_GROUP = ("openai", "openrouter", "azure-openai", "gemini", "gateway")
KIND_TO_ACCOUNT = {meta["kind"]: name for name, meta in ACCOUNTS.items() if not meta.get("provider")}
# Per-connection switches for a model reached through a translating gateway (harness/claude.py).
OPTIONS = ("disable_betas", "max_context_tokens", "max_output_tokens")
MAX_TOKENS = 20_000_000


def account_of(connection: dict) -> str | None:
    """Which of setup's accounts a configured connection is."""
    if connection.get("kind") == gateway.KIND:
        provider = str(connection.get("provider") or "")
        return provider if provider in ACCOUNTS else None
    return KIND_TO_ACCOUNT.get(str(connection.get("kind")))
NETWORK_WARNING = ("Anyone who can reach that address can run commands on this computer through the "
                   "assistant. Keep it on a private network; never a public address.")


class SetupError(Exception):
    """An answer that cannot be used; the message is a plain sentence for the person."""


@dataclass
class Plan:
    config: dict                                  # the config file as it will be written
    before: dict                                  # the config file as it is ({} when there is none)
    secrets: list[tuple[str, str, str]] = field(default_factory=list)   # (service, name, value) to store
    changes: list[str] = field(default_factory=list)                    # plain sentences
    generated_access_key: str | None = None       # made here; shown once by the terminal, never by the API
    exists: bool = False                          # there was a config file already

    @property
    def changed(self) -> bool:
        return bool(self.secrets) or _comparable(self.config) != _comparable(self.before)

    @property
    def restart_needed(self) -> bool:
        """Where the studio listens, who may use it and its access key are read when it starts.
        (Model accounts are read at every request, so they need no restart.)"""
        return self.restart_for(None)

    def restart_for(self, running: tuple | None) -> bool:
        """`running` is (port, hosts, open_tailnet, shared) of the studio that is up: a change
        that only writes down what it already does needs no restart."""
        after, before = _listening(self.config), _listening(self.before)
        moved = any(a != b and (running is None or a != r)
                    for a, b, r in zip(after, before, running or after))
        return moved or any(service == ACCESS for service, _name, _value in self.secrets)

    def diff(self, path: Path) -> str:
        """A unified diff of the config file. It holds no secret, before or after."""
        old = _dump(self.before).splitlines() if self.exists else []
        new = _dump(self.config).splitlines()
        return "\n".join(difflib.unified_diff(old, new, f"{path} (now)", f"{path} (after setup)", lineterm=""))


def _dump(config: dict) -> str:
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def _comparable(config: dict) -> dict:
    return {key: value for key, value in config.items() if key != "setup"}


def _listening(config: dict) -> tuple:
    run = config.get("run") if isinstance(config.get("run"), dict) else {}
    return (int(run.get("port") or DEFAULT_PORT), tuple(str(h) for h in run.get("hosts") or []),
            run.get("open_tailnet") is True, config.get("shared") is True)


def _text(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


# -- what is on this machine --------------------------------------------------------------------
def tailscale_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    return app if os.access(app, os.X_OK) else None


def detect_tailscale() -> dict:
    """→ {found, address}: this machine's own Tailscale address, when Tailscale is running."""
    binary = tailscale_binary()
    if not binary:
        return {"found": False, "address": None, "detail": "Tailscale is not installed on this computer."}
    try:
        done = subprocess.run([binary, "ip", "-4"], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.TimeoutExpired):
        return {"found": False, "address": None, "detail": "Tailscale did not answer."}
    address = next((line.strip() for line in done.stdout.splitlines() if on_tailnet(line.strip())), None)
    if not address:
        return {"found": False, "address": None, "detail": "Tailscale is installed but not connected."}
    return {"found": True, "address": address, "detail": f"This computer is {address} on your Tailscale network."}


def detect_claude_login(parent: dict | None = None) -> dict:
    """→ {found, plan, detail}: whether the agent program is signed in to a Claude plan here.

    Asks the agent program itself (`claude auth status`), with every key, gateway and cloud
    setting removed the same way the subscription connection removes them, so the answer is
    about the plan and not about a key in the studio's shell. Makes no model call."""
    from .harness.claude import ENDPOINT_VARS, find_cli
    cli = find_cli()
    if not cli:
        return {"found": False, "plan": None, "detail": "The Claude Code program was not found."}
    env = {name: value for name, value in (parent if parent is not None else os.environ).items()
           if name not in ENDPOINT_VARS - {"CLAUDE_CODE_OAUTH_TOKEN"}}
    try:
        done = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=20, env=env,
                              stdin=subprocess.DEVNULL)
        status = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"found": False, "plan": None, "detail": "The Claude Code program did not say whether it is signed in."}
    if not isinstance(status, dict) or not status.get("loggedIn"):
        return {"found": False, "plan": None,
                "detail": "No Claude sign-in on this computer. Sign in with `studio login`, or use a key."}
    plan = _text(status.get("subscriptionType"), 40) or None
    if status.get("authMethod") not in ("claude.ai", "oauth_token") and not plan:
        return {"found": False, "plan": None,
                "detail": "The Claude Code program here is signed in with a key, not a Claude plan."}
    return {"found": True, "plan": plan,
            "detail": f"Signed in on this computer{f' ({plan.capitalize()} plan)' if plan else ''}."}


# -- answers → plan -----------------------------------------------------------------------------
def _models(raw, preset: dict) -> tuple[list[str], str | None]:
    models = raw.get("models")
    if isinstance(models, str):
        models = [part for part in re.split(r"[,\s]+", models) if part]
    models = [str(m).strip() for m in (models or []) if str(m).strip()]
    for model in models:
        if not MODEL.fullmatch(model):
            raise SetupError(f"“{model}” is not a usable model name.")
    seen: list[str] = []
    for model in models:
        if model not in seen:
            seen.append(model)
    default = _text(raw.get("default_model"), 200) or None
    if default and seen and default not in seen:
        raise SetupError(f"The default model “{default}” is not among the models chosen.")
    return seen, default


def _account(raw: dict, existing: list[dict], shared: bool, plan: Plan) -> list[dict]:
    choice = _text(raw.get("account") or KIND_TO_ACCOUNT.get(_text(raw.get("kind"))), 40).lower()
    preset = ACCOUNTS.get(choice)
    if not preset:
        raise SetupError("Choose a model account: " + ", ".join(ACCOUNTS) + ".")
    if preset["kind"] == "claude-subscription" and shared:
        raise SetupError(SHARED_NO_PLAN)
    cid = _text(raw.get("id"), 40) or preset["id"]
    if not NAME.fullmatch(cid):
        raise SetupError("A connection's id is lower-case letters, digits, “-” and “_”.")
    old = next((dict(c) for c in existing if c.get("id") == cid), None)
    if old and old.get("kind") != preset["kind"]:
        old = None      # same id, another kind: start again rather than mix their fields
    if old and old.get("provider") != preset.get("provider"):
        old = None
    connection: dict = old or {}
    connection.update({"id": cid, "kind": preset["kind"]})
    if preset.get("provider"):
        connection["provider"] = preset["provider"]
    connection["label"] = _text(raw.get("label"), 80) or (old or {}).get("label") or preset["label"]

    for name in preset["fields"]:
        if name in raw and raw[name] is not None:
            value = _text(raw[name], 500)
            if value:
                connection[name] = value
            else:
                connection.pop(name, None)
    if preset["kind"] == "bedrock" and not connection.get("region"):
        raise SetupError("Bedrock needs the AWS region, for example us-east-1.")
    if preset["kind"] == "vertex" and not (connection.get("region") and connection.get("project")):
        raise SetupError("Vertex AI needs the region and the Google Cloud project id.")
    if preset["kind"] == "foundry" and not (connection.get("resource") or connection.get("base_url")):
        raise SetupError("Foundry needs the resource name or its address.")
    if preset["kind"] == "anthropic-gateway":
        address = connection.get("base_url") or ""
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", address):
            raise SetupError("A gateway needs its address, for example http://127.0.0.1:4000.")
        connection["base_url"] = address.rstrip("/")
    if preset["kind"] == gateway.KIND:
        address = connection.get("base_url") or ""
        if address and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", address):
            raise SetupError("The address must start with http:// or https://.")
        if address:
            connection["base_url"] = address.rstrip("/")
        if preset["provider"] == "azure-openai":
            if not address:
                raise SetupError("Azure OpenAI needs the address of your resource, for example "
                                 "https://my-resource.openai.azure.com.")
            if not connection.get("api_version"):
                raise SetupError("Azure OpenAI needs the API version, for example 2024-10-21.")
    if preset["kind"] in ("anthropic-gateway", gateway.KIND):
        if "disable_betas" in raw:
            if raw["disable_betas"] in (True, "true", "1", 1):
                connection["disable_betas"] = True
            else:
                connection.pop("disable_betas", None)
        for name in ("max_context_tokens", "max_output_tokens"):
            if name not in raw:
                continue
            if raw[name] in (None, "", 0, "0"):
                connection.pop(name, None)
                continue
            try:
                value = int(raw[name])
            except (TypeError, ValueError):
                value = 0
            if not 1_000 <= value <= MAX_TOKENS:
                raise SetupError(f"{name.replace('_', ' ').capitalize()} is a whole number of tokens, "
                                 "1000 or more (leave it empty for the agent program's own).")
            connection[name] = value
    if isinstance(raw.get("model_ids"), dict):
        connection["model_ids"] = {str(k): str(v) for k, v in raw["model_ids"].items() if str(v).strip()}

    models, default = _models(raw, preset)
    if models:
        connection["models"] = models
    elif not connection.get("models"):
        if not preset["models"]:
            raise SetupError(
                "Name at least one deployment, exactly as Azure calls it." if choice == "azure-openai" else
                "Name at least one model, exactly as the provider calls it." if preset.get("provider") else
                "Name at least one model the gateway routes, exactly as the gateway calls it.")
        connection["models"] = list(preset["models"])
    names = [m["id"] if isinstance(m, dict) else str(m) for m in connection["models"]]
    if preset["kind"] in (gateway.KIND, "anthropic-gateway"):
        small = str(connection.get("small_model") or "")
        if small and not MODEL.fullmatch(small):
            raise SetupError(f"“{small}” is not a usable model name.")
        if not small:
            # Titles, summaries, picking skills and other background work go to a cheap model: the
            # one named here, else the cheapest-looking one offered. With neither, the request's own
            # model does the background work and skills are picked by word search.
            guess = gateway.cheapest(names)
            if guess:
                connection["small_model"] = guess
    if default:
        if default not in names:
            raise SetupError(f"The default model “{default}” is not among the models chosen.")
        connection["default_model"] = default
    elif connection.get("default_model") not in names:
        preferred = preset["default_model"]
        connection["default_model"] = preferred if preferred in names else names[0]

    key = raw.get("api_key")
    key = key.strip() if isinstance(key, str) else ""
    key_env = _text(raw.get("api_key_env"), 100)
    if key_env and not ENV_NAME.fullmatch(key_env):
        raise SetupError("That is not a usable environment variable name.")
    if preset["key"] == "none":
        if key or key_env:
            raise SetupError(f"{preset['label']} does not take a key here.")
        connection.pop("api_key_env", None)
        connection.pop("api_key_secret", None)
    elif key:
        if len(key) < 8 or any(c.isspace() for c in key):
            raise SetupError("That does not look like a key.")
        connection["api_key_secret"] = cid
        connection.pop("api_key_env", None)
        plan.secrets.append((MODEL_KEYS, cid, key))
        plan.changes.append(f"The key for “{connection['label']}” is saved in {{secrets}}, not in the config file.")
    elif key_env:
        connection["api_key_env"] = key_env
        connection.pop("api_key_secret", None)
    elif preset["key"] == "required" and not (connection.get("api_key_env") or connection.get("api_key_secret")):
        raise SetupError(f"{preset['label']} needs its key.")

    others = [c for c in existing if c.get("id") != cid]
    listed = [connection, *others] if raw.get("make_default", True) else (
        [connection if c.get("id") == cid else c for c in existing] if old else [*existing, connection])
    verb = "Change" if old else "Add"
    first = " and use it by default" if listed[0] is connection and (others or not old) else ""
    plan.changes.append(f"{verb} the model account “{connection['label']}” "
                        f"(models: {', '.join(names)}; default {connection['default_model']}){first}.")
    if preset["kind"] in (gateway.KIND, "anthropic-gateway"):
        background = connection.get("small_model")
        plan.changes.append(f"Background work (titles, summaries, picking skills) uses {background}." if background else
                            "Background work uses the same model as each request (no cheaper one was named); "
                            "skills are then picked by word search.")
        plan.changes.append(OTHER_MODELS_HEADLINE)
        found = gateway.installed()
        if not found["installed"]:
            plan.changes.append(f"This account needs LiteLLM, which is not installed yet: `{gateway.INSTALL_COMMAND}` "
                                f"installs version {gateway.VERSION} ({gateway.SIZE_NOTE}). Until then the account "
                                "is listed as not available.")
    return listed


def _host(raw, tailscale) -> str:
    value = _text(raw, 100)
    if value.lower() in ("tailscale", "tailnet"):
        found = tailscale()
        if not found.get("address"):
            raise SetupError(f"{found.get('detail') or 'No Tailscale address was found.'} "
                             "Give the private address to listen on instead.")
        return found["address"]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise SetupError("Give the address as numbers, for example 100.101.102.103 (a Tailscale address).") from None
    if address.is_unspecified:
        raise SetupError("Name one private address; the studio does not listen on every address at once.")
    if address.is_loopback:
        raise SetupError("This computer's own address is always on. Name the private network address to add.")
    if address.is_global and not on_tailnet(str(address)):
        raise SetupError("That is a public address. The studio can run commands on this computer, so it stays "
                         "on a private network.")
    if address.version != 4:
        raise SetupError("Give an IPv4 address; the studio listens on IPv4.")
    return str(address)


def plan(answers: dict, settings: Settings, *, tailscale=detect_tailscale, store=None) -> Plan:
    """Work out the new config file. Raises SetupError for an answer that cannot be used."""
    if not isinstance(answers, dict):
        raise SetupError("The answers must be a JSON object.")
    unknown = set(answers) - {"account", "where", "who", "remove", "skip"}
    if unknown:
        raise SetupError(f"Unknown part of the answers: {', '.join(sorted(unknown))}.")
    exists = settings.config_path.is_file()
    before = read_config(settings.config_path)
    if exists and not before and settings.config_path.read_text("utf-8", "replace").strip():
        raise SetupError(f"{settings.config_path} is not a JSON object. Fix or move it, then run setup again.")
    config = json.loads(json.dumps(before))
    result = Plan(config=config, before=before, exists=exists)
    store = store if store is not None else settings.secret_store()

    connections = [c for c in config.get("connections") or [] if isinstance(c, dict)] \
        if isinstance(config.get("connections"), list) else []
    run = dict(config["run"]) if isinstance(config.get("run"), dict) else {}
    who = answers.get("who") if isinstance(answers.get("who"), dict) else {}
    where = answers.get("where") if isinstance(answers.get("where"), dict) else {}
    shared = bool(who["shared"]) if "shared" in who else config.get("shared") is True

    for cid in answers.get("remove") or []:
        if any(c.get("id") == cid for c in connections):
            connections = [c for c in connections if c.get("id") != cid]
            result.changes.append(f"Remove the model account “{cid}” (its saved key, if any, stays where it is).")
    if isinstance(answers.get("account"), dict):
        connections = _account(answers["account"], connections, shared, result)
    if connections or "connections" in config:
        config["connections"] = connections

    if "port" in where and where["port"] is not None:
        try:
            port = int(where["port"])
        except (TypeError, ValueError):
            raise SetupError(PORT_RULE) from None
        if not 1024 <= port <= MAX_PORT:
            raise SetupError(PORT_RULE)
        if port != int(run.get("port") or DEFAULT_PORT):
            result.changes.append(f"Port: {run.get('port') or DEFAULT_PORT} → {port} "
                                  f"(project pages on {port + 1}–{port + 99}).")
        run["port"] = port
    hosts_before = [str(h) for h in run.get("hosts") or []]
    if "network" in where or "host" in where:
        if where.get("network", True) and where.get("host"):
            hosts = [_host(where["host"], tailscale)]
        elif where.get("network"):
            hosts = [_host("tailscale", tailscale)]
        else:
            hosts = []
        if hosts != hosts_before:
            result.changes.append(
                f"Also listen on {hosts[0]}. {NETWORK_WARNING}" if hosts
                else "Listen on this computer only.")
        run["hosts"] = hosts
    hosts = [str(h) for h in run.get("hosts") or []]
    open_before = run.get("open_tailnet") is True
    if "open_tailnet" in where:
        run["open_tailnet"] = bool(where["open_tailnet"])
    if run.get("open_tailnet"):
        if shared and where.get("open_tailnet"):
            raise SetupError("A shared studio asks every device for the access key, so “no key on my tailnet” "
                             "cannot be used with it.")
        if shared or not hosts:
            run["open_tailnet"] = False
        elif not all(on_tailnet(h) for h in hosts):
            raise SetupError("“No key on my tailnet” only works when the address is a Tailscale address "
                             "(100.64.0.0/10).")
    if bool(run.get("open_tailnet")) != open_before:
        result.changes.append("Devices on your Tailscale network get in without the access key." if run["open_tailnet"]
                              else "Every device other than this computer must sign in with the access key.")

    if shared != (config.get("shared") is True):
        result.changes.append(
            "Shared with other people: the Claude plan is switched off and every device signs in with the access key."
            if shared else "Used by you alone: the Claude plan on this computer may be used.")
    if "shared" in who or "shared" in config:
        config["shared"] = shared
    if shared and not any(c.get("kind") != "claude-subscription" for c in (connections or [])):
        raise SetupError("A shared studio cannot use the Claude plan (Anthropic does not allow a tool to offer "
                         "plan sign-in to its users). Choose an API key or a cloud account as the model account.")

    typed = who.get("access_key") if isinstance(who.get("access_key"), str) else where.get("access_key")
    typed = typed.strip() if isinstance(typed, str) else ""
    needs_key = shared or (bool(hosts) and not run.get("open_tailnet"))
    if typed:
        if len(typed) < MIN_KEY or any(c.isspace() for c in typed):
            raise SetupError(f"The access key must be at least {MIN_KEY} characters, with no spaces.")
        config["access_key_secret"] = ACCESS_NAME
        result.secrets.append((ACCESS, ACCESS_NAME, typed))
        result.changes.append("The access key is saved in {secrets}, not in the config file.")
    elif needs_key:
        name = str(config.get("access_key_secret") or "")
        if not (name and store.get(ACCESS, name)) and not (os.environ.get("STUDIO2_ACCESS_KEY") or "").strip():
            made = random_secrets.token_urlsafe(24)
            config["access_key_secret"] = ACCESS_NAME
            result.secrets.append((ACCESS, ACCESS_NAME, made))
            result.generated_access_key = made
            result.changes.append("A new access key is made and saved in {secrets}. "
                                  "`studio access-key` shows it.")

    if run or "run" in config:
        config["run"] = run
    label = getattr(store, "label", "the secret store")
    result.changes = [change.replace("{secrets}", label) for change in result.changes]
    stamp = dict(config["setup"]) if isinstance(config.get("setup"), dict) else {}
    stamp.update({"version": VERSION, "skipped_at" if answers.get("skip") else "completed_at": now_iso()})
    if not answers.get("skip"):
        stamp.pop("skipped_at", None)
    config["setup"] = stamp
    return result


def apply(result: Plan, settings: Settings, store=None) -> None:
    """Store the secrets, then write the config file (the previous one is kept beside it as
    `config.json.bak`). Also switches the running studio's shared flag, so the Claude plan goes
    off at once when a studio becomes shared."""
    store = store if store is not None else settings.secret_store()
    for service, name, value in result.secrets:
        store.set(service, name, value)
    path = settings.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.exists:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_dump(result.config), "utf-8")
    os.replace(temporary, path)
    if os.environ.get("STUDIO2_SHARED") is None:
        settings.shared = result.config.get("shared") is True


# -- what the first-run screen and `studio setup` show -------------------------------------------
def configured(settings: Settings) -> dict:
    """The present choices, without any secret: what `GET /api/setup` calls `current`."""
    file = read_config(settings.config_path)
    run = file.get("run") if isinstance(file.get("run"), dict) else {}
    store = settings.secret_store()
    listed = []
    for item in file.get("connections") or [] if isinstance(file.get("connections"), list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        key_from = None
        if item.get("api_key_env"):
            key_from = "environment"
        elif item.get("api_key_secret"):
            key_from = store.backend if store.get(MODEL_KEYS, str(item["api_key_secret"])) else "missing"
        listed.append({
            "id": str(item["id"]), "kind": item.get("kind"), "label": str(item.get("label") or item["id"]),
            "account": account_of(item),
            "models": [m["id"] if isinstance(m, dict) else str(m) for m in item.get("models") or []],
            "default_model": item.get("default_model"),
            "key_from": key_from, "api_key_env": item.get("api_key_env"),
            **{name: item[name] for name in ("base_url", "region", "project", "resource", "small_model",
                                             "provider", "api_version", *OPTIONS) if item.get(name)},
        })
    name = str(file.get("access_key_secret") or "")
    return {
        "connections": listed,
        "port": int(run.get("port") or settings.port or DEFAULT_PORT),
        "hosts": [str(h) for h in run.get("hosts") or []],
        "open_tailnet": run.get("open_tailnet") is True,
        "shared": file.get("shared") is True,
        "access_key_set": bool(name and store.get(ACCESS, name)),
        "done": isinstance(file.get("setup"), dict),
    }


def needed(settings: Settings, has_projects: bool) -> bool:
    """Whether the first-run screen should open by itself: nothing has been configured, no page
    exists yet, and the studio was not told where to listen by its environment. Never in the
    fake studio (which is for tests and interface work) and never for a studio already in use."""
    if settings.fake or has_projects or settings.config_path.is_file():
        return False
    return not (os.environ.get("STUDIO2_HOSTS") or "").strip()


def accounts(settings: Settings) -> list[dict]:
    listed = []
    for name, meta in ACCOUNTS.items():
        blocked = SHARED_NO_PLAN if meta["kind"] == "claude-subscription" and settings.shared else None
        listed.append({"account": name, "kind": meta["kind"], "id": meta["id"], "label": meta["label"],
                       "summary": meta["summary"], "key": meta["key"], "fields": meta["fields"],
                       "models": meta["models"], "default_model": meta["default_model"],
                       "available": blocked is None, "note": blocked,
                       "group": "other" if name in OTHER_GROUP else "claude",
                       "provider": meta.get("provider"), "lists_models": bool(meta.get("lists_models")),
                       "needs_litellm": meta["kind"] == gateway.KIND})
    return listed


def other_models() -> dict:
    """What is said wherever a model that is not Claude's is chosen."""
    return {"headline": OTHER_MODELS_HEADLINE, "limits": list(OTHER_MODELS_LIMITS)}
