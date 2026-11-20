"""Questions before the work (CONTRACT.md, "Questions before the work"): the switch, the tool
`ask_person`, the wait on a card, what an answer may say, and the cases that do not ask."""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import run_turn

from studio2_server import effort, questions, tools
from studio2_server.turns import compose_prompt

ASK = {"depth": "standard", "verify": False, "ask": True}


async def wait_for(predicate, timeout=15.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


def of(studio, pid, *kinds):
    return [e for e in studio.log(pid).read() if e["type"] in kinds]


async def start_asking(client, studio, pid, text="make a report"):
    turn = (await client.post(f"/api/projects/{pid}/turns", json={"text": text, "options": {"ask": True}})).json()["turn_id"]
    await wait_for(lambda: of(studio, pid, "question_request"))
    return turn, of(studio, pid, "question_request")[-1]


# -- the switch -------------------------------------------------------------------------------
def test_ask_is_an_option_that_is_there_only_when_it_is_on():
    assert effort.clean({"ask": True}) == ASK
    assert effort.clean({"ask": False}) == effort.clean(None) == {"depth": "standard", "verify": False}
    assert effort.clean({"ask": True}, request=False) == ASK                    # remembered per project too
    assert effort.clean({"ask": None, "depth": "deep"}) == {"depth": "deep", "verify": False}
    for bad in ("yes", 1, []):
        with pytest.raises(effort.OptionsError):
            effort.clean({"ask": bad})
    # A request that only checks, or only polishes, never asks.
    assert "ask" not in effort.clean({"ask": True, "check_only": True})
    assert "ask" not in effort.clean({"ask": True, "polish": True})


def test_the_wording_names_the_tool_and_is_absent_when_off(tmp_path):
    said = effort.block(ASK)
    assert said.startswith("<studio-effort>\nThe person switched on \"Ask me first\"")
    assert "mcp__studio__ask_person" in said and "standing instructions" in said
    assert effort.block({"depth": "standard", "verify": False}) == ""
    assert effort.block({"depth": "deep", "verify": False, "ask": True}).index("Ask me first") < \
        effort.block({"depth": "deep", "verify": False, "ask": True}).index("chose Deep")
    assert compose_prompt("hello", None, [], tmp_path, options=effort.clean({})) == "hello"       # byte for byte
    assert "Ask me first" in compose_prompt("hello", None, [], tmp_path, options=ASK)


async def test_the_project_always_says_all_three_and_remembers_the_choice(client, project):
    pid = project["id"]
    assert project["turn_options"] == {"depth": "standard", "verify": False, "ask": False}
    patched = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"depth": "deep", "ask": True}})
    assert patched.json()["turn_options"] == {"depth": "deep", "verify": False, "ask": True}
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"]["ask"] is True
    off = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"depth": "deep"}})
    assert off.json()["turn_options"] == {"depth": "deep", "verify": False, "ask": False}
    bad = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"ask": "yes"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_options"


# -- what a call may ask ----------------------------------------------------------------------
def test_questions_are_folded_cut_and_capped():
    asked, why, cut = questions.clean({"why": "  It changes\nthe layout. ", "questions": [
        {"question": "  Who is it\nfor? ", "multiple": True, "choices": [
            {"label": " The board ", "description": "Short."}, "The team", {"label": "the board"}, {"label": ""}, 7]},
        {"question": "One choice only?", "choices": ["Yes"], "multiple": True},
        "A plain string is a question",
        {"question": ""}, 12,
        {"question": "Many?", "choices": [f"Choice {n}" for n in range(9)]},
        {"question": "word " * 100},
        {"question": "Sixth, over the limit"}]})
    assert why == "It changes the layout." and cut == 2 and [q["id"] for q in asked] == ["q1", "q2", "q3", "q4"]
    assert asked[0] == {"id": "q1", "question": "Who is it for?", "multiple": True, "choices": [
        {"label": "The board", "description": "Short."}, {"label": "The team", "description": ""},
        {"label": "7", "description": ""}]}
    assert asked[1]["choices"] == [] and asked[1]["multiple"] is False           # one choice is no choice
    assert asked[2] == {"id": "q3", "question": "A plain string is a question", "multiple": False, "choices": []}
    assert len(asked[3]["choices"]) == questions.MAX_CHOICES
    for bad in ({}, {"questions": "Who?"}, {"questions": []}, {"questions": [{"question": " "}]}):
        with pytest.raises(questions.QuestionsError):
            questions.clean(bad)


# -- what an answer may say -------------------------------------------------------------------
def test_an_answer_is_checked_against_the_card():
    asked, _why, _cut = questions.clean({"questions": [
        {"question": "Who?", "choices": ["A", "B", "C"]},
        {"question": "Which?", "choices": ["X", "Y"], "multiple": True},
        {"question": "Anything else?"}]})
    assert questions.check(asked, {"skip": True}) is None
    answers = questions.check(asked, {"answers": [{"id": "q2", "chosen": ["Y", "X"]}, {"id": "q1", "text": "  D \r\n really "}]})
    assert answers == [{"id": "q1", "chosen": [], "text": "D\n really"}, {"id": "q2", "chosen": ["X", "Y"], "text": ""},
                       {"id": "q3", "chosen": [], "text": ""}]                    # in the card's order, all three
    for bad in (None, {}, {"answers": "A"}, {"answers": []}, {"answers": [{"id": "q9", "text": "x"}]},
                {"answers": [{"id": "q1", "chosen": ["A"]}, {"id": "q1", "chosen": ["B"]}]},
                {"answers": [{"id": "q1", "chosen": ["Z"]}]}, {"answers": [{"id": "q1", "chosen": "A"}]},
                {"answers": [{"id": "q1", "chosen": ["A", "B"]}]}, {"answers": [{"id": "q3", "chosen": ["A"]}]},
                {"answers": [{"id": "q3", "text": 5}]}, {"answers": [{"id": "q3", "text": "x" * 2001}]},
                {"answers": [{"id": "q1"}, {"id": "q3", "text": "   "}]}):
        with pytest.raises(questions.QuestionsError):
            questions.check(asked, bad)
    told = questions.result(asked, answers, 1)
    assert told["answered"] is True and told["cut"] == 1 and told["answers"][2]["unanswered"] is True
    assert told["answers"][0] == {"question": "Who?", "chosen": [], "text": "D\n really"}
    assert questions.result(asked, None) == {"answered": False, "next": questions.AFTER_SKIP}


# -- the wait ---------------------------------------------------------------------------------
async def test_a_request_waits_for_the_answer_and_carries_on_with_it(client, studio, project):
    pid = project["id"]
    turn, asked = await start_asking(client, studio, pid)
    assert [q["id"] for q in asked["questions"]] == ["q1", "q2"] and asked["why"].startswith("(fake harness)")
    assert asked["questions"][0]["choices"][0]["label"] == "(fake) The team" and asked["questions"][1]["choices"] == []
    assert pid in studio.turns.active and (await client.get(f"/api/projects/{pid}")).json()["running_turn"] == turn
    assert of(studio, pid, "turn_started")[0]["options"] == ASK

    url = f"/api/projects/{pid}/turns/{turn}/questions/{asked['question_id']}"
    for bad in ({"answers": [{"id": "q1", "chosen": ["Nobody"]}]}, {"answers": []}, {}):
        refused = await client.post(url, json=bad)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_answer"
    assert pid in studio.turns.active                                          # a refused answer leaves it waiting
    other = await client.post(f"/api/projects/{pid}/turns/{turn}/questions/k_nope", json={"skip": True})
    assert other.status_code == 404 and other.json()["error"]["code"] == "unknown_question"

    sent = {"answers": [{"id": "q1", "chosen": ["(fake) A customer"]}, {"id": "q2", "text": "A summary table"}]}
    assert (await client.post(url, json=sent)).status_code == 204
    assert (await client.post(url, json=sent)).json()["error"]["code"] == "unknown_question"
    await studio.turns.wait(pid)

    events = [e for e in studio.log(pid).read() if e["turn_id"] == turn]
    kinds = [e["type"] for e in events]
    assert kinds.index("question_request") < kinds.index("question_resolved") < kinds.index("turn_finished")
    resolved = of(studio, pid, "question_resolved")
    assert [(e["question_id"], e["by"], e["skipped"]) for e in resolved] == [(asked["question_id"], "user", False)]
    assert resolved[0]["answers"] == [{"id": "q1", "chosen": ["(fake) A customer"], "text": ""},
                                      {"id": "q2", "chosen": [], "text": "A summary table"}]
    told = next(e for e in events if e["type"] == "tool_result" and "A summary table" in e["summary"])
    assert json.loads(told["summary"])["answers"][0]["chosen"] == ["(fake) A customer"]
    assert of(studio, pid, "turn_finished")[0]["status"] == "completed"
    assert studio.turns.asking == {} and studio.turns.asked == {} and studio.turns.options == {}


async def test_a_skip_tells_the_agent_to_decide_and_ends_the_asking(client, studio, project):
    pid = project["id"]
    turn, asked = await start_asking(client, studio, pid)
    url = f"/api/projects/{pid}/turns/{turn}/questions/{asked['question_id']}"
    assert (await client.post(url, json={"skip": True})).status_code == 204
    # Still inside the same request: a second ask is refused, with what to do instead.
    again = await tools.call(studio, pid, "ask_person", {"questions": [{"question": "And this?"}]})
    assert again.get("is_error") and again["content"][0]["text"] == questions.SKIPPED_BEFORE
    await studio.turns.wait(pid)
    resolved = of(studio, pid, "question_resolved")
    assert [(e["by"], e["skipped"], e["answers"]) for e in resolved] == [("user", True, [])]
    assert len(of(studio, pid, "question_request")) == 1
    assert of(studio, pid, "turn_finished")[0]["status"] == "completed"


async def test_stopping_a_request_closes_what_was_waiting(client, studio, project):
    pid = project["id"]
    turn, asked = await start_asking(client, studio, pid)
    await client.post(f"/api/projects/{pid}/turns/{turn}/stop")
    await studio.turns.wait(pid)
    resolved = of(studio, pid, "question_resolved")
    assert [(e["question_id"], e["by"], e["skipped"]) for e in resolved] == [(asked["question_id"], "turn_end", True)]
    assert of(studio, pid, "turn_finished")[0]["status"] == "stopped"
    late = await client.post(f"/api/projects/{pid}/turns/{turn}/questions/{asked['question_id']}", json={"skip": True})
    assert late.status_code == 404


# -- when it does not ask ---------------------------------------------------------------------
async def test_nothing_is_asked_unless_the_person_switched_it_on(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "make a report")                        # the switch is off
    assert of(studio, pid, "question_request") == []
    nobody = await tools.call(studio, pid, "ask_person", {"questions": [{"question": "Who?"}]})
    assert nobody.get("is_error") and nobody["content"][0]["text"] == questions.NOBODY

    turn = (await client.post(f"/api/projects/{pid}/turns", json={"text": "slow, please"})).json()["turn_id"]
    await wait_for(lambda: turn in studio.turns.options)
    off = await tools.call(studio, pid, "ask_person", {"questions": [{"question": "Who?"}]})
    assert off.get("is_error") and off["content"][0]["text"] == questions.OFF
    await client.post(f"/api/projects/{pid}/turns/{turn}/stop")
    await studio.turns.wait(pid)
    assert of(studio, pid, "question_request") == []

    await run_turn(client, studio, pid, "make a report, no questions", options={"ask": True})   # on, nothing to ask
    assert of(studio, pid, "question_request") == []


async def test_a_request_asks_twice_at_most(studio, project):
    # The runner's own bookkeeping, without a scripted request racing the test.
    pid, turn = project["id"], "t_twice"
    studio.turns.active[pid], studio.turns.options[turn] = turn, ASK
    try:
        for number, word in enumerate(("first", "second"), 1):
            call = asyncio.create_task(tools.call(studio, pid, "ask_person", {"questions": [{"question": f"The {word}?"}]}))
            await wait_for(lambda: len(of(studio, pid, "question_request")) == number)
            card = of(studio, pid, "question_request")[-1]
            assert studio.turns.reply(turn, card["question_id"], {"answers": [{"id": "q1", "text": word}]}) is True
            assert json.loads((await call)["content"][0]["text"])["answers"][0]["text"] == word
        third = await tools.call(studio, pid, "ask_person", {"questions": [{"question": "The third?"}]})
        assert third.get("is_error") and third["content"][0]["text"] == questions.TOO_MANY
        nothing = await tools.call(studio, pid, "ask_person", {"questions": []})
        assert nothing.get("is_error")
    finally:
        studio.turns.active.pop(pid, None)
        for held in (studio.turns.options, studio.turns.asked, studio.turns.asking):
            held.pop(turn, None)
    assert len(of(studio, pid, "question_request")) == len(of(studio, pid, "question_resolved")) == questions.MAX_ASKS


def test_the_tool_is_listed_and_the_agent_programs_own_is_refused():
    assert "ask_person" in tools.HANDLERS
    assert "mcp__studio__ask_person" in questions.BUILT_IN_ON and questions.BUILT_IN == "AskUserQuestion"


# -- the edges a review found -----------------------------------------------------------------
def test_an_answer_with_an_id_that_is_not_text_is_refused_not_a_crash():
    asked, _why, _cut = questions.clean({"questions": [{"question": "Who?"}]})
    for bad in ([["q1"]], [{}], [5], [None]):
        with pytest.raises(questions.QuestionsError):
            questions.check(asked, {"answers": [{"id": bad[0], "text": "x"}]})


async def test_an_answer_taken_as_the_request_ends_is_still_recorded_as_the_persons(studio, project):
    pid, turn = project["id"], "t_edge"
    studio.turns.active[pid], studio.turns.options[turn] = turn, ASK
    call = asyncio.create_task(tools.call(studio, pid, "ask_person", {"questions": [{"question": "Who?"}]}))
    try:
        await wait_for(lambda: of(studio, pid, "question_request"))
        card = of(studio, pid, "question_request")[0]
        # The answer is taken (the person's 204) and the turn closes before the waiting call wakes.
        assert studio.turns.reply(turn, card["question_id"], {"answers": [{"id": "q1", "text": "The board"}]}) is True
        studio.turns._close_questions(pid, turn)
        await call
    finally:
        studio.turns.active.pop(pid, None)
        for held in (studio.turns.options, studio.turns.asked, studio.turns.asking):
            held.pop(turn, None)
    resolved = of(studio, pid, "question_resolved")
    assert [(e["by"], e["skipped"], e["answers"]) for e in resolved] == [
        ("user", False, [{"id": "q1", "chosen": [], "text": "The board"}])]


async def test_a_card_left_waiting_by_a_studio_that_died_is_closed_at_the_next_start(studio, project):
    pid = project["id"]
    events = studio.log(pid)
    events.append("turn_started", "t_dead", text="make a report", selection=None, options=ASK)
    events.append("question_request", "t_dead", question_id="k_dead", why="",
                  questions=[{"id": "q1", "question": "Who?", "multiple": False, "choices": []}])
    assert events.unanswered() == {"t_dead": ["k_dead"]}
    await studio._close_orphaned_turns(studio.store.get(pid))
    kinds = [(e["type"], e.get("by")) for e in events.read() if e["turn_id"] == "t_dead"]
    assert ("question_resolved", "turn_end") in kinds
    assert kinds.index(("question_resolved", "turn_end")) < [k for k, _b in kinds].index("turn_finished")
    assert events.unanswered() == {}
