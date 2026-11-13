"""Append-only event log per project, in-process pub/sub and the SSE stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from .config import now_iso


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.subscribers: set[asyncio.Queue] = set()
        self.seq = max((event["seq"] for event in self.read()), default=0)

    def read(self, after: int = 0) -> list[dict]:
        events = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # a torn last line after a crash
                    if isinstance(event, dict) and event.get("seq", 0) > after:
                        events.append(event)
        except FileNotFoundError:
            pass
        return events

    def append(self, type: str, turn_id: str | None = None, **fields) -> dict:
        """Persist first, then broadcast (contract rule)."""
        self.seq += 1
        event = {"seq": self.seq, "ts": now_iso(), "type": type, "turn_id": turn_id, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        for queue in self.subscribers:
            queue.put_nowait(event)
        return event

    def unfinished_turns(self) -> list[str]:
        open_turns: list[str] = []
        for event in self.read():
            if event["type"] == "turn_started":
                open_turns.append(event["turn_id"])
            elif event["type"] == "turn_finished" and event["turn_id"] in open_turns:
                open_turns.remove(event["turn_id"])
        return open_turns

    def unresolved(self) -> tuple[list[dict], dict[str, list[str]]]:
        """→ (queued messages never started or removed, {turn id: approval ids never answered})."""
        queued: dict[str, dict] = {}
        approvals: dict[str, list[str]] = {}
        for event in self.read():
            kind = event["type"]
            if kind == "turn_queued":
                queued[event["queued_id"]] = event
            elif kind == "turn_unqueued" or (kind == "turn_started" and event.get("queued_id")):
                queued.pop(event["queued_id"], None)
            elif kind == "approval_request":
                approvals.setdefault(event["turn_id"], []).append(event["approval_id"])
            elif kind == "approval_resolved" and event["approval_id"] in approvals.get(event["turn_id"], []):
                approvals[event["turn_id"]].remove(event["approval_id"])
        return list(queued.values()), {turn: ids for turn, ids in approvals.items() if ids}

    async def stream(self, after: int = 0, ping_seconds: float = 15.0) -> AsyncIterator[str]:
        """SSE frames: replay everything with seq > after, then live events and pings."""
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)  # subscribe before replay so nothing falls in the gap
        try:
            last = after
            for event in self.read(after):
                last = event["seq"]
                yield _frame(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), ping_seconds)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event["seq"] > last:
                    last = event["seq"]
                    yield _frame(event)
        finally:
            self.subscribers.discard(queue)


def _frame(event: dict) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


class EventHub:
    def __init__(self):
        self.logs: dict[str, EventLog] = {}

    def get(self, project_id: str, path: Path) -> EventLog:
        if project_id not in self.logs:
            self.logs[project_id] = EventLog(path)
        return self.logs[project_id]

    def drop(self, project_id: str) -> None:
        self.logs.pop(project_id, None)
