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
    assert "403" in cost["reason"] and "token counts" in cost["reason"] and "not the provider's bill" in cost["reason"]
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


def test_a_sum_over_a_turns_requests_is_not_one_long_prompt():
    """The reviewer's case: 20 requests of 32k prompt tokens on Haiku 5.5, which has a higher rate above
    100k. Priced as one 640k prompt it came to $0.10 for $0.02; the size of each request is not known."""
    haiku = SHIPPED.find("claude-haiku-5-5")
    turn = {"input": 640_000, "cache_read": 0, "cache_write": 0, "output": 2_000}
    assert prices.cost(turn, haiku, summed=True) is None                  # above the tier: unpriced
    small = {"input": 80_000, "cache_read": 0, "cache_write": 0, "output": 2_000}
    assert prices.cost(small, haiku, summed=True) == pytest.approx(80_000 * 0.10 / 1e6 + 2_000 * 0.50 / 1e6)
    # One request is still priced at the long-prompt rate when it is above the tier.
    one = {"input": 150_000, "cache_read": 0, "cache_write": 0, "output": 1_000, "prompt": 150_000}
    assert prices.cost(one, haiku) == pytest.approx(150_000 * 0.50 / 1e6 + 1_000 * 2.50 / 1e6)
    assert prices.Pricer(SHIPPED).row(spent("t", "anthropic/claude-haiku-5.5", 150_000, 1_000)) == \
        pytest.approx(150_000 * 0.50 / 1e6 + 1_000 * 2.50 / 1e6)
    # A model with no tier is priced from a sum as before.
    assert prices.cost(turn, SHIPPED.find("claude-opus-5-5"), summed=True) is not None


async def test_a_turn_priced_from_its_totals_on_a_tiered_model_is_unpriced_above_the_tier(client, studio, project, monkeypatch):
    haiku = {**CONNECTION, "models": [{"id": "claude-haiku-5-5", "label": "haiku"}], "default_model": "claude-haiku-5-5"}

    class Many(Stub):
        tokens = {"input": 640_000, "cache_read": 0, "cache_write": 0, "output": 2_000}

        async def run(self, request, emit):
            return TurnResult("completed", "sess-1", None, {"input_tokens": self.tokens["input"], "output_tokens": 2_000,
                                                            "cost_usd": None, "duration_ms": 900, "cost_estimated": False,
                                                            "tokens": dict(self.tokens)})

    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [haiku])
    studio.registry._harnesses["anthropic-gateway"] = Many()
    use(studio, SpendLog([[]], status=403))
    await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    assert events_of(studio, pid, "turn_cost")[-1]["source"] == "unavailable"     # not $0.10 for $0.02
    Many.tokens = {"input": 80_000, "cache_read": 0, "cache_write": 0, "output": 2_000}
    await client.post(f"/api/projects/{pid}/turns", json={"text": "again"})
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    cost = events_of(studio, pid, "turn_cost")[-1]
    assert cost["source"] == "tokens" and cost["cost_usd"] == pytest.approx(80_000 * 0.10 / 1e6 + 2_000 * 0.50 / 1e6)


# ---- a model the provider serves for free -------------------------------------------------------------------
def test_a_free_variant_is_never_matched_to_the_paid_model_of_that_name():
    assert SHIPPED.find("qwen/qwen3-coder") is not None
    assert SHIPPED.find("qwen/qwen3-coder:free") is None and SHIPPED.find("orconn/qwen/qwen3-coder:free") is None
    assert prices.last_part("openrouter/qwen/qwen3-coder:free") == "qwen3-coder:free"


def test_a_zero_on_a_free_model_is_a_price_of_zero_on_the_studios_own_litellm(settings):
    """The reviewer's case: LiteLLM said 0 for an OpenRouter `:free` model, and the studio priced its
    200k tokens in and 20k out at the paid model's rate ($0.08, shown as charged)."""
    side = gateway.Sidecar(settings)
    side.folder.mkdir(parents=True, exist_ok=True)
    free = {"turn": "t_free", "model": "orconn/qwen/qwen3-coder:free", "cost": 0.0, "reported": False,
            "input": 200_000, "output": 20_000, "cached": 0, "cache_write": 0}
    paid = {**free, "turn": "t_paid", "model": "orconn/qwen/qwen3-coder"}
    silent = {**free, "turn": "t_silent", "cost": None}
    side.spend_path.write_text("\n".join(json.dumps(r) for r in (free, paid, silent)) + "\n", "utf-8")
    assert side.spend("t_free", (SHIPPED,)) == {"cost_usd": 0.0, "requests": 1, "unpriced": 0, "basis": "reported",
                                                "backup": None}
    # A 0 on a model that is not free is LiteLLM having no price: the backup, as before.
    assert side.spend("t_paid", (SHIPPED,))["basis"] == "backup" and side.spend("t_paid", (SHIPPED,))["cost_usd"] > 0
    # No cost at all on a free model is not a reported 0: unpriced, never a guess.
    assert side.spend("t_silent", (SHIPPED,)) == {"cost_usd": None, "requests": 1, "unpriced": 1, "basis": None,
                                                  "backup": None}


def test_a_zero_on_a_free_model_is_a_price_of_zero_in_a_gateways_spend_log():
    keep = costs._has_tag("t_1")
    free = spent("t_1", "openrouter/qwen/qwen3-coder:free", 200_000, 20_000, provider="openai")
    assert summarise([free], keep, (SHIPPED,)) == {"cost_usd": 0.0, "requests": 1, "unpriced": 0, "basis": "reported",
                                                   "backup": None}
    paid = spent("t_1", "openrouter/qwen/qwen3-coder", 200_000, 20_000, provider="openai")
    assert summarise([paid], keep, (SHIPPED,))["basis"] == "backup"
    silent = {**free, "spend": None}                                 # no cost at all is not a reported 0
    assert summarise([silent], keep, (SHIPPED,))["unpriced"] == 1


def test_a_model_the_list_prices_at_zero_and_zero_is_free(tmp_path):
    (tmp_path / "model-prices.json").write_text(json.dumps({"as_of": "2026-11-17", "models": {
        "maker/gratis": {"input": 0, "output": 0}, "maker/paid": {"input": 1.0, "output": 2.0}}}))
    listed = prices.shipped(tmp_path)
    assert prices.free_model("maker/gratis", (listed,)) and not prices.free_model("maker/paid", (listed,))
    assert not prices.free_model("maker/gratis")                     # no list: only the name can say
    assert prices.free_model("meta/llama:free") and prices.free_model("openrouter/free")
    assert prices.free_request("maker/gratis", 0, (listed,)) and not prices.free_request("maker/gratis", None, (listed,))
    assert not prices.free_request("maker/paid", 0, (listed,))
    # A gateway's own 0 and 0 is a plan route's, not a price (CONTRACT.md): it does not make a model free.
    plan = prices.from_gateway({"data": [{"model_name": "plan-route",
                                          "model_info": {"input_cost_per_token": 0, "output_cost_per_token": 0}}]})
    assert not prices.free_model("plan-route", (plan,))


# `GET /model/info` as LiteLLM 1.102.1 answers it: `litellm_params` holds what the gateway's own
# configuration set, `model_info` that merged with LiteLLM's price list (which fills in 0 where it has none).
GRATIS = {"model_name": "mine/qwen3-coder",
          "litellm_params": {"model": "openai/qwen3-coder", "input_cost_per_token": 0, "output_cost_per_token": 0.0},
          "model_info": {"id": "a1", "input_cost_per_token": 0, "output_cost_per_token": 0}}
FILLED = {"model_name": "other/qwen3-coder", "litellm_params": {"model": "bedrock/qwen3-coder"},
          "model_info": {"id": "a2", "input_cost_per_token": 0, "output_cost_per_token": 0}}
HALF = {"model_name": "half/qwen3-coder", "litellm_params": {"model": "x/qwen3-coder", "input_cost_per_token": 0},
        "model_info": {"id": "a3", "input_cost_per_token": 0, "output_cost_per_token": 0}}


def test_a_model_the_gateways_own_settings_price_at_zero_and_zero_is_free():
    """The gateway says, in its own settings, that this route costs 0 and 0. Its spend of 0 was priced at
    the paid `qwen/qwen3-coder` rate of the shipped list (same last part), shown as charged."""
    gateway_rates = prices.from_gateway({"data": [GRATIS, FILLED, HALF]})
    rates = (gateway_rates, SHIPPED)
    assert SHIPPED.find("mine/qwen3-coder") is not None                         # the paid model of that name
    assert prices.free_model("mine/qwen3-coder", rates)
    keep = costs._has_tag("t_1")
    free = spent("t_1", "mine/qwen3-coder", 200_000, 20_000, provider="openai")
    assert summarise([free], keep, rates) == {"cost_usd": 0.0, "requests": 1, "unpriced": 0, "basis": "reported",
                                              "backup": None}
    # The turn's own token counts, when the spend log cannot be read: tokens times the gateway's 0.
    turn = {"input": 200_000, "cache_read": 5_000, "cache_write": 1_000, "output": 20_000}
    pricer = prices.Pricer(*rates)
    assert pricer.price("mine/qwen3-coder", turn, summed=True) == 0.0
    assert pricer.summary() == {"requests": 1, "usd": 0.0, "rates": "gateway", "as_of": None}
    # A 0 and 0 that only LiteLLM's merged `model_info` holds can mean "no price known": not free.
    for name in ("other/qwen3-coder", "half/qwen3-coder"):
        assert not prices.free_model(name, rates), name
        assert summarise([spent("t_1", name, 200_000, 20_000)], keep, rates)["basis"] == "backup", name


# ---- a long-prompt tier, whichever source has the price -----------------------------------------------------
# The fields as LiteLLM 1.102.1's own price list names them (claude-sonnet-4-5 there, per token).
SONNET_INFO = {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05, "cache_read_input_token_cost": 3e-07,
               "cache_creation_input_token_cost": 3.75e-06, "cache_creation_input_token_cost_above_1hr": 6e-06,
               "input_cost_per_token_above_200k_tokens": 6e-06, "output_cost_per_token_above_200k_tokens": 2.25e-05,
               "cache_read_input_token_cost_above_200k_tokens": 6e-07,
               "cache_creation_input_token_cost_above_200k_tokens": 7.5e-06,
               "cache_creation_input_token_cost_above_1hr_above_200k_tokens": 1.2e-05,
               "input_cost_per_token_above_272k_tokens_priority": 9e-06}          # a service tier, not a size: ignored


def test_a_gateways_own_price_carries_its_long_prompt_tier():
    rates = prices.from_gateway({"data": [{"model_name": "sonnet", "model_info": SONNET_INFO},
                                          {"model_name": "flash", "model_info": {
                                              "input_cost_per_token": 7.5e-08, "output_cost_per_token": 3e-07,
                                              "input_cost_per_token_above_128k_tokens": 1.5e-07}}]})
    assert rates.find("sonnet")["above"] == {"prompt_tokens": 200_000, "input": 6.0, "output": 22.5, "cache_read": 0.6,
                                             "cache_write": 7.5, "cache_write_1h": 12.0}
    assert rates.find("flash")["above"] == {"prompt_tokens": 128_000, "input": 0.15, "output": None, "cache_read": None,
                                            "cache_write": None, "cache_write_1h": None}
    one = {"input": 250_000, "cache_read": 0, "cache_write": 0, "output": 1_000, "prompt": 250_000}
    assert prices.cost(one, rates.find("sonnet")) == pytest.approx(250_000 * 6 / 1e6 + 1_000 * 22.5 / 1e6)
    assert prices.cost(one, rates.find("sonnet"), summed=True) is None
    # A tier with no output rate above the size: a request above it that wrote output is unpriced.
    assert prices.cost({**one, "prompt": 130_000, "input": 130_000}, rates.find("flash")) is None


def test_the_shipped_tier_holds_when_the_gateways_price_has_none():
    """The reviewer's case: the gateway prices Haiku 5.5 without a tier and is asked first, so a turn of
    640k summed prompt tokens came to $0.0645 at the ordinary rate."""
    gateway_rates = prices.from_gateway({"data": [{"model_name": "anthropic/claude-haiku-5.5", "model_info": {
        "input_cost_per_token": 1e-07, "output_cost_per_token": 5e-07}}]})
    turn = {"input": 640_000, "cache_read": 0, "cache_write": 0, "output": 1_000}
    assert prices.Pricer(gateway_rates, SHIPPED).price("anthropic/claude-haiku-5.5", turn, summed=True) is None
    small = {"input": 80_000, "cache_read": 0, "cache_write": 0, "output": 1_000}
    both = prices.Pricer(gateway_rates, SHIPPED)
    assert both.price("anthropic/claude-haiku-5.5", small, summed=True) == pytest.approx(80_000 * 0.1 / 1e6 + 1_000 * 0.5 / 1e6)
    assert both.summary()["rates"] == "gateway"                      # below the tier the gateway's own price stands
    # One request above the tier is not priced at the gateway's ordinary rate: the list's long-prompt rate prices it.
    one = {"input": 150_000, "cache_read": 0, "cache_write": 0, "output": 1_000, "prompt": 150_000}
    single = prices.Pricer(gateway_rates, SHIPPED)
    assert single.price("anthropic/claude-haiku-5.5", one) == pytest.approx(150_000 * 0.50 / 1e6 + 1_000 * 2.50 / 1e6)
    assert single.summary()["rates"] == "list"


# ---- a model that cannot take pictures (CONTRACT.md, same heading) -------------------------------
def _price_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("prices_update", LIBRARY.parent / "tools" / "prices" / "update.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_price_tool_marks_a_text_only_model_and_nothing_else():
    listing = {"data": [
        {"id": "a/text-only", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "architecture": {"input_modalities": ["text"]}},
        {"id": "a/sees", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "a/says-nothing", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
    ]}
    models = _price_tool().build(listing, "2026-11-19")["models"]
    assert models["a/text-only"]["images"] is False
    assert "images" not in models["a/sees"] and "images" not in models["a/says-nothing"]


def test_the_shipped_list_knows_which_current_models_take_text_only():
    data = json.loads((LIBRARY / "model-prices.json").read_text("utf-8"))["models"]
    assert data["deepseek/deepseek-v4-pro-0813"]["images"] is False and data["z-ai/glm-5.3"]["images"] is False
    for sees in ("anthropic/claude-opus-5.5", "openai/gpt-6.1-sol", "z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash"):
        assert "images" not in data[sees], sees


def test_only_a_known_text_only_model_on_a_translated_connection_is_kept_from_pictures():
    gateway_connection = {"id": "g", "kind": "anthropic-gateway"}
    assert prices.takes_pictures(gateway_connection, "openrouter/deepseek/deepseek-v4-pro-0813", SHIPPED) is False
    assert prices.takes_pictures({"id": "l", "kind": "litellm"}, "openrouter/z-ai/glm-5.3", SHIPPED) is False
    assert prices.takes_pictures(gateway_connection, "openrouter/z-ai/glm-5.3-flash", SHIPPED) is True
    assert prices.takes_pictures(gateway_connection, "a-model-nobody-knows", SHIPPED) is True      # as before
    assert prices.takes_pictures(gateway_connection, None, SHIPPED) is True
    # The person can name one the list does not know; it is matched as the connection spells it.
    named = {**gateway_connection, "text_only_models": ["my-local-model"]}
    assert prices.takes_pictures(named, "my-local-model", SHIPPED) is False
    assert prices.takes_pictures(named, "my-other-model", SHIPPED) is True
    # Claude's own connections are never looked at, whatever the list says about a name.
    for kind in ("claude-subscription", "anthropic-api", "bedrock"):
        assert prices.takes_pictures({"id": "c", "kind": kind}, "deepseek-v4-pro-0813", SHIPPED) is True
