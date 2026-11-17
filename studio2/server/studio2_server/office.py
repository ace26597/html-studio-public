"""Office files the agent makes (CONTRACT.md, "Office documents").

A PowerPoint, Word or Excel file cannot be judged, or shown inside a page, until it has been
rendered. `render` turns one into a preview folder next to it: a PDF and page images for decks
and documents, the computed cell values for workbooks, and a small self-contained viewer page
that a report can frame or link to. Rendering uses a LibreOffice that is already installed on
the machine; without one the tool says so and the studio does not pretend.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

KINDS = {".pptx": "presentation", ".ppt": "presentation", ".odp": "presentation",
         ".docx": "document", ".doc": "document", ".rtf": "document", ".odt": "document",
         ".xlsx": "workbook", ".xlsm": "workbook", ".xls": "workbook", ".ods": "workbook",
         ".pdf": "pdf"}
LABELS = {"presentation": "PowerPoint", "document": "Word", "workbook": "Excel", "pdf": "PDF"}
MAX_PAGES, MAX_ROWS, MAX_COLS = 60, 400, 50
# The preview shows at most MAX_ROWS × MAX_COLS of a sheet, but the error check looks at every
# formula, up to this many in a workbook (the cells are in memory already; this bounds the time).
MAX_CHECKED, MAX_ERRORS_KEPT = 200_000, 200
PAGE_WIDTH = {"presentation": 1600, "document": 1240, "pdf": 1240}
ERRORS = ("#REF!", "#NAME?", "#DIV/0!", "#VALUE!", "#N/A", "#NUM!", "#NULL!")
VIEWER = Path(__file__).parent / "static" / "office-viewer.html"
# LibreOffice recalculates every formula when it loads a workbook (0 = always), so a file
# written by a library, which stores formulas without results, gets its numbers.
RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""
_lock: asyncio.Lock | None = None


class OfficeError(Exception):
    pass


def find_soffice() -> Path | None:
    candidates = [os.environ.get("STUDIO2_SOFFICE"), "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                  shutil.which("soffice"), shutil.which("libreoffice")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def reason() -> str | None:
    """None when rendering can work; otherwise why not."""
    if find_soffice() is None:
        return ("LibreOffice is not installed on this machine, so Office files cannot be rendered or their "
                "formulas calculated. Install it (macOS: `brew install --cask libreoffice`; Debian or Ubuntu: "
                "`sudo apt-get install libreoffice`; Fedora: `sudo dnf install libreoffice`).")
    try:
        import openpyxl  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError as exc:
        return f"The studio's environment is missing a package for this ({exc.name})."
    return None


def _profile() -> Path:
    """The studio's own LibreOffice profile, so it never fights a LibreOffice the person has open."""
    profile = Path(tempfile.gettempdir()) / f"studio2-office-{os.getuid()}"
    settings = profile / "user" / "registrymodifications.xcu"
    if not settings.is_file():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(RECALC_PROFILE, "utf-8")
    fonts = profile / "user" / "fonts"
    if sys.platform == "darwin" and not fonts.is_dir():
        # Headless LibreOffice on a Mac sees only the fonts it ships with, so Arial or Georgia
        # would be drawn in a stand-in and the render would not match what the person opens.
        fonts.mkdir(parents=True, exist_ok=True)
        for folder in (Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                       Path("/Library/Fonts"), Path.home() / "Library/Fonts"):
            for font in folder.glob("*.[ot]t[fc]") if folder.is_dir() else []:
                link = fonts / font.name
                if not link.exists():
                    with contextlib.suppress(OSError):
                        link.symlink_to(font)
    return profile


def convert(source: Path, target: str, outdir: Path, timeout: float = 180.0) -> Path:
    """Run LibreOffice headless: `target` is its --convert-to value. → the written file."""
    soffice = find_soffice()
    if soffice is None:
        raise OfficeError(reason() or "LibreOffice is not available.")
    outdir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in outdir.iterdir()}
    command = [str(soffice), f"-env:UserInstallation={_profile().as_uri()}", "--headless", "--norestore",
               "--convert-to", target, "--outdir", str(outdir), str(source)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OfficeError(f"LibreOffice took longer than {int(timeout)} seconds on {source.name}.") from None
    made = sorted(p for p in outdir.iterdir() if p.name not in before)
    if not made:
        detail = " ".join((done.stderr or done.stdout or "").split())[:300]
        raise OfficeError(f"LibreOffice could not convert {source.name}. {detail}".strip())
    return made[0] if len(made) == 1 else made[0].parent


def rasterise(pdf: Path, outdir: Path, width: int, limit: int = MAX_PAGES) -> tuple[list[Path], int]:
    """PDF → page-N.png files of the given pixel width. → (files, total pages in the PDF)."""
    import pypdfium2

    document = pypdfium2.PdfDocument(str(pdf))
    try:
        total, files = len(document), []
        for index in range(min(total, limit)):
            page = document[index]
            image = page.render(scale=width / max(page.get_width(), 1)).to_pil()
            target = outdir / f"page-{index + 1}.png"
            image.save(target, optimize=True)
            files.append(target)
        return files, total
    finally:
        document.close()


# ---- workbooks -------------------------------------------------------------------------------
def _rgb(color) -> str | None:
    value = getattr(color, "rgb", None)
    if isinstance(value, str) and len(value) == 8 and value != "00000000":
        return "#" + value[2:]
    return None


_SECTION = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]|[_*].|.', re.S)
_DATE_PARTS = (("yyyy", "%Y"), ("yy", "%y"), ("mmmm", "%B"), ("mmm", "%b"), ("dddd", "%A"), ("ddd", "%a"),
               ("dd", "%d"), ("d", "%-d"), ("hh", "%H"), ("h", "%-H"), ("ss", "%S"), ("am/pm", "%p"))


def shown(value, number_format: str = "General") -> str:
    """A cell value the way a spreadsheet displays it under its number format. Covers the
    formats people actually use (thousands, decimals, percent, currency, scaling, a separate
    negative section, dates); anything stranger falls back to a plain rendering."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        pattern = (number_format or "").lower()
        if any(part in pattern for part in ("y", "d", "h")):
            out, rest = "", pattern.split(";")[0]
            had_hour = "h" in rest
            while rest:
                for token, code in _DATE_PARTS + ((("mm", "%M"), ("m", "%-M")) if had_hour and ":" in pattern and
                                                  (out.endswith(":") or rest[2:3] == ":" or rest[1:2] == ":")
                                                  else (("mm", "%m"), ("m", "%-m"))):
                    if rest.startswith(token):
                        out, rest = out + code, rest[len(token):]
                        break
                else:
                    out, rest = out + rest[0].replace("%", "%%").replace("\\", ""), rest[1:]
            with contextlib.suppress(ValueError):
                return value.strftime(out)
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    if not isinstance(value, (int, float)):
        return str(value)
    sections, current, quoted = [], "", False
    for char in number_format or "General":
        quoted = not quoted if char == '"' else quoted
        if char == ";" and not quoted:
            sections.append(current)
            current = ""
        else:
            current += char
    sections.append(current)
    section, number = sections[0], value
    if value < 0 and len(sections) > 1:
        section, number = sections[1], abs(value)
    elif value == 0 and len(sections) > 2:
        section = sections[2]
    tokens = _SECTION.findall(section)
    digits = [i for i, t in enumerate(tokens) if t in ("0", "#", "?")]
    if section.strip().lower() in ("general", "") or not digits or any(t in ("E", "e", "/") for t in tokens):
        if isinstance(number, int) or float(number).is_integer() and abs(number) < 1e15:
            return f"{int(value)}"
        return f"{value:.10g}"
    first, last = digits[0], digits[-1]
    core = tokens[first:last + 1]
    after = tokens[last + 1:]
    scale = 0
    while after and after[0] == ",":
        scale, after = scale + 1, after[1:]
    literal = lambda items: "".join(                                              # noqa: E731
        t[1:-1] if t.startswith('"') else t[1] if t.startswith("\\") else
        (re.sub(r"^\[\$([^\-\]]*).*\]$", r"\1", t) if t.startswith("[$") else "") if t.startswith("[") else
        (" " if t.startswith("_") else "") if t[0] in "_*" else t for t in items)
    percent = "%" in [t for t in tokens if len(t) == 1]
    number = number * (100 if percent else 1) / (1000 ** scale)
    decimals = sum(1 for t in core[core.index("."):] if t in ("0", "#", "?")) if "." in core else 0
    text = f"{abs(number):,.{decimals}f}" if "," in core else f"{abs(number):.{decimals}f}"
    sign = "-" if number < 0 and float(text.replace(",", "")) != 0 else ""
    return literal(tokens[:first]) + sign + text + literal(after)


def formula_text(value) -> str | None:
    """A cell's formula as text, or None when it holds none. An ordinary formula is text starting
    with "="; an array formula or a data table arrives from openpyxl as an object."""
    if isinstance(value, str):
        return value if value.startswith("=") else None
    text = getattr(value, "text", None)
    if isinstance(text, str) and text:
        return text if text.startswith("=") else "=" + text
    if type(value).__name__ == "DataTableFormula":
        return f"=TABLE({getattr(value, 'ref', '')})"
    return None


def read_workbook(original: Path, recalculated: Path) -> list[dict]:
    """→ one entry per sheet: the cells as they display, their formulas, a little styling, and
    every formula that came out as an error."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    formulas = openpyxl.load_workbook(original)                      # formulas and styles
    values = openpyxl.load_workbook(recalculated, data_only=True)    # what they came out as
    sheets, checked = [], 0

    def failed(value, text: str) -> str | None:
        """What a formula's result shows when it is an error or missing, else None."""
        bad = text if text.startswith(("#", "Err:")) else (value if isinstance(value, str) and value in ERRORS else None)
        return bad or ("no result" if value is None else None)

    for sheet in formulas.worksheets:
        computed = values[sheet.title] if sheet.title in values.sheetnames else None
        rows, cols = min(sheet.max_row, MAX_ROWS), min(sheet.max_column, MAX_COLS)
        cells, errors, formula_count, error_count, unchecked = [], [], 0, 0, 0

        def found(r: int, c: int, formula: str, bad: str) -> None:
            nonlocal error_count
            error_count += 1
            if len(errors) < MAX_ERRORS_KEPT:
                errors.append({"cell": f"{get_column_letter(c)}{r}", "formula": formula, "value": bad})

        for r in range(1, rows + 1):
            line = []
            for c in range(1, cols + 1):
                cell = sheet.cell(r, c)
                value = computed.cell(r, c).value if computed is not None else cell.value
                formula = formula_text(cell.value)
                text = shown(value, cell.number_format)
                entry: dict = {}
                if text != "":
                    entry["t"] = text
                if formula:
                    formula_count += 1
                    checked += 1
                    entry["f"] = formula
                    if (bad := failed(value, text)):
                        entry["e"] = 1
                        found(r, c, formula, bad)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    entry["n"] = 1
                if cell.has_style:
                    if cell.font and cell.font.b:
                        entry["b"] = 1
                    fill = cell.fill.fgColor if cell.fill and cell.fill.fill_type == "solid" else None
                    if fill is not None and _rgb(fill):
                        entry["bg"] = _rgb(fill)
                    if cell.font and _rgb(cell.font.color):
                        entry["fg"] = _rgb(cell.font.color)
                    if cell.alignment and cell.alignment.horizontal in ("center", "right", "left"):
                        entry["a"] = cell.alignment.horizontal
                line.append(entry)
            cells.append(line)
        # The rest of the sheet, outside the preview: only the cells that exist (openpyxl keeps them
        # in `_cells`; asking for others with .cell() would create them). Without this, a sheet past
        # the preview limits would be called free of errors after looking at part of it.
        computed_cells = computed._cells if computed is not None else {}
        for (r, c) in sorted(key for key in sheet._cells if key[0] > rows or key[1] > cols):
            cell = sheet._cells[(r, c)]
            formula = formula_text(cell.value)
            if not formula:
                continue
            formula_count += 1
            if checked >= MAX_CHECKED:
                unchecked += 1
                continue
            checked += 1
            held = computed_cells.get((r, c))
            value = held.value if held is not None else (None if computed is not None else cell.value)
            if (bad := failed(value, shown(value, cell.number_format))):
                found(r, c, formula, bad)
        widths = [round((sheet.column_dimensions[get_column_letter(c)].width or 10) * 7.2) for c in range(1, cols + 1)]
        sheets.append({"name": sheet.title, "rows": sheet.max_row, "columns": sheet.max_column,
                       "cut": sheet.max_row > rows or sheet.max_column > cols, "formulas": formula_count,
                       "unchecked": unchecked, "errors": errors, "error_count": error_count, "cells": cells,
                       "widths": widths, "charts": len(sheet._charts), "frozen": sheet.freeze_panes or None})
    return sheets


# A cell with content. An empty cell is self-closing (<c r="A1" s="3"/>) and must not match,
# or it would swallow the cell after it.
_CELL = re.compile(r"<c\b((?:[^>/]|/(?!>))*)>(.*?)</c>", re.S)
_FORMULA = re.compile(r"<f\b[^>]*/>|<f\b[^>]*>.*?</f>", re.S)


def stamp_values(original: Path, recalculated: Path) -> int:
    """Write each formula's computed result into the original workbook, next to the formula.

    A library such as openpyxl stores formulas without results, so anything that does not
    calculate (the Files app on a phone, Quick Look, a browser preview) shows empty cells.
    Only the cached results are touched; formulas, styles and everything else stay as written.
    → the number of cells stamped."""
    import openpyxl

    values = openpyxl.load_workbook(recalculated, data_only=True)
    with zipfile.ZipFile(original) as archive:
        names = archive.namelist()
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        relations = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        targets = {}
        for relation in re.finditer(r"<Relationship\b[^>]*>", relations):
            rid, target = re.search(r'Id="([^"]+)"', relation.group(0)), re.search(r'Target="([^"]+)"', relation.group(0))
            if rid and target:
                targets[rid.group(1)] = target.group(1)
        parts: dict[str, str] = {}
        for sheet in re.finditer(r"<sheet\b[^>]*>", workbook):
            name, rid = re.search(r'name="([^"]*)"', sheet.group(0)), re.search(r'r:id="([^"]+)"', sheet.group(0))
            if not name or not rid or rid.group(1) not in targets:
                continue
            target = targets[rid.group(1)]
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if path in names:
                parts[path] = html.unescape(name.group(1))
        stamped, changed = 0, {}
        for path, title in parts.items():
            if title not in values.sheetnames:
                continue
            computed = values[title]

            def patch(match: re.Match) -> str:
                nonlocal stamped
                attrs, inner = match.group(1), match.group(2)
                formula = _FORMULA.search(inner)
                ref = re.search(r'\br="([A-Z]+[0-9]+)"', attrs)
                if not formula or not ref:
                    return match.group(0)
                value = computed[ref.group(1)].value
                attrs = re.sub(r'\s+t="[^"]*"', "", attrs)
                if value is None:
                    return f"<c{attrs}>{formula.group(0)}</c>"
                if isinstance(value, bool):
                    kind, text = ' t="b"', "1" if value else "0"
                elif isinstance(value, (int, float)):
                    kind, text = "", repr(value)
                elif isinstance(value, str) and value in ERRORS:
                    kind, text = ' t="e"', value
                else:
                    kind, text = ' t="str"', html.escape(str(value), quote=False)
                stamped += 1
                return f"<c{attrs}{kind}>{formula.group(0)}<v>{text}</v></c>"

            before = archive.read(path).decode("utf-8")
            after = _CELL.sub(patch, before)
            if after != before:
                changed[path] = after
        if not changed:
            return 0
        temporary = original.with_name(f"{original.stem}.stamping{original.suffix}")   # keeps an extension openpyxl accepts
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
            for item in archive.infolist():
                data = changed[item.filename].encode("utf-8") if item.filename in changed else archive.read(item.filename)
                out.writestr(item, data)
    try:
        openpyxl.load_workbook(temporary)          # never replace a good file with one that does not open
    except Exception:
        temporary.unlink(missing_ok=True)
        return 0
    temporary.replace(original)
    return stamped


# ---- the preview folder ------------------------------------------------------------------------
def write_viewer(folder: Path, data: dict) -> Path:
    """The self-contained viewer page for one file. It needs nothing from the studio."""
    page = VIEWER.read_text("utf-8").replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    target = folder / "index.html"
    target.write_text(page, "utf-8")
    return target


def _grid(sheet: dict, rows: int = 22, cols: int = 10) -> list[str]:
    """A few rows of a sheet as text, for the agent to read."""
    return [" | ".join((cell.get("t") or "")[:18] for cell in line[:cols]) for line in sheet["cells"][:rows]]


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def workbook_result(sheets: list[dict], stamped: int, pages: list) -> dict:
    """What the agent is told about a workbook. It says "no formula errors" only when every
    formula was checked, and says which sheets the preview shows only part of."""
    total = sum(s["formulas"] for s in sheets)
    unchecked = [s for s in sheets if s["unchecked"]]
    if any(s["error_count"] for s in sheets):
        verdict = "Fix every formula listed under errors (error_count says how many there are), then render again."
    elif unchecked:
        missing = sum(s["unchecked"] for s in unchecked)
        verdict = (f"Only {total - missing:,} of the {_count(total, 'formula')} were checked, and none of those has an error; "
                   + "; ".join(f"{_count(s['unchecked'], 'formula')} of sheet {s['name']!r} were not checked"
                               for s in unchecked)
                   + f" (the check stops after {MAX_CHECKED:,} formulas in a workbook), so errors may remain there.")
    else:
        verdict = f"No formula errors in any of the {_count(total, 'formula')}."
    cut = [s for s in sheets if s["cut"]]
    if cut:
        verdict += (f" The preview and start show only the first {MAX_ROWS} rows and {MAX_COLS} columns of "
                    + ", ".join(f"{s['name']!r} ({s['rows']:,} × {s['columns']:,})" for s in cut) + ".")
    if not any(s["error_count"] for s in sheets):
        verdict += " Look at the preview page with look_at_page to check the formatting."
        if pages:
            verdict += " The charts are drawn in chart_pages: open those images with the Read tool."
    return {
        "sheets": [{"name": s["name"], "rows": s["rows"], "columns": s["columns"], "cut": s["cut"],
                    "formulas": s["formulas"], "unchecked": s["unchecked"], "charts": s["charts"],
                    "errors": s["errors"][:30], "error_count": s["error_count"], "start": _grid(s)} for s in sheets],
        "formula_results_stored": stamped, "next": verdict}


def render(workspace: Path, relative: str, stamp: bool = True, max_pages: int = MAX_PAGES) -> dict:
    """Render one project file into `<file>.preview/`. Blocking; call from a thread."""
    source = (workspace / relative).resolve()
    kind = KINDS.get(source.suffix.lower())
    if kind is None:
        raise OfficeError("Only PowerPoint, Word, Excel and PDF files can be rendered "
                          f"({', '.join(sorted(KINDS))}).")
    if kind != "pdf" and reason():
        raise OfficeError(reason())
    folder = source.with_name(source.name + ".preview")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    rel = lambda path: path.relative_to(workspace.resolve()).as_posix()  # noqa: E731
    data = {"name": source.name, "kind": kind, "label": LABELS[kind], "file": "../" + source.name,
            "bytes": source.stat().st_size}
    result: dict = {"file": relative, "kind": kind}
    with tempfile.TemporaryDirectory(prefix="studio2-office-") as scratch:
        work = Path(scratch)
        if kind == "workbook":
            recalculated = convert(source, "xlsx", work / "recalc")
            sheets = read_workbook(source if source.suffix.lower() in (".xlsx", ".xlsm") else recalculated, recalculated)
            stamped = 0
            if stamp and source.suffix.lower() in (".xlsx", ".xlsm") and any(s["formulas"] for s in sheets):
                stamped = stamp_values(source, recalculated)
                data["bytes"] = source.stat().st_size
            data["sheets"] = sheets
            pages = []
            if any(s["charts"] for s in sheets):
                # Charts are drawn by the spreadsheet program, not stored as pictures: the print
                # view is the only place they can be seen without opening the file.
                # One page per sheet, whatever its size: default page breaks cut charts in half.
                pdf = convert(recalculated, 'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}',
                              work / "pdf")
                shutil.copyfile(pdf, folder / "document.pdf")
                pages, total = rasterise(pdf, folder, PAGE_WIDTH["presentation"], 20)
                data.update({"pdf": "document.pdf", "pages": [p.name for p in pages], "page_count": total})
                result["chart_pages"] = [rel(p) for p in pages]
            result.update(workbook_result(sheets, stamped, pages))
        else:
            pdf = source if kind == "pdf" else convert(source, "pdf", work / "pdf")
            if kind != "pdf":
                shutil.copyfile(pdf, folder / "document.pdf")
                data["pdf"] = "document.pdf"
            pages, total = rasterise(pdf, folder, PAGE_WIDTH[kind], max_pages)
            data.update({"pages": [p.name for p in pages], "page_count": total})
            result.update({
                "pdf": rel(folder / "document.pdf") if kind != "pdf" else relative,
                "pages": [rel(p) for p in pages], "page_count": total,
                "next": "Open every page image with the Read tool and fix what is cramped, cut off, misaligned or "
                        "empty, then render again."})
    viewer = write_viewer(folder, data)
    result["preview"] = rel(viewer)
    result["embed"] = (f'<iframe src="{rel(viewer)}" title="{html.escape(source.name)}" loading="lazy" '
                       f'style="width:100%;height:{"620" if kind == "workbook" else "720"}px;border:0"></iframe>'
                       "  (the path is from the project root: make it relative to the page that frames it)")
    return result


async def render_async(workspace: Path, relative: str, **options) -> dict:
    global _lock
    _lock = _lock or asyncio.Lock()
    async with _lock:  # one LibreOffice at a time on the studio's profile
        return await asyncio.to_thread(render, workspace, relative, **options)
