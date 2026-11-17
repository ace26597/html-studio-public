"""The Claude harness without Claude: SDK message objects are built by hand, no process is spawned."""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock, ThinkingBlock,
    ToolResultBlock, ToolUseBlock, UserMessage,
)

from studio2_server.config import Settings, load_connections
from studio2_server.harness import Registry
from studio2_server.harness.base import TurnRequest
from studio2_server.harness.claude import (
    LIBRARY_DIR, ClaudeHarness, EventMapper, HarnessError, build_options, child_env, describe_tool, hidden_secrets,
    outside_workspace,
)

SUB = {"id": "subscription", "label": "Claude subscription", "kind": "claude-subscription",
       "models": [{"id": "opus", "label": "Opus"}], "default_model": "opus"}
GATEWAY = {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:4000",
           "api_key_env": "LITELLM_API_KEY", "models": [{"id": "gpt-sol", "label": "gpt-sol"}],
           "default_model": "gpt-sol"}


def stream(event, parent=None):
    return StreamEvent(uuid="u", session_id="s1", event=event, parent_tool_use_id=parent)


def delta(kind, text, index=0):
    return stream({"type": "content_block_delta", "index": index, "delta": {"type": f"{kind}_delta", kind: text}})


def feed(mapper, *messages):
    return [event for message in messages for event in mapper.handle(message)]


def result(**overrides):
    fields = dict(subtype="success", duration_ms=1200, duration_api_ms=900, is_error=False, num_turns=2,
                  session_id="s1", total_cost_usd=0.0421,
                  usage={"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100,
                         "output_tokens": 50})
    return ResultMessage(**{**fields, **overrides})


def test_the_start_up_report_names_each_connectors_tools(tmp_path):
    events = feed(EventMapper(tmp_path), SystemMessage(subtype="init", data={
        "session_id": "s1", "tools": ["Read", "mcp__weather__get_forecast", "mcp__weather__get_alerts",
                                      "mcp__slack-legal__search"],
        "mcp_servers": [{"name": "weather", "status": "connected"}, {"name": "slack-legal", "status": "needs-auth"}]}))
    assert events[0]["mcp_servers"] == [
        {"name": "weather", "status": "connected", "tools": ["get_forecast", "get_alerts"]},
        {"name": "slack-legal", "status": "needs-auth", "tools": ["search"]}]
    assert "“slack-legal” could not be used in this turn (needs auth)" in events[1]["message"]


def test_streamed_text_is_emitted_once_and_tools_are_mapped(tmp_path):
    mapper = EventMapper(tmp_path)
    events = feed(
        mapper,
        SystemMessage(subtype="init", data={"session_id": "s1", "plugin_errors": [{"message": "bad manifest"}]}),
        stream({"type": "message_start", "message": {"id": "msg_1"}}),
        delta("thinking", "Let me look."),
        delta("text", "I will ", 1), delta("text", "edit the page.", 1),
        AssistantMessage(content=[ThinkingBlock(thinking="Let me look.", signature="sig")], model="m", message_id="msg_1"),
        AssistantMessage(content=[TextBlock(text="I will edit the page.")], model="m", message_id="msg_1"),
        AssistantMessage(content=[ToolUseBlock(id="tu_1", name="Edit", input={
            "file_path": str(tmp_path / "index.html"), "old_string": "a", "new_string": "b"})], model="m", message_id="msg_1"),
        UserMessage(content=[ToolResultBlock(tool_use_id="tu_1", content=[{"type": "text", "text": "Edited."}])]),
        AssistantMessage(content=[ToolUseBlock(id="tu_2", name="Bash", input={"command": "python build.py"})],
                         model="m", message_id="msg_2"),
        UserMessage(content=[ToolResultBlock(tool_use_id="tu_2", content="boom", is_error=True)]),
        result(),
    )
    assert events == [
        {"type": "session", "session_id": "s1", "skills": [], "mcp_servers": []},
        {"type": "notice", "level": "warning", "message": "Studio library did not load fully: bad manifest"},
        {"type": "thought_chunk", "message_id": "msg_1:0", "text": "Let me look."},
        {"type": "message_chunk", "message_id": "msg_1:1", "text": "I will "},
        {"type": "message_chunk", "message_id": "msg_1:1", "text": "edit the page."},
        {"type": "tool_call", "tool_id": "tu_1", "name": "Edit", "kind": "edit", "title": "Edit index.html",
         "input": {"file_path": str(tmp_path / "index.html"), "old_string": "a", "new_string": "b"},
         "parent_tool_id": None},
        {"type": "tool_result", "tool_id": "tu_1", "status": "ok", "summary": "Edited."},
        {"type": "tool_call", "tool_id": "tu_2", "name": "Bash", "kind": "execute", "title": "Run python build.py",
         "input": {"command": "python build.py"}, "parent_tool_id": None},
        {"type": "tool_result", "tool_id": "tu_2", "status": "error", "summary": "boom"},
    ]
    outcome = mapper.turn_result()
    assert outcome.status == "completed" and outcome.session_id == "s1" and outcome.error is None
    assert outcome.usage == {"input_tokens": 115, "output_tokens": 50, "cost_usd": 0.0421, "duration_ms": 1200,
                             "cost_estimated": True,
                             # the split is kept for the backup cost calculation: input without the cached ones
                             "tokens": {"input": 10, "cache_read": 100, "cache_write": 5, "output": 50, "cache_write_1h": None}}
    assert mapper.activity == 7


def test_a_gateway_turn_keeps_the_agent_programs_running_total_but_never_as_its_price(tmp_path):
    mapper = EventMapper(tmp_path, gateway=True)
    feed(mapper, result(total_cost_usd=1.5))
    usage = mapper.turn_result().usage
    assert usage["cost_usd"] is None                 # the gateway prices the turn, not the agent program
    assert usage["session_cost_usd"] == 1.5          # the conversation's total, so the next turn's part is right


def test_unstreamed_text_plan_subagents_and_errors(tmp_path):
    mapper = EventMapper(tmp_path)
    events = feed(
        mapper,
        AssistantMessage(content=[TextBlock(text="No partials here.")], model="m", message_id="msg_9"),
        AssistantMessage(content=[ToolUseBlock(id="todo", name="TodoWrite", input={"todos": [
            {"content": "Fetch data", "status": "completed", "activeForm": "Fetching"},
            {"content": "Draw chart", "status": "in_progress", "activeForm": "Drawing"}]})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="todo", content="Todos updated")]),
        AssistantMessage(content=[ToolUseBlock(id="agent_1", name="Task", input={
            "description": "Research competitors", "subagent_type": "general-purpose", "prompt": "…"})], model="m"),
        stream({"type": "message_start", "message": {"id": "sub_msg"}}, parent="agent_1"),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "sub chatter"}},
               parent="agent_1"),
        AssistantMessage(content=[ToolUseBlock(id="tu_3", name="WebFetch", input={"url": "https://example.com/a"})],
                         model="m", parent_tool_use_id="agent_1"),
        UserMessage(content=[ToolResultBlock(tool_use_id="tu_3", content="page text")], parent_tool_use_id="agent_1"),
        UserMessage(content="a plain user echo is ignored"),
    )
    assert [e["type"] for e in events] == ["message_chunk", "plan", "tool_call", "tool_call", "tool_result"]
    assert events[0]["text"] == "No partials here." and events[0]["message_id"].startswith("msg_9")
    assert events[1]["entries"] == [{"content": "Fetch data", "status": "completed"},
                                    {"content": "Draw chart", "status": "in_progress"}]
    assert events[2]["kind"] == "agent" and events[2]["title"] == "Research competitors"
    assert events[3]["kind"] == "fetch" and events[3]["parent_tool_id"] == "agent_1"
    assert events[3]["title"] == "Fetch https://example.com/a"

    assert mapper.turn_result().status == "failed"          # no ResultMessage: the process died
    mapper.handle(result(is_error=True, subtype="error_during_execution", result=None, errors=["API overloaded"]))
    failed = mapper.turn_result()
    assert failed.status == "failed" and failed.error == "API overloaded" and failed.session_id == "s1"


def test_task_tools_become_a_plan(tmp_path):
    mapper = EventMapper(tmp_path)
    events = feed(
        mapper,
        AssistantMessage(content=[ToolUseBlock(id="c1", name="TaskCreate", input={"subject": "Outline"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="Task #1 created successfully: Outline")],
                    tool_use_result={"task": {"id": "1", "subject": "Outline"}}),
        AssistantMessage(content=[ToolUseBlock(id="c2", name="TaskCreate", input={"subject": "Charts"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="c2", content="Task #2 created successfully: Charts")]),
        AssistantMessage(content=[ToolUseBlock(id="u1", name="TaskUpdate", input={"taskId": "2", "status": "in_progress"})],
                         model="m"),
    )
    assert [e["type"] for e in events] == ["plan", "plan", "plan"]
    assert events[-1]["entries"] == [{"content": "Outline", "status": "pending"},
                                     {"content": "Charts", "status": "in_progress"}]


def test_tool_titles(tmp_path):
    assert describe_tool("Read", {"file_path": str(tmp_path / "data" / "a.csv")}, tmp_path) == ("read", "Read data/a.csv")
    assert describe_tool("Write", {"file_path": "index.html"}, tmp_path)[0] == "edit"
    assert describe_tool("Grep", {"pattern": "TODO"}, tmp_path) == ("search", "Search for TODO")
    assert describe_tool("Glob", {"pattern": "**/*.html"}, tmp_path) == ("search", "Find files **/*.html")
    assert describe_tool("WebSearch", {"query": "acme revenue"}, tmp_path) == ("search", "Search the web for acme revenue")
    assert describe_tool("mcp__playwright__screenshot", {}, tmp_path) == ("other", "playwright · screenshot")
    kind, title = describe_tool("Bash", {"command": "echo " + "x" * 300}, tmp_path)
    assert kind == "execute" and len(title) <= 118 and title.endswith("…")


def test_child_env_for_both_connection_kinds():
    parent = {"ANTHROPIC_API_KEY": "sk-parent", "LITELLM_API_KEY": "sk-litellm", "PATH": "/bin"}
    env, scrub = child_env(SUB, parent)
    assert env == {} and {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                          "CLAUDE_CODE_USE_BEDROCK"} <= scrub and "CLAUDE_CODE_OAUTH_TOKEN" not in scrub

    env, scrub = child_env(GATEWAY, parent, model="gpt-sol")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4000" and env["ANTHROPIC_AUTH_TOKEN"] == "sk-litellm"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "gpt-sol"
    assert "ANTHROPIC_API_KEY" in scrub and "CLAUDE_CODE_OAUTH_TOKEN" in scrub and "ANTHROPIC_API_KEY" not in env

    with pytest.raises(HarnessError, match="LITELLM_API_KEY"):
        child_env(GATEWAY, {"PATH": "/bin"})
    with pytest.raises(HarnessError, match="base_url"):
        child_env({**GATEWAY, "base_url": ""}, parent)


def test_describe_reports_availability_without_a_model_call(monkeypatch, tmp_path):
    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("STUDIO2_CLAUDE_CLI", str(fake_cli))
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    assert ClaudeHarness("claude-subscription").describe(SUB)["available"] is True
    described = ClaudeHarness("anthropic-gateway").describe(GATEWAY)
    assert described["available"] is False and "LITELLM_API_KEY" in described["reason"]
    assert "base_url" not in described and "api_key_env" not in described
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-value")
    described = ClaudeHarness("anthropic-gateway").describe(GATEWAY)
    assert described["available"] is True and "sk-test-value" not in json.dumps(described)

    monkeypatch.setenv("STUDIO2_CLAUDE_CLI", str(tmp_path / "missing"))
    missing = ClaudeHarness("claude-subscription").describe(SUB)
    assert missing["available"] is False and "not found" in missing["reason"]


def test_options_isolate_the_session_and_guard_the_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO2_CLAUDE_CLI", raising=False)
    request = TurnRequest(project_id="p_1", turn_id="t_1", workspace=tmp_path, prompt="hi", session_id="sess",
                          connection=SUB, model="opus")
    options = build_options(request, {"X": "1"}, resume="sess")
    assert options.cwd == str(tmp_path) and options.resume == "sess" and options.model == "opus"
    assert options.include_partial_messages is True and options.env == {"X": "1"}
    assert options.setting_sources == [] and options.strict_mcp_config is True   # nothing personal is inherited
    assert options.permission_mode == "acceptEdits"
    assert options.plugins == [{"type": "local", "path": str(LIBRARY_DIR)}]
    assert options.system_prompt["preset"] == "claude_code"
    assert "Studio project" in options.system_prompt["append"]
    assert options.thinking == {"type": "adaptive", "display": "summarized"}
    assert build_options(TurnRequest("p_1", "t_1", tmp_path, "hi", None, GATEWAY, "gpt-sol"), {}, None).thinking is None

    inside = {"file_path": str(tmp_path / "sub" / "a.html")}
    outside = {"file_path": "/etc/hosts"}
    assert outside_workspace(tmp_path, inside) is None and outside_workspace(tmp_path, {"file_path": "a.html"}) is None
    assert outside_workspace(tmp_path, {"file_path": "../escape.txt"}) and outside_workspace(tmp_path, outside)
    assert outside_workspace(tmp_path, {"command": "ls"}) is None

    matcher = options.hooks["PreToolUse"][0]
    assert matcher.matcher == "Write|Edit|MultiEdit|NotebookEdit"
    hook = matcher.hooks[0]
    allowed = asyncio.run(hook({"tool_name": "Write", "tool_input": inside}, "tu", {"signal": None}))
    denied = asyncio.run(hook({"tool_name": "Write", "tool_input": outside}, "tu", {"signal": None}))
    assert allowed == {} and denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(options.can_use_tool("Bash", {"command": "ls"}, None)).behavior == "allow"
    assert asyncio.run(options.can_use_tool("Edit", outside, None)).behavior == "deny"


def test_library_is_a_valid_local_plugin():
    manifest = json.loads((LIBRARY_DIR / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "studio"
    skill = (LIBRARY_DIR / "skills" / "studio-page-basics" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: studio-page-basics\ndescription: ")
    assert 25 <= len((LIBRARY_DIR / "system-prompt.md").read_text().splitlines()) <= 60


def test_connections_from_config_file(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"connections": [
        {"id": "subscription", "kind": "claude-subscription", "models": ["opus", "sonnet"]},
        {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:4000",
         "api_key_env": "LITELLM_API_KEY", "models": ["claude-opus", "gpt-sol"]},
        {"id": "broken", "kind": "nonsense"}]}))
    settings = Settings(data_dir=tmp_path, config_path=config)
    connections = load_connections(settings)
    assert [c["id"] for c in connections] == ["subscription", "litellm"]
    assert connections[0]["models"] == [{"id": "opus", "label": "Opus"}, {"id": "sonnet", "label": "Sonnet"}]
    assert connections[0]["label"] == "subscription" and connections[1]["default_model"] == "claude-opus"

    assert [c["id"] for c in load_connections(Settings(data_dir=tmp_path, config_path=tmp_path / "none"))] == ["subscription"]
    fake = Settings(data_dir=tmp_path, config_path=config, fake=True)
    assert [c["kind"] for c in load_connections(fake)] == ["fake"]
    registry = Registry(settings)
    assert registry.resolve("litellm")["id"] == "litellm" and registry.resolve("gone")["id"] == "subscription"
    assert registry.harness("anthropic-gateway").kind == "anthropic-gateway"


def test_a_file_write_is_announced_as_soon_as_its_path_has_streamed(tmp_path):
    mapper = EventMapper(tmp_path)
    target = str(tmp_path / 'a "b".html')
    whole = json.dumps({"file_path": target, "content": "<h1>long page</h1>"})
    cut = whole.index('"content"') + 4
    part = lambda text: stream({"type": "content_block_delta", "index": 1,  # noqa: E731
                                "delta": {"type": "input_json_delta", "partial_json": text}})
    start = [stream({"type": "message_start", "message": {"id": "msg_1"}}),
             stream({"type": "content_block_start", "index": 1,
                     "content_block": {"type": "tool_use", "id": "tu_1", "name": "Write"}})]
    assert feed(mapper, *start, part(whole[:12])) == []            # path not complete yet
    early = feed(mapper, part(whole[12:cut]))
    assert [(e["type"], e["tool_id"], e["title"], e["input"]) for e in early] == [
        ("tool_call", "tu_1", 'Write a "b".html', {"file_path": target})]
    assert feed(mapper, part(whole[cut:])) == []                   # announced once
    final = feed(mapper, AssistantMessage(content=[ToolUseBlock(id="tu_1", name="Write", input=json.loads(whole))],
                                          model="m", message_id="msg_1"))
    assert [(e["type"], e["tool_id"]) for e in final] == [("tool_call", "tu_1")]
    assert final[0]["input"]["content"] == "<h1>long page</h1>"


def test_short_tools_are_not_announced_early(tmp_path):
    mapper = EventMapper(tmp_path)
    assert feed(
        mapper,
        stream({"type": "message_start", "message": {"id": "msg_1"}}),
        stream({"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "tu_1", "name": "Bash"}}),
        stream({"type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": "cat x", "file_path": "x"}'}}),
    ) == []


def test_background_subagents_keep_the_turn_open_and_studio_tools_get_titles(tmp_path):
    from claude_agent_sdk.types import TaskNotificationMessage, TaskStartedMessage, TaskUpdatedMessage
    mapper = EventMapper(tmp_path)
    mapper.handle(AssistantMessage(content=[ToolUseBlock(id="ag1", name="Agent", input={"description": "Financials"}),
                                            ToolUseBlock(id="sh1", name="Bash", input={"command": "sleep 99"})], model="m"))
    base = {"subtype": "task_started", "data": {}, "uuid": "u", "session_id": "s"}
    mapper.handle(TaskStartedMessage(**base, task_id="t1", description="Financials", tool_use_id="ag1"))
    mapper.handle(TaskStartedMessage(**base, task_id="t2", description="Other", task_type="local_agent"))
    mapper.handle(TaskStartedMessage(**base, task_id="t3", description="sleep", tool_use_id="sh1", task_type="local_bash"))
    assert mapper.background == {"t1", "t2"}          # a background shell command never holds a turn open
    mapper.handle(TaskUpdatedMessage(subtype="task_updated", data={}, task_id="t2", patch={"status": "killed"}, status="killed"))
    mapper.handle(TaskNotificationMessage(subtype="task_notification", data={}, task_id="t1", status="completed",
                                          output_file="o", summary="done", uuid="u", session_id="s"))
    assert mapper.background == set()
    assert describe_tool("mcp__studio__fetch_source", {"url": "https://a.example/x"}, tmp_path) == (
        "fetch", "Read and save https://a.example/x")
    assert describe_tool("mcp__studio__check_page", {}, tmp_path) == ("read", "Check the figures on index.html")
    assert describe_tool("mcp__docs__search", {}, tmp_path) == ("other", "docs · search")


def test_every_studio_tool_has_a_title_whatever_it_is_called_with(tmp_path):
    from studio2_server import tools
    from studio2_server.harness import claude
    for name, _description, schema, _handler in tools.TOOLS:
        filled = {key: ({"string": "x", "integer": 1, "number": 1.5, "boolean": True, "array": [1], "object": {}}[spec["type"]])
                  for key, spec in schema["properties"].items()}
        for given in ({}, filled):
            kind, title = claude._studio_tool(name, lambda key, given=given: " ".join(str(given.get(key) or "").split()))
            assert kind and title, name
            assert describe_tool(f"mcp__studio__{name}", given, tmp_path)[1]
    assert describe_tool("mcp__studio__render_video", {"path": "video/a.html", "check": True}, tmp_path) == (
        "read", "Look at frames of video/a.html")
    assert describe_tool("mcp__studio__render_video", {"path": "video/a.html"}, tmp_path) == (
        "execute", "Render video/a.html to MP4 video")


def test_the_agent_cannot_read_the_secrets_the_studio_was_started_with():
    parent = {"PATH": "/bin", "HOME": "/Users/x", "SSH_AUTH_SOCK": "/tmp/agent.sock", "STUDIO2_ACCESS_KEY": "a" * 24,
              "LITELLM_API_KEY": "sk-gateway", "FMP_API_KEY": "abc", "DB_PASSWORD": "pw", "GITHUB_TOKEN": "ghp",
              "ANTHROPIC_API_KEY": "sk-ant", "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "EMPTY_TOKEN": "",
              "NPM_TOKEN": "keep-me", "STUDIO2_AGENT_ENV": "NPM_TOKEN, OTHER"}
    hidden = hidden_secrets(parent)
    assert hidden == {"STUDIO2_ACCESS_KEY": "", "LITELLM_API_KEY": "", "FMP_API_KEY": "", "DB_PASSWORD": "",
                      "GITHUB_TOKEN": ""}
    # A gateway turn still gets its own key under the name the CLI reads.
    env, _ = child_env(GATEWAY, {**parent, GATEWAY["api_key_env"]: "sk-gateway"}, model="gpt-sol")
    merged = {**hidden_secrets(parent), **env}
    assert merged["ANTHROPIC_AUTH_TOKEN"] == "sk-gateway" and merged["LITELLM_API_KEY"] == ""


def test_direct_keys_and_cloud_providers_each_get_only_their_own_endpoint():
    parent = {"PATH": "/bin", "MY_CLAUDE_KEY": "sk-ant-1", "ANTHROPIC_AUTH_TOKEN": "stray", "AWS_ACCESS_KEY_ID": "AKIA",
              "AWS_SECRET_ACCESS_KEY": "aws-secret", "GOOGLE_APPLICATION_CREDENTIALS": "/keys/sa.json", "AZ_KEY": "az-1"}
    base = {"label": "x", "models": [{"id": "sonnet", "label": "Sonnet"}], "default_model": "sonnet"}

    env, scrub = child_env({**base, "id": "api", "kind": "anthropic-api", "api_key_env": "MY_CLAUDE_KEY"}, parent)
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-1"}
    assert {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"} <= scrub
    with pytest.raises(HarnessError, match="MY_CLAUDE_KEY"):
        child_env({**base, "id": "api", "kind": "anthropic-api", "api_key_env": "MY_CLAUDE_KEY"}, {"PATH": "/bin"})

    bedrock = {**base, "id": "aws", "kind": "bedrock", "region": "ap-south-1",
               "model_ids": {"sonnet": "apac.anthropic.claude-sonnet", "bogus": "x"}}
    env, scrub = child_env(bedrock, parent)
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1" and env["AWS_REGION"] == "ap-south-1"
    assert env["AWS_SECRET_ACCESS_KEY"] == "aws-secret" and env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "apac.anthropic.claude-sonnet"
    assert "ANTHROPIC_API_KEY" in scrub and "CLAUDE_CODE_USE_BEDROCK" not in scrub and "GOOGLE_APPLICATION_CREDENTIALS" not in env
    # The cloud's own credentials survive the blanking of secret-looking variables, for that kind only.
    merged = {**hidden_secrets(parent), **env}
    assert merged["AWS_SECRET_ACCESS_KEY"] == "aws-secret" and merged["AZ_KEY"] == "" and merged["MY_CLAUDE_KEY"] == ""
    with pytest.raises(HarnessError, match="region"):
        child_env({**bedrock, "region": ""}, parent)

    env, _ = child_env({**base, "id": "gcp", "kind": "vertex", "region": "asia-south1", "project": "p-1"}, parent)
    assert env == {"CLAUDE_CODE_USE_VERTEX": "1", "CLOUD_ML_REGION": "asia-south1", "ANTHROPIC_VERTEX_PROJECT_ID": "p-1",
                   "GOOGLE_APPLICATION_CREDENTIALS": "/keys/sa.json"}

    env, _ = child_env({**base, "id": "az", "kind": "foundry", "resource": "my-res", "api_key_env": "AZ_KEY"}, parent)
    assert env == {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_RESOURCE": "my-res", "ANTHROPIC_FOUNDRY_API_KEY": "az-1"}
    env, _ = child_env({**base, "id": "az", "kind": "foundry", "base_url": "https://x.example/anthropic"}, parent)
    assert env == {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://x.example/anthropic"}
    with pytest.raises(HarnessError, match="resource"):
        child_env({**base, "id": "az", "kind": "foundry"}, parent)


def test_a_helper_agents_words_never_become_a_chat_message(tmp_path):
    # Seen on 12 November 2026: a checker's closing note ("Coordinator may relay this …") arrived
    # unstreamed with its parent set and was shown to the person as the assistant's own message.
    mapper = EventMapper(tmp_path)
    events = feed(
        mapper,
        AssistantMessage(content=[ToolUseBlock(id="agent_1", name="Task", input={
            "description": "Check the page", "subagent_type": "checker", "prompt": "…"})], model="m"),
        AssistantMessage(content=[TextBlock(text="I have not written a report file. Coordinator may relay this.")],
                         model="m", message_id="msg_sub", parent_tool_use_id="agent_1"),
        AssistantMessage(content=[TextBlock(text="The page is ready.")], model="m", message_id="msg_main"),
    )
    said = [e["text"] for e in events if e["type"] in ("message_chunk", "thought_chunk")]
    assert said == ["The page is ready."]


# --- The check before a gateway turn, against a stand-in gateway on this machine (no model call) ---

class _PickyGateway:
    """Answers /v1/messages like a translating gateway in front of a reasoning model: a plain
    request with a tiny limit is refused (as the reviewer's Azure route did), a stream with the
    agent program's limit is accepted and keeps going for a while after its first event."""

    def __init__(self):
        import http.server
        import threading
        seen = self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append(body)
                if body["model"] == "unknown":
                    return self._json(400, {"error": {"message": "Invalid model name passed in model=unknown"}})
                if body["model"] == "small-cap" and body["max_tokens"] > 8192:
                    return self._json(400, {"error": {"message": (
                        f"max_tokens: {body['max_tokens']} > 8192, which is the maximum allowed number of "
                        "output tokens for small-cap")}})
                if body["model"] == "never-fits" and body["max_tokens"] > 16:
                    return self._json(400, {"error": {"message": "max_tokens is too large: this model supports at most 16"}})
                if not body.get("stream") and body["max_tokens"] < 16:
                    return self._json(400, {"error": {"message": "Unsupported value: 'max_tokens'. "
                                                                 "Use 'max_completion_tokens' instead."}})
                if not body.get("stream"):
                    return self._json(200, {"type": "message", "content": [{"type": "text", "text": "ok"}]})
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                if body["model"] == "refuses-in-stream":
                    events = [("error", {"type": "error", "error": {"type": "invalid_request_error",
                                                                    "message": "max_tokens is too large for this model"}})]
                else:
                    events = [("message_start", {"type": "message_start", "message": {"id": "m1"}})]
                try:
                    for name, data in events:
                        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(4)        # a reply still being written: the check must not wait for it
                    self.wfile.write(b'event: message_stop\ndata: {"type": "message_stop"}\n\n')
                except OSError:
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
                    "ANTHROPIC_AUTH_TOKEN": "sk-stand-in"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def picky_gateway():
    gateway = _PickyGateway()
    yield gateway
    gateway.close()


async def test_the_gateway_check_has_the_shape_of_the_agents_request(picky_gateway, monkeypatch):
    from studio2_server.harness.claude import gateway_problem
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    started = time.monotonic()
    assert await gateway_problem(GATEWAY, picky_gateway.env, "gpt-sol") is None
    # Read up to the first event and closed: not held open for the rest of the reply.
    assert time.monotonic() - started < 3
    sent = picky_gateway.seen[-1]
    assert sent["stream"] is True and sent["max_tokens"] == 32000       # the agent program's own limit
    assert "max_completion_tokens" not in sent

    # The limit the agent program will be given for this turn is the one checked.
    assert await gateway_problem(GATEWAY, {**picky_gateway.env, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"}, "gpt-sol") is None
    assert picky_gateway.seen[-1]["max_tokens"] == 8000
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "12000")      # inherited by the agent program too
    assert await gateway_problem(GATEWAY, picky_gateway.env, "gpt-sol") is None
    assert picky_gateway.seen[-1]["max_tokens"] == 12000


async def test_a_gateway_refusal_is_still_reported_in_its_own_words(picky_gateway):
    from studio2_server.harness.claude import gateway_problem
    problem = await gateway_problem(GATEWAY, picky_gateway.env, "unknown", shown="Unknown model")
    assert problem == ("“LiteLLM” refused the model Unknown model (HTTP 400): "
                       "Invalid model name passed in model=unknown")
    # A refusal sent as the stream's first event is a refusal too, not a pass.
    problem = await gateway_problem(GATEWAY, picky_gateway.env, "refuses-in-stream")
    assert problem and "max_tokens is too large for this model" in problem and "refuses-in-stream" in problem


async def test_a_gateway_turn_checks_the_limit_its_connection_sets(picky_gateway, monkeypatch, tmp_path):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-stand-in")
    connection = {**GATEWAY, "base_url": picky_gateway.env["ANTHROPIC_BASE_URL"], "max_output_tokens": 9000}
    request = TurnRequest("p_1", "t_1", tmp_path, "hi", None, connection, "unknown")
    result = await ClaudeHarness("anthropic-gateway").run(request, lambda _event: None)
    assert result.status == "failed" and "Invalid model name" in result.error
    assert picky_gateway.seen[-1]["max_tokens"] == 9000 and picky_gateway.seen[-1]["stream"] is True


def test_no_providers_credentials_or_endpoint_reach_a_turn_on_another_connection():
    cloud = {"ANTHROPIC_FOUNDRY_API_KEY": "az-shell", "ANTHROPIC_FOUNDRY_AUTH_TOKEN": "entra-shell",
             "ANTHROPIC_FOUNDRY_BASE_URL": "https://shell.example/anthropic", "ANTHROPIC_FOUNDRY_RESOURCE": "shell-res",
             "ANTHROPIC_VERTEX_PROJECT_ID": "shell-project", "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.example",
             "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.example", "AWS_BEARER_TOKEN_BEDROCK": "aws-bearer"}
    parent = {"PATH": "/bin", "LITELLM_API_KEY": "sk-litellm", "MY_CLAUDE_KEY": "sk-ant-1", "AZ_KEY": "az-1",
              "STUDIO2_AGENT_ENV": "AWS_BEARER_TOKEN_BEDROCK,NPM_TOKEN", "NPM_TOKEN": "keep-me", **cloud}
    base = {"label": "x", "models": [{"id": "sonnet", "label": "Sonnet"}], "default_model": "sonnet"}

    def child(connection, model=None):
        """What the agent program would see: the parent without the scrubbed names, then options.env."""
        env, scrub = child_env(connection, parent, model)
        inherited = {name: value for name, value in parent.items() if name not in scrub}
        return {**inherited, **hidden_secrets(parent), **env}

    for connection in (SUB, GATEWAY, {**base, "id": "api", "kind": "anthropic-api", "api_key_env": "MY_CLAUDE_KEY"}):
        seen = child(connection, "gpt-sol")
        leaked = {name: seen[name] for name in cloud if seen.get(name)}
        assert leaked == {}, connection["kind"]
        assert seen["NPM_TOKEN"] == "keep-me"            # STUDIO2_AGENT_ENV still passes other names through

    seen = child({**base, "id": "aws", "kind": "bedrock", "region": "ap-south-1"})
    assert seen["AWS_BEARER_TOKEN_BEDROCK"] == "aws-bearer"         # Bedrock's own sign-in, on Bedrock
    assert not any(seen.get(name) for name in cloud if "BEDROCK" not in name)
    seen = child({**base, "id": "gcp", "kind": "vertex", "region": "asia-south1", "project": "p-1"})
    assert seen["ANTHROPIC_VERTEX_PROJECT_ID"] == "p-1"             # the connection's own, not the shell's
    assert seen["ANTHROPIC_VERTEX_BASE_URL"] == "https://vertex.example"   # Vertex's own private address, on Vertex
    assert not any(seen.get(name) for name in cloud if "VERTEX" not in name)
    seen = child({**base, "id": "az", "kind": "foundry", "resource": "my-res", "api_key_env": "AZ_KEY"})
    assert seen["ANTHROPIC_FOUNDRY_RESOURCE"] == "my-res" and seen["ANTHROPIC_FOUNDRY_API_KEY"] == "az-1"
    assert seen["ANTHROPIC_FOUNDRY_AUTH_TOKEN"] == "entra-shell"   # Foundry's own sign-in, on Foundry
    assert not seen.get("ANTHROPIC_FOUNDRY_BASE_URL")               # the shell's other address does not fall through
    assert not any(seen.get(name) for name in cloud if "FOUNDRY" not in name)


async def test_a_limit_larger_than_the_model_takes_is_not_a_refusal_of_the_model(picky_gateway, monkeypatch):
    """The agent program lowers its own limit for the models it knows, so a check sent with the
    default limit must not fail a turn that would have run. It is asked again with a small limit."""
    from studio2_server.harness.claude import SMALL_CHECK_LIMIT, gateway_problem
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    assert await gateway_problem(GATEWAY, picky_gateway.env, "small-cap") is None
    assert [body["max_tokens"] for body in picky_gateway.seen[-2:]] == [32000, SMALL_CHECK_LIMIT]
    assert picky_gateway.seen[-1]["stream"] is True

    # Asked again once only, and a model that refuses the small limit too is reported in the gateway's words.
    before = len(picky_gateway.seen)
    problem = await gateway_problem(GATEWAY, picky_gateway.env, "never-fits")
    assert problem and "at most 16" in problem and len(picky_gateway.seen) == before + 2

    # Any other refusal is not asked about again.
    before = len(picky_gateway.seen)
    assert await gateway_problem(GATEWAY, picky_gateway.env, "unknown")
    assert len(picky_gateway.seen) == before + 1


def test_a_secret_under_the_agent_programs_own_prefix_is_hidden_too():
    """ANTHROPIC_* and CLAUDE_* used to be passed over whole, so an admin key or a key under
    another name in the studio's shell reached the agent's shell."""
    from studio2_server.harness.claude import child_env, hidden_secrets
    parent = {"ANTHROPIC_ADMIN_KEY": "sk-ant-admin", "CLAUDE_API_KEY": "x", "ANTHROPIC_SECRET": "s",
              "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "9000", "ANTHROPIC_MODEL": "opus", "CLAUDE_CODE_OAUTH_TOKEN": "plan",
              "NPM_TOKEN": "n", "PATH": "/bin"}
    hidden = hidden_secrets(parent)
    assert hidden == {"ANTHROPIC_ADMIN_KEY": "", "CLAUDE_API_KEY": "", "ANTHROPIC_SECRET": "", "NPM_TOKEN": ""}
    # The plan's own sign-in is child_env's to decide, and it still reaches a subscription turn.
    env, scrub = child_env({"id": "subscription", "kind": "claude-subscription"}, parent)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in scrub and "CLAUDE_CODE_OAUTH_TOKEN" not in {**hidden, **env}
    # A name the person lists is still passed through.
    assert "ANTHROPIC_ADMIN_KEY" not in hidden_secrets({**parent, "STUDIO2_AGENT_ENV": "ANTHROPIC_ADMIN_KEY"})


def test_a_clouds_own_endpoint_and_key_from_the_shell_reach_that_clouds_turns_only():
    """A private Bedrock or Vertex endpoint has no field in a connection: it is set in the studio's
    shell. It must still reach that cloud's own turns, and no other."""
    from studio2_server.harness.claude import child_env
    parent = {"ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.internal", "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.internal",
              "ANTHROPIC_FOUNDRY_API_KEY": "shell-key", "FOUNDRY_KEY": "connection-key"}
    env, scrub = child_env({"id": "b", "kind": "bedrock", "region": "us-east-1"}, parent)
    assert env["ANTHROPIC_BEDROCK_BASE_URL"] == "https://bedrock.internal"
    assert {"ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY"} <= scrub
    env, scrub = child_env({"id": "v", "kind": "vertex", "region": "us-east5", "project": "p"}, parent)
    assert env["ANTHROPIC_VERTEX_BASE_URL"] == "https://vertex.internal" and "ANTHROPIC_BEDROCK_BASE_URL" in scrub
    # Foundry with no key of its own in the connection uses the shell's; a connection's own key wins.
    env, _ = child_env({"id": "f", "kind": "foundry", "resource": "r"}, parent)
    assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "shell-key"
    env, _ = child_env({"id": "f", "kind": "foundry", "resource": "r", "api_key_env": "FOUNDRY_KEY"}, parent)
    assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "connection-key"
    _, scrub = child_env({"id": "subscription", "kind": "claude-subscription"}, parent)
    assert {"ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY"} <= scrub


# -- connector settings stay off the agent program's command line --------------------------------
SECRET_HEADER, SECRET_ENV = "Bearer ghp_header0123456789", "db-password-0123456789"
CONNECTORS = {"docs": {"type": "http", "url": "https://docs.example/mcp", "headers": {"Authorization": SECRET_HEADER}},
              "db": {"type": "stdio", "command": "db-mcp", "args": [], "env": {"DB_PASSWORD": SECRET_ENV}},
              "studio": {"type": "sdk", "name": "studio", "instance": object()}}


def command_line(options) -> list[str]:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    return SubprocessCLITransport(prompt="hi", options=options)._build_command()


def test_connector_secrets_are_in_an_owner_only_file_not_on_the_command_line(tmp_path, monkeypatch):
    from studio2_server.harness.claude import write_mcp_config
    monkeypatch.setenv("STUDIO2_CLAUDE_CLI", "/bin/echo")
    workspace, run = tmp_path / "project", tmp_path / "data" / "run" / "mcp"
    workspace.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    request = TurnRequest("p_1", "t_1", workspace, "hi", None, GATEWAY, "gpt-sol", context="Studio project facts",
                          plugin_dir=plugin, mcp_servers=CONNECTORS)
    env, _scrub = child_env(GATEWAY, {"LITELLM_API_KEY": "sk-gateway-0123456789"}, "gpt-sol")
    path = write_mcp_config(request.mcp_servers, run)
    argv = command_line(build_options(request, env, None, mcp_file=path))
    text = " ".join(argv)
    for secret in (SECRET_HEADER, SECRET_ENV, "sk-gateway-0123456789"):
        assert secret not in text
    assert "--plugin-dir" in argv and "--append-system-prompt" in argv
    # The studio's own tools (in the studio's process, no secret) stay inline; the rest is a file path.
    values = [argv[i + 1] for i, item in enumerate(argv) if item == "--mcp-config"]
    assert len(values) == 2 and json.loads(values[0]) == {"mcpServers": {"studio": {"type": "sdk", "name": "studio"}}}
    assert values[1] == str(path) and path.parent == run and workspace not in path.parents
    assert (path.stat().st_mode & 0o777) == 0o600 and (run.stat().st_mode & 0o777) == 0o700
    assert json.loads(path.read_text()) == {"mcpServers": {k: v for k, v in CONNECTORS.items() if k != "studio"}}
    # With no connectors there is no file, and a loosened folder is locked again.
    run.chmod(0o755)
    assert write_mcp_config({"studio": CONNECTORS["studio"]}, run) is None
    assert write_mcp_config(CONNECTORS, run) and (run.stat().st_mode & 0o777) == 0o700
    with pytest.raises(HarnessError):     # connectors without a file would go back on the command line
        build_options(request, env, None)


class FailingClient:
    """Stands in for ClaudeSDKClient: records whether the settings file is there while the program runs."""
    seen: list = []

    def __init__(self, options):
        self.options = options

    async def connect(self):
        path = Path(self.options.extra_args["mcp-config"])
        FailingClient.seen.append((path, path.is_file()))

    async def query(self, _prompt):
        raise RuntimeError("the program died")

    async def interrupt(self):
        pass

    async def disconnect(self):
        path = FailingClient.seen[-1][0]
        FailingClient.seen.append((path, path.is_file()))     # still there until the program is gone


async def test_the_connector_file_lives_as_long_as_the_turn_and_no_longer(tmp_path, monkeypatch):
    from studio2_server.harness import claude as harness_module
    monkeypatch.setattr(harness_module, "ClaudeSDKClient", FailingClient)
    run = tmp_path / "run"
    FailingClient.seen = []
    request = TurnRequest("p_1", "t_1", tmp_path, "hi", None, GATEWAY, "gpt-sol", mcp_servers=CONNECTORS)
    result, _ = await ClaudeHarness("anthropic-gateway", mcp_dir=run)._attempt(request, lambda _e: None, {}, set(), None)
    assert result.status == "failed" and "the program died" in result.error
    (path, during), (_same, before_exit) = FailingClient.seen
    assert during and before_exit and not path.exists() and list(run.iterdir()) == []

    # A studio that was killed mid-turn leaves its file; the next start removes it.
    registry = Registry(Settings(data_dir=tmp_path / "data", fake=True))
    registry.mcp_dir.mkdir(parents=True)
    (registry.mcp_dir / "mcp-old.json").write_text("{}")
    registry.sweep()
    assert list(registry.mcp_dir.iterdir()) == []


async def test_a_key_in_the_environment_stays_readable_while_another_turn_starts(tmp_path, monkeypatch):
    """The endpoint variables are taken out of the studio's environment while an agent program is
    started, so it cannot inherit them. Everything else the studio does meanwhile must still see them."""
    from studio2_server import config
    from studio2_server.harness import claude as harness_module
    monkeypatch.setenv("STUDIO2_CLAUDE_CLI", "/bin/echo")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-shell-0123")
    api = {"id": "api", "label": "API", "kind": "anthropic-api", "api_key_env": "ANTHROPIC_API_KEY",
           "models": [{"id": "sonnet", "label": "Sonnet"}], "default_model": "sonnet"}
    starting, release, inherited = asyncio.Event(), asyncio.Event(), []

    class SlowClient(FailingClient):
        async def connect(self):        # the spawn and the handshake take seconds
            inherited.append(os.environ.get("ANTHROPIC_API_KEY"))
            starting.set()
            await release.wait()

        async def disconnect(self):
            pass

    monkeypatch.setattr(harness_module, "ClaudeSDKClient", SlowClient)
    request = TurnRequest("p_1", "t_1", tmp_path, "hi", None, SUB, "opus")
    _env, scrub = child_env(SUB, os.environ)
    turn = asyncio.create_task(ClaudeHarness("claude-subscription")._attempt(request, lambda _e: None, {}, scrub, None))
    await starting.wait()
    try:
        assert inherited == [None]       # the program being started does not inherit it
        # Meanwhile a second project on the API key starts, health is asked, setup lists models.
        assert ClaudeHarness("anthropic-api").describe(api)["available"] is True
        assert child_env(api, config.environment())[0]["ANTHROPIC_API_KEY"] == "sk-from-the-shell-0123"
        assert config.connection_key(api, config.environment()) == "sk-from-the-shell-0123"
    finally:
        release.set()
        await turn
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-the-shell-0123"
    assert config.environment()["ANTHROPIC_API_KEY"] == "sk-from-the-shell-0123"
