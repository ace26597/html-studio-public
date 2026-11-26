"""The site check: every page of a project read together (CONTRACT.md, "Projects of many pages").

It reads files only: no browser, nothing fetched. Links, anchors, pictures, scripts, stylesheets and
frames are resolved against the project folder; pages no link reaches, pages that lead nowhere, and a
menu or a stylesheet that one page does not share with the rest are named in plain words.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import time
from collections import Counter, deque
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from . import export
from . import workspace as ws

MAX_FINDINGS = 300
MAX_LISTED_PAGES = 100
MAX_PAGES = 2_000
# Every limit holds while collecting, not after (a page of 500,000 broken links once took 3 GB):
PAGE_BYTES = 2_000_000         # a bigger page is `too_large` and not read
TOTAL_BYTES = 50_000_000       # pages and stylesheets read in one run
LINKS_PER_PAGE = 5_000         # links, pictures, scripts and frames kept from one page
KEPT_FINDINGS = 2_000          # findings kept (and saved); the rest are only counted
TEXT_CAP = 300                 # characters of an address or a title kept in a finding
SITES_CAP = 1_000              # outside host names counted
TIME_BUDGET = 20.0             # seconds for the whole run; then what there is, said as cut
SAVED_BYTES = 5_000_000        # the saved file at most
SAVED = "research/site-check.json"
FREE_ANCHORS = {"", "top"}
NOT_FILES = re.compile(r"^(?:mailto|tel|sms|javascript|data|blob|about|file):", re.I)
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
WHOLE_PAGE = re.compile(r"<!doctype|<html[\s>]|<head[\s>]|<body[\s>]", re.I)
JOIN_MARK = re.compile(r"<!--\s*join:|\{\{\s*fact:")   # a shell or a piece the large-documents joiner fills
CSS_REF = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)|@import\s+["']([^"']+)["']""", re.I)
COVERS = ("It reads the files as written: it does not see links or pictures a script builds, the routes of an "
          "app's back end, how the pages look, or whether the facts on them are right.")


class _Reader(HTMLParser):
    """One page → its links, files, ids, title, viewport, nav links and base."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []          # a/area href
        self.nav: list[str] = []            # the hrefs among them that sit inside a <nav>
        self.files: list[tuple[str, str]] = []   # (what, address): picture, script, stylesheet, frame
        self.ids: set[str] = set()
        self.title: str | None = None
        self.viewport = False
        self.base: str | None = None
        self._nav = 0
        self._title: list[str] | None = None
        self.cut = False                    # more than LINKS_PER_PAGE addresses: the rest were not kept

    def _room(self) -> bool:
        if len(self.links) + len(self.files) < LINKS_PER_PAGE:
            return True
        self.cut = True
        return False

    def handle_starttag(self, tag, attrs):
        a = {name.lower(): (value or "") for name, value in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        if tag == "nav":
            self._nav += 1
        elif tag in ("a", "area") and "href" in a:
            if self._room():
                self.links.append(a["href"])
                if self._nav:
                    self.nav.append(a["href"])
        elif tag == "title" and self.title is None:
            self._title = []
        elif tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.viewport = True
        elif tag == "link":
            rel = set(a.get("rel", "").lower().split())
            if "stylesheet" in rel and a.get("href"):
                self._room() and self.files.append(("stylesheet", a["href"]))
            elif rel & {"icon", "apple-touch-icon", "mask-icon"} and a.get("href"):
                self._room() and self.files.append(("picture", a["href"]))
        elif tag == "script" and a.get("src"):
            self._room() and self.files.append(("script", a["src"]))
        elif tag in ("iframe", "frame") and a.get("src"):
            self._room() and self.files.append(("frame", a["src"]))
        elif tag == "object" and a.get("data"):
            self._room() and self.files.append(("frame", a["data"]))
        elif tag == "embed" and a.get("src"):
            self._room() and self.files.append(("frame", a["src"]))
        if tag in ("img", "source", "video", "audio", "track") or (tag == "input" and a.get("type", "").lower() == "image"):
            if a.get("src"):
                self._room() and self.files.append(("picture", a["src"]))
            for address, _ in export._srcset(a.get("srcset", "")):
                self._room() and self.files.append(("picture", address))
            if a.get("poster"):
                self._room() and self.files.append(("picture", a["poster"]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "nav":
            self._nav = max(0, self._nav - 1)

    def handle_endtag(self, tag):
        if tag == "nav":
            self._nav = max(0, self._nav - 1)
        elif tag == "title" and self._title is not None:
            self.title = " ".join("".join(self._title).split())[:TEXT_CAP]
            self._title = None

    def handle_data(self, data):
        if self._title is not None and sum(map(len, self._title)) < TEXT_CAP * 4:
            self._title.append(data)


def _read(path: Path, cap: int = PAGE_BYTES) -> str:
    with path.open("rb") as handle:
        return handle.read(cap).decode("utf-8", "replace")


class _Site:
    def __init__(self, workspace: Path, start: str, max_pages: int, save_to: Path | None = None):
        self.root = workspace.resolve()
        self.save_to = Path(save_to) if save_to else None
        given = str(start or "").strip().lstrip("/")
        self.start = posixpath.normpath(given) if given else "index.html"
        self.start_chosen = not given
        self.start_note: str | None = None
        self.max_pages = max(1, min(int(max_pages or 200), MAX_PAGES))
        self.app = export._run_command(self.root) is not None
        kept = export.kept(self.root)
        self.kept = {path.as_posix() for path in kept}
        self.all_pages = [path.as_posix() for path in kept if path.suffix.lower() in (".html", ".htm")
                          and not any(part.startswith(".") for part in path.parts)]
        self.findings: list[dict] = []
        self.seen: dict[tuple, dict] = {}     # (page, kind, target) → its finding: a repeat only counts
        self.dropped: Counter = Counter()     # findings past KEPT_FINDINGS, by kind
        self.bytes_read = 0
        self.started = time.monotonic()
        self.cut: dict[str, str] = {}         # why the run was cut short, for `truncated`
        self.too_large: set[str] = set()
        self.unchecked: list[str] = []
        self.outside: Counter = Counter()
        self.pieces = 0
        self.read: dict[str, _Reader] = {}
        self.exists: dict[str, tuple[bool, bool, int]] = {}    # path → (inside, is file, size)
        if self.start_chosen and self.start not in self.all_pages:
            self._choose_start()

    # -- the start page --------------------------------------------------------------------------------
    def index_pages(self) -> list[str]:
        """The index pages there are (index.html, index.htm), nearest the root first."""
        return sorted((p for p in self.all_pages if posixpath.basename(p).lower() in ("index.html", "index.htm")),
                      key=lambda p: (p.count("/"), p))

    def _choose_start(self) -> None:
        """No start given and no index.html at the root: an app serves its own folder (the fastapi-app
        template serves static/), so the one index page nearest the root is the start. Two at the same
        depth are not guessed between; the answer is then no_start, naming them."""
        found = self.index_pages()
        if not found:
            return
        nearest = [p for p in found if p.count("/") == found[0].count("/")]
        if len(nearest) == 1:
            self.start = nearest[0]
            self.start_note = (f"No start was given and there is no index.html at the root, so {self.start}, the "
                               "index page nearest the root, was used. Give start to check from another page.")

    # -- addresses -------------------------------------------------------------------------------------
    def resolve(self, page: str, address: str, base: str | None, count: bool = True) -> tuple[str, str | None, str | None]:
        """→ (what, path, fragment). what: "page" (same page), "outside", "ignore", "leaves", "file"."""
        address = (address or "").strip()
        if NOT_FILES.match(address):
            return "ignore", None, None
        if address.startswith("//") or SCHEME.match(address):
            try:
                host = urlsplit(address if not address.startswith("//") else "https:" + address).hostname
            except ValueError:
                host = None
            if host and count and (host in self.outside or len(self.outside) < SITES_CAP):
                self.outside[host] += 1
            return "outside", None, None
        parts = urlsplit(address)
        fragment = unquote(parts.fragment) if "#" in address else None
        path = unquote(parts.path)
        if not path:
            return "page", page, fragment
        if path.startswith("/"):
            joined = path.lstrip("/")
        else:
            folder = posixpath.dirname(page)
            if base is not None:
                folder = base
            joined = posixpath.join(folder, path)
        trailing = path.endswith("/")
        normal = posixpath.normpath(joined) if joined else "."
        if normal == ".." or normal.startswith("../"):
            return "leaves", path, fragment
        if trailing or normal == ".":
            normal = posixpath.join("" if normal == "." else normal, "index.html")
        return "file", normal, fragment

    def stat(self, path: str) -> tuple[bool, bool, int]:
        """→ (inside the project, a file, its size); a folder means its index.html."""
        if path not in self.exists:
            try:
                target = ws.safe_path(self.root, path)
            except Exception:
                self.exists[path] = (False, False, 0)
            else:
                if target.is_dir():
                    inner = posixpath.join(path, "index.html")
                    self.exists[path] = (True, False, 0)     # set first: an index.html that is a folder again ends here
                    if not path.endswith("/index.html"):
                        self.exists[path] = self.stat(inner)
                    return self.exists[path]
                try:
                    size = target.stat().st_size if target.is_file() else -1
                except OSError:
                    size = -1
                self.exists[path] = (True, size >= 0, max(size, 0))
        return self.exists[path]

    def as_file(self, path: str) -> str:
        """A folder address → the index.html it means; anything else as it is."""
        try:
            if ws.safe_path(self.root, path).is_dir():
                return posixpath.join(path, "index.html")
        except Exception:
            pass
        return path

    def real_name(self, path: str) -> str | None:
        """The path as the disk spells it, when it differs from `path` only in capitals (None: no such file).
        This Mac's disk ignores case, so `About.html` opens `about.html` here; most hosts would not."""
        if not hasattr(self, "_listing"):
            self._listing: dict[str, list[str]] = {}
        folder, out = "", []
        for part in path.split("/"):
            if folder not in self._listing:
                try:
                    self._listing[folder] = os.listdir(self.root / folder) if folder else os.listdir(self.root)
                except OSError:
                    self._listing[folder] = []
            names = self._listing[folder]
            if part not in names:
                part = next((name for name in names if name.casefold() == part.casefold()), None)
                if part is None:
                    return None
            out.append(part)
            folder = "/".join(out)
        return folder

    def recase(self, page: str, what: str, address: str, target: str) -> str:
        """A target named with other capitals than the file's → the file's own path, and the finding said."""
        real = self.real_name(target)
        if real is None or real == target:
            return target
        self.add(page, "wrong_case", f"The {what} “{address}” names {target}, but the file is {real}. It works here, "
                                     "where capitals do not matter, and breaks on a host that tells them apart.", address)
        return real

    def add(self, page: str, kind: str, detail: str, target: str | None = None) -> None:
        if target is not None and len(target) > TEXT_CAP:
            target = target[:TEXT_CAP] + "…"
        key = (page, kind, target)
        if key in self.seen:                 # the same link twice on a page is one finding
            self.seen[key]["times"] = self.seen[key].get("times", 1) + 1
            return
        if len(self.findings) >= KEPT_FINDINGS:
            self.dropped[kind] += 1
            return
        item = {"page": page, "kind": kind, "detail": detail if len(detail) < TEXT_CAP * 3 else detail[:TEXT_CAP * 3] + "…"}
        if target is not None:
            item["target"] = target
        self.seen[key] = item
        self.findings.append(item)

    def out_of_time(self) -> bool:
        if time.monotonic() - self.started > TIME_BUDGET:
            self.cut.setdefault("time", f"The check stopped after {TIME_BUDGET:.0f} seconds; what it found so far is here.")
            return True
        return False

    def missing(self, page: str, kind: str, address: str, detail: str) -> None:
        if self.app:          # an app's back end may answer it
            self.unchecked.append(f"{page}: {address}")
        else:
            self.add(page, kind, detail, address)

    # -- the run ---------------------------------------------------------------------------------------
    def run(self) -> dict:
        # Pages are read in the order a reader finds them from the start page, then the rest: past
        # `max_pages` the unread ones are those furthest away, and no page read is called an orphan
        # because the page that links to it went unread.
        known = set(self.all_pages)
        rest = sorted(self.all_pages, key=lambda p: (p.count("/"), p))
        queue, queued, examined = deque([self.start] if self.start in known else []), {self.start}, 0
        while len(self.read) < self.max_pages:
            if self.out_of_time():
                break
            if self.bytes_read >= TOTAL_BYTES:
                self.cut.setdefault("bytes", f"The check read {TOTAL_BYTES // 1_000_000} MB of pages, its limit, "
                                             "and stopped reading there.")
                break
            if queue:
                path = queue.popleft()
            else:
                path = next((p for p in rest if p not in queued), None)
                if path is None:
                    break
                queued.add(path)
            examined += 1
            reader = self._parse(path)
            if reader is None:
                continue
            self.read[path] = reader
            base = self._base(path, reader.base)
            for address in reader.links + [a for what, a in reader.files if what == "frame"]:
                what, target, _fragment = self.resolve(path, address, base, count=False)
                if what == "file":
                    target = self.as_file(target) if target not in known else target
                    if target in known and target not in queued:
                        queued.add(target)
                        queue.append(target)
        ordered = self.all_pages
        pages = list(self.read)
        page_set = set(pages)
        left_unread = len(ordered) - examined
        links: dict[str, set[str]] = {page: set() for page in pages}     # page → other pages it links to
        framed: dict[str, set[str]] = {page: set() for page in pages}
        nav_sets: dict[str, frozenset] = {}
        sheets: dict[str, set[str]] = {page: set() for page in pages}
        css_done: set[str] = set()
        self.links, self.framed = links, framed

        for page in pages:
            if self.out_of_time():
                break
            reader = self.read[page]
            base = self._base(page, reader.base)
            nav_targets = []
            for address in reader.links:
                what, target, fragment = self.resolve(page, address, base)
                if what == "page":
                    self._anchor(page, page, fragment, address)
                    continue
                if what == "leaves":
                    self.add(page, "leaves_project", f"The link “{address}” points outside the project folder, "
                                                     "so it breaks wherever the site is put.", address)
                    continue
                if what != "file":
                    if what == "outside" and address in reader.nav:
                        nav_targets.append(address.strip())
                    continue
                inside, is_file, _size = self.stat(target)
                if not inside:
                    self.add(page, "leaves_project", f"The link “{address}” leads outside the project folder.", address)
                    continue
                if self.recase(page, "link", address, self.as_file(target)) != self.as_file(target):
                    target = self.real_name(self.as_file(target))
                    inside, is_file, _size = self.stat(target)
                if not is_file:
                    self.missing(page, "broken_link", address,
                                 f"The link “{address}” leads to {target}, which is not in the project.")
                    continue
                target = self.as_file(target)
                if target not in self.kept and not target.startswith("."):
                    self.add(page, "not_exported", f"The link “{address}” leads to {target}, which an export "
                                                   "leaves out (a working folder), so it breaks once exported.", address)
                if target in page_set:
                    if target != page:
                        links[page].add(target)
                    self._anchor(page, target, fragment, address)
                if address in reader.nav and not (target == page and fragment):
                    nav_targets.append(target)
            if reader.nav:
                nav_sets[page] = frozenset(nav_targets)
            for what, address in reader.files:
                kind, target, _fragment = self.resolve(page, address, base)
                if kind == "leaves":
                    self.add(page, "leaves_project", f"The {what} “{address}” is outside the project folder.", address)
                    continue
                if kind != "file":
                    continue
                inside, is_file, size = self.stat(target)
                if inside and self.recase(page, what, address, target) != target:
                    target = self.real_name(target)
                    inside, is_file, size = self.stat(target)
                if not inside:
                    self.add(page, "leaves_project", f"The {what} “{address}” is outside the project folder.", address)
                elif not is_file:
                    self.missing(page, "missing_file", address, f"The {what} “{address}” ({target}) is not in the project.")
                elif size == 0:
                    self.add(page, "empty_file", f"The {what} “{address}” ({target}) is an empty file.", address)
                else:
                    target = self.as_file(target)
                    if what == "frame" and target in page_set and target != page:
                        framed[page].add(target)
                    if what == "stylesheet":
                        sheets[page].add(target)
                        if target not in css_done:
                            css_done.add(target)
                            self._css(target)
        self._reach(pages, links, framed)
        self._pages(pages, links, framed)
        self._nav(pages, nav_sets, framed)
        self._styles(pages, sheets, framed)
        return self._answer(pages, links, framed, left_unread)

    def _parse(self, path: str) -> "_Reader | None":
        """A page → what it holds; None for a piece of a page (no <html>, <head>, <body> or doctype) or a
        shell or piece still holding the joiner's marks."""
        try:
            size = (self.root / path).stat().st_size
            if size > PAGE_BYTES:
                self.too_large.add(path)
                self.add(path, "too_large", f"The page is {size / 1e6:.1f} MB, more than the "
                                            f"{PAGE_BYTES // 1_000_000} MB this check reads, so it was not checked.")
                reader = _Reader()
                reader.title = path          # not read: no findings about its title, menu or links
                reader.viewport = True
                return reader
            text = _read(self.root / path)
            self.bytes_read += len(text)
        except OSError:
            return None
        if not WHOLE_PAGE.search(text) or JOIN_MARK.search(text):
            self.pieces += 1
            return None
        reader = _Reader()
        try:
            reader.feed(text)
            reader.close()
        except Exception:      # html.parser is forgiving; a page it cannot finish is read as far as it got
            pass
        return reader

    def _base(self, page: str, base: str | None) -> str | None:
        """A local <base href> → the folder relative links start from (None: the page's own folder)."""
        if not base or SCHEME.match(base) or base.startswith("//"):
            return None
        path = unquote(urlsplit(base).path)
        joined = path.lstrip("/") if path.startswith("/") else posixpath.join(posixpath.dirname(page), path)
        if not path.endswith("/"):
            joined = posixpath.dirname(joined)
        normal = posixpath.normpath(joined) if joined else "."
        return "" if normal == "." else normal

    def _anchor(self, page: str, target: str, fragment: str | None, address: str) -> None:
        if fragment is None or fragment.lower() in FREE_ANCHORS or fragment.startswith(":~:"):
            return
        reader = self.read.get(target)
        if reader is not None and fragment not in reader.ids:
            where = "this page" if target == page else target
            self.add(page, "broken_anchor", f"The link “{address}” points to #{fragment}, but {where} has no "
                                            "element with that id or name.", address)

    def _css(self, sheet: str) -> None:
        if self.bytes_read >= TOTAL_BYTES or self.out_of_time():
            return
        try:
            text = _read(self.root / sheet)
            self.bytes_read += len(text)
        except OSError:
            return
        folder = posixpath.dirname(sheet)
        for match in CSS_REF.finditer(text):
            address = next(v for v in match.groups() if v is not None).strip()
            if not address or NOT_FILES.match(address) or SCHEME.match(address) or address.startswith(("//", "#")):
                continue
            path = unquote(urlsplit(address).path)
            joined = path.lstrip("/") if path.startswith("/") else posixpath.join(folder, path)
            normal = posixpath.normpath(joined)
            if normal == ".." or normal.startswith("../"):
                self.add(sheet, "leaves_project", f"The stylesheet names “{address}”, outside the project folder.", address)
                continue
            inside, is_file, size = self.stat(normal)
            if inside and self.recase(sheet, "stylesheet's file", address, normal) != normal:
                normal = self.real_name(normal)
                inside, is_file, size = self.stat(normal)
            if not inside:
                self.add(sheet, "leaves_project", f"The stylesheet names “{address}”, outside the project folder.", address)
            elif not is_file:
                self.missing(sheet, "missing_file", address, f"The stylesheet names “{address}” ({normal}), which is "
                                                             "not in the project.")
            elif size == 0:
                self.add(sheet, "empty_file", f"The stylesheet names “{address}” ({normal}), an empty file.", address)

    def _reach(self, pages, links, framed) -> None:
        self.reachable: set[str] = set()
        if self.start not in self.read:
            found = self.index_pages()
            if found:
                said = (f" The index pages there are: {', '.join(found[:3])}"
                        + (f" and {len(found) - 3} more" if len(found) > 3 else "")
                        + ". Call again with one of them as start.")
            else:
                said = " There is no index page in the project: give start as the page a reader opens first."
            self.add(self.start, "no_start", f"There is no start page at {self.start}, so which pages can be "
                                             "reached was not checked." + said)
            self.reachable = set(pages)
            return
        queue = deque([self.start])
        self.reachable.add(self.start)
        while queue:
            page = queue.popleft()
            for target in links[page] | framed[page]:
                if target not in self.reachable:
                    self.reachable.add(target)
                    queue.append(target)
        for page in pages:
            if page not in self.reachable:
                self.add(page, "orphan", f"No link leads here from {self.start}, so a reader cannot find this page.")

    def _only_framed(self, page) -> bool:
        """Reached through a frame (a rendered file's preview, say) and by no link: not a page of the menu."""
        if not hasattr(self, "_linked"):
            self._linked = set().union(*self.links.values())
            self._in_frame = set().union(*self.framed.values())
        return page not in self._linked and page in self._in_frame

    def _pages(self, pages, links, framed) -> None:
        titles: dict[str, list[str]] = {}
        for page in pages:
            if page in self.too_large:
                continue
            reader = self.read[page]
            if not reader.title:
                self.add(page, "no_title", "The page has no <title>, so its tab and bookmarks have no name.")
            else:
                titles.setdefault(reader.title, []).append(page)
            if not reader.viewport:
                self.add(page, "no_viewport", "The page has no <meta name=\"viewport\">, so a phone shows it shrunk "
                                              "to a desktop's width.")
            if len(pages) > 1 and not links[page] and not self._only_framed(page):
                self.add(page, "dead_end", "The page links to no other page of the project: a reader who lands here "
                                           "has no way on or back.")
        for title, same in titles.items():
            if len(same) > 1:
                self.add(same[0], "duplicate_title", f"{len(same)} pages share the title “{title}”: "
                                                     f"{', '.join(same[:6])}.", ", ".join(same[1:6]))

    def _nav(self, pages, nav_sets, framed) -> None:
        counted = [page for page in pages if not self._only_framed(page) and page not in self.too_large]
        if len(counted) < 3 or not nav_sets:
            return
        common, count = Counter(nav_sets.values()).most_common(1)[0]
        if count * 2 <= len(counted):
            return
        for page in counted:
            have = nav_sets.get(page, frozenset())
            if have == common:
                continue
            lacks, adds = sorted(common - have), sorted(have - common)
            said = []
            if page not in nav_sets:
                said.append(f"has no <nav>, while {count} of {len(counted)} pages share one")
            else:
                if lacks:
                    said.append(f"lacks {', '.join(lacks[:8])}")
                if adds:
                    said.append(f"adds {', '.join(adds[:8])}")
            self.add(page, "nav_differs", "Its menu differs from the one most pages share: " + "; ".join(said) + ".")

    def _styles(self, pages, sheets, framed) -> None:
        counted = [page for page in pages if not self._only_framed(page) and page not in self.too_large]
        if len(counted) < 2:
            return
        use = Counter(sheet for page in counted for sheet in sheets[page])
        shared = {sheet for sheet, n in use.items() if n * 2 > len(counted)}
        if not shared:
            return
        for page in counted:
            if not sheets[page] & shared:
                self.add(page, "styles_differ", f"It links none of the stylesheets most pages share "
                                                f"({', '.join(sorted(shared))}), so it will not look like them.")

    def _save(self, text: str) -> str | None:
        """The whole result, saved → where (None: not saved). In the project only as research/site-check.json
        through the project's own path rule, and never when research/ or the file is a link: a link
        there would make the studio write wherever it points."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            if self.save_to is not None:      # the project's record folder, which the studio owns
                self.save_to.parent.mkdir(parents=True, exist_ok=True)
                folder = os.open(self.save_to.parent, flags)
                name, said = self.save_to.name, str(self.save_to)
            else:
                ws.safe_path(self.root, SAVED)            # refuses a path that ends outside the project
                research = self.root / "research"
                if research.is_symlink():
                    return None
                research.mkdir(exist_ok=True)
                folder = os.open(research, flags)        # fails if it became a link meanwhile
                name, said = "site-check.json", SAVED
            try:
                _write_in(folder, name, text)
            finally:
                os.close(folder)
            return said
        except (OSError, ws.InvalidPath):
            return None

    def _answer(self, pages, links, framed, left_unread) -> dict:
        order = {page: n for n, page in enumerate(pages)}
        self.findings.sort(key=lambda f: (f["page"] != self.start, order.get(f["page"], len(order)), f["page"]))
        by_kind = dict(Counter(f["kind"] for f in self.findings) + self.dropped)
        truncated = dict(self.cut)
        if left_unread:
            truncated["pages"] = f"{left_unread} more page{'s' if left_unread != 1 else ''} were not read."
        cut_pages = [page for page in pages if self.read[page].cut]
        if cut_pages:
            truncated["links"] = (f"{len(cut_pages)} page{'s have' if len(cut_pages) != 1 else ' has'} more than "
                                  f"{LINKS_PER_PAGE:,} links and files; the rest were not checked "
                                  f"({', '.join(cut_pages[:5])}).")
        dropped = sum(self.dropped.values())
        if dropped:
            truncated["findings"] = (f"{dropped} more findings were counted in by_kind but not kept: fix these and run "
                                     "the check again.")
        elif len(self.findings) > MAX_FINDINGS:
            truncated["findings"] = f"{len(self.findings) - MAX_FINDINGS} more findings are in the saved file."
        listed = [{"path": page, "title": self.read[page].title, "links_out": len(links[page]),
                   "reachable": page in self.reachable} for page in pages]
        sites = [host for host, _n in self.outside.most_common(20)]
        result = {"start": self.start, "start_chosen": self.start_chosen,
                  **({"start_note": self.start_note} if self.start_note else {}), "pages": len(pages), "pieces": self.pieces, "findings": self.findings,
                  "by_kind": by_kind, "pages_list": listed,
                  "outside_links": {"count": sum(self.outside.values()), "sites": sites},
                  "unchecked": {"count": len(self.unchecked), "examples": self.unchecked[:10],
                                **({"why": "This project has a back end, which may answer addresses that are not "
                                           "files: these were not counted as broken."} if self.unchecked else {})},
                  "truncated": truncated or None, "covers": COVERS}
        result["next"] = _next(result)
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if len(text) > SAVED_BYTES:          # long addresses on many findings: keep the first ones
            text = json.dumps({**result, "findings": self.findings[:MAX_FINDINGS]}, ensure_ascii=False)
        result["saved"] = self._save(text)
        return {**result, "findings": self.findings[:MAX_FINDINGS], "pages_list": listed[:MAX_LISTED_PAGES]}


def _write_in(folder_fd: int, name: str, text: str) -> None:
    """Write `name` in an open folder, never through a link (O_NOFOLLOW): a link there is an error."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644, dir_fd=folder_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _next(result: dict) -> str:
    if not result["pages"]:
        return "There are no pages in the project yet."
    if result["findings"]:
        kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(result["by_kind"].items(), key=lambda kv: -kv[1]))
        return (f"Fix these ({kinds}) and run check_site again until it finds nothing. Then run check_page on each "
                "page that states facts, and look_at_page on the start page and one inner page.")
    return ("No findings. Run check_page on each page that states facts, then look_at_page on the start page and at "
            "least one inner page, at 1440 and 390.")


def check_site(workspace: Path, start: str | None = None, max_pages: int = 200, save_to: Path | None = None) -> dict:
    """`save_to`: where the whole result is written instead of research/site-check.json in the project
    (a project brought in from a folder, whose folder is the person's own app). `start` None: index.html,
    or the one index page nearest the root when the root has none (`start_chosen` in the answer)."""
    return _Site(Path(workspace), start, max_pages, save_to).run()
