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
from .config import now_iso
from .check import check_page
from .datasources import DataError, run_query, run_schema, split_statement, to_json
from . import convert, diagram, effort, filecheck, filesql, generate, media, nextsteps, office, services, sitecheck
from . import context as contexts
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
    return await web_search.search(args.get("query"), args.get("max_results") or 8, scope=f"{project_id}:{turn}",
                                   tavily_key=services.key(studio.settings, "tavily"))


async def t_fetch_source(studio, project_id, args):
    url = str(args.get("url") or "").strip()
    max_chars = max(500, min(int(args.get("max_chars") or 6000), 40_000))
    sources = Sources(_workspace(studio, project_id))
    render, fetched = bool(args.get("render")), None
    # Not refreshing: an address saved while this download ran (a helper fetching the same page in parallel)
    # is the answer too, checked again inside the save where no other save can come between.
    saved = None if args.get("refresh") else await asyncio.to_thread(sources.by_url, url)
    # Listed, but its text is not in the project (a copy of an example): fetch it again under its own id.
    restore = saved["id"] if saved is not None and not sources.has_text(saved) else None
    if restore:
        saved = None
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
    item, by_itself = None, False
    if not render:
        data, content_type, final = fetched
        try:
            kind, title, text, pages = await asyncio.to_thread(extract, data, content_type, final)
            item, created = await asyncio.to_thread(
                sources.save, text=text, title=title, kind=kind, url=final, pages=pages, requested=url,
                original=data if kind == "pdf" else None, original_ext=".pdf" if kind == "pdf" else "", into=restore,
                reuse_address=not args.get("refresh"))
        except SourceError as exc:
            # Nothing readable in what was downloaded: the page is built by scripts. Read it in a browser
            # straight away instead of sending the agent back to ask for that.
            if not exc.empty or studio.looker.reason() is not None:
                raise
            render = by_itself = True
    if render and item is None:
        try:
            final, markup = await studio.looker.rendered(url)
        except LookUnavailable as exc:
            raise ToolError(f"render is not available: {exc}") from None
        except Exception as exc:
            raise ToolError(f"The browser could not load {url} ({' '.join(str(exc).split())[:200]}).") from None
        title, text = html_to_text(markup)
        item, created = await asyncio.to_thread(sources.save, text=text, title=title, kind="html", url=final,
                                                requested=url, into=restore, reuse_address=not args.get("refresh"))
    view = _source_view(item, created, sources.text(item["id"]), max_chars)
    if by_itself:
        view["rendered"] = True     # read in a browser because the plain download held nothing readable
    if restore and item["id"] == restore:
        view["restored"] = True      # the text was missing and is back under the id the page already cites
    return view


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
    target = _save_target(workspace, save_as)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows([["" if v is None else to_json(v) for v in row] for row in rows])
    else:
        target.write_text(json.dumps([dict(zip(columns, map(to_json, row))) for row in rows]), "utf-8")
    return target.relative_to(workspace.resolve()).as_posix()


def _save_target(workspace: Path, save_as: str) -> Path:
    try:
        target = ws.safe_path(workspace, save_as)
    except Exception:
        raise ToolError("save_as must be a path inside the project.") from None
    if target.suffix.lower() not in (".csv", ".json", ".parquet"):
        raise ToolError("save_as must end in .csv, .json or .parquet.")
    return target


def _profile_target(workspace: Path, relative: str) -> Path:
    try:
        target = ws.safe_path(workspace, relative)
    except Exception:
        raise ToolError("profile must be a file inside the project.") from None
    if not target.is_file():
        raise ToolError(f"There is no file {relative} in the project.")
    if target.suffix.lower() not in filesql.PROFILED:
        raise ToolError("profile reads .csv, .tsv, .parquet, .json, .jsonl and .ndjson files. Convert a workbook "
                        "to CSV or Parquet with a short script first.")
    return target


def _query_parts(workspace: Path, args: dict) -> tuple[list[dict], dict]:
    """→ (the worker's parts, what the answer needs to know) for one query_files call."""
    if args.get("profile"):
        if args.get("sql"):
            raise ToolError("Send either sql or profile, not both.")
        target = _profile_target(workspace, str(args["profile"]))
        relative = target.relative_to(workspace.resolve()).as_posix()
        return [{"profile": relative}], {"profile": relative, "bytes": target.stat().st_size}
    statement = _statement(str(args.get("sql") or ""))
    save_as = args.get("save_as")
    if save_as and _save_target(workspace, save_as).suffix.lower() == ".parquet":
        cap = int(args["limit"]) if args.get("limit") else None
        return [{"sql": statement, "limit": cap, "save_parquet": str(_save_target(workspace, save_as))}], {"parquet": True}
    limit = max(1, min(int(args.get("limit") or (500_000 if save_as else 200)), 500_000 if save_as else 5_000))
    return [{"sql": statement, "limit": limit}], {"limit": limit}


def _statement(sql: str) -> str:
    try:
        return split_statement(sql)[0]
    except DataError as exc:
        raise ToolError(exc.message) from None


PROFILE_COLUMNS, SAMPLE_COLUMNS = 250, 30


def _profiled(answer: dict, known: dict, timeout: int) -> dict:
    size, kind = known["bytes"], Path(known["profile"]).suffix.lower().lstrip(".")
    out = {"path": known["profile"], "bytes": size, "size": filesql.human(size), "format": kind, **answer}
    # A wide file must not push the answer past RESULT_CAP: every column is described, the sample shows
    # the first columns only, with short values.
    if len(out.get("columns") or []) > PROFILE_COLUMNS:
        out["columns_cut"] = len(out["columns"]) - PROFILE_COLUMNS
        out["columns"] = out["columns"][:PROFILE_COLUMNS]
    cut = lambda v, n: v[:n] + "…" if isinstance(v, str) and len(v) > n else v      # noqa: E731
    out["columns"] = [{**column, "min": cut(column.get("min"), 60), "max": cut(column.get("max"), 60)}
                      for column in out.get("columns") or []]
    sample = out.get("sample") or {}
    if len(sample.get("columns") or []) > SAMPLE_COLUMNS:
        out["sample"] = {"columns": sample["columns"][:SAMPLE_COLUMNS],
                         "rows": [row[:SAMPLE_COLUMNS] for row in sample.get("rows") or []],
                         "columns_cut": len(sample["columns"]) - SAMPLE_COLUMNS}
    out["sample"]["rows"] = [[cut(v, 80) for v in row] for row in out["sample"].get("rows") or []]
    steps = []
    if out.get("sampled"):
        steps.append(f"Reading every row did not fit in {int(timeout * filesql.FULL_SHARE)} s, so these figures come from "
                     f"{out['sampled']['how']}: say so wherever you use them.")
    if size > filesql.BIG_FILE and kind not in ("parquet",):
        name = str(Path(known["profile"]).with_suffix(".parquet"))
        steps.append(f"This file is over 100 MB: convert it to Parquet once (query_files with sql \"select * from "
                     f"'{known['profile']}'\" and save_as \"{name}\") and query that; it is much faster.")
    steps.append("For a page, save small aggregated tables (save_as) and load those, never the raw file.")
    out["next"] = " ".join(steps)
    return out


async def t_query_files(studio, project_id, args):
    workspace = _workspace(studio, project_id)
    try:
        timeout = filesql.timeout_of(args.get("timeout"))
    except filesql.QueryError as exc:
        raise ToolError(exc.message) from None
    parts, known = _query_parts(workspace, args)
    try:
        answer = (await asyncio.to_thread(filesql.run, workspace, parts, filesql.scratch_root(), timeout))[0]
    except filesql.QueryError as exc:
        raise ToolError(exc.message) from None
    if "error" in answer:
        raise ToolError(answer["error"])
    if "profile" in known:
        return _profiled(answer, known, timeout)
    if known.get("parquet"):
        saved = Path(parts[0]["save_parquet"])
        return {"columns": answer["columns"], "row_count": answer["row_count"], "truncated": False,
                "saved": saved.relative_to(workspace.resolve()).as_posix(), "bytes": saved.stat().st_size,
                "seconds": answer["seconds"]}
    columns, rows = answer["columns"], answer["rows"]
    truncated = bool(answer.get("truncated"))
    if args.get("save_as"):
        saved = await asyncio.to_thread(_save_rows, workspace, args["save_as"], columns, rows)
        return {"columns": columns, "row_count": len(rows), "truncated": truncated, "saved": saved}
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


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
    dialogs = str(args.get("dialogs") or "dismiss").strip().lower()
    if dialogs not in ("dismiss", "accept"):
        raise ToolError('dialogs is "dismiss" (the default: Cancel) or "accept" (OK, and a prompt gets its default text).')
    outside = str(args.get("url") or "").strip()
    if outside:
        # A page of an app the person added as context: never with anyone's sign-in (signed_in=False),
        # since a context address may be another project's page.
        items = studio.store.get(project_id).get("context") or []
        target = contexts.page_under(outside, items)
        if target is None:
            known = [item["address"] for item in items if item.get("kind") == "address"]
            raise ToolError("url must be a page under one of this project's context addresses"
                            + (f" ({', '.join(known)})." if known else
                               ": it has none. Use path for the project's own page."))
    else:
        path = str(args.get("path") or "/")
        if not path.startswith("/") and "://" not in path:
            path = "/" + path          # "index.html" is what the other page tools take
        if "://" in path or path.startswith("//"):
            raise ToolError("path must start with / and stay on the project's own URL. For a page of an app the "
                            "person added as context, give its full address as url.")
        project = await studio.open_preview(studio.store.get(project_id))
        target = f"http://127.0.0.1:{project['port']}" + path
    try:
        result = await studio.looker.look(
            target, studio.store.dir(project_id) / "looks",
            width=max(320, min(int(args.get("width") or 1440), 2560)), dark=bool(args.get("dark")),
            full_page=args.get("full_page", True) is not False, wait_ms=int(args.get("wait_ms") or 1200),
            actions=args.get("actions") if isinstance(args.get("actions"), list) else None,
            top=max(0, int(args.get("top") or 0)), signed_in=not outside, dialogs=dialogs)
    except LookUnavailable as exc:
        raise ToolError(str(exc)) from None
    result["next"] = look_next(result)
    return result


def look_next(result: dict) -> str:
    """What the agent is told to do with a look: which pictures to open, and what `layout` is."""
    pieces = len(result.get("tiles") or [])
    if pieces:
        said = (f"`image` is the whole page in one picture: enough for the overall layout, too small to judge "
                f"text or spacing. To see the page as the person does, open the {pieces} "
                f"picture{'s' if pieces != 1 else ''} in `tiles` with the Read tool (the same page in readable "
                "pieces, top to bottom).")
    else:
        said = "Open the file in `image` with the Read tool to see the screenshot."
    if result.get("cut_off"):
        below = int(result.get("captured_from") or 0) + int(result.get("captured_height") or 0)
        said += (f" This look covers the page from {result.get('captured_from') or 0} to {below} px of "
                 f"{result.get('page_height')}: call again with top: {below} to see what is below.")
    found = (result.get("layout") or {}).get("findings") or []
    if found:
        said += (f" `layout` lists what the studio measured in the browser ({sum(f['count'] for f in found)} "
                 "possible problems): confirm each in the pictures before acting on it.")
    if result.get("signed_in"):
        said += (" The page was loaded signed in as the person (`signed_in`): `actions` act in the app as them, "
                 "so do not change or delete their real data unless they asked for it.")
    if result.get("sign_in_note"):
        said += " " + result["sign_in_note"]
    return said


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


async def t_check_file(studio, project_id, args):
    relative = str(args.get("path") or "")
    result = await asyncio.to_thread(filecheck.check_file, _workspace(studio, project_id), relative)
    turn_id = studio.turns.active.get(project_id)
    if turn_id:      # the studio re-checks these files itself after a verification (turns.py)
        studio.turns.checked_files.setdefault(turn_id, []).append(result["file"])
    return filecheck.compact(result)


async def t_check_site(studio, project_id, args):
    try:
        max_pages = int(args.get("max_pages") or 200)
    except (TypeError, ValueError):
        raise ToolError("max_pages is a number of pages (200 by default).") from None
    # An app brought in from a folder is the person's own (or a copy that ships back): the result goes in the
    # project's record folder, beside look_at_page's pictures, never into the app.
    attached = bool(studio.store.get(project_id).get("attach"))
    save_to = studio.store.dir(project_id) / "site-check.json" if attached else None
    return await asyncio.to_thread(sitecheck.check_site, _workspace(studio, project_id),
                                   str(args.get("start") or "").strip() or None, max_pages, save_to)


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
        pages = args.get("pages")
        return await office.render_async(workspace, path.relative_to(workspace.resolve()).as_posix(),
                                         stamp=args.get("store_results", True) is not False,
                                         pages=None if pages is None else str(pages))
    except office.OfficeError as exc:
        raise ToolError(str(exc)) from None


async def t_render_diagram(studio, project_id, args):
    must = args.get("must_show")
    if must is not None and not isinstance(must, list):
        raise ToolError("must_show is a list of labels the diagram has to contain.")
    return await diagram.render(
        studio.looker, _workspace(studio, project_id), name=str(args.get("name") or ""),
        source=args.get("source") or None, path=args.get("path") or None, must_show=must,
        png_width=args.get("png_width"), colors=args.get("colors") or None, alt=args.get("alt") or None)


async def t_convert_document(studio, project_id, args):
    return await convert.convert(
        studio, _workspace(studio, project_id), str(args.get("path") or ""), str(args.get("to") or ""),
        sheet=args.get("sheet") if args.get("sheet") not in (None, "") else None,
        reference=args.get("reference") or None, replace=bool(args.get("replace")))


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


async def t_save_skill(studio, project_id, args):
    from . import ownskills
    from .library import LibraryError
    try:
        by_owner = studio.turns.active.get(project_id) in studio.turns.by_owner
        plan = await asyncio.to_thread(ownskills.plan, studio.lib, _workspace(studio, project_id), args, by_owner)
        if project_id not in studio.approvers:
            raise ToolError("No request is running, so there is nobody to ask. Nothing was saved.")
        allowed = await studio.ask(project_id, "save_skill", plan["title"], plan["detail"], plan["reason"],
                                   detail_limit=plan["detail_limit"], skill=plan["skill"])
        if not allowed:
            raise ToolError("The person did not allow this skill. Nothing was saved. Do not ask again with the same "
                            "text in this request; ask what they want changed.")
        saved = await asyncio.to_thread(ownskills.save, studio.lib, plan["name"], plan["description"], plan["body"],
                                        plan["files"], plan["replace"], plan["remove"])
    except LibraryError as exc:
        raise ToolError(exc.message) from None
    return {"saved": True, "id": f"yours:{plan['name']}", "replaced": saved["replaced"], "files": saved["files"],
            "scripts": saved["scripts"], "removed": plan["remove"],
            "next": "Tell the person what the skill does and how to start it. It is in their Library, on in every "
                    "project from their next request; there they can open it and read every file, switch it off "
                    "or export it."}


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


_TEST_RUNS: dict[str, asyncio.Lock] = {}


async def t_run_tests(studio, project_id, args):
    """CONTRACT.md, "Tests for a project's app: `run_tests`"."""
    from . import risk, testrun
    project = studio.store.get(project_id)
    workspace = _workspace(studio, project_id)
    try:
        timeout = int(args.get("timeout") or testrun.DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        raise ToolError("timeout must be a whole number of seconds (at most 900).") from None
    timeout = max(1, min(timeout, testrun.MAX_TIMEOUT))
    found = await asyncio.to_thread(testrun.find_command, workspace, project)
    if not found.get("command"):
        return {"command": None, "passed": False, "counts": None, "failures": [], "found": found.get("found", False),
                "next": found["next"]}
    try:
        command = testrun.narrow(found, workspace, args.get("path") or None, args.get("filter") or None)
    except testrun.TestsError as exc:
        raise ToolError(str(exc)) from None
    # The same rules as the agent's own shell: run_tests runs only what that would run without a card.
    reason = await asyncio.to_thread(
        risk.risky_command, command, workspace, in_place=ws.history_dir(workspace) is not None,
        studio_places=(studio.settings.data_dir, studio.settings.config_path.parent), reach=studio.reach(project_id))
    if reason:
        raise ToolError(f"The studio did not run this test command: {reason} run_tests runs only what the studio "
                        "would run in your shell without asking. Run it with Bash if it is needed: there the person "
                        "is asked (on a shared studio a guest's request is refused). Otherwise change the command.")
    lock = _TEST_RUNS.setdefault(project_id, asyncio.Lock())
    if lock.locked():
        raise ToolError("The tests of this project are already running. Wait for that run to end.")
    async with lock:
        try:
            result = await testrun.run(workspace, project_id, command, timeout)
        except testrun.TestsError as exc:
            raise ToolError(str(exc)) from None
    kept = await asyncio.to_thread(testrun.record, result, workspace)
    studio.store.update(project_id, tests_last=kept)
    result["next"] = testrun.next_step(result, timeout)
    if (project.get("attach") or {}).get("mode") == "in_place":
        result["note"] = "These tests ran in the person's own folder (the project is edited in place)."
    return result


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


async def t_ask_person(studio, project_id: str, args: dict) -> dict:
    """Put a card to the person and wait for the answer (CONTRACT.md, "How much to involve the person")."""
    from . import questions
    try:
        return await studio.turns.ask_person(project_id, args)
    except questions.QuestionsError as exc:
        raise ToolError(str(exc)) from None


PLAN_ITEMS, PLAN_TEXT = 30, 200
PLAN_STATUS = {"pending": "pending", "doing": "in_progress", "done": "completed"}


async def t_set_plan(studio, project_id: str, args: dict) -> dict:
    """The project's plan, replaced whole → the project record and one `plan` event of the running
    request (CONTRACT.md, "Big projects"). Never a file: no reload, no version, no person's change."""
    raw = args.get("items")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ToolError("items must be a list of {text, status}.")
    if len(raw) > PLAN_ITEMS:
        raise ToolError(f"A plan has at most {PLAN_ITEMS} items; this one has {len(raw)}. Group the small steps.")
    items = []
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ToolError(f"Item {number} must be {{text, status}}.")
        text = " ".join(str(item.get("text") or "").split())
        status = str(item.get("status") or "pending")
        if not text:
            raise ToolError(f"Item {number} has no text.")
        if len(text) > PLAN_TEXT:
            raise ToolError(f"Item {number} is {len(text)} characters; at most {PLAN_TEXT}. Say it shorter.")
        if status not in PLAN_STATUS:
            raise ToolError(f"Item {number}: status must be pending, doing or done.")
        items.append({"text": text, "status": status})
    turn_id = studio.turns.active.get(project_id)
    studio.store.update(project_id, plan={"items": items, "turn_id": turn_id, "updated_at": now_iso()} if items else None)
    studio.log(project_id).append("plan", turn_id, source="set_plan", entries=[
        {"content": item["text"], "status": PLAN_STATUS[item["status"]]} for item in items])
    done = sum(item["status"] == "done" for item in items)
    return {"saved": len(items), "done": done, "left": len(items) - done}


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
    ("add_source", "Register a file that is already in the project (PDF, text, Markdown, HTML), such as one the "
     "person added, as a source, so figures from it can be marked and checked like a fetched page",
     _schema({"path": S, "title": S}, ["path"]), t_add_source),
    ("query_files", "Run one read-only SQL statement (DuckDB) over files in the project: "
     "select … from 'data/sales.csv', Parquet and JSON too. Paths are relative to the project folder; "
     "nothing outside it can be read. `profile` (a file path, instead of sql) describes a file: size, rows, and "
     "per column type, missing values, distinct values, min and max, with five sample rows; do this first "
     "with a big file. save_as writes the rows to a .csv, .json or .parquet file in the project instead of "
     "returning them. 60 s and 2 GB of memory per call; `timeout` up to 300.",
     _schema({"sql": S, "profile": S, "limit": I, "save_as": S, "timeout": I}, []), t_query_files),
    ("database_schema", "See what an attached database holds before you query it: its tables, or the columns of one "
     "table (schema.table)",
     _schema({"source": S, "table": S}, ["source"]), t_database_schema),
    ("query_database", "Run one read-only SQL statement on an attached database. save_as writes up to 500,000 rows "
     "to a .csv or .json file in the project instead of returning them.",
     _schema({"source": S, "sql": S, "limit": I, "save_as": S}, ["source", "sql"]), t_query_database),
    ("look_at_page", "Load the project's page in a real browser, to see what the person sees before you finish or "
     "when something looks wrong. Returns screenshot files (`image`, and `tiles`: the page in readable pieces; "
     "judge from the tiles), `layout` (what the studio measured: cut-off text, sideways scrolling, overlaps, small "
     "or faint text, missing padding), console errors and failed requests. `width`: 1440 desktop (default), 820 "
     "tablet, 390 phone; `dark`; `top` to start further down (one look covers 6,000 px). `actions` run first: "
     "{fill, value}, {click}, {hover}, {press}, {select, value}, {wait_for}, {wait_ms}. Alerts, confirms and prompts "
     "are listed in `dialogs` and dismissed; `dialogs: \"accept\"` answers OK for this look. `url` instead of `path`: "
     "a page under one of the project's context addresses, loaded without sign-in",
     _schema({"path": S, "url": S, "width": I, "dark": B, "full_page": B, "wait_ms": I, "top": I,
              "actions": {"type": "array", "items": {"type": "object"}},
              "dialogs": {"type": "string", "enum": ["dismiss", "accept"]}}, []), t_look_at_page),
    ("check_page", "Check every marked figure and quote on a page against its saved source or project data, and list "
     "numbers that carry no mark. Run it before finishing any page that states facts or figures.",
     _schema({"path": S}, []), t_check_page),
    ("check_file", "Check every figure in a .pptx, .docx, .xlsx or .pdf file of the project against the figures "
     "check_page verified on the pages, the data files (data/) and the saved sources: each is found (exactly or as "
     "a rounding), derived (a workbook formula) or not_found, with where it is (slide, paragraph, cell, page). "
     "Run it before handing over any file that states figures.",
     _schema({"path": S}, ["path"]), t_check_file),
    ("check_site", "Check all the pages of the project together, from their files (no browser): links and anchors "
     "between pages, missing or empty pictures, scripts and stylesheets, pages no link reaches from `start` "
     "(index.html; without it, the one index page nearest the root when the root has none), pages that lead "
     "nowhere, missing or repeated titles, no viewport, and a menu (<nav>) or stylesheet one page does not share "
     "with the rest. Returns `findings` [{page, kind, target, detail}], `by_kind`, `pages_list` and what it does "
     "not cover. Run it before finishing any project of more than one page.",
     _schema({"start": S, "max_pages": I}, []), t_check_site),
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
     "done, no questions, no check of the page's content and no general polish of its look (the studio offers those "
     "two itself). Give fewer steps, or do not call "
     "this at all, when nothing sensible follows. Do not repeat the steps in your closing message.",
     _schema({"steps": {"type": "array", "items": {"type": "object", "properties": {"label": S, "text": S}}}},
             ["steps"]), t_suggest_next_steps),
    ("render_document", "Render a PowerPoint, Word, Excel or PDF file of the project so it can be looked at and shown. "
     "Decks, documents and PDFs become a PDF plus one image per page (open the images with the Read tool to check "
     "your work). A workbook has every formula calculated, the results stored in the file, and formula errors "
     "listed. All get a preview page (<file>.preview/index.html) that a report can frame, with Download. At most "
     "60 page images are drawn in one call: `pages` (\"61-120\") draws others from the same PDF; past 20 pages "
     "`overview` shows every page small.",
     _schema({"path": S, "store_results": B, "pages": S}, ["path"]), t_render_document),
    ("render_diagram", "Draw a diagram (flowchart, sequence, state, entity-relationship, class, timeline, Gantt) "
     "from Mermaid, when a page, deck or document needs one; read the `diagrams` skill first. Give `source` (or "
     "`path` to a .mmd file) and a `name`; it saves files/diagrams/<name>.svg and <name>-dark.svg (and .png at "
     "`png_width`, for decks and Word). `must_show`: labels it has to contain. `colors` {light, dark}: theme "
     "variables to follow the page's palette. The answer gives a parse error's line, labels `missing` and `layout` "
     "problems. Put the saved SVG in the page, never the Mermaid script",
     _schema({"source": S, "path": S, "name": S, "must_show": {"type": "array", "items": S}, "png_width": I,
              "colors": {"type": "object"}, "alt": S}, ["name"]), t_render_diagram),
    ("convert_document", "Convert a finished file of the project to another format, written beside it under the "
     "same name: Word .docx to .rtf, .odt, .pdf or .md; .odt and .rtf to Word; PowerPoint .pptx to .pdf or .odp; "
     "Excel .xlsx to .ods, .pdf or one sheet as .csv (`sheet`: its name); Markdown .md to .docx, .odt, .rtf, .epub "
     "or a standalone .html (Mermaid blocks become pictures; `reference`: a .docx or .odt of the project whose "
     "styles to follow). An existing file is written over only with `replace: true`. The answer says what did not "
     "come across (`not_carried`).",
     _schema({"path": S, "to": S, "sheet": S, "reference": S, "replace": B}, ["path", "to"]), t_convert_document),
    ("render_video", "Render an animated page of the project to a video file (mp4, gif or webm), frame by frame on a "
     "stepped clock, so the result is smooth whatever the page costs to draw. The page says how long it is "
     "(window.studioVideo, which the motion-video skill's stage.js sets) or pass `duration`. `check: true` encodes "
     "nothing and returns only a contact sheet of frames (`times: [seconds]` picks them; four or fewer are large): "
     "open the sheet with the Read tool to see the motion. `audio` is a sound file of the project.",
     _schema({"path": S, "out": S, "format": S, "width": I, "height": I, "fps": N, "duration": N, "start": N,
              "scale": I, "audio": S, "volume": N, "check": B, "times": {"type": "array", "items": N},
              "frames": I, "poster_at": N}, ["path"]), t_render_video),
    ("render_image", "Save a picture of a project page as a .png, .jpg or .webp file, when the person wants an "
     "image file (a thumbnail, a social card, a still for a deck): the whole page at `width`, exactly width × "
     "height, or one element (`selector`). `scale` 2 or 3 for more pixels, `transparent` to leave out the "
     "background, `at` (seconds) for a still from an animated page",
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
    ("save_skill", "Save a skill to the person's library when they ask to make or change one (read the `make-a-skill` "
     "skill first): instructions another agent follows for one kind of job, in every project from their next "
     "request. `name`: lower-case letters, digits, - and _. `description`: when to use it (600 characters at "
     "most). `body`: the Markdown instructions without front matter (12,000 at most). `files`: project files to "
     "include [{path, as? or to?}]: documents, data and pictures go to reference/, scripts to scripts/; `to` names "
     "the whole path inside the skill (how a file it has is replaced). `remove`: paths to take out. `replace: true` "
     "changes a skill of theirs with that name (the earlier version is kept). They see all of it on a card and "
     "allow or deny it; limits that are passed are said in the answer",
     _schema({"name": S, "description": S, "body": S, "replace": B,
              "files": {"type": "array", "items": {"type": "object", "properties": {"path": S, "as": S, "to": S},
                                                   "required": ["path"]}},
              "remove": {"type": "array", "items": S}},
             ["name", "description", "body"]), t_save_skill),
    ("propose_ship_recipe", "Propose how this attached app is made live, when the person asks to ship it and there is no "
     "recipe yet or it must change; they confirm it on a card, then make_live uses it. Give the whole recipe: "
     "`method` (one the project facts list: branch leaves a branch in the original's repository, merge "
     "fast-forwards its checked-out branch, files copies changed files into a plain folder, none: edited in "
     "place), `checks` (commands run "
     "here before every ship), `after` (commands run in the ORIGINAL folder afterwards), `address` (checked "
     "afterwards), `protected` (path patterns that need the owner, such as .env* or migrations/), `back` "
     "(commands after the studio puts the original back; empty: the after-commands again), `timeout` (seconds "
     "per command) and `notes` (what you read to decide)",
     _schema({"checks": {"type": "array", "items": S}, "method": S, "after": {"type": "array", "items": S},
              "address": S, "protected": {"type": "array", "items": S}, "back": {"type": "array", "items": S},
              "timeout": I, "notes": S}, ["method"]), t_propose_ship_recipe),
    ("make_live", "Make this attached app live with its confirmed recipe, when the person asks to ship, deploy or "
     "make it live. The studio does the shipping after your turn ends and asks the person on a card first; you never "
     "run deploy commands yourself.",
     _schema({}, []), t_make_live),
    ("run_tests", "Run the project's tests and keep the result: the \"test\" command in studio.json, else pytest "
     "(tests/ or test_*.py), else npm test. Run it before finishing whenever the app's code changed. `path` (a test "
     "file or folder) and `filter` (a test name) narrow the run; `timeout` in seconds (300, at most 900). Returns "
     "passed (true only on exit code 0), counts, failures, the end of the output and what to do next. The tests get "
     "STUDIO_TEST_STATE, an empty folder to use instead of the app's state/",
     _schema({"path": S, "filter": S, "timeout": I}, []), t_run_tests),
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
    ("ask_person", "Put a card to the person and wait for their answer. Use it only as the `<studio-effort>` block's "
     "involve setting says; at 0 it answers with an error and you decide yourself. `kind`: questions (what you "
     "need to know), plan (what you mean to build, in `summary`, before you build) or checkpoint (a part you made: "
     "`title`, and what you made and found in `summary`). `summary`: short Markdown. `references`: [{label, url or "
     "path, note}]. `questions`: one to four {question, choices?, multiple?}; choices are two to six real answers "
     "{label, description}, your own pick first with `pick: true` and its reason in the description; never a choice "
     "that only promises an answer (\"I'll paste it\") and no \"Other\" (they can always type their own). What "
     "only they have is a question without choices. A plan or checkpoint without questions asks whether to go "
     "ahead. `why`: one sentence on what the answers change. The answer says what to do next and how many cards "
     "are left",
     _schema({"kind": {"type": "string", "enum": ["questions", "plan", "checkpoint"]}, "title": S, "summary": S,
              "references": {"type": "array", "items": {"type": "object", "properties": {
                  "label": S, "url": S, "path": S, "note": S}, "required": ["label"]}},
              "questions": {"type": "array", "items": {"type": "object", "properties": {
                  "question": S, "multiple": B,
                  "choices": {"type": "array", "items": {"type": "object", "properties": {"label": S, "description": S, "pick": B},
                                                         "required": ["label"]}}},
                  "required": ["question"]}},
              "why": S}, []), t_ask_person),
    ("set_plan", "Show the person the plan for work of more than a few steps, and keep it current: call it first, "
     "then again as each item starts and ends. It replaces the whole plan; [] clears it. `items`: up to 30 "
     "{text (at most 200 characters), status: pending, doing or done}. The plan is the project's, kept across "
     "requests and conversations; while it has items not done it comes back in the project facts.",
     _schema({"items": {"type": "array", "items": {"type": "object", "properties": {
         "text": S, "status": {"type": "string", "enum": ["pending", "doing", "done"]}},
         "required": ["text", "status"]}}}, ["items"]), t_set_plan),
]
HANDLERS = {name: handler for name, _d, _s, handler in TOOLS}


# What a request a guest started on a shared studio may not do through the agent (CONTRACT.md, "What a
# guest may not do on a shared studio"): each adds to what every project gets or makes an app live, and a
# card for it would be the guest's to answer. save_skill is refused in ownskills.plan the same way.
OWNER_TOOLS = {
    "save_template": "Only the studio's owner can save a template: it is offered to every project. Nothing was "
                     "saved. Tell the person to ask the owner.",
    "propose_ship_recipe": "Only the studio's owner can set how this app is made live. Nothing was changed. Tell "
                           "the person to ask the owner.",
    "make_live": "Only the studio's owner can make this app live. Nothing was started. Tell the person to ask "
                 "the owner.",
}


def guest_request(studio, project_id: str) -> bool:
    """Whether the running request of this project is not the owner's, on a shared studio. With no
    request running there is nobody to vouch for, so that counts as a guest's too."""
    if not studio.settings.shared:
        return False
    return studio.turns.active.get(project_id) not in studio.turns.by_owner


async def call(studio, project_id: str, name: str, args: dict) -> dict:
    """Run one tool → the MCP result ({content, is_error?}). Never raises."""
    try:
        if name in OWNER_TOOLS and guest_request(studio, project_id):
            raise ToolError(OWNER_TOOLS[name])
        result = await HANDLERS[name](studio, project_id, args or {})
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > RESULT_CAP:
            text = text[:RESULT_CAP] + "\n… (cut: ask for less, or save the result to a file and read that)"
        return {"content": [{"type": "text", "text": text}]}
    except (ToolError, SourceError, SearchError, media.MediaError, generate.GenerateError, diagram.DiagramError,
            convert.ConvertError, office.OfficeError) as exc:
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
             "render_diagram": diagram.reason(studio.looker), "convert_document": convert.reason(),
             "propose_ship_recipe": "Only in a project brought in from a folder.",
             "make_live": "Only in a project brought in from a folder."}
    off = {"look_at_page": bool(look), "render_document": bool(office.reason()),
           "render_video": bool(media.video_reason(studio.looker)), "render_image": bool(look),
           "generate_media": bool(generate.reason()), "generation_models": bool(generate.reason()),
           "render_diagram": bool(diagram.reason(studio.looker)), "convert_document": bool(convert.reason())}
    return [{"name": name, "description": description, "available": not off.get(name, False),
             "note": notes.get(name)} for name, description, _s, _h in TOOLS]


def _studio_wide(studio) -> dict[str, str | None]:
    """Tool name → why this studio cannot run it (None: it can). Only tools that need something the
    computer may lack; the rest always work."""
    look = studio.looker.reason()
    return {"look_at_page": look, "render_image": look, "render_video": media.video_reason(studio.looker),
            "render_document": office.reason(), "render_diagram": diagram.reason(studio.looker),
            "convert_document": convert.reason(), "generate_media": generate.reason(),
            "generation_models": generate.reason()}


def missing_here(studio) -> dict[str, list[str]]:
    """→ {reason: [tool names]} for the studio tools this studio leaves out (said once in the system
    prompt, so a skill that names one does not turn into a confusing instruction)."""
    out: dict[str, list[str]] = {}
    if studio.settings.fake:     # the fake harness has no studio tools server at all
        return out
    for name, why in _studio_wide(studio).items():
        if why:
            out.setdefault(str(why), []).append(name)
    return out


def registered(studio, project_id: str) -> list[tuple[str, str, dict, Handler]]:
    """The tools one turn of one project is given: each only where it can work (CONTRACT.md, "What the
    agent is told once, and what with each message"). Ship tools need an attached app, the database
    tools an attached database; the rest need what `_studio_wide` checks."""
    project = studio.store.get(project_id)
    wide = _studio_wide(studio)
    def fits(name: str) -> bool:
        if name in ("propose_ship_recipe", "make_live"):
            return bool(project.get("attach"))
        if name in ("database_schema", "query_database"):
            return bool(studio.attached_sources(project_id))
        return not wide.get(name)
    return [entry for entry in TOOLS if fits(entry[0])]


def build_server(studio, project_id: str):
    """→ the SDK's in-process MCP server config for one turn of one project."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def wrap(name: str, description: str, schema: dict):
        @tool(name, description, schema)
        async def handler(args):
            return await call(studio, project_id, name, args)
        return handler

    return create_sdk_mcp_server(SERVER_NAME, "1.0.0", [wrap(n, d, s) for n, d, s, _h in registered(studio, project_id)])
