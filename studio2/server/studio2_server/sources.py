"""Sources: pages and documents the agent read, saved in the project as plain text.

`sources/index.json` lists them; `sources/<id>.txt` is the text exactly as it was read. The
page check (check.py) verifies quotes against these files. CONTRACT.md, "Sources".
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

from .config import now_iso
from .store import write_json_atomic

MAX_DOWNLOAD, MAX_TEXT = 25 * 1024 * 1024, 2_000_000
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "select", "button"}
BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "li", "ul", "ol", "tr",
         "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "figure", "figcaption", "br", "hr",
         "dt", "dd", "form", "details", "summary"}
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "area", "base", "col", "embed", "track", "wbr"}


class SourceError(Exception):
    def __init__(self, message: str, refused: bool = False):
        super().__init__(message)
        self.refused = refused   # the site turned a plain download away; a real browser may get in


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in SKIP:
            self._skip += 1
        elif tag in BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self._skip:
            self.parts.append(data)


def tidy(text: str) -> str:
    text = text.replace("\r", "\n").replace(" ", " ").replace("­", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip(" |") for line in text.split("\n")]
    out, blank = [], False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def html_to_text(markup: str) -> tuple[str, str]:
    """→ (title, readable text)."""
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # a broken page still yields what was parsed so far
        pass
    return " ".join("".join(parser.title).split())[:300], tidy("".join(parser.parts))


def pdf_to_text(data: bytes) -> tuple[str, int, str]:
    """→ (text with `[page N]` lines, page count, title)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = []
        for number, page in enumerate(reader.pages, 1):
            pages.append(f"[page {number}]\n{tidy(page.extract_text() or '')}")
            if sum(map(len, pages)) > MAX_TEXT:
                break
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except Exception as exc:
        raise SourceError(f"This PDF could not be read ({exc.__class__.__name__}: {exc}).") from None
    text = "\n\n".join(pages)
    if not re.sub(r"\[page \d+\]|\s", "", text):
        raise SourceError("This PDF has no text layer (it is probably scanned images), so nothing could be read.")
    return text, len(reader.pages), title[:300]


async def download(url: str) -> tuple[bytes, str, str]:
    """→ (body, content type, final URL). Public http(s) only, at most 25 MB."""
    if not re.match(r"https?://", url, re.I):
        raise SourceError("Only http and https addresses can be fetched.")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, connect=10.0),
                                     headers=headers, max_redirects=8) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise SourceError(f"The site answered HTTP {response.status_code} for {url}.",
                                      refused=response.status_code in (401, 403, 406, 429, 503))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise SourceError("The file is larger than 25 MB.")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type", ""), str(response.url)
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not download {url} ({exc.__class__.__name__}: {exc}).") from None


def extract(data: bytes, content_type: str, name: str) -> tuple[str, str, str, int | None]:
    """→ (kind, title, text, pages) for downloaded or local bytes."""
    lowered = name.lower().split("?")[0]
    if data[:5] == b"%PDF-" or "application/pdf" in content_type or lowered.endswith(".pdf"):
        text, pages, title = pdf_to_text(data)
        return "pdf", title, text, pages
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        charset = re.search(r"charset=([\w-]+)", content_type or "")
        try:
            decoded = data.decode(charset.group(1) if charset else "latin-1", "replace")
        except LookupError:
            decoded = data.decode("latin-1", "replace")
    if "html" in content_type or lowered.endswith((".html", ".htm")) or re.search(r"<html[\s>]|<!doctype html", decoded[:2000], re.I):
        title, text = html_to_text(decoded)
        return "html", title, text, None
    if b"\x00" in data[:4000]:
        raise SourceError("This file is not text, HTML or PDF, so it cannot be read as a source.")
    return "text", "", tidy(decoded), None


def same_address(url: str | None) -> str:
    """An address reduced to what makes it the same page: no fragment, no trailing slash, host in lower case."""
    url = str(url or "").strip().split("#", 1)[0]
    if "://" not in url:
        return ""
    scheme, rest = url.split("://", 1)
    host, _, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}/{path}".rstrip("/")


class Sources:
    """The sources of one project. Synchronous; callers use a thread for the slow parts."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.folder = workspace / "sources"
        self.index_path = self.folder / "index.json"

    def list(self) -> list[dict]:
        try:
            items = json.loads(self.index_path.read_text("utf-8")).get("sources")
        except (OSError, ValueError, AttributeError):
            return []
        return [item for item in items or [] if isinstance(item, dict) and item.get("id")]

    def get(self, source_id: str) -> dict:
        for item in self.list():
            if item["id"] == source_id:
                return item
        raise SourceError(f"There is no source “{source_id}” in this project. "
                          "Use fetch_source or add_source first; sources/index.json lists what exists.")

    def text(self, source_id: str) -> str:
        item = self.get(source_id)
        try:
            return (self.workspace / item["path"]).read_text("utf-8")
        except OSError:
            raise SourceError(f"The saved text of source {source_id} is missing ({item['path']}).") from None

    def by_url(self, url: str) -> dict | None:
        """The newest saved source that came from this address (as asked for, or as it ended up)."""
        wanted = same_address(url)
        if not wanted:
            return None
        for item in reversed(self.list()):
            if wanted in {same_address(item.get("url")), *map(same_address, item.get("asked") or [])}:
                return item
        return None

    def save(self, *, text: str, title: str, kind: str, url: str | None, pages: int | None = None,
             original: bytes | None = None, original_ext: str = "", requested: str | None = None) -> tuple[dict, bool]:
        """→ (source, created). The same URL with the same text returns the existing source.
        `requested` is the address that was asked for, kept when it differs from where it ended up."""
        asked = [requested] if requested and same_address(requested) != same_address(url) else []
        text = text[:MAX_TEXT]
        if len(text.strip()) < 20:
            raise SourceError("Nothing readable was found there (the page may be built by scripts: "
                              "try again with render: true).")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        items = self.list()
        for item in items:
            if item.get("sha256") == digest and item.get("url") == url:
                if asked and asked[0] not in (item.get("asked") or []):  # another way to the same page
                    item["asked"] = [*(item.get("asked") or []), asked[0]][-8:]
                    write_json_atomic(self.index_path, {"sources": items})
                return item, False
        number = max([int(m.group(1)) for m in (re.fullmatch(r"s(\d+)", i["id"]) for i in items) if m] or [0]) + 1
        source_id = f"s{number}"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{source_id}.txt").write_text(text, "utf-8")
        kept = None
        if original is not None and original_ext:
            kept = f"sources/{source_id}{original_ext}"
            (self.workspace / kept).write_bytes(original)
        item = {"id": source_id, "url": url, "path": f"sources/{source_id}.txt", "original": kept,
                "title": (title or url or source_id)[:300], "kind": kind, "fetched_at": now_iso(),
                "chars": len(text), "sha256": digest, "pages": pages, **({"asked": asked} if asked else {})}
        write_json_atomic(self.index_path, {"sources": [*items, item]})
        return item, True

    def read(self, source_id: str, find: str | None = None, offset: int = 0, max_chars: int = 12_000,
             end: int | None = None) -> dict:
        """A part of the text: the passages around `find`, or the range from `offset` (to `end` when given)."""
        text = self.text(source_id)
        max_chars = max(500, min(int(max_chars or 12_000), 60_000))
        if end and not find:  # an exact range, however short
            max_chars = max(1, min(int(end) - max(0, int(offset or 0)), 60_000))
        if find:
            needle, passages, start = find.lower(), [], 0
            lowered = text.lower()
            while len(passages) < 12:
                at = lowered.find(needle, start)
                if at < 0:
                    break
                a, b = max(0, at - 400), min(len(text), at + len(find) + 600)
                passages.append({"at": at, "text": text[a:b]})
                start = b
            return {"id": source_id, "chars": len(text), "find": find, "matches": len(passages),
                    "passages": passages}
        offset = max(0, int(offset or 0))
        chunk = text[offset:offset + max_chars]
        return {"id": source_id, "chars": len(text), "offset": offset, "text": chunk,
                "next_offset": offset + len(chunk) if offset + len(chunk) < len(text) else None}

    def add_file(self, path: Path, relative: str, title: str | None = None) -> tuple[dict, bool]:
        if not path.is_file():
            raise SourceError(f"There is no file at {relative}.")
        if path.stat().st_size > MAX_DOWNLOAD:
            raise SourceError("The file is larger than 25 MB.")
        kind, found_title, text, pages = extract(path.read_bytes(), "", path.name)
        item, created = self.save(text=text, title=title or found_title or path.name, kind=kind,
                                  url=None, pages=pages)
        if created:  # remember which project file it came from
            items = self.list()
            for entry in items:
                if entry["id"] == item["id"]:
                    entry["original"] = item["original"] = relative
            write_json_atomic(self.index_path, {"sources": items})
        return item, created
