"""Cost per request and per project (CONTRACT.md, "Cost per request and per project").

A figure here is always one somebody who charged for the request reported: LiteLLM's own
price for a request, a gateway's spend log, the agent program's figure on an API key. Token
counts are never turned into dollars. A request nobody priced is counted as unpriced, never
shown as free.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

import httpx

from .config import connection_key

if TYPE_CHECKING:
    from .studio import Studio

log = logging.getLogger("studio2")

TAG_PREFIX = "studio-turn:"
STUDIO_TURN_HEADER = "x-studio-turn"       # the header the studio's own LiteLLM reads (gateway.TURN_HEADER)
GATEWAY_KIND = "anthropic-gateway"
# The gateway writes its spend log some seconds after a request: wait, then read until two reads agree.
READ_DELAYS = (12.0,) + (3.0,) * 16      # LiteLLM writes every 10 s
LOOKBACK = timedelta(seconds=60)
LOOKAHEAD = timedelta(minutes=2)
SLACK = (timedelta(seconds=5), timedelta(seconds=10))     # a recovered turn's window: before start, after end
PAGE_SIZE, MAX_PAGES = 1000, 50
RESTART_ERROR = "The studio restarted during this turn."

# The turn a model call belongs to, for side calls (the library's choosing call) that have no
# request object of their own to carry it.
CURRENT_TURN: contextvars.ContextVar[str | None] = contextvars.ContextVar("studio_turn", default=None)


def turn_header(kind: str, turn_id: str | None) -> str | None:
    """The one extra request header that lets a route's spend be tied to a turn, or None for a
    route that reports its own cost. The turn id is random: no project, path, text or key."""
    if not turn_id:
        return None
    if kind == "litellm":
        return f"{STUDIO_TURN_HEADER}: {turn_id}"
    if kind == GATEWAY_KIND:
        return f"x-litellm-tags: {TAG_PREFIX}{turn_id}"
    return None


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def per_turn(total: float, session_id: str | None, prior: dict | None) -> float:
    """The agent program reports a running total for the whole conversation, not for one turn.
    → this turn's part: the total less what the same conversation had reached before it. A new
    conversation (another session id, or a total that went down) starts from nothing."""
    before = (prior or {}).get("total_usd")
    if prior and session_id and prior.get("session_id") == session_id and _number(before) and total >= before:
        return round(total - before, 6)
    return total


SELF_PRICED = ("litellm", GATEWAY_KIND)     # their figure is the route's own, per turn already
AGENT_PRICED = ("anthropic-api", "bedrock", "vertex", "foundry")    # the agent program's own arithmetic, billed


class SpendLogError(Exception):
    """The gateway's spend log cannot be read; the message is a sentence for the person."""


def _is_price(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def provider_reported(row: dict) -> bool:
    """Whether the provider itself sent this request's cost (OpenRouter does), as against LiteLLM
    working it out from token counts and its price list."""
    usage = (row.get("metadata") or {}).get("usage_object") if isinstance(row.get("metadata"), dict) else None
    return _is_price((usage or {}).get("cost")) if isinstance(usage, dict) else False


def reported_free(row: dict) -> bool:
    """OpenRouter sends a cost with every answer; when that cost is 0 the request was not billed."""
    usage = (row.get("metadata") or {}).get("usage_object") if isinstance(row.get("metadata"), dict) else None
    cost = (usage or {}).get("cost") if isinstance(usage, dict) else None
    return row.get("custom_llm_provider") == "openrouter" and _number(cost) and cost == 0


def basis_of(priced: int, reported: int) -> str | None:
    """`reported` only when every priced request was; None when nothing was priced."""
    if not priced:
        return None
    return "reported" if reported == priced else "calculated"


def summarise(rows: list[dict], keep: Callable[[dict], bool]) -> dict:
    """→ {cost_usd, requests, unpriced, basis} over the rows `keep` accepts. A row with a price above
    zero is priced whatever its status; a successful row with none is unpriced. `cost_usd` is None
    when nothing was priced: that is "nobody said", not zero."""
    total, requests, priced, reported = 0.0, 0, 0, 0
    for row in rows:
        if not keep(row):
            continue
        if _is_price(row.get("spend")):
            total += float(row["spend"])
            priced += 1
            requests += 1
            reported += provider_reported(row)
        elif row.get("status") == "success":
            requests += 1
            if reported_free(row):      # the provider said this one cost nothing: a price, not a gap
                priced += 1
                reported += 1
    return {"cost_usd": round(total, 6) if priced else None, "requests": requests, "unpriced": requests - priced,
            "basis": basis_of(priced, reported)}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch_rows(client: httpx.AsyncClient, base_url: str, key: str, since: datetime, until: datetime) -> list[dict]:
    """Every spend-log row between two moments, read-only: `GET /spend/logs/v2`, paged."""
    base, rows, page = base_url.rstrip("/"), [], 1
    while page <= MAX_PAGES:
        try:
            response = await client.get(
                f"{base}/spend/logs/v2", headers={"Authorization": f"Bearer {key}"},
                params={"start_date": _stamp(since), "end_date": _stamp(until), "page": page, "page_size": PAGE_SIZE})
        except httpx.ConnectError:
            raise SpendLogError(f"The gateway is not reachable at {base}.") from None
        except httpx.HTTPError as exc:
            raise SpendLogError(f"The gateway did not answer ({exc.__class__.__name__}).") from None
        if response.status_code in (401, 403):
            raise SpendLogError(f"The gateway refused to show its spend log to this connection's key "
                                f"(HTTP {response.status_code}).")
        if response.status_code == 404:
            raise SpendLogError("The gateway has no spend log at /spend/logs/v2 (it is not LiteLLM, or it keeps "
                                "no database).")
        if response.status_code >= 400:
            raise SpendLogError(f"The gateway's spend log answered HTTP {response.status_code}.")
        try:
            body = response.json()
            data = body["data"]
            if not isinstance(data, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise SpendLogError("The gateway answered something that is not a spend log.") from None
        rows.extend(row for row in data if isinstance(row, dict))
        try:
            pages = int(body.get("total_pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages:
            break
        page += 1
    return rows


def _has_tag(turn_id: str) -> Callable[[dict], bool]:
    wanted = TAG_PREFIX + turn_id
    return lambda row: wanted in (row.get("request_tags") or [])


class Costs:
    def __init__(self, studio: "Studio"):
        self.studio = studio
        self.delays: tuple[float, ...] = READ_DELAYS
        self.sleep = asyncio.sleep
        self.settling: dict[str, asyncio.Task] = {}
        self.client_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False)

    # -- writing --------------------------------------------------------------------------------
    def _write(self, project_id: str, turn_id: str, cost_usd, requests: int, unpriced: int, source: str,
               reason: str | None = None, basis: str | None = None) -> None:
        try:
            self.studio.log(project_id).append("turn_cost", turn_id, cost_usd=cost_usd, requests=requests,
                                               unpriced=unpriced, source=source, reason=reason, basis=basis)
        except Exception:    # the project was deleted while the read-back waited
            log.debug("Dropped a turn_cost for %s", project_id)

    def _credentials(self, connection: dict) -> tuple[str, str]:
        base = str(connection.get("base_url") or "")
        key = connection_key(connection, os.environ, self.studio.settings.secret_store())
        if not base or not key:
            raise SpendLogError("This connection has no address or key to read the gateway's spend log with.")
        return base, key

    # -- after a turn ----------------------------------------------------------------------------
    def settle(self, project_id: str, turn_id: str, connection: dict, started_at: str | None) -> None:
        """Read what a gateway turn cost, in the background, and write `turn_cost`."""
        if turn_id in self.settling:
            return
        task = asyncio.ensure_future(self._settle(project_id, turn_id, connection, started_at))
        self.settling[turn_id] = task
        task.add_done_callback(lambda _t: self.settling.pop(turn_id, None))

    async def _settle(self, project_id: str, turn_id: str, connection: dict, started_at: str | None) -> None:
        try:
            found, reason = await self._read_tagged(project_id, turn_id, connection, started_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:    # noqa: BLE001 - a read-back must never take anything else down
            log.exception("Reading the cost of turn %s failed", turn_id)
            found, reason = None, f"Reading the gateway's spend log failed ({exc.__class__.__name__})."
        if found is None:
            self._write(project_id, turn_id, None, 0, 0, "unavailable", reason)
        else:
            self._write(project_id, turn_id, found["cost_usd"], found["requests"], found["unpriced"], "gateway",
                        basis=found["basis"])

    async def _read_tagged(self, project_id: str, turn_id: str, connection: dict,
                           started_at: str | None) -> tuple[dict | None, str | None]:
        try:
            base, key = self._credentials(connection)
        except SpendLogError as exc:
            return None, str(exc)
        since = (parse_time(started_at) or datetime.now(timezone.utc) - timedelta(hours=1)) - LOOKBACK
        keep, last, tagged, seen = _has_tag(turn_id), None, 0, 0
        async with self.client_factory() as client:
            for wait in self.delays:
                await self.sleep(wait)
                try:
                    self.studio.store.get(project_id)
                except Exception:   # noqa: BLE001 - the project is gone; nothing to write to
                    return None, "The project was deleted."
                try:
                    rows = await fetch_rows(client, base, key, since, datetime.now(timezone.utc) + LOOKAHEAD)
                except SpendLogError as exc:
                    if last is not None:
                        break       # keep what an earlier read found
                    return None, str(exc)
                found = summarise(rows, keep)
                tagged = sum(1 for row in rows if keep(row))
                if (found["requests"] and found == last) or (tagged and tagged == seen):
                    break           # two reads in a row agree (rows that all failed settle the same way)
                last, seen = found, tagged
        if tagged and (last is None or not last["requests"]):
            return None, (f"The gateway logged {tagged} {'request' if tagged == 1 else 'requests'} for this turn, "
                          "but none succeeded and none carried a price, so nothing was billed.")
        if last is None or not last["requests"]:
            return None, ("The gateway's spend log had no request tagged for this turn (it writes the log some "
                          "seconds late, and a gateway that is not LiteLLM may not record the tag).")
        if last["cost_usd"] is None:
            log.info("Turn %s: %d requests, none priced by the gateway", turn_id, last["requests"])
        return last, None

    def pending_turns(self, project_id: str) -> list[dict]:
        """Turns whose read-back never finished (the studio restarted first), oldest first."""
        started, wanted, costed = {}, {}, set()
        for event in self.studio.log(project_id).read():
            kind, turn = event["type"], event.get("turn_id")
            if kind == "turn_started":
                started[turn] = event
            elif kind == "turn_finished" and ((event.get("usage") or {}).get("cost_pending")
                                              or event.get("error") == RESTART_ERROR):
                wanted[turn] = event
            elif kind == "turn_cost":
                costed.add(turn)
        return [{"turn_id": turn, "started_at": started[turn]["ts"], "connection": started[turn].get("connection")}
                for turn in wanted if turn in started and turn not in costed]

    def reconcile(self, project_id: str) -> None:
        """At start-up: pick up the read-backs a restart cut short."""
        connections = {c["id"]: c for c in self.studio.registry.connections()}
        for item in self.pending_turns(project_id):
            connection = connections.get(item["connection"])
            if connection and connection["kind"] == GATEWAY_KIND:
                self.settle(project_id, item["turn_id"], connection, item["started_at"])

    async def stop(self) -> None:
        tasks = list(self.settling.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- turns from before the tag --------------------------------------------------------------
    async def recover(self, project_id: str) -> dict:
        """Price finished gateway turns that have no price by what the spend log does record for
        them: the agent program's session id and the turn's time window. Writes nothing for a turn
        it cannot match."""
        connections = {c["id"]: c for c in self.studio.registry.connections()}
        turns: dict[str, dict] = {}
        for event in self.studio.log(project_id).read():
            kind, turn = event["type"], event.get("turn_id")
            if kind == "turn_started":
                turns[turn] = {"turn_id": turn, "start": parse_time(event["ts"]), "connection": event.get("connection"),
                               "end": None, "session": None, "priced": False}
            elif kind == "turn_finished" and turn in turns:
                turns[turn].update(end=parse_time(event["ts"]), session=event.get("session_id"))
            elif kind == "turn_cost" and turn in turns:
                turns[turn]["priced"] = event.get("cost_usd") is not None
        wanted = [t for t in turns.values() if t["end"] and t["start"] and t["session"] and not t["priced"]
                  and (connections.get(t["connection"]) or {}).get("kind") == GATEWAY_KIND]
        report: dict = {"recovered": 0, "unmatched": 0, "turns": [], "reason": None}
        by_connection: dict[str, list[dict]] = {}
        for turn in wanted:
            by_connection.setdefault(turn["connection"], []).append(turn)
        for connection_id, group in by_connection.items():
            try:
                base, key = self._credentials(connections[connection_id])
                async with self.client_factory() as client:
                    rows = await fetch_rows(client, base, key, min(t["start"] for t in group) - LOOKBACK,
                                            max(t["end"] for t in group) + LOOKAHEAD)
            except SpendLogError as exc:
                report["reason"] = str(exc)
                report["unmatched"] += len(group)
                continue
            for turn in group:
                low, high = turn["start"] - SLACK[0], turn["end"] + SLACK[1]

                def keep(row: dict, turn=turn, low=low, high=high) -> bool:
                    when = parse_time(row.get("startTime"))
                    return row.get("session_id") == turn["session"] and when is not None and low <= when <= high

                found = summarise(rows, keep)
                if not found["requests"]:
                    report["unmatched"] += 1
                    continue
                self._write(project_id, turn["turn_id"], found["cost_usd"], found["requests"], found["unpriced"],
                            "recovered", basis=found["basis"])
                report["recovered"] += 1
                report["turns"].append({"turn_id": turn["turn_id"], **found})
        return report

    # -- per project ---------------------------------------------------------------------------
    def project_cost(self, project_id: str) -> dict:
        """The project's running total, summed from its event log, split into what was charged, what
        the subscription would have charged at API prices, and what nobody priced."""
        turns: dict[str, dict] = {}
        for event in self.studio.log(project_id).read():
            kind, turn = event["type"], event.get("turn_id")
            if kind == "turn_started":
                turns[turn] = {"turn_id": turn, "at": event["ts"], "connection": event.get("connection"),
                               "model": event.get("model"), "usage": None, "cost": None}
            elif kind == "turn_finished" and turn in turns:
                turns[turn]["usage"] = event.get("usage") or {}
                turns[turn]["session"] = event.get("session_id")
            elif kind == "turn_cost" and turn in turns:
                turns[turn]["cost"] = event
        charged = plan = calculated = 0.0
        unpriced_turns = unpriced_requests = pending = 0
        listing = []
        kinds = {c["id"]: c["kind"] for c in self.studio.registry.connections()}
        reached: dict[str, float] = {}      # conversation → the running total its last turn reported
        for turn in turns.values():
            usage, cost = turn["usage"], turn["cost"]
            if usage is None:
                continue                                     # still running
            if (cost is None and _number(usage.get("cost_usd")) and "session_cost_usd" not in usage
                    and kinds.get(turn["connection"]) not in SELF_PRICED and turn.get("session")):
                # Written before turns recorded their own share: the figure is the conversation's
                # running total, so this turn's part is the growth since the turn before.
                total = float(usage["cost_usd"])
                usage = {**usage, "cost_usd": per_turn(total, turn["session"], {
                    "session_id": turn["session"], "total_usd": reached.get(turn["session"])})}
                reached[turn["session"]] = total
            elif _number(usage.get("session_cost_usd")) and turn.get("session"):
                reached[turn["session"]] = float(usage["session_cost_usd"])
            if cost is not None:
                value, estimated, basis = cost.get("cost_usd"), False, cost.get("basis")
                requests, unpriced = int(cost.get("requests") or 0), int(cost.get("unpriced") or 0)
                source, reason = cost.get("source"), cost.get("reason")
            else:
                value, estimated, basis = usage.get("cost_usd"), usage.get("cost_estimated"), usage.get("cost_basis")
                requests, unpriced = int(usage.get("requests") or 0), int(usage.get("unpriced_requests") or 0)
                source, reason = None, None
                if basis is None and estimated is False and kinds.get(turn["connection"]) in AGENT_PRICED:
                    basis = "calculated"        # written before turns said so: the agent program's arithmetic
            waiting = cost is None and bool(usage.get("cost_pending"))
            made_request = requests > 0 or usage.get("input_tokens") is not None or usage.get("output_tokens") is not None
            if not made_request and not waiting:
                continue                                     # failed before it asked a model anything
            if waiting:
                kind = "pending"
                pending += 1
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and estimated is True:
                kind = "plan"
                plan += float(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and estimated is False:
                kind = "charged"
                charged += float(value)
                calculated += float(value) if basis == "calculated" else 0.0
                unpriced_requests += unpriced
            else:
                kind = "unpriced"
                unpriced_turns += 1
                unpriced_requests += requests
            listing.append({"turn_id": turn["turn_id"], "at": turn["at"], "connection": turn["connection"],
                            "model": turn["model"], "kind": kind,
                            "cost_usd": value if kind in ("charged", "plan") else None,
                            "basis": "plan" if kind == "plan" else basis if kind == "charged" else None,
                            "requests": requests, "unpriced_requests": unpriced, "source": source, "reason": reason})
        return {"charged_usd": round(charged, 6), "calculated_usd": round(calculated, 6),
                "plan_equivalent_usd": round(plan, 6),
                "unpriced_turns": unpriced_turns, "unpriced_requests": unpriced_requests,
                "pending_turns": pending, "turns": listing}


__all__ = ["Costs", "CURRENT_TURN", "SpendLogError", "fetch_rows", "summarise", "turn_header"]
