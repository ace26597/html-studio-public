"""Turn runner: one agent turn per project at a time, Stop by task cancellation."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import drawing, effort, nextsteps, tools
from .check import check_page
from .config import now_iso
from .costs import AGENT_PRICED, GATEWAY_KIND, SELF_PRICED, per_turn
from . import workspace as ws
from .harness import TurnRequest, empty_usage
from .store import ProjectNotFound

if TYPE_CHECKING:
    from .studio import Studio

log = logging.getLogger("studio2")
INPUT_CAP, SUMMARY_CAP = 8_000, 4_000


def suggested_name(text: str, workspace) -> str | None:
    """A name to offer for a project still called "Untitled" after a request: the page's own title
    when the agent gave it one, else the first words of the request. No model call."""
    try:
        page = (Path(workspace) / "index.html").read_text("utf-8", "replace")[:20_000]
    except OSError:
        page = ""
    found = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    title = " ".join(html.unescape(re.sub(r"<[^>]+>", "", found.group(1))).split()) if found else ""
    if title and title.lower() != "untitled":
        return title[:80].rstrip()
    said = " ".join(text.split())
    short = " ".join(said.split()[:8])[:60].rstrip(" .,;:!?")
    return (short + ("…" if len(short) < len(said.rstrip(" .,;:!?")) else "")) or None


class TurnRunning(Exception):
    pass


def _clip(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit - 20].decode("utf-8", "ignore") + "… [truncated]"


def cap_input(value, limit: int = INPUT_CAP):
    """Keep a tool input under the contract's 8 KB, trimming long strings first."""
    size = lambda v: len(json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"))  # noqa: E731
    if size(value) <= limit:
        return value
    if isinstance(value, dict):
        slim = {k: _clip(v, 1_000) if isinstance(v, str) else v for k, v in value.items()}
        if size(slim) <= limit:
            return slim
    return {"truncated": _clip(json.dumps(value, ensure_ascii=False, default=str), limit - 200)}


def _page_file(workspace: Path, url_path: str) -> str:
    """" (file: index.html)" for a page that is a file in the project; for a page the project's
    own app renders there is no such file, and saying nothing beats naming a wrong one."""
    try:
        rel = ws.page_to_file(workspace, url_path)
        if (workspace / rel).is_file():
            return f" (file: {rel})"
    except Exception:  # noqa: BLE001 - an odd path only loses the hint
        pass
    return " (rendered by the project's app; find the source that produces this element)"


def compose_prompt(text: str, selection: dict | None, changed: list[dict], workspace,
                   attachments: list[str] | None = None, context: str = "",
                   options: dict | None = None) -> str:
    parts = [text.strip()]
    if context:
        # Sent with every message, not in the system prompt: a resumed conversation keeps the
        # system prompt it started with, so facts placed there go stale (or never arrive).
        parts.append(f"<studio-project>\n{context}\n</studio-project>")
    if said := effort.block(options):   # standard without verify adds nothing
        parts.append(said)
    drawings = [(path, drawn) for path in attachments or [] if (drawn := drawing.load(workspace, path))]
    for path, drawn in drawings:
        parts.append(drawing.describe(drawn, ws.page_to_file(workspace, drawn.get("path") or "/"), path))
    skip = {path for path, _d in drawings} | {i["path"] for _p, d in drawings for i in d.get("images") or []}
    attachments = [path for path in attachments or [] if path not in skip]
    if attachments:
        listing = "\n".join(f"- {path}" for path in attachments)
        parts.append(
            "<studio-attachments>\n"
            "The user attached these files to this message. They are in the project folder; open each "
            "one before you answer (the Read tool shows images and PDFs as well as text):\n"
            f"{listing}\n"
            "</studio-attachments>")
    if selection:
        parts.append(
            "<studio-selection>\n"
            "The user selected one element in the live preview. This request is about that element; "
            "change only it unless the request clearly says otherwise.\n"
            f"Page: {selection.get('path') or '/'}{_page_file(workspace, selection.get('path') or '/')}\n"
            f"CSS selector: {selection.get('selector', '')}\n"
            f"Tag: {selection.get('tag', '')}\n"
            + (f"Source (reported by the app's development build; trust it over a text search): "
               f"{selection['source']}\n" if selection.get("source") else "")
            + f"Text: {selection.get('text', '')}\n"
            f"HTML:\n{selection.get('html', '')}\n"
            "</studio-selection>")
    if changed:
        listing = "\n".join(f"- {item['path']} ({item['status']})" for item in changed[:50])
        parts.append(
            "<studio-user-changes>\n"
            "Since your last turn the user changed these project files directly (inline text edits, "
            "the file panel, uploads or restoring an older version). Re-read them before you edit "
            "and keep the user's changes:\n"
            f"{listing}\n"
            "</studio-user-changes>")
    return "\n\n".join(parts)


class TurnRunner:
    def __init__(self, studio: "Studio"):
        self.studio = studio
        self.active: dict[str, str] = {}            # project id → running turn id
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancellable: set[str] = set()          # turns still inside the harness, not yet stopped
        self.harness_tasks: dict[str, asyncio.Task] = {}
        self.queue: dict[str, list[dict]] = {}      # project id → messages waiting for the running turn
        self.pending: dict[str, dict[str, asyncio.Future]] = {}  # turn id → approval id → answer
        self.verified: set[str] = set()             # turns that called report_verification
        self.checked_pages: dict[str, list[str]] = {}   # turn id → pages check_page ran on in it
        self.reports: dict[str, dict] = {}          # turn id → {failed} of its last report_verification
        self.proposed: dict[str, list[dict]] = {}   # turn id → the agent's suggest_next_steps, cleaned

    def start(self, project: dict, text: str, selection: dict | None,
              connection_id: str | None, model: str | None,
              attachments: list[str] | None = None, queued_id: str | None = None,
              options: dict | None = None) -> str:
        """Accept a turn. Synchronous on purpose: no await between the check and the claim."""
        studio, pid = self.studio, project["id"]
        if pid in self.active:
            raise TurnRunning(pid)
        if pid in studio.ships.running:   # a ship works from the saved version; nothing may change under it
            from .ship import ShipRunning
            raise ShipRunning(pid)
        connection = studio.registry.resolve(connection_id or project.get("connection"))
        if not model and connection["id"] == project.get("connection"):
            model = project.get("model")
        model = model or connection["default_model"]
        turn_id = "t_" + secrets.token_hex(4)
        self.active[pid] = turn_id
        studio.store.update(pid, running_turn=turn_id, connection=connection["id"], model=model)
        attachments = list(attachments or [])
        options = effort.clean(options)
        studio.log(pid).append("turn_started", turn_id, text=text, selection=selection,
                               connection=connection["id"], model=model, attachments=attachments,
                               queued_id=queued_id, options=options)
        studio.watch(pid)
        self.cancellable.add(turn_id)
        self.tasks[pid] = asyncio.create_task(
            self._run(pid, turn_id, text, selection, connection, model, attachments, options))
        return turn_id

    # -- queued follow-ups ------------------------------------------------------------------
    def enqueue(self, project: dict, text: str, selection: dict | None, connection_id: str | None,
                model: str | None, attachments: list[str] | None = None, options: dict | None = None) -> str:
        pid = project["id"]
        item = {"queued_id": "q_" + secrets.token_hex(4), "text": text, "selection": selection,
                "attachments": list(attachments or []), "connection": connection_id, "model": model,
                "options": effort.clean(options)}
        self.queue.setdefault(pid, []).append(item)
        self.studio.log(pid).append("turn_queued", None, **item)
        return item["queued_id"]

    def unqueue(self, project_id: str, queued_id: str, reason: str = "cancelled") -> None:
        waiting = self.queue.get(project_id, [])
        for item in waiting:
            if item["queued_id"] == queued_id:
                waiting.remove(item)
                self.studio.log(project_id).append("turn_unqueued", None, queued_id=queued_id, reason=reason)
                return

    def _start_next(self, pid: str) -> None:
        waiting = self.queue.get(pid, [])
        if pid in self.studio.ships.running:   # they run when the ship ends
            return
        while waiting and pid not in self.active:
            item = waiting.pop(0)
            try:
                self.start(self.studio.store.get(pid), item["text"], item["selection"], item["connection"],
                           item["model"], item["attachments"], item["queued_id"], item.get("options"))
            except ProjectNotFound:
                waiting.clear()
            except Exception:  # e.g. its connection was removed meanwhile
                log.exception("Queued message %s could not start", item["queued_id"])
                self.studio.log(pid).append("turn_unqueued", None, queued_id=item["queued_id"], reason="cancelled")

    # -- approvals --------------------------------------------------------------------------
    def answer(self, turn_id: str, approval_id: str, decision: str) -> bool:
        """The user's answer. → False when that approval is not waiting."""
        future = self.pending.get(turn_id, {}).get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(decision == "allow")
        return True

    def _approver(self, pid: str, turn_id: str):
        events = self.studio.log(pid)

        async def approve(tool: str, title: str, detail: str, reason: str, always: bool = False) -> bool:
            # `always`: something that spends the person's money asks even in a project set to "allow".
            if not always and self.studio.store.get(pid).get("approvals", "ask") == "allow":
                return True
            approval_id = "a_" + secrets.token_hex(4)
            future = asyncio.get_running_loop().create_future()
            self.pending.setdefault(turn_id, {})[approval_id] = future
            events.append("approval_request", turn_id, approval_id=approval_id, tool=tool, title=title,
                          detail=_clip(detail, 4_000), reason=reason)
            allowed = await future  # cancelled when the turn ends first; _close_approvals announces that
            if self.pending.get(turn_id, {}).pop(approval_id, None) is not None:
                events.append("approval_resolved", turn_id, approval_id=approval_id,
                              decision="allow" if allowed else "deny", by="user")
            return allowed

        return approve

    def _close_approvals(self, pid: str, turn_id: str) -> None:
        for approval_id, future in self.pending.pop(turn_id, {}).items():
            if not future.done():
                future.cancel()
            self.studio.log(pid).append("approval_resolved", turn_id, approval_id=approval_id,
                                        decision="deny", by="turn_end")

    def stop(self, project_id: str, turn_id: str) -> None:
        """Idempotent: cancels the harness once; later calls and unknown turns are no-ops."""
        if self.active.get(project_id) == turn_id and turn_id in self.cancellable:
            self.cancellable.discard(turn_id)
            # Only the harness call is cancelled, never the turn's own task: a task cancelled
            # before its first step would end without recording `turn_finished`.
            inner = self.harness_tasks.get(turn_id)
            if inner is not None:
                inner.cancel()

    async def stop_all(self) -> None:
        for pid, turn_id in list(self.active.items()):
            self.stop(pid, turn_id)
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    async def wait(self, project_id: str) -> None:
        task = self.tasks.get(project_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)

    RECHECK_PAGES, RECHECK_PROBLEMS = 10, 40

    async def _recheck(self, pid: str, turn_id: str, workspace: Path) -> int:
        """After a reported verification: the page check once more, on index.html and every page
        check_page ran on in this request → one `verification_recheck` event (CONTRACT.md).
        → how many problems it found (0 when there was no page to check)."""
        pages: list[str] = []
        for page in ["index.html", *self.checked_pages.get(turn_id, [])]:
            if page not in pages and (workspace / page).is_file():
                pages.append(page)
        if not pages:
            return 0
        summaries, problems = [], []
        for page in pages[:self.RECHECK_PAGES]:
            try:
                result = await asyncio.to_thread(check_page, workspace, page, False)
            except Exception as exc:  # noqa: BLE001 - said on the card, never a silent pass
                summaries.append({"page": page, "checked": 0, "verified": 0, "derived": 0, "failed": 0,
                                  "cited_not_listed": 0, "source_list": False, "error": effort._cut(str(exc), 300)})
                continue
            summary = result["summary"]
            summaries.append({"page": page, "checked": len(result["items"]), "verified": summary["verified"],
                              "derived": summary["derived"], "failed": summary["failed"],
                              "cited_not_listed": summary["cited_not_listed"], "source_list": result["source_list"],
                              "error": None})
            for item in result["items"]:
                if item["status"] == "failed":
                    problems.append({"page": page, "what": effort._cut(item["text"] or f"Item {item['n']}", 200),
                                     "where": effort._cut(item.get("where") or "", 200),
                                     "why": effort._cut(item.get("reason") or "", 300)})
            for source_id in result["not_listed"]:
                problems.append({"page": page, "what": f"Source {source_id}", "where": "",
                                 "why": "Cited on the page but has no entry in the page's source list."})
        kept = problems[:self.RECHECK_PROBLEMS]
        read = any(summary["error"] is None for summary in summaries)
        self.studio.log(pid).append("verification_recheck", turn_id, pages=summaries, problems=kept,
                                    problems_total=len(problems), problems_cut=len(problems) - len(kept),
                                    agrees=read and not problems)
        return len(problems)

    async def _run(self, pid: str, turn_id: str, text: str, selection: dict | None,
                   connection: dict, model: str | None, attachments: list[str],
                   options: dict | None = None) -> None:
        studio = self.studio
        events, workspace = studio.log(pid), studio.store.workspace(pid)
        project = studio.store.get(pid)
        started, started_at = time.monotonic(), now_iso()
        status, error, usage = "completed", None, empty_usage()
        session = {"id": project.get("session_id")}

        def emit(event: dict) -> None:
            fields = dict(event)
            kind = fields.pop("type")
            if kind == "session":  # internal: lets a harness report its session before it finishes
                session["id"] = fields.get("session_id") or session["id"]
                if fields.get("skills") is not None:
                    studio.record_session({"skills": fields["skills"], "mcp_servers": fields.get("mcp_servers") or []})
                return
            if kind == "tool_call":
                if fields.get("name") == "Skill" and isinstance(fields.get("input"), dict):
                    studio.picker.note_used(pid, fields["input"].get("skill") or fields["input"].get("command") or "")
                fields["input"] = cap_input(fields.get("input"))
            elif kind == "tool_result":
                fields["summary"] = _clip(str(fields.get("summary") or ""), SUMMARY_CAP)
            events.append(kind, turn_id, **fields)

        try:
            changed = await asyncio.to_thread(ws.user_changes_since_agent, workspace)
            if not project.get("attach"):    # an attached app's ignore file is the person's own
                await asyncio.to_thread(ws.ensure_ignored, workspace)
            approve = studio.approvers[pid] = self._approver(pid, turn_id)   # studio tools ask through it too
            options = effort.clean(options)
            prepared = await studio.prepare_turn(pid, turn_id, text, connection,   # this request's skills and connectors
                                                 depth=options["depth"],
                                                 check_only=bool(options.get("check_only")))
            request = TurnRequest(
                project_id=pid, turn_id=turn_id, workspace=workspace,
                prompt=compose_prompt(text, selection, changed, workspace, attachments,
                                      studio.agent_context(pid, prepared["picked"]), options),
                session_id=project.get("session_id"), connection=connection, model=model,
                text=text, selection=selection, attachments=attachments,
                approve=approve, studio_tool=lambda name, args: tools.call(studio, pid, name, args),
                options=options, **prepared["request"])
            if turn_id not in self.cancellable:  # Stop arrived before the harness started
                raise asyncio.CancelledError
            inner = asyncio.create_task(studio.registry.harness(connection["kind"]).run(request, emit))
            self.harness_tasks[turn_id] = inner
            result = await inner
            status = "failed" if result.status == "failed" else "completed"
            error = result.error if status == "failed" else None
            usage = {**usage, **(result.usage or {})}
            session["id"] = result.session_id or session["id"]
        except asyncio.CancelledError:
            status = "stopped"
        except Exception as exc:  # a harness crash is a failed turn, never a dead task
            log.exception("Turn %s failed", turn_id)
            status, error = "failed", str(exc) or exc.__class__.__name__
        studio.approvers.pop(pid, None)
        reported, still_wrong = turn_id in self.verified, 0
        if reported:     # the studio checks the agent's claims itself (never sends it back)
            try:
                still_wrong = await self._recheck(pid, turn_id, workspace)
            except Exception:  # noqa: BLE001 - a failed re-check only loses the extra line
                log.exception("The re-check after turn %s failed", turn_id)
        self.checked_pages.pop(turn_id, None)
        if status == "completed" and (options or {}).get("verify") and not reported:
            events.append("notice", turn_id, level="warning", message=effort.NOT_REPORTED)   # never a silent pass
        self.verified.discard(turn_id)
        report, proposed = self.reports.pop(turn_id, {}), self.proposed.pop(turn_id, [])
        studio.picker.finish(pid, status)
        self.cancellable.discard(turn_id)  # from here on the turn only records its outcome
        self.harness_tasks.pop(turn_id, None)
        self._close_approvals(pid, turn_id)

        changed_files = False
        try:
            changed_files = await self._checkpoint(pid, turn_id, text, project.get("head"))
        except Exception as exc:
            log.exception("Checkpoint after turn %s failed", turn_id)
            events.append("notice", turn_id, level="error", message=f"Could not save a version: {exc}")
        try:  # the project's own back end picks up what the agent changed
            await studio.sync_app(pid, restart=changed_files)
        except Exception:
            log.exception("Could not restart the app of %s", pid)
        page_errors = 0
        if status == "completed" and changed_files:
            try:    # the studio opens the page once itself: a page that is broken when it loads is said, not found later
                page_errors = await self._look_after(pid, turn_id, workspace)
            except Exception:  # noqa: BLE001 - the look is an extra; the request still ends properly
                log.exception("The look at the page after turn %s failed", turn_id)
        if status == "completed":    # chips under the answer; a tap only fills the chat box (nextsteps.py)
            try:
                steps = nextsteps.compose(
                    {"changed": changed_files, "verify": bool((options or {}).get("verify")), "reported": reported,
                     "page_errors": page_errors,
                     "has_page": (workspace / "index.html").is_file() or (workspace / "studio.json").is_file(),
                     "failed": report.get("failed", 0), "still_wrong": still_wrong}, proposed)
                if steps:
                    events.append("next_steps", turn_id, steps=steps)
            except Exception:  # noqa: BLE001 - suggestions are an extra; the request still ends properly
                log.exception("Could not suggest next steps after turn %s", turn_id)
        if usage.get("duration_ms") is None:
            usage["duration_ms"] = int((time.monotonic() - started) * 1000)
        if status == "failed" and not error:
            error = "The turn failed without an error message."
        kept = {}
        total = usage.get("cost_usd")
        if isinstance(total, (int, float)) and connection["kind"] not in SELF_PRICED:
            # The agent program's figure is the conversation's running total: keep this turn's part.
            usage["cost_usd"] = per_turn(float(total), session["id"], project.get("session_cost"))
            usage["session_cost_usd"] = total
            # Tokens times a price list: the plan's equivalent, or a bill the agent program worked out.
            usage["cost_basis"] = ("plan" if usage.get("cost_estimated")
                                   else "calculated" if connection["kind"] in AGENT_PRICED else None)
            kept = {"session_cost": {"session_id": session["id"], "total_usd": total}}
        self.active.pop(pid, None)
        self.tasks.pop(pid, None)
        try:  # release the project before announcing the end, so a follow-up turn is accepted
            offer, record = {}, studio.store.get(pid)
            if status == "completed" and record.get("name") == "Untitled" and not record.get("name_offered"):
                name = suggested_name(text, workspace)  # offered once; nothing is renamed without a tap
                offer = {"name_suggestion": name, "name_offered": True} if name else {}
            studio.store.update(pid, running_turn=None, session_id=session["id"], **offer, **kept)
        except ProjectNotFound:
            pass
        if connection["kind"] == GATEWAY_KIND:
            usage["cost_pending"] = True     # the gateway's own figure is read back after the turn (costs.py)
        events.append("turn_finished", turn_id, status=status, error=error, usage=usage,
                      session_id=session["id"])
        if usage.get("cost_pending"):
            studio.costs.settle(pid, turn_id, connection, started_at)
        if status == "stopped":  # Stop means stop: the follow-ups and an asked-for ship do not run either
            studio.ships.from_agent.pop(pid, None)
            for item in list(self.queue.get(pid, [])):
                self.unqueue(pid, item["queued_id"])
        else:
            if status == "failed":
                studio.ships.from_agent.pop(pid, None)
            await studio.ships.after_turn(pid)   # "make it live" asked for during this turn
            self._start_next(pid)

    LOOK_WAIT = 8.0         # how long an app may take to come back up before the look is skipped

    async def _problems(self, pid: str) -> dict | None:
        """Open the project's page once in the studio's own browser → {errors, failed} (lists of
        short strings), or None when it cannot be looked at (no browser, the scripted agent, an app
        that is not up). Replaced in tests."""
        studio = self.studio
        if studio.settings.fake or studio.looker.reason() is not None:
            return None
        project = studio.store.get(pid)
        if (studio.store.workspace(pid) / "studio.json").is_file():
            deadline = time.monotonic() + self.LOOK_WAIT
            while studio.app(pid).state != "running":
                if time.monotonic() > deadline:
                    return None
                await asyncio.sleep(0.4)
        seen = await studio.looker.look(f"http://127.0.0.1:{project['port']}/", studio.store.dir(pid) / "looks",
                                        width=1280, height=800, full_page=False, wait_ms=1500)
        errors = [item["text"] for item in seen.get("console") or [] if item.get("level") in ("error", "exception")]
        failed = [f"{item.get('url')} ({item.get('status') or item.get('error') or 'failed'})"
                  for item in seen.get("failed_requests") or []]
        return {"errors": errors, "failed": failed}

    async def _look_after(self, pid: str, turn_id: str, workspace: Path) -> int:
        """After a request that changed the project: the page's own errors, as one notice. → how many."""
        if not ((workspace / "index.html").is_file() or (workspace / "studio.json").is_file()):
            return 0
        found = await self._problems(pid)
        if not found:
            return 0
        lines = [effort._cut(text, 160) for text in dict.fromkeys([*found["errors"], *found["failed"]])]
        if not lines:
            return 0
        shown = "; ".join(lines[:3]) + (f"; and {len(lines) - 3} more" if len(lines) > 3 else "")
        self.studio.log(pid).append(
            "notice", turn_id, level="warning",
            message=f"The studio opened the page after this request and it shows {len(lines)} "
                    f"error{'s' if len(lines) != 1 else ''} when it loads: {shown}")
        return len(lines)

    async def _checkpoint(self, pid: str, turn_id: str, text: str, head_before: str | None) -> bool:
        """One `agent` checkpoint when the turn changed files (also when the agent committed itself)."""
        studio, workspace = self.studio, self.studio.store.workspace(pid)
        message = " ".join(text.split())
        message = message[:70] + ("…" if len(message) > 70 else "")
        if await studio.checkpoint(pid, "agent", message, turn_id):
            return True
        head = await asyncio.to_thread(ws.head, workspace)
        if head and head_before and head != head_before:
            described = await asyncio.to_thread(ws.describe, workspace, head, head_before)
            studio.record_checkpoint(pid, {**described, "kind": "agent"}, turn_id)
            return True
        return False
