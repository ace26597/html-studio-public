"""Suggested next steps (CONTRACT.md, "Suggested next steps"): the agent's tool, the studio's own
rules, the one `next_steps` event before `turn_finished`, and the cases that write none."""

from pathlib import Path

import pytest

from conftest import run_turn

from studio2_server import effort, nextsteps, tools

CHECK_OPTIONS = {"depth": "standard", "verify": True, "check_only": True}
FACTS = {"changed": False, "has_page": True, "verify": False, "reported": False, "failed": 0, "still_wrong": 0}


def steps_of(events: list[dict]) -> list[dict]:
    found = [e for e in events if e["type"] == "next_steps"]
    assert len(found) <= 1, "never more than one next_steps per request"
    return found[0]["steps"] if found else []


# -- the agent's part -------------------------------------------------------------------------
def test_the_agents_steps_are_folded_cut_and_capped():
    kept, cut = nextsteps.agent_steps({"steps": [
        {"label": "  Add   a section\non battery life ", "text": " Add a section on\n battery life. "},
        {"label": "add a section on battery life", "text": "The same label again."},      # repeat, dropped
        {"label": "", "text": "No label."}, {"label": "No text", "text": "  "}, "not a step",
        {"label": "Compare " + "very " * 20 + "long", "text": "word " * 200},
        {"label": "Third", "text": "Third."}, {"label": "Fourth", "text": "Fourth."}]})
    assert [s["label"] for s in kept][0] == "Add a section on battery life"
    assert kept[0]["text"] == "Add a section on battery life."
    assert len(kept) == 3 and cut == 1 and kept[2]["label"] == "Third"
    assert len(kept[1]["label"]) <= nextsteps.LABEL_CAP and kept[1]["label"].endswith("…")
    assert len(kept[1]["text"]) <= nextsteps.TEXT_CAP
    assert not kept[1]["label"].rstrip("…").endswith(("ver", "ve", "v"))                  # cut at a word
    assert nextsteps.agent_steps({"steps": []}) == ([], 0)
    for bad in ({"steps": "Add a chart"}, {}, {"steps": None}):
        with pytest.raises(nextsteps.StepsError):
            nextsteps.agent_steps(bad)


# -- the studio's part ------------------------------------------------------------------------
def test_the_studios_rules():
    ids = lambda **facts: [s["id"] for s in nextsteps.studio_steps({**FACTS, **facts})]  # noqa: E731
    assert ids() == []                                              # nothing changed, nothing asked
    assert ids(changed=True) == ["check"]
    assert ids(changed=True, has_page=False) == []
    assert ids(verify=True) == ["check"]                            # asked for and not reported
    assert ids(changed=True, verify=True, reported=True) == []      # a clean reported check
    assert ids(reported=True, failed=2) == ["failed"]
    assert ids(reported=True, failed=2, still_wrong=3) == ["fix"]   # the studio's own finding comes first
    check = nextsteps.studio_steps({**FACTS, "changed": True})[0]
    assert check == {"id": "check", "from": "studio", "label": "Check this page",
                     "text": effort.VERIFY_ONLY_TEXT, "options": CHECK_OPTIONS}
    fix = nextsteps.studio_steps({**FACTS, "reported": True, "still_wrong": 3})[0]
    assert "3 items" in fix["text"] and "options" not in fix
    one = nextsteps.studio_steps({**FACTS, "reported": True, "still_wrong": 1})[0]
    assert "1 item " in one["text"]


def test_compose_puts_the_studios_step_first_and_keeps_four():
    agent = [{"label": f"Add part {n}", "text": f"Add part {n}."} for n in (1, 2, 3)]
    steps = nextsteps.compose({**FACTS, "changed": True}, [{"label": "check THIS page", "text": "x"}, *agent])
    assert [s["id"] for s in steps] == ["check", "a1", "a2", "a3"]
    assert [s["from"] for s in steps] == ["studio", "agent", "agent", "agent"]
    assert steps[1] == {"id": "a1", "from": "agent", "label": "Add part 1", "text": "Add part 1."}
    assert nextsteps.compose(FACTS, []) == []
    assert [s["id"] for s in nextsteps.compose(FACTS, agent)] == ["a1", "a2", "a3"]


# -- the event --------------------------------------------------------------------------------
async def test_a_request_that_changed_the_page_offers_the_check(client, studio, project):
    events = await run_turn(client, studio, project["id"], "Make a page")
    kinds = [e["type"] for e in events]
    assert kinds.index("checkpoint") < kinds.index("next_steps") < kinds.index("turn_finished")
    assert steps_of(events) == [{"id": "check", "from": "studio", "label": "Check this page",
                                 "text": effort.VERIFY_ONLY_TEXT, "options": CHECK_OPTIONS}]


async def test_the_agents_steps_follow_the_studios(client, studio, project):
    events = await run_turn(client, studio, project["id"], "Make a page and suggest next steps")
    steps = steps_of(events)
    assert [(s["id"], s["from"]) for s in steps] == [("check", "studio"), ("a1", "agent"), ("a2", "agent")]
    assert all(s["label"].startswith("(fake)") for s in steps[1:])   # the scripted agent's own are labelled
    assert not [e for e in events if e["type"] == "tool_result" and e["status"] == "error"]


async def test_a_request_that_changed_nothing_offers_nothing(client, studio, project):
    events = await run_turn(client, studio, project["id"], "use skill charts")
    assert "next_steps" not in [e["type"] for e in events]


async def test_a_reported_check_is_not_offered_again(client, studio, project):
    events = await run_turn(client, studio, project["id"], "Make a page", options={"verify": True})
    assert [e for e in events if e["type"] == "verification"]
    assert "check" not in [s["id"] for s in steps_of(events)]
    events = await run_turn(client, studio, project["id"], effort.VERIFY_ONLY_TEXT, options={"check_only": True})
    assert "check" not in [s["id"] for s in steps_of(events)]


async def test_a_check_that_was_not_reported_is_offered(client, studio, project):
    events = await run_turn(client, studio, project["id"], "use skill charts, skip the report",
                            options={"verify": True})
    assert [e for e in events if e["type"] == "notice" and e.get("message") == effort.NOT_REPORTED]
    assert [s["id"] for s in steps_of(events)] == ["check"]


async def test_what_the_studios_own_check_still_finds_is_offered_first(client, studio, project):
    pid = project["id"]
    workspace: Path = studio.store.workspace(pid)
    (workspace / "sources").mkdir(exist_ok=True)
    (workspace / "index.html").write_text(      # cites s9, which the page's source list does not hold
        '<main><p>Up <span data-src="s9" data-quote="up twelve percent">12%</span>.</p>'
        '<ol><li id="src-s1">one</li></ol></main>')
    events = await run_turn(client, studio, pid, effort.VERIFY_ONLY_TEXT, options={"check_only": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert recheck["problems_total"] >= 1
    steps = steps_of(events)
    assert steps[0]["id"] == "fix" and str(recheck["problems_total"]) in steps[0]["text"]
    kinds = [e["type"] for e in events]
    assert kinds.index("verification_recheck") < kinds.index("next_steps")


async def test_failed_and_stopped_requests_offer_nothing(client, studio, project, monkeypatch):
    pid = project["id"]
    events = await run_turn(client, studio, pid, "Please fail and suggest next steps")
    assert events[-1]["status"] == "failed" and "next_steps" not in [e["type"] for e in events]
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0.05")
    response = await client.post(f"/api/projects/{pid}/turns", json={"text": "slow"})
    turn_id = response.json()["turn_id"]
    await client.post(f"/api/projects/{pid}/turns/{turn_id}/stop")
    await studio.turns.wait(pid)
    events = [e for e in studio.log(pid).read() if e["turn_id"] == turn_id]
    assert events[-1]["status"] == "stopped" and "next_steps" not in [e["type"] for e in events]


# -- the tool ---------------------------------------------------------------------------------
async def test_the_tool_keeps_the_last_call_and_refuses_bad_input(studio, project):
    pid = project["id"]
    studio.turns.active[pid] = "t_test"
    try:
        first = await tools.call(studio, pid, "suggest_next_steps",
                                 {"steps": [{"label": "One", "text": "Do one."}]})
        assert "is_error" not in first and '"kept": 1' in first["content"][0]["text"]
        await tools.call(studio, pid, "suggest_next_steps", {"steps": [{"label": "Two", "text": "Do two."}]})
        assert [s["label"] for s in studio.turns.proposed["t_test"]] == ["Two"]
        bad = await tools.call(studio, pid, "suggest_next_steps", {"steps": "Add a chart"})
        assert bad.get("is_error") and "list" in bad["content"][0]["text"]
        assert [s["label"] for s in studio.turns.proposed["t_test"]] == ["Two"]     # a refused call changes nothing
    finally:
        studio.turns.active.pop(pid, None)
        studio.turns.proposed.pop("t_test", None)
    assert not [e for e in studio.log(pid).read() if e["type"] == "next_steps"]     # the tool writes no event
    assert any(t["name"] == "suggest_next_steps" for t in tools.describe(studio))


async def test_nothing_is_left_behind_after_a_request(client, studio, project):
    await run_turn(client, studio, project["id"], "Make a page and suggest next steps")
    assert studio.turns.proposed == {} and studio.turns.reports == {}


def test_the_agent_is_told_how_to_suggest(studio, project):
    """With every message, in the project facts: the agent program loads studio tools on demand, so
    a rule kept only in the tool's description is never read (two real requests showed it)."""
    context = studio.agent_context(project["id"])
    assert nextsteps.AGENT_RULE in context and "mcp__studio__suggest_next_steps" in nextsteps.AGENT_RULE
    for phrase in ("one to three", "no check of the page", "closing message", "only fills their chat box"):
        assert phrase in nextsteps.AGENT_RULE, phrase
    description = next(d for name, d, _schema, _handler in tools.TOOLS if name == "suggest_next_steps")
    assert "at most three" in description and "closing message" in description


# ---- the studio's own look at the page after a request that changed it ------------------------------
async def test_a_page_that_shows_errors_is_said_and_offered_first(client, studio, project):
    looked = []

    async def problems(pid):
        looked.append(pid)
        return {"errors": ["Uncaught TypeError: chart is undefined", "Uncaught TypeError: chart is undefined"],
                "failed": ["http://127.0.0.1:9/data/x.json (404)"]}

    studio.turns._problems = problems
    events = await run_turn(client, studio, project["id"], "Make a page and suggest next steps")
    notice = next(e for e in events if e["type"] == "notice")
    assert "shows 2 errors when it loads" in notice["message"] and "chart is undefined" in notice["message"]   # the repeat counts once
    kinds = [e["type"] for e in events]
    assert kinds.index("checkpoint") < kinds.index("notice") < kinds.index("next_steps") < kinds.index("turn_finished")
    steps = steps_of(events)
    assert [s["id"] for s in steps] == ["errors", "a1", "a2"] and "2 errors" in steps[0]["text"]
    assert looked == [project["id"]]
    # A request that changed nothing is not looked at; a clean page says nothing.
    await run_turn(client, studio, project["id"], "use skill charts")
    assert len(looked) == 1

    async def clean(pid):
        return {"errors": [], "failed": []}

    studio.turns._problems = clean
    events = await run_turn(client, studio, project["id"], "Add a paragraph")
    assert not [e for e in events if e["type"] == "notice"] and [s["id"] for s in steps_of(events)] == ["check"]


async def test_a_look_that_fails_or_cannot_be_taken_changes_nothing(client, studio, project):
    async def broken(pid):
        raise RuntimeError("the browser went away")

    studio.turns._problems = broken
    events = await run_turn(client, studio, project["id"], "Make a page")
    assert events[-1]["status"] == "completed" and [s["id"] for s in steps_of(events)] == ["check"]
    del studio.turns._problems                                  # the real one: skipped on the scripted agent
    assert await studio.turns._problems(project["id"]) is None
    assert "opens the page once itself" in studio.agent_context(project["id"])
    assert nextsteps.studio_steps({**FACTS, "page_errors": 1, "still_wrong": 3})[0]["id"] == "errors"
