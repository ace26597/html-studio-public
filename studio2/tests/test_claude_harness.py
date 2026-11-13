"""The Claude harness without Claude: SDK message objects are built by hand, no process is spawned."""

import asyncio
import json

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
                             "cost_estimated": True}
    assert mapper.activity == 7


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
