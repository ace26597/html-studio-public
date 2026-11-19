"""Context from this computer (CONTRACT.md, "Context from this computer: folders, files and addresses").

A project can name a folder, a file or the address of a running app as context for the agent.
Nothing is copied and nothing there is written: `inspect` only reads (git on the person's folder
runs the way attach.py runs it), and the agent is told the items are read-only. This module holds
the reading, the rules for what may be context, and the words the agent is given; studio.py keeps
the list in the project record.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import attach as attaching
from . import keyfiles
from .config import now_iso

MAX_ITEMS = 12
LABEL_MAX, NOTE_MAX, ADDRESS_MAX = 60, 500, 2000
TOP_MAX = 40                # names from the top of a folder, in an inspection and in the agent's facts
COUNT_LIMIT = 50_000        # files counted before the count is called partial
COUNT_SECONDS = 20.0        # a folder that cannot be counted in this time is added without a count
LARGE_FILES = 5_000         # above this a note says the agent searches the folder
PROBE_SECONDS, STATE_SECONDS = 5.0, 2.0
TITLE_BYTES = 64 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_PORTS = {"http": 80, "https": 443}
# `localhost:3000`, `127.0.0.1:8000/admin`, `[::1]:5173`: an address typed without its scheme.
HOST_PORT = re.compile(r"^(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):\d{1,5}(?:[/?#].*)?$")
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
WHAT_TO_TYPE = "Type a folder's full path (~/dev/my-app) or an address (http://localhost:3000)."


class ContextError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# -- what was typed ------------------------------------------------------------------------
def classify(value: str) -> tuple[str, str]:
    """→ ("path" | "address", the text to read as one). ContextError when it is neither."""
    text = str(value or "").strip()
    if text.startswith(("/", "~")):
        return "path", text
    if "://" in text:
        return "address", text
    if HOST_PORT.match(text):
        return "address", f"http://{text}"
    raise ContextError("invalid_request", WHAT_TO_TYPE)


def check_address(text: str) -> str:
    """An http or https address, normalised (no fragment, `/` when there is no path)."""
    text = str(text or "").strip()
    if len(text) > ADDRESS_MAX:
        raise ContextError("context_refused", f"This address is longer than {ADDRESS_MAX:,} characters.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ContextError("context_refused", "This is not an address the studio can read. " + WHAT_TO_TYPE) from None
    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise ContextError("context_refused", "Only http and https addresses can be context.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ContextError("context_refused", "Leave the user name and password out of the address: "
                                              "the studio never keeps one.")
    host = (parts.hostname or "").lower()
    if not host:
        raise ContextError("context_refused", "This address has no host. " + WHAT_TO_TYPE)
    scheme = parts.scheme.lower()
    shown = f"[{host}]" if ":" in host else host
    if port and port != DEFAULT_PORTS[scheme]:
        shown += f":{port}"
    return f"{scheme}://{shown}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")


def origin(address: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) of an http(s) address, or None when it is not one."""
    try:
        parts = urlsplit(str(address or "").strip())
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        port = parts.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    return (scheme, host, port) if scheme in DEFAULT_PORTS and host and port else None


def host_label(address: str) -> str:
    parts = urlsplit(address)
    return parts.netloc or address


def this_computer(address: str) -> bool:
    found = origin(address)
    return bool(found and found[1] in LOOPBACK)


def page_under(url: str, items: list[dict]) -> str | None:
    """`url`, normalised, when it is a page under one of the items' addresses (the same scheme,
    host and port), else None. What `look_at_page` may open beyond the project's own page."""
    try:
        target = check_address(url)
    except ContextError:
        return None
    allowed = {origin(item["address"]) for item in items if item.get("kind") == "address" and item.get("address")}
    return target if origin(target) in allowed else None


# -- reading a folder or a file ------------------------------------------------------------
def _git(folder: Path) -> dict | None:
    """Branch, head and last subject when the folder is the top of a work tree (read only)."""
    try:
        code, top = attaching._git_read_bytes(folder, ["rev-parse", "--show-toplevel"], timeout=10)
        if code != 0 or Path(top.decode("utf-8", "replace").strip()).resolve() != folder:
            return None
        said = {}
        for key, args in (("head", ["rev-parse", "-q", "--verify", "HEAD"]),
                          ("branch", ["symbolic-ref", "-q", "--short", "HEAD"]),
                          ("subject", ["log", "-1", "--format=%s"])):
            _code, out = attaching._git_read_bytes(folder, args, timeout=10)
            said[key] = out.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"branch": said["branch"] or None, "head": said["head"] or None, "subject": said["subject"]}


def _count(folder: Path, tracked: bool) -> tuple[int | None, int | None, bool]:
    """→ (files, bytes, partial): what git tracks, or what a plain folder's .gitignore keeps.
    (None, None, True) when it cannot be counted in COUNT_SECONDS."""
    deadline = time.monotonic() + COUNT_SECONDS
    try:
        if tracked:
            _code, out = attaching._git_read_bytes(folder, ["ls-files", "-z"], timeout=COUNT_SECONDS)
            names = [name for name in out.decode("utf-8", "replace").split("\0") if name]
            partial = len(names) > COUNT_LIMIT
            names = names[:COUNT_LIMIT]
        else:
            names, partial = attaching._plain_files(folder, timeout=COUNT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None, None, True
    total = 0
    for index, name in enumerate(names):
        if index % 2000 == 0 and time.monotonic() > deadline:
            return None, None, True
        try:
            total += (folder / name).lstat().st_size
        except OSError:
            continue
    return len(names), total, partial


def _hidden_last(names: list[str]) -> list[str]:
    return sorted(names, key=lambda name: (name.startswith("."), name))


def top_names(folder: Path) -> tuple[list[str], int, list[str]]:
    """→ (the first TOP_MAX names: folders first, each ending in `/`, then files, names that start
    with a dot last in each; how many more; the key files at the top, by name). Key files and
    what is not the code (`.git`, dependencies, build output, caches) are not among the names."""
    folders, files, keys = [], [], []
    try:
        for entry in folder.iterdir():
            if entry.name in attaching.SKIP_DIRS:
                continue
            if keyfiles.is_key_name(entry.name):
                keys.append(entry.name)
            elif entry.is_dir():
                folders.append(entry.name + "/")
            else:
                files.append(entry.name)
    except OSError:
        return [], 0, []
    names = _hidden_last(folders) + _hidden_last(files)
    return names[:TOP_MAX], max(0, len(names) - TOP_MAX), sorted(keys)


def hidden_in_home(path: Path, home: Path) -> str | None:
    """`~/.name` when the path is a hidden file or folder at the top of the home folder, or is
    inside one (`~/.ssh`, `~/.aws`, `~/.config/…`, `~/.zsh_history`): where keys and settings live."""
    try:
        first = path.relative_to(home.resolve()).parts[:1]
    except ValueError:
        return None
    return f"~/{first[0]}" if first and first[0].startswith(".") else None


def _hidden_refusal(path: Path, home: Path) -> str | None:
    place = hidden_in_home(path, home)
    if place is None:
        return None
    return (f"This is in a hidden part of your home folder ({place}), where keys and settings live. "
            "The studio does not hand those to the agent as context.")


def folder_refusal(folder: Path, protected: list[Path], home: Path) -> str | None:
    choose = "choose the folder of the code itself."
    if folder == Path(folder.anchor) or os.path.ismount(folder):
        return f"This is the top of a disk; {choose}"
    real_home = home.resolve()
    if folder == real_home:
        return f"This is your home folder; {choose}"
    if folder in real_home.parents:
        return f"This folder holds your whole home folder; {choose}"
    for item in protected:
        if folder in item.resolve().parents:     # a folder that holds the studio; one inside it is fine
            return f"This folder holds the studio's own files; {choose}"
    return None


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def inspect_path(raw: str, protected: list[Path], home: Path, shared: bool = False) -> dict:
    typed = os.path.expanduser(raw.strip())
    if not os.path.isabs(typed):
        raise ContextError("invalid_request", WHAT_TO_TYPE)
    path = Path(os.path.normpath(typed))
    result: dict = {"kind": "folder", "path": str(path), "address": None, "label": path.name or str(path),
                    "refused": None, "found": None, "notes": []}
    if not os.path.lexists(path):
        result["refused"] = "There is nothing at this path."
        return result
    path = path.resolve()
    result.update(path=str(path), label=path.name or str(path))
    if path.is_file():
        result["kind"] = "file"
    if (reason := _hidden_refusal(path, home)):
        result["refused"] = reason
    elif path.is_file():
        if keyfiles.is_key_file(path.parts):
            result["refused"] = "This is a key file. The studio does not hand key files to the agent as context."
        elif not os.access(path, os.R_OK):
            result["refused"] = "The studio cannot read this file."
        else:
            result["found"] = {"bytes": path.stat().st_size}
    elif not path.is_dir():
        result["refused"] = "This is neither a file nor a folder."
    elif not os.access(path, os.R_OK | os.X_OK):
        result["refused"] = "The studio cannot read this folder."
    elif (reason := folder_refusal(path, protected, home)):
        result["refused"] = reason      # before reading (and counting) a whole disk or home folder
    else:
        info = _git(path)
        files, size, partial = _count(path, info is not None)
        top, more, keys = top_names(path)
        result["found"] = {"git": info, "framework": attaching.detect(path)["framework"], "files": files,
                           "bytes": size, "partial": partial, "key_files": keys, "top": top, "top_more": more}
        if files is None or partial or files > LARGE_FILES:
            many = "more than it could count" if files is None else \
                f"more than {COUNT_LIMIT:,} files" if partial else _plural(files, "file")
            result["notes"].append(f"A large folder ({many}): the agent searches it and reads the files a "
                                   "question needs. It does not read it whole.")
        if keys:
            result["notes"].append(f"Key files at its top ({', '.join(keys)}): the agent is told not to open them.")
    if shared and not result["refused"]:
        result["notes"].append("This studio is shared: the path is on the computer the studio runs on, not on "
                               "the device you are typing on.")
    return result


# -- reading an address --------------------------------------------------------------------
async def probe(address: str, seconds: float = PROBE_SECONDS, title: bool = True) -> dict:
    """One GET → {reachable, status, title, error, this_computer}. Sends nothing but the request."""
    found = {"reachable": False, "status": None, "title": None, "error": None, "this_computer": this_computer(address)}

    async def ask() -> None:
        # verify=False: a development server's own certificate is normal on this computer, and
        # nothing is sent to the address or trusted from it beyond a status and a title.
        async with httpx.AsyncClient(follow_redirects=True, max_redirects=3, verify=False, timeout=seconds) as client:
            async with client.stream("GET", address, headers={"accept": "text/html,*/*;q=0.5"}) as response:
                found.update(reachable=True, status=response.status_code)
                if not title or "html" not in response.headers.get("content-type", "").lower():
                    return
                start = b""
                async for chunk in response.aiter_bytes():
                    start += chunk
                    if len(start) >= TITLE_BYTES:
                        break
                match = TITLE.search(start[:TITLE_BYTES])
                if match:
                    text = " ".join(html.unescape(match.group(1).decode("utf-8", "replace")).split())
                    found["title"] = text[:120] or None

    try:
        await asyncio.wait_for(ask(), seconds)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        if not found["reachable"]:
            found["error"] = f"{address} did not answer within {seconds:g} seconds."
    except httpx.ConnectError:
        found["error"] = f"Nothing answered at {address}."
    except (httpx.HTTPError, OSError, ValueError) as exc:
        if not found["reachable"]:
            found["error"] = f"{address} could not be read ({exc.__class__.__name__})."
    return found


async def inspect_address(text: str, shared: bool = False) -> dict:
    try:
        address = check_address(text)
    except ContextError as exc:
        if exc.code != "context_refused":
            raise
        return {"kind": "address", "path": None, "address": text.strip()[:ADDRESS_MAX], "label": "",
                "refused": exc.message, "found": None, "notes": []}
    found = await probe(address)
    notes = []
    if not found["reachable"]:
        notes.append("Nothing answered at this address just now. It can still be added: the agent is given the "
                     "address and reads the app once it is running.")
    if not found["this_computer"]:
        notes.append("This address is on another machine. The agent reaches it from the computer the studio runs "
                     "on, not from your device.")
    elif shared:
        notes.append("This studio is shared: localhost here is the computer the studio runs on, not the device "
                     "you are typing on.")
    return {"kind": "address", "path": None, "address": address, "label": host_label(address)[:LABEL_MAX],
            "refused": None, "found": found, "notes": notes}


async def inspect(value: str, protected: list[Path], home: Path, shared: bool = False) -> dict:
    kind, text = classify(value)
    if kind == "address":
        return await inspect_address(text, shared)
    return await asyncio.to_thread(inspect_path, text, protected, home, shared)


# -- the items a project keeps -------------------------------------------------------------
def clean_label(label: str | None, default: str = "") -> str:
    text = " ".join(str(label if label is not None else default).split())
    if not text:
        text = " ".join(default.split())
    if not text or len(text) > LABEL_MAX:
        raise ContextError("invalid_request", f"The label needs 1 to {LABEL_MAX} characters.")
    return text


def clean_note(note: str | None) -> str:
    text = str(note or "").strip()
    if len(text) > NOTE_MAX:
        raise ContextError("invalid_request", f"The note can be at most {NOTE_MAX} characters.")
    return text


def inside(path: str | None, folder: Path) -> bool:
    """Is `path` the folder or something in it?"""
    if not path:
        return False
    real, root = Path(path).resolve(), folder.resolve()
    return real == root or root in real.parents


def new_item(inspection: dict, label: str | None, note: str | None, existing: list[dict]) -> dict:
    """The item to keep for an inspection that was not refused. ContextError for a duplicate or a full list."""
    key = "address" if inspection["kind"] == "address" else "path"
    if any(item.get(key) == inspection[key] for item in existing):
        raise ContextError("already_added", "This project already has that as context.", 409)
    if len(existing) >= MAX_ITEMS:
        raise ContextError("too_many", f"A project holds at most {MAX_ITEMS} context items. Remove one first.")
    taken = {item.get("id") for item in existing}
    while (item_id := "cx_" + secrets.token_hex(4)) in taken:
        pass
    found = {key: value for key, value in (inspection.get("found") or {}).items() if key not in ("top", "top_more")}
    return {"id": item_id, "kind": inspection["kind"], "path": inspection["path"], "address": inspection["address"],
            "label": clean_label(label, inspection["label"]), "note": clean_note(note), "added_at": now_iso(),
            "found": found}


def path_state(item: dict) -> dict:
    path = Path(item.get("path") or "")
    folder = item.get("kind") == "folder"
    what = "folder" if folder else "file"
    if not (path.is_dir() if folder else path.is_file()):
        return {"ok": False, "problem": f"This {what} is not there now."}
    if not os.access(path, os.R_OK | os.X_OK if folder else os.R_OK):
        return {"ok": False, "problem": f"The studio cannot read this {what} now."}
    return {"ok": True, "problem": None}


async def with_state(items: list[dict]) -> list[dict]:
    """The items with `state`; addresses are asked together, each with STATE_SECONDS at most."""
    async def one(item: dict) -> dict:
        if item.get("kind") != "address":
            return {**item, "state": await asyncio.to_thread(path_state, item)}
        found = await probe(item["address"], STATE_SECONDS, title=False)
        return {**item, "state": {"ok": found["reachable"],
                                  "problem": None if found["reachable"] else "This address does not answer now."}}
    return list(await asyncio.gather(*(one(item) for item in items)))


# -- what the agent is told ----------------------------------------------------------------
def _quoted(text: str, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def _size(count: int) -> str:
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if count >= size:
            return f"{count / size:.1f} {unit}"
    return f"{count} bytes"


def _describe(item: dict) -> str:
    found, kind = item.get("found") or {}, item.get("kind")
    label = _quoted(item.get("label") or "", LABEL_MAX)
    note = _quoted(item.get("note") or "", NOTE_MAX)
    said = f" The person's note: “{note}”." if note else ""
    if kind == "address":
        seen = ("it answered when it was added" + (f", with the title “{_quoted(found['title'], 120)}”"
                                                   if found.get("title") else "")
                if found.get("reachable") else "it did not answer when it was added, so it may not be running")
        return f"  - Address “{label}”: {item.get('address')} ({seen}).{said}"
    state = path_state(item)
    where = f"  - {'Folder' if kind == 'folder' else 'File'} “{label}”: {item.get('path')}"
    if not state["ok"]:
        return f"{where} — NOT AVAILABLE NOW ({state['problem']}) Say so instead of guessing what it holds.{said}"
    if kind == "file":
        return f"{where} ({_size(int(found.get('bytes') or 0))} when it was added).{said}"
    facts = []
    if (found.get("framework") or {}).get("label"):
        facts.append(f"{found['framework']['label']} app")
    if (found.get("git") or {}).get("branch"):
        facts.append(f"git branch {found['git']['branch']}")
    if found.get("files") is None:
        facts.append("very large: more files than the studio could count")
    elif found.get("partial"):
        facts.append(f"very large: more than {COUNT_LIMIT:,} files")
    else:
        facts.append(f"about {_plural(int(found['files']), 'file')} when it was added")
    top, more, _keys = top_names(Path(item["path"]))
    listing = (" At its top now: " + ", ".join(top) + (f" (and {more:,} more)" if more else "") + ".") if top else ""
    return f"{where} ({'; '.join(facts)}).{said}{listing}"


def facts(items: list[dict]) -> str | None:
    """The project fact about its context items, or None when it has none."""
    if not items:
        return None
    lines = [
        f"- The person added context from outside this project ({_plural(len(items), 'item')}). Their questions and "
        "requests may be about these: look there before answering from memory or guessing, and say which files an "
        "answer came from.",
        *(_describe(item) for item in items),
    ]
    if any(item.get("kind") in ("folder", "file") for item in items):
        lines.append(
            "  Read a context folder or file with your ordinary file tools and its full path. A folder may be very "
            "large: search it first (Grep, Glob), read only the files the question needs, never read it whole, and "
            "hand a broad question to sub-agents. It is READ ONLY: create, change or delete nothing there, and run "
            "nothing there that writes (no install, build, formatter or code generator, and no git command that "
            "changes anything). To try a change, copy the files into the project and work on the copy. When the "
            "person wants the code there itself changed, say that it was added as context, and that bringing the "
            "folder in as an app (New page, “An app on this computer”) is how the studio edits one. Do not open key "
            "files there (.env and the like) unless the person asks for that file by name.")
    if any(item.get("kind") == "address" for item in items):
        lines.append(
            "  Request a context address from the shell (curl), and see it with look_at_page and `url` (the full "
            "address of a page under it; such a look never carries a sign-in). Read it, do not use it: submit no "
            "form and change no data there unless the person asks for it. If it does not answer, say so.")
    return "\n".join(lines)
