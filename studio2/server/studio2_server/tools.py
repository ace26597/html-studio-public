"""The studio's own tools, offered to the agent as an in-process MCP server named `studio`.

CONTRACT.md, "Studio tools". Each tool is a plain coroutine `(studio, project_id, args) → dict`
so it is tested without an agent; `build_server` wraps them for the Claude Agent SDK.
"""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import workspace as ws
from .check import check_page, run_sql
from .datasources import DataError, run_query, run_schema, to_json
from . import effort, generate, media, nextsteps, office
from .look import LookUnavailable
from . import search as web_search
from .search import SearchError
from .sources import SourceError, Sources, download, extract, html_to_text

SERVER_NAME = "studio"
RESULT_CAP = 60_000


class ToolError(Exception):
    pass


def _workspace(studio, project_id: str) -> Path:
    return studio.store.workspace(project_id)


def _source_view(item: dict, created: bool, text: str, max_chars: int) -> dict:
    return {"id": item["id"], "title": item["title"], "url": item.get("url"), "kind": item["kind"],
            "chars": item["chars"], "pages": item.get("pages"), "saved": item["path"],
            "already_saved": not created, "text_start": text[:max_chars],
            "more": "Use read_source with this id (offset, or find) to read the rest." if len(text) > max_chars else None}


async def t_search_web(studio, project_id, args):
    try:  # the same query asked twice in one turn (by two helpers, say) is answered once
        turn = studio.store.get(project_id).get("running_turn") or ""
    except Exception:
        turn = ""
    return await web_search.search(args.get("query"), args.get("max_results") or 8, scope=f"{project_id}:{turn}")


async def t_fetch_source(studio, project_id, args):
    url = str(args.get("url") or "").strip()
    max_chars = max(500, min(int(args.get("max_chars") or 6000), 40_000))
    sources = Sources(_workspace(studio, project_id))
    render, fetched = bool(args.get("render")), None
    saved = None if args.get("refresh") else await asyncio.to_thread(sources.by_url, url)
    if saved is not None:  # no download and no text again: the agent already has this one
        return {"id": saved["id"], "title": saved["title"], "url": saved.get("url"), "kind": saved["kind"],
                "chars": saved["chars"], "saved": saved["path"], "already_saved": True,
                "note": f"This address is already saved as {saved['id']}; its text is not repeated. Read from it "
                        "with read_source (find, or offset). Pass refresh: true only if the page has changed."}
    if not render:
        try:
            fetched = await download(url)
        except SourceError as exc:
            # A site that refuses a plain download often serves a real browser.
            if not exc.refused or studio.looker.reason() is not None:
                raise
            render = True
    if render:
        try:
            final, markup = await studio.looker.rendered(url)
        except LookUnavailable as exc:
            raise ToolError(f"render is not available: {exc}") from None
        except Exception as exc:
            raise ToolError(f"The browser could not load {url} ({' '.join(str(exc).split())[:200]}).") from None
        title, text = html_to_text(markup)
        item, created = await asyncio.to_thread(sources.save, text=text, title=title, kind="html", url=final,
                                                requested=url)
    else:
        data, content_type, final = fetched
        kind, title, text, pages = await asyncio.to_thread(extract, data, content_type, final)
        item, created = await asyncio.to_thread(
            sources.save, text=text, title=title, kind=kind, url=final, pages=pages, requested=url,
            original=data if kind == "pdf" else None, original_ext=".pdf" if kind == "pdf" else "")
    return _source_view(item, created, sources.text(item["id"]), max_chars)


async def t_read_source(studio, project_id, args):
    sources = Sources(_workspace(studio, project_id))
    return await asyncio.to_thread(sources.read, str(args.get("id") or ""), args.get("find") or None,
                                   args.get("offset") or 0, args.get("max_chars") or 12_000, args.get("end"))


async def t_add_source(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    relative = str(args.get("path") or "")
    try:
        path = ws.safe_path(workspace, relative)
    except Exception:
        raise ToolError("path must be a file inside the project.") from None
    sources = Sources(workspace)
    item, created = await asyncio.to_thread(sources.add_file, path, relative, args.get("title") or None)
    return _source_view(item, created, sources.text(item["id"]), 3000)


def _save_rows(workspace: Path, save_as: str, columns: list[str], rows: list) -> str:
    try:
        target = ws.safe_path(workspace, save_as)
    except Exception:
        raise ToolError("save_as must be a path inside the project.") from None
    if target.suffix.lower() not in (".csv", ".json"):
        raise ToolError("save_as must end in .csv or .json.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows([["" if v is None else to_json(v) for v in row] for row in rows])
    else:
        target.write_text(json.dumps([dict(zip(columns, map(to_json, row))) for row in rows]), "utf-8")
    return target.relative_to(workspace.resolve()).as_posix()


async def t_query_files(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    save_as = args.get("save_as")
    limit = max(1, min(int(args.get("limit") or (500_000 if save_as else 200)), 500_000 if save_as else 5_000))
    columns, rows = await asyncio.to_thread(run_sql, workspace, str(args.get("sql") or ""), limit + 1)
    truncated, rows = len(rows) > limit, rows[:limit]
    if save_as:
        saved = await asyncio.to_thread(_save_rows, workspace, save_as, columns, rows)
        return {"columns": columns, "row_count": len(rows), "truncated": truncated, "saved": saved}
    return {"columns": columns, "rows": [[to_json(v) for v in row] for row in rows],
            "row_count": len(rows), "truncated": truncated}


def _attached(studio, project_id: str, name: str) -> dict:
    allowed = studio.store.get(project_id).get("data_sources") or []
    return studio.data.by_name(name, allowed)


async def t_database_schema(studio, project_id, args):
    source = _attached(studio, project_id, str(args.get("source") or ""))
    return await asyncio.to_thread(run_schema, source, studio.data.password(source["id"]), args.get("table") or None)


async def t_query_database(studio, project_id, args):
    source = _attached(studio, project_id, str(args.get("source") or ""))
    workspace, save_to = _workspace(studio, project_id), None
    if args.get("save_as"):
        try:
            save_to = ws.safe_path(workspace, args["save_as"])
        except Exception:
            raise ToolError("save_as must be a path inside the project.") from None
        if save_to.suffix.lower() not in (".csv", ".json"):
            raise ToolError("save_as must end in .csv or .json.")
    limit = int(args.get("limit") or 0) or (500_000 if save_to else 1_000)
    result = await asyncio.to_thread(run_query, source, studio.data.password(source["id"]),
                                     str(args.get("sql") or ""), limit, save_to)
    if save_to:
        result["saved"] = save_to.relative_to(workspace.resolve()).as_posix()
    return result


async def t_look_at_page(studio, project_id, args):
    path = str(args.get("path") or "/")
    if not path.startswith("/") and "://" not in path:
        path = "/" + path          # "index.html" is what the other page tools take
    if "://" in path or path.startswith("//"):
        raise ToolError("path must start with / and stay on the project's own URL.")
    project = await studio.open_preview(studio.store.get(project_id))
    try:
        result = await studio.looker.look(
            f"http://127.0.0.1:{project['port']}" + path, studio.store.dir(project_id) / "looks",
            width=max(320, min(int(args.get("width") or 1440), 2560)), dark=bool(args.get("dark")),
            full_page=args.get("full_page", True) is not False, wait_ms=int(args.get("wait_ms") or 1200),
            actions=args.get("actions") if isinstance(args.get("actions"), list) else None)
    except LookUnavailable as exc:
        raise ToolError(str(exc)) from None
    result["next"] = "Open the file in `image` with the Read tool to see the screenshot."
    return result


async def t_check_page(studio, project_id, args):
    page = str(args.get("path") or "index.html")
    result = await asyncio.to_thread(check_page, _workspace(studio, project_id), page)
    turn_id = studio.turns.active.get(project_id)
    if turn_id:      # the studio re-checks these pages itself after a verification (turns.py)
        studio.turns.checked_pages.setdefault(turn_id, []).append(result["page"])
    failed = [item for item in result["items"] if item["status"] == "failed"]
    missing = result["not_listed"]
    nexts = []
    if failed:
        nexts.append("Fix every failed item and run the check again.")
    if missing:
        nexts.append("These cited sources have no entry in the page's source list (an element with "
                     f"id=\"src-<id>\"): {', '.join(missing[:40])}. Add them, then run the check again.")
    return {"page": result["page"], "summary": result["summary"], "failed": failed[:40],
            "not_listed": missing[:40], "unmarked": result["unmarked"][:25], "saved": result["saved"],
            "next": " ".join(nexts) or "No failures. The full list for the checked-numbers appendix is in the saved file."}


async def t_render_document(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    relative = str(args.get("path") or "")
    try:
        path = ws.safe_path(workspace, relative)
    except Exception:
        raise ToolError("path must be a file inside the project.") from None
    if not path.is_file():
        raise ToolError(f"There is no file at {relative} in the project.")
    try:
        return await office.render_async(workspace, path.relative_to(workspace.resolve()).as_posix(),
                                         stamp=args.get("store_results", True) is not False)
    except office.OfficeError as exc:
        raise ToolError(str(exc)) from None


def _page_url(project: dict, relative: str, workspace: Path) -> str:
    """A project file → its address on the project's own port, as the studio's browser loads it."""
    try:
        path = ws.safe_path(workspace, relative.split("?")[0].lstrip("/"))
    except Exception:
        raise ToolError("path must be a page inside the project.") from None
    if not path.is_file():
        raise ToolError(f"There is no file at {relative} in the project.")
    query = relative.split("?", 1)[1] if "?" in relative else ""
    address = path.relative_to(workspace.resolve()).as_posix()
    return f"http://127.0.0.1:{project['port']}/{address}" + (f"?{query}" if query else "")


async def t_render_video(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    relative = str(args.get("path") or "")
    project = await studio.open_preview(studio.store.get(project_id))
    try:
        return await media.render_video(
            studio.looker, _page_url(project, relative, workspace), workspace, relative.split("?")[0],
            studio.store.dir(project_id) / "looks", out=args.get("out") or None, fmt=args.get("format") or "mp4",
            width=args.get("width"), height=args.get("height"), fps=args.get("fps"), duration=args.get("duration"),
            start=args.get("start") or 0, scale=args.get("scale") or 1, audio=args.get("audio") or None,
            volume=args.get("volume") if args.get("volume") is not None else 1.0, check=bool(args.get("check")),
            times=args.get("times") if isinstance(args.get("times"), list) else None,
            frames=args.get("frames") or 12, poster_at=args.get("poster_at"))
    except (media.MediaError, LookUnavailable) as exc:
        raise ToolError(str(exc)) from None


async def t_render_image(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    relative = str(args.get("path") or "index.html")
    project = await studio.open_preview(studio.store.get(project_id))
    try:
        return await media.render_image(
            studio.looker, _page_url(project, relative, workspace), workspace, str(args.get("out") or ""),
            width=args.get("width") or 1200, height=args.get("height") or None, scale=args.get("scale") or 1,
            selector=args.get("selector") or None, transparent=bool(args.get("transparent")),
            dark=bool(args.get("dark")), wait_ms=args.get("wait_ms") if args.get("wait_ms") is not None else 600,
            at=args.get("at"))
    except (media.MediaError, LookUnavailable) as exc:
        raise ToolError(str(exc)) from None


async def t_generation_models(studio, project_id, args):
    generator = studio.generator
    if args.get("model"):
        return await generator.model(str(args["model"]))
    kind = str(args.get("kind") or "") or None
    listed = await generator.models(kind)
    return {"defaults": {k: v["model"] for k, v in generate.KINDS.items() if v["model"]},
            "models": listed[:150], "credits_left": await generator.balance(),
            "spent_through_the_studio_today": generator.spent_today(), "daily_limit": generator.daily_credits,
            "paused": generator.halted(),
            "next": "Call again with `model` to see what one takes. Nothing here costs anything."}


def _project_files(workspace: Path, values, what: str) -> list[Path]:
    paths = []
    for value in ([values] if isinstance(values, str) else list(values or [])):
        try:
            path = ws.safe_path(workspace, str(value))
        except Exception:
            raise ToolError(f"{what} must be files inside the project.") from None
        if not path.is_file():
            raise ToolError(f"There is no file at {value} in the project.")
        paths.append(path)
    return paths


async def t_generate_media(studio, project_id, args):
    workspace, generator = _workspace(studio, project_id), studio.generator
    kind = str(args.get("kind") or "image")
    if kind not in generate.KINDS:
        raise ToolError("kind must be image, video or audio.")
    spec = generate.KINDS[kind]
    try:
        target = ws.safe_path(workspace, str(args.get("out") or ""))
    except Exception:
        raise ToolError("out must be a path inside the project, for example media/hero.png.") from None
    if target.suffix.lower() not in spec["types"]:
        raise ToolError(f"out must end in one of {', '.join(spec['types'])} for {kind}.")
    params = dict(args.get("params") or {}) if isinstance(args.get("params"), dict) else {}
    prompt = str(args.get("prompt") or params.get("prompt") or "").strip()
    if prompt:
        params["prompt"] = prompt
    for name in ("aspect_ratio", "resolution", "quality", "duration"):
        if args.get(name) not in (None, ""):
            params[name] = args[name]
    media_files = {name: _project_files(workspace, args.get(name), name)
                   for name in generate.MEDIA_FLAGS if args.get(name)}
    quoted = await generator.quote(kind, args.get("model") or None, params, media_files)
    settings = ", ".join(f"{k}: {v}" for k, v in params.items() if k != "prompt") or "the model's defaults"
    used = ", ".join(p.name for paths in media_files.values() for p in paths)
    detail = (f"{prompt or '(no prompt)'}\n\nModel: {quoted['model']}\nSettings: {settings}"
              + (f"\nUses: {used}" if used else "")
              + f"\nSaved as: {target.relative_to(workspace.resolve()).as_posix()}")
    price = f"{quoted['credits']:g} credit" + ("" if quoted["credits"] == 1 else "s")
    allowed = await studio.ask(
        project_id, "generate_media", f"Generate a {spec['noun']} for {price}", detail,
        f"It spends {quoted['credits']:g} of your {quoted['balance']:g} Higgsfield credits.")
    if not allowed:
        raise ToolError("The person did not allow this generation. Nothing was spent. Do not ask again for the "
                        "same thing; carry on without it, or say what you wanted it for.")
    made = await generator.run(quoted, target, project_id, prompt)
    relative = made["file"].relative_to(workspace.resolve()).as_posix()
    result = {"file": relative, "kind": kind, "model": quoted["model"], "credits_spent": made["credits"],
              "credits_left": made["balance"], "width": made["width"], "height": made["height"]}
    if kind == "image":
        result["next"] = "Open the file with the Read tool and check it before using it. Label it as generated where it is shown."
    elif kind == "video":
        result["next"] = ("To use it in a video, place it in a scene of the stage (the motion-video skill says how) "
                          "and use render_video with check: true to see it in place. Label it as generated where it is shown.")
    else:
        result["next"] = "Use it as a soundtrack with data-audio on the stage, or the audio argument of render_video."
    return result


async def t_save_template(studio, project_id, args):
    from .templates import TemplateError
    try:
        saved = await studio.save_template(project_id, str(args.get("name") or ""), str(args.get("description") or ""),
                                           args.get("kind") or None, args.get("file") or None, str(args.get("notes") or ""))
    except TemplateError as exc:
        raise ToolError(exc.message) from None
    return {"saved": saved["label"], "id": saved["id"], "kind": saved["kind"], "files": saved["files"],
            "next": "Tell the person the template is saved and that it is in the template list when starting a new page."}


async def t_propose_ship_recipe(studio, project_id, args):
    from .ship import ShipError
    try:
        done = await studio.ships.propose(project_id, args)
    except ShipError as exc:
        raise ToolError(exc.message) from None
    return {"recipe": done["recipe"], "changed": done["changed"],
            "next": "The person confirmed this recipe." if done["changed"] else
                    "This is already the confirmed recipe; nothing changed."}


async def t_make_live(studio, project_id, args):
    from .ship import ShipError
    turn_id = studio.turns.active.get(project_id)
    try:
        if turn_id is None:
            ship_id = await studio.ships.start(project_id, "agent")
            return {"ship_id": ship_id, "next": "The studio is running the checks and will show the person a card."}
        studio.ships.request_from_agent(project_id, turn_id)
    except ShipError as exc:
        raise ToolError(exc.message) from None
    return {"queued": True,
            "next": "The studio makes it live when your turn ends: it saves this version, runs the recipe's checks in "
                    "the copy and shows the person a card with the files, the checks and every command; nothing is "
                    "written to the original unless they allow it. End your turn now with a short summary of what "
                    "changed, and do not run any deploy command yourself."}


def _library(run):
    from .library import LibraryError
    try:
        return run()
    except LibraryError as exc:
        raise ToolError(exc.message) from None


async def t_search_library(studio, project_id, args):
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("query: say what you are looking for, in a few words.")
    project = studio.store.get(project_id)
    return _library(lambda: studio.picker.search(project, query, str(args.get("kind") or "all"), args.get("limit") or 12))


async def t_activate_skill(studio, project_id, args):
    project = studio.store.get(project_id)
    return await asyncio.to_thread(_library, lambda: studio.picker.activate(project, args.get("id") or args.get("name")))


async def t_request_connector(studio, project_id, args):
    project = studio.store.get(project_id)
    return _library(lambda: studio.picker.request(project, args.get("id") or args.get("name"), str(args.get("reason") or "")))


async def t_report_verification(studio, project_id: str, args: dict) -> dict:
    """The verification's result → one `verification` event of the running request (sizes capped)."""
    try:
        fields = effort.verification_event(args)
    except effort.OptionsError as exc:
        raise ToolError(str(exc)) from None
    turn_id = studio.turns.active.get(project_id)
    studio.log(project_id).append("verification", turn_id, **fields)
    if turn_id:
        studio.turns.verified.add(turn_id)
        studio.turns.reports[turn_id] = {"failed": fields["failed"]}
    return {"reported": True, "findings_kept": len(fields["findings"]), "findings_cut": fields["findings_cut"]}


async def t_suggest_next_steps(studio, project_id: str, args: dict) -> dict:
    """The agent's own suggestions for what could follow → kept for the end of the running request,
    where the studio writes the one `next_steps` event. The last call wins; no event is written here."""
    try:
        kept, cut = nextsteps.agent_steps(args)
    except nextsteps.StepsError as exc:
        raise ToolError(str(exc)) from None
    turn_id = studio.turns.active.get(project_id)
    if turn_id:
        studio.turns.proposed[turn_id] = kept
    return {"kept": len(kept), "cut": cut}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


S, I, B, N = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "number"}
Handler = Callable[[Any, str, dict], Awaitable[dict]]
TOOLS: list[tuple[str, str, dict, Handler]] = [
    ("search_web", "Search the web. Returns `results` (title, url, snippet) and `via`, the service that answered; "
     "read a result with fetch_source. Searches run one at a time and a repeated query is answered from memory. "
     "When it says search is unavailable, do what the message says instead of calling it again.",
     _schema({"query": S, "max_results": I}, ["query"]), t_search_web),
    ("fetch_source", "Download a public web page, PDF or text file, extract its readable text and save it in the "
     "project as a source (sources/<id>.txt, listed in sources/index.json). Returns the source id, which is what "
     "data-src marks on the page refer to. Use render: true for a page that is built by scripts and came back empty. "
     "An address already saved answers with its id and title only (`already_saved`), without the text: read it "
     "with read_source. refresh: true downloads it again.",
     _schema({"url": S, "render": B, "refresh": B, "max_chars": I}, ["url"]), t_fetch_source),
    ("read_source", "Read part of a saved source: the passages around each match of `find` (the way to locate one "
     "figure or the exact words to quote), or a range (`offset` to `end`, or `offset` and `max_chars`). Read a "
     "source through once and write down what you take from it (id, figure, exact quote); go back only with "
     "`find` or a range, never to read the whole text again.",
     _schema({"id": S, "find": S, "offset": I, "end": I, "max_chars": I}, ["id"]), t_read_source),
    ("add_source", "Register a file that is already in the project (PDF, text, Markdown, HTML) as a source so it can be quoted.",
     _schema({"path": S, "title": S}, ["path"]), t_add_source),
    ("query_files", "Run one read-only SQL statement (DuckDB) over files in the project: "
     "select … from 'data/sales.csv', Parquet and JSON too. Paths are relative to the project folder. "
     "save_as writes the rows to a .csv or .json file in the project instead of returning them.",
     _schema({"sql": S, "limit": I, "save_as": S}, ["sql"]), t_query_files),
    ("database_schema", "List the tables of an attached database, or the columns of one table (schema.table).",
     _schema({"source": S, "table": S}, ["source"]), t_database_schema),
    ("query_database", "Run one read-only SQL statement on an attached database. save_as writes up to 500,000 rows "
     "to a .csv or .json file in the project instead of returning them.",
     _schema({"source": S, "sql": S, "limit": I, "save_as": S}, ["source", "sql"]), t_query_database),
    ("look_at_page", "Load the project's page in a real browser and get a screenshot file, console errors and failed "
     "requests. `actions` run first: {fill, value}, {click}, {hover}, {press}, {select, value}, {wait_for}, {wait_ms}.",
     _schema({"path": S, "width": I, "dark": B, "full_page": B, "wait_ms": I,
              "actions": {"type": "array", "items": {"type": "object"}}}, []), t_look_at_page),
    ("check_page", "Check every marked figure and quote on a page against its saved source or project data, and list "
     "numbers that carry no mark. Run it before finishing any page that states facts or figures.",
     _schema({"path": S}, []), t_check_page),
    ("report_verification", "Report the result of verifying the page(s), once, at the end of a request that was asked "
     "to verify: the counts (checked, passed, fixed, failed, unverifiable) and findings [{what, where, status: passed|"
     "fixed|failed|unverifiable, note}]. List every fixed, failed and unverifiable item; passed ones may be summarised. "
     "The person sees it as a card. A pass means found in the saved source or recomputed from the page's own data.",
     _schema({"checked": I, "passed": I, "fixed": I, "failed": I, "unverifiable": I, "summary": S,
              "findings": {"type": "array", "items": {"type": "object", "properties": {
                  "what": S, "where": S, "status": S, "note": S}}}},
             ["checked", "passed", "fixed", "failed", "unverifiable", "findings"]), t_report_verification),
    ("suggest_next_steps", "Offer the person at most three things they might ask for next, once, as your last tool "
     "call in a request whose work is done: steps [{label, text}]. Each is a request you could carry out yourself in "
     "this project, specific to this page. label is what the chip says (40 characters at most, starting with a verb: "
     "\"Add a section on battery life\"); text is the whole request as the person would write it. The person sees "
     "them as chips under your reply; a tap only puts the text in their chat box. Suggest nothing that is already "
     "done, no questions, and no check of the page (the studio offers that itself). Give fewer steps, or do not call "
     "this at all, when nothing sensible follows. Do not repeat the steps in your closing message.",
     _schema({"steps": {"type": "array", "items": {"type": "object", "properties": {"label": S, "text": S}}}},
             ["steps"]), t_suggest_next_steps),
    ("render_document", "Render a PowerPoint, Word, Excel or PDF file of the project so it can be looked at and shown. "
     "Decks, documents and PDFs become a PDF plus one image per page (open the images with the Read tool to check "
     "your work). A workbook has every formula calculated, the results stored in the file, and formula errors "
     "listed. All get a preview page (<file>.preview/index.html) that a report can frame, with Download.",
     _schema({"path": S, "store_results": B}, ["path"]), t_render_document),
    ("render_video", "Render an animated page of the project to a video file (mp4, gif or webm), frame by frame on a "
     "stepped clock, so the result is smooth whatever the page costs to draw. The page says how long it is "
     "(window.studioVideo, which the motion-video skill's stage.js sets) or pass `duration`. `check: true` encodes "
     "nothing and returns only a contact sheet of frames (`times: [seconds]` picks them; four or fewer are large): "
     "open the sheet with the Read tool to see the motion. `audio` is a sound file of the project.",
     _schema({"path": S, "out": S, "format": S, "width": I, "height": I, "fps": N, "duration": N, "start": N,
              "scale": I, "audio": S, "volume": N, "check": B, "times": {"type": "array", "items": N},
              "frames": I, "poster_at": N}, ["path"]), t_render_video),
    ("render_image", "Save a picture of a project page as a .png, .jpg or .webp file in the project: the whole page "
     "at `width`, exactly width × height, or one element (`selector`). `scale` 2 or 3 for more pixels, "
     "`transparent` to leave out the background, `at` (seconds) for a still from an animated page.",
     _schema({"path": S, "out": S, "width": I, "height": I, "scale": I, "selector": S, "transparent": B,
              "dark": B, "at": N, "wait_ms": I}, ["path", "out"]), t_render_image),
    ("generation_models", "List what the generation service can make (kind: image, video or audio), or with `model` "
     "the settings one model takes. Also says how many credits are left. Costs nothing.",
     _schema({"kind": S, "model": S}, []), t_generation_models),
    ("generate_media", "Ask the generation service for a photograph or illustration (kind: image), a short clip "
     "(video) or music or speech (audio), saved as a file in the project. It spends the person's credits: the "
     "studio gets the price, shows it to the person and waits for their answer, every time. `prompt` describes it; "
     "`aspect_ratio`, `resolution`, `quality`, `duration` and anything else the model takes (`params`) are optional; "
     "`references` are project pictures to follow, `start_image` the first frame of a clip.",
     _schema({"kind": S, "prompt": S, "out": S, "model": S, "aspect_ratio": S, "resolution": S, "quality": S,
              "duration": I, "params": {"type": "object"}, "references": {"type": "array", "items": S},
              "start_image": S, "end_image": S}, ["kind", "out"]), t_generate_media),
    ("save_template", "Save this project as a template the person can start other work from, or save one Office file "
     "of it (`file`: a .pptx, .xlsx or .docx path) with the script that builds it. `kind` is page, app, deck, "
     "workbook or document and is usually left out. Put how it was made in `notes`: palette, type, grid, section "
     "order, chart choices, what each part of the build script does.",
     _schema({"name": S, "description": S, "kind": S, "file": S, "notes": S}, ["name"]), t_save_template),
    ("propose_ship_recipe", "Propose how this attached app is made live (its ship recipe); the person confirms it on a "
     "card. Give the whole recipe: `checks` (commands run in this project folder before every ship, for example the "
     "tests or a build), `method` (branch: leave a branch in the original's repository; merge: fast-forward the "
     "original's checked-out branch; files: copy changed files into a plain folder; none: edited in place), `after` "
     "(commands run in the ORIGINAL folder afterwards, such as a build and a restart), `address` (http(s) address "
     "checked afterwards), `protected` (path patterns that need the owner's extra confirmation, such as .env* or "
     "migrations/), `back` (commands to run after the studio has put the original back; empty means the after-"
     "commands again), `timeout` (seconds per command) and `notes` (what you read to decide this).",
     _schema({"checks": {"type": "array", "items": S}, "method": S, "after": {"type": "array", "items": S},
              "address": S, "protected": {"type": "array", "items": S}, "back": {"type": "array", "items": S},
              "timeout": I, "notes": S}, ["method"]), t_propose_ship_recipe),
    ("make_live", "Make this attached app live with its confirmed recipe, when the person asks to ship, deploy or "
     "make it live. The studio does the shipping after your turn ends and asks the person on a card first; you never "
     "run deploy commands yourself.",
     _schema({}, []), t_make_live),
    ("search_library", "Search the person's whole library of skills and connectors (far more than are loaded for one "
     "request) by words: what the task is, a file format, a service, a method. Use it when the task needs know-how or "
     "a service you were not given. `kind` is skill, connector or all. Each result has an `id` for activate_skill or "
     "request_connector.",
     _schema({"query": S, "kind": S, "limit": I}, ["query"]), t_search_library),
    ("activate_skill", "Switch on a skill from the library right now, by its `id` from search_library. The answer "
     "carries the skill's full instructions and the folder its files were put in; follow them for the task in hand.",
     _schema({"id": S}, ["id"]), t_activate_skill),
    ("request_connector", "Ask for a connector (an MCP server) from the library, by its `id` from search_library. It "
     "cannot join the request that is running: the studio connects it when the person sends their next message, so "
     "say so plainly. `reason`: what you need it for.",
     _schema({"id": S, "reason": S}, ["id"]), t_request_connector),
]
HANDLERS = {name: handler for name, _d, _s, handler in TOOLS}


async def call(studio, project_id: str, name: str, args: dict) -> dict:
    """Run one tool → the MCP result ({content, is_error?}). Never raises."""
    try:
        result = await HANDLERS[name](studio, project_id, args or {})
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > RESULT_CAP:
            text = text[:RESULT_CAP] + "\n… (cut: ask for less, or save the result to a file and read that)"
        return {"content": [{"type": "text", "text": text}]}
    except (ToolError, SourceError, SearchError, media.MediaError, generate.GenerateError) as exc:
        message = str(exc)
    except DataError as exc:
        message = exc.message
    except Exception as exc:  # a tool must never take the turn down
        message = f"{exc.__class__.__name__}: {exc}"
    return {"content": [{"type": "text", "text": message[:2000]}], "is_error": True}


def describe(studio) -> list[dict]:
    look = studio.looker.reason()
    notes = {"look_at_page": look, "fetch_source": f"render: true is not available. {look}" if look else None,
             "render_document": office.reason(), "render_video": media.video_reason(studio.looker),
             "render_image": look, "generate_media": generate.reason(), "generation_models": generate.reason(),
             "propose_ship_recipe": "Only in a project brought in from a folder.",
             "make_live": "Only in a project brought in from a folder."}
    off = {"look_at_page": bool(look), "render_document": bool(office.reason()),
           "render_video": bool(media.video_reason(studio.looker)), "render_image": bool(look),
           "generate_media": bool(generate.reason()), "generation_models": bool(generate.reason())}
    return [{"name": name, "description": description, "available": not off.get(name, False),
             "note": notes.get(name)} for name, description, _s, _h in TOOLS]


def build_server(studio, project_id: str):
    """→ the SDK's in-process MCP server config for one turn of one project."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def wrap(name: str, description: str, schema: dict):
        @tool(name, description, schema)
        async def handler(args):
            return await call(studio, project_id, name, args)
        return handler

    return create_sdk_mcp_server(SERVER_NAME, "1.0.0", [wrap(n, d, s) for n, d, s, _h in TOOLS])
