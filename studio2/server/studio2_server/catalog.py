"""The index of everything the person has: skills, helper agents and connectors (step 3o).

CONTRACT.md, "A library that picks for itself". Found, read-only: the studio's own skills, the
ones added in the studio, `~/.claude/skills`, `~/.claude/agents`, and the skills, agents and
`.mcp.json` of every installed plugin (`~/.claude/plugins/installed_plugins.json`, falling back
to a scan of the plugin cache). Only names, descriptions and where each thing lives are kept;
hooks, permission rules and settings are never read. The index is cached in the data folder
and rebuilt when the files it was built from change.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from .config import Settings, now_iso
from .store import write_json_atomic

INDEX_VERSION = 3
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
SOURCE_ORDER = {"studio": 0, "yours": 1, "machine": 2, "plugin": 3, "app": 4, "suggested": 5}
WORD = re.compile(r"[a-z0-9]+")
STOP = frozenset("""a an and are as at be by can do for from has have how i if in into is it its me my of on or our
 so that the their them then this to up use used uses using user want we when which will with you your make makes
 made need needs please any all one new get also more via etc not should would could about what skill skills
 page""".split())
# A few words people say for things skills name differently. Query side only.
ALSO = {
    "powerpoint": ["pptx", "deck", "slide", "presentation"], "deck": ["pptx", "slide", "presentation", "powerpoint"],
    "slides": ["pptx", "deck", "presentation"], "presentation": ["pptx", "deck", "slide"],
    "excel": ["xlsx", "spreadsheet", "workbook"], "spreadsheet": ["xlsx", "excel", "csv"], "word": ["docx"],
    "chart": ["plot", "graph", "visualization", "dataviz"], "graph": ["chart", "plot"], "plot": ["chart"],
    "dashboard": ["chart", "analytics", "visualization"], "csv": ["data", "spreadsheet", "tabular"],
    "db": ["database", "sql"], "database": ["sql", "postgres", "query"], "sql": ["database", "query"],
    "pr": ["pull", "github", "review"], "pull": ["github", "pr"], "github": ["git", "pull", "repo"],
    "video": ["ffmpeg", "clip", "remotion", "film"], "cut": ["edit", "trim", "ffmpeg"], "3d": ["three", "scene", "webgl"],
    "contract": ["legal", "agreement", "clause"], "legal": ["contract", "law"], "stock": ["equity", "screener", "trading"],
    "stocks": ["equity", "screener", "trading", "stock"], "screen": ["screener"], "trial": ["clinical", "trials"],
    "trials": ["clinical", "trial"], "market": ["research", "competitor"], "report": ["research", "document"],
    "picture": ["image", "photo"], "photo": ["image"], "image": ["picture", "photo"], "pdf": ["document"],
    "website": ["web", "frontend", "page"], "email": ["gmail", "mail"], "test": ["testing", "tests"],
}


def stem(word: str) -> str:
    for tail in ("ings", "ing", "ies", "es", "ed", "s"):
        if len(word) - len(tail) >= 4 and word.endswith(tail):
            return word[:-len(tail)] + ("y" if tail == "ies" else "")
    return word


def words(text: str) -> list[str]:
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]


def front_matter(path: Path) -> dict:
    """The YAML block at the top of a SKILL.md or an agent file, read without a YAML parser:
    top-level `key: value`, block scalars (`>` and `|`) and values continued on indented lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(12_000)
    except OSError:
        return {}
    block = re.match(r"\s*---[ \t]*\n(.*?)\n---", head, re.S)
    fields: dict[str, str] = {}
    key = None
    for line in (block.group(1) if block else "").splitlines():
        top = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", line)
        if top and not line[:1].isspace():
            key, value = top.group(1).lower(), top.group(2).strip()
            fields[key] = "" if re.fullmatch(r"[>|][+-]?\d?", value) else value.strip("\"'")
        elif key and line[:1].isspace() and line.strip():
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return {k: " ".join(v.split()) for k, v in fields.items()}


def with_description(text: str, name: str, description: str) -> str:
    """A SKILL.md whose front matter says `name` and `description` exactly as given (the copy
    the agent program lists; the body and every other field are kept)."""
    block = re.match(r"(\s*---[ \t]*\n)(.*?)(\n---[ \t]*(?:\n|$))", text, re.S)
    kept, skipping = [], False
    for line in (block.group(2) if block else "").splitlines():
        top = re.match(r"([A-Za-z_][\w-]*):", line)
        if top and not line[:1].isspace():
            skipping = top.group(1).lower() in ("name", "description")
        if not skipping:
            kept.append(line)
    head = "\n".join([f"name: {name}", f"description: {json.dumps(description, ensure_ascii=False)}", *kept])
    body = text[block.end():] if block else text
    return f"---\n{head}\n---\n{body}"


def folder_size(folder: Path, cap: int = 5000) -> tuple[int, int]:
    """→ (bytes, files) of a skill folder, skipping what is never copied."""
    total = files = 0
    stack = [folder]
    while stack and files < cap:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    files += 1
            except OSError:
                continue
    return total, files


def _stat(path: Path) -> str:
    try:
        info = path.stat()
        return f"{path}:{info.st_mtime_ns}:{info.st_size}"
    except OSError:
        return f"{path}:-"


def _json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def expand(value, root: Path | None, missing: set[str], env=os.environ):
    """`${CLAUDE_PLUGIN_ROOT}`, `${VAR}` and `${VAR:-default}` in a connector's settings."""
    if isinstance(value, str):
        def swap(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            if name == "CLAUDE_PLUGIN_ROOT" and root is not None:
                return str(root)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            missing.add(name)
            return ""
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", swap, value)
    if isinstance(value, list):
        return [expand(item, root, missing, env) for item in value]
    if isinstance(value, dict):
        return {key: expand(item, root, missing, env) for key, item in value.items()}
    return value


class Catalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.claude = settings.home / ".claude"
        self.path = settings.data_dir / "library-index.json"
        self.entries: list[dict] = []
        self.plugin_connectors: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.meta: dict = {}
        self._signature: str | None = None
        self._checked = 0.0
        self._postings: dict[str, list[tuple[int, float]]] = {}
        self._idf: dict[str, float] = {}

    # -- what is installed ------------------------------------------------------------------
    def plugins(self) -> list[dict]:
        """Installed plugins → [{id, name, marketplace, path, version, enabled}], enabled first."""
        root = self.claude / "plugins"
        enabled = (_json(self.claude / "settings.json") or {}).get("enabledPlugins")
        enabled = enabled if isinstance(enabled, dict) else {}
        found: dict[str, dict] = {}
        installed = (_json(root / "installed_plugins.json") or {}).get("plugins")
        if isinstance(installed, dict):
            for plugin_id, records in installed.items():
                for record in records if isinstance(records, list) else []:
                    path = Path(str((record or {}).get("installPath") or ""))
                    if path.is_dir():
                        name, _, market = plugin_id.partition("@")
                        found[plugin_id] = {"id": plugin_id, "name": name, "marketplace": market, "path": path,
                                            "version": str(record.get("version") or ""),
                                            "enabled": bool(enabled.get(plugin_id, not enabled))}
        else:   # no record of what is installed: take the newest version of each plugin in the cache
            for plugin_dir in sorted((root / "cache").glob("*/*")):
                versions = [v for v in plugin_dir.iterdir() if v.is_dir()] if plugin_dir.is_dir() else []
                if versions:
                    newest = max(versions, key=lambda v: v.stat().st_mtime)
                    plugin_id = f"{plugin_dir.name}@{plugin_dir.parent.name}"
                    found[plugin_id] = {"id": plugin_id, "name": plugin_dir.name, "marketplace": plugin_dir.parent.name,
                                        "path": newest, "version": newest.name,
                                        "enabled": bool(enabled.get(plugin_id, not enabled))}
        return sorted(found.values(), key=lambda p: (not p["enabled"], p["id"]))

    def signature(self) -> str:
        parts = [str(INDEX_VERSION), _stat(self.claude / "plugins" / "installed_plugins.json"),
                 _stat(self.claude / "settings.json"), _stat(self.settings.home / ".claude.json"),
                 _stat(self.claude / "agents"), _stat(self.settings.library_dir / "mcp-suggestions.json"),
                 _stat(self.settings.data_dir / "mcp-servers.json")]
        parts.extend(_stat(Path(path)) for path in self.settings.connector_files)
        for folder in (self.settings.library_dir / "skills", self.settings.data_dir / "skills", self.claude / "skills",
                       *(Path(path) for path in self.settings.skill_dirs)):
            parts.append(_stat(folder))
            if folder.is_dir():
                parts.extend(_stat(manifest) for manifest in sorted(folder.glob("*/SKILL.md")))
        if not (self.claude / "plugins" / "installed_plugins.json").is_file():
            parts.extend(_stat(p) for p in sorted((self.claude / "plugins" / "cache").glob("*/*")))
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    # -- building ---------------------------------------------------------------------------
    @staticmethod
    def _skill(manifest: Path, source: str, origin: str, skill_id: str, enabled: bool = True) -> dict:
        fields = front_matter(manifest)
        name = manifest.parent.name
        size, files = folder_size(manifest.parent)
        return {"id": skill_id, "kind": "skill", "name": name, "label": fields.get("name") or name,
                "description": fields.get("description", "")[:600], "source": source, "origin": origin,
                "enabled": enabled, "size": size, "files": files, "dir": str(manifest.parent),
                "default": source in ("studio", "yours")}

    @staticmethod
    def _agent(path: Path, source: str, origin: str, agent_id: str, enabled: bool = True) -> dict:
        fields = front_matter(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {"id": agent_id, "kind": "agent", "name": path.stem, "label": fields.get("name") or path.stem,
                "description": fields.get("description", "")[:600], "source": source, "origin": origin,
                "enabled": enabled, "size": size, "files": 1, "dir": str(path), "default": source == "studio"}

    @staticmethod
    def _plugin_skill_files(root: Path) -> list[Path]:
        """Every SKILL.md of a plugin, at most four folders down (plugins keep skills in
        `skills/<name>/`, a folder named in their manifest, or straight under the root)."""
        found, stack = [], [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if depth < 4 and entry.name not in SKIP_DIRS and entry.name not in ("deprecated", "templates", "template"):
                        stack.append((Path(entry.path), depth + 1))
                elif entry.name == "SKILL.md" and depth > 0:
                    found.append(Path(entry.path))
        return sorted(found)

    @staticmethod
    def plugin_servers(plugin: dict) -> dict[str, dict]:
        """name → raw connector settings a plugin ships (`.mcp.json`, or its manifest's `mcpServers`)."""
        root: Path = plugin["path"]
        out: dict[str, dict] = {}

        def take(raw) -> None:
            servers = raw.get("mcpServers", raw) if isinstance(raw, dict) else None
            for name, config in (servers or {}).items() if isinstance(servers, dict) else []:
                if isinstance(config, dict) and (config.get("url") or config.get("command")):
                    out.setdefault(str(name), config)

        take(_json(root / ".mcp.json"))
        declared = (_json(root / ".claude-plugin" / "plugin.json") or {}).get("mcpServers")
        if isinstance(declared, str) and (root / declared).resolve().is_relative_to(root.resolve()):
            take(_json(root / declared))
        elif isinstance(declared, dict):
            take(declared)
        return out

    def build(self) -> dict:
        started = time.monotonic()
        entries: list[dict] = []
        for folder, source, origin in ((self.settings.library_dir / "skills", "studio", "Comes with the studio"),
                                       (self.settings.data_dir / "skills", "yours", "Added in the studio"),
                                       (self.claude / "skills", "machine", "~/.claude/skills")):
            if folder.is_dir():
                entries.extend(self._skill(m, source, origin, f"{source}:{m.parent.name}")
                               for m in sorted(folder.glob("*/SKILL.md")))
        known = {entry["name"] for entry in entries}
        for number, folder in enumerate(Path(path) for path in self.settings.skill_dirs):     # the person's other folders
            if not folder.is_dir():
                continue
            found = sorted(folder.glob("*/SKILL.md")) + ([folder / "SKILL.md"] if (folder / "SKILL.md").is_file() else [])
            for manifest in found:
                if manifest.parent.name in known:       # the same skill is already found elsewhere: that one stays
                    continue
                known.add(manifest.parent.name)
                entries.append(self._skill(manifest, "machine", str(folder), f"extra{number}:{manifest.parent.name}"))
        agents = self.settings.library_dir / "agents"
        entries.extend(self._agent(p, "studio", "Comes with the studio", f"studio-agent:{p.stem}")
                       for p in (sorted(agents.glob("*.md")) if agents.is_dir() else []))
        personal = self.claude / "agents"
        entries.extend(self._agent(p, "machine", "~/.claude/agents", f"machine-agent:{p.stem}")
                       for p in (sorted(personal.glob("*.md")) if personal.is_dir() else []))
        plugins = self.plugins()
        seen: dict[tuple[str, str], str] = {}
        for entry in entries:
            if entry["kind"] == "skill":
                seen.setdefault((entry["name"], entry["description"]), entry["id"])
        connectors = []
        for plugin in plugins:
            origin = f"Plugin {plugin['id']}"
            taken: set[str] = set()
            for manifest in self._plugin_skill_files(plugin["path"]):
                name = manifest.parent.name
                if name in taken:
                    continue
                taken.add(name)
                entry = self._skill(manifest, "plugin", origin, f"plugin:{plugin['id']}:{name}", plugin["enabled"])
                same = seen.setdefault((entry["name"], entry["description"]), entry["id"])
                if same != entry["id"]:   # the same skill shipped by several plugins is offered once
                    entry["duplicate_of"] = same
                entries.append(entry)
            folder = plugin["path"] / "agents"
            entries.extend(self._agent(p, "plugin", origin, f"plugin-agent:{plugin['id']}:{p.stem}", plugin["enabled"])
                           for p in (sorted(folder.glob("*.md")) if folder.is_dir() else []))
            for name, config in self.plugin_servers(plugin).items():
                connectors.append({"plugin": plugin["id"], "name": name, "root": str(plugin["path"]),
                                   "enabled": plugin["enabled"],
                                   "description": str(config.get("description") or "")[:400], "config": config})
        counts: dict[str, dict[str, int]] = {"skill": {}, "agent": {}}
        for entry in entries:
            if not entry.get("duplicate_of"):
                counts[entry["kind"]][entry["source"]] = counts[entry["kind"]].get(entry["source"], 0) + 1
        meta = {"version": INDEX_VERSION, "signature": self.signature(), "built_at": now_iso(),
                "build_ms": int((time.monotonic() - started) * 1000), "plugins": len(plugins),
                "plugins_enabled": sum(p["enabled"] for p in plugins),
                "duplicates": sum(1 for e in entries if e.get("duplicate_of")), "counts": counts}
        return {"meta": meta, "entries": entries, "plugin_connectors": connectors}

    def _load(self, data: dict) -> None:
        self.entries = data["entries"]
        self.plugin_connectors = data.get("plugin_connectors") or []
        self.meta = data["meta"]
        self.by_id = {entry["id"]: entry for entry in self.entries}
        self._signature = self.meta["signature"]
        postings: dict[str, dict[int, float]] = {}
        for index, entry in enumerate(self.entries):
            weights: dict[str, float] = {}
            for text, weight in ((entry["name"], 3.0), (entry["label"], 2.0), (entry["description"], 1.0),
                                 (entry["origin"] if entry["source"] == "plugin" else "", 0.4)):
                for word in set(words(text)):
                    weights[word] = max(weights.get(word, 0.0), weight)
            for word, weight in weights.items():
                postings.setdefault(word, {})[index] = weight
        total = max(1, len(self.entries))
        self._postings = {word: list(found.items()) for word, found in postings.items()}
        self._idf = {word: math.log(1 + total / len(found)) for word, found in postings.items()}

    def ensure(self, force: bool = False) -> dict:
        """Load the index, rebuilding it when the files it came from changed. → its summary."""
        now = time.monotonic()
        if not force and self._signature and now - self._checked < 0.5:
            return self.meta
        self._checked = now
        signature = self.signature()
        if force or signature != self._signature:
            cached = None if force else _json(self.path)
            if not (isinstance(cached, dict) and (cached.get("meta") or {}).get("signature") == signature):
                cached = self.build()
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    write_json_atomic(self.path, cached)
                except OSError:
                    pass
            self._load(cached)
        return self.meta

    # -- reading ----------------------------------------------------------------------------
    def skills(self, sources: tuple[str, ...] | None = None, duplicates: bool = False) -> list[dict]:
        self.ensure()
        return [e for e in self.entries if e["kind"] == "skill" and (sources is None or e["source"] in sources)
                and (duplicates or not e.get("duplicate_of"))]

    def agents(self) -> list[dict]:
        self.ensure()
        return [e for e in self.entries if e["kind"] == "agent"]

    def search(self, query: str, kind: str = "skill", limit: int = 60, allow=None) -> list[tuple[dict, float, list[str]]]:
        """Word search over names and descriptions → [(entry, score, the words that matched)], best first."""
        self.ensure()
        asked: dict[str, float] = {}
        for word in words(query):
            asked[word] = 1.0
            for other in ALSO.get(word, []) + ALSO.get(word.rstrip("s"), []):
                asked.setdefault(stem(other), 0.5)
        scores: dict[int, float] = {}
        hits: dict[int, list[str]] = {}
        for word, strength in asked.items():
            idf = self._idf.get(word)
            if idf is None:
                continue
            for index, weight in self._postings[word]:
                scores[index] = scores.get(index, 0.0) + idf * weight * strength
                hits.setdefault(index, []).append(word)
        ranked = []
        for index, score in scores.items():
            entry = self.entries[index]
            if entry["kind"] != kind or entry.get("duplicate_of") or (allow and not allow(entry)):
                continue
            ranked.append((entry, score * (1.0 if entry.get("enabled", True) else 0.6), hits[index]))
        ranked.sort(key=lambda item: (-item[1], SOURCE_ORDER.get(item[0]["source"], 9), item[0]["name"]))
        return ranked[:limit]

    def listing(self, kind: str, query: str = "", source: str | None = None) -> list[dict]:
        """Entries of one kind for the library screen: by relevance when searching, else by source and name."""
        self.ensure()
        keep = lambda e: source is None or e["source"] == source  # noqa: E731
        if query.strip():
            found = [entry for entry, _s, _w in self.search(query, kind, limit=100_000, allow=keep)]
            low = query.strip().lower()   # a typed fragment of a name finds it even when no whole word matches
            ids = {entry["id"] for entry in found}
            found += [e for e in self.entries if e["kind"] == kind and not e.get("duplicate_of") and keep(e)
                      and e["id"] not in ids and low in e["name"].lower()]
            return found
        chosen = [e for e in self.entries if e["kind"] == kind and not e.get("duplicate_of") and keep(e)]
        return sorted(chosen, key=lambda e: (SOURCE_ORDER.get(e["source"], 9), not e.get("enabled", True),
                                             e["origin"], e["name"]))
