"""The capability library: skills, sub-agents and MCP servers, and what each project has on.

CONTRACT.md, "Skills, agents and MCP servers per project" and "A library that picks for
itself". Skills come from the studio, from the person in the studio, and from this machine
(`~/.claude/skills` and installed plugins, indexed by catalog.py). Before each turn the
studio's own skills and the ones picked for that request (picker.py) are copied into a small
plugin folder for that project, which is all the agent CLI loads.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

from .catalog import Catalog, expand, front_matter, with_description
from .config import Settings, now_iso
from .store import write_json_atomic

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
MAX_ENABLED_SERVERS = 12
LIST_BUDGET_CHARS = 8_000     # 1% of a 200,000-token context window, at about four characters a token
LIST_OVERHEAD = 8             # per skill in the agent program's list: punctuation and the plugin prefix
MIN_DESCRIPTION = 60
SECRET_SERVICE = "studio2-mcp-server"
SKILL_COPY_CAP = 20 * 1024 * 1024


class LibraryError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def invalid(message: str) -> LibraryError:
    return LibraryError(400, "invalid_request", message)


class KeychainJson:
    """A small JSON object per id in the studio's secret store (keystore.py): the operating
    system's keychain, or the owner-only file on a machine that has no keychain."""

    def __init__(self, service: str = SECRET_SERVICE, store=None):
        self.service = service
        if store is None:
            from .keystore import KeyringStore
            store = KeyringStore()
        self.store = store

    def get(self, key: str) -> dict:
        try:
            return json.loads(self.store.get(self.service, key) or "{}")
        except ValueError:
            return {}

    def set(self, key: str, value: dict) -> None:
        self.store.set(self.service, key, json.dumps(value))

    def delete(self, key: str) -> None:
        self.store.delete(self.service, key)


class MemoryJson:
    def __init__(self):
        self.values: dict[str, dict] = {}

    def get(self, key: str) -> dict:
        return dict(self.values.get(key) or {})

    def set(self, key: str, value: dict) -> None:
        self.values[key] = dict(value)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class Library:
    def __init__(self, settings: Settings, secrets):
        self.settings = settings
        self.secrets = secrets
        self.own_skills = settings.data_dir / "skills"
        self.servers_path = settings.data_dir / "mcp-servers.json"
        self.status_path = settings.data_dir / "library-status.json"
        self.catalog = Catalog(settings)

    # -- skills -----------------------------------------------------------------------------
    @staticmethod
    def public(entry: dict) -> dict:
        return {k: v for k, v in entry.items() if k not in ("dir", "_dir", "kind", "config", "root")}

    def _skills(self) -> list[dict]:
        """Every skill in the index (studio first, then yours, this machine's, plugins')."""
        return [{**skill, "_dir": Path(skill["dir"])} for skill in self.catalog.skills()]

    def skill(self, skill_id: str) -> dict | None:
        self.catalog.ensure()
        entry = self.catalog.by_id.get(skill_id)
        return entry if entry and entry["kind"] == "skill" else None

    def skills(self, sources: tuple[str, ...] | None = ("studio", "yours")) -> list[dict]:
        """The studio's own skills and the ones added here (every skill with sources=None)."""
        return [self.public(skill) for skill in self.catalog.skills(sources)]

    def agents(self) -> list[dict]:
        return [{"name": a["label"], "description": a["description"]} for a in self.catalog.agents()
                if a["source"] == "studio"]

    def add_skill(self, fields: dict) -> dict:
        name = str(fields.get("name") or "").strip().lower()
        description = " ".join(str(fields.get("description") or "").split())
        body = str(fields.get("body") or "").strip()
        if not NAME.fullmatch(name):
            raise invalid("name: use lower-case letters, digits, - and _ (at most 60 characters).")
        if not description:
            raise invalid("description: say when the assistant should use this skill.")
        if not body:
            raise invalid("body: the instructions are empty.")
        if any(skill["name"] == name for skill in self.skills()):
            raise LibraryError(409, "name_taken", f"There is already a skill called {name}.")
        folder = self.own_skills / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", "utf-8")
        self.catalog.ensure(force=True)
        return next(skill for skill in self.skills() if skill["id"] == f"yours:{name}")

    def delete_skill(self, name: str) -> None:
        folder = self.own_skills / name
        if not NAME.fullmatch(name) or not (folder / "SKILL.md").is_file():
            raise LibraryError(404, "unknown_skill", "There is no skill of yours with that name.")
        shutil.rmtree(folder, ignore_errors=True)
        self.catalog.ensure(force=True)

    # -- MCP servers ------------------------------------------------------------------------
    def _own_servers(self) -> list[dict]:
        try:
            items = json.loads(self.servers_path.read_text("utf-8")).get("servers")
        except (OSError, ValueError, AttributeError):
            return []
        return [item for item in items or [] if isinstance(item, dict) and item.get("id")]

    def _machine_servers(self) -> dict[str, dict]:
        try:
            raw = json.loads((self.settings.home / ".claude.json").read_text("utf-8")).get("mcpServers")
        except (OSError, ValueError, AttributeError):
            return {}
        return {name: config for name, config in (raw or {}).items()
                if isinstance(config, dict) and NAME.fullmatch(name.lower()) and name != "studio"}

    def _suggested(self) -> list[dict]:
        try:
            items = json.loads((self.settings.library_dir / "mcp-suggestions.json").read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in items if isinstance(item, dict) and item.get("name") and item.get("url")]

    def servers(self) -> list[dict]:
        out = []
        for item in self._own_servers():
            out.append({**{k: item.get(k) for k in ("id", "name", "label", "transport", "url", "command", "args",
                                                    "secret_names", "description", "last_test")}, "source": "yours"})
        for name, config in self._machine_servers().items():
            transport = "http" if config.get("url") else "stdio"
            out.append({"id": f"machine:{name}", "name": name.lower(), "label": name, "transport": transport,
                        "url": config.get("url"), "command": config.get("command"),
                        "args": [str(a) for a in config.get("args") or []],
                        "secret_names": sorted({*(config.get("env") or {}), *(config.get("headers") or {})}),
                        "source": "machine", "description": "Configured for Claude Code on this machine.",
                        "last_test": None})
        for item in self._suggested():
            out.append({"id": f"suggested:{item['name']}", "name": item["name"], "label": item.get("label") or item["name"],
                        "transport": "http", "url": item["url"], "command": None, "args": [], "secret_names": [],
                        "source": "suggested", "description": item.get("description", ""), "last_test": None})
        self.catalog.ensure()
        taken = {server["name"] for server in out}
        for item in self.catalog.plugin_connectors:
            name = item["name"].lower()
            if not NAME.fullmatch(name) or name == "studio":
                continue
            if name in taken:   # the person's own server of the same name comes first
                name = f"{name}-{re.sub(r'[^a-z0-9]+', '-', item['plugin'].split('@')[0].lower())}"[:60]
                if name in taken:
                    continue
            taken.add(name)
            out.append(self._found_view(f"plugin:{item['plugin']}:{item['name']}", name, item["name"], item["config"],
                                        "plugin", f"Plugin {item['plugin']}", Path(item["root"]),
                                        item.get("description") or "",
                                        enabled=item["enabled"]))
        return self._with_state(out)

    @staticmethod
    def _found_view(server_id: str, name: str, label: str, config: dict, source: str, origin: str,
                    root: Path | None, description: str, enabled: bool = True) -> dict:
        missing: set[str] = set()
        expand(config, root, missing)
        return {"id": server_id, "name": name, "label": label, "transport": "http" if config.get("url") else "stdio",
                "url": config.get("url"), "command": config.get("command"),
                "args": [str(a) for a in config.get("args") or []],
                "secret_names": sorted({*(config.get("env") or {}), *(config.get("headers") or {})}),
                "source": source, "origin": origin, "description": description, "last_test": None,
                "plugin_enabled": enabled, "missing": sorted(missing)}

    def app_servers(self, workspace: Path | None) -> list[dict]:
        """Connectors an attached app declares in its own `.mcp.json`. Never picked: only pinned."""
        try:
            raw = json.loads((workspace / ".mcp.json").read_text("utf-8")) if workspace else None
        except (OSError, ValueError):
            return []
        servers = raw.get("mcpServers", raw) if isinstance(raw, dict) else None
        out = []
        for label, config in (servers or {}).items() if isinstance(servers, dict) else []:
            name = str(label).lower()
            if isinstance(config, dict) and (config.get("url") or config.get("command")) and NAME.fullmatch(name) \
                    and name != "studio":
                view = self._found_view(f"app:{label}", name, str(label), config, "app", "This app's .mcp.json",
                                        workspace, "Declared by this app in its .mcp.json.")
                out.append({**view, "_config": config})
        return self._with_state(out)

    def statuses(self) -> dict:
        try:
            found = json.loads(self.status_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def record_statuses(self, servers: list[dict]) -> None:
        """What the agent program said about each connector when a turn started."""
        known = self.statuses()
        for server in servers:
            if server.get("name") and server["name"] != "studio":
                known[str(server["name"])] = {"status": str(server.get("status")), "at": now_iso()}
        try:
            write_json_atomic(self.status_path, known)
        except OSError:
            pass

    def _with_state(self, servers: list[dict]) -> list[dict]:
        """`state` (ok | needs-auth | failed | needs-setting | None when never tried) and `fix`."""
        seen = self.statuses()
        for server in servers:
            status = (seen.get(server["name"]) or {}).get("status")
            state, fix = None, None
            if server.get("missing"):
                state = "needs-setting"
                fix = ("Set " + ", ".join(server["missing"]) + " in the environment the studio starts in, "
                       "or add this server in the studio with its key.")
            elif status == "needs-auth":
                state, fix = "needs-auth", f"claude mcp login {server['label']}"
            elif status == "failed":
                state = "failed"
            elif status == "connected":
                state = "ok"
            server["state"], server["fix"] = state, fix
        return servers

    def _server_fields(self, fields: dict, base: dict | None = None) -> tuple[dict, dict | None]:
        """→ (the stored record, secrets to store or None to leave them alone)."""
        merged = {**(base or {}), **{k: v for k, v in fields.items() if v is not None}}
        name = str(merged.get("name") or "").strip().lower()
        if not NAME.fullmatch(name) or len(name) > 40 or name == "studio":
            raise invalid("name: use lower-case letters, digits, - and _ (at most 40 characters; not “studio”).")
        transport = merged.get("transport")
        if transport not in ("http", "stdio"):
            raise invalid("transport: choose http (a web address) or stdio (a command).")
        record = {"name": name, "label": str(merged.get("label") or name)[:80], "transport": transport,
                  "description": str(merged.get("description") or "")[:400], "url": None, "command": None, "args": []}
        if transport == "http":
            url = str(merged.get("url") or "").strip()
            if not re.match(r"https?://\S+$", url):
                raise invalid("url: give the server's http or https address.")
            record["url"] = url
        else:
            command = str(merged.get("command") or "").strip()
            if not command:
                raise invalid("command: give the program to run.")
            args = merged.get("args") or []
            if not isinstance(args, list) or len(args) > 40:
                raise invalid("args: give a list of arguments.")
            record["command"], record["args"] = command, [str(a) for a in args]
        secrets = None
        given = fields.get("headers" if transport == "http" else "env")
        if given is not None:
            if not isinstance(given, dict) or any(not str(k).strip() for k in given):
                raise invalid("headers/env: give names and values.")
            secrets = {str(k).strip(): str(v) for k, v in given.items()}
        return record, secrets

    def add_server(self, fields: dict) -> dict:
        record, secrets = self._server_fields(fields)
        if any(server["name"] == record["name"] for server in self.servers()):
            raise LibraryError(409, "name_taken", f"There is already a server called {record['name']}.")
        record.update(id=f"mcp_{uuid.uuid4().hex[:8]}", created_at=now_iso(), last_test=None,
                      secret_names=sorted(secrets or {}))
        if secrets:
            self.secrets.set(record["id"], secrets)
        write_json_atomic(self.servers_path, {"servers": [*self._own_servers(), record]})
        return self.server(record["id"])

    def server(self, server_id: str, workspace: Path | None = None) -> dict:
        for item in self.servers() + (self.app_servers(workspace) if server_id.startswith("app:") else []):
            if item["id"] == server_id:
                return item
        raise LibraryError(404, "unknown_server", "There is no such MCP server.")

    def _own(self, server_id: str) -> tuple[list[dict], dict]:
        items = self._own_servers()
        for item in items:
            if item["id"] == server_id:
                return items, item
        raise LibraryError(404, "unknown_server", "Only servers you added in the studio can be changed here.")

    def update_server(self, server_id: str, fields: dict) -> dict:
        items, current = self._own(server_id)
        record, secrets = self._server_fields(fields, current)
        if any(s["name"] == record["name"] and s["id"] != server_id for s in self.servers()):
            raise LibraryError(409, "name_taken", f"There is already a server called {record['name']}.")
        if secrets is not None:
            kept = self.secrets.get(server_id)
            merged = {k: (v if v != "" else kept.get(k, "")) for k, v in secrets.items()}  # "" keeps a stored value
            merged = {k: v for k, v in merged.items() if v != ""}
            self.secrets.set(server_id, merged) if merged else self.secrets.delete(server_id)
            record["secret_names"] = sorted(merged)
        current.update(record)
        write_json_atomic(self.servers_path, {"servers": items})
        return self.server(server_id)

    def delete_server(self, server_id: str) -> None:
        items, current = self._own(server_id)
        self.secrets.delete(server_id)
        write_json_atomic(self.servers_path, {"servers": [i for i in items if i is not current]})

    def record_test(self, server_id: str, result: dict) -> None:
        try:
            items, current = self._own(server_id)
        except LibraryError:
            return
        current["last_test"] = {"ok": result["ok"], "at": now_iso(), "message": result["message"],
                                "tools": result.get("tools") or []}
        write_json_atomic(self.servers_path, {"servers": items})

    def server_config(self, server_id: str, workspace: Path | None = None) -> dict:
        """The config the agent CLI (and the test) connects with, secrets included. Never returned by the API."""
        view = self.server(server_id, workspace)
        if view["source"] in ("plugin", "app"):
            if view["source"] == "app":
                raw, root = view["_config"], workspace
            else:
                found = next(item for item in self.catalog.plugin_connectors
                             if f"plugin:{item['plugin']}:{item['name']}" == server_id)
                raw, root = found["config"], Path(found["root"])
            config = expand({k: v for k, v in raw.items() if k != "description"}, root, set())
            config.setdefault("type", "http" if config.get("url") else "stdio")
            return config
        if view["source"] == "machine":
            config = dict(self._machine_servers()[view["label"]])
            config.setdefault("type", "http" if config.get("url") else "stdio")
            return config
        secrets = self.secrets.get(server_id) if view["source"] == "yours" else {}
        if view["transport"] == "http":
            return {"type": "http", "url": view["url"], "headers": secrets}
        return {"type": "stdio", "command": view["command"], "args": view["args"], "env": secrets}

    # -- per project ------------------------------------------------------------------------
    def for_project(self, project: dict, workspace: Path | None = None, prefs: dict | None = None) -> dict:
        """The project's library panel: the studio's own skills, every skill the person decided
        about (here or for the whole machine), and every connector. `enabled` is what a turn
        loads without being asked; `choice` is this project's decision (true always, false
        never, null left to the studio); `pref` the whole machine's (always | never | null)."""
        chosen = project.get("library") or {}
        prefs = prefs or {}
        picked_skills, picked_servers = chosen.get("skills") or {}, chosen.get("mcp_servers") or {}
        skills = []
        for skill in self._skills():
            choice, pref = picked_skills.get(skill["id"]), (prefs.get("skills") or {}).get(skill["id"])
            if not skill["default"] and choice is None and pref is None:
                continue
            on = choice if choice is not None else (pref == "always" if pref else skill["default"])
            skills.append({**self.public(skill), "enabled": bool(on), "choice": choice, "pref": pref})
        servers = []
        for server in self.servers() + self.app_servers(workspace if project.get("attach") else None):
            choice, pref = picked_servers.get(server["id"]), (prefs.get("mcp_servers") or {}).get(server["id"])
            on = choice if choice is not None else pref == "always"
            servers.append({**{k: v for k, v in server.items() if k != "_config"}, "enabled": bool(on),
                            "choice": choice, "pref": pref})
        return {"skills": skills, "mcp_servers": servers, "requested": list(chosen.get("requested") or []),
                "last_pick": project.get("last_pick")}

    def choose(self, project: dict, body: dict, workspace: Path | None = None) -> dict:
        """→ the project's new `library` field. For each id: true = always in this project,
        false = never in this project, null = leave it to the studio (the studio's own skills
        are then on, everything else is picked per request)."""
        current = project.get("library") or {}
        skills, servers = dict(current.get("skills") or {}), dict(current.get("mcp_servers") or {})
        known_servers = {server["id"] for server in self.servers() + self.app_servers(workspace)}
        for key, value in (body.get("skills") or {}).items():
            skill = self.skill(key)
            if skill is None:
                raise invalid(f"There is no skill {key}.")
            if value is None or (skill["default"] and value):
                skills.pop(key, None)
            else:
                skills[key] = bool(value)
        for key, value in (body.get("mcp_servers") or {}).items():
            if key not in known_servers:
                raise invalid(f"There is no MCP server {key}.")
            if value is None:
                servers.pop(key, None)
            else:
                servers[key] = bool(value)
        servers = {key: value for key, value in servers.items() if key in known_servers or key.startswith("app:")}
        if sum(1 for value in servers.values() if value) > MAX_ENABLED_SERVERS:
            raise invalid(f"At most {MAX_ENABLED_SERVERS} MCP servers can be on in one project.")
        requested = [key for key in current.get("requested") or [] if servers.get(key) is None]
        out = {"skills": {k: v for k, v in skills.items() if self.skill(k)}, "mcp_servers": servers}
        return {**out, "requested": requested} if requested else out

    def forget_server(self, projects: list[dict], server_id: str) -> list[tuple[str, dict]]:
        """→ [(project id, new library field)] for projects that had the server on."""
        out = []
        for project in projects:
            chosen = project.get("library") or {}
            if server_id in (chosen.get("mcp_servers") or {}):
                servers = {k: v for k, v in chosen["mcp_servers"].items() if k != server_id}
                out.append((project["id"], {**chosen, "mcp_servers": servers}))
        return out

    @staticmethod
    def fit(skills: list[dict], budget: int = LIST_BUDGET_CHARS) -> tuple[list[dict], int, int]:
        """Keep the agent program's skill list inside its budget (it gives the list 1% of the
        context window and drops descriptions past that). → (the skills that fit, each with the
        `listed` description it gets, the characters the list takes, the description cap used).
        Descriptions are shortened evenly first; only then are the last skills left out."""
        skills = list(skills)
        while skills:
            names = sum(len(skill["name"]) + LIST_OVERHEAD for skill in skills)
            low, high = MIN_DESCRIPTION - 1, 600
            while low < high:   # the longest description length at which everything fits
                cap = (low + high + 1) // 2
                if names + sum(min(len(skill["description"]), cap) for skill in skills) <= budget:
                    low = cap
                else:
                    high = cap - 1
            if low >= MIN_DESCRIPTION:
                out = []
                for skill in skills:
                    text = skill["description"]
                    out.append({**skill, "listed": text if len(text) <= low else text[:low - 1].rstrip() + "…"})
                return out, names + sum(len(skill["listed"]) for skill in out), low
            skills.pop()
        return [], 0, 0

    def copy_skill(self, skill: dict, target: Path, listed: str | None = None) -> Path | None:
        """Copy one skill folder into a project's plugin folder (None when it carries large assets)."""
        source = Path(skill.get("_dir") or skill["dir"]).resolve()
        if skill.get("size", 0) > SKILL_COPY_CAP:
            return None
        folder = target / "skills" / skill["name"]
        shutil.rmtree(folder, ignore_errors=True)
        shutil.copytree(source, folder, symlinks=False,
                        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv"))
        manifest = folder / "SKILL.md"
        if listed is not None and manifest.is_file():
            fields = front_matter(manifest)
            if fields.get("description", "") != listed or fields.get("name") != skill["name"]:
                manifest.write_text(with_description(manifest.read_text("utf-8", "replace"), skill["name"], listed), "utf-8")
        return folder

    def materialise(self, project: dict, target: Path, picked: list[str] | None = None) -> dict:
        """Build the plugin folder the agent loads for this project: the studio's own skills
        that are not switched off, the skills picked for this request (in order), and the
        agents. → {plugin_dir, loaded: [skill ids], list_chars, budget_chars, description_cap, left_out}."""
        chosen = (project.get("library") or {}).get("skills") or {}
        if picked is None:   # nothing was picked for a request: the pinned ones
            picked = [key for key, on in chosen.items() if on]
        shutil.rmtree(target, ignore_errors=True)
        (target / ".claude-plugin").mkdir(parents=True)
        (target / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": "studio", "version": "0.3.0",
             "description": "Studio 2 library: the skills and agents loaded for this request."}), "utf-8")
        wanted, taken = [], set()
        own = [skill for skill in self._skills() if skill["default"] and chosen.get(skill["id"], True)]
        extra = [skill for skill in (self.skill(skill_id) for skill_id in picked) if skill and not skill["default"]]
        for skill in own + extra:   # studio first, then yours, then the picked ones: first name wins
            if skill["name"] in taken or skill.get("size", 0) > SKILL_COPY_CAP:
                continue
            taken.add(skill["name"])
            wanted.append(skill)
        fitted, chars, cap = self.fit(wanted)
        for skill in fitted:
            self.copy_skill(skill, target, skill["listed"])
        agents = self.settings.library_dir / "agents"
        if agents.is_dir():
            shutil.copytree(agents, target / "agents")
        kept = {skill["id"] for skill in fitted}
        return {"plugin_dir": target, "loaded": [skill["id"] for skill in fitted], "list_chars": chars,
                "budget_chars": LIST_BUDGET_CHARS, "description_cap": cap,
                "left_out": [skill["id"] for skill in wanted if skill["id"] not in kept]}

    def mcp_configs(self, project: dict, picked: list[str] | None = None, workspace: Path | None = None) -> dict[str, dict]:
        """name → config for the connectors of one request: the ids picked for it, or (picked
        None) the ones pinned in this project."""
        if picked is None:
            picked = [key for key, on in ((project.get("library") or {}).get("mcp_servers") or {}).items() if on]
        out = {}
        for server_id in picked:
            try:
                out[self.server(server_id, workspace)["name"]] = self.server_config(server_id, workspace)
            except (LibraryError, KeyError, StopIteration):
                continue  # removed since it was switched on
        return out


async def test_server(config: dict, timeout: float = 20.0) -> dict:
    """Connect, list tools, disconnect → {ok, message, tools}."""
    from mcp import ClientSession

    async def run() -> list[dict]:
        if config.get("type") in ("http", "sse") or config.get("url"):
            if config.get("type") == "sse":
                from mcp.client.sse import sse_client
                opened = sse_client(config["url"], headers=config.get("headers") or None)
            else:
                from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
                opened = streamable_http_client(config["url"], http_client=create_mcp_http_client(
                    headers={str(k): str(v) for k, v in (config.get("headers") or {}).items()} or None))
        else:
            import os

            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            opened = stdio_client(StdioServerParameters(
                command=config["command"], args=[str(a) for a in config.get("args") or []],
                env={**os.environ, **{k: str(v) for k, v in (config.get("env") or {}).items()}}))
        async with opened as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listed = await session.list_tools()
                return [{"name": t.name, "description": " ".join((t.description or "").split())[:300]}
                        for t in listed.tools[:200]]

    try:
        tools = await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "message": f"The server did not answer within {int(timeout)} seconds.", "tools": []}
    except BaseException as exc:  # the MCP client reports failures as exception groups
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        inner = exc
        while getattr(inner, "exceptions", None):
            inner = inner.exceptions[0]
        return {"ok": False, "message": " ".join(f"{inner.__class__.__name__}: {inner}".split())[:400], "tools": []}
    return {"ok": True, "message": f"Connected. {len(tools)} tool{'' if len(tools) == 1 else 's'}.", "tools": tools}
