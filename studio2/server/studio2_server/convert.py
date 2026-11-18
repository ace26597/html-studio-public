"""Convert a finished document to another format (CONTRACT.md, "Diagrams and conversions").

Office formats go through the LibreOffice that renders Office files already (office.py); Markdown
goes through pandoc. Both are programs installed on the machine, run with an argument list (never
a shell line) and a time limit, on files confined to the project. The answer says what was made,
by which program, and what did not come across or was not checked.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from functools import lru_cache
from pathlib import Path

from . import office
from . import workspace as ws

PANDOC_TIMEOUT = 120
LIBRE = "LibreOffice"
PANDOC = "pandoc"
# source extension → {target extension: program}
PAIRS: dict[str, dict[str, str]] = {
    ".docx": {".rtf": LIBRE, ".odt": LIBRE, ".pdf": LIBRE, ".md": PANDOC},
    ".odt": {".docx": LIBRE, ".rtf": LIBRE, ".pdf": LIBRE, ".md": PANDOC},
    ".rtf": {".docx": LIBRE, ".odt": LIBRE, ".pdf": LIBRE},
    ".pptx": {".pdf": LIBRE, ".odp": LIBRE},
    ".odp": {".pptx": LIBRE, ".pdf": LIBRE},
    ".xlsx": {".ods": LIBRE, ".csv": LIBRE, ".pdf": LIBRE},
    ".ods": {".xlsx": LIBRE, ".csv": LIBRE},
    ".md": {".docx": PANDOC, ".odt": PANDOC, ".rtf": PANDOC, ".epub": PANDOC, ".html": PANDOC},
}
# LibreOffice's --convert-to value per target
FILTERS = {".pdf": "pdf", ".rtf": "rtf", ".odt": "odt", ".docx": "docx:MS Word 2007 XML", ".odp": "odp",
           ".pptx": "pptx:Impress MS PowerPoint 2007 XML", ".ods": "ods", ".xlsx": "xlsx:Calc MS Excel 2007 XML"}
CHECKABLE = {".pptx", ".docx", ".xlsx", ".pdf"}        # what check_file reads
RENDERABLE = set(office.KINDS)                         # what render_document reads
ELSEWHERE = ("template/", "sources/")                  # never written into: results go under files/
MERMAID_BLOCK = re.compile(r"^(?P<fence>```|~~~)[ \t]*mermaid[ \t]*\n(?P<body>.*?)^(?P=fence)[ \t]*$", re.M | re.S)
DIAGRAM_PNG_WIDTH = 1200


class ConvertError(Exception):
    pass


def find_pandoc() -> str | None:
    candidates = [os.environ.get("STUDIO2_PANDOC"), shutil.which("pandoc"), "/opt/homebrew/bin/pandoc",
                  "/usr/local/bin/pandoc"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def pandoc_reason() -> str | None:
    if find_pandoc():
        return None
    return ("pandoc is not installed on this machine, so Markdown cannot be turned into Word, OpenDocument, RTF, "
            "EPUB or HTML files, nor a Word file into Markdown. Install it (macOS: `brew install pandoc`; Debian or "
            "Ubuntu: `sudo apt-get install pandoc`; Fedora: `sudo dnf install pandoc`).")


def reason() -> str | None:
    """None when at least one program is there; otherwise what is missing and how to get it."""
    if office.find_soffice() or find_pandoc():
        return None
    return f"{office.reason()} {pandoc_reason()}"


@lru_cache(maxsize=4)
def _version(program: str, where: str) -> str:
    try:
        done = subprocess.run([where, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "version unknown"
    first = next((line.strip() for line in (done.stdout or "").splitlines() if line.strip()), "")
    match = re.search(r"\d+(\.\d+)+", first)
    return f"{program} {match.group(0)}" if match else program


def version(program: str) -> str:
    where = find_pandoc() if program == PANDOC else office.find_soffice()
    return _version(program, str(where)) if where else program


def table() -> list[dict]:
    """The supported pairs, for the contract and the tool's error message."""
    return [{"from": source, "to": sorted(targets)} for source, targets in PAIRS.items()]


def _pairs_text() -> str:
    return "; ".join(f"{source} to {', '.join(sorted(targets))}" for source, targets in PAIRS.items())


# ---- what a file holds that may not come across ------------------------------------------------
def _zip_text(path: Path, names: tuple[str, ...]) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            return "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist()
                             if n.startswith(names) and n.endswith(".xml"))
    except (zipfile.BadZipFile, OSError, KeyError):
        return ""


def _zip_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return []


def fonts_asked(path: Path) -> set[str]:
    """Font families an Office file names (Word runs and styles, theme fonts, workbook styles)."""
    text = _zip_text(path, ("word/", "ppt/", "xl/"))
    names = set(re.findall(r'w:(?:ascii|hAnsi)="([^"]+)"', text))
    names |= set(re.findall(r'<a:latin typeface="([^"]+)"', text))
    names |= set(re.findall(r'<name val="([^"]+)"', text))
    return {n for n in names if n and not n.startswith("+")}


def fonts_in_pdf(pdf: Path) -> set[str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        found = set()
        for page in reader.pages[:60]:
            fonts = (page.get("/Resources") or {}).get("/Font") or {}
            for ref in fonts.values():
                base = str(ref.get_object().get("/BaseFont") or "")
                found.add(base.split("+", 1)[-1].lstrip("/"))
        return found
    except Exception:  # noqa: BLE001 - a font list is extra information
        return set()


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def substituted(asked: set[str], embedded: set[str]) -> list[str]:
    have = [_squash(name) for name in embedded]
    return sorted(name for name in asked if not any(_squash(name) in h for h in have))


def notes_before(source: Path, target: str) -> list[str]:
    """What the source holds that the conversion drops or that nobody checked."""
    kind, notes = source.suffix.lower(), []
    if kind == ".docx":
        body = _zip_text(source, ("word/document.xml",))
        changes = len(re.findall(r"<w:(?:ins|del) ", body))
        if changes:
            notes.append(f"The file has {changes} tracked changes. "
                         + ("pandoc accepts them: the Markdown holds the text as if every change were accepted."
                            if target == ".md" else "How they appear in the result was not checked."))
        if "word/comments.xml" in _zip_names(source):
            notes.append("The file has comments: " + ("they are left out." if target in (".md", ".pdf")
                                                      else "whether they come across was not checked."))
        if re.search(r"instrText[^>]*>\s*TOC\b", body) or "w:sdtDocPart" in body and "Table of Contents" in body:
            notes.append("It has a contents list: it is carried over as it was saved, not rebuilt, so page "
                         "numbers a script never filled in are still not filled in.")
    if kind in (".pptx", ".odp"):
        names = _zip_names(source)
        if target == ".pdf" and any(n.startswith("ppt/notesSlides/") for n in names):
            notes.append("Speaker notes are not in the PDF.")
        slides = _zip_text(source, ("ppt/slides/",))
        if re.search(r"<p:(?:timing|transition)\b", slides):
            notes.append("Animations and slide transitions " + ("are not in a PDF." if target == ".pdf"
                                                               else "were not checked."))
    if kind in (".xlsx", ".ods"):
        if target == ".csv":
            pass        # said with the sheet's name, below
        elif target == ".pdf":
            notes.append("Each sheet is printed with LibreOffice's page breaks; a wide sheet runs over several pages.")
        else:
            notes.append("Charts, conditional formats and data validation were not checked after the conversion.")
    if kind == ".md":
        text = source.read_text("utf-8", errors="replace")
        if re.search(r"<(?:div|span|table|p|br|img|iframe|style|script)\b", text, re.I) and target != ".html":
            notes.append("HTML written inside the Markdown is left out of this format.")
    if kind in (".docx", ".odt") and target == ".md":
        notes.append("Page layout, headers and footers, fonts, colours and comments are not kept in Markdown; "
                     "a table with merged cells is written as an HTML table.")
    if target not in (".pdf", ".md", ".csv", ".html", ".epub") and kind != ".md":
        notes.append("Fonts are named in the file, not included: a computer without them shows others.")
    return notes


def opens(path: Path) -> dict:
    """Read the result back: it must open as what it says it is. → {opens, detail}"""
    kind = path.suffix.lower()
    try:
        if kind in (".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".epub"):
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                names = archive.namelist()
            main = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml",
                    ".odt": "content.xml", ".odp": "content.xml", ".ods": "content.xml",
                    ".epub": "META-INF/container.xml"}[kind]
            ok = bad is None and main in names
            return {"opens": ok, "detail": None if ok else f"{main} is missing or the archive is damaged"}
        if kind == ".pdf":
            from pypdf import PdfReader
            pages = len(PdfReader(str(path)).pages)
            return {"opens": pages > 0, "pages": pages, "detail": None}
        text = path.read_bytes()
        if kind == ".rtf":
            ok = text.lstrip().startswith(b"{\\rtf")
            return {"opens": ok, "detail": None if ok else "it does not start as an RTF file"}
        text.decode("utf-8")
        return {"opens": True, "detail": None}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"opens": False, "detail": f"{exc.__class__.__name__}: {exc}"[:200]}


# ---- the two programs --------------------------------------------------------------------------
def sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True)
        try:
            return list(book.sheetnames)
        finally:
            book.close()
    content = _zip_text(path, ("content.xml",))
    return re.findall(r'<table:table [^>]*table:name="([^"]+)"', content)


def _libre(source: Path, target: str, sheet: str | None, notes: list[str]) -> tuple[Path, Path]:
    """→ (the written file in a scratch folder, the scratch folder). Blocking."""
    scratch = Path(tempfile.mkdtemp(prefix="studio2-convert-"))
    value = FILTERS.get(target)
    if target == ".csv":
        names = sheet_names(source)
        if not names:
            raise ConvertError("The workbook has no sheets.")
        if sheet is None and len(names) > 1:
            raise ConvertError(f"A CSV file holds one sheet: say which with `sheet` ({', '.join(names[:30])}).")
        chosen = sheet if sheet is not None else names[0]
        if chosen not in names:
            raise ConvertError(f"There is no sheet named {chosen!r}; the sheets are {', '.join(names[:30])}.")
        # Comma, double quote, UTF-8, from row 1, …, the sheet's number (1 = first).
        value = f"csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,{names.index(chosen) + 1}"
        notes.append(f"Only the sheet “{chosen}”: its values as LibreOffice calculates them, without formulas, "
                     "formatting, charts or the other sheets.")
    try:
        office.convert(source, value, scratch)
    except office.OfficeError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ConvertError(str(exc)) from None
    made = [p for p in scratch.iterdir() if p.is_file() and p.suffix.lower() == target]
    if not made:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ConvertError(f"LibreOffice wrote no {target} file for {source.name}.")
    return made[0], scratch


def _has_title(text: str) -> bool:
    front = re.match(r"---\n(.*?)\n(?:---|\.\.\.)\n", text, re.S)
    return bool(front and re.search(r"^title\s*:", front.group(1), re.M))


def _pandoc(source: Path, target: str, workspace: Path, text: str | None, reference: Path | None,
            notes: list[str], beside: Path) -> tuple[Path, Path, Path | None]:
    """→ (written file, scratch folder, pictures folder or None). Blocking. `text` replaces the
    Markdown's own (its diagrams drawn); it is read from standard input, never from a shell line."""
    program = find_pandoc()
    if program is None:
        raise ConvertError(pandoc_reason())
    scratch = Path(tempfile.mkdtemp(prefix="studio2-convert-"))
    out = scratch / (source.stem + target)
    folder = source.parent      # pictures named in the Markdown are found from here
    command = [program, "--resource-path", os.pathsep.join([str(folder), str(workspace.resolve())]), "-o", str(out)]
    media = None
    if source.suffix.lower() == ".md":
        command += ["-f", "markdown"]
        if target in (".rtf", ".html"):
            command.append("--standalone")
        if target == ".html":
            command += ["--embed-resources", "-M", f"pagetitle={source.stem}"]
        if target == ".epub" and not _has_title(text if text is not None else source.read_text("utf-8", "replace")):
            command += ["-M", f"title={source.stem}"]
            notes.append(f"The Markdown has no title, so the book is called “{source.stem}”.")
        if reference is not None:
            command += ["--reference-doc", str(reference)]
        elif target in (".docx", ".odt"):
            notes.append("The styles are pandoc's plain defaults: pass `reference` (a .docx or .odt of the project) "
                         "to follow its styles.")
        if target == ".rtf":
            notes.append("RTF from pandoc has no page settings (margins, headers, footers).")
        command.append("-")         # the Markdown comes on standard input
    else:
        # Pictures in the document are saved beside the result, and the Markdown links them from there.
        folder = beside
        media = beside / f"{source.stem}.media"
        command += ["-t", "gfm", "--wrap=none", f"--extract-media={media.name}", str(source)]
    try:
        done = subprocess.run(command, input=text if text is not None else (
            source.read_text("utf-8", errors="replace") if source.suffix.lower() == ".md" else None),
            capture_output=True, text=True, timeout=PANDOC_TIMEOUT, cwd=str(folder))
    except subprocess.TimeoutExpired:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ConvertError(f"pandoc took longer than {PANDOC_TIMEOUT} seconds on {source.name}.") from None
    if done.returncode != 0 or not out.is_file():
        shutil.rmtree(scratch, ignore_errors=True)
        detail = " ".join((done.stderr or done.stdout or "").split())[:400]
        raise ConvertError(f"pandoc could not convert {source.name}. {detail}".strip())
    for line in (done.stderr or "").splitlines():
        if "[WARNING]" in line and len(notes) < 12:
            notes.append("pandoc: " + line.replace("[WARNING]", "").strip()[:200])
    return out, scratch, media if media is not None and media.is_dir() else None


async def _draw_mermaid(studio, workspace: Path, source: Path, target: str, notes: list[str]) -> tuple[str | None, list[dict]]:
    """Mermaid blocks in the Markdown → pictures (PNG, or SVG for HTML) through render_diagram's renderer.
    → (the Markdown to convert, or None when there were none, [{n, picture | error}])."""
    from . import diagram
    text = source.read_text("utf-8", errors="replace")
    blocks = list(MERMAID_BLOCK.finditer(text))
    if not blocks:
        return None, []
    why = diagram.reason(studio.looker)
    if why:
        notes.append(f"{len(blocks)} Mermaid diagram{'s were' if len(blocks) != 1 else ' was'} left as code: {why}")
        return None, []
    drawn, pieces, at = [], [], 0
    for number, block in enumerate(blocks, 1):
        name = re.sub(r"[^a-z0-9_-]+", "-", source.stem.lower()).strip("-")[:40] or "document"
        name = f"{name}-diagram-{number}"
        try:
            result = await diagram.render(studio.looker, workspace, name=name, source=block.group("body"),
                                          png_width=None if target == ".html" else DIAGRAM_PNG_WIDTH)
        except diagram.DiagramError as exc:
            result = {"rendered": False, "error": str(exc)}
        pieces.append(text[at:block.start()])
        if result.get("rendered"):
            picture = result["svg"] if target == ".html" else result["png"]
            relative = os.path.relpath(workspace.resolve() / picture, source.parent)
            pieces.append(f"![Diagram {number}]({Path(relative).as_posix()})\n")
            drawn.append({"n": number, "picture": picture})
        else:
            pieces.append(block.group(0))
            line = text.count("\n", 0, block.start()) + 1 + (result.get("line") or 0)
            drawn.append({"n": number, "error": (result.get("error") or "")[:300], "line": line})
            notes.append(f"Mermaid diagram {number} (line {line} of the Markdown) did not draw and was left as code.")
        at = block.end()
    pieces.append(text[at:])
    return "".join(pieces), drawn


async def convert(studio, workspace: Path, relative: str, to: str, *, sheet: str | None = None,
                  reference: str | None = None, replace: bool = False) -> dict:
    started = time.monotonic()
    try:
        source = ws.safe_path(workspace, relative)
    except Exception:
        raise ConvertError("path must be a file inside the project.") from None
    if not source.is_file():
        raise ConvertError(f"There is no file at {relative} in the project.")
    kind = source.suffix.lower()
    target = "." + str(to or "").strip().lower().lstrip(".")
    if kind not in PAIRS:
        raise ConvertError(f"A {kind or 'file without an extension'} cannot be converted here. Supported: {_pairs_text()}.")
    if target not in PAIRS[kind]:
        raise ConvertError(f"A {kind} file converts to {', '.join(sorted(PAIRS[kind]))} here, not {target}.")
    program = PAIRS[kind][target]
    if program == LIBRE and office.find_soffice() is None:
        raise ConvertError(office.reason() or "LibreOffice is not installed.")
    if program == PANDOC and find_pandoc() is None:
        raise ConvertError(pandoc_reason())
    root = workspace.resolve()
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731
    where = rel(source)
    folder = ws.safe_path(workspace, "files") if where.startswith(ELSEWHERE) else source.parent
    stem = source.stem
    if target == ".csv" and kind in (".xlsx", ".ods"):
        names = await asyncio.to_thread(sheet_names, source)
        if len(names) > 1 and sheet:
            stem = f"{stem}-{re.sub(r'[^A-Za-z0-9_-]+', '-', sheet).strip('-') or 'sheet'}"
    destination = folder / (stem + target)
    if destination.exists() and not replace:
        raise ConvertError(f"{rel(destination)} already exists. Pass replace: true to write over it (the project's "
                           "history keeps the old one), or move it first.")
    style = None
    if reference:
        if program != PANDOC or target not in (".docx", ".odt"):
            raise ConvertError("`reference` is for Markdown to .docx or .odt: the styles pandoc should follow.")
        try:
            style = ws.safe_path(workspace, reference)
        except Exception:
            raise ConvertError("reference must be a .docx or .odt file inside the project.") from None
        if not style.is_file() or style.suffix.lower() != target:
            raise ConvertError(f"reference must be a {target} file of the project, to match the target.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    notes = await asyncio.to_thread(notes_before, source, target)
    diagrams: list[dict] = []
    media = None
    if program == LIBRE:
        made, scratch = await office.locked(_libre, source, target, sheet, notes)
    else:
        text = None
        if kind == ".md":
            text, diagrams = await _draw_mermaid(studio, workspace, source, target, notes)
        made, scratch, media = await asyncio.to_thread(_pandoc, source, target, workspace, text, style, notes,
                                                     destination.parent)
    try:
        if target == ".pdf" and kind in (".docx", ".pptx", ".xlsx"):
            missing = substituted(fonts_asked(source), fonts_in_pdf(made))
            if missing:
                notes.append("Fonts not on this computer were replaced by others in the PDF: " + ", ".join(missing[:10]) + ".")
        replaced = destination.exists()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), str(destination))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    check = opens(destination)
    nexts = []
    if not check["opens"]:
        nexts.append(f"The result does not open as a {target} file ({check.get('detail')}): do not hand it over.")
    if target in RENDERABLE:
        nexts.append(f"Call render_document on {rel(destination)} and look at its pages before handing it over.")
    if target in CHECKABLE:
        nexts.append(f"If it holds figures, run check_file on {rel(destination)}.")
    if target == ".html":
        nexts.append(f"Look at it with look_at_page (path /{rel(destination)}).")
    if notes:
        nexts.append("Tell the person what did not come across (`not_carried`).")
    return {"path": rel(destination), "from": where, "size": destination.stat().st_size, "tool": program,
            "version": version(program), "replaced": replaced, **check,
            "media": rel(media) if media is not None else None, "diagrams": diagrams,
            "not_carried": notes, "ms": int((time.monotonic() - started) * 1000), "next": " ".join(nexts)}
