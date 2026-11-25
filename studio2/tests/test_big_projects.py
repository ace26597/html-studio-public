"""Big projects: the plan (`set_plan`), the project's notes (`notes/project.md`) and the studio's
limit on requests at once (CONTRACT.md, "Big projects: a plan the person sees, notes that last, and
room for a few requests at once")."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from studio2_server import tools
from studio2_server.harness.base import TurnResult
from studio2_server.studio import NOTES_CAP, plan_lines, project_notes

from conftest import run_turn


async def wait_for(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


def answer(result: dict) -> dict:
    assert not result.get("is_error"), result
    return json.loads(result["content"][0]["text"])


class Held:
    """A harness whose requests run until released, one by one (`let(text)`) or all (`release`)."""
    kind = "fake"

    def __init__(self):
        self.release = asyncio.Event()
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.calls: list[tuple] = []     # (tool, args) to call during the request
        self.prompts: list[str] = []     # each request's message as the agent got it
        self.ask: set[str] = set()       # texts of requests that put an approval card first

    def let(self, text: str) -> None:
        self.gates.setdefault(text, asyncio.Event()).set()

    async def run(self, request, emit):
        self.started.append(request.text)
        self.prompts.append(request.prompt)
        if request.text in self.ask:
            await request.approve("Bash", "Run a command", "echo hi", "a test card")
        for name, args in self.calls:
            await request.studio_tool(name, args)
        gate = self.gates.setdefault(request.text, asyncio.Event())
        done = [asyncio.ensure_future(gate.wait()), asyncio.ensure_future(self.release.wait())]
        try:
            await asyncio.wait(done, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in done:
                task.cancel()
        return TurnResult("completed", request.session_id or "s_" + request.turn_id)

    def describe(self, connection):
        return {"available": True, "reason": None}


def hold(monkeypatch, studio) -> Held:
    held = Held()
    monkeypatch.setattr(studio.registry, "harness", lambda kind: held)
    return held


# -- the plan ---------------------------------------------------------------------------------------
async def test_set_plan_keeps_the_plan_in_the_record_and_writes_one_plan_event(client, studio, project, monkeypatch):
    pid = project["id"]
    held = hold(monkeypatch, studio)
    held.calls = [("set_plan", {"items": [{"text": "Shared style", "status": "done"},
                                          {"text": "  Home   page ", "status": "doing"},
                                          {"text": "Events page", "status": "pending"}]})]
    workspace = studio.store.workspace(pid)
    head = (workspace / ".git" / "HEAD").read_text()
    commits = len(studio.log(pid).read())
    turn_id = (await client.post(f"/api/projects/{pid}/turns", json={"text": "Build the site"})).json()["turn_id"]
    await wait_for(lambda: studio.store.get(pid).get("plan"))
    plan = studio.store.get(pid)["plan"]
    assert plan["items"] == [{"text": "Shared style", "status": "done"}, {"text": "Home page", "status": "doing"},
                             {"text": "Events page", "status": "pending"}]
    assert plan["turn_id"] == turn_id and plan["updated_at"]
    events = [e for e in studio.log(pid).read()[commits:] if e["type"] == "plan"]
    assert len(events) == 1 and events[0]["turn_id"] == turn_id and events[0]["source"] == "set_plan"
    assert events[0]["entries"] == [{"content": "Shared style", "status": "completed"},
                                    {"content": "Home page", "status": "in_progress"},
                                    {"content": "Events page", "status": "pending"}]
    # Not a file of the project: nothing written there, so no reload, no version, no person's change.
    assert not [e for e in studio.log(pid).read()[commits:] if e["type"] == "files_changed"]
    assert not (workspace / "notes").exists() and (workspace / ".git" / "HEAD").read_text() == head
    held.release.set()
    await studio.turns.wait(pid)
    # The record keeps it after the request, and an empty list clears it.
    assert studio.store.get(pid)["plan"]["items"][1]["status"] == "doing"
    assert answer(await tools.call(studio, pid, "set_plan", {"items": []})) == {"saved": 0, "done": 0, "left": 0}
    assert studio.store.get(pid)["plan"] is None


@pytest.mark.parametrize("items, said", [
    ([{"text": f"Step {n}", "status": "pending"} for n in range(31)], "at most 30"),
    ([{"text": "x" * 201, "status": "pending"}], "at most 200"),
    ([{"text": "Step", "status": "started"}], "pending, doing or done"),
    ([{"text": "   ", "status": "done"}], "no text"),
    ("not a list", "must be a list"),
])
async def test_set_plan_refuses_what_is_not_a_plan(studio, project, items, said):
    result = await tools.call(studio, project["id"], "set_plan", {"items": items})
    assert result.get("is_error") and said in result["content"][0]["text"]
    assert studio.store.get(project["id"]).get("plan") is None


async def test_an_unfinished_plan_rides_in_the_current_facts_and_a_done_one_does_not(studio, project):
    pid = project["id"]
    answer(await tools.call(studio, pid, "set_plan", {"items": [
        {"text": "Shared style", "status": "done"}, {"text": "Home page", "status": "doing"},
        {"text": "Events page", "status": "pending"}]}))
    standing, current = studio.project_facts(pid)
    assert "plan for this project" not in standing
    assert ("- The plan for this project (set_plan; set in an earlier request or this one). Keep it current:\n"
            "  - [done] Shared style\n  - [doing] Home page\n  - [ ] Events page") in current
    answer(await tools.call(studio, pid, "set_plan", {"items": [{"text": "Home page", "status": "done"}]}))
    assert "plan for this project" not in "\n".join(studio.project_facts(pid))
    many = {"items": [{"text": f"Step {n}", "status": "done" if n else "pending"} for n in range(30)]}
    assert len(plan_lines(many).splitlines()) == 31


async def test_the_plan_tool_is_registered_and_its_call_is_drawn_as_the_plan_not_a_row(studio, project):
    assert "set_plan" in [name for name, *_ in tools.registered(studio, project["id"])]
    names = [name for name, *_ in tools.TOOLS]
    assert names.index("set_plan") == names.index("ask_person") + 1


def test_the_harness_draws_set_plan_as_the_plan_not_as_a_tool_row(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    from studio2_server.harness.claude import EventMapper
    from test_claude_harness import feed
    mapper = EventMapper(tmp_path)
    events = feed(
        mapper,
        AssistantMessage(content=[ToolUseBlock(id="p1", name="mcp__studio__set_plan", input={"items": []})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="p1", content='{"saved": 0}')]),
        AssistantMessage(content=[ToolUseBlock(id="r1", name="Read", input={"file_path": "index.html"})], model="m"))
    assert [e["type"] for e in events] == ["tool_call"] and events[0]["name"] == "Read"


def test_only_the_main_agent_gets_set_plan():
    """The researcher is refused it by name; every other helper has a `tools:` list without it."""
    folder = Path(__file__).resolve().parents[1] / "library" / "agents"
    seen = 0
    for path in sorted(folder.glob("*.md")):
        head = path.read_text().split("\n---\n", 1)[0]
        fields = dict(line.split(": ", 1) for line in head.splitlines()[1:] if ": " in line)
        if "tools" in fields:
            assert "mcp__studio__set_plan" not in {t.strip() for t in fields["tools"].split(",")}, path.name
        else:
            assert "mcp__studio__set_plan" in {t.strip() for t in fields["disallowedTools"].split(",")}, path.name
        seen += 1
    assert seen >= 4


def test_the_system_prompt_names_set_plan_and_the_notes_in_long_work_only():
    prompt = (Path(__file__).resolve().parents[1] / "library" / "system-prompt.md").read_text()
    long_work = prompt.split("## Long work", 1)[1].split("\n## ", 1)[0]
    assert "mcp__studio__set_plan" in long_work and "notes/project.md" in long_work
    assert prompt.count("set_plan") == 1 and "TodoWrite" not in prompt
    project = prompt.split("## The project", 1)[1].split("\n## ", 1)[0]
    assert "the file tools write nowhere\n  else" in project and "commands may put scratch files" in project
    assert "No other file tracks the work; a page shows progress only if a skill\n  says so." in long_work
    assert len("## Long work" + long_work) <= 543     # no longer than the part it replaced


# -- the project's notes ----------------------------------------------------------------------------
async def test_the_notes_are_standing_facts_capped_and_absent_without_a_file(studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    assert "notes/project.md" not in studio.project_facts(pid)[0]
    (workspace / "notes").mkdir()
    (workspace / "notes" / "project.md").write_text("   \n")
    assert project_notes(workspace) == ""
    (workspace / "notes" / "project.md").write_text("# Garden club site\n\n- 2026-11-20: green and cream, serif headings\n")
    standing, current = studio.project_facts(pid)
    assert ("- The project's notes (notes/project.md; keep them current):\n  # Garden club site\n\n"
            "  - 2026-11-20: green and cream, serif headings") in standing
    assert "project's notes" not in current
    (workspace / "notes" / "project.md").write_text("\n".join(f"- line {n:05d} " + "x" * 80 for n in range(200)))
    told = project_notes(workspace)
    assert "Cut here" in told and "longer than 6,000 characters" in told
    assert len(told) < NOTES_CAP + 600 and "line 00199" not in told


async def test_a_notes_file_that_points_outside_the_project_is_not_read(studio, project, tmp_path):
    workspace = studio.store.workspace(project["id"])
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("not the project's")
    (workspace / "notes").mkdir()
    (workspace / "notes" / "project.md").symlink_to(secret)
    assert project_notes(workspace) == ""


async def test_a_change_to_the_notes_goes_in_full_with_the_next_request(client, studio, project, monkeypatch):
    """The same conversation: unchanged standing facts go as one line; changed notes go in full."""
    pid = project["id"]
    held = hold(monkeypatch, studio)
    held.release.set()
    note = "- 2026-11-20: the person wants three pages, green and cream"
    for text in ("first", "second"):
        assert (await client.post(f"/api/projects/{pid}/turns", json={"text": text})).status_code == 202
        await studio.turns.wait(pid)
    assert note not in held.prompts[0] and studio.FACTS_SAME in held.prompts[1]
    workspace = studio.store.workspace(pid)
    (workspace / "notes").mkdir()
    (workspace / "notes" / "project.md").write_text(note + "\n")
    assert (await client.post(f"/api/projects/{pid}/turns", json={"text": "third"})).status_code == 202
    await studio.turns.wait(pid)
    assert studio.store.get(pid)["session_id"] == studio.store.get(pid)["facts_sent"]["session_id"]
    assert f"  {note}" in held.prompts[2] and studio.FACTS_SAME not in held.prompts[2]
    assert "<studio-project>" in held.prompts[2]
    assert (await client.post(f"/api/projects/{pid}/turns", json={"text": "fourth"})).status_code == 202
    await studio.turns.wait(pid)
    assert note not in held.prompts[3] and studio.FACTS_SAME in held.prompts[3]


async def test_the_notes_are_versioned_and_a_persons_edit_is_reported(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "Hello studio")
    workspace = studio.store.workspace(pid)
    (workspace / "notes").mkdir()
    (workspace / "notes" / "project.md").write_text("- first\n")
    made = await studio.checkpoint(pid, "agent", "notes")
    assert made
    # The person edits the notes in the Files panel (a `user` checkpoint).
    put = await client.put(f"/api/projects/{pid}/file", json={"path": "notes/project.md",
                                                               "content": "- first\n- the person's line\n"})
    assert put.status_code == 200, put.text
    from studio2_server import workspace as ws
    changed = ws.user_changes_since_agent(workspace)
    assert {"path": "notes/project.md", "status": "modified"} in changed
    # Restoring the agent's version restores the notes.
    ws.restore(workspace, made["sha"])
    assert (workspace / "notes" / "project.md").read_text() == "- first\n"


async def test_a_saved_template_does_not_carry_the_projects_notes(client, studio, project):
    workspace = studio.store.workspace(project["id"])
    (workspace / "index.html").write_text("<h1>Garden club</h1>")
    (workspace / "notes").mkdir()
    (workspace / "notes" / "project.md").write_text("- 2026-11-20: decided by the person\n")
    (workspace / "src" / "notes").mkdir(parents=True)
    (workspace / "src" / "notes" / "card.html").write_text("<p>a part of the page</p>")
    saved = answer(await tools.call(studio, project["id"], "save_template", {"name": "Club page"}))
    files = studio.saved.files(saved["id"])
    assert not (files / "notes").exists()
    assert "notes/project.md" not in (files / "TEMPLATE.md").read_text()
    # Only the project's own notes folder: a folder of that name inside the page's code stays.
    assert (files / "src" / "notes" / "card.html").is_file()


# -- room for a few requests at once ----------------------------------------------------------------
async def test_a_request_over_the_limit_is_refused_and_health_says_how_many(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 2)
    held = hold(monkeypatch, studio)
    pids = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(3)]
    for pid in pids[:2]:
        assert (await client.post(f"/api/projects/{pid}/turns", json={"text": "Build it"})).status_code == 202
    assert (await client.get("/api/health")).json()["turns"] == {"running": 2, "limit": 2, "waiting": 0}
    refused = await client.post(f"/api/projects/{pids[2]}/turns", json={"text": "Build it"})
    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["code"] == "studio_busy" and body["details"] == {"running": 2, "limit": 2}
    assert body["message"] == "The studio is already running 2 requests, its limit. Try again when one finishes."
    assert not [e for e in studio.log(pids[2]).read() if e["type"] == "turn_started"]
    # Asking about a skill makes a project for it: refused before it is made.
    before = len(studio.store.list())
    asked = await client.post("/api/library/skills/ask", json={"id": "x", "about": "y"})
    assert asked.status_code == 429 and asked.json()["error"]["code"] == "studio_busy"
    assert len(studio.store.list()) == before
    held.release.set()
    for pid in pids[:2]:
        await studio.turns.wait(pid)
    assert (await client.get("/api/health")).json()["turns"] == {"running": 0, "limit": 2, "waiting": 0}


async def test_no_limit_when_it_is_zero(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 0)
    held = hold(monkeypatch, studio)
    pids = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(5)]
    for pid in pids:
        assert (await client.post(f"/api/projects/{pid}/turns", json={"text": "Build it"})).status_code == 202
    assert (await client.get("/api/health")).json()["turns"] == {"running": 5, "limit": 0, "waiting": 0}
    held.release.set()
    for pid in pids:
        await studio.turns.wait(pid)


async def test_a_queued_message_waits_for_room_and_starts_when_any_request_ends(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 2)
    held = hold(monkeypatch, studio)
    a, b, c = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(3)]
    for pid, text in ((a, "first in a"), (b, "first in b")):
        assert (await client.post(f"/api/projects/{pid}/turns", json={"text": text})).status_code == 202
    # A queued message in an idle project of a full studio waits instead of being refused.
    queued_c = (await client.post(f"/api/projects/{c}/turns", json={"text": "first in c", "queue": True})).json()
    queued_a = (await client.post(f"/api/projects/{a}/turns", json={"text": "next in a", "queue": True})).json()
    assert "queued_id" in queued_c and "queued_id" in queued_a
    assert set(studio.turns.active) == {a, b} and studio.turns.queue[c][0]["text"] == "first in c"
    # b ends: its place goes to c's message, queued first. a's follow-up waits for a.
    held.let("first in b")
    await wait_for(lambda: c in studio.turns.active)
    assert set(studio.turns.active) == {a, c} and studio.turns.queue[a][0]["text"] == "next in a"
    # a ends: its own follow-up takes the place.
    held.let("first in a")
    await wait_for(lambda: "next in a" in held.started)
    assert set(studio.turns.active) == {a, c}
    held.release.set()
    await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()))
    # The first two start side by side, so their order is not fixed; what follows is.
    assert sorted(held.started[:2]) == ["first in a", "first in b"]
    assert held.started[2:] == ["first in c", "next in a"]
    for pid in (a, b, c):
        assert not [e for e in studio.log(pid).read() if e["type"] == "turn_unqueued"]


async def test_the_queue_never_goes_over_the_limit(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 1)
    held = hold(monkeypatch, studio)
    a, b = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(2)]
    assert (await client.post(f"/api/projects/{a}/turns", json={"text": "one"})).status_code == 202
    for text in ("two", "three"):
        assert "queued_id" in (await client.post(f"/api/projects/{b}/turns", json={"text": text, "queue": True})).json()
    assert "queued_id" in (await client.post(f"/api/projects/{a}/turns", json={"text": "four", "queue": True})).json()
    most = 0

    async def watch():
        nonlocal most
        while True:
            most = max(most, len(studio.turns.active))
            await asyncio.sleep(0.002)
    watcher = asyncio.create_task(watch())
    try:
        for text in ("one", "two", "three", "four"):
            await wait_for(lambda: text in held.started)
            held.let(text)
        await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()))
    finally:
        watcher.cancel()
    # Oldest queued first, over both projects: b's two, queued before a's "four".
    assert most == 1 and held.started == ["one", "two", "three", "four"]


def test_the_limit_comes_from_the_environment(monkeypatch, tmp_path):
    from studio2_server.config import Settings
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "none.json"))
    for value, expected in ((None, 4), ("0", 0), ("7", 7), ("-3", 0), ("many", 4)):
        if value is None:
            monkeypatch.delenv("STUDIO2_MAX_TURNS", raising=False)
        else:
            monkeypatch.setenv("STUDIO2_MAX_TURNS", value)
        assert Settings.from_env(data=str(tmp_path)).max_turns == expected, value


async def test_a_project_with_messages_waiting_queues_a_new_one_behind_them(client, studio, monkeypatch):
    """Limit 1: a1 runs, b1 then a2 queue. a1 ends, b1 (older) takes the room, a2 waits in an idle project.
    a3, sent without `queue`, goes behind a2: not refused, and never ahead of it."""
    monkeypatch.setattr(studio.settings, "max_turns", 1)
    held = hold(monkeypatch, studio)
    a, b = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(2)]
    assert (await client.post(f"/api/projects/{a}/turns", json={"text": "a1"})).status_code == 202
    assert "queued_id" in (await client.post(f"/api/projects/{b}/turns", json={"text": "b1", "queue": True})).json()
    assert "queued_id" in (await client.post(f"/api/projects/{a}/turns", json={"text": "a2", "queue": True})).json()
    held.let("a1")
    await wait_for(lambda: "b1" in held.started)
    assert a not in studio.turns.active and studio.turns.queue[a][0]["text"] == "a2"
    sent = await client.post(f"/api/projects/{a}/turns", json={"text": "a3"})
    assert sent.status_code == 202 and "queued_id" in sent.json(), sent.text
    assert [item["text"] for item in studio.turns.queue[a]] == ["a2", "a3"]
    held.release.set()
    await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()))
    assert held.started == ["a1", "b1", "a2", "a3"]


async def test_a_request_waiting_for_the_person_stops_taking_room_after_a_while(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 1)
    monkeypatch.setattr(type(studio.turns), "WAIT_GRACE", 0.3)
    held = hold(monkeypatch, studio)
    held.ask.add("a1")
    a, b, c = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(3)]
    assert (await client.post(f"/api/projects/{a}/turns", json={"text": "a1"})).status_code == 202
    await wait_for(lambda: studio.turns.pending.get(studio.turns.active[a]))
    # Waiting, but not yet for long: it still takes the room.
    assert (await client.post(f"/api/projects/{b}/turns", json={"text": "b1"})).status_code == 429
    assert "queued_id" in (await client.post(f"/api/projects/{c}/turns", json={"text": "c1", "queue": True})).json()
    # After the grace a queued message takes its place by itself, and health says one waits.
    await wait_for(lambda: "c1" in held.started, timeout=5)
    turns = (await client.get("/api/health")).json()["turns"]
    assert turns == {"running": 2, "limit": 1, "waiting": 1}
    # The person answers: it carries on and takes room again, so nothing new starts.
    turn_a = studio.turns.active[a]
    approval = next(iter(studio.turns.pending[turn_a]))
    assert studio.turns.answer(turn_a, approval, "deny")
    await wait_for(lambda: turn_a not in studio.turns.waiting_since)
    assert studio.turns.counted() == 2 and studio.turns.full()
    assert (await client.post(f"/api/projects/{b}/turns", json={"text": "b1"})).status_code == 429
    held.release.set()
    await wait_for(lambda: not studio.turns.active)
    assert studio.turns.waiting_since == {}


async def test_a_model_check_waits_for_room_between_its_messages(studio, monkeypatch):
    """A full studio between the check's messages makes it wait, not fail."""
    from studio2_server.modelcheck import ModelChecks
    monkeypatch.setattr(studio.settings, "max_turns", 1)
    monkeypatch.setattr(ModelChecks, "ROOM_POLL", 0.02)
    held = hold(monkeypatch, studio)
    held.release.set()
    other = (await studio.create_project("Club", "blank"))
    pid = (await studio.create_project("Check", "blank"))["id"]
    checks = ModelChecks(studio)
    checks.running = {"stopping": False}
    # Another request holds the only room when the check's message is due.
    gate = asyncio.Event()
    held.release.clear()
    held.gates["busy"] = gate
    studio.turns.start(other, "busy", None, None, None)
    sent = asyncio.create_task(checks._message(pid, "hello", studio.registry.connections()[0],
                                               studio.registry.connections()[0]["default_model"], []))
    await asyncio.sleep(0.2)
    assert not sent.done() and "hello" not in held.started
    gate.set()
    held.release.set()
    result = await asyncio.wait_for(sent, 5)
    assert result["status"] == "completed" and "hello" in held.started
    # Stopped while it waits: nothing is sent, and it says stopped.
    held.release.clear()
    studio.turns.start(other, "busy again", None, None, None)
    checks.running = {"stopping": True}
    stopped = await asyncio.wait_for(checks._message(pid, "never", studio.registry.connections()[0],
                                                     studio.registry.connections()[0]["default_model"], []), 5)
    assert stopped["status"] == "stopped" and "never" not in held.started
    held.release.set()
    await studio.turns.wait(other["id"])


# -- a shared studio: the limit holds guests back, not the owner ---------------------------------------
OWNER, GUEST = "owner@example.org", "guest@example.org"


@pytest.fixture
async def people(app, settings):
    """A shared studio with its owner and a guest at the door (as the gate in front signs them in)."""
    import httpx

    def through(email):
        async def signed_in(received, receive, send):
            if received["type"] == "http":
                received = {**received, "state": {**(received.get("state") or {}), "gate_email": email}}
            await app(received, receive, send)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=signed_in), base_url="http://127.0.0.1:8200")

    settings.shared, settings.owners = True, (OWNER,)
    made = {"owner": through(OWNER), "guest": through(GUEST)}
    try:
        yield made
    finally:
        for client in made.values():
            await client.aclose()
        settings.shared, settings.owners = False, ()


async def test_on_a_shared_studio_a_guest_never_keeps_the_owner_out(people, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 2)
    held = hold(monkeypatch, studio)
    owner, guest = people["owner"], people["guest"]
    g1, g2, g3, mine, mine2 = [(await guest.post("/api/projects", json={"name": f"Club {n}"})).json()["id"]
                               for n in range(5)]
    for pid in (g1, g2):
        assert (await guest.post(f"/api/projects/{pid}/turns", json={"text": f"guest {pid}"})).status_code == 202
    refused = await guest.post(f"/api/projects/{g3}/turns", json={"text": "guest more"})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "studio_busy"
    # The guest queues messages first; then the owner sends one, without asking to queue: it starts at once.
    for n in range(3):
        assert "queued_id" in (await guest.post(f"/api/projects/{g3}/turns",
                                                json={"text": f"guest queued {n}", "queue": True})).json()
    started = await owner.post(f"/api/projects/{mine}/turns", json={"text": "owner first"})
    assert started.status_code == 202 and "turn_id" in started.json(), started.text
    assert len(studio.turns.active) == 3          # over the limit, and health tells the truth
    assert (await owner.get("/api/health")).json()["turns"]["running"] == 3
    # The owner's message queued behind its own busy project starts before the guest's older ones.
    assert "queued_id" in (await owner.post(f"/api/projects/{mine}/turns", json={"text": "owner next", "queue": True})).json()
    held.let("owner first")
    await wait_for(lambda: "owner next" in held.started)
    assert not any(text.startswith("guest queued") for text in held.started)
    # A guest's message in the owner's project goes after the owner's own messages there.
    assert "queued_id" in (await guest.post(f"/api/projects/{mine}/turns", json={"text": "guest in mine", "queue": True})).json()
    assert "queued_id" in (await owner.post(f"/api/projects/{mine}/turns", json={"text": "owner third", "queue": True})).json()
    assert [item["text"] for item in studio.turns.queue[mine]] == ["owner third", "guest in mine"]
    # When room appears, guests' messages start oldest first and stay guests'.
    held.release.set()
    await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()))
    assert held.started.index("owner third") < held.started.index("guest in mine")
    assert studio.turns.by_owner == set()


async def test_on_a_shared_studio_a_project_holds_at_most_twenty_waiting_messages(people, studio, monkeypatch):
    held = hold(monkeypatch, studio)
    guest = people["guest"]
    pid = (await guest.post("/api/projects", json={"name": "Club"})).json()["id"]
    assert (await guest.post(f"/api/projects/{pid}/turns", json={"text": "first"})).status_code == 202
    for n in range(20):
        assert "queued_id" in (await guest.post(f"/api/projects/{pid}/turns", json={"text": f"q{n}", "queue": True})).json()
    refused = await guest.post(f"/api/projects/{pid}/turns", json={"text": "one too many", "queue": True})
    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["code"] == "queue_full" and body["details"] == {"waiting": 20, "cap": 20}
    assert body["message"] == "This project already has 20 messages waiting. Send more when some have run."
    held.release.set()
    await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()), timeout=20)


async def test_a_studio_that_is_not_shared_has_no_queue_cap_and_no_owner_exemption(client, studio, monkeypatch):
    monkeypatch.setattr(studio.settings, "max_turns", 1)
    held = hold(monkeypatch, studio)
    a, b = [(await client.post("/api/projects", json={"name": f"Club {n}"})).json()["id"] for n in range(2)]
    assert (await client.post(f"/api/projects/{a}/turns", json={"text": "first"})).status_code == 202
    assert (await client.post(f"/api/projects/{b}/turns", json={"text": "second"})).status_code == 429
    for n in range(25):
        assert "queued_id" in (await client.post(f"/api/projects/{a}/turns", json={"text": f"q{n}", "queue": True})).json()
    held.release.set()
    await wait_for(lambda: not studio.turns.active and not any(studio.turns.queue.values()), timeout=20)


async def test_a_queued_message_whose_sender_is_unknown_runs_as_a_guests(people, studio, monkeypatch):
    held = hold(monkeypatch, studio)
    pid = (await people["owner"].post("/api/projects", json={"name": "Club"})).json()["id"]
    assert (await people["owner"].post(f"/api/projects/{pid}/turns", json={"text": "first"})).status_code == 202
    queued = studio.turns.enqueue(studio.store.get(pid), "who sent this", None, None, None)   # no owner given
    assert [item["owner"] for item in studio.turns.queue[pid] if item["queued_id"] == queued] == [False]
    held.let("first")
    await wait_for(lambda: "who sent this" in held.started)
    turn = studio.turns.active.get(pid)
    assert turn and turn not in studio.turns.by_owner
    held.release.set()
    await studio.turns.wait(pid)


def test_the_notes_are_read_only_up_to_64_kb(tmp_path, monkeypatch):
    from studio2_server import studio as studio_module
    (tmp_path / "notes").mkdir()
    big = tmp_path / "notes" / "project.md"
    big.write_bytes((b"- a line of the project's notes\n" * 400_000))     # about 13 MB
    asked = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self == big:
            read = handle.read
            handle.read = lambda size=-1: (asked.append(size), read(size))[1]
        return handle
    monkeypatch.setattr(Path, "open", spy)
    told = studio_module.project_notes(tmp_path)
    assert asked == [64 * 1024] and "Cut here" in told and len(told) < NOTES_CAP + 600
