"""The harness interface from CONTRACT.md ("Harness interface")."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

Emit = Callable[[dict], None]
Approve = Callable[[str, str, str, str], Awaitable[bool]]  # (tool, title, detail, reason) → allowed


async def allow_everything(_tool: str, _title: str, _detail: str, _reason: str) -> bool:
    return True


def empty_usage() -> dict:
    return {"input_tokens": None, "output_tokens": None, "cost_usd": None, "duration_ms": None,
            "cost_estimated": False}


@dataclass
class TurnRequest:
    project_id: str
    turn_id: str
    workspace: Path
    prompt: str                 # user text plus selection / user-change context, already composed
    session_id: str | None      # resume when set
    connection: dict            # resolved connection config
    model: str | None
    # Additions to the contract (optional): the raw parts the prompt was composed from.
    text: str = ""
    selection: dict | None = None
    attachments: list[str] = field(default_factory=list)
    context: str = ""           # extra system-prompt text; unused today (project facts ride in the message)
    approve: Approve = allow_everything
    plugin_dir: Path | None = None   # the project's own library plugin (enabled skills + agents)
    mcp_servers: dict = field(default_factory=dict)  # name → MCP server config, "studio" included
    # Connectors the studio picked by itself for this request (not switched on by the person):
    # the first use of each asks the person, unless the project allows everything.
    ask_first: set = field(default_factory=set)
    # The studio's own tools called directly (the fake harness uses it; a real harness reaches them over MCP).
    studio_tool: Callable[[str, dict], Awaitable[dict]] | None = None
    # How much to do and whether to verify ({depth, verify}); already in `prompt` as <studio-effort>.
    options: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    status: str                 # completed | failed
    session_id: str | None = None
    error: str | None = None
    usage: dict = field(default_factory=empty_usage)


class Harness(Protocol):
    kind: str

    def describe(self, connection: dict) -> dict: ...          # → Connection

    async def run(self, request: TurnRequest, emit: Emit) -> TurnResult: ...


def public_connection(connection: dict, available: bool, reason: str | None) -> dict:
    """The Connection object the API returns. Never includes base URLs or key names."""
    from ..config import is_experimental
    models = [{**model, "experimental": True} if is_experimental(connection, model["id"]) else model
              for model in connection["models"]]
    public = {"id": connection["id"], "label": connection["label"], "kind": connection["kind"],
              "available": available, "reason": reason,
              "models": models, "default_model": connection["default_model"]}
    if any(model.get("experimental") for model in models):
        public["experimental"] = True      # some model here is not Claude's (CONTRACT.md, "Any model")
    return public
