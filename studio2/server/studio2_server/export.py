"""Export: take a page or an app out of the studio (CONTRACT.md, "Export").

One HTML file with its styles, scripts, pictures and data inside; or a folder to host or run, with
a note on how. Nothing here publishes anything, and what an export leaves out or still needs is
always said in its answer.
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import mimetypes
import re
import shlex
import zipfile
from pathlib import Path, PurePosixPath

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
STYLE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.I | re.S)
MEDIA = re.compile(r"<(?:img|source|video|audio|track|embed|input)\b[^>]*>", re.I)
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
def kept(workspace: Path) -> list[PurePosixPath]:
    """The project's files an export may hold, as paths from the project's root."""
    root, found = workspace.resolve(), []
    for path in sorted(root.rglob("*")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if not path.is_file() or path.is_symlink() or LEFT_OUT & set(relative.parts):
            continue
        if any(part.startswith(".env") for part in relative.parts) or relative.suffix in (".pyc", ".pyo"):
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


def _host(ref: str) -> str | None:
    match = re.match(r"(?:https?:)?//([^/?#]+)", ref.strip(), re.I)
    return match.group(1).lower() if match else None


def _type(path: PurePosixPath) -> str:
    return DATA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _data_uri(path: Path, relative: PurePosixPath) -> str:
    return f"data:{_type(relative)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class _Page:
    """One page being turned into one file: what it found on the way is kept for the answer."""

    def __init__(self, workspace: Path, page: str):
        self.root = workspace.resolve()
        self.page = PurePosixPath(page)
        self.allowed = set(kept(workspace))
        self.inlined: set[PurePosixPath] = set()
        self.external: set[str] = set()
        self.notes: list[str] = []
        self.missing: set[str] = set()
        self.blocked: str | None = None

    def find(self, ref: str, base: PurePosixPath) -> PurePosixPath | None:
        """A reference in a file under `base` → the project file it names, or None (noted when it
        names nothing)."""
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
            if target is not None and not re.search(r"\$\{|\{\{|<%", ref):     # not a template string in a script
                self.missing.add(clean)
            return None
        return target

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
            return self.css((self.root / target).read_text("utf-8", "replace"), target.parent, depth + 1)

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
            return f'url("{_data_uri(self.root / target, target)}")'

        return CSS_URL.sub(url, CSS_IMPORT.sub(imported, text))

    def build(self) -> str:
        base = self.page.parent
        html = (self.root / self.page).read_text("utf-8", "replace")

        def link(match: re.Match) -> str:
            tag, attrs = match.group(0), _attrs(match.group(0))
            rel, href = attrs.get("rel", "").lower().split(), attrs.get("href", "")
            if not href:
                return tag
            if not _is_local(href):
                if "stylesheet" in rel or "icon" in rel or "preload" in rel:
                    self.external.add(_host(href) or href)
                return tag
            target = self.find(href, base)
            if target is None:
                return tag
            if "stylesheet" in rel:
                self.inlined.add(target)
                media = f' media="{attrs["media"]}"' if attrs.get("media") else ""
                sheet = self.css((self.root / target).read_text("utf-8", "replace"), target.parent)
                return f"<style{media}>\n{sheet.replace('</style', '<\\/style')}\n</style>"
            if "icon" in rel:
                self.inlined.add(target)
                return tag.replace(href, _data_uri(self.root / target, target))
            return tag

        def script(match: re.Match) -> str:
            attrs_text, body = match.group(1), match.group(2)
            attrs = _attrs("<script " + attrs_text + ">")
            src = attrs.get("src", "")
            kind = attrs.get("type", "").lower()
            if not src:
                if kind in ("module", "importmap"):
                    self.outside(body)
                if kind == "module" and LOCAL_IMPORT.search(body):
                    self.blocked = ("The page's own script imports other files of the project, which a browser "
                                    "will not load for a page opened from a file. Use the folder.")
                return match.group(0)
            if not _is_local(src):
                self.external.add(_host(src) or src)
                return match.group(0)
            target = self.find(src, base)
            if target is None:
                return match.group(0)
            code = (self.root / target).read_text("utf-8", "replace")
            self.outside(code)
            if kind == "module" and LOCAL_IMPORT.search(code):
                self.blocked = (f"{target} is a module that imports other files of the project, which one file "
                                "cannot hold. Use the folder.")
                return match.group(0)
            self.inlined.add(target)
            kept_attrs = re.sub(r"""\s*\bsrc\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", "", attrs_text, flags=re.I)
            return f"<script{kept_attrs}>\n{re.sub(r'</(script)', r'<\\/\1', code, flags=re.I)}\n</script>"

        def media(match: re.Match) -> str:
            tag, attrs = match.group(0), _attrs(match.group(0))
            for name in ("src", "poster"):
                ref = attrs.get(name, "")
                if not ref:
                    continue
                if not _is_local(ref):
                    host = _host(ref)
                    if host:
                        self.external.add(host)
                    continue
                target = self.find(ref, base)
                if target is not None:
                    self.inlined.add(target)
                    tag = tag.replace(ref, _data_uri(self.root / target, target))
            return tag

        html = LINK.sub(link, html)
        html = SCRIPT.sub(script, html)
        html = STYLE.sub(lambda m: m.group(1) + self.css(m.group(2), base) + m.group(3), html)
        html = MEDIA.sub(media, html)

        others = set()
        for tag in ANCHOR.findall(html):
            ref = _attrs(tag).get("href") or _attrs(tag).get("src") or ""
            if _is_local(ref) and re.search(r"\.html?(?:[?#]|$)", ref, re.I):
                target = self.find(ref, base)
                if target is not None and target != self.page:
                    others.add(target.as_posix())
        if others:
            self.notes.append(f"It links to {len(others)} other page{'s' if len(others) != 1 else ''} of the project "
                              f"({', '.join(sorted(others)[:4])}{'…' if len(others) > 4 else ''}), which are not in "
                              "the file. Use the folder to keep them together.")
        for name in sorted(self.missing)[:5]:
            self.notes.append(f"The page names “{name}”, which is not among the project's files.")

        files = {}
        for path in sorted(self.allowed):
            if path.suffix.lower() in DATA_TYPES and path not in self.inlined and path != self.page:
                files[path.as_posix()] = {"t": _type(path),
                                          "b": base64.b64encode((self.root / path).read_bytes()).decode("ascii")}
        self.embedded = {"files": len(files), "bytes": sum(len(item["b"]) * 3 // 4 for item in files.values())}
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


def one_file(workspace: Path, page: str = "index.html") -> dict:
    """→ {available, reason, html, bytes, inlined, embedded, external, notes} for one page."""
    empty = {"available": False, "reason": None, "html": None, "bytes": 0, "inlined": 0,
             "embedded": {"files": 0, "bytes": 0}, "external": [], "notes": []}
    if (workspace / "studio.json").is_file():
        return {**empty, "reason": "This project has its own back end, and its pages need that server. "
                                   "Use the folder: it says how to run the app."}
    if PurePosixPath(page) not in set(kept(workspace)) or PurePosixPath(page).suffix.lower() not in (".html", ".htm"):
        return {**empty, "reason": f"There is no page at {page} in this project."}
    built = _Page(workspace, page)
    html = built.build()
    size = len(html.encode("utf-8"))
    answer = {**empty, "inlined": len(built.inlined), "embedded": built.embedded, "external": sorted(built.external),
              "notes": built.notes, "bytes": size}
    if built.blocked:
        return {**answer, "reason": built.blocked}
    if size > MAX_BYTES:
        return {**answer, "reason": f"As one file this page would be {size / 1e6:.0f} MB (the limit is "
                                    f"{MAX_BYTES // 1_000_000} MB). Use the folder."}
    return {**answer, "available": True, "html": html}


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


def folder_zip(workspace: Path, project: dict) -> bytes:
    info = folder_info(workspace, project)
    if not info["available"]:
        raise ExportUnavailable(info["reason"])
    root, out = workspace.resolve(), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
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
    return out.getvalue()


def describe(workspace: Path, project: dict, page: str = "index.html") -> dict:
    """What `GET …/export` answers."""
    listing = pages(workspace)
    html = one_file(workspace, page)
    html.pop("html", None)
    folder = folder_info(workspace, project)
    return {"name": project.get("name"), "app": (workspace / "studio.json").is_file(), "pages": listing, "page": page,
            "html": html, "folder": {key: value for key, value in folder.items() if key not in ("app", "sources")}}
