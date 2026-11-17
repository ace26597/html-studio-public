"""Cost per request and per project (CONTRACT.md, "Cost per request and per project").

Zero model calls: the gateway is a stand-in that answers like LiteLLM's /spend/logs/v2, and the
agent harness for a gateway connection is a stub that reports tokens and no price.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from studio2_server import costs, gateway
from studio2_server.costs import CURRENT_TURN, Costs, summarise, turn_header
from studio2_server.harness import claude
from studio2_server.harness.base import TurnRequest, TurnResult

KEY = "sk-spend-test"
CONNECTION = {"id": "mine", "label": "Mine", "kind": "anthropic-gateway", "base_url": "http://spend.test:4000",
              "api_key_env": "TEST_SPEND_KEY", "models": [{"id": "gpt-x", "label": "gpt-x"}], "default_model": "gpt-x"}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("TEST_SPEND_KEY", KEY)


def row(turn=None, spend=0.0, status="success", session="s1", start="2026-11-12T19:00:30.000000Z", tags=None):
    tagged = list(tags if tags is not None else ([costs.TAG_PREFIX + turn] if turn else []))
    return {"request_tags": ["User-Agent: claude-cli", *tagged], "spend": spend, "status": status,
            "session_id": session, "startTime": start, "prompt_tokens": 10, "completion_tokens": 5}


class SpendLog:
    """A stand-in for LiteLLM's paginated spend log: what each read returns, in order."""

    def __init__(self, reads, status=200, model_info=()):
        self.reads, self.status, self.requests = list(reads), status, []
        self.model_info, self.price_reads = list(model_info), 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.method == "GET"          # read-only
        if request.url.path == "/model/info":   # the gateway's own prices, for the backup calculation
            self.price_reads += 1
            return httpx.Response(200, json={"data": self.model_info})
        self.requests.append(request)
        assert request.url.path == "/spend/logs/v2"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "no"})
        rows = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params["page_size"])
        chunk = rows[(page - 1) * size: page * size]
        return httpx.Response(200, json={"data": chunk, "total": len(rows), "page": page, "page_size": size,
                                         "total_pages": max(1, -(-len(rows) // size))})


def use(studio, spend_log, delays=(0.0, 0.0, 0.0, 0.0)):
    studio.costs.client_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(spend_log))
    studio.costs.delays = delays

    async def no_wait(_seconds):
        return None

    studio.costs.sleep = no_wait


def events_of(studio, pid, kind):
    return [e for e in studio.log(pid).read() if e["type"] == kind]


# ---- the pieces ------------------------------------------------------------------------------------
def test_headers_tie_a_route_to_the_turn_and_say_nothing_else():
    assert turn_header("anthropic-gateway", "t_ab12cd34") == "x-litellm-tags: studio-turn:t_ab12cd34"
    assert turn_header("litellm", "t_ab12cd34") == "x-studio-turn: t_ab12cd34"
    assert turn_header("litellm", "t_1").split(":")[0] == gateway.TURN_HEADER   # what the studio's LiteLLM reads
    for kind in ("claude-subscription", "anthropic-api", "bedrock", "vertex", "foundry", "fake"):
        assert turn_header(kind, "t_1") is None        # these report their own cost
    assert turn_header("anthropic-gateway", None) is None


def test_summarise_never_turns_missing_prices_into_zero():
    rows = [row("t_1", 0.5), row("t_1", 0.25), row("t_1", 0.0), row("t_1", None),
            row("t_1", 0.0, status="failure"),        # a failed request nobody billed is not a request
            row("t_1", 0.1, status="failure"),        # but a price that was reported is a price
            row("t_2", 9.0)]
    assert summarise(rows, costs._has_tag("t_1")) == {"cost_usd": 0.85, "requests": 5, "unpriced": 2,
                                                       "basis": "calculated", "backup": None}
    nothing = summarise([row("t_1", 0.0), row("t_1", None)], costs._has_tag("t_1"))
    assert nothing == {"cost_usd": None, "requests": 2, "unpriced": 2, "basis": None, "backup": None}       # "nobody said", not "free"
    assert summarise([], costs._has_tag("t_1")) == {"cost_usd": None, "requests": 0, "unpriced": 0, "basis": None, "backup": None}


def test_a_figure_says_whether_the_provider_reported_it_or_it_was_calculated():
    def sent(turn, spend, provider_cost=None):
        return {**row(turn, spend), "metadata": {"usage_object": {"cost": provider_cost} if provider_cost is not None else {}}}

    keep = costs._has_tag("t_1")
    assert summarise([sent("t_1", 0.5, 0.5), sent("t_1", 0.25, 0.25)], keep)["basis"] == "reported"
    assert summarise([sent("t_1", 0.5, 0.5), sent("t_1", 0.25)], keep)["basis"] == "calculated"   # the weaker word wins
    assert summarise([sent("t_1", 0.5), sent("t_1", 0.0, 0)], keep)["basis"] == "calculated"
    assert summarise([{**row("t_1", 0.5), "metadata": None}], keep)["basis"] == "calculated"


def test_a_tag_matches_exactly_not_by_prefix():
    keep = costs._has_tag("t_abc")
    assert keep(row("t_abc")) and not keep(row("t_abcd")) and not keep(row(None))


# ---- reading it back -------------------------------------------------------------------------------
async def test_a_gateway_turn_is_priced_from_its_tagged_rows_once_two_reads_agree(studio, project):
    pid = project["id"]
    late = [row("t_one", 1.5), row("t_one", 2.0), row("t_one", 0.0), row("t_other", 40.0),
            row("t_oneish", 7.0), row(None, 3.0)]
    log = SpendLog([[], [late[0]], late[:3] + late[3:], late[:3] + late[3:]])
    use(studio, log)
    studio.costs.settle(pid, "t_one", CONNECTION, "2026-11-12T19:00:00.000Z")
    await asyncio.gather(*studio.costs.settling.values())

    (event,) = events_of(studio, pid, "turn_cost")
    assert event["turn_id"] == "t_one" and event["source"] == "gateway"
    assert (event["cost_usd"], event["requests"], event["unpriced"]) == (3.5, 3, 1)
    assert len(log.requests) == 4        # empty, partial, full, full again: two equal reads end it
    params = log.requests[0].url.params   # UTC, a minute before the turn began, two minutes after "now"
    assert params["start_date"] == "2026-11-12 18:59:00" and params["page_size"] == "1000"
    assert datetime.strptime(params["end_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)


async def test_a_long_log_is_read_page_by_page(studio, project):
    pid = project["id"]
    rows = [row("t_big", 0.01) for _ in range(2300)]
    log = SpendLog([rows])
    use(studio, log)
    studio.costs.settle(pid, "t_big", CONNECTION, "2026-11-12T19:00:00.000Z")
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert event["requests"] == 2300 and event["cost_usd"] == 23.0
    assert sorted({int(r.url.params["page"]) for r in log.requests}) == [1, 2, 3]


@pytest.mark.parametrize("status, words", [(401, "refused"), (403, "refused"), (404, "no spend log"),
                                            (500, "HTTP 500")])
async def test_a_gateway_that_will_not_show_its_log_leaves_the_turn_unpriced_and_says_why(studio, project, status, words):
    pid = project["id"]
    use(studio, SpendLog([[]], status=status))
    studio.costs.settle(pid, "t_x", CONNECTION, None)
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert event["source"] == "unavailable" and event["cost_usd"] is None and words in event["reason"]


async def test_nothing_tagged_in_the_log_is_unavailable_not_free(studio, project):
    pid = project["id"]
    use(studio, SpendLog([[row("t_else", 1.0), row(None, 2.0)]]))
    studio.costs.settle(pid, "t_x", CONNECTION, None)
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert event["cost_usd"] is None and event["requests"] == 0 and "no request tagged" in event["reason"]


async def test_requests_that_all_failed_are_said_to_have_billed_nothing(studio, project):
    pid = project["id"]
    use(studio, SpendLog([[row("t_f", 0.0, status="failure"), row("t_f", 0.0, status="failure")]]))
    studio.costs.settle(pid, "t_f", CONNECTION, None)
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert event["cost_usd"] is None and "2 requests for this turn" in event["reason"] and "nothing was billed" in event["reason"]


async def test_requests_the_gateway_could_not_price_stay_unpriced(studio, project):
    pid = project["id"]
    use(studio, SpendLog([[row("t_u", 0.0), row("t_u", None)]]))
    studio.costs.settle(pid, "t_u", CONNECTION, None)
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert (event["cost_usd"], event["requests"], event["unpriced"], event["source"]) == (None, 2, 2, "gateway")


async def test_a_connection_without_a_key_or_address_is_unavailable(studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.delenv("TEST_SPEND_KEY")
    use(studio, SpendLog([[]]))
    studio.costs.settle(pid, "t_k", CONNECTION, None)
    await asyncio.gather(*studio.costs.settling.values())
    (event,) = events_of(studio, pid, "turn_cost")
    assert event["source"] == "unavailable" and "no address or key" in event["reason"]


async def test_a_deleted_project_is_left_alone(client, studio, project):
    pid = project["id"]
    use(studio, SpendLog([[row("t_d", 1.0)]]))
    studio.costs.settle(pid, "t_d", CONNECTION, None)
    await client.delete(f"/api/projects/{pid}")
    await asyncio.gather(*studio.costs.settling.values())
    assert not (studio.store.root / pid).exists()        # no event log resurrected for it


# ---- a whole turn on a gateway connection --------------------------------------------------------------
class Stub:
    """The agent for a gateway connection: tokens, and no price (the agent program cannot know it)."""
    kind = "anthropic-gateway"

    async def run(self, request, emit):
        self.request = request
        usage = {"input_tokens": 1200, "output_tokens": 80, "cost_usd": None, "duration_ms": 900, "cost_estimated": False}
        return TurnResult("completed", "sess-1", None, usage)

    def describe(self, connection):
        return {"id": connection["id"], "label": connection["label"], "kind": connection["kind"], "available": True,
                "reason": None, "models": connection["models"], "default_model": connection["default_model"]}


async def test_a_turn_on_a_gateway_says_pending_then_gets_its_price(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    studio.registry._harnesses["anthropic-gateway"] = Stub()
    log = SpendLog([[]])
    use(studio, log)
    turn_id = (await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})).json()["turn_id"]
    log.reads = [[row(turn_id, 0.75), row(turn_id, 0.25)]]
    await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())

    finished = next(e for e in events_of(studio, pid, "turn_finished") if e["turn_id"] == turn_id)
    assert finished["usage"]["cost_pending"] is True and finished["usage"]["cost_usd"] is None
    (cost,) = events_of(studio, pid, "turn_cost")
    assert cost["turn_id"] == turn_id and cost["cost_usd"] == 1.0
    assert cost["seq"] > finished["seq"]                      # after turn_finished, which was not held back
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["charged_usd"] == 1.0 and total["plan_equivalent_usd"] == 0 and total["pending_turns"] == 0
    assert [t["kind"] for t in total["turns"]] == ["charged"]


async def test_a_restart_before_the_read_back_ended_picks_it_up_again(studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    events = studio.log(pid)
    events.append("turn_started", "t_old", text="x", connection="mine", model="gpt-x")
    events.append("turn_finished", "t_old", status="completed", error=None, session_id="s1",
                  usage={"input_tokens": 5, "output_tokens": 5, "cost_usd": None, "cost_pending": True})
    events.append("turn_started", "t_done", text="x", connection="mine", model="gpt-x")
    events.append("turn_finished", "t_done", status="completed", error=None, session_id="s1",
                  usage={"input_tokens": 5, "output_tokens": 5, "cost_usd": None, "cost_pending": True})
    events.append("turn_cost", "t_done", cost_usd=2.0, requests=1, unpriced=0, source="gateway", reason=None)
    use(studio, SpendLog([[row("t_old", 0.4), row("t_done", 99.0)]]))
    assert [t["turn_id"] for t in studio.costs.pending_turns(pid)] == ["t_old"]
    studio.costs.reconcile(pid)
    await asyncio.gather(*studio.costs.settling.values())
    assert [(e["turn_id"], e["cost_usd"]) for e in events_of(studio, pid, "turn_cost")] == [("t_done", 2.0), ("t_old", 0.4)]


# ---- turns from before the tag ---------------------------------------------------------------------------
def old_turn(events, turn, start, end, session="5217", connection="mine"):
    events.append("turn_started", turn, text="x", connection=connection, model="gpt-x")
    events.append("turn_finished", turn, status="completed", error=None, session_id=session,
                  usage={"input_tokens": 9, "output_tokens": 9, "cost_usd": None, "cost_estimated": False})


async def test_recovery_prices_old_turns_only_from_rows_of_their_session_and_window(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    events = studio.log(pid)
    old_turn(events, "t_a", 0, 0)
    old_turn(events, "t_b", 0, 0, session="9999")
    old_turn(events, "t_c", 0, 0, connection="gone")
    # Make the stored times what the gateway log was written against.
    path = studio.store.events_path(pid)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    stamps = {("turn_started", "t_a"): "2026-11-12T19:00:22.874Z", ("turn_finished", "t_a"): "2026-11-12T19:25:40.000Z",
              ("turn_started", "t_b"): "2026-11-12T20:00:00.000Z", ("turn_finished", "t_b"): "2026-11-12T20:05:00.000Z",
              ("turn_started", "t_c"): "2026-11-12T19:39:00.000Z", ("turn_finished", "t_c"): "2026-11-12T19:45:00.000Z"}
    for line in lines:
        line["ts"] = stamps.get((line["type"], line["turn_id"]), line["ts"])
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    studio.hub.drop(pid)

    inside = [row(None, 2.0, session="5217", start="2026-11-12T19:00:24.853000Z", tags=[]),
              row(None, 2.25, session="5217", start="2026-11-12T19:25:39.802000Z", tags=[])]
    outside = [row(None, 50.0, session="5217", start="2026-11-12T19:40:00.000000Z", tags=[]),     # a later turn
               row(None, 60.0, session="other", start="2026-11-12T19:10:00.000000Z", tags=[])]    # another session
    use(studio, SpendLog([inside + outside]))
    report = await studio.costs.recover(pid)
    assert report["recovered"] == 1 and report["unmatched"] == 1        # t_b has no row; t_c's connection is gone
    (written,) = events_of(studio, pid, "turn_cost")
    assert written["turn_id"] == "t_a" and written["source"] == "recovered"
    assert (written["cost_usd"], written["requests"]) == (4.25, 2)


async def test_recovery_gives_each_row_to_one_turn(studio, project, monkeypatch):
    """The reviewer's case: a queued follow-up starts a second after the turn before it ends, so their
    padded windows overlapped and a row was counted in both ($5 for $3 spent)."""
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    events = studio.log(pid)
    times = {"t_1": ("10:00:00", "10:00:30"), "t_2": ("10:00:31", "10:01:00"),      # back to back
             "t_3": ("10:05:00", "10:05:20"), "t_4": ("10:05:21", "10:05:40")}      # after a gap; t_4 already priced
    for turn in times:
        old_turn(events, turn, 0, 0)
    events.append("turn_cost", "t_4", cost_usd=0.5, requests=1, unpriced=0, source="gateway", reason=None,
                  basis="reported", backup=None)
    path = studio.store.events_path(pid)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    for line in lines:
        if line["turn_id"] in times and line["type"] in ("turn_started", "turn_finished"):
            line["ts"] = f"2026-11-12T{times[line['turn_id']][line['type'] == 'turn_finished']}.000Z"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    studio.hub.drop(pid)

    def at(spend, clock, tags=()):
        return row(None, spend, session="5217", start=f"2026-11-12T{clock}.000000Z", tags=list(tags))

    rows = [at(1.0, "10:00:10"), at(2.0, "10:00:35"),               # one in each of the back-to-back turns
            at(4.0, "10:00:25", [costs.TAG_PREFIX + "t_other"]),     # another turn's tag: nobody's here
            at(8.0, "10:00:20", [costs.TAG_PREFIX + "t_1"]),         # its own tag: its own
            at(16.0, "10:01:05"),                                     # just after t_2 ended: the gap is t_2's
            at(32.0, "10:04:57"),                                     # just before t_3 started, after a long gap
            at(64.0, "10:05:25"),                                     # inside t_4, which t_3's padding reached
            # A gateway whose clock runs ahead logs t_1's last request just past t_2's start: its tag says
            # whose it is, whatever the time (the second review's case: nobody took it).
            {**at(128.0, "10:00:31", [costs.TAG_PREFIX + "t_1"]), "startTime": "2026-11-12T10:00:31.500000Z"},
            at(256.0, "10:05:10", [costs.TAG_PREFIX + "t_1"]),        # tagged t_1, in t_3's window: still t_1's
            {**at(512.0, "10:00:20", [costs.TAG_PREFIX + "t_1"]), "session_id": "other"}]   # another session: nobody's
    use(studio, SpendLog([rows]))
    report = await studio.costs.recover(pid)
    assert report["recovered"] == 3
    found = {e["turn_id"]: e["cost_usd"] for e in events_of(studio, pid, "turn_cost") if e["source"] == "recovered"}
    assert found == {"t_1": 393.0, "t_2": 18.0, "t_3": 32.0}
    # Nothing counted twice: what is left is the other turn's row, the one of t_4 (already priced) and
    # the row of another session.
    assert sum(found.values()) + 4.0 + 64.0 + 512.0 == sum(r["spend"] for r in rows)


async def test_recovery_with_no_matching_row_writes_nothing(studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    old_turn(studio.log(pid), "t_a", 0, 0)
    use(studio, SpendLog([[row(None, 5.0, session="elsewhere", tags=[])]]))
    report = await studio.costs.recover(pid)
    assert report["recovered"] == 0 and report["unmatched"] == 1 and not events_of(studio, pid, "turn_cost")


async def test_recovery_reports_a_log_it_cannot_read(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [CONNECTION])
    old_turn(studio.log(pid), "t_a", 0, 0)
    use(studio, SpendLog([[]], status=404))
    body = (await client.post(f"/api/projects/{pid}/cost/recover")).json()
    assert body["recovered"] == 0 and "no spend log" in body["reason"]


async def test_recovery_waits_for_a_running_turn_and_unknown_projects_are_404(client, studio, project):
    pid = project["id"]
    studio.turns.active[pid] = "t_running"
    busy = await client.post(f"/api/projects/{pid}/cost/recover")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_running"
    studio.turns.active.pop(pid)
    assert (await client.get("/api/projects/p_00000000/cost")).status_code == 404
    assert (await client.post("/api/projects/p_00000000/cost/recover")).status_code == 404


# ---- the project's total -----------------------------------------------------------------------------------
async def test_the_project_total_keeps_charged_plan_and_unpriced_apart(client, studio, project):
    pid = project["id"]
    events = studio.log(pid)

    def turn(name, usage, model="m", cost=None):
        events.append("turn_started", name, text="x", connection="c", model=model)
        events.append("turn_finished", name, status="completed", error=None, usage=usage, session_id="s")
        if cost:
            events.append("turn_cost", name, **cost)

    tokens = {"input_tokens": 10, "output_tokens": 5}
    turn("t_plan", {**tokens, "cost_usd": 1.02, "cost_estimated": True})                  # the subscription
    turn("t_plan2", {**tokens, "cost_usd": 0.5, "cost_estimated": True})
    turn("t_lite", {**tokens, "cost_usd": 0.10, "cost_estimated": False, "requests": 4, "unpriced_requests": 1})
    turn("t_old", {**tokens, "cost_usd": 7.0})                                               # before Step 2: no flag
    turn("t_nobody", {**tokens, "cost_usd": None, "cost_estimated": False, "requests": 3})  # no price at all
    turn("t_wait", {**tokens, "cost_usd": None, "cost_pending": True})
    turn("t_gw", {**tokens, "cost_usd": None, "cost_pending": True},
         cost={"cost_usd": 4.446269, "requests": 193, "unpriced": 0, "source": "recovered", "reason": None})
    turn("t_dead", {"input_tokens": None, "output_tokens": None, "cost_usd": None})        # restarted: asked nothing
    events.append("turn_started", "t_running", text="x", connection="c", model="m")         # not finished yet

    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["charged_usd"] == pytest.approx(4.546269)            # 0.10 + 4.446269
    assert total["plan_equivalent_usd"] == pytest.approx(1.52)         # never added into the charged sum
    assert total["unpriced_turns"] == 2 and total["unpriced_requests"] == 1 + 3
    assert total["pending_turns"] == 1
    kinds = {t["turn_id"]: t["kind"] for t in total["turns"]}
    assert kinds == {"t_plan": "plan", "t_plan2": "plan", "t_lite": "charged", "t_old": "unpriced",
                     "t_nobody": "unpriced", "t_wait": "pending", "t_gw": "charged"}
    assert next(t for t in total["turns"] if t["turn_id"] == "t_gw")["source"] == "recovered"
    assert next(t for t in total["turns"] if t["turn_id"] == "t_old")["cost_usd"] is None


async def test_an_empty_project_costs_nothing_and_says_so(client, project):
    total = (await client.get(f"/api/projects/{project['id']}/cost")).json()
    assert total == {"charged_usd": 0, "calculated_usd": 0, "backup_usd": 0, "plan_equivalent_usd": 0, "unpriced_turns": 0,
                     "unpriced_requests": 0, "pending_turns": 0, "turns": []}


# ---- what goes out ------------------------------------------------------------------------------------------
async def test_the_agent_for_a_gateway_sends_the_tag_and_the_studios_litellm_its_own_header(monkeypatch):
    seen = {}

    async def attempt(self, request, emit, env, scrub, resume):
        seen.update(env)
        return TurnResult("completed", "s", None), 1

    async def fine(*_a, **_k):
        return None

    monkeypatch.setattr(claude.ClaudeHarness, "_attempt", attempt)
    monkeypatch.setattr(claude, "gateway_problem", fine)
    harness = claude.ClaudeHarness("anthropic-gateway", None, None, lambda: [])
    request = TurnRequest(project_id="p_1", turn_id="t_abc12345", workspace=Path("."), prompt="x", session_id=None,
                          connection=CONNECTION, model="gpt-x")
    await harness.run(request, lambda _e: None)
    assert seen["ANTHROPIC_CUSTOM_HEADERS"] == "x-litellm-tags: studio-turn:t_abc12345"
    assert "p_1" not in json.dumps(seen["ANTHROPIC_CUSTOM_HEADERS"])         # no project, path or text in it


async def test_the_choosing_call_is_a_request_of_its_turn(studio, project, monkeypatch):
    seen = {}

    async def pick(*_a, **_k):
        seen["turn"] = CURRENT_TURN.get()
        return {"skills": [], "connectors": [], "note": None, "mode": "words"}

    monkeypatch.setattr(studio.picker, "pick", pick)
    try:
        await studio.prepare_turn(project["id"], "t_pick0001", "hello", studio.registry.resolve(None))
    except Exception:       # only the context at the picking call matters here
        pass
    assert seen["turn"] == "t_pick0001" and CURRENT_TURN.get() is None       # and it does not leak after


async def test_the_cheap_call_carries_the_current_turns_tag(monkeypatch):
    from studio2_server.harness import cheap
    captured = {}

    class Client:
        def __init__(self, options):
            captured["env"] = options.env

        async def connect(self):
            raise RuntimeError("stop here")

        async def disconnect(self):
            return None

    monkeypatch.setattr(cheap, "ClaudeSDKClient", Client)
    token = CURRENT_TURN.set("t_cheap001")
    try:
        with pytest.raises(cheap.CheapCallFailed):
            await cheap.ask(CONNECTION, "s", "p", 5.0)
    finally:
        CURRENT_TURN.reset(token)
    assert captured["env"]["ANTHROPIC_CUSTOM_HEADERS"] == "x-litellm-tags: studio-turn:t_cheap001"
    captured.clear()
    with pytest.raises(cheap.CheapCallFailed):       # outside a turn there is nothing to tag
        await cheap.ask(CONNECTION, "s", "p", 5.0)
    assert "ANTHROPIC_CUSTOM_HEADERS" not in captured["env"]


# ---- the agent program's running total ------------------------------------------------------------------------
def test_a_turns_part_of_the_conversations_running_total():
    prior = {"session_id": "s1", "total_usd": 10.6}
    assert costs.per_turn(10.99, "s1", prior) == 0.39
    assert costs.per_turn(10.99, "s1", None) == 10.99            # the first turn of a conversation
    assert costs.per_turn(0.5, "s2", prior) == 0.5               # a new conversation starts from nothing
    assert costs.per_turn(0.2, "s1", prior) == 0.2               # a total that went down is a fresh count
    assert costs.per_turn(10.6, "s1", prior) == 0.0


class Plan:
    """The agent on the Claude plan: like the real one, it reports the conversation's running total."""
    kind = "claude-subscription"

    def __init__(self):
        self.total = 0.0

    async def run(self, request, emit):
        self.total += {0.0: 10.6, 10.6: 0.39}.get(self.total, 0.66)
        usage = {"input_tokens": 100, "output_tokens": 10, "cost_usd": round(self.total, 2), "duration_ms": 5,
                 "cost_estimated": True}
        return TurnResult("completed", "sess-plan", None, usage)

    describe = Stub.describe


async def test_each_turn_keeps_its_own_part_and_the_project_total_is_not_counted_twice(client, studio, project, monkeypatch):
    pid = project["id"]
    plan = {**CONNECTION, "id": "plan", "kind": "claude-subscription"}
    monkeypatch.setattr(studio.registry, "connections", lambda: [plan])
    studio.registry._harnesses["claude-subscription"] = Plan()
    for text in ("one", "two", "three"):
        await client.post(f"/api/projects/{pid}/turns", json={"text": text})
        await studio.turns.wait(pid)
    usages = [e["usage"] for e in events_of(studio, pid, "turn_finished")]
    assert [u["cost_usd"] for u in usages] == [10.6, 0.39, 0.66]
    assert [u["session_cost_usd"] for u in usages] == [10.6, 10.99, 11.65]
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["plan_equivalent_usd"] == pytest.approx(11.65) and total["charged_usd"] == 0
    assert studio.store.get(pid)["session_cost"] == {"session_id": "sess-plan", "total_usd": 11.65}


class Conversation:
    """One conversation of the agent program that moves between connections: a changed connection
    resumes the same session (turns.start passes the project's session id whatever the connection),
    and the program's `total_cost_usd` keeps running. Each kind's usage is what ClaudeHarness hands
    back for it: the plan's and a key's total as `cost_usd`, a gateway's own figure (or none) with
    the program's total beside it."""

    def __init__(self, steps):
        self.steps, self.total = list(steps), 0.0      # (kind, what the agent program's total grows by, gateway figure)

    def harness(self, kind):
        conversation = self

        class Harness:
            async def run(self, request, emit):
                expected, grows, figure = conversation.steps.pop(0)
                assert request.connection["kind"] == expected and request.session_id in (None, "sess-1")
                conversation.total = round(conversation.total + grows, 6)
                usage = {"input_tokens": 100, "output_tokens": 10, "duration_ms": 5}
                if kind in costs.SELF_PRICED:
                    usage.update(cost_usd=figure, cost_estimated=False, session_cost_usd=conversation.total)
                    if figure is not None:
                        usage.update(cost_basis="reported", requests=1, unpriced_requests=0)
                else:
                    usage.update(cost_usd=conversation.total, cost_estimated=kind == "claude-subscription")
                return TurnResult("completed", "sess-1", None, usage)

            describe = Stub.describe

        Harness.kind = kind
        return Harness()


@pytest.mark.parametrize("gateway_kind", ["litellm", "anthropic-gateway"])
async def test_a_gateway_turn_in_the_middle_of_a_conversation_is_counted_once(client, studio, project, monkeypatch,
                                                                              gateway_kind):
    pid = project["id"]
    plan = {**CONNECTION, "id": "plan", "kind": "claude-subscription"}
    key = {**CONNECTION, "id": "key", "kind": "anthropic-api"}
    via = {**CONNECTION, "id": "via", "kind": gateway_kind}
    monkeypatch.setattr(studio.registry, "connections", lambda: [plan, key, via])
    # The plan reaches $1; the gateway turn adds $0.50 to the program's total (its own guess at a price it
    # cannot know) and the gateway says $0.30; the key turn costs $0.20; the plan again $0.10.
    gateway_figure = 0.3 if gateway_kind == "litellm" else None    # an anthropic-gateway turn's comes later, read back
    talk = Conversation([("claude-subscription", 1.0, None), (gateway_kind, 0.5, gateway_figure),
                         ("anthropic-api", 0.2, None), ("claude-subscription", 0.1, None)])
    for kind in ("claude-subscription", "anthropic-api", gateway_kind):
        studio.registry._harnesses[kind] = talk.harness(kind)
    use(studio, SpendLog([[]], status=404))       # an anthropic-gateway turn's read-back finds nothing
    for connection in ("plan", "via", "key", "plan"):
        await client.post(f"/api/projects/{pid}/turns", json={"text": "go", "connection": connection})
        await studio.turns.wait(pid)
    await asyncio.gather(*studio.costs.settling.values())
    usages = [e["usage"] for e in events_of(studio, pid, "turn_finished")]
    assert [u["cost_usd"] for u in usages] == [1.0, gateway_figure, 0.2, 0.1]     # not 0.7 and not 0.6
    assert [u["session_cost_usd"] for u in usages] == [1.0, 1.5, 1.7, 1.8]
    assert studio.store.get(pid)["session_cost"] == {"session_id": "sess-1", "total_usd": 1.8}
    # The project's total, replayed from the log, agrees with what each turn recorded.
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert [t["cost_usd"] for t in total["turns"]] == [1.0, gateway_figure, 0.2, 0.1]
    assert total["plan_equivalent_usd"] == pytest.approx(1.1)
    assert total["charged_usd"] == pytest.approx(0.5 if gateway_kind == "litellm" else 0.2)


async def test_the_replay_reads_an_older_conversation_the_same_way_as_the_live_path(client, studio, project, monkeypatch):
    """Turns written before turns recorded their own part, replayed: a gateway turn that kept the
    agent program's total moves the conversation's mark, so the turn after it is not charged for it."""
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [{**CONNECTION, "id": "plan", "kind": "claude-subscription"},
                                                                  {**CONNECTION, "id": "key", "kind": "anthropic-api"},
                                                                  {**CONNECTION, "id": "own", "kind": "litellm"}])
    events = studio.log(pid)

    def turn(name, connection, usage):
        events.append("turn_started", name, text="x", connection=connection, model="m")
        events.append("turn_finished", name, status="completed", error=None, session_id="s1",
                      usage={"input_tokens": 1, "output_tokens": 1, **usage})

    turn("t_1", "plan", {"cost_usd": 1.0, "cost_estimated": True})                         # a running total
    turn("t_2", "own", {"cost_usd": 0.3, "cost_estimated": False, "session_cost_usd": 1.5})
    turn("t_3", "key", {"cost_usd": 1.7, "cost_estimated": False})                         # a running total
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert [t["cost_usd"] for t in total["turns"]] == [1.0, 0.3, pytest.approx(0.2)]
    assert total["charged_usd"] == pytest.approx(0.5) and total["plan_equivalent_usd"] == pytest.approx(1.0)


async def test_older_turns_that_stored_the_running_total_are_read_as_their_own_part(client, studio, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(studio.registry, "connections", lambda: [{**CONNECTION, "id": "subscription", "kind": "claude-subscription"},
                                                                  {**CONNECTION, "id": "own", "kind": "litellm"}])
    events = studio.log(pid)

    def turn(name, cost, session="s1", connection="subscription", estimated=True):
        events.append("turn_started", name, text="x", connection=connection, model="opus")
        events.append("turn_finished", name, status="completed", error=None, session_id=session,
                      usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": cost, "cost_estimated": estimated})

    turn("t_1", 10.6); turn("t_2", 10.99); turn("t_3", 11.65)          # one conversation: 10.60 + 0.39 + 0.66
    turn("t_4", 0.5, session="s2")                                        # a new conversation
    turn("t_5", 0.2, connection="own", estimated=False)                  # the studio's LiteLLM: already per turn
    turn("t_6", 0.3, connection="own", estimated=False)
    total = (await client.get(f"/api/projects/{pid}/cost")).json()
    assert total["plan_equivalent_usd"] == pytest.approx(12.15)          # not 33.74
    assert total["charged_usd"] == pytest.approx(0.5)
    assert [t["cost_usd"] for t in total["turns"]] == [10.6, 0.39, 0.66, 0.5, 0.2, 0.3]



def test_a_cost_the_provider_reports_as_zero_is_a_price_not_a_gap():
    def sent(spend, provider, cost):
        return {**row("t_1", spend), "custom_llm_provider": provider, "metadata": {"usage_object": {"cost": cost}}}

    keep = costs._has_tag("t_1")
    found = summarise([sent(0.5, "openrouter", 0.5), sent(0.0, "openrouter", 0)], keep)
    assert found == {"cost_usd": 0.5, "requests": 2, "unpriced": 0, "basis": "reported", "backup": None}
    assert summarise([sent(0.0, "openrouter", 0)], keep) == {"cost_usd": 0.0, "requests": 1, "unpriced": 0, "basis": "reported", "backup": None}
    # Another provider's 0 is LiteLLM having no price for the model: still unpriced.
    assert summarise([sent(0.0, "chatgpt", 0)], keep) == {"cost_usd": None, "requests": 1, "unpriced": 1, "basis": None, "backup": None}
