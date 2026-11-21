"""How much to involve the person (CONTRACT.md, "How much to involve the person"): the scale, the
tool `ask_person` and its three kinds of card, the wait, what an answer may say, and the cases that
do not ask."""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import run_turn

from studio2_server import effort, questions, tools
from studio2_server.turns import compose_prompt

ASK = {"depth": "standard", "verify": False, "involve": 50}


def at(stop: int) -> dict:
    return {"depth": "standard", "verify": False, "involve": stop}


async def wait_for(predicate, timeout=15.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


def of(studio, pid, *kinds):
    return [e for e in studio.log(pid).read() if e["type"] in kinds]


async def start_asking(client, studio, pid, text="make a report"):
    turn = (await client.post(f"/api/projects/{pid}/turns", json={"text": text, "options": {"involve": 50}})).json()["turn_id"]
    await wait_for(lambda: of(studio, pid, "question_request"))
    return turn, of(studio, pid, "question_request")[-1]


# -- the scale --------------------------------------------------------------------------------
def test_involve_is_one_of_five_stops_and_is_there_only_above_zero():
    for stop in (25, 50, 75, 100):
        assert effort.clean({"involve": stop}) == at(stop)
        assert effort.clean({"involve": stop}, request=False) == at(stop)        # remembered per project too
    assert effort.clean({"involve": 0}) == effort.clean(None) == {"depth": "standard", "verify": False}
    assert effort.clean({"involve": None, "depth": "deep"}) == {"depth": "deep", "verify": False}
    for bad in (10, 37, 101, -25, "50", True, 50.5, 50.0, []):
        with pytest.raises(effort.OptionsError):
            effort.clean({"involve": bad})
    # The on/off switch this replaced, read as the middle stop and never written.
    assert effort.clean({"ask": True}) == at(50) and effort.clean({"ask": False}) == effort.clean(None)
    assert effort.clean({"ask": True, "involve": 100}) == at(100)
    # A request that only checks, or only polishes, never asks.
    assert "involve" not in effort.clean({"involve": 100, "check_only": True})
    assert "involve" not in effort.clean({"involve": 100, "polish": True})
    assert set(effort.INVOLVE_STOPS) == set(effort.INVOLVE_NAMES) == set(effort.INVOLVE_CARDS)
    assert [effort.INVOLVE_CARDS[stop] for stop in effort.INVOLVE_STOPS] == [0, 1, 2, 4, 12]


def test_each_stop_has_its_own_wording_and_zero_adds_nothing(tmp_path):
    said = {stop: effort.block(at(stop)) for stop in (25, 50, 75, 100)}
    for stop, text in said.items():
        assert text.startswith(f"<studio-effort>\nThe person set how much to involve them to {stop} of 100")
        assert "mcp__studio__ask_person" in text and "standing instructions" in text
        assert "Match the asking to the work" in text and "no card at all" in text      # a small request is just done
    assert "cannot sensibly go on" in said[25] and "kind: \"plan\"" not in said[25]
    assert "kind: \"plan\"" not in said[50] and "kind: \"checkpoint\"" not in said[50]
    assert "kind: \"plan\"" in said[75] and "kind: \"checkpoint\"" not in said[75]
    assert "kind: \"plan\"" in said[100] and "kind: \"checkpoint\"" in said[100] and "`references`" in said[100]
    assert "never all at once" in said[100] and "showing it at the end" in said[100]   # the first real run built it whole
    assert effort.block({"depth": "standard", "verify": False}) == ""
    deep = effort.block({"depth": "deep", "verify": False, "involve": 50})
    assert deep.index("involve them") < deep.index("chose Deep")
    assert compose_prompt("hello", None, [], tmp_path, options=effort.clean({})) == "hello"       # byte for byte
    assert "Ask first" in compose_prompt("hello", None, [], tmp_path, options=ASK)


async def test_the_project_always_says_all_three_and_remembers_the_stop(client, studio, project):
    pid = project["id"]
    assert project["turn_options"] == {"depth": "standard", "verify": False, "involve": 0}
    patched = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"depth": "deep", "involve": 75}})
    assert patched.json()["turn_options"] == {"depth": "deep", "verify": False, "involve": 75}
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"]["involve"] == 75
    off = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"depth": "deep"}})
    assert off.json()["turn_options"] == {"depth": "deep", "verify": False, "involve": 0}
    bad = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"involve": 60}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_options"
    # A record from the hour the switch existed, and one written by hand: the view still answers.
    studio.store.update(pid, turn_options={"depth": "quick", "verify": True, "ask": True})
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"] == {"depth": "quick", "verify": True, "involve": 50}
    studio.store.update(pid, turn_options={"depth": "sideways"})
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"] == {"depth": "standard", "verify": False, "involve": 0}


# -- what a call may ask ----------------------------------------------------------------------
def test_questions_are_folded_cut_and_capped():
    card = questions.clean({"why": "  It changes\nthe layout. ", "questions": [
        {"question": "  Who is it\nfor? ", "multiple": True, "choices": [
            {"label": " The board ", "description": "Short."}, "The team", {"label": "the board"}, {"label": ""}, 7]},
        {"question": "One choice only?", "choices": ["Yes"], "multiple": True},
        "A plain string is a question",
        {"question": ""}, 12,
        {"question": "Many?", "choices": [f"Choice {n}" for n in range(9)]},
        {"question": "word " * 100},
        {"question": "Sixth, over the limit"}]})
    asked = card["questions"]
    assert card["why"] == "It changes the layout." and card["cut"] == 2 and [q["id"] for q in asked] == ["q1", "q2", "q3", "q4"]
    assert (card["kind"], card["title"], card["summary"], card["references"]) == ("questions", "", "", [])
    assert asked[0] == {"id": "q1", "question": "Who is it for?", "multiple": True, "choices": [
        {"label": "The board", "description": "Short."}, {"label": "The team", "description": ""},
        {"label": "7", "description": ""}]}
    assert asked[1]["choices"] == [] and asked[1]["multiple"] is False           # one choice is no choice
    assert asked[2] == {"id": "q3", "question": "A plain string is a question", "multiple": False, "choices": []}
    assert len(asked[3]["choices"]) == questions.MAX_CHOICES
    for bad in ({}, {"questions": "Who?"}, {"questions": []}, {"questions": [{"question": " "}]}, {"kind": "poll"},
                {"kind": "plan", "questions": "Who?"}):
        with pytest.raises(questions.QuestionsError):
            questions.clean(bad)


def test_a_plan_and_a_check_in_carry_a_summary_and_what_they_rest_on(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "q3.csv").write_text("region,sales\n")
    card = questions.clean({
        "kind": "plan", "title": " The\nplan ",
        "summary": "1. **Headline**:   the total\r\n2. By region\n\n\n\n   - indented stays\n",
        "references": [
            {"label": " Q3 figures ", "path": "data/q3.csv", "note": " the four\nregions "},
            {"label": "A bulletin", "url": "https://example.org/b", "path": "data/q3.csv"},     # an address wins
            {"label": "Not a file", "path": "data/missing.csv"}, {"label": "Outside", "path": "../../etc/passwd"},
            {"label": "A script", "url": "javascript:alert(1)"}, {"url": "https://example.org/only"},
            "https://example.org/plain", {"note": "no label and nothing to name it"}, 5,
            {"label": {"not": "text"}, "url": "HTTPS://Example.org/Upper"}]}, tmp_path)
    assert card["kind"] == "plan" and card["title"] == "The plan"
    assert card["summary"] == "1. **Headline**: the total\n2. By region\n\n   - indented stays"
    assert card["references"] == [
        {"label": "Q3 figures", "note": "the four regions", "path": "data/q3.csv"},
        {"label": "A bulletin", "note": "", "url": "https://example.org/b"},
        {"label": "Not a file", "note": ""}, {"label": "Outside", "note": ""}, {"label": "A script", "note": ""},
        {"label": "https://example.org/only", "note": "", "url": "https://example.org/only"},
        {"label": "https://example.org/plain", "note": "", "url": "https://example.org/plain"},
        {"label": "HTTPS://Example.org/Upper", "note": "", "url": "HTTPS://Example.org/Upper"}]
    # Without a question of its own, a plan or a check-in still asks one.
    assert [q["question"] for q in card["questions"]] == ["Go ahead with this plan?"]
    assert [c["label"] for c in card["questions"][0]["choices"]] == ["Go ahead", "Change something"]
    part = questions.clean({"kind": "checkpoint", "title": "Sales by region", "questions": [{"question": " "}]})
    assert [q["question"] for q in part["questions"]] == ["Carry on to the next part?"]
    own = questions.clean({"kind": "checkpoint", "questions": [{"question": "Keep the chart?"}]})
    assert [q["question"] for q in own["questions"]] == ["Keep the chart?"]
    long = questions.clean({"kind": "plan", "summary": "\n".join(f"{n}. A section about something" for n in range(200)),
                            "references": [{"label": f"Source {n}"} for n in range(12)]})
    assert len(long["summary"]) <= questions.SUMMARY_CAP and long["summary"].endswith(" …")
    assert len(long["references"]) == questions.MAX_REFERENCES
    assert questions.clean({"kind": "plan", "references": [{"label": "A file", "path": "data/q3.csv"}]})["references"] == [
        {"label": "A file", "note": ""}]                                          # no project folder given: words only


# -- what an answer may say -------------------------------------------------------------------
def test_an_answer_is_checked_against_the_card():
    asked = questions.clean({"questions": [
        {"question": "Who?", "choices": ["A", "B", "C"]},
        {"question": "Which?", "choices": ["X", "Y"], "multiple": True},
        {"question": "Anything else?"}]})["questions"]
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
    told = questions.result(asked, answers, 1, 3)
    assert told["answered"] is True and told["cut"] == 1 and told["left"] == 3 and told["answers"][2]["unanswered"] is True
    assert "last card" not in told["next"] and "last card" in questions.result(asked, answers)["next"]
    assert told["answers"][0] == {"question": "Who?", "chosen": [], "text": "D\n really"}
    assert questions.result(asked, None) == {"answered": False, "next": questions.AFTER_SKIP}


# -- the wait ---------------------------------------------------------------------------------
async def test_a_request_waits_for_the_answer_and_carries_on_with_it(client, studio, project):
    pid = project["id"]
    turn, asked = await start_asking(client, studio, pid)
    assert [q["id"] for q in asked["questions"]] == ["q1", "q2"] and asked["why"].startswith("(fake harness)")
    assert asked["questions"][0]["choices"][0]["label"] == "(fake) The team" and asked["questions"][1]["choices"] == []
    assert (asked["kind"], asked["title"], asked["summary"], asked["references"]) == ("questions", "", "", [])
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
    assert json.loads(told["summary"])["left"] == 1                             # the middle stop allows two cards
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
async def test_nothing_is_asked_while_the_scale_is_at_zero(client, studio, project):
    pid = project["id"]
    await run_turn(client, studio, pid, "make a report")                        # the scale is at 0
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

    await run_turn(client, studio, pid, "make a report, no questions", options={"involve": 100})   # on, nothing to ask
    assert of(studio, pid, "question_request") == []


@pytest.mark.parametrize("stop, cards", [(25, 1), (50, 2), (75, 4), (100, 12)])
async def test_the_stop_caps_the_cards_of_a_request(studio, project, stop, cards):
    # The runner's own bookkeeping, without a scripted request racing the test.
    pid, turn = project["id"], f"t_cap{stop}"
    studio.turns.active[pid], studio.turns.options[turn] = turn, at(stop)
    try:
        for number in range(1, cards + 1):
            call = asyncio.create_task(tools.call(studio, pid, "ask_person", {
                "kind": "checkpoint" if number > 2 else "questions", "title": f"Part {number}",
                "questions": [{"question": f"Card {number}?"}]}))
            await wait_for(lambda: len(of(studio, pid, "question_request")) == number)
            card = of(studio, pid, "question_request")[-1]
            assert card["kind"] == ("checkpoint" if number > 2 else "questions") and card["title"] == f"Part {number}"
            assert studio.turns.reply(turn, card["question_id"], {"answers": [{"id": "q1", "text": f"answer {number}"}]}) is True
            told = json.loads((await call)["content"][0]["text"])
            assert told["answers"][0]["text"] == f"answer {number}" and told["left"] == cards - number
        over = await tools.call(studio, pid, "ask_person", {"questions": [{"question": "One more?"}]})
        assert over.get("is_error") and over["content"][0]["text"] == questions.TOO_MANY
        nothing = await tools.call(studio, pid, "ask_person", {"questions": []})
        assert nothing.get("is_error")
    finally:
        studio.turns.active.pop(pid, None)
        for held in (studio.turns.options, studio.turns.asked, studio.turns.asking):
            held.pop(turn, None)
    assert len(of(studio, pid, "question_request")) == len(of(studio, pid, "question_resolved")) == cards


async def test_a_plan_card_reaches_the_event_with_its_summary_and_references(studio, project):
    pid, turn = project["id"], "t_plan"
    workspace = studio.store.workspace(pid)
    (workspace / "notes.md").write_text("# Notes\n")
    studio.turns.active[pid], studio.turns.options[turn] = turn, at(75)
    try:
        call = asyncio.create_task(tools.call(studio, pid, "ask_person", {
            "kind": "plan", "summary": "1. Headline\n2. By region", "why": "So it is built once.",
            "references": [{"label": "My notes", "path": "notes.md"}, {"label": "A bulletin", "url": "https://example.org/b"}]}))
        await wait_for(lambda: of(studio, pid, "question_request"))
        card = of(studio, pid, "question_request")[0]
        assert (card["kind"], card["summary"], card["why"]) == ("plan", "1. Headline\n2. By region", "So it is built once.")
        assert card["references"] == [{"label": "My notes", "note": "", "path": "notes.md"},
                                      {"label": "A bulletin", "note": "", "url": "https://example.org/b"}]
        assert card["questions"][0]["question"] == "Go ahead with this plan?" and "cut" not in card
        assert studio.turns.reply(turn, card["question_id"], {"answers": [{"id": "q1", "chosen": ["Go ahead"]}]}) is True
        told = json.loads((await call)["content"][0]["text"])
        assert told["answers"] == [{"question": "Go ahead with this plan?", "chosen": ["Go ahead"], "text": ""}] and told["left"] == 3
    finally:
        studio.turns.active.pop(pid, None)
        for held in (studio.turns.options, studio.turns.asked, studio.turns.asking):
            held.pop(turn, None)


def test_the_tool_is_listed_and_the_agent_programs_own_is_refused():
    assert "ask_person" in tools.HANDLERS
    assert "mcp__studio__ask_person" in questions.BUILT_IN_ON and questions.BUILT_IN == "AskUserQuestion"


# -- the edges a review found -----------------------------------------------------------------
def test_an_answer_with_an_id_that_is_not_text_is_refused_not_a_crash():
    asked = questions.clean({"questions": [{"question": "Who?"}]})["questions"]
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


# -- after the field test of 24 real requests --------------------------------------------------
def test_a_choice_is_an_answer_and_at_most_one_is_the_agents_pick():
    card = questions.clean({"questions": [
        {"question": "Share the product list?", "choices": ["I'll paste or upload it", "Build an empty template"]},
        {"question": "Compare against?", "choices": [
            {"label": "Within the quarter", "pick": True}, {"label": "I will give you targets"},
            {"label": "I\u2019ll send last year", "pick": True}, {"label": "Region against region", "pick": True},
            {"label": "Illustrative only"}]},
        {"question": "Who?", "choices": [{"label": "The board", "pick": "yes"}, {"label": "The team"}]}]})
    first, second, third = card["questions"]
    assert first["choices"] == []                                # a promise is no answer; one choice is no choice
    assert [c["label"] for c in second["choices"]] == ["Within the quarter", "Region against region", "Illustrative only"]
    assert [c.get("pick") for c in second["choices"]] == [True, None, None]     # the first one marked, no second
    assert all("pick" not in c for c in third["choices"])                       # only a real true counts
    answers = questions.check(card["questions"], {"answers": [{"id": "q2", "chosen": ["Region against region"]}]})
    assert answers[1]["chosen"] == ["Region against region"]


def test_the_wording_carries_what_the_field_test_found():
    for stop in (25, 50, 75, 100):
        said = effort.block(at(stop))
        assert "`pick: true`" in said and "Every choice is an answer in itself" in said
        assert "never ask how deep" in said and "for every project and for this one" in said
    for stop in (50, 75, 100):
        said = effort.block(at(stop))
        assert "do not assume the United States" in said and "one question may be about the look" in said
    for stop in (75, 100):
        assert "A short or one-page piece still gets its plan" in effort.block(at(stop))
