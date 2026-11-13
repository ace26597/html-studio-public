"""The real harness: Claude Code as a library (claude-agent-sdk).

One class serves every connection kind; they differ only in the environment the agent program
is started with (child_env). `claude-subscription` uses the Claude login on this machine,
`anthropic-api` an API key, `anthropic-gateway` LiteLLM or any Anthropic-compatible gateway,
`litellm` the LiteLLM the studio runs itself for another provider's key (gateway.py), and
`bedrock`, `vertex` and `foundry` Claude on those clouds.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, PermissionResultAllow,
    PermissionResultDeny, RateLimitEvent, ResultMessage, StreamEvent, SystemMessage, TextBlock,
    ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)
from claude_agent_sdk.types import (TERMINAL_TASK_STATUSES, TaskNotificationMessage, TaskStartedMessage,
                                    TaskUpdatedMessage)
from claude_agent_sdk.types import ServerToolUseBlock

from .. import costs
from .. import gateway as managed
from ..config import STUDIO2_ROOT, TRANSLATED_KINDS, connection_key, outside_python
from ..risk import risky_command
from .. import prices
from .base import Emit, TurnRequest, TurnResult, empty_usage, public_connection

LIBRARY_DIR = STUDIO2_ROOT / "library"
SCRUBBED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
WRITE_TOOLS = "Write|Edit|MultiEdit|NotebookEdit"
KINDS = {"Read": "read", "NotebookRead": "read", "Glob": "search", "Grep": "search", "WebSearch": "search",
         "web_search": "search", "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
         "Bash": "execute", "BashOutput": "execute", "KillShell": "execute", "WebFetch": "fetch",
         "web_fetch": "fetch", "Task": "agent", "Agent": "agent"}
# The SDK merges options.env OVER the parent environment (subprocess_cli.py connect():
# {**os.environ, ..., **options.env}); it cannot remove an inherited variable. So variables
# that must not reach the child are popped from os.environ just while the process is spawned.
_SPAWN_LOCK = asyncio.Lock()


class HarnessError(Exception):
    pass


SECRET_NAME = re.compile(r"key|token|secret|passw|credential", re.I)


def hidden_secrets(parent: Mapping[str, str]) -> dict[str, str]:
    """Inherited variables the agent's shell must not be able to read, blanked for the child.

    The studio is started from the person's shell, which holds its own access key, gateway keys
    and whatever else that shell exports. None of it is the agent's to see: anything whose name
    looks like a secret is set to an empty string (the SDK can add to the child's environment but
    not remove from it). ANTHROPIC_* and CLAUDE_* are the harness's own business (child_env), and
    STUDIO2_AGENT_ENV names variables the person wants passed through.
    """
    allowed = {name.strip() for name in (parent.get("STUDIO2_AGENT_ENV") or "").split(",") if name.strip()}
    return {name: "" for name, value in parent.items()
            if value and SECRET_NAME.search(name) and name not in allowed
            and not name.startswith(("ANTHROPIC_", "CLAUDE_")) and not name.endswith("_SOCK")}


# Every way of naming a model endpoint to the agent program. A connection sets its own and the
# rest are removed from what the child inherits, so one kind can never fall through to another.
ENDPOINT_VARS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}
# Credentials a cloud provider's own sign-in reads from the environment. The agent program needs
# them, so they are passed through for that kind only (hidden_secrets blanks them otherwise).
CLOUD_CREDENTIALS = {
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                "AWS_BEARER_TOKEN_BEDROCK", "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE"),
    "vertex": ("GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_CONFIG"),
    "foundry": ("ANTHROPIC_FOUNDRY_AUTH_TOKEN", "AZURE_CONFIG_DIR"),
}
MODEL_PINS = {"opus": "ANTHROPIC_DEFAULT_OPUS_MODEL", "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
              "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL", "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL"}


def _need(connection: dict, *fields: str) -> list[str]:
    values = [str(connection.get(field) or "").strip() for field in fields]
    missing = [field for field, value in zip(fields, values) if not value]
    if missing:
        raise HarnessError(f"Connection “{connection['id']}” needs {' and '.join(missing)}.")
    return values


def _key(connection: dict, parent: Mapping[str, str], secrets=None) -> str:
    """The connection's key: from the environment variable it names, or from where setup put it
    (the system keychain, or the owner-only file where there is no keychain)."""
    key = connection_key(connection, parent, secrets)
    if key:
        return key
    if connection.get("api_key_secret") and not connection.get("api_key_env"):
        raise HarnessError(f"Connection “{connection['id']}” has no key saved on this machine. "
                           "Run `studio setup` (or open Setup in the studio) and enter it again.")
    (key_env,) = _need(connection, "api_key_env")
    raise HarnessError(f"Connection “{connection['id']}” reads its key from the environment variable "
                       f"{key_env}, which is not set for the studio server.")


def _whole(connection: dict, name: str) -> str | None:
    """An optional whole number of a connection (a token limit), as the text an environment wants."""
    try:
        value = int(connection.get(name) or 0)
    except (TypeError, ValueError):
        return None
    return str(value) if value > 0 else None


def translated_options(connection: dict) -> dict[str, str]:
    """Per-connection switches for a model reached through a translating gateway. All off unless set."""
    env: dict[str, str] = {}
    if connection.get("disable_betas") is True:
        # An escape hatch for a provider that rejects Anthropic's beta request fields. It also turns
        # off the agent program's tool search, so it is never on by default.
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    for name, variable in (("max_context_tokens", "CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
                           ("max_output_tokens", "CLAUDE_CODE_MAX_OUTPUT_TOKENS")):
        value = _whole(connection, name)
        if value:
            env[variable] = value
    return env


def child_env(connection: dict, parent: Mapping[str, str], model: str | None = None,
              secrets=None, endpoint: "managed.Endpoint | None" = None) -> tuple[dict, set]:
    """→ (variables to set for the child, inherited variables to remove before spawning).

    claude-subscription  the machine's own Claude login; nothing is set.
    anthropic-api        an Anthropic API key (api_key_env), sent as X-Api-Key.
    anthropic-gateway    an Anthropic-compatible gateway (base_url + api_key_env), sent as a bearer token.
    litellm              the LiteLLM the studio runs (`endpoint`: where it is and its own key); every
                         model slot of the agent program is mapped to a model the connection routes.
    bedrock              Claude on Amazon Bedrock (region; AWS's own credential chain).
    vertex               Claude on Google Cloud (region + project; application default credentials).
    foundry              Claude on Microsoft Foundry (resource or base_url; api_key_env or Entra sign-in).
    """
    kind = connection["kind"]
    if kind == "claude-subscription":
        # The machine's Claude login is used: never a key, a gateway or a cloud provider that the
        # studio's own shell happens to name. (The headless login token is that login; it stays.)
        return {}, ENDPOINT_VARS - {"CLAUDE_CODE_OAUTH_TOKEN"}
    env: dict[str, str] = {}
    if kind == "anthropic-api":
        env["ANTHROPIC_API_KEY"] = _key(connection, parent, secrets)
    elif kind == "anthropic-gateway":
        (base_url,) = _need(connection, "base_url")
        env.update({"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": _key(connection, parent, secrets),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        small = connection.get("small_model") or model
        if small:  # background helper calls must also name a model the gateway routes
            env["ANTHROPIC_SMALL_FAST_MODEL"] = env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = small
        env.update(translated_options(connection))
    elif kind == "litellm":
        if endpoint is None:
            raise HarnessError(f"Connection “{connection['id']}” runs through the studio's own LiteLLM, "
                               "which has not been started for this request.")
        cid = str(connection["id"])
        env.update({"ANTHROPIC_BASE_URL": endpoint.base_url, "ANTHROPIC_AUTH_TOKEN": endpoint.key,
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        # Every slot the agent program fills by itself must name a model this proxy routes: left
        # alone, a helper that asks for "haiku" or "sonnet" would ask the provider for a Claude
        # model it does not have. The main slots get the request's model, the cheap slot (titles,
        # summaries, other background work) the connection's small model.
        main = managed.routed(cid, model) if model else None
        small = managed.small_model(connection, model)
        small = managed.routed(cid, small) if small else main
        if main:
            for variable in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                             "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
                env[variable] = main
        if small:
            # ANTHROPIC_SMALL_FAST_MODEL is the older name of the same slot; current programs still read it.
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_SMALL_FAST_MODEL"] = small
        env.update(translated_options(connection))
    elif kind == "bedrock":
        (region,) = _need(connection, "region")
        env.update({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": region})
    elif kind == "vertex":
        region, project = _need(connection, "region", "project")
        env.update({"CLAUDE_CODE_USE_VERTEX": "1", "CLOUD_ML_REGION": region, "ANTHROPIC_VERTEX_PROJECT_ID": project})
    elif kind == "foundry":
        env["CLAUDE_CODE_USE_FOUNDRY"] = "1"
        if connection.get("base_url"):
            env["ANTHROPIC_FOUNDRY_BASE_URL"] = str(connection["base_url"])
        else:
            (env["ANTHROPIC_FOUNDRY_RESOURCE"],) = _need(connection, "resource")
        if connection.get("api_key_env") or connection.get("api_key_secret"):
            env["ANTHROPIC_FOUNDRY_API_KEY"] = _key(connection, parent, secrets)   # else Entra sign-in (az login)
    else:
        raise HarnessError(f"Connection “{connection['id']}” has a kind this harness does not run: {kind}.")
    for name in CLOUD_CREDENTIALS.get(kind, ()):
        if parent.get(name):
            env[name] = parent[name]
    # `model_ids` pins the short names to this provider's own ids, e.g. {"sonnet": "us.anthropic.…"}.
    for alias, model_id in (connection.get("model_ids") or {}).items():
        if alias in MODEL_PINS and model_id:
            env[MODEL_PINS[alias]] = str(model_id)
    # No other endpoint may leak in from the studio's own environment (a first-party key or the
    # subscription token must never reach a gateway, and a stray key must not outrank this one).
    return env, ENDPOINT_VARS - set(env)


def find_cli() -> str | None:
    override = os.environ.get("STUDIO2_CLAUDE_CLI")
    if override:
        return override if Path(override).is_file() else None
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(bundled) if bundled.is_file() else shutil.which("claude")


def outside_workspace(workspace: Path, tool_input: dict) -> str | None:
    """The resolved target of a file-writing tool call when it is outside the project, else None."""
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    root = workspace.resolve()
    target = (root / str(raw)).resolve()  # an absolute `raw` replaces root
    return None if target == root or root in target.parents else str(target)


def build_options(request: TurnRequest, env: dict, resume: str | None, stderr=None) -> ClaudeAgentOptions:
    workspace = request.workspace

    async def pre_tool_use(hook_input, _tool_use_id, _context):
        target = outside_workspace(workspace, hook_input.get("tool_input") or {})
        if target:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"{target} is outside the project folder. Stay inside the project."}}
        return {}

    async def can_use_tool(tool_name, tool_input, _context):
        if tool_name in WRITE_TOOLS.split("|") and outside_workspace(workspace, tool_input):
            return PermissionResultDeny(message="Files outside the project folder cannot be changed.")
        if tool_name == "Bash":
            command = str(tool_input.get("command") or "")
            reason = risky_command(command, workspace)
            if reason and not await request.approve("Bash", "Run a shell command", command, reason):
                return PermissionResultDeny(message=(
                    "The user did not allow this command. Do not try to reach the same result another "
                    "way; carry on without it, or explain what you needed it for."))
        server = tool_name.split("__")[1] if tool_name.startswith("mcp__") and tool_name.count("__") >= 2 else None
        if server in request.ask_first and server not in allowed_servers:
            if not await request.approve(tool_name, f"Use the connector {server}",
                                         json.dumps(tool_input, ensure_ascii=False, default=str)[:2000],
                                         "The studio picked this connector for the request; you have not switched "
                                         "it on yourself."):
                return PermissionResultDeny(message=(
                    f"The user did not allow the connector {server} in this request. Carry on without it."))
            allowed_servers.add(server)
        return PermissionResultAllow()

    allowed_servers: set[str] = set()
    plugin = request.plugin_dir or LIBRARY_DIR
    prompt_file = LIBRARY_DIR / "system-prompt.md"
    append = prompt_file.read_text("utf-8") if prompt_file.is_file() else ""
    if request.context:
        append = f"{append}\n\n{request.context}".strip()
    return ClaudeAgentOptions(
        cwd=str(workspace),
        resume=resume,
        model=request.model,
        include_partial_messages=True,
        # One message can carry a screenshot or a large tool result; the SDK's 1 MB default made
        # such a turn fail with "JSON message exceeded maximum buffer size".
        max_buffer_size=64 * 1024 * 1024,
        # Permissions (Step 1: no prompts in the UI yet). `acceptEdits` auto-approves file edits
        # inside cwd only; everything that would otherwise prompt (shell, web, edits elsewhere)
        # is routed to `can_use_tool`, which approves all but out-of-project file writes. This is
        # narrower than `bypassPermissions` (which would also silence the callback), and the
        # PreToolUse hook is the hard gate: hooks run for every call, whatever the mode.
        # The shell itself is not confined: same trust as running Claude Code locally.
        permission_mode="acceptEdits",
        can_use_tool=can_use_tool,
        hooks={"PreToolUse": [HookMatcher(matcher=WRITE_TOOLS, hooks=[pre_tool_use], on_failure="block")]},
        # [] = load no user/project/local settings: the person's own ~/.claude settings, hooks,
        # CLAUDE.md and permissions are not inherited. strict_mcp_config drops their MCP servers.
        setting_sources=[],
        strict_mcp_config=True,
        plugins=[{"type": "local", "path": str(plugin)}] if (plugin / ".claude-plugin").is_dir() else [],
        mcp_servers=request.mcp_servers or {},
        system_prompt={"type": "preset", "preset": "claude_code", "append": append},
        # Thinking text is omitted by default on current Claude models; ask for the summary so
        # the activity feed can show it. Gateways opt in with "thinking": true (not every
        # routed model accepts the parameter).
        thinking={"type": "adaptive", "display": "summarized"} if (
            request.connection["kind"] not in TRANSLATED_KINDS or request.connection.get("thinking")) else None,
        env=env,
        stderr=stderr,
        cli_path=os.environ.get("STUDIO2_CLAUDE_CLI") or None,
    )


FINAL_TURN_GRACE = 120.0
EARLY_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
EARLY_PATH = re.compile(r'"(?:file_path|notebook_path)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _rel(workspace: Path, raw: Any) -> str:
    try:
        return Path(str(raw)).resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(raw)


def describe_tool(name: str, tool_input: dict, workspace: Path) -> tuple[str, str]:
    """→ (kind, short human title) for a tool call."""
    kind = KINDS.get(name, "other")
    get = lambda key: " ".join(str(tool_input.get(key) or "").split())  # noqa: E731
    path = _rel(workspace, tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if name in ("Read", "NotebookRead", "Edit", "MultiEdit", "NotebookEdit"):
        title = f"{'Read' if kind == 'read' else 'Edit'} {path}"
    elif name == "Write":
        title = f"Write {path}"
    elif name == "Bash":
        command = get("command")
        here = re.escape(str(workspace)) + "|" + re.escape(str(workspace.resolve()))
        command = re.sub(rf"^cd\s+[\"']?(?:{here})/?[\"']?\s*(?:&&|;)\s*", "", command)
        title = f"Run {command}"
    elif name in ("Glob", "Grep"):
        title = f"{'Find files' if name == 'Glob' else 'Search for'} {get('pattern')}"
    elif kind == "search":
        title = f"Search the web for {get('query')}"
    elif kind == "fetch":
        title = f"Fetch {get('url')}"
    elif kind == "agent":
        title = get("description") or f"Sub-agent {get('subagent_type')}".strip()
    elif name == "Skill":
        title = f"Use skill {get('skill') or get('command')}"
    elif name.startswith("mcp__studio__"):
        try:
            kind, title = _studio_tool(name.split("__", 2)[2], get)
        except Exception:       # a title is decoration: it must never end the turn
            kind, title = "other", name.split("__", 2)[2].replace("_", " ")
    elif name.startswith("mcp__"):
        title = " · ".join(name.split("__")[1:])
    else:
        title = name
    return kind, title[:117] + "…" if len(title) > 118 else title


def _studio_tool(tool: str, get) -> tuple[str, str]:
    """(kind, title) for one of the studio's own tools (tools.py)."""
    if tool == "search_web":
        return "search", f"Search the web for {get('query')}"
    if tool == "fetch_source":
        return "fetch", f"Read and save {get('url')}"
    if tool == "read_source":
        return "read", f"Read source {get('id')}" + (f" for “{get('find')}”" if get("find") else "")
    if tool == "add_source":
        return "read", f"Add {get('path')} as a source"
    if tool == "query_files":
        return "execute", f"Query project files: {get('sql')}"
    if tool == "database_schema":
        return "read", f"Look at the tables of {get('source')}" + (f" ({get('table')})" if get("table") else "")
    if tool == "query_database":
        return "execute", f"Query {get('source')}: {get('sql')}"
    if tool == "look_at_page":
        return "read", f"Look at the page {get('path') or '/'}"
    if tool == "save_template":
        return "other", f"Save the template “{get('name')}”"
    if tool == "render_document":
        return "read", f"Render {get('path')} for viewing"
    if tool == "render_video":
        if get("check"):
            return "read", f"Look at frames of {get('path')}"
        return "execute", f"Render {get('path')} to {(get('format') or 'mp4').upper()} video"
    if tool == "generation_models":
        return "read", f"Look up what {get('model') or 'the generation service'} can make"
    if tool == "generate_media":
        noun = {"image": "picture", "video": "clip", "audio": "sound"}.get(get("kind"), "picture")
        return "execute", f"Generate a {noun}: {get('prompt')}"
    if tool == "render_image":
        return "execute", f"Save a picture of {get('path') or 'index.html'} as {get('out')}"
    if tool == "propose_ship_recipe":
        return "other", f"Propose how to make the app live ({get('method') or 'recipe'})"
    if tool == "make_live":
        return "other", "Make the app live"
    if tool == "check_page":
        return "read", f"Check the figures on {get('path') or 'index.html'}"
    if tool == "search_library":
        return "search", f"Search the library for {get('query')}"
    if tool == "activate_skill":
        return "read", f"Switch on the skill {str(get('id')).split(':')[-1]}"
    if tool == "request_connector":
        return "other", f"Ask for the connector {str(get('id')).split(':')[-1]}"
    return "other", tool.replace("_", " ")


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) and part.get("type") == "text"
                         else f"[{part.get('type', 'content') if isinstance(part, dict) else 'content'}]"
                         for part in content)
    return "" if content is None else json.dumps(content, default=str)


class EventMapper:
    """Turns SDK messages into contract events. Pure: no I/O, so it is tested without a process."""

    def __init__(self, workspace: Path, gateway: bool = False, charged: bool = False):
        self.charged = charged     # a key or a cloud account pays for this turn; the price is a charge
        self.workspace = workspace
        self.gateway = gateway
        self.session_id: str | None = None
        self.result: ResultMessage | None = None
        self.error: str | None = None
        self.activity = 0                       # message and tool events produced so far
        self._stream_id: str | None = None      # API message id of the message being streamed
        self._streamed: set[tuple[str, str]] = set()
        self._plan_tools: set[str] = set()
        self._early: dict[int, dict] = {}       # file tools being streamed, by content block index
        self._tasks: dict[str, dict] = {}       # best-effort plan from TaskCreate / TaskUpdate
        self._agent_calls: set[str] = set()     # tool ids of sub-agent calls
        self.background: set[str] = set()       # sub-agents still working in the background

    def handle(self, message: Any) -> list[dict]:
        events: list[dict] = []
        if isinstance(message, StreamEvent):
            self._stream(message, events)
        elif isinstance(message, AssistantMessage):
            self._assistant(message, events)
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    self._tool_result(block, message, events)
        elif isinstance(message, ResultMessage):
            self.result, self.session_id = message, message.session_id or self.session_id
        elif isinstance(message, TaskStartedMessage):
            # This CLI starts sub-agents in the background and ends the assistant's turn; the
            # session then continues by itself when they report back. Only sub-agents count:
            # a background shell command may never end.
            if message.tool_use_id in self._agent_calls or "agent" in (message.task_type or "").lower():
                self.background.add(message.task_id)
        elif isinstance(message, (TaskNotificationMessage, TaskUpdatedMessage)):
            if message.status in TERMINAL_TASK_STATUSES:
                self.background.discard(message.task_id)
        elif isinstance(message, RateLimitEvent):
            if message.rate_limit_info.status == "rejected":
                events.append({"type": "notice", "level": "warning",
                               "message": "The Claude usage limit for this account has been reached."})
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            self.session_id = message.data.get("session_id") or self.session_id
            # Internal event (never logged): lets the turn runner keep the session after a Stop.
            names = lambda key: [str(x.get("name") if isinstance(x, dict) else x)  # noqa: E731
                                 for x in message.data.get(key) or []]
            events.append({"type": "session", "session_id": self.session_id,
                           "skills": names("skills"),
                           "mcp_servers": [{"name": str(s.get("name")), "status": str(s.get("status"))}
                                           for s in message.data.get("mcp_servers") or [] if isinstance(s, dict)]})
            for server in message.data.get("mcp_servers") or []:
                if isinstance(server, dict) and server.get("status") in ("failed", "needs-auth"):
                    events.append({"type": "notice", "level": "warning", "message":
                                   f"The MCP server “{server.get('name')}” could not be used in this turn "
                                   f"({str(server.get('status')).replace('-', ' ')})."})
            for problem in message.data.get("plugin_errors") or []:
                events.append({"type": "notice", "level": "warning",
                               "message": f"Studio library did not load fully: {problem.get('message')}"})
        self.activity += sum(event["type"] not in ("notice", "session") for event in events)
        return events

    def _stream(self, message: StreamEvent, events: list[dict]) -> None:
        event = message.event
        if message.parent_tool_use_id:
            return  # sub-agent chatter is not the assistant's reply; its tool calls still show
        if event.get("type") == "message_start":
            self._stream_id = (event.get("message") or {}).get("id") or message.uuid
            self._early = {}
        elif event.get("type") == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use" and block.get("name") in EARLY_TOOLS:
                self._early[event.get("index", 0)] = {"id": block.get("id"), "name": block["name"], "json": ""}
        elif event.get("type") == "content_block_delta" and (event.get("delta") or {}).get("type") == "input_json_delta":
            # A large Write can take over a minute to stream. Announce the tool as soon as its
            # path is readable; the complete call replaces this one when the message ends.
            early = self._early.get(event.get("index", 0))
            if early:
                early["json"] += event["delta"].get("partial_json") or ""
                found = EARLY_PATH.search(early["json"])
                if found and early["id"]:
                    del self._early[event.get("index", 0)]
                    try:
                        path = json.loads(f'"{found.group(1)}"')
                    except ValueError:
                        return
                    tool_input = {"file_path": path}
                    kind, title = describe_tool(early["name"], tool_input, self.workspace)
                    events.append({"type": "tool_call", "tool_id": early["id"], "name": early["name"],
                                   "kind": kind, "title": title, "input": tool_input, "parent_tool_id": None})
        elif event.get("type") == "content_block_delta" and self._stream_id:
            delta = event.get("delta") or {}
            for field, kind, out in (("text", "text", "message_chunk"), ("thinking", "thinking", "thought_chunk")):
                if delta.get("type") == f"{field}_delta" and delta.get(field):
                    self._streamed.add((self._stream_id, kind))
                    events.append({"type": out, "message_id": f"{self._stream_id}:{event.get('index', 0)}",
                                   "text": delta[field]})

    def _assistant(self, message: AssistantMessage, events: list[dict]) -> None:
        # Text arrives twice with partial messages on: as stream deltas and again in the final
        # AssistantMessage. Deltas win; the final blocks are used only when nothing was streamed.
        message_id = message.message_id or self._stream_id or message.uuid or "message"
        parent = message.parent_tool_use_id
        for index, block in enumerate(message.content):
            if isinstance(block, TextBlock):
                # A helper agent's words are for the agent that started it, never the chat: the
                # stream path drops them already, and so must this one (they arrive unstreamed).
                if block.text and not parent and (message_id, "text") not in self._streamed:
                    events.append({"type": "message_chunk", "message_id": f"{message_id}:full{index}",
                                   "text": block.text})
            elif isinstance(block, ThinkingBlock):
                if block.thinking and not parent and (message_id, "thinking") not in self._streamed:
                    events.append({"type": "thought_chunk", "message_id": f"{message_id}:full{index}",
                                   "text": block.thinking})
            elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                self._tool_call(block, parent, events)
        if message.error:
            self.error = f"The model request failed ({message.error.replace('_', ' ')})."

    def _tool_call(self, block: Any, parent: str | None, events: list[dict]) -> None:
        tool_input = block.input if isinstance(block.input, dict) else {}
        if block.name == "TodoWrite":
            self._plan_tools.add(block.id)
            events.append({"type": "plan", "entries": [
                {"content": str(todo.get("content", "")), "status": _plan_status(todo.get("status"))}
                for todo in tool_input.get("todos") or [] if isinstance(todo, dict)]})
            return
        if block.name in ("TaskCreate", "TaskUpdate") and not parent:
            self._plan_tools.add(block.id)
            if block.name == "TaskCreate":
                self._tasks[block.id] = {"content": str(tool_input.get("subject") or ""), "status": "pending"}
            else:
                task = self._tasks.get(str(tool_input.get("taskId")))
                if task and tool_input.get("status") == "deleted":
                    self._tasks = {k: v for k, v in self._tasks.items() if v is not task}
                elif task:
                    task["status"] = _plan_status(tool_input.get("status") or task["status"])
                    task["content"] = str(tool_input.get("subject") or task["content"])
            events.append({"type": "plan", "entries": [dict(task) for task in self._tasks.values()]})
            return
        kind, title = describe_tool(block.name, tool_input, self.workspace)
        if kind == "agent":
            self._agent_calls.add(block.id)
        events.append({"type": "tool_call", "tool_id": block.id, "name": block.name, "kind": kind,
                       "title": title, "input": tool_input, "parent_tool_id": parent})

    def _tool_result(self, block: ToolResultBlock, message: UserMessage, events: list[dict]) -> None:
        if block.tool_use_id in self._plan_tools:
            # TaskCreate reports the new task's id only in its result; re-key the entry by it.
            # (CLI 2.1.321: structured result {"task": {"id", ...}}, text "Task #<id> created successfully".)
            task = self._tasks.pop(block.tool_use_id, None)
            if task:
                structured = message.tool_use_result if isinstance(message.tool_use_result, dict) else {}
                in_text = re.search(r"Task #(\S+) created", _result_text(block.content))
                task_id = (structured.get("task") or {}).get("id") or (in_text and in_text.group(1))
                self._tasks[str(task_id or len(self._tasks) + 1)] = task
            return
        events.append({"type": "tool_result", "tool_id": block.tool_use_id,
                       "status": "error" if block.is_error else "ok",
                       "summary": _result_text(block.content)})

    def turn_result(self, stderr_tail: str = "") -> TurnResult:
        result = self.result
        if result is None:
            detail = self.error or "The agent process ended before it reported a result."
            return TurnResult("failed", self.session_id, f"{detail} {stderr_tail}".strip())
        raw = result.usage or {}
        tokens_in = [raw.get(key) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
        usage = {**empty_usage(), "output_tokens": raw.get("output_tokens"), "cost_usd": result.total_cost_usd,
                 "duration_ms": result.duration_ms,
                 "input_tokens": sum(t for t in tokens_in if isinstance(t, int)) if any(
                     isinstance(t, int) for t in tokens_in) else None}
        split = {"input": raw.get("input_tokens"), "cache_read": raw.get("cache_read_input_tokens"),
                 "cache_write": raw.get("cache_creation_input_tokens"), "output": raw.get("output_tokens")}
        if any(isinstance(value, int) for value in split.values()):
            # Kept apart for the backup cost calculation: input here is without the cached tokens.
            usage["tokens"] = {kind: value if isinstance(value, int) else 0 for kind, value in split.items()}
            # How many of the written tokens had the 1-hour lifetime (None when the program does not say):
            # Anthropic bills those at a higher rate, and the agent program writes them.
            usage["tokens"]["cache_write_1h"] = prices.long_write(raw)
        if self.gateway:
            usage["cost_usd"] = None        # priced by the gateway, which the CLI cannot know
        else:
            # On the subscription the figure is what the tokens would cost at API prices, not a charge.
            usage["cost_estimated"] = usage["cost_usd"] is not None and not self.charged
        if result.is_error:
            error = (result.result or "; ".join(result.errors or []) or self.error
                     or f"The agent stopped with an error ({result.subtype.replace('_', ' ')}).")
            return TurnResult("failed", self.session_id, error, usage)
        return TurnResult("completed", self.session_id, None, usage)


def _plan_status(status: Any) -> str:
    return status if status in ("pending", "in_progress", "completed") else "pending"


async def gateway_problem(connection: dict, env: dict, model: str | None, max_tokens: int = 1,
                          shown: str | None = None) -> str | None:
    """One tiny request before the CLI starts. The CLI retries a rejected key or an unknown
    model for minutes; the gateway's own answer says what is wrong in a second. `shown` is the
    model's name as the person knows it, when the gateway is asked for it under another."""
    import httpx
    url = env["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
    body = {"model": model, "max_tokens": max_tokens, "system": "Connection check.",
            "messages": [{"role": "user", "content": "ok"}]}
    headers = {"x-api-key": env["ANTHROPIC_AUTH_TOKEN"], "authorization": f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}",
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.ConnectError:
        return f"The gateway for “{connection['label']}” is not reachable at {env['ANTHROPIC_BASE_URL']}."
    except httpx.HTTPError as exc:
        return f"The gateway for “{connection['label']}” did not answer ({exc.__class__.__name__})."
    if response.status_code < 400:
        return None
    try:
        detail = response.json().get("error", {})
        detail = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        detail = response.text
    detail = " ".join(str(detail or "").split())[:500]
    return f"“{connection['label']}” refused the model {shown or model} (HTTP {response.status_code}): {detail}"


class ClaudeHarness:
    def __init__(self, kind: str, secrets=None, sidecar: "managed.Sidecar | None" = None, connections=None):
        self.kind = kind
        self.secrets = secrets      # where setup keeps typed keys (keystore.py); None reads the environment only
        self.sidecar = sidecar      # the LiteLLM the studio runs for `litellm` connections
        self.connections = connections or (lambda: [])     # every configured connection, read when needed
        # The Claude plan: is this computer signed in? `claude auth status` runs in a thread and the
        # answer is kept for LOGIN_TTL seconds, so describe() never waits on it.
        self._login: dict | None = None
        self._login_at = 0.0
        self._login_checking = False
        self._login_guard = threading.Lock()

    # Tests inject a detector (no real `claude` runs); it is asked directly, on the caller's thread.
    # None → setup.detect_claude_login, always in a background thread.
    login_detector = None
    LOGIN_TTL = 60.0
    LOGIN_CHECKING = "Checking the Claude sign-in on this computer."

    def _refresh_login(self) -> None:
        try:
            if self.login_detector is not None:
                answer = self.login_detector()
            else:
                from ..setup import detect_claude_login
                answer = detect_claude_login()
        except Exception as exc:        # the check itself failed: say so, never claim a sign-in
            answer = {"found": False, "detail": f"The Claude sign-in could not be checked ({exc})."}
        with self._login_guard:
            self._login, self._login_at, self._login_checking = answer, time.monotonic(), False

    def login_problem(self) -> str | None:
        """None when signed in to a Claude plan; else the sentence to show. Never blocks."""
        with self._login_guard:
            answer, fresh = self._login, time.monotonic() - self._login_at < self.LOGIN_TTL
            start = not fresh and not self._login_checking
            if start:
                self._login_checking = True
        if start and self.login_detector is not None:
            self._refresh_login()
            answer = self._login
        elif start:
            threading.Thread(target=self._refresh_login, name="claude-login-check", daemon=True).start()
        if answer is None:
            return self.LOGIN_CHECKING
        return None if answer.get("found") else str(answer.get("detail") or
                                                     "No Claude sign-in on this computer. Sign in with `studio login`, or use a key.")

    def _managed_problem(self, connection: dict) -> str | None:
        """Why a `litellm` connection cannot run, without starting anything."""
        if connection.get("provider") not in managed.PROVIDERS:
            return f"Connection “{connection['id']}” names no provider the studio's LiteLLM knows."
        if self.sidecar is None:
            return "This studio was started without its own LiteLLM."
        if not self.sidecar.command and not managed.installed()["installed"]:
            return managed.installed()["detail"]
        try:
            _key(connection, os.environ, self.secrets)
            managed.model_entry(connection, "check")
        except (HarnessError, managed.GatewayError) as exc:
            return str(exc)
        return None

    def describe(self, connection: dict) -> dict:
        reason = connection.get("unavailable")      # set by config.load_connections: the plan in a shared studio
        if reason:
            pass
        elif not find_cli():
            reason = "The Claude Code CLI was not found on this machine."
        elif connection["kind"] == "claude-subscription" and os.environ.get("STUDIO2_FAKE") != "1" \
                and (reason := self.login_problem()):
            pass
        elif connection["kind"] == "litellm":
            reason = self._managed_problem(connection)
        else:
            try:
                child_env(connection, os.environ, secrets=self.secrets)
            except HarnessError as exc:
                reason = str(exc)
        return public_connection(connection, reason is None, reason)

    async def run(self, request: TurnRequest, emit: Emit) -> TurnResult:
        if request.connection.get("unavailable"):
            return TurnResult("failed", request.session_id, str(request.connection["unavailable"]))
        endpoint, kind, asked = None, request.connection["kind"], request.model
        if kind == "litellm":
            problem = self._managed_problem(request.connection)
            if problem:
                return TurnResult("failed", request.session_id, problem)
            try:
                endpoint = await self.sidecar.ensure(self.connections())
            except managed.GatewayError as exc:
                return TurnResult("failed", request.session_id, str(exc))
            if endpoint.notice:
                emit({"type": "notice", "level": "warning", "message": endpoint.notice})
        env, scrub = child_env(request.connection, os.environ, request.model, self.secrets, endpoint)
        env = {**hidden_secrets(os.environ), **outside_python(os.environ), **env}
        if kind == "litellm":
            # Inside the studio's LiteLLM a model is named after its connection; the agent program
            # asks for it by that name. The turn id rides along so LiteLLM's price for each request
            # can be added up for this turn (gateway.Sidecar.spend).
            request = dataclasses.replace(request, model=managed.routed(str(request.connection["id"]), asked))
            env["ANTHROPIC_CUSTOM_HEADERS"] = costs.turn_header(kind, request.turn_id)
        elif kind == "anthropic-gateway":
            # The turn's id as a tag the gateway records, so its spend log can be read back per turn.
            env["ANTHROPIC_CUSTOM_HEADERS"] = costs.turn_header(kind, request.turn_id)
        if kind in TRANSLATED_KINDS:
            # 16 tokens for the studio's LiteLLM: OpenAI's own endpoint refuses a smaller limit.
            problem = await gateway_problem(request.connection, env, request.model,
                                            16 if kind == "litellm" else 1, asked)
            if problem:
                return TurnResult("failed", request.session_id, problem)
        result, activity = await self._attempt(request, emit, env, scrub, request.session_id)
        if result.status == "failed" and request.session_id and activity == 0:
            # Nothing happened at all while resuming: most likely the saved session is gone from
            # disk (CLI: "No conversation found with session ID"). Try once more without it, so
            # a stale session id can never wedge a project.
            if "No conversation found" in (result.error or ""):
                emit({"type": "notice", "level": "warning",
                      "message": "The earlier conversation could not be resumed, so a new one was started."})
            result, _ = await self._attempt(request, emit, env, scrub, None)
        if kind == "litellm" and self.sidecar is not None:
            rates = (prices.shipped(self.sidecar.settings.library_dir),)
            spent = await asyncio.to_thread(self.sidecar.spend, request.turn_id, rates)
            if spent:   # what LiteLLM itself priced the turn's requests at; one it could not price the studio
                #         prices from the shipped list (the backup), and what is left is counted as unpriced
                result.usage.update(requests=spent["requests"], unpriced_requests=spent["unpriced"])
                if spent["cost_usd"] is not None:
                    result.usage.update(cost_usd=spent["cost_usd"], cost_estimated=False, cost_basis=spent["basis"],
                                        backup=spent.get("backup"))
        return result

    async def _attempt(self, request, emit, env, scrub, resume) -> tuple[TurnResult, int]:
        kind = request.connection["kind"]
        mapper = EventMapper(request.workspace, kind in TRANSLATED_KINDS,
                             charged=kind in ("anthropic-api", "bedrock", "vertex", "foundry"))
        started = time.monotonic()
        stderr: deque[str] = deque(maxlen=12)
        client = ClaudeSDKClient(build_options(request, env, resume, stderr.append))
        process = None
        try:
            async with _SPAWN_LOCK:
                saved = {name: os.environ.pop(name) for name in scrub if name in os.environ}
                try:
                    await client.connect()
                finally:
                    os.environ.update(saved)
            process = getattr(getattr(client, "_transport", None), "_process", None)
            await client.query(request.prompt)
            stream = client.receive_messages().__aiter__()
            answered = False   # the result of the person's own message has arrived
            while True:
                # A result is not always the end: the session runs further turns by itself when
                # a background sub-agent reports back, and a resumed session can open with an
                # empty result for sub-agents that died with the previous process. Once nothing
                # is outstanding, wait a bounded time for a last turn instead of forever.
                idle = mapper.result is not None and not mapper.background
                try:
                    message = await asyncio.wait_for(stream.__anext__(), FINAL_TURN_GRACE if idle else None)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    break
                before = mapper.activity
                for event in mapper.handle(message):
                    emit(event)
                if isinstance(message, ResultMessage):
                    injected = (message.origin or {}).get("kind") == "task-notification"
                    empty = before == 0 and not (message.usage or {}).get("output_tokens")
                    answered = answered or not (injected or empty)
                    if message.is_error or (answered and not mapper.background):
                        break
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):  # ask the CLI to stop its tools before closing
                await asyncio.wait_for(client.interrupt(), 2)
            raise
        except Exception as exc:  # CLI missing, spawn failure, process died, bad JSON
            tail = " ".join(stderr)[-600:]
            return TurnResult("failed", mapper.session_id, f"{exc} {tail}".strip()), mapper.activity
        finally:
            # disconnect() closes stdin, waits, then SIGTERMs/SIGKILLs the child (bounded by the SDK).
            with contextlib.suppress(Exception):
                await client.disconnect()
            if process is not None and process.returncode is None:
                with contextlib.suppress(Exception):
                    process.kill()
        result = mapper.turn_result(" ".join(stderr)[-600:] if mapper.result is None else "")
        if mapper.result is not None:  # the CLI reports only its last turn; background sub-agents add more
            result.usage["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result, mapper.activity
