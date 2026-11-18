"""The file check: every figure in a deck, a Word document, a workbook or a PDF of the project is
looked for in what the project can vouch for (CONTRACT.md, "Verify content reaches files").

What vouches, in this order: the figures the page check found verified on the project's pages
(`checks/<page>.json`), the project's data files (`data/`, CSV, JSON and GeoJSON), and the saved text of
its sources (`sources/`). A figure is found (exactly, or as a correct rounding), derived (a
workbook formula, or a figure a page marks as derived), or not found. Found says the number is
in a vouched place, not that it is the right number for that sentence.

Office files are zips of XML: slides and paragraphs are read with the standard library, cells
with openpyxl and PDF pages with pypdf, all already in the studio's environment. No LibreOffice,
no model.
"""

from __future__ import annotations

import ast
import bisect
import csv
import io
import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from .check import DATE
from .config import now_iso
from .sources import SourceError, Sources
from .store import write_json_atomic

RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
KINDS = {".pptx": "presentation", ".docx": "document", ".xlsx": "workbook", ".pdf": "pdf"}
MAX_ITEMS, CONTEXT = 10_000, 160
MAX_DATA_FILES, MAX_DATA_BYTES, MAX_DATA_ROWS, MAX_CELLS = 200, 20_000_000, 200_000, 200_000
MAX_PLACEHOLDERS, MAX_CONFLICTS, MAX_LEDGER_PROBLEMS, CONFLICT_FIGURES = 500, 200, 200, 6
# The tool's answer: how many of each list it carries (the saved file has the rest).
ANSWER = {"not_found": 40, "derived": 15, "rounded": 15, "placeholders": 30, "conflicts": 15,
          "ledger_problems": 20, "by_part": 120}
LEDGER = "data/facts.json"
NOT_FOUND_WHY = "Not in any checked page figure, data file or saved source."
PLACEHOLDER_WHY = "Leftover placeholder text."

# -- figures in text ----------------------------------------------------------------------------
# A number: Western (1,234,567) or Indian (12,34,567) grouping, or plain digits, with decimals.
CORE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})*,\d{3}(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d,]\d)")
SCALES = {"thousand": 3, "k": 3, "million": 6, "mn": 6, "m": 6, "mio": 6, "billion": 9, "bn": 9, "b": 9,
          "trillion": 12, "tn": 12, "lakh": 5, "lakhs": 5, "lac": 5, "crore": 7, "crores": 7, "cr": 7}
SPACED = {"thousand", "million", "mn", "billion", "bn", "trillion", "tn", "lakh", "lakhs", "lac", "crore",
          "crores", "cr"}         # a single letter is a scale only when written against the number: 4.2m
UNITS = {"x", "pp", "bp", "bps", "km", "kg", "mw", "gw", "kw", "kwh", "mwh", "gwh", "twh", "w", "wh",
         "h", "hr", "hrs", "ms", "min", "mins", "mph", "kmh", "gb", "tb", "mb", "kb", "ml", "cm", "mm",
         "ft", "sqft", "yr", "yrs", "mo", "pc", "pcs", "kwp", "mwp", "kv", "ah", "kva"}
ORDINALS = {"st", "nd", "rd", "th"}
CURRENCY = re.compile(r"(?:US\$|A\$|C\$|S\$|[$€£₹¥]|Rs\.?|USD|EUR|GBP|INR|JPY)\s?$")
NUMBERED = re.compile(r"(?:\b(?:slide|slides|page|pages|p\.|pp\.|figure|fig\.|table|chart|exhibit|appendix|section|"
                      r"chapter|step|item|no\.|number)|#)\s*$", re.I)
MASKS = [
    re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\w+", re.I),                       # addresses
    DATE,                                                                           # dates, times, FY, year ranges
    re.compile(r"\b(?:version|ver\.?|v)\s?\d+(?:\.\d+)*\b|\b\d+(?:\.\d+){2,}\b", re.I),   # versions
    re.compile(r"\+\d[\d\s().-]{6,}\d|\(\d{3}\)\s?\d{3}[-.\s]\d{4}\b|\b\d{3}[-.]\d{3}[-.]\d{4}\b"),  # phones
    re.compile(r"\b\d+(?:-\d+){2,}\b"),                                             # ids joined by hyphens
    re.compile(r"\[\d{1,3}(?:[,–-]\s?\d{1,3})*\]"),                                 # reference markers [1]
]
PAGE_LINE = re.compile(r"^\s*(?:(?:page|slide|p\.)\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.I)


class Figure(dict):
    """{text, value (Decimal, the quantity: 12.3 for 12.3%, 4200000 for 4.2 million), decimals,
    scale, percent, where, context}."""


def _mask(text: str) -> str:
    """The text with what is not a figure blanked out, character for character."""
    for pattern in MASKS:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _context(line: str, start: int, end: int) -> str:
    room = (CONTEXT - (end - start)) // 2
    left, right = max(0, start - room), min(len(line), end + room)
    return ((" …" if left else "") + " ".join(line[left:right].split()) + ("…" if right < len(line) else "")).strip()


def figures_in(text: str, where: str, cell: bool = False, pdf: bool = False,
               label: str | None = None) -> tuple[list[Figure], int]:
    """→ (the figures in the text, how many numbers were skipped as not figures). `cell`: the text
    is a spreadsheet cell's shown value, so list numbering and the long-id rule do not apply.
    `pdf`: a line that is only a page number ("3", "Page 3", "3 of 12") is skipped. Each figure
    carries `label`: the words right before it and a colon ("Revenue 2025: $4.2m"), else the
    `label` given (a table cell's row and column), else None."""
    figures, skipped = [], 0
    for line in (text or "").split("\n"):
        if not line.strip():
            continue
        if pdf and PAGE_LINE.match(line):
            skipped += len(CORE.findall(line))
            continue
        masked = _mask(line)
        skipped += len(CORE.findall(line)) - len(CORE.findall(masked))
        for match in CORE.finditer(masked):
            figure = _figure(line, masked, match, cell)
            if figure is None:
                skipped += 1
                continue
            span = figure.pop("span")
            figure.update(where=where, context=_context(line, *span), label=_label_before(line[:span[0]]) or label)
            figures.append(figure)
    return figures, skipped


LABEL_SPLIT = re.compile(r"[.;•|()\[\]{}]|,\s|\s[–—-]\s")


def label_key(label: str | None) -> str | None:
    """A label as compared: lower case, words only. None unless it has two words and a letter."""
    words = re.findall(r"[a-z0-9]+", (label or "").lower())
    if words and words[0] == "the":
        words = words[1:]
    if len(words) < 2 or not any(re.search(r"[a-z]", w) for w in words):
        return None
    return " ".join(words)


def _label_before(before: str) -> str | None:
    """The words written right before a figure and a colon or "=" ("Revenue 2025: "), back to the
    start of the line or the last sentence, list or bracket break."""
    match = re.search(r"([^:=]{2,60}?)\s*[:=]\s*$", LABEL_SPLIT.split(before)[-1])
    if not match:
        return None
    text = " ".join(match.group(1).split())
    return text if label_key(text) else None


# -- leftover placeholders ----------------------------------------------------------------------
_HERE_WORDS = ("text|title|name|content|image|picture|logo|chart|data|subtitle|heading|caption|quote|copy|"
               "number|date|value")
PLACEHOLDER_ANY_CASE = re.compile("|".join([
    r"\b(?:click|tap)\s+(?:here\s+)?(?:icon\s+)?to\s+(?:add|edit|insert|enter)\b[^.\n]{0,40}",
    r"\blorem\s+ipsum\b[^.\n]{0,30}|\bdolor\s+sit\s+amet\b",
    r"\{\{[^{}\n]{1,60}\}\}|\$\{[A-Za-z_][\w.-]{0,40}\}|(?<![\w$}{])\{[A-Za-z_][\w.-]{0,40}\}(?![\w}])",
    r"<<[A-Za-z_][\w.-]{0,40}>>|«[A-Za-z_][\w.-]{0,40}»",
    r"\[\s*(?:insert|add|enter|placeholder|tbd|tbc|todo|fill\s+in|your|source|citation\s+needed|x{2,})\b[^\]\n]{0,60}\]",
    rf"\b(?:your|insert|add|enter|type)\s+(?:[\w-]+\s+){{0,2}}(?:{_HERE_WORDS})\s+here\b",
]), re.I)
PLACEHOLDER_CAPITALS = re.compile(
    r"\b(?:TBD|TBC|FIXME)\b|(?<![\w$€£₹¥])(?:X{3,}|x{3,})(?!\w)|\bTODO\s*:|^\s*TODO\b"
    r"|[$€£₹¥]\s?X+(?:[.,]X+)*(?:\s?(?:million|billion|thousand|[mbk]n?))?(?!\w)|(?<!\w)X+(?:\.X+)?\s?%", re.M)


def placeholders_in(text: str, where: str) -> list[dict]:
    """Leftover placeholder text in one piece of text → [{text, where, context}] (CONTRACT.md,
    "Very large documents")."""
    found, spans = [], []
    for pattern in (PLACEHOLDER_ANY_CASE, PLACEHOLDER_CAPITALS):
        for match in pattern.finditer(text or ""):
            shown = " ".join(match.group(0).split())
            if not shown or any(match.start() < end and start < match.end() for start, end in spans):
                continue                                                # one leftover, found by two rules
            spans.append((match.start(), match.end()))
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end >= 0 else len(text)]
            found.append({"text": shown[:120], "where": where,
                          "context": _context(line, match.start() - line_start, match.end() - line_start)})
    return found


def _figure(line: str, masked: str, match: re.Match, cell: bool) -> Figure | None:
    raw, start, end = match.group(1), match.start(1), match.end(1)
    before, after = masked[:start], masked[end:]
    if NUMBERED.search(before):
        return None                                                     # slide 3, page 4, #4411
    if before.endswith("[") and after.startswith("]"):
        return None                                                     # a reference marker
    currency = CURRENCY.search(before)
    first = currency.start() if currency else start
    signed = False
    if before[:first].endswith(("-", "−")) and not before[:first - 1][-1:].isalnum():
        signed, first = True, first - 1                                 # -1,234 (not the 15 of 10-15)
    opened = before[:first].endswith("(")
    percent, scale, rest = False, 0, after
    pct = re.match(r"\s?(?:%|per\s?cent\b)", rest, re.I)
    if pct:
        percent, rest = True, rest[pct.end():]
    else:
        glued = re.match(r"([A-Za-z]+)", rest)
        spaced = re.match(r"\s([A-Za-z]+)\b", rest)
        if glued:
            word = glued.group(1).lower()
            if word in ORDINALS:
                return None                                             # 3rd, 21st
            if word in SCALES:
                scale, rest = SCALES[word], rest[glued.end():]
            elif word not in UNITS:
                return None                                             # 4411A, 5G: an id or a code
        elif spaced and spaced.group(1).lower() in SPACED:
            scale, rest = SCALES[spaced.group(1).lower()], rest[spaced.end():]
    small = re.fullmatch(r"\d{1,3}", raw) is not None and not (currency or percent or scale or signed)
    at_line_start = not before[:first - (1 if opened else 0)].strip()
    if not cell and small and at_line_start and (re.match(r"[.)](?:\s|$)", after) or (opened and after.startswith(")"))):
        return None                                                     # 1. 2) (3) numbering a line
    parenthesised = opened and rest.startswith(")") and not signed
    if parenthesised:
        first -= 1
    negative = signed or parenthesised
    if not (currency or percent or scale or negative or "," in raw or "." in raw):
        number = int(raw)
        if 1900 <= number <= 2100 and len(raw) == 4:
            return None                                                 # a year standing alone
        if number <= 12:
            return None                                                 # "3 regions": too small to tell apart
        if not cell and len(raw) >= 9:
            return None                                                 # an account, order or ISBN number
    try:
        shown = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    last = end + (len(after) - len(rest)) + (1 if parenthesised else 0)
    return Figure(text=" ".join(line[first:last].split()), value=shown.scaleb(scale),
                  decimals=len(raw.split(".")[1]) if "." in raw else 0, scale=scale, percent=percent,
                  negative=negative, span=(first, last))


# -- what vouches for a figure ------------------------------------------------------------------
def _number(text: str) -> tuple[Decimal, bool] | None:
    """A data cell → (value, is a percentage), or None when it is not a number."""
    cleaned = re.sub(r"[\s$€£₹¥,]", "", str(text)).replace("−", "-")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    percent = cleaned.endswith("%")
    cleaned = cleaned.rstrip("%")
    if not re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned), percent
    except InvalidOperation:
        return None


def _key(value: Decimal) -> Decimal:
    return abs(value).normalize()


class Pool:
    """Vouched values: exact lookup by value, and a sorted list for roundings."""

    def __init__(self):
        self.exact: dict[Decimal, tuple] = {}
        self.values: list[float] = []
        self.rows: list[tuple] = []          # parallel to values: (value, priority, via, ref)
        self._sorted = True

    def add(self, value: Decimal, priority: int, via: str, ref: str) -> None:
        if not value.is_finite():
            return
        key = _key(value)
        if key not in self.exact:
            self.exact[key] = (value, priority, via, ref)
            self.values.append(float(key))
            self.rows.append((key, priority, via, ref))
            self._sorted = False

    def _sort(self):
        if not self._sorted:
            order = sorted(range(len(self.values)), key=self.values.__getitem__)
            self.values = [self.values[i] for i in order]
            self.rows = [self.rows[i] for i in order]
            self._sorted = True

    def find(self, target: Decimal, quantum: Decimal) -> tuple | None:
        """→ (match, value, via, ref) for an exact value, else the best rounding, else None."""
        key = _key(target)
        if key in self.exact:
            value, _p, via, ref = self.exact[key]
            return "exact", key, via, ref
        self._sort()
        half = quantum / 2
        low, high = float(key - half), float(key + half)
        start = bisect.bisect_left(self.values, low - abs(low) * 1e-12)
        best = None
        for index in range(start, len(self.values)):
            if self.values[index] > high + abs(high) * 1e-12:
                break
            value, priority, via, ref = self.rows[index]
            if abs(value - key) <= half and (best is None or (priority, abs(value - key)) < best[0]):
                best = ((priority, abs(value - key)), value, via, ref)
        return ("rounded", best[1], best[2], best[3]) if best else None


def _numbers_loose(text: str):
    """Every number in a vouching text with its scale word: years and small numbers included."""
    for match in CORE.finditer(text):
        raw = match.group(1)
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        after = text[match.end(1):match.end(1) + 14]
        glued, spaced = re.match(r"([A-Za-z]+)", after), re.match(r"\s([A-Za-z]+)\b", after)
        if glued and glued.group(1).lower() in SCALES:
            value = value.scaleb(SCALES[glued.group(1).lower()])
        elif spaced and spaced.group(1).lower() in SPACED:
            value = value.scaleb(SCALES[spaced.group(1).lower()])
        yield value


def _data_files(workspace: Path) -> list[Path]:
    folder = workspace / "data"
    if not folder.is_dir():
        return []
    found = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".json", ".geojson")
                   and not any(part.startswith(".") for part in p.relative_to(workspace).parts))
    return [p for p in found if p.stat().st_size <= MAX_DATA_BYTES][:MAX_DATA_FILES]


def _add_csv(pool: Pool, path: Path, name: str) -> None:
    text = path.read_text("utf-8-sig", "replace")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count) if first else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None) or []
    totals: dict[int, Decimal] = {}
    counted: dict[int, int] = {}
    for row_number, row in enumerate(reader, 1):
        if row_number > MAX_DATA_ROWS:
            break
        for column, cell in enumerate(row):
            parsed = _number(cell) if cell.strip() else None
            if parsed is None:                      # a cell of text: the numbers written inside it
                for value in _numbers_loose(cell):
                    pool.add(value, 1, "data", f"{name}, row {row_number}")
                continue
            value, _percent = parsed
            label = header[column].strip() if column < len(header) and header[column].strip() else f"{column + 1}"
            pool.add(value, 1, "data", f"{name}, column {label}, row {row_number}")
            totals[column] = totals.get(column, Decimal(0)) + value
            counted[column] = counted.get(column, 0) + 1
    for column, total in totals.items():
        if counted[column] >= 2:
            label = header[column].strip() if column < len(header) and header[column].strip() else f"{column + 1}"
            pool.add(total, 1, "data", f"{name}, column {label} (total)")


def _add_json(pool: Pool, path: Path, name: str) -> None:
    try:
        data = json.loads(path.read_text("utf-8-sig", "replace"))
    except ValueError:
        return
    seen = 0

    def walk(node, at: str):
        nonlocal seen
        seen += 1
        if seen > MAX_DATA_ROWS * 4:
            return
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            pool.add(Decimal(str(node)), 1, "data", f"{name}, {at or 'value'}")
        elif isinstance(node, str):
            parsed = _number(node)
            if parsed is not None:
                pool.add(parsed[0], 1, "data", f"{name}, {at or 'value'}")
            else:                                   # text: the numbers written inside it ("148 km WSW of …")
                for value in _numbers_loose(node):
                    pool.add(value, 1, "data", f"{name}, {at or 'value'}")
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{at}.{key}" if at else str(key))
        elif isinstance(node, list):
            totals: dict[str, Decimal] = {}
            for index, value in enumerate(node):
                walk(value, f"{at}[{index}]")
                if isinstance(value, dict):
                    for key, item in value.items():
                        if isinstance(item, (int, float)) and not isinstance(item, bool):
                            totals[key] = totals.get(key, Decimal(0)) + Decimal(str(item))
            if len(node) >= 2:
                for key, total in totals.items():
                    pool.add(total, 1, "data", f"{name}, {at or 'list'}[].{key} (total)")

    walk(data, "")


# -- the ledger: data/facts.json ------------------------------------------------------------------
KEY = re.compile(r"[a-z][a-z0-9_]*")
OBTAINED = ("quoted", "data", "computed", "given")
_FUNCTIONS = {"abs": lambda *a: abs(a[0]), "min": lambda *a: min(a), "max": lambda *a: max(a),
              "sum": lambda *a: sum(a, Decimal(0)),
              "round": lambda *a: round(a[0], int(a[1])) if len(a) > 1 else round(a[0])}


class LedgerError(Exception):
    pass


def _precision(value: Decimal, written: bool = False) -> Decimal:
    """Half of this is how far a figure may be from `value` and still be it. `written`: the digits
    as written (1687.58 → 0.01); otherwise trailing zeros count too (4200000 → 100000)."""
    exponent = (value if written else value.normalize()).as_tuple().exponent
    return Decimal(1).scaleb(min(exponent, 0) if written else exponent)


def _evaluate(formula: str, values: dict[str, Decimal], allowed: set[str]) -> Decimal:
    """A ledger formula → its value, from the ledger values of the keys it names. Only numbers,
    the keys in `from`, + - * / **, brackets and abs, min, max, round, sum."""
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError:
        raise LedgerError(f"the formula {formula!r} cannot be read") from None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in allowed:
                raise LedgerError(f"the formula uses {node.id}, which is not in from")
            if node.id not in values:
                raise LedgerError(f"{node.id} has no checked value in the ledger")
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise LedgerError("the formula divides by zero")
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise LedgerError("the formula raises to too large a power")
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right if isinstance(node.op, ast.Div) else left ** right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS \
                and not node.keywords and node.args:
            return Decimal(_FUNCTIONS[node.func.id](*[walk(arg) for arg in node.args]))
        raise LedgerError(f"the formula {formula!r} has something other than keys, numbers and + - * / ( )")

    try:
        return walk(tree)
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise LedgerError(f"the formula cannot be calculated ({exc.__class__.__name__})") from None


def read_ledger(workspace: Path, sources: Sources) -> tuple[list[tuple[Decimal, str, str | None]], dict | None]:
    """data/facts.json → ([(value, key, note)] for the facts that vouch, the `ledger` block of the
    result), or ([], None) when there is no ledger. A fact with a problem vouches for nothing."""
    path = workspace / LEDGER
    if not path.is_file():
        return [], None
    info = {"file": LEDGER, "facts": 0, "vouching": 0, "computed": 0, "given": 0, "problems": [],
            "problems_total": 0, "error": None}
    try:
        data = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError) as exc:
        info["error"] = f"{LEDGER} is not JSON ({exc.__class__.__name__}), so it was read as an ordinary data file."
        return [], info
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        info["error"] = (f"{LEDGER} has no list called facts at its top level, so it was read as an ordinary "
                         "data file.")
        return [], info
    problems: dict[str, str] = {}
    known: dict[str, dict] = {}
    unnamed = 0

    def problem(key: str, why: str) -> None:
        problems.setdefault(key, why)

    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            unnamed += 1
            problem(f"facts[{index}]", "it is not an object with key, value and obtained.")
            continue
        key = str(fact.get("key") or "")
        if not KEY.fullmatch(key):
            problem(key or f"facts[{index}]", "its key is missing or not a lower-case name (letters, digits, _).")
            continue
        if key in known:
            problem(key, "the same key is in the ledger twice.")
            continue
        known[key] = fact
        raw = fact.get("value")
        parsed = None if isinstance(raw, bool) or raw is None else _number(str(raw))
        if parsed is None:
            problem(key, "its value is not a number.")
            continue
        fact["_value"] = parsed[0]
        if fact.get("obtained") not in OBTAINED:
            problem(key, "obtained is not quoted, data, computed or given.")
    info["facts"] = len(known) + unnamed

    pools: dict[str, Pool | None] = {}

    def source_pool(name: str) -> Pool | None:
        if name not in pools:
            pool = None
            if name.startswith("data/") or name.endswith((".csv", ".json")):
                target = (workspace / name).resolve()
                inside = (workspace / "data").resolve() in target.parents
                if inside and target.is_file() and target.suffix.lower() in (".csv", ".json") and name != LEDGER:
                    pool = Pool()
                    try:
                        (_add_csv if target.suffix.lower() == ".csv" else _add_json)(pool, target, name)
                    except OSError:
                        pool = None
            else:
                try:
                    text = sources.text(name)
                except (SourceError, OSError, ValueError):
                    text = None
                if text is not None:
                    pool = Pool()
                    for value in _numbers_loose(text):
                        pool.add(value, 2, "source", name)
            pools[name] = pool
        return pools[name]

    checked: dict[str, Decimal] = {}
    for key, fact in known.items():
        if key in problems or fact["obtained"] in ("computed", "given"):
            continue
        source = str(fact.get("source") or "").strip()
        if not source:
            problem(key, "it has no source.")
            continue
        pool = source_pool(source)
        if pool is None:
            problem(key, f"{source} is not a saved source." if fact["obtained"] == "quoted" else
                    f"{source} is not a CSV or JSON file under data/.")
            continue
        value = fact["_value"]
        if pool.find(value, _precision(value)) is None:
            problem(key, f"{_plain(value)} is not in {'the saved text of ' if fact['obtained'] == 'quoted' else ''}"
                         f"{source}.")
            continue
        checked[key] = value

    for key, fact in known.items():
        if key not in problems and fact["obtained"] == "given":
            checked[key] = fact["_value"]
    resolving: set[str] = set()

    def computed(key: str) -> bool:
        """Check one computed fact (and the computed facts it uses first). → whether it vouches."""
        if key in checked:
            return True
        if key in problems or key not in known or known[key].get("obtained") != "computed":
            return False
        if key in resolving:
            problem(key, "it depends on itself.")
            return False
        fact = known[key]
        inputs = fact.get("from") if isinstance(fact.get("from"), list) else []
        formula = str(fact.get("formula") or "")
        if not inputs or not formula:
            problem(key, "a computed fact needs from (its keys) and formula.")
            return False
        resolving.add(key)
        try:
            for name in inputs:
                if str(name) not in known:
                    problem(key, f"{name} in from is not a key of the ledger.")
                    return False
                if not computed(str(name)):
                    problem(key, f"its input {name} has a problem.")
                    return False
            try:
                result = _evaluate(formula, checked, {str(name) for name in inputs})
            except LedgerError as exc:
                problem(key, f"{exc}.")
                return False
        finally:
            resolving.discard(key)
        value = fact["_value"]
        if abs(result - value) > _precision(value, written=True) / 2 * Decimal("1.000001"):
            problem(key, f"the formula gives {float(result):.10g}, not {_plain(value)}.")
            return False
        checked[key] = value
        return True

    for key, fact in known.items():
        if fact.get("obtained") == "computed":
            computed(key)
    vouching = []
    for key, fact in known.items():
        if key in checked and key not in problems:
            note = (f"computed in the ledger: {str(fact.get('formula'))[:160]}" if fact["obtained"] == "computed" else
                    "given by the person; not checked against a source" if fact["obtained"] == "given" else None)
            vouching.append((checked[key], key, note))
    listed = [{"key": key, "why": why} for key, why in problems.items()]
    info.update(vouching=len(vouching), problems=listed[:MAX_LEDGER_PROBLEMS], problems_total=len(listed),
                computed=sum(1 for f in known.values() if f.get("obtained") == "computed"),
                given=sum(1 for f in known.values() if f.get("obtained") == "given"))
    return vouching, info


def vouched(workspace: Path) -> tuple[Pool, Pool, dict]:
    """→ (what vouches for a figure, the figures pages mark as derived, what was read). The ledger
    comes first, so a figure it holds is reported by its key."""
    pool, derived = Pool(), Pool()
    sources = Sources(workspace)
    facts, ledger = read_ledger(workspace, sources)
    notes: dict[str, str] = {}
    for value, key, note in facts:
        pool.add(value, -1, "ledger", key)
        if note:
            notes[key] = note
    pages = []
    for saved in sorted((workspace / "checks").glob("*.json")) if (workspace / "checks").is_dir() else []:
        try:
            result = json.loads(saved.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(result, dict) or "page" not in result or not isinstance(result.get("items"), list):
            continue                                                    # a file check's result, or not a check
        pages.append(result["page"])
        for item in result["items"]:
            if not isinstance(item, dict):
                continue
            ref = f"{result['page']}" + (f" › {item['where']}" if item.get("where") else "")
            target = pool if item.get("status") == "verified" else derived if item.get("status") == "derived" else None
            if target is None:
                continue
            for figure in figures_in(str(item.get("text") or ""), "", cell=True)[0]:
                target.add(figure["value"], 0, "page", ref if target is pool else
                           f"{ref} ({str(item.get('reason') or '')[:120]})")
            if item.get("found") is not None and target is pool:
                parsed = _number(item["found"])
                if parsed is not None:
                    pool.add(parsed[0], 0, "page", ref)
    data_files = []
    for path in _data_files(workspace):
        name = path.relative_to(workspace).as_posix()
        if name == LEDGER and ledger is not None and ledger["error"] is None:
            continue                                                    # read above, as the ledger
        try:
            (_add_csv if path.suffix.lower() == ".csv" else _add_json)(pool, path, name)
            data_files.append(name)
        except OSError:
            continue
    count = 0
    for item in sources.list():
        try:
            text = sources.text(item["id"])
        except SourceError:
            continue
        count += 1
        for value in _numbers_loose(text):
            pool.add(value, 2, "source", item["id"])
    return pool, derived, {"pages": pages, "sources": count, "data_files": data_files, "ledger": ledger,
                           "ledger_notes": notes}


def _quantum(figure: Figure) -> Decimal:
    return Decimal(1).scaleb(figure["scale"] - figure["decimals"])


def judge(figure: Figure, pool: Pool, derived: Pool) -> dict:
    """One figure → its status and where it was found."""
    targets = [(figure["value"], _quantum(figure))]
    if figure["percent"]:
        targets.append((figure["value"] / 100, _quantum(figure) / 100))
    for source, status in ((pool, "found"), (derived, "derived")):
        hits = [source.find(value, quantum) for value, quantum in targets]
        hits = [hit for hit in hits if hit]
        if not hits:
            continue
        hit = next((h for h in hits if h[0] == "exact"), hits[0])
        match, value, via, ref = hit
        out = {"status": status, "match": match, "via": via, "ref": ref,
               "rounded_from": _plain(value) if match == "rounded" else None, "note": None}
        if status == "derived":
            out["note"] = f"The same figure is marked derived on {ref}: its arithmetic is not checked."
        return out
    return {"status": "not_found", "match": None, "via": None, "ref": None, "rounded_from": None, "note": None}


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


# -- reading the files ----------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _child(element, name: str):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _xml(archive: zipfile.ZipFile, name: str):
    return ElementTree.fromstring(archive.read(name))


def _drawing_paragraphs(body) -> list[str]:
    """DrawingML paragraphs (a:p) → their text; superscript runs and slide-number fields left out."""
    out = []
    for paragraph in body.iter():
        if _local(paragraph.tag) != "p":
            continue
        parts = []
        for run in paragraph:
            kind = _local(run.tag)
            if kind == "br":
                parts.append("\n")
            elif kind in ("r", "fld"):
                if kind == "fld" and (_attr(run, "type") or "").startswith(("slidenum", "datetime")):
                    continue
                props = _child(run, "rPr")
                if props is not None and int(_attr(props, "baseline") or 0) > 0:
                    continue
                text = _child(run, "t")
                parts.append(text.text or "" if text is not None else "")
        out.append("".join(parts))
    return out


def _table_labels(rows: list[list[str]]) -> dict[tuple[int, int], str]:
    """A table's cells → the label of each body cell: its row's first cell and its column's first
    cell, when both have a letter in them ("Revenue" and "2025" → "Revenue 2025")."""
    labels = {}
    header = rows[0] if rows else []
    for r, row in enumerate(rows[1:], 2):
        name = " ".join((row[0] if row else "").split())
        for c in range(2, len(row) + 1):
            head = " ".join((header[c - 1] if c - 1 < len(header) else "").split())
            if name and head and re.search(r"[A-Za-z]", name):
                labels[(r, c)] = f"{name} {head}"
    return labels


EMPTY_OK = {"sldNum", "dt", "ftr", "hdr"}


def read_pptx(path: Path, empty: list | None = None) -> list[tuple]:
    """→ [(text, where) or (text, where, label)] for every shape paragraph and table cell, slide by
    slide. `empty` (a list) receives (text, where, the number of segments before it) for every
    placeholder shape left without text."""
    segments = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        order = []
        if "ppt/presentation.xml" in names and "ppt/_rels/presentation.xml.rels" in names:
            targets = {_attr(r, "Id"): _attr(r, "Target") for r in _xml(archive, "ppt/_rels/presentation.xml.rels")}
            for slide in _xml(archive, "ppt/presentation.xml").iter():
                if _local(slide.tag) == "sldId":
                    target = targets.get(slide.attrib.get(f"{{{RELS}}}id"))
                    if target:
                        name = target.lstrip("/") if target.startswith("/") else "ppt/" + target
                        if name in names:
                            order.append(name)
        if not order:
            order = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        for number, name in enumerate(order, 1):
            root = _xml(archive, name)

            def shapes(parent):
                for shape in parent:
                    kind = _local(shape.tag)
                    if kind == "grpSp":
                        yield from shapes(shape)
                    elif kind in ("sp", "graphicFrame"):
                        yield shape

            tree = next((e for e in root.iter() if _local(e.tag) == "spTree"), None)
            for shape in shapes(tree) if tree is not None else []:
                props = next((e for e in shape.iter() if _local(e.tag) == "cNvPr"), None)
                label = (_attr(props, "name") if props is not None else None) or "shape"
                placeholder = next((e for e in shape.iter() if _local(e.tag) == "ph"), None)
                kind = _attr(placeholder, "type") if placeholder is not None else None
                if kind in ("sldNum", "dt", "ftr"):
                    continue
                if kind in ("title", "ctrTitle"):
                    label = "title"
                table = next((e for e in shape.iter() if _local(e.tag) == "tbl"), None)
                if table is not None:
                    rows = [["\n".join(_drawing_paragraphs(cell)) for cell in row if _local(cell.tag) == "tc"]
                            for row in table if _local(row.tag) == "tr"]
                    labels = _table_labels(rows)
                    for r, row in enumerate(rows, 1):
                        for c, text in enumerate(row, 1):
                            if text.strip():
                                segments.append((text, f"Slide {number}, table row {r}, column {c}",
                                                 labels.get((r, c))))
                    continue
                body = next((e for e in shape if _local(e.tag) == "txBody"), None)
                paragraphs = _drawing_paragraphs(body) if body is not None else []
                if (empty is not None and placeholder is not None and _local(shape.tag) == "sp"
                        and kind not in EMPTY_OK and not any(p.strip() for p in paragraphs)):
                    title = kind in ("title", "ctrTitle")
                    empty.append(("(empty title)" if title else f"(empty {kind or 'body'} placeholder)",
                                  f"Slide {number}, {'title' if title else (kind or 'body') + ' placeholder'}",
                                  len(segments)))
                if body is None:
                    continue                                            # a picture or a chart: not read
                for paragraph in paragraphs:
                    if paragraph.strip():
                        segments.append((paragraph, f"Slide {number}, {label}"))
    return segments


def _word_text(element) -> str:
    """A Word paragraph (or any part) → its text: superscript runs, field codes and page fields left out."""
    parts = []

    def walk(node, skip=False):
        kind = _local(node.tag)
        if kind == "fldSimple" and re.search(r"\b(?:PAGE|NUMPAGES|SECTIONPAGES)\b", _attr(node, "instr") or ""):
            return
        if kind == "r":
            props = _child(node, "rPr")
            align = _child(props, "vertAlign") if props is not None else None
            if align is not None and _attr(align, "val") == "superscript":
                return
        if kind == "instrText" or kind == "delText":
            return
        if kind == "t":
            parts.append(node.text or "")
        elif kind == "tab":
            parts.append(" ")
        elif kind in ("br", "cr"):
            parts.append("\n")
        elif kind == "p" and parts and parts[-1] != "\n":
            parts.append("\n")
        for child in node:
            walk(child)

    walk(element)
    return "".join(parts).strip("\n")


HEADING_NAME = re.compile(r"(?i)title|heading\s?\d|berschrift\s?\d|titre\s?\d")


def _heading_styles(archive: zipfile.ZipFile) -> set[str]:
    """The ids of the document's heading styles: named Title or Heading n (whatever the id), or
    with an outline level, or based on one of those."""
    if "word/styles.xml" not in archive.namelist():
        return set()
    based, headings = {}, set()
    for style in _xml(archive, "word/styles.xml"):
        if _local(style.tag) != "style":
            continue
        sid = _attr(style, "styleId") or ""
        name = _child(style, "name")
        props = _child(style, "pPr")
        parent = _child(style, "basedOn")
        if (name is not None and HEADING_NAME.fullmatch(_attr(name, "val") or "")) or \
                (props is not None and _child(props, "outlineLvl") is not None):
            headings.add(sid)
        elif parent is not None:
            based[sid] = _attr(parent, "val") or ""
    for sid, parent in based.items():
        seen = set()
        while parent and parent not in headings and parent not in seen:
            seen.add(parent)
            parent = based.get(parent, "")
        if parent in headings:
            headings.add(sid)
    return headings


def _heading(paragraph, styles: set[str]) -> bool:
    props = _child(paragraph, "pPr")
    if props is None:
        return False
    style = _child(props, "pStyle")
    name = (_attr(style, "val") or "") if style is not None else ""
    return bool(HEADING_NAME.fullmatch(name)) or name in styles or _child(props, "outlineLvl") is not None


def read_docx(path: Path) -> list[tuple]:
    segments, heading, paragraphs, tables = [], "", 0, 0
    with zipfile.ZipFile(path) as archive:
        root = _xml(archive, "word/document.xml")
        styles = _heading_styles(archive)
    body = next((e for e in root if _local(e.tag) == "body"), None)

    def under() -> str:
        return f' under "{heading[:60]}"' if heading else ""

    def blocks(parent):
        for node in parent:
            kind = _local(node.tag)
            if kind == "sdt":
                content = _child(node, "sdtContent")
                if content is not None:
                    yield from blocks(content)
            elif kind in ("p", "tbl"):
                yield node

    for node in blocks(body) if body is not None else []:
        if _local(node.tag) == "p":
            text = _word_text(node)
            if not text.strip():
                continue
            paragraphs += 1
            segments.append((text, f"Paragraph {paragraphs}{under()}"))
            if _heading(node, styles):
                heading = " ".join(text.split())
        else:
            tables += 1
            rows = [[_word_text(cell) for cell in row if _local(cell.tag) == "tc"]
                    for row in node if _local(row.tag) == "tr"]
            labels = _table_labels(rows)
            for r, row in enumerate(rows, 1):
                for c, text in enumerate(row, 1):
                    if text.strip():
                        segments.append((text, f"Table {tables}, row {r}, column {c}{under()}", labels.get((r, c))))
    return segments


def read_pdf(path: Path) -> list[tuple[str, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    segments = [((page.extract_text() or ""), f"Page {number}") for number, page in enumerate(reader.pages, 1)]
    if not any(re.sub(r"\s", "", text) for text, _where in segments):
        raise SourceError("This PDF has no text layer (it is probably scanned pictures), so no figure in it "
                          "could be read or checked.")
    return segments


def _sheet_ref(title: str, coordinate: str) -> str:
    return (title if re.fullmatch(r"[A-Za-z0-9_]+", title) else "'" + title.replace("'", "''") + "'") + "!" + coordinate


def read_xlsx(path: Path) -> tuple[list[tuple[str, str]], list[Figure], list[dict], int, int, list[str]]:
    """→ (text segments, typed-in figures, formula cells as derived items, numbers skipped, cells not
    read, the sheets' names in order)."""
    import datetime

    import openpyxl
    from openpyxl.utils import get_column_letter

    from .office import formula_text, shown

    formulas = openpyxl.load_workbook(path)
    values = openpyxl.load_workbook(path, data_only=True)
    segments, figures, derived, skipped, seen = [], [], [], 0, 0
    for sheet in formulas.worksheets:
        computed = values[sheet.title]._cells if sheet.title in values.sheetnames else {}
        cells = sorted(sheet._cells.items())
        labels: dict[int, list[tuple[int, str]]] = {}
        headings: dict[int, tuple[int, str]] = {}
        for (r, c), cell in cells:
            if isinstance(cell.value, str) and not cell.value.startswith("=") and cell.value.strip():
                labels.setdefault(r, []).append((c, cell.value.strip()))
                if c not in headings:
                    headings[c] = (r, cell.value.strip())

        def where(r: int, c: int) -> str:
            label = next((text for col, text in reversed(labels.get(r, [])) if col < c), "")
            head = headings.get(c)
            parts = [part[:40] for part in (label, head[1] if head and head[0] < r else "") if part]
            return _sheet_ref(sheet.title, f"{get_column_letter(c)}{r}") + (f" ({', '.join(parts)})" if parts else "")

        for (r, c), cell in cells:
            seen += 1
            if seen > MAX_CELLS:
                continue
            value = cell.value
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            formula = formula_text(value)
            if formula:
                held = computed.get((r, c))
                result = held.value if held is not None else None
                if isinstance(result, bool) or isinstance(result, (datetime.date, datetime.time, datetime.datetime)):
                    continue
                if isinstance(result, str):
                    continue                                            # text, or an error render_document reports
                text = shown(result, cell.number_format) if result is not None else formula
                derived.append({"text": text[:200], "where": where(r, c), "context": formula[:CONTEXT],
                                "note": formula[:200] + ("" if result is not None else
                                                         " (no stored result: run render_document to calculate it)")})
                continue
            if isinstance(value, bool) or isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
                skipped += 1
                continue
            if isinstance(value, (int, float)):
                text = shown(value, cell.number_format)
                found, dropped = figures_in(text, where(r, c), cell=True)
                skipped += dropped
                for figure in found[:1]:
                    exact = Decimal(str(value))
                    if not figure["percent"] and figure["value"] != exact and abs(figure["value"] - exact) > _quantum(figure):
                        figure.update(value=exact, decimals=max(0, -exact.as_tuple().exponent), scale=0)  # a scaled format
                    figure["context"] = text
                    figures.append(figure)
                continue
            segments.append((str(value), where(r, c)))
    return segments, figures, derived, skipped, max(0, seen - MAX_CELLS), list(formulas.sheetnames)


# -- the check ----------------------------------------------------------------------------------
def saved_name(relative: str) -> str:
    return "checks/" + re.sub(r"[^A-Za-z0-9._-]+", "-", relative) + ".json"


def _agree(a: Figure, b: Figure) -> bool:
    """Two figures are one value when either is the other shown at its precision."""
    return abs(abs(a["value"]) - abs(b["value"])) <= max(_quantum(a), _quantum(b)) / 2


def conflicts_in(figures: list[Figure]) -> list[dict]:
    """Figures that share a label (CONTRACT.md, "Very large documents") but not a value →
    [{label, figures: [{text, where}]}], in the order the labels first appear."""
    groups: dict[tuple, list[Figure]] = {}
    for figure in figures:
        key = label_key(figure.get("label"))
        if key:
            groups.setdefault((key, bool(figure["percent"])), []).append(figure)
    out = []
    for group in groups.values():
        distinct: list[Figure] = []
        for figure in group:
            if not any(_agree(figure, other) for other in distinct):
                distinct.append(figure)
        # Several figures in one place are one statement (a range, "70% to 158%"), not a disagreement.
        if len({figure["where"] for figure in distinct}) > 1:
            out.append({"label": " ".join(str(group[0]["label"]).split())[:80],
                        "figures": [{"text": f["text"], "where": f["where"]} for f in distinct[:CONFLICT_FIGURES]]})
    return out


def part_of(where: str, kind: str) -> str:
    """The slide, page, sheet or heading a place is in: "Slide 41", "Page 3", "Summary", "Results"."""
    if kind in ("presentation", "pdf"):
        return where.split(",")[0]
    if kind == "workbook":
        sheet = where.split("!")[0]
        return sheet[1:-1].replace("''", "'") if sheet.startswith("'") and sheet.endswith("'") else sheet
    match = re.search(r' under "(.*)"$', where)
    return match.group(1) if match else "(before the first heading)"


def _by_part(kind: str, items: list[dict], placeholders: list[dict], conflicts: list[dict],
             rank: dict[str, int]) -> list[dict]:
    """Every part with a problem, in the file's order (`rank`: a part → its place in the file)."""
    parts: dict[str, dict] = {}

    def count(where: str, what: str) -> None:
        name = part_of(where, kind)
        parts.setdefault(name, {"part": name, "not_found": 0, "placeholders": 0, "conflicts": 0})[what] += 1

    for item in items:
        if item["status"] == "not_found":
            count(item["where"], "not_found")
    for item in placeholders:
        count(item["where"], "placeholders")
    for conflict in conflicts:
        for figure in conflict["figures"]:
            count(figure["where"], "conflicts")
    out = list(parts.values())
    if kind in ("presentation", "pdf"):
        out.sort(key=lambda p: int(re.search(r"\d+", p["part"]).group(0)) if re.search(r"\d+", p["part"]) else 0)
    else:
        out.sort(key=lambda p: rank.get(p["part"], len(rank)))
    return out


def check_file(workspace: Path, relative: str, save: bool = True) -> dict:
    """The file check (CONTRACT.md). With `save` the result is written to checks/<file>.json."""
    root = workspace.resolve()
    target = (workspace / str(relative or "")).resolve()
    if root not in target.parents or not target.is_file():
        raise SourceError(f"There is no file at {relative} in this project.")
    kind = KINDS.get(target.suffix.lower())
    if kind is None:
        raise SourceError("check_file reads .pptx, .docx, .xlsx and .pdf files; for a page use check_page.")
    relative = target.relative_to(root).as_posix()
    figures: list[Figure] = []
    formula_items: list[dict] = []
    placeholders: list[dict] = []
    rank: dict[str, int] = {}
    skipped, error, not_read = 0, None, None
    try:
        empty: list = []
        if kind == "workbook":
            segments, figures, formula_items, skipped, unread, sheets = read_xlsx(target)
            rank = {name: index for index, name in enumerate(sheets)}
            if unread:
                not_read = (f"Only the first {MAX_CELLS:,} cells were read; {unread:,} more were not checked.")
        elif kind == "presentation":
            segments = read_pptx(target, empty)
        else:
            segments = {"document": read_docx, "pdf": read_pdf}[kind](target)
        ordered: list[tuple[float, dict]] = [(at - 0.5, {"text": text, "where": where, "context": ""})
                                             for text, where, at in empty]   # an empty shape, before the next text
        for index, segment in enumerate(segments):
            text, where = segment[0], segment[1]
            if kind == "document":
                rank.setdefault(part_of(where, kind), len(rank))
            found, dropped = figures_in(text, where, pdf=kind == "pdf", label=segment[2] if len(segment) > 2 else None)
            figures.extend(found)
            skipped += dropped
            ordered.extend((index, item) for item in placeholders_in(text, where))
        placeholders = [item for _at, item in sorted(ordered, key=lambda pair: pair[0])]
    except SourceError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - a broken file is said, never a pass
        error = f"The file could not be read ({exc.__class__.__name__}: {' '.join(str(exc).split())[:200]})."
    if error is None:
        pool, derived_pool, read = vouched(workspace)
    else:
        figures, formula_items, placeholders = [], [], []
        pool, derived_pool, read = Pool(), Pool(), {"pages": [], "sources": 0, "data_files": [], "ledger": None,
                                                     "ledger_notes": {}}
    ledger, notes = read.pop("ledger"), read.pop("ledger_notes")
    items = []
    for item in formula_items:
        items.append({"text": item["text"], "value": None, "status": "derived", "where": item["where"],
                      "context": item["context"], "match": None, "via": None, "ref": None, "rounded_from": None,
                      "note": item["note"]})
    for figure in figures:
        verdict = judge(figure, pool, derived_pool)
        if verdict["via"] == "ledger" and notes.get(verdict["ref"]):
            verdict["note"] = notes[verdict["ref"]]
        items.append({"text": figure["text"], "value": _plain(figure["value"]), "where": figure["where"],
                      "context": figure["context"], **verdict})
    for n, item in enumerate(items, 1):
        item["n"] = n
    conflicts = conflicts_in(figures) if kind != "workbook" else []
    count = lambda status: sum(item["status"] == status for item in items)  # noqa: E731
    kept = items[:MAX_ITEMS] + [item for item in items[MAX_ITEMS:] if item["status"] == "not_found"]
    result = {
        "file": relative, "kind": kind, "checked_at": now_iso(), "error": error, "not_read": not_read,
        "summary": {"figures": len(items), "found": count("found"),
                    "rounded": sum(item["status"] == "found" and item["match"] == "rounded" for item in items),
                    "derived": count("derived"), "not_found": count("not_found"), "skipped": skipped,
                    "ledger": sum(item["via"] == "ledger" for item in items),
                    "placeholders": len(placeholders), "conflicts": len(conflicts)},
        "vouched": read,
        "ledger": ledger,
        "items": [{"n": item["n"], **{k: v for k, v in item.items() if k != "n"}} for item in kept],
        "items_cut": len(items) - len(kept),
        "placeholders": placeholders[:MAX_PLACEHOLDERS],
        "conflicts": conflicts[:MAX_CONFLICTS],
        "by_part": _by_part(kind, items, placeholders, conflicts, rank),
    }
    if save:
        out = workspace / saved_name(relative)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out, result)
        result["saved"] = saved_name(relative)
    return result


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def compact(result: dict) -> dict:
    """The tool's answer: the not-found figures first, then derived and rounded ones, the
    placeholders, conflicts and ledger problems, where they are, and what to do."""
    def brief(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in ("n", "value") and v is not None}

    items, summary = result["items"], result["summary"]
    missing = [brief(i) for i in items if i["status"] == "not_found"]
    derived = [brief(i) for i in items if i["status"] == "derived"]
    rounded = [brief(i) for i in items if i["status"] == "found" and i["match"] == "rounded"]
    placeholders = result.get("placeholders") or []
    conflicts = result.get("conflicts") or []
    ledger = result.get("ledger")
    by_part = result.get("by_part") or []
    total_placeholders = summary.get("placeholders", len(placeholders))
    total_conflicts = summary.get("conflicts", len(conflicts))
    ledger_problems = ledger["problems_total"] if ledger else 0
    lists_cut = (summary["not_found"] > ANSWER["not_found"] or total_placeholders > ANSWER["placeholders"]
                 or total_conflicts > ANSWER["conflicts"] or ledger_problems > ANSWER["ledger_problems"]
                 or len(by_part) > ANSWER["by_part"])
    if result["error"]:
        nexts = result["error"] + " Nothing in this file was checked: say so in your report."
    else:
        said = []
        if result.get("not_read"):
            said.append(result["not_read"] + " Say so in your report.")
        if missing:
            said.append(f"{len(missing)} figure{'s are' if len(missing) != 1 else ' is'} in no checked page figure, "
                        "data file or saved source (not_found). Correct each in the file's build script from its "
                        "source or data (or fetch the source it came from), build and render again, and run "
                        "check_file again. A figure you calculated stays not_found here: recompute it from vouched "
                        "figures and say so in your report.")
        elif not items:
            said.append("No figures to check in this file.")
        elif not result.get("not_read"):
            said.append("Every figure is found or derived. Derived ones (formulas, figures a page marks as derived) "
                        "are not recomputed by this check: recompute them when the request asks for verification.")
        if total_placeholders:
            said.append(f"{_plural(total_placeholders, 'leftover placeholder text', 'leftover placeholder texts')} "
                        "(placeholders): replace or remove each in the build script, then build and check again.")
        if total_conflicts:
            said.append(f"{_plural(total_conflicts, 'label carries', 'labels carry')} two different figures in this "
                        "file (conflicts): make them agree, from the ledger when there is one, or relabel them if "
                        "they truly measure different things.")
        if ledger_problems:
            said.append(f"The ledger {LEDGER} has {_plural(ledger_problems, 'problem', 'problems')} "
                        "(ledger.problems): a fact with a problem vouches for nothing until it is fixed.")
        if ledger and ledger.get("error"):
            said.append(ledger["error"])
        if lists_cut:
            said.append(f"The lists in this answer are cut; the full lists are in {result.get('saved') or 'the saved result'}"
                        " (by_part says where each problem is).")
        nexts = " ".join(said)
    answer_ledger = None
    if ledger:
        answer_ledger = {**ledger, "problems": ledger["problems"][:ANSWER["ledger_problems"]]}
    return {"file": result["file"], "kind": result["kind"], "summary": summary, "error": result["error"],
            "not_read": result.get("not_read"), "not_found": missing[:ANSWER["not_found"]],
            "derived": derived[:ANSWER["derived"]], "rounded": rounded[:ANSWER["rounded"]],
            "placeholders": placeholders[:ANSWER["placeholders"]], "conflicts": conflicts[:ANSWER["conflicts"]],
            "ledger": answer_ledger, "by_part": by_part[:ANSWER["by_part"]], "lists_cut": lists_cut,
            "saved": result.get("saved"), "next": nexts}


# -- files a page shows -------------------------------------------------------------------------
class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src", "data") and value:
                self.found.append(value)


def linked_files(workspace: Path, page: str = "index.html") -> list[str]:
    """The .pptx, .docx, .xlsx and .pdf files of the project that a page links to or frames (a framed
    preview, files/deck.pptx.preview/index.html, counts as its file), in the order they appear."""
    root = workspace.resolve()
    source = (workspace / page)
    if not source.is_file():
        return []
    parser = _Links()
    try:
        parser.feed(source.read_text("utf-8", "replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page still yields the links read so far
        pass
    out: list[str] = []
    base = (workspace / page).parent
    for link in parser.found:
        parts = urlsplit(link.strip())
        if parts.scheme or parts.netloc or not parts.path:
            continue
        path = unquote(parts.path)
        preview = re.match(r"(.+\.(?:pptx|docx|xlsx|pdf))\.preview(?:/.*)?$", path, re.I)
        if preview:
            path = preview.group(1)
        if Path(path).suffix.lower() not in KINDS:
            continue
        target = (root / path.lstrip("/")) if path.startswith("/") else (base / path)
        target = target.resolve()
        if root not in target.parents or not target.is_file():
            continue
        relative = target.relative_to(root).as_posix()
        if relative not in out:
            out.append(relative)
    return out
