"""Append-only event log per project, in-process pub/sub and the SSE stream."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import AsyncIterator

from .config import now_iso

log = logging.getLogger("studio2")


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.subscribers: set[asyncio.Queue] = set()
        # Appends come from the event loop and from worker threads: one at a time, so two events
        # never share a line and no number is given twice.
        self._lock = threading.Lock()
        self._mend()
        self.seq = max((event["seq"] for event in self.read()), default=0)

    def _mend(self) -> None:
        """A write cut short by a crash leaves a last line with no newline. Appending after it
        would glue the next event onto it and both would be skipped on every replay, so the torn
        tail is cut off here (a copy goes to `<log>.torn`, replaced each time). A last event that is
        whole and only lost its newline gets the newline instead."""
        try:
            with self.path.open("rb+") as handle:
                size = handle.seek(0, 2)
                if size == 0:
                    return
                handle.seek(max(0, size - 1))
                if handle.read(1) == b"\n":
                    return
                start = max(0, size - 1)
                while start > 0:            # back to the end of the last whole line
                    step = min(start, 65_536)
                    handle.seek(start - step)
                    found = handle.read(step).rfind(b"\n")
                    if found >= 0:
                        start = start - step + found + 1
                        break
                    start -= step
                handle.seek(start)
                tail = handle.read()
                try:
                    whole = isinstance(json.loads(tail), dict)
                except ValueError:
                    whole = False
                if whole:
                    handle.seek(0, 2)
                    handle.write(b"\n")
                    return
                self.path.with_name(self.path.name + ".torn").write_bytes(tail)
                handle.truncate(start)
                log.warning("Cut a torn last line (%d bytes) off %s", len(tail), self.path)
        except FileNotFoundError:
            pass

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
        with self._lock:
            event = {"seq": self.seq + 1, "ts": now_iso(), "type": type, "turn_id": turn_id, **fields}
            line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            with self.path.open("ab") as handle:     # one write of the whole line
                handle.write(line)
            self.seq += 1   # only once it is written: a failed write does not use up a number
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

    def unanswered(self) -> dict[str, list[str]]:
        """→ {turn id: ids of question cards never resolved} ("Questions before the work")."""
        asked: dict[str, list[str]] = {}
        for event in self.read():
            if event["type"] == "question_request":
                asked.setdefault(event["turn_id"], []).append(event["question_id"])
            elif event["type"] == "question_resolved" and event["question_id"] in asked.get(event["turn_id"], []):
                asked[event["turn_id"]].remove(event["question_id"])
        return {turn: ids for turn, ids in asked.items() if ids}

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
                    # A named event, not a comment: the page must see it to notice a stalled stream.
                    yield PING
                    continue
                if event["seq"] > last:
                    last = event["seq"]
                    yield _frame(event)
        finally:
            self.subscribers.discard(queue)


PING = "event: ping\ndata: {}\n\n"   # the keep-alive (CONTRACT.md, "Events"); no id, so Last-Event-ID stays put


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
