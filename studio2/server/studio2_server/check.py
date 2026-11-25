"""The page check: every marked figure on a page is compared with its saved source.

CONTRACT.md, "The page check". Three marks: a quote from a saved source, a value computed
from project data (re-run here), and a figure the author derived (sources must exist; the
arithmetic is not checked). A quote match says the words are in the saved source. It does
not say the source is right.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

from .config import now_iso
from .sources import VOID, SourceError, Sources
from .store import write_json_atomic

SKIP_TAGS = {"script", "style", "code", "pre", "svg", "template", "noscript", "head", "canvas", "textarea"}
BLOCK_TAGS = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "article", "figcaption",
              "blockquote", "dt", "dd", "caption", "summary", "tr", "br", "header", "footer", "main", "aside"}
HEADINGS = {"h1", "h2", "h3", "h4"}
NUMBER = re.compile(r"(?<![\w.\[#/-])[-+−]?[$€£₹¥]?\d(?:[\d,]*\d)?(?:\.\d+)?\s?%?")
WORD_SCALES = {"thousand": 3, "million": 6, "mn": 6, "billion": 9, "bn": 9, "trillion": 12, "tn": 12}
LETTER_SCALES = {"k": 3, "m": 6, "b": 9}   # only when attached to the number: 4.2B, 300k


def scale_after(rest: str) -> int:
    """The power of ten named right after a number: " million" → 6, "bn" → 9, "B" → 9."""
    word = re.match(r"\s?([A-Za-z]+)", rest)
    if not word:
        return 0
    name = word.group(1).lower()
    if name in WORD_SCALES:
        return WORD_SCALES[name]
    return LETTER_SCALES.get(name, 0) if not rest[:1].isspace() else 0
# A month is a whole word, so "Margin 12" and "Marketing 2025" are not dates.
MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b")
DAY = r"\d{1,2}(?!\d)(?:st|nd|rd|th)?"
DAYS = rf"{DAY}(?:\s?[–-]\s?{DAY})?"            # a day, or a run of days: 11–18 November
DATE = re.compile(rf"\b{DAYS}\s+{MONTH}\b\.?(?:,?\s+\d{{4}})?|\b{MONTH}\.?\s+{DAYS}"
                  rf"(?:,?\s+\d{{4}})?|\b{MONTH}\.?\s+\d{{4}}\b|\b\d{{4}}-\d{{2}}(?:-\d{{2}})?\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b"
                  r"|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b(?:19|20)\d{2}\s?[–-]\s?(?:(?:19|20)?\d{2})\b|\b(?:FY|CY)\s?\d{2,4}\b", re.I)


def normalise(text: str) -> str:
    text = (text or "").replace("­", "").replace(" ", " ")
    for old, new in (("“", '"'), ("”", '"'), ("„", '"'), ("‘", "'"), ("’", "'"), ("–", "-"), ("—", "-"),
                     ("−", "-"), ("…", "...")):
        text = text.replace(old, new)
    return " ".join(text.lower().split())


def squeeze(text: str) -> str:
    """Tolerates line-break hyphenation and spacing differences in PDF text."""
    return re.sub(r"[\s\-]+", "", normalise(text))


def numbers_in(text: str, keep_years: bool = True, scaled: bool = False) -> list[tuple]:
    """→ [(as written, value, decimals shown)] for each number in the text; with `scaled`, a
    fourth item: the power of ten of the unit word that follows (million → 6)."""
    found = []
    cleaned = DATE.sub(" ", text) if not keep_years else text
    for match in NUMBER.finditer(cleaned):
        raw = match.group(0).strip()
        digits = re.sub(r"[^\d.]", "", raw).strip(".")
        if not digits:
            continue
        try:
            value = Decimal(digits)
        except InvalidOperation:
            continue
        if raw.lstrip("$€£₹¥").startswith(("-", "−")):
            value = -value
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        end = match.end() - (len(match.group(0)) - len(match.group(0).rstrip()))
        found.append((raw, value, decimals, scale_after(cleaned[end:])) if scaled else (raw, value, decimals))
    return found


def _is_year(raw: str, value: Decimal) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}", raw)) and 1900 <= value <= 2100


def _matches(shown: Decimal, decimals: int, candidates: list[Decimal], scale: int = 0,
             scales: list[int] | None = None) -> tuple[bool, Decimal | None]:
    """Is `shown` one of the candidates, exactly or as that candidate rounded to the precision
    shown? With scales, "1.33 billion" matches "1,330.4 million"."""
    quantum = Decimal(1).scaleb(-decimals)
    for candidate in candidates:
        if candidate == shown:
            return True, None
    for index, candidate in enumerate(candidates):
        same_unit = [candidate]
        if scales is not None and scales[index] != scale:
            same_unit.append(candidate.scaleb(scales[index] - scale))
        for value in same_unit:
            try:
                if abs(value).quantize(quantum) == abs(shown).quantize(quantum):
                    return True, candidate
            except InvalidOperation:
                continue
    return False, None


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.marks: list[dict] = []
        self.blocks: list[dict] = []
        self.listed: set[str] = set()      # ids with an entry in the page's source list (id="src-<id>")
        self.heading = ""
        self._buffer: list[str] = []
        self._heading_text: list[str] | None = None
        self._mark: dict | None = None
        self._skip = 0

    def _flush(self):
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        if text:
            self.blocks.append({"text": text, "where": self.heading})

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (attrs.get("id") or "").startswith("src-") and len(attrs["id"]) > 4:
            self.listed.add(attrs["id"][4:])
        skip = (tag in SKIP_TAGS or "data-nocheck" in attrs or attrs.get("id") == "checked-numbers"
                or "hidden" in attrs)
        entry = {"tag": tag, "skip": skip, "mark": False}
        if tag in BLOCK_TAGS and not self._mark:
            self._flush()
        if tag in HEADINGS and not self._skip:
            self._heading_text = []
        if not self._skip and not skip and self._mark is None and attrs.get("data-src"):
            self._mark = {"sources": attrs["data-src"].split(), "quote": attrs.get("data-quote"),
                          "sql": attrs.get("data-sql"), "derived": attrs.get("data-derived"),
                          "parts": [], "where": self.heading}
            entry["mark"] = True
        if tag in VOID:
            return
        self._skip += skip
        self.stack.append(entry)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            entry = self.stack.pop()
            self._skip -= entry["skip"]
            if entry["mark"] and self._mark is not None:
                mark, self._mark = self._mark, None
                mark["text"] = " ".join("".join(mark.pop("parts")).split())
                self.marks.append(mark)
                self._buffer.append(" ⁣ ")  # keeps the words either side of a mark apart
            if entry["tag"] == tag:
                break
        if tag in HEADINGS and self._heading_text is not None:
            self.heading = " ".join("".join(self._heading_text).split())[:120]
            self._heading_text = None
        if tag in BLOCK_TAGS and not self._mark:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._heading_text is not None:
            self._heading_text.append(data)
        if self._mark is not None:
            self._mark["parts"].append(data)
        else:
            self._buffer.append(data)


def parse_page(markup: str) -> tuple[list[dict], list[dict]]:
    marks, blocks, _listed = parse_page_full(markup)
    return marks, blocks


def parse_page_full(markup: str) -> tuple[list[dict], list[dict], set[str]]:
    """→ (marks, text blocks, the ids that have an entry in the page's source list)."""
    parser = _Page()
    parser.feed(markup)
    parser.close()
    parser._flush()
    return parser.marks, parser.blocks, parser.listed


def not_listed(marks: list[dict], listed: set[str], known: dict, workspace: Path) -> list[str]:
    """Source ids cited by a mark that have no entry in the page's source list, in the order they
    are first cited; [] when the page has no source list at all. Project files are not sources."""
    if not listed:
        return []
    missing: list[str] = []
    for mark in marks:
        for name in mark["sources"]:
            is_source = name in known or (re.fullmatch(r"s\d+", name) and not (workspace / name).is_file())
            if is_source and name not in listed and name not in missing:
                missing.append(name)
    return missing


def unmarked_numbers(blocks: list[dict]) -> list[dict]:
    out = []
    for block in blocks:
        text = DATE.sub(" ", block["text"])
        text = re.sub(r"^\s*(?:\d{1,2}[.)]|step \d+|figure \d+|table \d+|chart \d+)\s*", " ", text, flags=re.I)
        found = []
        for raw, value, decimals in numbers_in(text):
            bare = re.fullmatch(r"\d+", raw) is not None
            if _is_year(raw, value) or (bare and abs(value) < 13):
                continue
            found.append(raw)
        if found:
            out.append({"text": block["text"][:260], "numbers": found[:12], "where": block["where"]})
    return out


def _statement(sql: str) -> str:
    from .datasources import DataError, split_statement
    try:
        return split_statement(sql)[0]
    except DataError as exc:
        raise SourceError(exc.message) from None


def run_sql(workspace: Path, sql: str, limit: int = 1000, timeout: int | None = None) -> tuple[list[str], list[tuple]]:
    """One read-only DuckDB statement over the project's files, in a worker that can open nothing
    outside the project (filesql.py); relative file names resolve inside the project."""
    from . import filesql
    statement = _statement(sql)
    try:
        answer = filesql.run(workspace, [{"sql": statement, "limit": limit}], filesql.scratch_root(),
                             timeout or filesql.DEFAULT_TIMEOUT)[0]
    except filesql.QueryError as exc:
        raise SourceError(exc.message) from None
    if "error" in answer:
        raise SourceError(answer["error"])
    return answer["columns"], [tuple(row) for row in answer["rows"]]


def run_marks(workspace: Path, statements: list[str]) -> list[tuple[list[str], list[tuple]] | SourceError]:
    """Every data-sql of one page in one worker (the page check): → per statement its (columns,
    rows) or the SourceError that says why it failed."""
    from . import filesql
    out: list = [None] * len(statements)
    parts, where = [], []
    for index, sql in enumerate(statements):
        try:
            parts.append({"sql": _statement(sql), "limit": 2})
            where.append(index)
        except SourceError as exc:
            out[index] = exc
    if parts:
        try:
            answers = filesql.run(workspace, parts, filesql.scratch_root(), filesql.DEFAULT_TIMEOUT)
        except filesql.QueryError as exc:
            answers = [{"error": exc.message}] * len(parts)
        for index, answer in zip(where, answers):
            out[index] = SourceError(answer["error"]) if "error" in answer else (
                answer["columns"], [tuple(row) for row in answer["rows"]])
    return out


def _shown(text: str) -> tuple[Decimal, int, int, bool] | None:
    """The first number in a marked element → (value, decimals, power of ten of its unit, is a percentage)."""
    for raw, value, decimals, scale in numbers_in(text, scaled=True):
        return value, decimals, scale, raw.rstrip().endswith("%")
    return None


def _check_quote(mark: dict, sources: Sources) -> dict:
    source_id = mark["sources"][0]
    text = sources.text(source_id)  # raises SourceError when the source is unknown
    quote = mark["quote"] or ""
    if len(normalise(quote)) < 8:
        return {"status": "failed", "reason": "The data-quote is too short to identify a passage (use at least a few words)."}
    if normalise(quote) not in normalise(text) and squeeze(quote) not in squeeze(text):
        return {"status": "failed", "reason": f"The quote is not in the saved text of {source_id}."}
    quoted = numbers_in(quote, scaled=True)
    in_quote, quote_scales = [q[1] for q in quoted], [q[3] for q in quoted]
    notes = []
    for raw, value, decimals, scale in numbers_in(mark["text"], scaled=True):
        if _is_year(raw, value) and value not in in_quote:
            continue
        ok, rounded_from = _matches(value, decimals, in_quote, scale, quote_scales)
        if not ok:
            seen = ", ".join(str(v) for v in in_quote[:6]) or "no numbers"
            return {"status": "failed", "found": seen,
                    "reason": f"{raw} is shown, but the quote has {seen}. Show the figure as the source "
                              "states it, or mark it data-derived and say how it was calculated."}
        if rounded_from is not None:
            notes.append(f"{raw} is {rounded_from} rounded")
    return {"status": "verified", "reason": "; ".join(notes) or None}


def _check_sql(mark: dict, workspace: Path, ran=None) -> dict:
    for name in mark["sources"]:
        if not re.fullmatch(r"s\d+", name) and not (workspace / name).is_file():
            return {"status": "failed", "reason": f"There is no file {name} in the project."}
    if ran is None:
        ran = run_marks(workspace, [mark["sql"]])[0]
    if isinstance(ran, SourceError):
        raise ran
    _columns, rows = ran
    if len(rows) != 1 or len(rows[0]) != 1:
        return {"status": "failed", "reason": "data-sql must return exactly one value (one row, one column)."}
    value = rows[0][0]
    shown = _shown(mark["text"])
    if shown is None:
        same = normalise(str(value)) == normalise(mark["text"])
        return {"status": "verified" if same else "failed", "found": str(value),
                "reason": None if same else f"The query returns {value}."}
    try:
        actual = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return {"status": "failed", "found": str(value), "reason": f"The query returns {value!r}, which is not a number."}
    number, decimals, scale, percent = shown
    candidates = [actual, actual.scaleb(-scale)]
    if percent:
        candidates.append(actual * 100)
    ok, _ = _matches(number, decimals, candidates)
    return {"status": "verified" if ok else "failed", "found": str(actual),
            "reason": None if ok else f"The query returns {actual}, but the page shows {mark['text']}."}


def check_page(workspace: Path, page: str = "index.html", save: bool = True) -> dict:
    """The page check (CONTRACT.md). With `save` (the default) the result is also written to
    checks/<page>.json; the studio's own re-check after a verification leaves that file alone."""
    target = (workspace / page).resolve()
    if workspace.resolve() not in target.parents or not target.is_file():
        raise SourceError(f"There is no page at {page} in this project.")
    marks, blocks, listed = parse_page_full(target.read_text("utf-8", "replace"))
    sources = Sources(workspace)
    known = {item["id"]: item for item in sources.list()}
    cited: dict[str, int] = {}
    items = []
    with_sql = [mark for mark in marks if mark["sql"]]
    ran = dict(zip(map(id, with_sql), run_marks(workspace, [mark["sql"] for mark in with_sql]))) if with_sql else {}
    for number, mark in enumerate(marks, 1):
        kind = "data" if mark["sql"] else "quote" if mark["quote"] else "derived"
        try:
            if kind == "data":
                outcome = _check_sql(mark, workspace, ran[id(mark)])
            elif kind == "quote":
                outcome = _check_quote(mark, sources)
            else:
                missing = [s for s in mark["sources"] if s not in known and not (workspace / s).is_file()]
                if missing:
                    outcome = {"status": "failed", "reason": f"Unknown source: {', '.join(missing)}."}
                elif not (mark["derived"] or "").strip():
                    outcome = {"status": "failed", "reason": "A mark needs data-quote, data-sql or data-derived "
                                                             "(how the figure was calculated)."}
                else:
                    outcome = {"status": "derived", "reason": mark["derived"].strip()[:300]}
        except SourceError as exc:
            outcome = {"status": "failed", "reason": str(exc)}
        for source_id in mark["sources"]:
            if source_id in known:
                cited[source_id] = cited.get(source_id, 0) + 1
        items.append({"n": number, "text": mark["text"][:200], "status": outcome["status"], "kind": kind,
                      "sources": mark["sources"], "quote": (mark["quote"] or mark["sql"] or "")[:500] or None,
                      "reason": outcome.get("reason"), "found": outcome.get("found"), "where": mark["where"]})
    unmarked = unmarked_numbers(blocks)
    missing = not_listed(marks, listed, known, workspace)
    count = lambda status: sum(item["status"] == status for item in items)  # noqa: E731
    result = {
        "page": page, "checked_at": now_iso(),
        "summary": {"verified": count("verified"), "derived": count("derived"), "failed": count("failed"),
                    "unmarked_numbers": sum(len(u["numbers"]) for u in unmarked),
                    "sources_cited": len(cited), "sources_unused": len(known) - len(cited),
                    "cited_not_listed": len(missing)},
        "source_list": bool(listed), "not_listed": missing,
        "items": items, "unmarked": unmarked[:80],
        "sources": [{"id": item["id"], "title": item.get("title"), "url": item.get("url"),
                     "fetched_at": item.get("fetched_at"), "cited": cited.get(item["id"], 0)}
                    for item in known.values()],
    }
    if not save:
        return result
    out = workspace / "checks" / (re.sub(r"[^A-Za-z0-9._-]+", "-", page) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, result)
    result["saved"] = out.relative_to(workspace).as_posix()
    return result
