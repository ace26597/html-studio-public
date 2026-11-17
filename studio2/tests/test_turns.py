import asyncio

from conftest import run_turn

from studio2_server import workspace as ws
from studio2_server.harness import TurnResult
from studio2_server.studio import RESTART_ERROR, Studio
from studio2_server.turns import cap_input, compose_prompt


async def wait_for(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")


async def test_fake_turn_produces_the_contract_event_sequence(client, studio, project):
    pid = project["id"]
    events = await run_turn(client, studio, pid, "Hello studio")
    types = [e["type"] for e in events]

    assert types[0] == "turn_started" and types[-1] == "turn_finished"
    assert types.count("turn_finished") == 1 and types.count("checkpoint") == 1
    # The agent checkpoint precedes the end; only the suggested next steps come between them.
    assert types[-3:] == ["checkpoint", "next_steps", "turn_finished"]
    for expected in ("plan", "thought_chunk", "tool_call", "tool_result", "message_chunk"):
        assert expected in types
    assert events[0]["text"] == "Hello studio" and events[0]["connection"] == "fake"
    assert events[0]["selection"] is None and events[0]["model"] == "fake"

    everything = studio.log(pid).read()
    assert [e["seq"] for e in everything] == list(range(1, len(everything) + 1))
    assert all(e["ts"].endswith("Z") for e in everything)

    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls[0]["kind"] == "read" and calls[0]["title"] == "Read index.html"
    assert {c["kind"] for c in calls[1:]} == {"edit"} and all(c["parent_tool_id"] is None for c in calls)
    results = {e["tool_id"]: e["status"] for e in events if e["type"] == "tool_result"}
    assert set(results) == {c["tool_id"] for c in calls} and set(results.values()) == {"ok"}
    plan = [e for e in events if e["type"] == "plan"][-1]
    assert {entry["status"] for entry in plan["entries"]} == {"completed"}

    checkpoint = events[-3]
    assert checkpoint["kind"] == "agent" and checkpoint["message"] == "Hello studio"
    assert checkpoint["files"] == [{"path": "index.html", "status": "modified"}]
    finished = events[-1]
    assert finished["status"] == "completed" and finished["error"] is None
    assert set(finished["usage"]) == {"input_tokens", "output_tokens", "cost_usd", "duration_ms", "cost_estimated",
                                      "session_cost_usd", "cost_basis"}   # the conversation's running total; where the figure is from
    assert finished["session_id"] == f"fake_{pid}"

    page = (studio.store.workspace(pid) / "index.html").read_text()
    assert "<h1>Hello studio</h1>" in page and "You asked: “Hello studio”" in page
    record = (await client.get(f"/api/projects/{pid}")).json()
    assert record["running_turn"] is None and record["session_id"] == f"fake_{pid}"
    assert record["head"] == checkpoint["sha"]
    versions = (await client.get(f"/api/projects/{pid}/versions")).json()
    assert [v["kind"] for v in versions] == ["agent", "init"]


async def test_dashboard_and_selection_turns(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "Make a dashboard please")
    page = studio.store.workspace(pid) / "index.html"
    assert "<svg" in page.read_text() and "Placeholder demo content" in page.read_text()

    before = page.read_text()
    selection = {"path": "/", "selector": "main > h1", "tag": "h1", "text": "Demo dashboard",
                 "html": "<h1>Demo dashboard</h1>"}
    events = await run_turn(client, studio, pid, 'Rename this to "Sales overview"', selection=selection)
    assert events[0]["selection"] == selection
    after = page.read_text()
    assert "<h1>Sales overview</h1>" in after
    assert after == before.replace("<h1>Demo dashboard</h1>", "<h1>Sales overview</h1>")  # nothing else moved


async def test_second_turn_is_refused_and_stop_ends_a_slow_turn(client, studio, project, monkeypatch):
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0.05")
    pid = project["id"]
    base = f"/api/projects/{pid}"
    turn_id = (await client.post(f"{base}/turns", json={"text": "go slow"})).json()["turn_id"]
    assert (await client.get(base)).json()["running_turn"] == turn_id

    busy = await client.post(f"{base}/turns", json={"text": "another"})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_running"
    for refused in (await client.put(f"{base}/file", json={"path": "a.txt", "content": "x"}),
                    await client.post(f"{base}/dom-edits", json={"path": "/", "before_text": "a", "after_text": "b"}),
                    await client.post(f"{base}/restore", json={"sha": project["head"]})):
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "turn_running"

    log = studio.log(pid)
    await wait_for(lambda: sum(e["type"] == "tool_call" and e["kind"] == "edit" for e in log.read()) >= 2)
    assert (await client.post(f"{base}/turns/{turn_id}/stop")).status_code == 202
    assert (await client.post(f"{base}/turns/{turn_id}/stop")).status_code == 202   # idempotent
    await studio.turns.wait(pid)

    events = [e for e in log.read() if e["turn_id"] == turn_id and e["type"] != "files_changed"]
    finished = [e for e in events if e["type"] == "turn_finished"]
    assert len(finished) == 1 and events[-1] is finished[0]
    assert finished[0]["status"] == "stopped" and finished[0]["error"] is None
    assert sum(e["type"] == "tool_call" and e["kind"] == "edit" for e in events) < 20
    assert events[-2]["type"] == "checkpoint" and events[-2]["kind"] == "agent"   # partial work is kept
    assert (await client.get(base)).json()["running_turn"] is None
    assert (await client.post(f"{base}/turns/{turn_id}/stop")).status_code == 202   # after the end, still fine
    assert (await client.post(f"{base}/turns", json={"text": "next"})).status_code == 202
    await studio.turns.wait(pid)


async def test_failing_turn_ends_failed_with_a_readable_error(client, studio, project):
    events = await run_turn(client, studio, project["id"], "please fail now")
    types = [e["type"] for e in events]
    assert types.count("turn_finished") == 1 and "checkpoint" not in types   # nothing changed on disk
    assert events[-1]["status"] == "failed" and "asked to fail" in events[-1]["error"]
    assert studio.store.get(project["id"])["running_turn"] is None


async def test_agent_is_told_about_user_changes(client, studio, project):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    seen = []

    class Recorder:
        kind = "fake"

        async def run(self, request, emit):
            seen.append(request)
            emit({"type": "session", "session_id": "sess-1"})
            emit({"type": "tool_call", "tool_id": "t1", "name": "Write", "kind": "edit", "title": "Write big",
                  "input": {"file_path": "big.txt", "content": "x" * 50_000}, "parent_tool_id": None})
            emit({"type": "tool_result", "tool_id": "t1", "status": "ok", "summary": "y" * 10_000})
            return TurnResult("completed")

    studio.registry._harnesses["fake"] = Recorder()
    events = await run_turn(client, studio, pid, "first")
    assert "<studio-user-changes>" not in seen[0].prompt and seen[0].prompt.startswith("first\n\n<studio-project>")
    assert "look_at_page" in seen[0].prompt or "cannot see the rendered page" in seen[0].prompt
    assert seen[0].session_id is None and seen[0].workspace == studio.store.workspace(pid)
    assert "session" not in [e["type"] for e in events]                  # internal, never logged
    assert events[-1]["session_id"] == "sess-1"
    call = next(e for e in events if e["type"] == "tool_call")
    assert len(call["input"]["content"]) < 1_100 and call["input"]["file_path"] == "big.txt"   # 8 KB cap
    assert len(next(e for e in events if e["type"] == "tool_result")["summary"]) <= 4_000       # 4 KB cap

    await client.put(f"{base}/file", json={"path": "notes.md", "content": "# mine"})
    await client.post(f"{base}/upload", files={"file": ("data.csv", b"a\n1\n")})
    selection = {"path": "/", "selector": "main", "tag": "main", "text": "", "html": "<main></main>"}
    await run_turn(client, studio, pid, "second", selection=selection)
    prompt = seen[1].prompt
    assert prompt.startswith("second") and seen[1].session_id == "sess-1"
    assert "<studio-user-changes>" in prompt and "- notes.md (added)" in prompt and "- data.csv (added)" in prompt
    assert "<studio-selection>" in prompt and "CSS selector: main" in prompt and "file: index.html" in prompt


def test_prompt_and_input_cap_helpers(tmp_path):
    assert compose_prompt("  hi ", None, [], tmp_path) == "hi"
    assert cap_input({"a": 1}) == {"a": 1}
    assert "truncated" in cap_input({"k" + str(i): "v" * 900 for i in range(40)})


async def test_orphaned_turn_is_closed_on_startup(studio, project, settings):
    pid = project["id"]
    studio.log(pid).append("turn_started", "t_dead0001", text="x", selection=None, connection="fake", model=None)
    studio.store.update(pid, running_turn="t_dead0001")
    (studio.store.workspace(pid) / "half.txt").write_text("partial work")
    await studio.stop()   # the old process "dies"

    reborn = Studio(settings)
    await reborn.start()
    try:
        events = reborn.log(pid).read()
        tail = [e for e in events if e["turn_id"] == "t_dead0001"]
        assert [e["type"] for e in tail] == ["turn_started", "checkpoint", "turn_finished"]
        assert tail[-1]["status"] == "failed" and tail[-1]["error"] == RESTART_ERROR
        assert tail[1]["kind"] == "agent" and tail[1]["files"] == [{"path": "half.txt", "status": "added"}]
        assert reborn.store.get(pid)["running_turn"] is None
        assert ws.git(reborn.store.workspace(pid), "status", "--porcelain") == ""
        await reborn.stop()
        again = Studio(settings)   # a second restart adds nothing
        await again.start()
        assert len(again.log(pid).read()) == len(events)
        await again.stop()
    finally:
        await reborn.stop()


async def test_an_untitled_project_is_offered_a_name_and_never_renamed_without_a_tap(client, studio):
    from studio2_server.turns import suggested_name

    async def record(pid):
        return (await client.get(f"/api/projects/{pid}")).json()

    created = (await client.post("/api/projects", json={})).json()
    assert created["name"] == "Untitled" and created["name_suggestion"] is None
    await run_turn(client, studio, created["id"], "Do as much research as you can regarding the new game. Then more.")
    offered = await record(created["id"])
    assert offered["name"] == "Untitled" and "name_offered" not in offered       # nothing renamed
    suggestion = offered["name_suggestion"]
    assert suggestion and suggestion != "Untitled" and len(suggestion) <= 80
    await run_turn(client, studio, created["id"], "Now make the heading green")
    assert (await record(created["id"]))["name_suggestion"] == suggestion          # offered once, kept
    # One tap: the ordinary rename with the offered name; the offer is over.
    taken = (await client.patch(f"/api/projects/{created['id']}", json={"name": suggestion})).json()
    assert taken["name"] == suggestion and taken["name_suggestion"] is None

    # Dismissed: still "Untitled", and later turns do not offer again.
    ignored = (await client.post("/api/projects", json={})).json()
    await run_turn(client, studio, ignored["id"], "Build a page about tides")
    assert (await record(ignored["id"]))["name_suggestion"]
    gone = (await client.patch(f"/api/projects/{ignored['id']}", json={"name_suggestion": None})).json()
    assert gone["name"] == "Untitled" and gone["name_suggestion"] is None
    await run_turn(client, studio, ignored["id"], "Add a chart of the spring tides")
    again = await record(ignored["id"])
    assert again["name"] == "Untitled" and again["name_suggestion"] is None
    refused = await client.patch(f"/api/projects/{ignored['id']}", json={"name_suggestion": "Sneaky"})
    assert refused.status_code == 400                                              # only null: no back door

    # Typing a name by hand closes a pending offer; a project the person named is never offered one.
    typed = (await client.post("/api/projects", json={})).json()
    await run_turn(client, studio, typed["id"], "Build a page about kites")
    by_hand = (await client.patch(f"/api/projects/{typed['id']}", json={"name": "Kites"})).json()
    assert by_hand["name"] == "Kites" and by_hand["name_suggestion"] is None
    kept = (await client.post("/api/projects", json={"name": "My own name"})).json()
    await run_turn(client, studio, kept["id"], "Build a page about tides")
    after = await record(kept["id"])
    assert after["name"] == "My own name" and after["name_suggestion"] is None

    # The page's own title wins; a page still titled "Untitled" falls back to the request's first words.
    workspace = studio.store.workspace(created["id"])
    (workspace / "index.html").write_text("<title>GTA VI &amp; Leonida — Field Guide</title>", "utf-8")
    assert suggested_name("anything", workspace) == "GTA VI & Leonida — Field Guide"
    (workspace / "index.html").write_text("<title>Untitled</title>", "utf-8")
    assert suggested_name("Do as much research as you can regarding GTA 6 game. It's leaked things", workspace) \
        == "Do as much research as you can regarding…"
    assert suggested_name("Sales dashboard.", workspace) == "Sales dashboard" and suggested_name("  ", workspace) is None


async def test_look_at_page_takes_a_file_name_and_search_says_what_to_do_when_refused(studio, project):
    import inspect

    from studio2_server import search, tools

    async def look(url, *_a, **_k):
        return {"url": url}
    studio.looker.look = look
    seen = await tools.t_look_at_page(studio, project["id"], {"path": "index.html", "width": 390})
    assert seen["url"].endswith("/index.html") and "127.0.0.1" in seen["url"]
    refused = await tools.call(studio, project["id"], "look_at_page", {"path": "https://example.com/"})
    assert refused.get("is_error")
    assert "WebSearch" in inspect.getsource(search.search)
    assert "unavailable" in {name: text for name, text, *_rest in tools.TOOLS}["search_web"]


def _hold_sync_app(studio, pid, monkeypatch):
    """Hold the finishing phase (after the agent's part) open at the app restart → (reached, release)."""
    reached, release = asyncio.Event(), asyncio.Event()
    real = studio.sync_app

    async def held(project_id, restart=False):
        if project_id == pid and not release.is_set():
            reached.set()
            await release.wait()
        await real(project_id, restart)
    monkeypatch.setattr(studio, "sync_app", held)
    return reached, release


async def test_stop_while_a_request_is_finishing_stops_what_would_follow(client, studio, project, monkeypatch):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    reached, release = _hold_sync_app(studio, pid, monkeypatch)
    looked, shipped = [], []

    async def problems(project_id):
        looked.append(studio.turns.active.get(project_id))
        return None
    monkeypatch.setattr(studio.turns, "_problems", problems)

    async def ship(*args, **kwargs):
        shipped.append(args)
    monkeypatch.setattr(studio.ships, "start", ship)

    turn_id = (await client.post(f"{base}/turns", json={"text": "Hello studio"})).json()["turn_id"]
    await asyncio.wait_for(reached.wait(), 5)       # the agent is done; the studio is still finishing
    queued = (await client.post(f"{base}/turns", json={"text": "next", "queue": True})).json()["queued_id"]
    studio.ships.from_agent[pid] = turn_id          # the agent asked to make it live during the request
    try:
        assert (await client.post(f"{base}/turns/{turn_id}/stop")).status_code == 202
        # "Stop, do this instead": a message sent after the Stop is the person's to keep.
        after = (await client.post(f"{base}/turns", json={"text": "instead", "queue": True})).json()["queued_id"]
    finally:
        release.set()
    await studio.turns.wait(pid)
    await wait_for(lambda: pid not in studio.turns.active)

    events = [e for e in studio.log(pid).read() if e["type"] != "files_changed"]
    mine = [e for e in events if e["turn_id"] == turn_id]
    assert mine[-1]["type"] == "turn_finished" and mine[-1]["status"] == "stopped" and mine[-1]["error"] is None
    assert any(e["type"] == "checkpoint" and e["kind"] == "agent" for e in mine)    # what it did is kept
    assert not any(e["type"] == "next_steps" for e in mine)
    notice = [e["message"] for e in mine if e["type"] == "notice"]
    assert len(notice) == 1 and "already finished" in notice[0] and "saved as a version" in notice[0]
    started = [e for e in events if e["type"] == "turn_started"]
    assert [(e["text"], e["queued_id"]) for e in started] == [("Hello studio", None), ("instead", after)]
    assert [(e["queued_id"], e["reason"]) for e in events if e["type"] == "turn_unqueued"] == [(queued, "cancelled")]
    assert studio.turns.queue.get(pid) == [] and pid not in studio.turns.active
    assert turn_id not in looked and shipped == [] and pid not in studio.ships.from_agent
    assert turn_id not in studio.turns.stopped_with
    assert (await client.get(base)).json()["running_turn"] is None


async def test_a_failed_request_stopped_while_finishing_stays_failed(client, studio, project, monkeypatch):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    reached, release = _hold_sync_app(studio, pid, monkeypatch)
    finished = []
    real_finish = studio.picker.finish
    monkeypatch.setattr(studio.picker, "finish", lambda p, status: (finished.append(status), real_finish(p, status)))
    turn_id = (await client.post(f"{base}/turns", json={"text": "please fail now"})).json()["turn_id"]
    await asyncio.wait_for(reached.wait(), 5)
    queued = (await client.post(f"{base}/turns", json={"text": "next", "queue": True})).json()["queued_id"]
    try:
        assert (await client.post(f"{base}/turns/{turn_id}/stop")).status_code == 202
    finally:
        release.set()
    await studio.turns.wait(pid)
    mine = [e for e in studio.log(pid).read() if e["turn_id"] == turn_id]
    assert mine[-1]["status"] == "failed" and "asked to fail" in mine[-1]["error"]
    assert not any(e["type"] == "notice" and "already finished" in e["message"] for e in mine)
    assert finished == ["failed"]
    # The Stop still clears what was queued before it.
    unqueued = [e["queued_id"] for e in studio.log(pid).read() if e["type"] == "turn_unqueued"]
    assert unqueued == [queued] and pid not in studio.turns.active


async def test_a_late_stop_says_what_is_true_when_nothing_was_saved(client, studio, project, monkeypatch):
    pid = project["id"]
    finished = []
    real_finish = studio.picker.finish
    monkeypatch.setattr(studio.picker, "finish", lambda p, status: (finished.append(status), real_finish(p, status)))
    reached, release = _hold_sync_app(studio, pid, monkeypatch)

    async def nothing(*_a, **_k):
        return False
    monkeypatch.setattr(studio.turns, "_checkpoint", nothing)      # the request changed no files
    turn_id = studio.turns.start(studio.store.get(pid), "Hello studio", None, None, None)
    await asyncio.wait_for(reached.wait(), 5)
    try:
        studio.turns.stop(pid, turn_id)
    finally:
        release.set()
    await studio.turns.wait(pid)
    mine = [e for e in studio.log(pid).read() if e["turn_id"] == turn_id]
    notice = [e["message"] for e in mine if e["type"] == "notice"]
    assert mine[-1]["status"] == "stopped" and len(notice) == 1
    assert "saved as a version" not in notice[0] and "changed no files" in notice[0]
    assert finished == ["stopped"]


async def test_a_message_sent_after_an_ordinary_stop_still_runs(client, studio, project, monkeypatch):
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0.05")
    pid = project["id"]
    base = f"/api/projects/{pid}"
    turn_id = (await client.post(f"{base}/turns", json={"text": "go slow"})).json()["turn_id"]
    before = (await client.post(f"{base}/turns", json={"text": "queued before", "queue": True})).json()["queued_id"]
    await wait_for(lambda: any(e["type"] == "tool_call" for e in studio.log(pid).read()))
    studio.turns.stop(pid, turn_id)
    after = (await client.post(f"{base}/turns", json={"text": "queued after", "queue": True})).json()["queued_id"]
    await studio.turns.wait(pid)
    await wait_for(lambda: len([e for e in studio.log(pid).read() if e["type"] == "turn_finished"]) == 2)
    events = studio.log(pid).read()
    assert [e["queued_id"] for e in events if e["type"] == "turn_unqueued"] == [before]
    assert [e["queued_id"] for e in events if e["type"] == "turn_started"] == [None, after]



async def test_a_stopped_request_gets_no_recheck(client, studio, project, monkeypatch):
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0.05")
    pid = project["id"]
    turn_id = (await client.post(f"/api/projects/{pid}/turns", json={"text": "go slow"})).json()["turn_id"]
    studio.turns.verified.add(turn_id)      # as if the agent had reported a verification
    await wait_for(lambda: any(e["type"] == "tool_call" for e in studio.log(pid).read()))
    studio.turns.stop(pid, turn_id)
    await studio.turns.wait(pid)
    mine = [e for e in studio.log(pid).read() if e["turn_id"] == turn_id]
    assert mine[-1]["status"] == "stopped" and not any(e["type"] == "verification_recheck" for e in mine)


async def test_deleting_a_project_while_a_request_is_finishing_starts_nothing(client, studio, project, monkeypatch):
    pid = project["id"]
    base = f"/api/projects/{pid}"
    reached, release = _hold_sync_app(studio, pid, monkeypatch)
    turn_id = (await client.post(f"{base}/turns", json={"text": "Hello studio"})).json()["turn_id"]
    await asyncio.wait_for(reached.wait(), 5)
    assert "queued_id" in (await client.post(f"{base}/turns", json={"text": "next", "queue": True})).json()

    deleting = asyncio.create_task(client.delete(base))
    try:
        await wait_for(lambda: pid in studio.deleting)
        # From the moment delete begins, nothing new is accepted for the project.
        refused = await client.post(f"{base}/turns", json={"text": "more", "queue": True})
        assert refused.status_code == 404
        assert studio.turns.queue.get(pid) == []
    finally:
        release.set()
    assert (await deleting).status_code == 204
    await asyncio.sleep(0.05)
    assert pid not in studio.turns.active and pid not in studio.turns.tasks and pid not in studio.deleting
    assert turn_id not in studio.turns.cancellable


async def test_a_request_whose_end_raises_still_releases_the_project(studio, project, monkeypatch):
    pid = project["id"]

    calls = []

    def broken(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the disk is full")
    turn_id = studio.turns.start(studio.store.get(pid), "Hello studio", None, None, None)
    queued = studio.turns.enqueue(studio.store.get(pid), "next", None, None, None)
    monkeypatch.setattr(studio.turns, "_close_approvals", broken)   # something in the ending raises
    await studio.turns.wait(pid)
    assert turn_id not in studio.turns.cancellable and turn_id not in studio.turns.harness_tasks
    # It still ends, with what went wrong, and the queue goes on: nothing waits for ever.
    mine = [e for e in studio.log(pid).read() if e["turn_id"] == turn_id and e["type"] == "turn_finished"]
    assert len(mine) == 1 and mine[0]["status"] == "failed" and "the disk is full" in mine[0]["error"]
    started = [e for e in studio.log(pid).read() if e["type"] == "turn_started"]
    assert [e["queued_id"] for e in started] == [None, queued]
    await studio.turns.wait(pid)
    assert pid not in studio.turns.active and pid not in studio.turns.tasks
    assert studio.store.get(pid)["running_turn"] is None


async def test_nothing_starts_by_itself_for_a_project_being_deleted(studio, project, monkeypatch):
    import pytest

    from studio2_server.store import ProjectNotFound
    pid = project["id"]
    synced = []

    class App:
        async def sync(self, restart=False):
            synced.append(restart)
    monkeypatch.setattr(studio, "app", lambda _pid: App())
    studio.deleting.add(pid)
    try:
        await studio.sync_app(pid, restart=True)    # e.g. spawned by a studio.json change
        assert synced == []
        with pytest.raises(ProjectNotFound):
            await studio.ships.start(pid, "agent")
        with pytest.raises(ProjectNotFound):
            studio.turns.start(studio.store.get(pid), "hi", None, None, None)
        with pytest.raises(ProjectNotFound):
            studio.turns.enqueue(studio.store.get(pid), "hi", None, None, None)
    finally:
        studio.deleting.discard(pid)
    await studio.sync_app(pid, restart=True)
    assert synced == [True]
