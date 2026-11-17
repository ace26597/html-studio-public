"""One short question to the connection's cheap, fast model (Haiku on the Claude plan).

Used by the library to pick skills and connectors before a turn (picker.py). It goes through
the same agent program and the same environment as a turn (child_env), so it works on every
connection kind a turn works on, with no tools, no settings, no plugins and no MCP servers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock

from .. import costs
from ..config import environment, without
from . import claude as agent

CHEAP_MODEL = "haiku"   # an alias: child_env pins what it means on gateways and cloud accounts


class CheapCallFailed(Exception):
    pass


async def ask(connection: dict, system: str, prompt: str, timeout: float, secrets=None,
              workdir: str | None = None, endpoint=None) -> str:
    """→ the model's text. Raises CheapCallFailed on any failure and asyncio.TimeoutError on time."""
    parent = environment()      # os.environ lacks the endpoint variables while another program starts
    try:
        env, scrub = agent.child_env(connection, parent, None, secrets, endpoint)
    except agent.HarnessError as exc:
        raise CheapCallFailed(str(exc)) from None
    env = {**agent.hidden_secrets(parent), **agent.outside_python(parent), **env}
    header = costs.turn_header(str(connection.get("kind")), costs.CURRENT_TURN.get())
    if header:      # a request of the turn it helps: its spend is counted in the turn
        env["ANTHROPIC_CUSTOM_HEADERS"] = header
    options = ClaudeAgentOptions(
        model=CHEAP_MODEL, system_prompt=system, max_turns=1, tools=[], setting_sources=[],
        strict_mcp_config=True, mcp_servers={}, permission_mode="default", env=env, cwd=workdir,
        cli_path=os.environ.get("STUDIO2_CLAUDE_CLI") or None)
    client = ClaudeSDKClient(options)
    parts: list[str] = []

    async def run() -> None:
        async with agent._SPAWN_LOCK:
            with without(scrub):
                await client.connect()
        await client.query(prompt)
        async for message in client.receive_messages():
            if isinstance(message, AssistantMessage):
                parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    raise CheapCallFailed(str(message.result or "the model call failed")[:300])
                return

    process = None
    try:
        task = asyncio.ensure_future(run())
        try:
            await asyncio.wait_for(task, timeout)
        finally:
            process = getattr(getattr(client, "_transport", None), "_process", None)
    except (asyncio.TimeoutError, CheapCallFailed):
        raise
    except Exception as exc:  # CLI missing, spawn failure, bad JSON
        raise CheapCallFailed(f"{exc.__class__.__name__}: {exc}"[:300]) from None
    finally:
        with contextlib.suppress(Exception):
            if process is not None and process.returncode is None:
                process.kill()   # a picking call is never worth a graceful wait
        with contextlib.suppress(Exception):
            await asyncio.wait_for(client.disconnect(), 2)
    return "".join(parts)
