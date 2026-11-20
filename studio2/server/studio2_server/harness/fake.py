"""A scripted harness: zero model calls, deterministic, but it really edits the workspace.

Used when STUDIO2_FAKE=1 and in tests. Behaviour is keyed on the user's text:
"fail" raises, a selection changes only that element, "slow" takes 20 steps (to exercise
Stop), "dashboard" writes a demo dashboard, the model check's two messages are carried out as
asked (so the check itself is tested without a model), anything else adds a heading and a paragraph.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time

from ..workspace import page_to_file, safe_path
from .base import Emit, TurnRequest, TurnResult, public_connection

NOTE = "Placeholder demo content from the fake harness — no model was called and these numbers are not real."
DASH_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo dashboard</title>
<style>
  :root { color-scheme: light dark; --line: color-mix(in srgb, CanvasText 15%, Canvas); }
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; background: Canvas; color: CanvasText; }
  main { max-width: 64rem; margin: 0 auto; padding: 2.5rem 2rem; }
  .note { color: GrayText; }
  .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.5rem 0; }
  .card { border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.25rem; }
  .card b { display: block; font-size: 1.8rem; }
  svg { width: 100%; height: auto; border: 1px solid var(--line); border-radius: 12px; }
  svg rect { fill: #4c78dd; } svg text { fill: currentColor; font-size: 12px; }
</style>
</head>
<body>
<main>
<h1>Demo dashboard</h1>
<p class="note">""" + NOTE + """</p>
"""
DASH_CARDS = """<section class="cards">
<div class="card"><span>Example metric A</span><b>128</b></div>
<div class="card"><span>Example metric B</span><b>42%</b></div>
<div class="card"><span>Example metric C</span><b>7</b></div>
</section>
"""
DASH_CHART = """<h2>Example bar chart</h2>
<svg viewBox="0 0 400 180" role="img" aria-label="Placeholder bar chart">
<rect x="30" y="90" width="50" height="60"/><rect x="110" y="50" width="50" height="100"/>
<rect x="190" y="110" width="50" height="40"/><rect x="270" y="30" width="50" height="120"/>
<text x="55" y="168" text-anchor="middle">Q1</text><text x="135" y="168" text-anchor="middle">Q2</text>
<text x="215" y="168" text-anchor="middle">Q3</text><text x="295" y="168" text-anchor="middle">Q4</text>
</svg>
"""
FAKE_SERVER = '''"""Fake-harness demo back end: standard library only."""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STARTED = time.time()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/uptime":
            body, kind = json.dumps({"uptime_seconds": round(time.time() - STARTED, 1)}).encode(), "application/json"
        else:
            body = (b"<!doctype html><html><body><main><h1>Fake back end</h1>"
                    b"<p>Served by server.py. No model was called.</p></main></body></html>")
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print("fake back end listening on", os.environ["PORT"], flush=True)
ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''
DASH_TAIL = "</main>\n</body>\n</html>\n"


def into_main(page: str, fragment: str) -> str:
    for marker in ("</main>", "</body>"):
        at = page.lower().rfind(marker)
        if at >= 0:
            return page[:at] + fragment + page[at:]
    return page + fragment


class _Script:
    def __init__(self, request: TurnRequest, emit: Emit, delay: float):
        self.request, self.emit, self.delay = request, emit, delay
        self.tools = 0
        self.index = request.workspace / "index.html"

    async def pause(self, factor: float = 1.0) -> None:
        await asyncio.sleep(self.delay * factor)

    def plan(self, steps: list[str], done: int) -> None:
        status = lambda i: "completed" if i < done else "in_progress" if i == done else "pending"  # noqa: E731
        self.emit({"type": "plan", "entries": [{"content": s, "status": status(i)} for i, s in enumerate(steps)]})

    async def tool(self, name: str, kind: str, title: str, input: dict, summary: str, action=None) -> None:
        self.tools += 1
        tool_id = f"{self.request.turn_id}_tool{self.tools}"
        self.emit({"type": "tool_call", "tool_id": tool_id, "name": name, "kind": kind, "title": title,
                   "input": input, "parent_tool_id": None})
        await self.pause()
        if action:
            action()
        self.emit({"type": "tool_result", "tool_id": tool_id, "status": "ok", "summary": summary})

    async def write(self, content: str, title: str, path: str = "index.html") -> None:
        target = safe_path(self.request.workspace, path)
        await self.tool("Write", "edit", title, {"file_path": path}, f"Wrote {path} ({len(content)} bytes)",
                        lambda: target.write_text(content, "utf-8"))

    async def say(self, text: str) -> None:
        words = text.split(" ")
        for start in range(0, len(words), 4):
            chunk = " ".join(words[start:start + 4]) + (" " if start + 4 < len(words) else "")
            self.emit({"type": "message_chunk", "message_id": f"{self.request.turn_id}_m1", "text": chunk})
            await self.pause(0.25)


class FakeHarness:
    kind = "fake"

    def describe(self, connection: dict) -> dict:
        return public_connection(connection, True, None)

    async def run(self, request: TurnRequest, emit: Emit) -> TurnResult:
        started = time.monotonic()
        text = (request.text or request.prompt).strip()
        low = text.lower()
        script = _Script(request, emit, float(os.environ.get("STUDIO2_FAKE_DELAY", "0.4")))
        steps = ["Read the current page", "Update the page", "Summarise what changed"]

        from ..modelcheck import MARKER
        if MARKER in text:
            return await self._model_check(script, request, text, started)
        script.plan(steps, 0)
        emit({"type": "thought_chunk", "message_id": f"{request.turn_id}_t1",
              "text": f"(fake harness) The request is: {text[:200]!r}. I will read index.html first."})
        page = script.index.read_text("utf-8") if script.index.is_file() else ""
        await script.tool("Read", "read", "Read index.html", {"file_path": "index.html"}, f"{len(page)} bytes")
        if "fail" in low:
            raise RuntimeError("The fake harness was asked to fail (the request contains “fail”).")
        script.plan(steps, 1)
        answered = await self._ask(script, request, low)

        if request.selection:
            reply = await self._edit_selection(script, request.selection, text)
        elif "backend" in low:
            await script.write(FAKE_SERVER, "Write server.py", "server.py")
            await script.write('{"run": "python3 server.py"}\n', "Write studio.json", "studio.json")
            reply = "Added a small Python back end (server.py) and studio.json so the studio runs it."
        elif "ship recipe" in low and request.studio_tool:
            # "propose ship recipe {json}": the scripted stand-in for an agent that read the app.
            raw = text[text.find("{"):] if "{" in text else "{}"
            try:
                recipe = json.loads(raw)
            except ValueError:
                recipe = {}
            reply = await self._studio_tool(script, request, "propose_ship_recipe", recipe,
                                            "Propose how to make the app live")
        elif low.startswith("use skill ") and text[10:].strip():
            # The stand-in for the agent program's own Skill tool: the studio counts these.
            await script.tool("Skill", "other", f"Use skill {text[10:].strip()}", {"skill": text[10:].strip()}, "Loaded.")
            reply = f"Used the skill {text[10:].strip()}."
        elif low.startswith("find skill ") and request.studio_tool:
            found = await request.studio_tool("search_library", {"query": text[11:], "kind": "skill"})
            listed = json.loads(found["content"][0]["text"]).get("skills") or []
            reply = await self._studio_tool(script, request, "search_library", {"query": text[11:], "kind": "skill"},
                                            "Search the library")
            if listed:
                reply = await self._studio_tool(script, request, "activate_skill", {"id": listed[0]["id"]},
                                                f"Switch on the skill {listed[0]['name']}")
        elif low.startswith("need connector ") and request.studio_tool:
            reply = await self._studio_tool(script, request, "request_connector",
                                            {"id": text[15:].strip(), "reason": "The request needs it."},
                                            f"Ask for the connector {text[15:].strip()}")
        elif low.startswith("check file ") and request.studio_tool:
            # The stand-in for an agent that checks the figures in one of the project's files.
            reply = await self._studio_tool(script, request, "check_file", {"path": text[11:].strip()},
                                            f"Check the figures in {text[11:].strip()}")
        elif "make it live" in low and request.studio_tool:
            reply = await self._studio_tool(script, request, "make_live", {}, "Make the app live")
        elif "risky" in low:
            allowed = await request.approve("Bash", "Run a shell command", "brew install example-tool",
                                            "It installs or changes software on this machine.")
            page = into_main(page, f"<p>The risky command was {'allowed' if allowed else 'denied'}; "
                                   "the fake harness did not run anything.</p>\n")
            await script.write(page, "Edit index.html (approval result)")
            reply = "You allowed the command." if allowed else "You denied the command, so I skipped it."
        elif "slow" in low:
            for step in range(1, 21):
                page = into_main(page, f"<p>Slow step {step} of 20.</p>\n")
                await script.write(page, f"Edit index.html (step {step} of 20)")
            reply = "Added twenty lines, one at a time."
        elif "dashboard" in low:
            await script.write(DASH_HEAD + DASH_TAIL, "Write index.html (page shell)")
            await script.write(DASH_HEAD + DASH_CARDS + DASH_TAIL, "Edit index.html (summary cards)")
            await script.write(DASH_HEAD + DASH_CARDS + DASH_CHART + DASH_TAIL, "Edit index.html (bar chart)")
            reply = "Built a demo dashboard with three cards and a bar chart. It is placeholder content."
        else:
            heading = html.escape(" ".join(text.split())[:60]) or "Untitled"
            page = into_main(page, f"<h1>{heading}</h1>\n")
            await script.write(page, "Edit index.html (heading)")
            page = into_main(page, f"<p>You asked: “{html.escape(text)}”. {NOTE}</p>\n")
            await script.write(page, "Edit index.html (paragraph)")
            reply = "Added a heading and a paragraph that echo your request."
        if answered:
            reply = f"{answered} {reply}"

        script.plan(steps, 2)
        await self._verify(script, request, low)
        if "suggest next steps" in low and request.studio_tool:
            # Scripted, and labelled so: the stand-in for an agent that offers what could follow.
            await self._studio_tool(script, request, "suggest_next_steps", {"steps": [
                {"label": "(fake) Add a summary table", "text": "(fake) Add a summary table at the top of the page."},
                {"label": "(fake) Add a chart", "text": "(fake) Add a bar chart of the figures on the page."}]},
                "Suggest next steps")
        await script.say(reply + " (Fake harness: no model was called.)")
        script.plan(steps, 3)
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "cost_estimated": False,
                 "duration_ms": int((time.monotonic() - started) * 1000)}
        return TurnResult("completed", request.session_id or f"fake_{request.project_id}", None, usage)

    _remembered: dict[str, str] = {}     # project id → the code word of the model check's first message

    async def _model_check(self, script: _Script, request: TurnRequest, text: str, started: float) -> TurnResult:
        """The model check (modelcheck.py), done to the letter: two reads in one step, edits in
        place in two files, a studio tool call, and a second message that needs the first."""
        workspace = request.workspace
        usage = lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "cost_estimated": False,  # noqa: E731
                         "duration_ms": int((time.monotonic() - started) * 1000)}
        session = request.session_id or f"fake_{request.project_id}"
        word = re.search(r"code word for my next message: ([\w-]+)", text)
        if word:
            self._remembered[request.project_id] = word.group(1)
            ids = [f"{request.turn_id}_tool{n}" for n in (1, 2)]
            script.tools = 2
            for tool_id, name in zip(ids, ("index.html", "styles.css")):    # both asked for before either answers
                script.emit({"type": "tool_call", "tool_id": tool_id, "name": "Read", "kind": "read",
                             "title": f"Read {name}", "input": {"file_path": name}, "parent_tool_id": None})
            await script.pause()
            for tool_id in ids:
                script.emit({"type": "tool_result", "tool_id": tool_id, "status": "ok", "summary": "read"})

            def swap(name: str, old: str, new: str):
                target = workspace / name
                return lambda: target.write_text(target.read_text("utf-8").replace(old, new), "utf-8")

            for name, old, new in (("index.html", "<h1>Orchard report</h1>", "<h1>Orchard report 2</h1>"),
                                   ("index.html", "Status: Draft", "Status: Checked"),
                                   ("styles.css", "--accent: #2255aa", "--accent: #117733")):
                await script.tool("Edit", "edit", f"Edit {name}", {"file_path": name, "old_string": old,
                                                                 "new_string": new}, "edited", swap(name, old, new))
            answer = await self._studio_tool(script, request, "query_files",
                                             {"sql": "SELECT sum(crates) AS total FROM 'data/harvest.csv'"},
                                             "Query project files")
            number = re.search(r'"rows": \[\[(\d+)', answer)
            await script.write((number.group(1) if number else "?") + "\n", "Write total.txt", "total.txt")
        else:
            remembered = self._remembered.pop(request.project_id, "")
            target = workspace / "total.txt"
            before = target.read_text("utf-8") if target.is_file() else ""
            await script.tool("Edit", "edit", "Edit total.txt", {"file_path": "total.txt"}, "edited",
                              lambda: target.write_text(before.rstrip("\n") + "\n" + remembered + "\n", "utf-8"))
        await self._verify(script, request, (request.text or "").lower())
        await script.say("DONE")
        return TurnResult("completed", session, None, usage())

    @classmethod
    async def _ask(cls, script: "_Script", request: TurnRequest, low: str) -> str:
        """With "Ask me first" on, ask two scripted questions (labelled as the fake harness's) before
        the work, so tests and the mock cover the card ("no questions" in the text leaves them out).
        → a sentence for the reply saying what came back."""
        if not (request.options or {}).get("ask") or not request.studio_tool or "no questions" in low:
            return ""
        said = await cls._studio_tool(script, request, "ask_person", {
            "why": "(fake harness) Scripted questions; no model decided to ask them.",
            "questions": [
                {"question": "(fake) Who is this for?", "choices": [
                    {"label": "(fake) The team", "description": "Scripted first choice."},
                    {"label": "(fake) A customer", "description": "Scripted second choice."}]},
                {"question": "(fake) Anything it must include?"}]}, "Ask you before starting")
        return "I asked first. " + said[:200]

    @classmethod
    async def _verify(cls, script: "_Script", request: TurnRequest, low: str) -> None:
        """With verify on, report a small fixed result, labelled as the fake harness's, so tests cover
        the event and the card ("skip the report" in the text leaves it out)."""
        if not (request.options or {}).get("verify") or not request.studio_tool or "skip the report" in low:
            return
        await cls._studio_tool(script, request, "report_verification", {
            "checked": 3, "passed": 1, "fixed": 1, "failed": 0, "unverifiable": 1,
            "summary": "(fake harness) A scripted check result; nothing on the page was really checked.",
            "findings": [
                {"what": "(fake) a figure", "where": "index.html", "status": "passed", "note": "scripted"},
                {"what": "(fake) a total", "where": "index.html", "status": "fixed", "note": "scripted"},
                {"what": "(fake) a claim", "where": "index.html", "status": "unverifiable", "note": "scripted"}]},
            "Report the verification")

    @staticmethod
    async def _studio_tool(script: _Script, request: TurnRequest, name: str, args: dict, title: str) -> str:
        script.tools += 1
        tool_id = f"{request.turn_id}_tool{script.tools}"
        script.emit({"type": "tool_call", "tool_id": tool_id, "name": f"mcp__studio__{name}", "kind": "other",
                     "title": title, "input": args, "parent_tool_id": None})
        result = await request.studio_tool(name, args)
        text = " ".join(part.get("text", "") for part in result.get("content") or [])
        failed = bool(result.get("is_error"))
        script.emit({"type": "tool_result", "tool_id": tool_id, "status": "error" if failed else "ok",
                     "summary": text[:4000]})
        return f"{title}: {'it did not work' if failed else 'done'}. {text[:300]}"

    @staticmethod
    async def _edit_selection(script: _Script, selection: dict, text: str) -> str:
        old = (selection.get("text") or "").strip()
        quoted = re.search(r'"([^"]+)"|“([^”]+)”', text)
        new = (quoted.group(1) or quoted.group(2)) if quoted else f"{old} (edited)"
        rel = page_to_file(script.request.workspace, selection.get("path") or "/")
        target = safe_path(script.request.workspace, rel)
        source = target.read_text("utf-8") if target.is_file() else ""
        outer = selection.get("html") or ""
        safe_new = html.escape(new, quote=False)
        # Prefer the element's own markup (unique even when its text repeats elsewhere).
        for needle in (old, html.escape(old, quote=False)):
            scope = outer if needle and outer.count(needle) == 1 and source.count(outer) == 1 else needle
            if needle and source.count(scope) == 1:
                await script.write(source.replace(scope, scope.replace(needle, safe_new)),
                                   f"Edit {rel} (selected {selection.get('tag') or 'element'})", rel)
                return f"Changed the selected {selection.get('tag') or 'element'} to “{new}”."
        return "I could not find the selected element's text exactly once in the file, so nothing was changed."
