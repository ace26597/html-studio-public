"""The backup cost calculation (CONTRACT.md, "The backup calculation"): the shipped price list, how a
model's name is matched, the arithmetic, "complete or not at all", and the three places it is used.
Zero model calls and no network: the gateway is the stand-in from test_costs."""

import asyncio
import json
from pathlib import Path

import pytest

from test_costs import CONNECTION, KEY, SpendLog, events_of, row, use

from studio2_server import costs, gateway, prices
from studio2_server.costs import summarise
from studio2_server.harness.base import TurnResult

LIBRARY = Path(__file__).resolve().parents[1] / "library"
SHIPPED = prices.shipped(LIBRARY)
CURRENT = ("anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5",
           "anthropic/claude-fable-5.2", "openai/gpt-6.1-sol")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("TEST_SPEND_KEY", KEY)


def spent(turn, model, prompt, completion, cached=0, written=0, spend=0.0, provider="openai"):
    """A spend-log row as LiteLLM writes it: prompt tokens include the cached ones."""
    return {**row(turn, spend), "model": model, "custom_llm_provider": provider, "prompt_tokens": prompt,
            "completion_tokens": completion,
            "metadata": {"usage_object": {"prompt_tokens": prompt, "completion_tokens": completion,
                                          "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written}}}}


# ---- the list and the names ---------------------------------------------------------------------
def test_the_shipped_list_has_the_current_models_with_every_kind_of_rate():
    data = json.loads((LIBRARY / "model-prices.json").read_text("utf-8"))
    assert data["as_of"] and data["source"].startswith("https://") and len(data["models"]) > 100
    for name in CURRENT:
        rates = data["models"][name]
        assert all(isinstance(rates[kind], (int, float)) and rates[kind] > 0 for kind in prices.KINDS), name
        assert rates["cache_read"] < rates["input"] < rates["output"], name
    assert not [name for name in data["models"] if ":" in name]              # no batch or free variants
    above = data["models"]["anthropic/claude-haiku-5.5"]["above"]
    assert above["prompt_tokens"] == 100_000 and above["input"] > data["models"]["anthropic/claude-haiku-5.5"]["input"]
    assert above["source"].startswith("https://")


def test_a_model_is_found_by_its_whole_name_then_its_last_part():
    opus = SHIPPED.find("anthropic/claude-opus-5.5")
    assert opus and SHIPPED.find("openrouter/anthropic/claude-opus-5.5") is opus
    assert SHIPPED.find("claude-opus-5-5") is opus and SHIPPED.find("Claude-Opus-5-5-20260401") is opus
    assert SHIPPED.find("openrouter/openai/gpt-6.1-sol") is SHIPPED.find("gpt-6-1-sol")
    for unknown in ("opus", "gpt-x", "", None, "claude-opus-9-9"):
        assert SHIPPED.find(unknown) is None, unknown
    twice = prices.Rates({"a/same-model": {"input": 1.0, "output": 2.0}, "b/same-model": {"input": 3.0, "output": 4.0}}, "list")
    assert twice.find("same-model") is None and twice.find("a/same-model")["input"] == 1.0   # fits two: fits neither


# ---- the arithmetic -----------------------------------------------------------------------------
def test_a_real_request_comes_out_at_the_figure_the_provider_reported():
    """One request of 12 November 2026 on the owner's gateway, for which OpenRouter reported $0.2350585:
    92,814 prompt tokens of which 91,701 were written to the cache, 358 completion tokens."""
    tokens = prices.row_tokens(spent("t", "openrouter/openai/gpt-6.1-sol", 92_814, 358, written=91_701))
    assert tokens == {"input": 1_113, "cache_read": 0, "cache_write": 91_701, "cache_write_1h": None, "output": 358,
                      "prompt": 92_814}
    assert round(prices.cost(tokens, SHIPPED.find("openrouter/openai/gpt-6.1-sol")), 7) == 0.2350585


def test_complete_or_not_at_all():
    no_cache = {"input": 2.0, "output": 10.0, "cache_read": None, "cache_write": None, "above": None}
    plain = {"input": 1_000, "cache_read": 0, "cache_write": 0, "output": 100}
    read = {**plain, "cache_read": 50_000}
    assert prices.cost(plain, no_cache) == pytest.approx(0.003)
    assert prices.cost(read, no_cache) is None                      # never the input rate for cached tokens
    assert prices.cost(plain, None) is None
    gateway_rates = prices.Rates({"openrouter/openai/gpt-6.1-sol": no_cache}, "gateway")
    both = prices.Pricer(gateway_rates, SHIPPED)
    assert both.price("openrouter/openai/gpt-6.1-sol", plain) == pytest.approx(0.003)      # the gateway's own price first
    assert both.price("openrouter/openai/gpt-6.1-sol", read) == pytest.approx(0.008)       # incomplete there: the list
    assert both.price("no-such-model", plain) is None and both.price("gpt-6-1-sol", None) is None
    assert both.summary() == {"requests": 2, "usd": 0.011, "rates": "both", "as_of": SHIPPED.as_of}
    assert prices.Pricer().price("gpt-6-1-sol", plain) is None and prices.Pricer().summary() is None


def test_a_long_prompt_takes_the_higher_rate_where_the_model_has_one():
    haiku = SHIPPED.find("claude-haiku-5-5")
    short = {"input": 90_000, "cache_read": 0, "cache_write": 0, "output": 1_000, "prompt": 90_000}
    long = {"input": 150_000, "cache_read": 0, "cache_write": 0, "output": 1_000, "prompt": 150_000}
    assert prices.cost(short, haiku) == pytest.approx(90_000 * 0.10 / 1e6 + 1_000 * 0.50 / 1e6)
    assert prices.cost(long, haiku) == pytest.approx(150_000 * 0.50 / 1e6 + 1_000 * 2.50 / 1e6)


def test_what_a_gateway_says_about_its_own_prices():
    info = {"data": [
        {"model_name": "mine/fast", "model_info": {"input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05,
                                                   "cache_read_input_token_cost": 1e-07}},
        {"model_name": "plan-route", "model_info": {"input_cost_per_token": 0, "output_cost_per_token": 0}},
        {"model_name": "half", "model_info": {"input_cost_per_token": 1e-06}}, {"model_info": {}}, "junk"]}
    rates = prices.from_gateway(info)
    assert rates.find("mine/fast") == {"input": 2.0, "output": 10.0, "cache_read": 0.1, "cache_write": None,
                                       "cache_write_1h": None, "above": None}
    assert rates.find("plan-route") is None and rates.find("half") is None      # 0 and 0 is not a price
    for nothing in (None, {}, {"data": "x"}, []):
        assert prices.from_gateway(nothing).models == {}
    assert prices.shipped(Path("/nonexistent")).models == {}                    # an unreadable list is an empty one


# ---- a readable spend log -------------------------------------------------------------------------
def test_a_request_nobody_priced_is_priced_by_the_studio_and_said_so():
    keep = costs._has_tag("t_1")
    rows = [spent("t_1", "openrouter/openai/gpt-6.1-sol", 10_000, 500, spend=0.03),          # the gateway priced it
            spent("t_1", "openrouter/openai/gpt-6.1-sol", 92_814, 358, written=91_701),      # it did not
            spent("t_1", "gpt-x", 1_000, 100),                                               # nobody can
            spent("t_1", "openrouter/free", 1_000, 100, provider="openrouter")]              # reported as free below
    rows[3]["metadata"]["usage_object"]["cost"] = 0
    found = summarise(rows, keep, (SHIPPED,))
    assert found["requests"] == 4 and found["unpriced"] == 1 and found["basis"] == "backup"
    assert found["cost_usd"] == pytest.approx(0.03 + 0.2350585, abs=1e-6)
    assert found["backup"] == {"requests": 1, "usd": 0.235059, "rates": "list", "as_of": SHIPPED.as_of}
    without = summarise(rows, keep)                                  # no rates: as before the backup existed
    assert without["unpriced"] == 2 and without["backup"] is None and without["basis"] == "calculated"
    failed = summarise([{**spent("t_1", "openrouter/openai/gpt-6.1-sol", 9_000, 0), "status": "failure"}], keep, (SHIPPED,))
    assert failed == {"cost_usd": None, "requests": 0, "unpriced": 0, "basis": None, "backup": None}   # a failure is not billed


class Stub:
    """The agent on a gateway: token counts kept apart, and no price."""
    kind = "anthropic-gateway"

    async def run(self, request, emit):
        usage = {"input_tokens": 101_200, "output_tokens": 800, "cost_usd": None, "duration_ms": 900,
                 "cost_estimated": False, "tokens": {"input": 1_200, "cache_read": 100_000, "cache_write": 0, "output": 800}}
        return TurnResult("completed", "sess-1", None, usage)

    def describe(self, connection):
        return {"id": connection["id"], "label": connection["label"], "kind": connection["kind"], "available": True,
                "reason": None, "models": connection["models"], "default_model": connection["default_model"]}


SOL = {**CONNECTION, "models": [{"id": "openrouter/openai/gpt-6.1-sol", "label": "sol"}],
       "default_model": "openrouter/openai/gpt-6.1-sol"}


async def gateway_turn(client, studio, project, monkeypatch, log, connection=SOL):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [connection])
    studio.registry._harnesses["anthropic-gateway"] = Stub()
    use(studio, log)
    turn_id = (await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})).json()["turn_id"]
    return pid, turn_id


async def test_a_turn_with_an_unpriced_request_gets_the_backup_from_the_gateways_own_price(client, studio, project, monkeypatch):
    info = [{"model_name": "openrouter/openai/gpt-6.1-sol",
             "model_info": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1e-05}}]     # its own, dearer, price
    log = SpendLog([[]], model_info=info)
    pid, turn_id = await gateway_turn(client, studio, project, monkeypatch, log)
    log.reads = [[spent(turn_id, "openrouter/openai/gpt-6.1-sol", 10_000, 500, spend=0.03),
                  spent(turn_id, "openrouter/openai/gpt-6.1-sol", 1_000, 100)]]
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    (cost,) = events_of(studio, pid, "turn_cost")
    assert cost["source"] == "gateway" and cost["basis"] == "backup" and cost["unpriced"] == 0 and cost["requests"] == 2
    assert cost["cost_usd"] == pytest.approx(0.03 + 1_000 * 3 / 1e6 + 100 * 10 / 1e6)
    assert cost["backup"] == {"requests": 1, "usd": 0.004, "rates": "gateway", "as_of": None}
    assert log.price_reads == 1                                     # asked once, not at every read of the log
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["charged_usd"] == pytest.approx(0.034) and total["backup_usd"] == 0.004
    assert total["turns"][0]["basis"] == "backup" and total["turns"][0]["backup"]["requests"] == 1


# ---- a spend log that cannot be read ----------------------------------------------------------------
async def test_a_log_that_cannot_be_read_falls_back_to_the_turns_own_tokens(client, studio, project, monkeypatch):
    pid, turn_id = await gateway_turn(client, studio, project, monkeypatch, SpendLog([[]], status=403))
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    (cost,) = events_of(studio, pid, "turn_cost")
    expected = 1_200 * 2 / 1e6 + 100_000 * 0.1 / 1e6 + 800 * 10 / 1e6        # cached tokens at the cache-read rate
    assert cost["source"] == "tokens" and cost["basis"] == "backup" and cost["cost_usd"] == pytest.approx(expected)
    assert cost["backup"] == {"requests": None, "usd": round(expected, 6), "rates": "list", "as_of": SHIPPED.as_of}
    assert "403" in cost["reason"] and "own token counts" in cost["reason"] and "can be high" in cost["reason"]
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["turns"][0]["kind"] == "charged" and total["backup_usd"] == pytest.approx(expected, abs=1e-6)


async def test_no_rate_for_the_model_stays_unavailable(client, studio, project, monkeypatch):
    pid, turn_id = await gateway_turn(client, studio, project, monkeypatch, SpendLog([[]], status=404), CONNECTION)   # gpt-x
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    (cost,) = events_of(studio, pid, "turn_cost")
    assert cost["source"] == "unavailable" and cost["cost_usd"] is None and cost["backup"] is None


async def test_requests_that_all_failed_get_no_backup(client, studio, project, monkeypatch):
    log = SpendLog([[]])
    pid, turn_id = await gateway_turn(client, studio, project, monkeypatch, log)
    log.reads = [[{**spent(turn_id, "openrouter/openai/gpt-6.1-sol", 9_000, 0), "status": "failure"}]]
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    (cost,) = events_of(studio, pid, "turn_cost")
    assert cost["source"] == "unavailable" and cost["cost_usd"] is None and "nothing was billed" in cost["reason"]


# ---- the studio's own LiteLLM -----------------------------------------------------------------------
def test_the_studios_own_litellm_prices_what_litellm_could_not(settings):
    side = gateway.Sidecar(settings)
    side.folder.mkdir(parents=True, exist_ok=True)
    rows = [{"turn": "t_1", "model": "anthropic/claude-sonnet-5.5", "cost": 0.01, "reported": False, "input": 1_000, "output": 100},
            {"turn": "t_1", "model": "anthropic/claude-sonnet-5.5", "cost": None, "input": 60_000, "output": 1_000,
             "cached": 50_000, "cache_write": 0},
            {"turn": "t_1", "model": "anthropic/claude-sonnet-5.5", "cost": None, "input": 5_000, "output": 50},   # written before: no cache counts
            {"turn": "t_1", "model": "nobody/knows", "cost": None, "input": 5, "output": 5, "cached": 0, "cache_write": 0}]
    side.spend_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
    found = side.spend("t_1", (SHIPPED,))
    backup = 10_000 * 2 / 1e6 + 50_000 * 0.1 / 1e6 + 1_000 * 10 / 1e6
    assert found["requests"] == 4 and found["unpriced"] == 2 and found["basis"] == "backup"
    assert found["cost_usd"] == pytest.approx(0.01 + backup)
    assert found["backup"] == {"requests": 1, "usd": round(backup, 6), "rates": "list", "as_of": SHIPPED.as_of}
    assert side.spend("t_1")["unpriced"] == 3 and side.spend("t_1")["backup"] is None       # no rates given: as before
    assert "prompt_tokens_details" in gateway.SPEND_SOURCE and '"cached"' in gateway.SPEND_SOURCE
    compile(gateway.SPEND_SOURCE, "studio_spend.py", "exec")                                # what LiteLLM loads still parses


# ---- cache writes of two lifetimes -------------------------------------------------------------------
def test_a_one_hour_cache_write_has_its_own_rate_and_an_unknown_lifetime_is_not_priced():
    """A real request of 13 November 2026 on the Claude plan (Opus): 6 input, 117,340 read from the cache,
    91,210 written to it, 237 output. The agent program worked out $0.757912 for it, which is the 1-hour
    cache-write rate; at the 5-minute rate the figure would be $0.484282, 36% too low."""
    opus = SHIPPED.find("claude-opus-5-5")
    assert opus["cache_write_1h"] == 2 * opus["input"] and opus["cache_write"] == 1.25 * opus["input"]
    tokens = {"input": 6, "cache_read": 117_340, "cache_write": 91_210, "output": 237}
    assert round(prices.cost({**tokens, "cache_write_1h": 91_210}, opus), 6) == 0.757912
    assert round(prices.cost({**tokens, "cache_write_1h": 0}, opus), 6) == 0.484282
    assert prices.cost(tokens, opus) is None and prices.cost({**tokens, "cache_write_1h": None}, opus) is None
    assert prices.cost({**tokens, "cache_write": 0}, opus) is not None                 # nothing written: nothing to know
    half = prices.cost({**tokens, "cache_write_1h": 41_210}, opus)
    assert half == pytest.approx(0.484282 + 41_210 * (8 - 5) / 1e6, abs=1e-6)
    sol = SHIPPED.find("gpt-6-1-sol")                                                  # one rate whatever the lifetime
    assert sol["cache_write_1h"] is None and prices.cost(tokens, sol) is not None
    for name in ("anthropic/claude-sonnet-5.5", "anthropic/claude-fable-5.2", "anthropic/claude-haiku-5.5"):
        rates = SHIPPED.find(name)
        assert rates["cache_write_1h"] == pytest.approx(2 * rates["input"]), name


def test_the_lifetime_split_is_read_from_the_usage_where_it_is_given():
    assert prices.long_write({"cache_creation": {"ephemeral_1h_input_tokens": 900, "ephemeral_5m_input_tokens": 100}}) == 900
    assert prices.long_write({"cache_creation_token_details": {"ephemeral_1h_input_tokens": 0}}) == 0
    for unknown in (None, {}, {"cache_creation": None}, {"cache_creation": {"ephemeral_5m_input_tokens": 5}}, "x"):
        assert prices.long_write(unknown) is None
    claude = spent("t", "anthropic/claude-opus-5.5", 10_000, 100, written=9_000)
    assert prices.row_tokens(claude)["cache_write_1h"] is None and prices.Pricer(SHIPPED).row(claude) is None   # stays unpriced
    claude["metadata"]["usage_object"]["cache_creation_token_details"] = {"ephemeral_1h_input_tokens": 9_000}
    assert prices.Pricer(SHIPPED).row(claude) == pytest.approx(1_000 * 4 / 1e6 + 9_000 * 8 / 1e6 + 100 * 20 / 1e6)
    info = {"data": [{"model_name": "c", "model_info": {
        "input_cost_per_token": 4e-06, "output_cost_per_token": 2e-05, "cache_creation_input_token_cost": 5e-06,
        "cache_creation_input_token_cost_above_1hr": 8e-06}}]}
    assert prices.from_gateway(info).find("c")["cache_write_1h"] == 8.0


async def test_the_turns_own_tokens_carry_the_split_into_the_backup(client, studio, project, monkeypatch):
    opus = {**CONNECTION, "models": [{"id": "claude-opus-5-5", "label": "opus"}], "default_model": "claude-opus-5-5"}

    class Wrote(Stub):
        tokens = {"input": 6, "cache_read": 117_340, "cache_write": 91_210, "cache_write_1h": 91_210, "output": 237}

        async def run(self, request, emit):
            return TurnResult("completed", "sess-1", None, {"input_tokens": 208_556, "output_tokens": 237, "cost_usd": None,
                                                            "duration_ms": 900, "cost_estimated": False, "tokens": self.tokens})

    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [opus])
    studio.registry._harnesses["anthropic-gateway"] = Wrote()
    use(studio, SpendLog([[]], status=401))
    await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    (cost,) = events_of(studio, pid, "turn_cost")
    assert cost["source"] == "tokens" and cost["cost_usd"] == 0.757912
    # The same turn without the split: the lifetime is unknown, so no figure at all rather than a low one.
    Wrote.tokens = {key: value for key, value in Wrote.tokens.items() if key != "cache_write_1h"}
    await client.post(f"/api/projects/{pid}/turns", json={"text": "again"})
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    assert events_of(studio, pid, "turn_cost")[-1]["source"] == "unavailable"
