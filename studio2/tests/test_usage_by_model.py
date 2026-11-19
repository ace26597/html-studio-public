"""What a turn used, by model (CONTRACT.md, "What a turn used, by model" and "A turn that ended without
a result"). The numbers in the two "measured" tests are what the agent program reported on 19 November
2026 in real requests on the Claude plan. Zero model calls here.
"""
import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from test_costs import CONNECTION, SpendLog, events_of, use

from studio2_server import costs
from studio2_server.costs import add_counts, message_counts, model_counts, same_model, sum_counts, turn_models
from studio2_server.harness.base import TurnResult
from studio2_server.harness.claude import EventMapper


def counts(input=0, cache_read=0, cache_write=0, output=0):
    return {"input": input, "cache_read": cache_read, "cache_write": cache_write, "output": output}


def running(input, written, read, output, cost=0.0):
    """One model's entry of the agent program's `modelUsage`, in its own spelling."""
    return {"inputTokens": input, "cacheCreationInputTokens": written, "cacheReadInputTokens": read,
            "outputTokens": output, "costUSD": cost, "webSearchRequests": 0, "contextWindow": 200_000}


# ---- the pieces -------------------------------------------------------------------------------------
def test_the_programs_running_totals_and_a_messages_usage_become_the_same_four_counts():
    assert model_counts({"claude-haiku-5-5": running(1218, 28197, 14980, 259, 0.0052)}) == {
        "claude-haiku-5-5": counts(1218, 14980, 28197, 259)}
    assert model_counts({"quiet": running(0, 0, 0, 0), "odd": "not a row"}) == {}        # nothing used: not kept
    assert model_counts(None) == {} and model_counts("x") == {}
    assert message_counts({"input_tokens": 4, "cache_creation_input_tokens": 17104, "cache_read_input_tokens": 14980,
                           "output_tokens": 161}) == counts(4, 14980, 17104, 161)
    assert message_counts({"input_tokens": None, "output_tokens": -3, "cache_read_input_tokens": True}) == counts()
    assert sum_counts(add_counts({"a": counts(1, 2, 3, 4)}, {"a": counts(10, 0, 0, 1), "b": counts(output=5)})) == counts(11, 2, 3, 10)


def test_a_model_is_the_same_with_or_without_its_date():
    assert same_model("claude-haiku-5-5-20251001", "claude-haiku-5-5") and same_model("Claude-Opus-5-5", "claude-opus-5-5")
    assert not same_model("claude-haiku-5-5", "claude-sonnet-5-5") and not same_model("gpt-6.1-sol", "gpt-6.1-sol-pro")


def test_a_turns_part_is_the_running_total_less_what_the_conversation_had_reached():
    first = {"m": counts(100, 1000, 500, 40)}
    used, mark = turn_models(first, None, "s1", None)
    assert used == first and mark == {"session_id": "s1", "models": first}
    second = {"m": counts(130, 4000, 500, 90), "helper": counts(5, 0, 300, 7)}       # a helper model joined
    used, mark = turn_models(second, None, "s1", mark)
    assert used == {"m": counts(30, 3000, 0, 50), "helper": counts(5, 0, 300, 7)}
    assert mark == {"session_id": "s1", "models": second}
    same, _ = turn_models(second, None, "s1", mark)
    assert same == {}                                                                 # nothing new: nothing used


def test_a_new_conversation_or_a_count_that_went_down_starts_from_nothing():
    mark = {"session_id": "s1", "models": {"m": counts(100, 1000, 500, 40)}}
    now = {"m": counts(20, 300, 10, 5)}
    assert turn_models(now, None, "s2", mark)[0] == now                               # another conversation
    assert turn_models(now, None, "s1", mark)[0] == now                               # the totals were reset
    assert turn_models(now, None, None, mark) == (now, None)                          # no session: nothing to keep
    assert turn_models(None, None, "s1", mark) == ({}, None)                          # nothing known at all


def test_measured_with_a_helper_the_turns_own_usage_misses_what_the_running_totals_hold():
    # The result's `usage` said 4 / 17,104 written / 14,980 read / 161 out. `modelUsage` said more:
    # the helper agent and the program's own small calls are only there.
    totals = model_counts({"claude-haiku-5-5": running(1218, 28197, 14980, 259, 0.005208525)})
    used, _ = turn_models(totals, {"claude-haiku-5-5": counts(4, 14980, 17104, 21)}, "s1", None)
    assert used == {"claude-haiku-5-5": counts(1218, 14980, 28197, 259)}
    assert used["claude-haiku-5-5"]["cache_write"] - 17104 == 11093                   # the helper's part, exactly


def test_a_turn_without_a_result_keeps_what_was_heard_and_remembers_it_as_counted():
    before = {"session_id": "s1", "models": {"m": counts(1176, 10899, 4044, 18)}}
    heard = {"m": counts(6, 45340, 7806, 13)}
    used, mark = turn_models(None, heard, "s1", before)
    assert used == heard
    assert mark == {"session_id": "s1", "models": before["models"], "pending": heard}
    again, mark = turn_models(None, {"m": counts(1, 10, 0, 2)}, "s1", mark)           # stopped twice in a row
    assert mark["pending"] == {"m": counts(7, 45350, 7806, 15)} and mark["models"] == before["models"]


def test_measured_after_a_stop_the_next_turn_is_neither_counted_twice_nor_lost():
    # Request A ended at these running totals; B was stopped part-way having been heard using `pending`;
    # C resumed the conversation. The totals at the end of C hold most of B (all but the answer that
    # was on its way) as well as C. C's part: the growth less what B was counted for, never less than
    # what C itself was heard using.
    mark = {"session_id": "s1", "models": {"claude-haiku-5-5": counts(1176, 10899, 4044, 18)},
            "pending": {"claude-haiku-5-5-20251001": counts(6, 45340, 7806, 13)}}     # heard under the dated name
    totals = {"claude-haiku-5-5": counts(1182, 56492, 12231, 454)}
    heard = {"claude-haiku-5-5-20251001": counts(2, 19174, 634, 2)}
    used, after = turn_models(totals, heard, "s1", mark)
    assert used == {"claude-haiku-5-5": counts(2, 19174, 634, 423)}
    assert after == {"session_id": "s1", "models": totals}                            # nothing pending any more
    # Had the stopped request been wholly in the totals, the growth less its part would be the larger.
    whole = {"claude-haiku-5-5": counts(1190, 80000, 13000, 500)}
    assert turn_models(whole, heard, "s1", mark)[0] == {"claude-haiku-5-5": counts(8, 23761, 1150, 469)}


# ---- the agent program's messages -------------------------------------------------------------------
def result(**overrides):
    fields = {"subtype": "success", "duration_ms": 900, "duration_api_ms": 800, "is_error": False, "num_turns": 2,
              "session_id": "s1", "total_cost_usd": 0.0052, "result": "done",
              "usage": {"input_tokens": 4, "cache_creation_input_tokens": 17104, "cache_read_input_tokens": 14980,
                        "output_tokens": 161}}
    return ResultMessage(**{**fields, **overrides})


def said(message_id, usage, helper=None, model="claude-haiku-5-5"):
    return AssistantMessage(content=[TextBlock(text="x")], model=model, message_id=message_id, usage=usage,
                            parent_tool_use_id=helper)


def test_the_harness_hands_on_the_running_totals_and_what_it_heard_each_message_once(tmp_path):
    mapper = EventMapper(tmp_path)
    main = {"input_tokens": 2, "cache_creation_input_tokens": 17104, "cache_read_input_tokens": 0, "output_tokens": 9}
    for message in (said("msg_1", main), said("msg_1", main),                          # the same message, two parts
                    said("msg_2", {"input_tokens": 2, "cache_read_input_tokens": 14980, "output_tokens": 12}),
                    said("msg_h", {"input_tokens": 2, "cache_creation_input_tokens": 11093, "output_tokens": 5},
                         helper="tu_agent", model="claude-haiku-5-5-20251001"),
                    result(model_usage={"claude-haiku-5-5": running(1218, 28197, 14980, 259, 0.0052)})):
        mapper.handle(message)
    usage = mapper.turn_result().usage
    assert usage["session_models"] == {"claude-haiku-5-5": counts(1218, 14980, 28197, 259)}
    assert usage["models_heard"] == {"claude-haiku-5-5": counts(4, 14980, 17104, 21),
                                     "claude-haiku-5-5-20251001": counts(2, 0, 11093, 5)}
    assert usage["tokens"]["cache_write"] == 17104                                     # the program's own `usage`, as before


def streamed(event, helper=None):
    return StreamEvent(uuid="u", session_id="s1", event=event, parent_tool_use_id=helper)


def test_through_a_gateway_the_counts_come_when_a_message_ends_and_are_heard_there(tmp_path):
    # LiteLLM starts every message with zeros and says what it used in the closing `message_delta`
    # (seen on 19 November 2026); a stopped turn on that route used to keep nothing at all.
    mapper = EventMapper(tmp_path, gateway=True)
    zeros = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    for message in (
            streamed({"type": "message_start", "message": {"id": "msg_a", "model": "openrouter/x/y", "usage": zeros}}),
            said("msg_a", zeros, model="openrouter/x/y"),                              # the message itself: still zeros
            streamed({"type": "message_delta", "usage": {"input_tokens": 16536, "output_tokens": 3}}),
            streamed({"type": "message_start", "message": {"id": "msg_b", "model": "openrouter/x/y", "usage": zeros}}),
            streamed({"type": "message_start", "message": {"id": "msg_h", "model": "openrouter/x/small", "usage": zeros}},
                     helper="tu_agent"),                                               # a helper, at the same time
            streamed({"type": "message_delta", "usage": {"input_tokens": 900, "output_tokens": 40}}, helper="tu_agent"),
            streamed({"type": "message_delta", "usage": {"input_tokens": 22174, "cache_read_input_tokens": 16000,
                                                         "output_tokens": 568}})):
        mapper.handle(message)
    assert mapper.heard_models() == {"openrouter/x/y": counts(16536 + 22174, 16000, 0, 3 + 568),
                                     "openrouter/x/small": counts(900, 0, 0, 40)}


def test_a_message_that_reports_twice_is_counted_at_its_largest_not_added(tmp_path):
    mapper = EventMapper(tmp_path)
    mapper.handle(streamed({"type": "message_start", "message": {
        "id": "msg_1", "model": "claude-haiku-5-5", "usage": {"input_tokens": 4, "cache_read_input_tokens": 14980, "output_tokens": 1}}}))
    mapper.handle(said("msg_1", {"input_tokens": 4, "cache_read_input_tokens": 14980, "output_tokens": 21}))
    mapper.handle(streamed({"type": "message_delta", "usage": {"output_tokens": 161}}))
    assert mapper.heard_models() == {"claude-haiku-5-5": counts(4, 14980, 0, 161)}      # the true output, once


def test_a_program_that_reports_no_running_totals_leaves_the_usage_as_it_was(tmp_path):
    mapper = EventMapper(tmp_path)
    mapper.handle(result())
    usage = mapper.turn_result().usage
    assert "session_models" not in usage and "models_heard" not in usage


def test_a_process_that_died_before_any_result_still_says_what_it_heard(tmp_path):
    mapper = EventMapper(tmp_path)
    mapper.handle(said("msg_1", {"input_tokens": 3, "cache_read_input_tokens": 900, "output_tokens": 1}))
    outcome = mapper.turn_result("the process ended")
    assert outcome.status == "failed" and outcome.usage["models_heard"] == {"claude-haiku-5-5": counts(3, 900, 0, 1)}
    assert EventMapper(tmp_path).turn_result().usage.get("models_heard") is None


# ---- a whole turn -----------------------------------------------------------------------------------
class Reports:
    """A harness that reports the conversation's running totals the way the real one does."""
    kind = "anthropic-gateway"

    def __init__(self, *turns):
        self.turns = list(turns)

    async def run(self, request, emit):
        totals, heard, stop = self.turns.pop(0)
        request.progress["heard"] = lambda: heard
        if stop:
            raise asyncio.CancelledError
        usage = {"input_tokens": 7, "output_tokens": 3, "cost_usd": None, "duration_ms": 5, "cost_estimated": False,
                 "tokens": {"input": 7, "cache_read": 0, "cache_write": 0, "output": 3, "cache_write_1h": None},
                 "session_models": totals, "models_heard": heard}
        return TurnResult("completed", "sess-1", None, usage)

    def describe(self, connection):
        return {"id": connection["id"], "label": connection["label"], "kind": connection["kind"], "available": True,
                "reason": None, "models": connection["models"], "default_model": connection["default_model"]}


MAIN, SMALL = "openrouter/openai/gpt-6.1-sol", "openrouter/anthropic/claude-haiku-5.5"
TWO = {**CONNECTION, "models": [{"id": MAIN, "label": "sol"}], "default_model": MAIN}


async def turn(client, studio, pid):
    turn_id = (await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})).json()["turn_id"]
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    return turn_id


async def test_each_turn_writes_its_own_part_by_model_and_prices_each_model_at_its_own_rate(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [TWO])
    first = {MAIN: counts(1_000, 200_000, 0, 2_000), SMALL: counts(500, 0, 0, 100)}
    second = {MAIN: counts(1_500, 500_000, 0, 5_000), SMALL: counts(500, 0, 0, 100)}
    studio.registry._harnesses["anthropic-gateway"] = Reports((first, {}, False), (second, {}, False))
    use(studio, SpendLog([[]], status=403))                 # this key may not read the gateway's spend log

    await turn(client, studio, pid)
    finished = events_of(studio, pid, "turn_finished")[-1]["usage"]
    assert finished["models"] == first and "session_models" not in finished and "models_heard" not in finished
    assert finished["tokens"] == {"input": 1_500, "cache_read": 200_000, "cache_write": 0, "output": 2_100, "cache_write_1h": None}
    assert finished["input_tokens"] == 201_500 and finished["output_tokens"] == 2_100   # the helper model is in the count
    assert studio.store.get(pid)["session_usage"] == {"session_id": "sess-1", "models": first}
    cost = events_of(studio, pid, "turn_cost")[-1]
    rates = costs.prices.shipped(studio.settings.library_dir)
    sol, haiku = rates.find(MAIN), rates.find(SMALL)
    expected = ((1_000 * sol["input"] + 200_000 * sol["cache_read"] + 2_000 * sol["output"])
                + (500 * haiku["input"] + 100 * haiku["output"])) / 1e6
    assert cost["source"] == "tokens" and cost["cost_usd"] == pytest.approx(expected)
    assert cost["backup"]["requests"] is None and "unpriced_models" not in cost["backup"]

    await turn(client, studio, pid)                         # the same conversation: only what grew
    finished = events_of(studio, pid, "turn_finished")[-1]["usage"]
    assert finished["models"] == {MAIN: counts(500, 300_000, 0, 3_000)}
    cost = events_of(studio, pid, "turn_cost")[-1]
    assert cost["cost_usd"] == pytest.approx((500 * sol["input"] + 300_000 * sol["cache_read"] + 3_000 * sol["output"]) / 1e6)


async def test_a_model_without_a_rate_is_left_out_and_named(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [TWO])
    totals = {MAIN: counts(1_000, 0, 0, 500), "a-model-nobody-prices": counts(9_000, 0, 0, 900)}
    studio.registry._harnesses["anthropic-gateway"] = Reports((totals, {}, False))
    use(studio, SpendLog([[]], status=403))
    await turn(client, studio, pid)
    cost = events_of(studio, pid, "turn_cost")[-1]
    sol = costs.prices.shipped(studio.settings.library_dir).find(MAIN)
    assert cost["cost_usd"] == pytest.approx((1_000 * sol["input"] + 500 * sol["output"]) / 1e6)
    assert cost["backup"]["unpriced_models"] == ["a-model-nobody-prices"]
    assert "a-model-nobody-prices" in cost["reason"] and "Not in this figure" in cost["reason"]


async def test_a_stopped_turn_keeps_what_was_heard_says_so_and_the_next_turn_does_not_count_it_again(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [TWO])
    begun = {MAIN: counts(1_000, 100_000, 0, 400)}
    heard_when_stopped = {MAIN: counts(300, 250_000, 0, 20)}
    after = {MAIN: counts(1_700, 600_000, 0, 2_400)}        # holds the stopped turn's work as well
    studio.registry._harnesses["anthropic-gateway"] = Reports(
        (begun, {}, False), (None, heard_when_stopped, True), (after, {MAIN: counts(350, 240_000, 0, 30)}, False))
    use(studio, SpendLog([[]], status=403))
    await turn(client, studio, pid)
    await turn(client, studio, pid)
    stopped = events_of(studio, pid, "turn_finished")[-1]
    assert stopped["status"] == "stopped" and stopped["usage"]["models"] == heard_when_stopped
    assert stopped["usage"]["tokens_partial"] is True and stopped["usage"]["input_tokens"] == 250_300
    cost = events_of(studio, pid, "turn_cost")[-1]
    assert cost["source"] == "tokens" and cost["backup"]["partial"] is True and "at least" in cost["reason"]
    assert studio.store.get(pid)["session_usage"] == {"session_id": "sess-1", "models": begun, "pending": heard_when_stopped}

    await turn(client, studio, pid)
    finished = events_of(studio, pid, "turn_finished")[-1]["usage"]
    # growth 700 / 500,000 / 2,000 less the stopped turn's 300 / 250,000 / 20: the three turns add up to `after`
    assert finished["models"] == {MAIN: counts(400, 250_000, 0, 1_980)} and "tokens_partial" not in finished
    assert studio.store.get(pid)["session_usage"] == {"session_id": "sess-1", "models": after}


# ---- the studio's own small call before a turn ------------------------------------------------------
def test_a_small_call_is_kept_for_the_turn_it_helped_and_handed_over_once():
    token = costs.CURRENT_TURN.set("t_small")
    try:
        costs.note_auxiliary({"claude-haiku-5-5": running(900, 0, 0, 40, 0.0011)}, 0.0011)
        costs.note_auxiliary({"claude-haiku-5-5": running(100, 0, 0, 10, 0.0002)}, 0.0002)
    finally:
        costs.CURRENT_TURN.reset(token)
    costs.note_auxiliary({"claude-haiku-5-5": running(5, 0, 0, 5)}, 1.0)               # outside any turn: not kept
    kept = costs.take_auxiliary("t_small")
    assert kept["models"] == {"claude-haiku-5-5": counts(1000, 0, 0, 50)} and kept["cost_usd"] == pytest.approx(0.0013)
    assert costs.take_auxiliary("t_small") == {"models": {}, "cost_usd": 0.0}
