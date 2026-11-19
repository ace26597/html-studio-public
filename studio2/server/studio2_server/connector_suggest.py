"""A connector from a description (CONTRACT.md, "A connector from a description").

One question to the connection's small model, which answers from memory, and then everything
that can be checked without running anything: the shape of what it named, the package in its
registry, the address with one keyless request. Nothing is added here; the answer fills a form.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import quote, urlsplit

import httpx

from .catalog import words
from .library import NAME

MAX_TEXT, MAX_SUGGESTIONS, MAX_HAVE, MAX_SECRETS = 2_000, 3, 5, 6
MODEL_SECONDS, CHECK_SECONDS = 40.0, 6.0
MAX_REGISTRY_BYTES = 6 * 1024 * 1024
NPM_PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*")
NPM_VERSION = re.compile(r"@[A-Za-z0-9][A-Za-z0-9._+-]*")
PYPI_PACKAGE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
PYPI_REST = re.compile(r"(?:\[[A-Za-z0-9_,.-]+\])?(?:(?:==|>=|~=|@)[A-Za-z0-9.*+!_-]+)?")
SECRET_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
KEY_IN_ARGS = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]")
BAD_FORM = "the model's answer for it was not in a form that can be used"
# Words that say what to do and nothing about which service: "read and search my notes" must not
# match every connector whose description says "search and read".
GENERIC = frozenset(words("read search find get list look see show query create make add update edit write use "
                          "access connect manage open send check page file data item thing agent tool"))

SYSTEM = """You suggest MCP servers (connectors) for a person who describes what they want an AI \
coding agent to be able to reach. Answer with JSON only, no other text:
{"connectors": [{"name": "...", "label": "...", "description": "...", "why": "...", "transport": "http" or "stdio", \
"url": "...", "command": "npx" or "uvx", "args": ["..."], "secrets": [{"name": "...", "where": "..."}], \
"sign_in": false}]}

Rules:
- At most 3, the best fit first. Only MCP servers you are sure exist, with the exact address or the \
exact package name as it is published. If you are not sure of the exact address or package name, leave \
that one out. An empty list is a good answer; a guessed name is a bad one.
- Prefer the server published by the service's own maker, then one that is widely used.
- transport "http": `url` is the https address of the MCP endpoint itself. transport "stdio": only a \
package started with `npx` (npm) or `uvx` (PyPI); `args` begins with the package name. No docker, no \
git clone, no local path, no other program.
- `name`: a short id in lower-case letters, digits and "-". `label`: the service's name. `description`: \
one sentence on what its tools let an agent do. `why`: one sentence on why it fits this person's words.
- `secrets`: what the person must supply. For stdio the environment variable names the package reads; \
for http the header names (usually Authorization). `where`: one short sentence (under 200 characters) saying \
where the person gets the value and its form (for example "Bearer <token>"). Never put a key, a token or a placeholder for \
one in `url` or `args`.
- `sign_in`: true only for an http server that works by signing in through a browser (OAuth) and takes \
no key.
- Do not suggest anything named under "Already in the library"."""


def _line(value, limit: int) -> str | None:
    """One line of at most `limit` characters, cut at a word with … when it was longer."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text or None


def read_answer(said: str) -> list | None:
    """The model's text → its list of connectors, or None when it cannot be read."""
    start, end = said.find("{"), said.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(said[start:end + 1])
    except ValueError:
        return None
    items = data.get("connectors") if isinstance(data, dict) else None
    return items if isinstance(items, list) else None


def _npm(args: list[str]) -> tuple[str, list[str]] | None:
    """`npx` arguments → (package, the arguments as they will run), or None for another shape."""
    rest = list(args)
    while rest and rest[0] in ("-y", "--yes"):
        rest.pop(0)
    if not rest or rest[0].startswith("-"):
        return None
    name = NPM_PACKAGE.match(rest[0])
    if not name or not (name.end() == len(rest[0]) or NPM_VERSION.fullmatch(rest[0], name.end())):
        return None
    return name.group(0), ["-y", *rest]     # without -y, npx waits for a key press nobody can give


def _pypi(args: list[str]) -> tuple[str, list[str]] | None:
    if args[:1] == ["--from"]:
        if len(args) < 3 or args[2].startswith("-"):
            return None
        spec = args[1]
    elif args and not args[0].startswith("-"):
        spec = args[0]
    else:
        return None
    name = PYPI_PACKAGE.match(spec)
    if not name or not PYPI_REST.fullmatch(spec, name.end()):
        return None
    return name.group(0), list(args)


def clean(item) -> dict | str:
    """One thing the model named → a suggestion (with `_package` for the check), or the reason
    it is left out. Only an https address, `npx <package>` and `uvx <package>` are accepted."""
    if not isinstance(item, dict):
        return BAD_FORM
    label = _line(item.get("label"), 80) or _line(item.get("name"), 80)
    description, why = _line(item.get("description"), 400), _line(item.get("why"), 300)
    if not label or not description:
        return BAD_FORM
    name = re.sub(r"[^a-z0-9_-]+", "-", str(item.get("name") or label).lower()).strip("-_")[:40].strip("-_")
    if not NAME.fullmatch(name) or name == "studio":
        return BAD_FORM
    out = {"name": name, "label": label, "description": description, "why": why or "", "url": None,
           "command": None, "args": [], "sign_in": False}
    transport = item.get("transport")
    if transport == "http":
        url = item.get("url")
        parts = urlsplit(url) if isinstance(url, str) and len(url) <= 300 and not re.search(r"\s", url) else None
        if not parts or parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            return "it is not an https address"
        if KEY_IN_ARGS.search(parts.query):
            return "it put a key in the address"
        out.update(transport="http", url=url, sign_in=item.get("sign_in") is True)
    elif transport == "stdio":
        command, args = item.get("command"), item.get("args")
        if (not isinstance(args, list) or len(args) > 20
                or not all(isinstance(a, str) and 0 < len(a) <= 200 and not re.search(r"[\x00-\x1f]", a) for a in args)):
            return BAD_FORM
        found = _npm(args) if command == "npx" else _pypi(args) if command == "uvx" else None
        if command not in ("npx", "uvx"):
            return "it is not a package started with npx or uvx, so it cannot be checked"
        if found is None:
            return "its package name could not be read"
        if any(KEY_IN_ARGS.search(a) for a in args):
            return "it put a key in the command"
        out.update(transport="stdio", command=command, args=found[1],
                   _package=("npm" if command == "npx" else "pypi", found[0]))
    else:
        return BAD_FORM
    secrets = item.get("secrets") or []
    if not isinstance(secrets, list) or len(secrets) > MAX_SECRETS:
        return BAD_FORM
    out["secrets"] = []
    for secret in secrets:
        key = secret.get("name") if isinstance(secret, dict) else None
        if not isinstance(key, str) or not SECRET_NAME.fullmatch(key.strip()):
            return BAD_FORM
        if key.strip() not in [s["name"] for s in out["secrets"]]:
            out["secrets"].append({"name": key.strip(), "where": _line(secret.get("where"), 300) or ""})
    return out


# -- checking what was named --------------------------------------------------------------------
async def fetch(method: str, url: str, **options) -> tuple[int, bytes]:
    """One request, no redirect followed, at most MAX_REGISTRY_BYTES read. Raises OSError on no answer."""
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=CHECK_SECONDS) as client:
            async with client.stream(method, url, **options) as response:
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_REGISTRY_BYTES:
                        return response.status_code, b""
                return response.status_code, body
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError(str(exc)) from None


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _repository(value) -> str | None:
    url = value.get("url") if isinstance(value, dict) else value
    if not isinstance(url, str):
        return None
    url = re.sub(r"^git\+", "", url.strip())
    url = re.sub(r"^git://", "https://", url)
    url = re.sub(r"\.git$", "", url)
    return url if re.match(r"https://\S+$", url) and len(url) <= 300 else None


async def check_npm(package: str, get=None) -> dict:
    get = get or fetch
    out = {"kind": "npm", "package": package, "found": None, "version": None, "description": None,
           "published": None, "first_published": None, "downloads_week": None, "repository": None}
    address = "https://registry.npmjs.org/" + quote(package, safe="@")
    try:
        status, body = await get("GET", address)
        if status == 200 and not body:      # too large to read whole: its latest version alone
            status, body = await get("GET", address + "/latest")
    except OSError:
        return out
    if status == 404:
        return {**out, "found": False}
    data = _json(body)
    if status != 200 or data.get("name") != package:
        return out
    version = (data.get("dist-tags") or {}).get("latest") if "dist-tags" in data else data.get("version")
    times = data.get("time") if isinstance(data.get("time"), dict) else {}
    latest = (data.get("versions") or {}).get(version) if isinstance(data.get("versions"), dict) else data
    latest = latest if isinstance(latest, dict) else {}
    out.update(found=True, version=version if isinstance(version, str) else None,
               description=_line(data.get("description") or latest.get("description"), 300),
               published=times.get(version) if isinstance(times.get(version), str) else None,
               first_published=times.get("created") if isinstance(times.get("created"), str) else None,
               repository=_repository(data.get("repository") or latest.get("repository")))
    try:
        status, body = await get("GET", "https://api.npmjs.org/downloads/point/last-week/" + quote(package, safe="@/"))
        count = _json(body).get("downloads") if status == 200 else None
        out["downloads_week"] = count if isinstance(count, int) and not isinstance(count, bool) else None
    except OSError:
        pass
    return out


async def check_pypi(package: str, get=None) -> dict:
    get = get or fetch
    out = {"kind": "pypi", "package": package, "found": None, "version": None, "description": None,
           "published": None, "first_published": None, "downloads_week": None, "repository": None}
    try:
        status, body = await get("GET", f"https://pypi.org/pypi/{quote(package, safe='')}/json")
    except OSError:
        return out
    if status == 404:
        return {**out, "found": False}
    data = _json(body)
    info = data.get("info") if isinstance(data.get("info"), dict) else None
    if status != 200 or not info:
        return out

    def uploaded(files) -> list[str]:
        return [f["upload_time_iso_8601"] for f in files if isinstance(f, dict)
                and isinstance(f.get("upload_time_iso_8601"), str)] if isinstance(files, list) else []

    every = [t for files in (data.get("releases") or {}).values() for t in uploaded(files)] \
        if isinstance(data.get("releases"), dict) else []
    now = uploaded(data.get("urls"))
    links = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
    order = ("repository", "source", "source code", "github", "homepage")      # the code before the front page
    wanted = [links[k] for k in sorted(links, key=lambda k: order.index(k.lower()) if k.lower() in order else 99)
              if k.lower() in order]
    out.update(found=True, version=info.get("version") if isinstance(info.get("version"), str) else None,
               description=_line(info.get("summary"), 300), published=min(now) if now else None,
               first_published=min(every) if every else None,
               repository=next((u for u in map(_repository, wanted) if u), None))
    return out


async def is_public_host(host: str) -> bool | None:
    """Every address the name resolves to is one on the public internet. None: there is no such name."""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return None if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)) else False
    except (OSError, UnicodeError):
        return False
    try:
        return bool(found) and all(ipaddress.ip_address(item[4][0].split("%")[0]).is_global for item in found)
    except ValueError:
        return False


async def check_address(url: str, get=None, public=None) -> dict:
    """One keyless MCP `initialize`, only to a public host. The answer's status is all that is kept.
    (The name is resolved again by the request itself; what a changed answer could gain is a
    status code from an address the person's own browser can already reach.)"""
    get, public = get or fetch, public or is_public_host
    out = {"kind": "address", "found": None, "status": None}
    reachable = await public(urlsplit(url).hostname or "")
    if reachable is None:       # no such name: as much "no answer" as a refused connection
        return {**out, "found": False}
    if not reachable:
        return out
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "html-studio-check", "version": "1"}}}
    try:
        status, _body = await get("POST", url, json=hello,
                                  headers={"Accept": "application/json, text/event-stream"})
    except OSError:
        return {**out, "found": False}
    return {**out, "status": status, "found": not (status in (404, 410) or status >= 500)}


def _same(suggestion: dict, server: dict) -> bool:
    """The library already has it: the same address, or a command naming the same package."""
    if suggestion["transport"] == "http":
        return bool(server.get("url")) and server["url"].rstrip("/").lower() == suggestion["url"].rstrip("/").lower()
    package = suggestion["_package"][1].lower()
    return any(re.sub(r"(?<=.)[@=\[].*$", "", str(arg)).lower() == package for arg in server.get("args") or [])


def _have_row(server: dict) -> dict:
    return {"id": server["id"], "name": server["name"], "label": server["label"], "source": server["source"],
            "origin": server.get("origin") or "", "description": server["description"],
            "state": server.get("state") or "", "fix": server.get("fix") or ""}


def matching(servers: list[dict], text: str) -> list[dict]:
    """Connectors of the library the description's words match, the closest first: a word of
    its own name counts for more than a word of what it does."""
    asked = set(words(text)) - GENERIC
    rows = []
    for server in servers:
        own = len(asked & set(words(f"{server['name']} {server['label']}")))
        told = len(asked & set(words(server["description"])))
        if own or told >= 2:
            rows.append((-(own * 3 + told), server["label"].lower(), server))
    seen, out = set(), []
    for _score, label, server in sorted(rows, key=lambda row: row[:2]):
        if label not in seen:       # two plugins often ship the same service under one name
            seen.add(label)
            out.append(server)
    return out[:MAX_HAVE]


async def suggest(studio, text, connection_id=None, get=None, public=None) -> dict:
    from .library import LibraryError
    from .picker import picking_model
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT:
        raise LibraryError(400, "invalid_request", f"text: say what you want to reach, in 1 to {MAX_TEXT:,} characters.")
    text = text.strip()
    connections = studio.registry.connections()
    connection = next((c for c in connections if c["id"] == connection_id), None) if connection_id else connections[0]
    if connection is None:
        raise LibraryError(400, "unknown_connection", f"There is no connection named “{connection_id}”.")
    servers = await asyncio.to_thread(studio.lib.servers)
    have = matching(servers, text)
    out = {"have": [_have_row(s) for s in have], "suggestions": [], "left_out": [], "asked": None, "note": None}
    model = picking_model(connection)
    picker = studio.picker
    if not picker.ask and (studio.settings.fake or connection.get("kind") == "fake"):
        out["note"] = "No model was asked (the studio is running its scripted stand-in), so only your library was searched."
        return out
    if not model:
        out["note"] = ("This model account has no small model set, so no model was asked and only your library "
                       "was searched.")
        return out
    known = ", ".join(sorted({s["label"] for s in servers})[:60]) or "(nothing)"
    prompt = f"What the person wants:\n{' '.join(text.split())}\n\nAlready in the library: {known}"
    try:
        said = await picker._call("connection", connection, SYSTEM, prompt, MODEL_SECONDS)
    except asyncio.TimeoutError:
        out["note"] = f"The model did not answer within {int(MODEL_SECONDS)} seconds, so only your library was searched."
        return out
    except Exception as exc:    # the call's own failures (CheapCallFailed) and a gateway that would not start
        out["note"] = f"The model could not be asked ({' '.join(str(exc).split())[:160]}), so only your library was searched."
        return out
    out["asked"] = {"connection": connection["id"], "model": model}
    items = read_answer(said)
    if items is None:
        out["note"] = "The model's answer could not be read, so only your library was searched."
        return out
    kept: list[dict] = []
    for item in items[:MAX_SUGGESTIONS * 2]:
        cleaned = clean(item)
        label = _line(item.get("label") if isinstance(item, dict) else None, 80) or "One suggestion"
        if isinstance(cleaned, str):
            out["left_out"].append({"label": label, "reason": cleaned})
            continue
        already = next((s for s in servers if _same(cleaned, s)), None)
        if already is not None:
            if already["id"] not in [row["id"] for row in out["have"]]:
                out["have"].append(_have_row(already))
            continue
        if any(_same(cleaned, {"url": k["url"], "args": k["args"]}) for k in kept) or len(kept) == MAX_SUGGESTIONS:
            continue
        kept.append(cleaned)

    async def check(suggestion: dict) -> dict:
        if suggestion["transport"] == "http":
            return await check_address(suggestion["url"], get, public)
        registry, package = suggestion["_package"]
        return await (check_npm if registry == "npm" else check_pypi)(package, get)

    checks = await asyncio.gather(*(check(s) for s in kept))
    taken = {s["name"] for s in servers}
    for suggestion, checked in zip(kept, checks):
        suggestion.pop("_package", None)
        if checked["found"] is False:
            where = {"npm": "npm does not have that package", "pypi": "PyPI does not have that package",
                     "address": "nothing answered at its address"}[checked["kind"]]
            out["left_out"].append({"label": suggestion["label"], "reason": where})
            continue
        base, number = suggestion["name"], 2
        while suggestion["name"] in taken:
            suggestion["name"] = f"{base[:37]}-{number}"
            number += 1
        taken.add(suggestion["name"])
        out["suggestions"].append({**suggestion, "check": checked})
    if not out["suggestions"] and not out["note"]:
        out["note"] = ("The model named nothing that could be used." if out["left_out"] or items
                       else "The model knew of no connector for this.")
    return out
