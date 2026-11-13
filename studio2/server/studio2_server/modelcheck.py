"""“Check this model” (CONTRACT.md, "Any model (step 3n)").

A fixed, small request run through the real agent program on a scratch project, so a person can
see whether a model can do what the studio asks of every model: edit files in place, call two
tools in one step, call one of the studio's own tools, and carry a conversation into a second
message. The result (passed, or what failed, with the time and the number of steps) is kept per
connection and model in `<data>/model-checks.json` and shown beside the model in the Library.

The scratch project is a real project (the check goes through the same turn runner, library and
tools as any request) that is hidden from the project list and removed when the check ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .picker import picking_model
from . import __version__
from .config import model_cost, now_iso
from .store import ProjectNotFound, write_json_atomic

if TYPE_CHECKING:
    from .studio import Studio

log = logging.getLogger("studio2")

VERSION = 1
MARKER = "[studio model check]"
CODE_WORD = "quince-417"
TOTAL = "42"
MESSAGE_SECONDS = 420.0

SEED = {
    "index.html": """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Model check</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<main>
<h1>Orchard report</h1>
<p class="status">Status: Draft</p>
<p>This page and its files are a fixture for the studio's model check. The figures are made up for the check.</p>
<ul>
<li>North block</li>
<li>South block</li>
</ul>
</main>
</body>
</html>
""",
    "styles.css": """:root { --accent: #2255aa; --ink: #1b1b1b; }
body { font: 16px/1.5 system-ui, sans-serif; color: var(--ink); margin: 2rem; }
h1 { color: var(--accent); }
.status { font-weight: 600; }
""",
    "data/harvest.csv": "block,crates\nNorth,17\nSouth,25\n",
}
EXPECTED = {
    "index.html": SEED["index.html"].replace("<h1>Orchard report</h1>", "<h1>Orchard report 2</h1>")
                                    .replace("Status: Draft", "Status: Checked"),
    "styles.css": SEED["styles.css"].replace("--accent: #2255aa", "--accent: #117733"),
}
FIRST = f"""{MARKER} This is a fixed check of the model, not a real task. Do exactly these five things, in order, and nothing else.

1. Read `index.html` and `styles.css` with two tool calls in the same step: both in one message, not one after the other.
2. In `index.html`, change the heading `Orchard report` to `Orchard report 2`, and change the word `Draft` to `Checked`. Make each change as an edit in place; do not rewrite the file.
3. In `styles.css`, change `--accent: #2255aa` to `--accent: #117733`, also as an edit in place.
4. Call the studio tool `query_files` (its full name is `mcp__studio__query_files`) with this SQL: SELECT sum(crates) AS total FROM 'data/harvest.csv'
   Then create the file `total.txt` containing only the number it returned.
5. Remember this code word for my next message: {CODE_WORD}. Do not write it anywhere yet.

Do not look at the page, do not start anything and do not make anything else. When you are done, reply with the single word DONE."""
SECOND = (f"{MARKER} Second and last part of the check. Add a second line to `total.txt` containing only the "
          "code word I gave you in my previous message. Then reply with the single word DONE.")

PARTS = [
    ("finished", "Both messages finished"),
    ("parallel", "Two tool calls in one step"),
    ("in_place", "Edits in place in two files"),
    ("studio_tool", "A studio tool call"),
    ("continued", "The second message continued the conversation"),
]
EDIT_TOOLS = {"Edit", "MultiEdit"}


class CheckError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def key_of(connection_id: str, model: str) -> str:
    return f"{connection_id}::{model}"


def target_of(connection: dict) -> dict:
    """What a result was measured against: a connection re-pointed elsewhere under the same id
    must not keep the old result."""
    return {"kind": connection.get("kind"), "provider": connection.get("provider") or None,
            "base_url": connection.get("base_url") or None}


def _tidy(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().splitlines())


def _file(workspace: Path, name: str) -> str | None:
    try:
        return (workspace / name).read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def tool_calls(events: list[dict], turn_id: str | None = None) -> list[dict]:
    """The top-level tool calls of a turn (or of all turns), each once, with how it ended."""
    calls: dict[str, dict] = {}
    for event in events:
        if turn_id and event.get("turn_id") != turn_id:
            continue
        if event["type"] == "tool_call" and not event.get("parent_tool_id"):
            known = calls.get(event["tool_id"])
            if known:       # a file tool is announced early, then again complete
                known.update(name=event.get("name"), input=event.get("input") or known["input"],
                             title=event.get("title") or known["title"])
            else:
                calls[event["tool_id"]] = {"id": event["tool_id"], "name": event.get("name"),
                                           "title": event.get("title") or "", "input": event.get("input") or {},
                                           "status": None, "turn": event.get("turn_id")}
        elif event["type"] == "tool_result" and event.get("tool_id") in calls:
            calls[event["tool_id"]]["status"] = event.get("status")
    return list(calls.values())


def had_parallel_calls(events: list[dict], turn_id: str) -> bool:
    """Whether some step of the turn asked for two tools at once: two calls were open (asked for,
    not yet answered) at the same moment."""
    open_calls: set[str] = set()
    for event in events:
        if event.get("turn_id") != turn_id or event.get("parent_tool_id"):
            continue
        if event["type"] == "tool_call":
            open_calls.add(event["tool_id"])
            if len(open_calls) >= 2:
                return True
        elif event["type"] == "tool_result":
            open_calls.discard(event.get("tool_id"))
    return False


def _is_total(line: str) -> bool:
    try:
        return float(line.strip()) == float(TOTAL)
    except ValueError:
        return False


def _edited_in_place(calls: list[dict], name: str) -> bool:
    return any(call["name"] in EDIT_TOOLS and call["status"] == "ok"
               and str(call["input"].get("file_path") or "").replace("\\", "/").endswith(name) for call in calls)


def verify(workspace: Path, events: list[dict], turns: list[dict]) -> list[dict]:
    """→ the five parts, each {id, label, ok, detail}. `turns` is [{turn_id, status, error}] for
    the messages that were sent (one or two). Pure: reads the folder and the events, nothing else."""
    first = turns[0] if turns else None
    second = turns[1] if len(turns) > 1 else None
    calls = tool_calls(events, first["turn_id"]) if first else []
    result: dict[str, tuple[bool, str]] = {}

    if first is None:
        result["finished"] = (False, "The first message could not be sent.")
    elif first["status"] != "completed":
        result["finished"] = (False, f"The first message {_ended(first)}")
    elif second is None:
        result["finished"] = (False, "The second message was not sent.")
    elif second["status"] != "completed":
        result["finished"] = (False, f"The second message {_ended(second)}")
    else:
        result["finished"] = (True, "")

    if first is None or (first["status"] != "completed" and not calls):
        # Nothing ran at all (a refused key, an unknown model): one reason, not five.
        return [{"id": part, "label": label, "ok": False,
                 "detail": result["finished"][1] if part == "finished" else "Not reached."} for part, label in PARTS]
    if had_parallel_calls(events, first["turn_id"]):
        result["parallel"] = (True, "")
    else:
        result["parallel"] = (False, f"The model made its {len(calls)} tool call{'s' if len(calls) != 1 else ''} one "
                                     "at a time; it was asked to read two files in one step." if calls else
                                     "The model made no tool call at all.")

    wrong = [name for name in EXPECTED if _tidy(_file(workspace, name) or "") != _tidy(EXPECTED[name])]
    rewritten = [name for name in EXPECTED if not _edited_in_place(calls, name)]
    if wrong:
        untouched = [name for name in wrong if _tidy(_file(workspace, name) or "") == _tidy(SEED[name])]
        result["in_place"] = (False, (
            f"{' and '.join(untouched)} {'was' if len(untouched) == 1 else 'were'} not changed."
            if len(untouched) == len(wrong) else
            f"{' and '.join(wrong)} {'does' if len(wrong) == 1 else 'do'} not hold exactly the asked-for changes "
            "(something else in the file changed, or a change is missing)."))
    elif rewritten:
        result["in_place"] = (False, f"The content is right, but {' and '.join(rewritten)} "
                                     f"{'was' if len(rewritten) == 1 else 'were'} written again whole instead of "
                                     "edited in place.")
    else:
        result["in_place"] = (True, "")

    total = (_file(workspace, "total.txt") or "").strip().splitlines()
    asked = [call for call in calls if call["name"] == "mcp__studio__query_files"]
    if not asked:
        result["studio_tool"] = (False, "The model never called the studio's query_files tool.")
    elif not any(call["status"] == "ok" for call in asked):
        result["studio_tool"] = (False, "The model called query_files, but the call failed.")
    elif not total:
        result["studio_tool"] = (False, "query_files answered, but total.txt was not written.")
    elif not _is_total(total[0]):
        result["studio_tool"] = (False, "query_files answered, but total.txt does not start with the number it "
                                        f"returned ({TOTAL}).")
    else:
        result["studio_tool"] = (True, "")

    if second is None or second["status"] != "completed":
        result["continued"] = (False, "Not reached." if second is None else f"The second message {_ended(second)}")
    elif len(total) >= 2 and total[1].strip() == CODE_WORD:
        result["continued"] = (True, "")
    elif any(CODE_WORD in line for line in total):
        result["continued"] = (False, "The code word is in total.txt, but not alone on its second line.")
    else:
        result["continued"] = (False, "The code word from the first message is not in total.txt: the model did "
                                      "not carry the conversation into the second message.")
    return [{"id": part, "label": label, "ok": result[part][0], "detail": result[part][1]} for part, label in PARTS]


def _ended(turn: dict) -> str:
    if turn["status"] == "stopped":
        return "was stopped."
    # A provider's error often arrives as JSON inside JSON, with its line breaks spelled out.
    words = str(turn.get("error") or "no reason was given").replace("\\n", " ").replace('\\"', '"')
    return f"failed: {' '.join(words.split())[:600]}"


def _reason(part: dict) -> str:
    """The first part that failed, as the one sentence shown beside the model."""
    text = part["detail"] if part["id"] == "finished" else f"{part['label']}: {part['detail']}"
    return text if len(text) <= 420 else text[:419].rstrip() + "…"


def cost_warning(connection: dict, model: str) -> str | None:
    """What to say before a check that may cost money; None when it cannot."""
    cost = model_cost(connection, model)
    if cost == "free":
        return None
    if cost == "plan":
        return ("The check sends two short messages on your Claude plan. It costs no money, but it uses a "
                "little of the plan's allowance.")
    if cost == "unknown":
        return (f"The studio cannot tell whether {model} costs money on “{connection['label']}”: a gateway "
                f"prices its own models. {CHECK_SIZE}")
    return f"{model} is billed per use by its provider. {CHECK_SIZE}"


# Measured on 12 November 2026 (GPT 6.1 Sol through OpenRouter: 159,827 tokens in, 757 out, $0.10).
# Most of it is the agent program's own instructions, sent again at every step.
CHECK_SIZE = ("The check sends two short messages, which come to roughly 150,000 tokens in and under 1,000 out: "
              "about ten cents on a large model, less on a small one.")


class ModelChecks:
    def __init__(self, studio: "Studio"):
        self.studio = studio
        self.path = studio.settings.data_dir / "model-checks.json"
        self.running: dict | None = None       # {key, connection, model, started_at, phase, project}
        self._task: asyncio.Task | None = None
        try:
            self.seconds = max(20.0, float(os.environ.get("STUDIO2_CHECK_SECONDS") or MESSAGE_SECONDS))
        except ValueError:
            self.seconds = MESSAGE_SECONDS

    # -- stored results ----------------------------------------------------------------------------
    def results(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        found = data.get("results") if isinstance(data, dict) else None
        return found if isinstance(found, dict) else {}

    def result_for(self, connection: dict, model: str) -> dict | None:
        result = self.results().get(key_of(str(connection["id"]), model))
        if not result or result.get("target") != target_of(connection):
            return None
        return result

    def _save(self, result: dict) -> None:
        results = self.results()
        results[key_of(result["connection"], result["model"])] = result
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, {"version": VERSION, "results": results})

    # -- what the Library shows --------------------------------------------------------------------
    def view(self) -> dict:
        studio = self.studio
        connections = []
        for raw, public in zip(studio.registry.connections(), studio.registry.describe_all()):
            models = [{**model, "cost": model_cost(raw, model["id"]),
                       "warning": cost_warning(raw, model["id"]),
                       "check": self.result_for(raw, model["id"])} for model in public["models"]]
            connections.append({**public, "models": models, "provider": raw.get("provider") or None,
                                "small_model": raw.get("small_model") or None,
                                "picking_model": picking_model(raw)})
        running = {k: v for k, v in self.running.items() if k != "project"} if self.running else None
        return {"connections": connections, "running": running,
                "parts": [label for _part, label in PARTS], "check_version": VERSION}

    # -- running one -------------------------------------------------------------------------------
    def start(self, connection_id: str, model: str, confirm_cost: bool = False) -> dict:
        studio = self.studio
        connection = next((c for c in studio.registry.connections() if c["id"] == connection_id), None)
        if connection is None:
            raise CheckError(404, "unknown_connection", "There is no such model account.")
        if model not in [m["id"] for m in connection["models"]]:
            raise CheckError(404, "unknown_model", f"“{connection['label']}” does not offer the model {model}.")
        if self.running:
            raise CheckError(409, "check_running", f"A check of {self.running['model']} is still running. "
                                                   "Wait for it, or stop it.")
        public = studio.registry.harness(connection["kind"]).describe(connection)
        if not public["available"]:
            raise CheckError(409, "connection_unavailable", public["reason"] or "This model account is not available.")
        warning = cost_warning(connection, model)
        if warning and not confirm_cost:
            raise CheckError(400, "cost_not_confirmed", warning)
        self.running = {"key": key_of(connection_id, model), "connection": connection_id, "model": model,
                        "started_at": now_iso(), "phase": "Starting", "project": None}
        self._task = asyncio.create_task(self._run(connection, model))
        return {k: v for k, v in self.running.items() if k != "project"}

    def stop(self) -> bool:
        """Stop the running check. → False when none is running."""
        if not self.running or not self._task:
            return False
        self.running["stopping"] = True
        pid = self.running.get("project")
        if pid and pid in self.studio.turns.active:
            self.studio.turns.stop(pid, self.studio.turns.active[pid])
        return True

    async def wait(self) -> None:
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def stop_all(self) -> None:
        if self.stop():
            await self.wait()

    async def sweep(self) -> None:
        """Remove scratch projects a check left behind (the studio was stopped in the middle of one)."""
        for project in self.studio.store.list():
            if project.get("check"):
                await self._remove(project["id"])

    async def _remove(self, pid: str) -> None:
        studio = self.studio
        try:
            await studio.delete_project(pid)
        except ProjectNotFound:
            return
        except Exception:
            log.exception("Could not remove the model-check project %s", pid)
            return
        for folder in (studio.store.root / ".trash").glob(f"{pid}-*"):
            await asyncio.to_thread(shutil.rmtree, folder, True)     # a fixture, not anyone's work

    async def _message(self, pid: str, text: str, connection: dict, model: str, notes: list[str]) -> dict:
        """Send one message and wait for it; a question the agent asks is answered no."""
        studio = self.studio
        started = time.monotonic()
        turn_id = studio.turns.start(studio.store.get(pid), text, None, connection["id"], model)
        task = studio.turns.tasks.get(pid)
        timed_out = False
        while task is not None and not task.done():
            for approval_id, future in list(studio.turns.pending.get(turn_id, {}).items()):
                if not future.done() and studio.turns.answer(turn_id, approval_id, "deny"):
                    notes.append("The model asked to run a command that needs your permission; the check said no.")
            if not timed_out and time.monotonic() - started > self.seconds:
                timed_out = True
                studio.turns.stop(pid, turn_id)
            await asyncio.sleep(0.2)
        finished = next((e for e in reversed(studio.log(pid).read())
                         if e["type"] == "turn_finished" and e.get("turn_id") == turn_id), None) or {}
        usage = finished.get("usage") or {}
        status = finished.get("status") or "failed"
        error = finished.get("error")
        if timed_out:
            status, error = "failed", f"it took longer than {int(self.seconds // 60)} minutes and was stopped."
        return {"turn_id": turn_id, "status": status, "error": error,
                "seconds": round(time.monotonic() - started, 1),
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                "cost_usd": usage.get("cost_usd"), "cost_estimated": bool(usage.get("cost_estimated"))}

    async def _run(self, connection: dict, model: str) -> None:
        studio = self.studio
        started = time.monotonic()
        notes: list[str] = []
        turns: list[dict] = []
        pid = None
        parts: list[dict] = []
        calls: list[dict] = []
        said = ""
        try:
            project = await studio.create_project(f"Model check: {model}"[:120], "blank")
            pid = project["id"]
            studio.store.update(pid, hidden=True, check=True, approvals="ask")
            self.running["project"] = pid
            workspace = studio.store.workspace(pid)
            for name, content in SEED.items():
                (workspace / name).parent.mkdir(parents=True, exist_ok=True)
                (workspace / name).write_text(content, "utf-8")
            await studio.checkpoint(pid, "user", "Model check files")

            self.running["phase"] = "First message: two files to edit, a studio tool to call"
            turns.append(await self._message(pid, FIRST, connection, model, notes))
            if turns[0]["status"] == "completed" and not self.running.get("stopping"):
                self.running["phase"] = "Second message: does the conversation continue"
                turns.append(await self._message(pid, SECOND, connection, model, notes))
            events = studio.log(pid).read()
            parts = verify(workspace, events, turns)
            calls = tool_calls(events)
            notes.extend(str(e.get("message")) for e in events if e["type"] == "notice" and e.get("message"))
            said = "".join(e.get("text") or "" for e in events
                           if e["type"] == "message_chunk" and turns and e.get("turn_id") == turns[-1]["turn_id"])
        except Exception as exc:    # the check itself broke: say so, as a failed check
            log.exception("The model check of %s failed to run", model)
            notes.append(f"The check could not be run to the end ({exc.__class__.__name__}: {exc}).")
            parts = parts or verify(Path("/nonexistent"), [], turns)
        finally:
            if pid:
                await self._remove(pid)
        stopped = bool(self.running and self.running.get("stopping")) or any(t["status"] == "stopped" for t in turns)
        failed = [part for part in parts if not part["ok"]]
        status = "stopped" if stopped else "failed" if failed else "passed"
        costs = [t["cost_usd"] for t in turns if isinstance(t.get("cost_usd"), (int, float))]
        order = {turn["turn_id"]: index for index, turn in enumerate(turns, 1)}
        result = {
            "connection": str(connection["id"]), "model": model, "status": status,
            "reason": None if status == "passed" else "The check was stopped before it finished." if stopped
            else _reason(failed[0]),
            "parts": parts, "seconds": round(time.monotonic() - started, 1), "steps": len(calls),
            "messages": [{k: v for k, v in turn.items() if k != "turn_id"} for turn in turns],
            "input_tokens": sum(t["input_tokens"] or 0 for t in turns) or None,
            "output_tokens": sum(t["output_tokens"] or 0 for t in turns) or None,
            "cost_usd": round(sum(costs), 6) if costs else None,
            "cost_estimated": any(t.get("cost_estimated") for t in turns),
            "notes": list(dict.fromkeys(notes)),
            "trace": [{"message": order.get(call["turn"]), "name": call["name"], "title": call["title"][:120],
                       "status": call["status"]} for call in calls[:60]],
            "said": " ".join(said.split())[:300],
            "at": now_iso(), "check_version": VERSION, "target": target_of(connection),
            "studio_version": __version__,
        }
        try:
            if not stopped or turns:     # a check stopped before its first message leaves the old result
                self._save(result)
        finally:
            self.running = None
