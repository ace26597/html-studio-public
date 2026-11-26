"""Find words in a project's pages and across projects (CONTRACT.md, "Find words in a project's
pages and across projects").

Plain matching, no index: a first scan of the owner's whole studio takes about a second, later ones
a tenth of that, from a small cache of each file's folded text. Pure functions; the routes in api.py run them in a worker thread with a time budget.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import keyfiles
from .watcher import BUILD_DIRS
from .workspace import IGNORED_DIRS

MIN_QUERY = 2
FILE_LIMIT = 2_000_000          # bytes; a larger file is not read
MAX_FILES = 2000                # files looked at per project
MAX_ENTRIES = 20_000            # names of any kind walked per project
SKIP_DIRS = {"venv", "site-packages"}   # besides the folders the studio never watches
MAX_QUERY = 500                 # characters of a query that are read
MAX_PAGES = 200                 # pages listed per project
HITS_PER_PAGE = 20
SNIPPET = 160
PAGE_SUFFIXES = (".html", ".htm", ".md")
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
SECTION_TAGS = {"h1", "h2", "h3", "h4"}
HEADING_TAGS = SECTION_TAGS | {"h5", "h6"}
# Tags that end one run of text and start another. Anything else (a, span, b, code ...) is inline.
BLOCK_TAGS = HEADING_TAGS | {
    "p", "li", "td", "th", "dt", "dd", "div", "section", "article", "aside", "header", "footer",
    "main", "nav", "blockquote", "pre", "figure", "figcaption", "caption", "table", "tr", "thead",
    "tbody", "tfoot", "ul", "ol", "dl", "form", "fieldset", "legend", "label", "button", "option",
    "select", "textarea", "details", "summary", "hr", "br", "body", "html", "head", "title", "address",
}
QUOTES = str.maketrans({"“": '"', "”": '"'})
WHITE = re.compile(r"\s+")

# Rank of a hit: lower first.
HEADING, PHRASE, OTHER = 0, 1, 2


# -- matching -------------------------------------------------------------------------------------
_MARKS: dict[int, None] | None = None


def _marks() -> dict[int, None]:
    """Every nonspacing mark (Unicode category Mn, the browser's `\\p{Mn}`: the page and the home
    page's box drop the same ones), for str.translate to drop. Built once, on first use."""
    global _MARKS
    if _MARKS is None:
        _MARKS = {cp: None for cp in range(0x20000) if unicodedata.category(chr(cp)) == "Mn"}
    return _MARKS


def _fold_char(char: str) -> str:
    if char.isascii():
        return char.lower()
    return unicodedata.normalize("NFKD", char).translate(_marks()).casefold()


def fold(text: str) -> str:
    """Accents dropped, case ignored, white space collapsed and trimmed."""
    if not text:
        return ""
    if text.isascii():
        folded = text.lower()       # casefold and lower agree on ASCII
    else:
        folded = unicodedata.normalize("NFKD", text).translate(_marks()).casefold()
    return WHITE.sub(" ", folded).strip()


@dataclass(frozen=True)
class Query:
    raw: str
    terms: tuple[str, ...]      # folded words and phrases, each must be a substring
    whole: str                  # the folded query without quotes, for the exact-phrase rank


def parse_query(raw: str | None) -> Query | None:
    """→ None when there is nothing to find (empty, or under two characters)."""
    text = (raw or "")[:MAX_QUERY].translate(QUOTES)
    terms: list[str] = []
    rest: list[str] = []
    parts = text.split('"')
    for index, part in enumerate(parts):
        if index % 2:       # inside quotes (an unclosed quote runs to the end)
            phrase = fold(part)
            if phrase:
                terms.append(phrase)
        else:
            rest.append(part)
    for word in fold(" ".join(rest)).split(" "):
        if word:
            terms.append(word)
    whole = fold(text.replace('"', " "))
    if len(whole) < MIN_QUERY or not terms:
        return None
    unique = tuple(dict.fromkeys(terms))
    return Query(raw=raw or "", terms=unique, whole=whole)


def matches(query: Query, folded: str) -> bool:
    return all(term in folded for term in query.terms)


def snippet(text: str, query: Query, size: int = SNIPPET) -> str:
    """A short piece of `text` around the first match, cut at word edges, `…` where it was cut."""
    display = WHITE.sub(" ", text).strip()
    if len(display) <= size:
        return display
    if display.isascii():
        flat = display.lower()          # one folded character per character: no map needed
        found = [at for at in (flat.find(term) for term in query.terms) if at >= 0]
        hit = min(found) if found else 0
    else:
        # Fold char by char, remembering where each folded char came from.
        folded, origin = [], []
        for index, char in enumerate(display):
            for out in _fold_char(char):
                folded.append(out)
                origin.append(index)
        flat = "".join(folded)
        found = [at for at in (flat.find(term) for term in query.terms) if at >= 0]
        hit = origin[min(found)] if found else 0
    start = max(0, hit - size // 3)
    end = min(len(display), start + size)
    start = max(0, end - size)
    if start > 0:
        space = display.find(" ", start)
        if 0 <= space < hit:
            start = space + 1
    if end < len(display):
        space = display.rfind(" ", start, end)
        if space > hit:
            end = space
    return ("…" if start > 0 else "") + display[start:end].strip() + ("…" if end < len(display) else "")


# -- text from files ------------------------------------------------------------------------------
@dataclass
class Block:
    kind: str               # "heading" (h1-h6) or "text"
    text: str
    section: str | None     # nearest h1-h4 above
    level: int = 0          # 1-6 for a heading
    folded: str = ""        # set by `prepare`


@dataclass
class Page:
    title: str | None = None
    blocks: list[Block] = field(default_factory=list)
    folded_title: str = ""  # these two are set by `prepare`
    folded_all: str = ""    # every block's folded text, one per line: a term not in it is in no block

    def prepare(self) -> "Page":
        for block in self.blocks:
            block.folded = fold(block.text)
        self.folded_title = fold(self.title or "")
        self.folded_all = "\n".join([self.folded_title, *(block.folded for block in self.blocks)])
        return self

    def may_hold(self, query: "Query") -> bool:
        return all(term in self.folded_all for term in query.terms)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self.skip = 0
        self.buffer: list[str] = []
        self.heading: int = 0          # level of the heading being read, 0 outside one
        self.section: str | None = None
        self.in_title = False
        self.title_text: list[str] = []

    def _flush(self) -> None:
        text = WHITE.sub(" ", "".join(self.buffer)).strip()
        self.buffer = []
        if not text:
            return
        if self.heading:
            self.page.blocks.append(Block("heading", text, None, self.heading))
            if self.heading <= 4:
                self.section = text
        else:
            self.page.blocks.append(Block("text", text, self.section))

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self.heading = int(tag[1])

    def handle_startendtag(self, tag, attrs):
        if tag in SKIP_TAGS:        # <svg/>: nothing to skip
            return
        if not self.skip and tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = False
            return
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self.heading = 0

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title_text.append(data)
        else:
            self.buffer.append(data)

    def finish(self) -> Page:
        self.close()
        self._flush()
        title = WHITE.sub(" ", "".join(self.title_text)).strip()
        self.page.title = title or None
        return self.page


def html_page(text: str) -> Page:
    parser = _PageParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - html.parser rarely raises; keep what was read
        pass
    page = parser.finish()
    if not page.title:
        page.title = next((b.text for b in page.blocks if b.kind == "heading"), None)
    return page


# These run on files an agent or a guest wrote, inside a thread that holds the interpreter for the
# whole match: a pattern that can backtrack is a way to stop the studio. So each one is anchored
# and its classes cannot overlap what follows them; the closing hashes of a heading are taken off
# with string methods, and a link's text and target have a length the search never goes past.
MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")
MD_ITEM = re.compile(r"^[ \t]{0,12}(?:[-*+]|\d{1,9}[.)])[ \t]+(.*)$")
MD_LINK = re.compile(r"!?\[([^\[\]\n]{0,300})\]\([^()\n]{0,2000}\)")
MD_LINE = 20_000                # characters of one line that are read; the rest is left
MD_FENCE = re.compile(r"^[ \t]{0,12}(```|~~~)")


def _md_title(words: str) -> str:
    """A heading's words without closing hashes (`## Costs ##`): hashes at the end count as closing
    only after a space, so `# C#` keeps its name."""
    bare = words.rstrip("#")
    return bare.rstrip() if bare != words and (not bare or bare[-1] in " \t") else words


def _md_clean(text: str) -> str:
    text = MD_LINK.sub(r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "")


def md_page(text: str) -> Page:
    page = Page()
    section: str | None = None
    paragraph: list[str] = []

    def flush():
        if paragraph:
            joined = WHITE.sub(" ", _md_clean(" ".join(paragraph))).strip()
            if joined:
                page.blocks.append(Block("text", joined, section))
            paragraph.clear()

    for line in text.splitlines():
        line = line[:MD_LINE].rstrip()
        if MD_FENCE.match(line):
            flush()
            continue
        heading = MD_HEADING.match(line)
        if heading:
            flush()
            level, words = len(heading.group(1)), WHITE.sub(" ", _md_clean(_md_title(heading.group(2)))).strip()
            if words:
                page.blocks.append(Block("heading", words, None, level))
                if level <= 4:
                    section = words
            continue
        item = MD_ITEM.match(line)
        if item:
            flush()
            paragraph.append(item.group(1))
            flush()
            continue
        stripped = line.strip()
        if stripped.startswith("|"):
            flush()
            for cell in stripped.strip("|").split("|"):
                cell = WHITE.sub(" ", _md_clean(cell)).strip()
                if cell and not set(cell) <= set("-: "):
                    page.blocks.append(Block("text", cell, section))
            continue
        if not stripped:
            flush()
            continue
        paragraph.append(stripped)
    flush()
    page.title = next((b.text for b in page.blocks if b.kind == "heading"), None)
    return page


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > FILE_LIMIT:
            return None
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def parse_page(path: Path, text: str) -> Page:
    return md_page(text) if path.suffix.lower() == ".md" else html_page(text)


# -- a small cache --------------------------------------------------------------------------------
# The first scan of the owner's studio took about 1 s, nearly all of it parsing pages and folding
# text, and a person types several queries in a row. So each file's parsed, folded text is kept,
# keyed on its path, size and time, and all of it is dropped when it would hold more than
# CACHE_CHARS characters.
# A studio that nobody searches holds none of it: the routes ask for `drop_if_idle` a while after
# each search (the machine this was written on has 16 GB and runs two studios).
CACHE_CHARS = 60_000_000       # counted as `_cost` below: roughly bytes. A studio with 68 projects held about 55 MB
CACHE_IDLE = 600.0             # seconds without a search before all of it is let go
_cache: dict[str, tuple[tuple, object, int]] = {}
_cache_chars = 0
_cache_used = 0.0
_cache_lock = threading.Lock()


def touch_cache() -> None:
    """A search began: the cache is in use."""
    global _cache_used
    _cache_used = time.monotonic()


def drop_if_idle(idle: float = CACHE_IDLE) -> bool:
    """Let the cache go when no search began in the last `idle` seconds; True when it was dropped."""
    if time.monotonic() - _cache_used < idle:
        return False
    clear_cache()
    return True


def _stamp(path: Path) -> tuple | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _held(kind: str, path: Path, stamp: tuple):
    with _cache_lock:
        held = _cache.get(f"{kind}\x00{path}")
    return held[1] if held is not None and held[0] == stamp else None


def _keep(kind: str, path: Path, stamp: tuple, value, chars: int) -> None:
    global _cache_chars
    key = f"{kind}\x00{path}"
    with _cache_lock:
        old = _cache.pop(key, None)
        if old is not None:
            _cache_chars -= old[2]
        if _cache_chars + chars > CACHE_CHARS:
            _cache.clear()
            _cache_chars = 0
        _cache[key] = (stamp, value, chars)
        _cache_chars += chars


def _cost(texts: int, pieces: int) -> int:
    """About the bytes one entry holds: each piece's text and its folded text, the joined folded
    text, and an object per piece (a table-heavy page is mostly objects, so they are counted)."""
    return 3 * texts + 300 * pieces


def clear_cache() -> None:
    global _cache_chars
    with _cache_lock:
        _cache.clear()
        _cache_chars = 0


def load_page(path: Path) -> Page | None:
    """The file's page, parsed and folded; None when it cannot be read or is over FILE_LIMIT."""
    stamp = _stamp(path)
    if stamp is None or stamp[0] > FILE_LIMIT:
        return None
    page = _held("page", path, stamp)
    if page is None:
        text = read_text(path)
        if text is None:
            return None
        page = parse_page(path, text).prepare()
        _keep("page", path, stamp, page, _cost(len(page.folded_all), len(page.blocks)))
    return page


# -- one project's pages --------------------------------------------------------------------------
def find_in_page(page: Page, query: Query) -> list[tuple[int, int, dict]]:
    """→ [(rank, order, hit)] for every matching block, unsorted. `snippet` holds the block's whole
    text here; `find_in_project` cuts it for the hits it lists."""
    hits: list[tuple[int, int, dict]] = []
    if not page.may_hold(query):
        return hits
    first_heading = next((b.text for b in page.blocks if b.kind == "heading"), None)
    if page.title and page.title != first_heading and matches(query, page.folded_title):
        hits.append((HEADING, -1, {"kind": "heading", "section": None, "snippet": page.title}))
    for order, block in enumerate(page.blocks):
        if not matches(query, block.folded):
            continue
        heading = block.kind == "heading"       # h1-h6, as the page itself ranks them
        rank = HEADING if heading else PHRASE if query.whole in block.folded else OTHER
        hits.append((rank, order, {"kind": "heading" if block.kind == "heading" else "text",
                                   "section": None if block.kind == "heading" else block.section,
                                   "snippet": block.text}))
    return hits


def page_files(workspace: Path, limit: int = MAX_FILES, deadline: float | None = None,
               max_entries: int = MAX_ENTRIES) -> tuple[list[tuple[str, Path]], bool]:
    """The project's pages and notes as (relative path, path), and whether a limit was reached:
    `limit` pages, `max_entries` names of any kind (a data folder of a hundred thousand files has
    no page in it and would still be walked on every query), or the `deadline`.
    Links are never followed or read; folders the studio never watches are skipped."""
    root = workspace.resolve()
    found: list[tuple[str, Path]] = []
    looked = 0
    seen = 0
    if not root.is_dir():
        return found, False
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and d not in BUILD_DIRS and d not in SKIP_DIRS
                         and not d.startswith(".") and not keyfiles.is_key_name(d))
        seen += len(dirs) + len(names)
        if seen > max_entries or (deadline is not None and time.monotonic() > deadline):
            return found, True
        for name in sorted(names):
            if name.startswith(".") or keyfiles.is_key_name(name) or not name.lower().endswith(PAGE_SUFFIXES):
                continue
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
                continue
            looked += 1
            if looked > limit:
                return found, True
            found.append((path.relative_to(root).as_posix(), path))
    return found, False


def find_in_project(workspace: Path, query: Query, deadline: float | None = None,
                    max_pages: int = MAX_PAGES, hits_per_page: int = HITS_PER_PAGE) -> tuple[list[dict], int, bool]:
    """→ (pages, pages found beyond `max_pages`, capped)."""
    files, capped = page_files(workspace, deadline=deadline)
    pages: list[tuple[int, str, dict]] = []
    for rel, path in files:
        if deadline is not None and time.monotonic() > deadline:
            capped = True
            break
        stamp = _stamp(path)
        if stamp is None:
            continue
        if stamp[0] > FILE_LIMIT:
            capped = True
            continue
        page = load_page(path)
        if page is None:
            continue
        hits = find_in_page(page, query)
        if not hits:
            continue
        hits.sort(key=lambda item: (item[0], item[1]))
        listed = [{**hit, "snippet": snippet(hit["snippet"], query)} for _, _, hit in hits[:hits_per_page]]
        pages.append((hits[0][0], rel, {"path": rel, "title": page.title, "hits": listed,
                                        "more": max(0, len(hits) - hits_per_page)}))
    pages.sort(key=lambda item: (item[0], item[1]))
    left = max(0, len(pages) - max_pages)
    if left:
        capped = True
    return [page for _, _, page in pages[:max_pages]], left, capped


# -- the chat -------------------------------------------------------------------------------------
TURN_MARK = '"type": "turn_started"'
CHUNK_MARK = '"type": "message_chunk"'


def chat_messages(events_path: Path, deadline: float | None = None) -> tuple[list[dict], bool]:
    """The person's messages and the agent's words, in order: [{seq, role, text, at}].
    Only `turn_started` and `message_chunk` lines are parsed; any other line is passed over on its
    raw text (tool output can be large)."""
    messages: list[dict] = []
    open_messages: dict[str, dict] = {}
    capped = False
    try:
        handle = events_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return messages, False
    with handle:
        for count, line in enumerate(handle):
            if deadline is not None and count % 2000 == 0 and time.monotonic() > deadline:
                capped = True
                break
            if TURN_MARK not in line and CHUNK_MARK not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "turn_started":
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    messages.append({"seq": event.get("seq"), "role": "person", "text": text, "at": event.get("ts")})
            elif kind == "message_chunk":
                text = event.get("text")
                if not isinstance(text, str):
                    continue
                key = f"{event.get('turn_id')}\x00{event.get('message_id')}"
                message = open_messages.get(key)
                if message is None:
                    message = {"seq": event.get("seq"), "role": "agent", "parts": [], "at": event.get("ts")}
                    open_messages[key] = message
                    messages.append(message)
                message["parts"].append(text)
    for message in messages:
        if "parts" in message:
            message["text"] = "".join(message.pop("parts"))
    return [m for m in messages if m["text"].strip()], capped


def load_chat(events_path: Path, deadline: float | None = None) -> tuple[list[dict], str, bool]:
    """→ (messages, each with `folded` as well; all their folded text in one string; capped).
    A log read only in part (the time ran out) is not kept."""
    stamp = _stamp(events_path)
    if stamp is None:
        return [], "", False
    held = _held("chat", events_path, stamp)
    if held is not None:
        return held[0], held[1], False
    messages, capped = chat_messages(events_path, deadline)
    for message in messages:
        message["folded"] = fold(message["text"])
    folded_all = "\n".join(message["folded"] for message in messages)
    if not capped:
        _keep("chat", events_path, stamp, (messages, folded_all), _cost(len(folded_all), len(messages)))
    return messages, folded_all, capped


def chat_matches(events_path: Path, query: Query, deadline: float | None = None) -> tuple[list[dict], bool]:
    """→ (the matching messages in order, as `load_chat` holds them; capped). No snippet is cut
    here: a long chat and a common word match thousands of lines, and three are listed."""
    messages, folded_all, capped = load_chat(events_path, deadline)
    if not all(term in folded_all for term in query.terms):
        return [], capped
    return [m for m in messages if matches(query, m["folded"])], capped


def chat_hit(message: dict, query: Query) -> dict:
    return {"seq": message["seq"], "role": message["role"], "snippet": snippet(message["text"], query), "at": message["at"]}


def find_in_chat(events_path: Path, query: Query, deadline: float | None = None,
                 newest: int | None = None) -> tuple[list[dict], bool, int]:
    """→ (chat hits [{seq, role, snippet, at}], capped, how many matched). In order; with `newest`,
    only that many, newest first."""
    found, capped = chat_matches(events_path, query, deadline)
    kept = found if newest is None else found[::-1][:newest]
    return [chat_hit(message, query) for message in kept], capped, len(found)


# -- the routes' answers --------------------------------------------------------------------------
def project_answer(workspace: Path, attached: bool, query: Query | None, raw: str,
                   budget: float = 5.0) -> dict:
    if query is None:
        return {"query": raw, "attached": attached, "pages": [], "capped": False}
    if attached:
        return {"query": raw, "attached": True, "pages": [], "capped": False}
    pages, _left, capped = find_in_project(workspace, query, time.monotonic() + budget)
    return {"query": raw, "attached": False, "pages": pages, "capped": capped}


def everywhere_answer(projects: list[dict], workspace_of, events_of, query: Query | None, raw: str,
                      budget: float = 2.0, pages_each: int = 3, hits_each: int = 3, chat_each: int = 3) -> dict:
    """`projects`: the records the caller may see, in the list's order. `workspace_of(id)` and
    `events_of(id)` give each one's folder and event log."""
    if query is None:
        return {"query": raw, "projects": [], "capped": False}
    deadline = time.monotonic() + budget
    named, others = [], []
    capped = False
    for record in projects:
        if time.monotonic() > deadline:
            capped = True
            break
        project_id = record["id"]
        name = record.get("name") or ""
        attached = bool(record.get("attach"))
        name_match = matches(query, fold(name))
        pages: list[dict] = []
        left_pages = 0
        if not attached:
            pages, left_pages, page_capped = find_in_project(workspace_of(project_id), query, deadline,
                                                             hits_per_page=hits_each)
            capped = capped or page_capped
        chat, chat_capped, chat_found = find_in_chat(events_of(project_id), query, deadline, newest=chat_each)
        capped = capped or chat_capped
        if not (name_match or pages or chat):
            continue
        entry = {"id": project_id, "name": name, "name_match": name_match, "attached": attached,
                 "pages": pages[:pages_each], "chat": chat,
                 "more": {"pages": left_pages + max(0, len(pages) - pages_each),
                          "chat": max(0, chat_found - len(chat))}}
        (named if name_match else others).append(entry)
    return {"query": raw, "projects": named + others, "capped": capped}
