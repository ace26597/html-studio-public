"""Export: take a page or an app out of the studio (CONTRACT.md, "Export").

One HTML file with its styles, scripts, pictures and data inside; or a folder to host or run, with
a note on how. Nothing here publishes anything, and what an export leaves out or still needs is
always said in its answer.
"""

from __future__ import annotations

import base64
import datetime
import html as entities
import json
import mimetypes
import re
import shlex
import zipfile
from pathlib import Path, PurePosixPath

from . import keyfiles

LEFT_OUT = {".git", "research", "attachments", "template", "notes", "state", "looks", "node_modules", "__pycache__",
            ".venv", ".pytest_cache", ".DS_Store"}
DATA_TYPES = {".json": "application/json", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
              ".txt": "text/plain", ".geojson": "application/geo+json", ".topojson": "application/json",
              ".ndjson": "application/x-ndjson", ".jsonl": "application/x-ndjson", ".xml": "application/xml",
              ".yaml": "application/yaml", ".yml": "application/yaml", ".md": "text/markdown",
              ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif"}
MAX_BYTES = 30_000_000
README = "EXPORT-README.md"

ATTR = re.compile(r"""([^\s"'=<>/]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
LINK = re.compile(r"<link\b[^>]*>", re.I)
SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
# The parts of a page that are text, not tags: comments, and the insides of the elements whose content the
# browser never reads as markup. A tag written inside them (an HTML string in a script) is left alone.
TEXT = re.compile(r"<!--.*?-->|(<(script|style|textarea|title|xmp)\b[^>]*>)(.*?)(</\2\s*>)", re.I | re.S)
BASE = re.compile(r"<base\b[^>]*>", re.I)
MEDIA = re.compile(r"<(?:img|image|source|video|audio|track|embed|input|object)\b[^>]*>", re.I)
STYLED = re.compile(r"<[a-z][^>]*\bstyle\s*=[^>]*>", re.I)
USE = re.compile(r"<use\b[^>]*>", re.I)
ANCHOR = re.compile(r"<(?:a|iframe)\b[^>]*>", re.I)
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)""", re.I)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"')\s;]+)["']?\s*\)?\s*;""", re.I)
LOCAL_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.{1,2}/[^"']+)["']""")

# Answers fetch() and XMLHttpRequest for the files put inside the page; anything else goes to the network as before.
SHIM = """<script>/* Studio export: the page's data files are inside this file. */
(function(){var F=__FILES__,D=__DIR__,real=window.fetch?window.fetch.bind(window):null;
var here=location.href.replace(/[?#].*$/,"").replace(/[^\\/]*$/,"");
function key(input){var u=typeof input==="string"?input:(input&&input.url)||String(input);
if(u.indexOf(here)===0)u=u.slice(here.length);else if(/^[a-z][a-z0-9+.-]*:/i.test(u)||u.indexOf("//")===0)return null;
try{return decodeURIComponent(new URL(u,"https://export.invalid/"+D).pathname.replace(/^\\/+/,""));}catch(e){return null;}}
function bytes(b){var s=atob(b),a=new Uint8Array(s.length);for(var i=0;i<s.length;i++)a[i]=s.charCodeAt(i);return a;}
window.fetch=function(input,init){var k=key(input);
if(k!==null&&Object.prototype.hasOwnProperty.call(F,k)){
return Promise.resolve(new Response(bytes(F[k].b),{status:200,headers:{"Content-Type":F[k].t}}));}
if(!real)return Promise.reject(new TypeError("fetch is not available"));return real(input,init);};
var X=window.XMLHttpRequest;if(X){var P=X.prototype,open=P.open,send=P.send,header=P.setRequestHeader;
P.open=function(method,url){var k=key(url);this.__f=(k!==null&&Object.prototype.hasOwnProperty.call(F,k))?F[k]:null;
if(!this.__f)return open.apply(this,arguments);};
P.setRequestHeader=function(){if(!this.__f)return header.apply(this,arguments);};
P.send=function(){var f=this.__f;if(!f)return send.apply(this,arguments);var x=this,b=bytes(f.b),t=x.responseType||"",text=null;
function txt(){if(text===null)text=new TextDecoder("utf-8").decode(b);return text;}
var body=t==="arraybuffer"?b.buffer:t==="blob"?new Blob([b],{type:f.t}):t==="json"?(function(){try{return JSON.parse(txt());}catch(e){return null;}})():txt();
function set(n,v){Object.defineProperty(x,n,{configurable:true,get:function(){return v;}});}
setTimeout(function(){set("readyState",4);set("status",200);set("statusText","OK");set("response",body);
if(t===""||t==="text")set("responseText",txt());
x.getResponseHeader=function(n){return /^content-type$/i.test(n)?f.t:null;};
x.getAllResponseHeaders=function(){return "content-type: "+f.t+"\\r\\n";};
["readystatechange","load","loadend"].forEach(function(n){x.dispatchEvent(new ProgressEvent(n,{loaded:b.length,total:b.length,lengthComputable:true}));});},0);};}
})();
</script>"""


class ExportUnavailable(Exception):
    """This way out is not available for this project; the message says why, for the person."""


# -- which files ------------------------------------------------------------------------------------
def is_env(parts: tuple[str, ...]) -> bool:
    """A key file, or anything under a folder named like one (keyfiles.py): it never leaves."""
    return keyfiles.is_key_file(tuple(parts))


def kept(workspace: Path) -> list[PurePosixPath]:
    """The project's files an export may hold, as paths from the project's root."""
    root, found = workspace.resolve(), []
    for path in sorted(root.rglob("*")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if not path.is_file() or path.is_symlink() or LEFT_OUT & set(relative.parts):
            continue
        if is_env(relative.parts) or relative.suffix in (".pyc", ".pyo"):
            continue
        if relative.parts[0] == "sources" and relative.as_posix() != "sources/index.json":
            continue        # the saved text of sources is other people's writing
        found.append(relative)
    return found


def pages(workspace: Path) -> list[str]:
    return [path.as_posix() for path in kept(workspace) if path.suffix.lower() in (".html", ".htm")]


def _attrs(tag: str) -> dict[str, str]:
    return {name.lower(): next(v for v in values if v is not None) if any(v is not None for v in values) else ""
            for name, *values in ATTR.findall(tag)}


def _is_local(ref: str) -> bool:
    ref = ref.strip()
    return bool(ref) and not re.match(r"(?:[a-z][a-z0-9+.-]*:|//|#)", ref, re.I)


def _srcset(value: str) -> list[tuple[str, str]]:
    """A srcset → [(address, descriptor)], read the way the HTML standard reads it: an address runs to
    the next space, so the commas inside a data: address stay part of it; the descriptor keeps its space."""
    found, at, size = [], 0, len(value)
    while at < size:
        while at < size and (value[at].isspace() or value[at] == ","):
            at += 1
        start = at
        while at < size and not value[at].isspace():
            at += 1
        url = value[start:at]
        if not url:
            break
        if url.endswith(","):
            found.append((url.rstrip(","), ""))
            continue
        start, depth = at, 0
        while at < size and (value[at] != "," or depth):
            depth += {"(": 1, ")": -1}.get(value[at], 0)
            at += 1
        descriptor = value[start:at].strip()
        found.append((url, " " + descriptor if descriptor else ""))
    return found


def _rewrite(tag: str, change) -> str:
    """Changes only the values of a tag's attributes: `change(name, value)` gets each value as the
    browser reads it and answers a new one or None. The rest of the tag, its quotes included, is kept
    as written, so an alt text that happens to repeat an address is never touched."""
    def one(match: re.Match) -> str:
        name = match.group(1)
        raw = next((v for v in match.groups()[1:] if v is not None), "")
        new = change(name.lower(), entities.unescape(raw))
        if new is None or new == entities.unescape(raw):
            return match.group(0)
        quote = "'" if match.group(3) is not None else '"'      # kept: the tag may sit in a script's string
        return f"{name}={quote}{entities.escape(new, quote=True)}{quote}"
    head = re.match(r"<[^\s>/]+", tag)
    at = head.end() if head else 0
    return tag[:at] + ATTR.sub(one, tag[at:])


def _host(ref: str) -> str | None:
    match = re.match(r"(?:https?:)?//([^/?#]+)", ref.strip(), re.I)
    return match.group(1).lower() if match else None


def _type(path: PurePosixPath) -> str:
    return DATA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _data_uri(path: Path, relative: PurePosixPath) -> str:
    return f"data:{_type(relative)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class _Page:
    """One page being turned into one file: what it found on the way is kept for the answer.

    `measure`: build it without reading any picture, clip or data file, from their sizes alone. Each
    data: address is only its prefix and `extra` counts the base64 it stands for, so the size comes out
    to the byte and a 300 MB clip costs nothing to describe or to refuse."""

    def __init__(self, workspace: Path, page: str, measure: bool = False):
        self.measure, self.extra = measure, 0
        self.root = workspace.resolve()
        self.page = PurePosixPath(page)
        self.allowed = set(kept(workspace))
        self.inlined: set[PurePosixPath] = set()
        self.external: set[str] = set()
        self.notes: list[str] = []
        self.missing: set[str] = set()
        self.unheld: set[str] = set()
        self.scripted: set[str] = set()
        self.blocked: str | None = None

    def find(self, ref: str, base: PurePosixPath, quiet: bool = False) -> PurePosixPath | None:
        """A reference in a file under `base` → the project file it names, or None (noted when it
        names nothing, unless `quiet`)."""
        clean = re.split(r"[?#]", ref.strip(), maxsplit=1)[0]
        if not clean:
            return None
        parts: list[str] = [] if clean.startswith("/") else list(base.parts)
        for part in clean.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return None      # points outside the project
                parts.pop()
            else:
                parts.append(part)
        target = PurePosixPath(*parts) if parts else None
        if target is None or target not in self.allowed:
            if target is not None and not quiet and not re.search(r"\$\{|\{\{|<%", ref):   # not a template string
                self.missing.add(clean)
            return None
        return target

    def uri(self, target: PurePosixPath) -> str:
        if not self.measure:
            return _data_uri(self.root / target, target)
        self.extra += 4 * (((self.root / target).stat().st_size + 2) // 3)
        return f"data:{_type(target)};base64,"

    def text(self, target: PurePosixPath) -> str:
        """A stylesheet or script, read to see what it names; one that alone is over the limit is not read."""
        if self.measure and (size := (self.root / target).stat().st_size) > MAX_BYTES:
            self.extra += size
            return ""
        return (self.root / target).read_text("utf-8", "replace")

    def address(self, ref: str, base: PurePosixPath) -> str:
        """A picture's, clip's or icon's address → a data: address when it names a project file; any
        other address is kept as it is (another site's is noted as still needed)."""
        if not _is_local(ref):
            host = _host(ref)
            if host:
                self.external.add(host)
            return ref
        target = self.find(ref, base)
        if target is None:
            return ref
        self.inlined.add(target)
        return self.uri(target)

    def outside(self, code: str) -> None:
        """The other sites a script imports from (a module's imports, an import map): still needed."""
        for ref in re.findall(r"""["'](https?://[^"'\s]+)["']""", code):
            host = _host(ref)
            if host:
                self.external.add(host)

    def css(self, text: str, base: PurePosixPath, depth: int = 0) -> str:
        def imported(match: re.Match) -> str:
            ref = match.group(1)
            if not _is_local(ref):
                self.external.add(_host(ref) or ref)
                return match.group(0)
            target = self.find(ref, base)
            if target is None or depth >= 3:
                return match.group(0)
            self.inlined.add(target)
            return self.css(self.text(target), target.parent, depth + 1)

        def url(match: re.Match) -> str:
            ref = next(group for group in match.groups() if group is not None)
            if not _is_local(ref):
                host = _host(ref)
                if host:
                    self.external.add(host)
                return match.group(0)
            target = self.find(ref, base)
            if target is None:
                return match.group(0)
            self.inlined.add(target)
            return f'url("{self.uri(target)}")'

        return CSS_URL.sub(url, CSS_IMPORT.sub(imported, text))

    def markup_in(self, code: str, base: PurePosixPath) -> None:
        """A script that builds markup naming the project's pictures (`'<img src="a.png">'`): those cannot
        go inside without editing the script, which could break it, so they are only noted."""
        for tag in MEDIA.findall(code) + STYLED.findall(code):
            for name, value in _attrs(tag.replace('\\"', '"').replace("\\'", "'")).items():
                if name == "srcset":
                    refs = [url for url, _ in _srcset(value)]
                elif name == "style":
                    refs = [next(g for g in m.groups() if g is not None) for m in CSS_URL.finditer(value)]
                elif name in ("src", "poster", "href", "data"):
                    refs = [value]
                else:
                    continue
                for ref in refs:
                    ref = ref.strip("\\\"'` ")
                    if _is_local(ref) and (target := self.find(ref, base, quiet=True)) is not None:
                        self.scripted.add(target.as_posix())

    def where(self, html: str) -> PurePosixPath:
        """The folder the page's relative addresses start from: its own, or a `<base href>` inside the
        project. A base elsewhere (another site, outside the project) is noted and the page's folder kept."""
        here = self.page.parent
        found = next((m.group(0) for part, text in _parts(html) if part == "tags" for m in [BASE.search(text)] if m), None)
        href = _attrs(found).get("href", "").strip() if found else ""
        if not href:
            return here
        folder = href.split("?")[0].split("#")[0]
        folder = folder[:folder.rfind("/") + 1]
        parts: list[str] | None = [] if folder.startswith("/") else list(here.parts)
        for part in folder.split("/"):
            if part == "..":
                if not parts:
                    parts = None
                    break
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        if not _is_local(href) or parts is None:
            self.notes.append(f"The page sets <base href=\"{href}\">, which is not a folder of the project: its "
                              "relative addresses were read from the page's own folder, so they may differ from what "
                              "a browser loads. Use the folder.")
            return here
        return PurePosixPath(*parts) if parts else PurePosixPath(".")

    def build(self) -> str:
        html = (self.root / self.page).read_text("utf-8", "replace")
        base = self.where(html)

        def link(match: re.Match) -> str:
            tag, attrs = match.group(0), _attrs(match.group(0))
            rel, href = attrs.get("rel", "").lower().split(), attrs.get("href", "")
            if not href:
                return tag
            icon = any("icon" in word for word in rel)       # icon, apple-touch-icon, mask-icon
            if not _is_local(href):
                if "stylesheet" in rel or icon or "preload" in rel:
                    self.external.add(_host(href) or href)
                return tag
            target = self.find(href, base)
            if target is None:
                return tag
            if "stylesheet" in rel:
                self.inlined.add(target)
                media = f' media="{attrs["media"]}"' if attrs.get("media") else ""
                sheet = self.css(self.text(target), target.parent)
                return f"<style{media}>\n{sheet.replace('</style', '<\\/style')}\n</style>"
            if icon:
                return _rewrite(tag, lambda name, value: self.address(value, base) if name == "href" else None)
            return tag

        def script(whole: str, attrs_text: str, body: str) -> str:
            attrs = _attrs("<script " + attrs_text + ">")
            src = attrs.get("src", "")
            kind = attrs.get("type", "").lower()
            if not src:
                if kind in ("module", "importmap"):
                    self.outside(body)
                if kind == "module" and LOCAL_IMPORT.search(body):
                    self.blocked = ("The page's own script imports other files of the project, which a browser "
                                    "will not load for a page opened from a file. Use the folder.")
                self.markup_in(body, base)
                return whole
            if not _is_local(src):
                self.external.add(_host(src) or src)
                return whole
            target = self.find(src, base)
            if target is None:
                return whole
            code = self.text(target)
            self.outside(code)
            if kind == "module" and LOCAL_IMPORT.search(code):
                self.blocked = (f"{target} is a module that imports other files of the project, which one file "
                                "cannot hold. Use the folder.")
                return whole
            self.markup_in(code, base)
            self.inlined.add(target)
            kept_attrs = re.sub(r"""\s*\bsrc\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", "", attrs_text, flags=re.I)
            return f"<script{kept_attrs}>\n{re.sub(r'</(script)', r'<\\/\1', code, flags=re.I)}\n</script>"

        def media(match: re.Match) -> str:
            # A screen at 2x (every Mac and phone) loads the srcset candidate, not src: each one goes inside,
            # with its descriptor. Browsers take data: addresses in a srcset (an address runs to a space).
            element = re.match(r"<([a-z]+)", match.group(0), re.I).group(1).lower()

            def change(name: str, value: str) -> str | None:
                if not value.strip():
                    return None
                if name == "srcset":
                    return ", ".join(self.address(url, base) + descriptor for url, descriptor in _srcset(value))
                if name in ("src", "poster") or (element == "object" and name == "data") \
                        or (element == "image" and name in ("href", "xlink:href")):
                    return self.address(value, base)
                return None
            return _rewrite(match.group(0), change)

        def styled(match: re.Match) -> str:
            return _rewrite(match.group(0), lambda name, value: self.css(value, base) if name == "style" else None)

        def use(match: re.Match) -> str:
            # An SVG <use> of another file cannot be loaded by a page opened from a file, nor from a data:
            # address (browsers refuse both), so it is said rather than changed.
            for name, value in _attrs(match.group(0)).items():
                if name in ("href", "xlink:href") and _is_local(value) and not value.startswith("#"):
                    target = self.find(value, base)
                    if target is not None:
                        self.unheld.add(target.as_posix())
            return match.group(0)

        # Tags are changed only where they are tags: never inside a script, a style, a textarea, a title or a
        # comment, where `<div style=\"…\">` is a string of a script and a rewrite would break it.
        out, anchors = [], []
        for part, text in _parts(html):
            if part == "tags":
                for pattern, change in ((LINK, link), (MEDIA, media), (STYLED, styled), (USE, use)):
                    text = pattern.sub(change, text)
                anchors += ANCHOR.findall(text)
            elif part == "script":
                match = SCRIPT.fullmatch(text)
                text = script(text, match.group(1), match.group(2)) if match else text
            elif part == "style":
                opening, inside, closing = TEXT.fullmatch(text).group(1, 3, 4)
                text = opening + self.css(inside, base) + closing
            out.append(text)
        html = "".join(out)

        others = set()
        for tag in anchors:
            ref = _attrs(tag).get("href") or _attrs(tag).get("src") or ""
            if _is_local(ref) and re.search(r"\.html?(?:[?#]|$)", ref, re.I):
                target = self.find(ref, base)
                if target is not None and target != self.page:
                    others.add(target.as_posix())
        if others:
            self.notes.append(f"It links to {len(others)} other page{'s' if len(others) != 1 else ''} of the project "
                              f"({', '.join(sorted(others)[:4])}{'…' if len(others) > 4 else ''}), which are not in "
                              "the file. Use the folder to keep them together.")
        if self.unheld:
            self.notes.append(f"Its SVG <use> names {', '.join(sorted(self.unheld)[:4])}, which a page opened from "
                              "a file cannot load: those drawings will be missing. Use the folder, or put the "
                              "drawing inside the page.")
        if self.scripted:
            self.notes.append(f"A script of the page builds markup naming {', '.join(sorted(self.scripted)[:4])}"
                              f"{'…' if len(self.scripted) > 4 else ''}: those are not inside the file (changing "
                              "the script could break it), so they will be missing when it is opened. Use the folder.")
        for name in sorted(self.missing)[:5]:
            self.notes.append(f"The page names “{name}”, which is not among the project's files.")

        files = {}
        for path in sorted(self.allowed):
            if path.suffix.lower() in DATA_TYPES and path not in self.inlined and path != self.page:
                size = (self.root / path).stat().st_size
                if self.measure:        # base64 has no character JSON escapes: its length is all it adds
                    self.extra += 4 * ((size + 2) // 3)
                    files[path.as_posix()] = {"t": _type(path), "b": "", "n": size}
                else:
                    files[path.as_posix()] = {"t": _type(path), "n": size,
                                              "b": base64.b64encode((self.root / path).read_bytes()).decode("ascii")}
        self.embedded = {"files": len(files), "bytes": sum(item.pop("n") for item in files.values())}
        if files:
            folder = "" if str(base) == "." else base.as_posix() + "/"
            shim = SHIM.replace("__FILES__", json.dumps(files, separators=(",", ":")).replace("<", "\\u003c")) \
                       .replace("__DIR__", json.dumps(folder))
            head = re.search(r"<head\b[^>]*>", html, re.I) or re.search(r"<html\b[^>]*>", html, re.I)
            at = head.end() if head else (re.match(r"\s*<!doctype[^>]*>", html, re.I) or re.match("", html)).end()
            html = html[:at] + "\n" + shim + html[at:]
        stamp = datetime.date.today().isoformat()
        banner = (f"<!-- Exported from Studio on {stamp} as one file: its styles, scripts, pictures and data are inside."
                  + (f" It still loads from: {', '.join(sorted(self.external))}." if self.external else "") + " -->\n")
        doctype = re.match(r"\s*<!doctype[^>]*>\s*", html, re.I)
        at = doctype.end() if doctype else 0
        return html[:at] + banner + html[at:]


def _parts(html: str) -> list[tuple[str, str]]:
    """The page cut into ("tags", markup), ("script", element), ("style", element) and ("text", a
    comment or a textarea, title or xmp element) in order; joined again they are the page."""
    found, at = [], 0
    for match in TEXT.finditer(html):
        found.append(("tags", html[at:match.start()]))
        name = (match.group(2) or "").lower()
        found.append((name if name in ("script", "style") else "text", match.group(0)))
        at = match.end()
    found.append(("tags", html[at:]))
    return found


def one_file(workspace: Path, page: str = "index.html", build: bool = True) -> dict:
    """→ {available, reason, html, bytes, inlined, embedded, external, notes} for one page. It is
    measured first from file sizes, and refused past the limit before a byte is put inside; `build`
    False stops there (the dialog only shows what the file would be)."""
    empty = {"available": False, "reason": None, "html": None, "bytes": 0, "inlined": 0,
             "embedded": {"files": 0, "bytes": 0}, "external": [], "notes": []}
    if (workspace / "studio.json").is_file():
        return {**empty, "reason": "This project has its own back end, and its pages need that server. "
                                   "Use the folder: it says how to run the app."}
    if PurePosixPath(page) not in set(kept(workspace)) or PurePosixPath(page).suffix.lower() not in (".html", ".htm"):
        return {**empty, "reason": f"There is no page at {page} in this project."}
    measured = _Page(workspace, page, measure=True)
    size = len(measured.build().encode("utf-8")) + measured.extra
    answer = {**empty, "inlined": len(measured.inlined), "embedded": measured.embedded,
              "external": sorted(measured.external), "notes": measured.notes, "bytes": size}
    if measured.blocked:
        return {**answer, "reason": measured.blocked}
    if size > MAX_BYTES:
        return {**answer, "reason": f"As one file this page would be {size / 1e6:.0f} MB (the limit is "
                                    f"{MAX_BYTES // 1_000_000} MB). Use the folder."}
    if not build:
        return {**answer, "available": True}
    html = _Page(workspace, page).build()
    return {**answer, "available": True, "html": html, "bytes": len(html.encode("utf-8"))}


# -- a folder to host or run --------------------------------------------------------------------------
TOOLS = {"uv": "uv (https://docs.astral.sh/uv/): it fetches Python and the app's packages by itself",
         "npm": "Node.js with npm (https://nodejs.org/)", "npx": "Node.js with npm (https://nodejs.org/)",
         "node": "Node.js (https://nodejs.org/)", "python": "Python 3", "python3": "Python 3",
         "deno": "Deno (https://deno.com/)", "bun": "Bun (https://bun.sh/)"}
NEEDS = {
    "STUDIO_MODEL_URL": "the studio's model address (STUDIO_MODEL_URL, STUDIO_TOKEN): its calls to a language model "
                        "will fail until you point it at a model API of your own",
    "STUDIO_DATA_URL": "a data source attached in the studio (STUDIO_DATA_URL): it will have to connect to that "
                       "database itself",
    "STUDIO_PROJECT_URL": "the project's own address in the studio (STUDIO_PROJECT_URL)",
    "STUDIO_PROJECT_ID": "the project's id in the studio (STUDIO_PROJECT_ID)",
}
CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".sh", ".toml", ".html"}


def _run_command(workspace: Path) -> str | None:
    try:
        run = json.loads((workspace / "studio.json").read_text("utf-8")).get("run")
    except (OSError, ValueError, AttributeError):
        return None
    return run.strip() if isinstance(run, str) and run.strip() else None


def folder_info(workspace: Path, project: dict) -> dict:
    """→ {available, reason, files, bytes, run, tools, needs}."""
    if project.get("attach"):
        return {"available": False, "reason": "This project was brought in from a folder on this computer, which is "
                                              "its own repository. Everything (.zip) still gives you its files.",
                "files": 0, "bytes": 0, "run": None, "tools": [], "needs": []}
    files = kept(workspace)
    root = workspace.resolve()
    is_app = (workspace / "studio.json").is_file()
    run = _run_command(workspace) if is_app else None
    tools, needs = [], []
    if run:
        try:
            words = shlex.split(run)
        except ValueError:      # a command the shell would not take either: name its first word as written
            words = run.split()
        first = (words or [""])[0].rsplit("/", 1)[-1]
        tools = [TOOLS.get(first, f"“{first}” on your PATH")]
        text = ""
        for path in files:
            if path.suffix.lower() in CODE and (root / path).stat().st_size < 2_000_000:
                text += (root / path).read_text("utf-8", "replace")
        needs = [sentence for name, sentence in NEEDS.items() if name in text]
    return {"available": True, "reason": None, "files": len(files) + 1 + bool(run),
            "sources": PurePosixPath("sources/index.json") in files,
            "bytes": sum((root / path).stat().st_size for path in files), "run": run, "tools": tools, "needs": needs,
            "app": is_app}


def readme(project: dict, info: dict, page_list: list[str]) -> str:
    stamp = datetime.date.today().isoformat()
    lines = [f"# {project.get('name') or 'Exported project'}", "",
             f"Exported from Studio on {stamp}. This folder is the whole project; it needs nothing from the studio "
             "to open, host or run.", ""]
    if info.get("app"):
        lines += ["## Run it", ""]
        if info["run"]:
            lines += ["You need: " + "; ".join(info["tools"]) + ".", "", "```sh", "./run.sh            # then open http://127.0.0.1:8000/",
                      "PORT=9000 ./run.sh  # another port", "```", "",
                      "`run.sh` runs the same command the studio ran (it is in `studio.json`):", "",
                      "```sh", info["run"], "```", "",
                      "It listens on this computer only (127.0.0.1). To put it on a server, run it there behind "
                      "whatever that host uses to serve apps, and give it the port in `PORT`."]
        else:
            lines += ["`studio.json` says how the studio started this app; start it the same way."]
        if info["needs"]:
            lines += ["", "## What it used from the studio", "", "These will not be there outside it:", ""]
            lines += [f"- {need}." for need in info["needs"]]
        lines += ["", "## What is not here", "",
                  "Anything the app saved while it ran in the studio (`state/`) was left out: it starts empty."]
    else:
        main = "index.html" if "index.html" in page_list else (page_list[0] if page_list else "index.html")
        lines += ["## Open it", "",
                  f"Open `{main}` in a browser. If the page loads data files and shows nothing when opened from a "
                  "double click, serve the folder instead (a browser does not let a page read files next to it):", "",
                  "```sh", "python3 -m http.server 8000   # in this folder, then open http://127.0.0.1:8000/", "```", "",
                  "## Host it", "",
                  "It is a static site: upload the folder as it is to any static host (GitHub Pages, Netlify, "
                  "Cloudflare Pages, an S3 bucket, any web server's public folder). There is nothing to build."]
        if len(page_list) > 1:
            lines += ["", f"Pages: {', '.join(f'`{name}`' for name in page_list[:12])}."]
    lines += ["", "## Left out on purpose", "", "Working notes, attachments and version history."
              + (" Also the saved text of the sources the page cites: that text is other people's writing, and "
                 "`sources/index.json` lists each source with its address." if info.get("sources") else "")]
    return "\n".join(lines) + "\n"


RUN_SH = """#!/bin/sh
# Starts this app the way the studio did. PORT is 8000 unless you set it.
cd "$(dirname "$0")" || exit 1
PORT="${PORT:-8000}"; export PORT
HOST="${HOST:-127.0.0.1}"; export HOST
echo "Starting on http://127.0.0.1:$PORT/  (Ctrl-C stops it)"
exec sh -c __RUN__
"""


def folder_zip(workspace: Path, project: dict, target: Path) -> None:
    """Writes the archive to `target`, a file, one project file at a time: a project with a rendered
    video of hundreds of MB never sits in memory whole."""
    info = folder_info(workspace, project)
    if not info["available"]:
        raise ExportUnavailable(info["reason"])
    root = workspace.resolve()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in kept(workspace):
            archive.write(root / path, path.as_posix())
        note = zipfile.ZipInfo(README, date_time=datetime.datetime.now().timetuple()[:6])
        note.external_attr = 0o644 << 16
        note.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(note, readme(project, info, pages(workspace)))
        if info["run"]:
            entry = zipfile.ZipInfo("run.sh", date_time=datetime.datetime.now().timetuple()[:6])
            entry.external_attr = 0o755 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, RUN_SH.replace("__RUN__", shlex.quote(info["run"])))


def describe(workspace: Path, project: dict, page: str = "index.html") -> dict:
    """What `GET …/export` answers."""
    listing = pages(workspace)
    html = one_file(workspace, page, build=False)       # sizes only: opening the dialog builds nothing
    html.pop("html", None)
    folder = folder_info(workspace, project)
    return {"name": project.get("name"), "app": (workspace / "studio.json").is_file(), "pages": listing, "page": page,
            "html": html, "folder": {key: value for key, value in folder.items() if key not in ("app", "sources")}}
