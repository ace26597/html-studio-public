"""Picking skills and connectors for one request, and remembering what the person said (step 3o).

CONTRACT.md, "A library that picks for itself". Before a turn: a word search over the index
narrows the person's skills to about sixty, then one call to the connection's cheap model
picks up to twenty-five skills and up to eight connectors, each with a line of reason. Pinned
ones are always in, switched-off ones never; a library of about forty skills or fewer is loaded
whole; when the model call fails or is slow the word search alone decides. During a turn the
agent can search the library, switch a skill on and ask for a connector (tools.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .catalog import stem, words
from .config import now_iso
from .library import MAX_ENABLED_SERVERS, LibraryError, invalid
from .store import write_json_atomic

if TYPE_CHECKING:
    from .studio import Studio

log = logging.getLogger("studio2")
SMALL_LIBRARY = 40        # this many pickable skills or fewer: load them all, choose nothing
SHORTLIST = 60            # what the word search hands to the model
MAX_SKILLS = 25           # picked per request, on top of the studio's own and the pinned ones
MAX_CONNECTORS = 8        # connected per request, pinned ones included
MAX_EARLIER = 8           # skills used earlier in this project that stay loaded
WORDS_ONLY = 5            # picked at most when the word search decides alone
WORDS_SHARE = 1 / 3       # … and only skills matching this share of the request's own words
ROUTE_REST = 600.0        # seconds a way of asking the model is left alone after it failed
SYSTEM = (
    "You choose which skills and connectors an AI assistant is given for one request. A skill is a set of "
    "instructions for one kind of work; a connector gives tools for an outside service. You get the request and "
    "numbered candidates. Choose only the ones that would plainly help with THIS request: usually two to eight "
    "skills, never more than {skills}, and at most {connectors} connectors, often none. Prefer the most specific "
    "candidate, skip near-duplicates, and never choose something only because it shares a word with the request. "
    "Answer with one line per choice and nothing else, in the form `S<number>: reason in under ten words` or "
    "`C<number>: reason in under ten words`. If nothing fits, answer `none`.")


def picking_model(connection: dict) -> str | None:
    """The model that picks skills for requests on this connection, or None when it has none.
    Always a model of the connection itself: the plan, a key and the cloud accounts have Haiku;
    the studio's own LiteLLM its small model; a gateway only what its settings name."""
    kind = connection.get("kind")
    if kind == "anthropic-gateway":   # a gateway routes its own names: "haiku" means nothing unless set
        return str(connection.get("small_model") or (connection.get("model_ids") or {}).get("haiku") or "") or None
    if kind == "litellm":
        from . import gateway as managed
        return managed.small_model(connection)
    return str((connection.get("model_ids") or {}).get("haiku") or "haiku")


def _read(path: Path) -> dict:
    try:
        found = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


class Picker:
    def __init__(self, studio: "Studio"):
        self.studio = studio
        self.lib = studio.lib
        data = studio.settings.data_dir
        self.prefs_path, self.usage_path = data / "library-prefs.json", data / "library-usage.json"
        self.turns: dict[str, dict] = {}     # project id → what the running turn was given
        self.timeout = float(os.environ.get("STUDIO2_PICK_TIMEOUT") or 6)
        self.unused_after = int(os.environ.get("STUDIO2_LIBRARY_UNUSED_AFTER") or 5)
        self.ask = None                      # replaced in tests: async (connection, system, prompt) → text
        self.resting: dict[tuple[str, str], float] = {}   # (connection id, route) → not asked before this time
        self.told: set[str] = set()          # connections whose missing cheap model was explained

    # -- what the person said, for the whole machine ----------------------------------------
    def prefs(self) -> dict:
        found = _read(self.prefs_path)
        return {"skills": dict(found.get("skills") or {}), "mcp_servers": dict(found.get("mcp_servers") or {})}

    def set_prefs(self, body: dict) -> dict:
        """`{skills: {id: "always" | "never" | null}, mcp_servers: {…}}` → the new preferences."""
        prefs, usage = self.prefs(), self.usage()
        servers = {server["id"] for server in self.lib.servers()}
        for kind, known in (("skills", lambda key: self.lib.skill(key) is not None),
                            ("mcp_servers", lambda key: key in servers)):
            for key, value in (body.get(kind) or {}).items():
                if value not in ("always", "never", None):
                    raise invalid(f"{key}: say always, never or null.")
                if not known(key):
                    raise invalid(f"There is no {'skill' if kind == 'skills' else 'MCP server'} {key}.")
                if value is None:
                    prefs[kind].pop(key, None)
                else:
                    prefs[kind][key] = value
                if kind == "skills" and key in usage and value != "never":
                    usage[key]["streak"] = 0     # a decision by the person gives a skill a fresh start
        write_json_atomic(self.prefs_path, prefs)
        write_json_atomic(self.usage_path, usage)
        return prefs

    def usage(self) -> dict:
        return _read(self.usage_path)

    def benched(self, usage: dict | None = None) -> set[str]:
        """Skills picked again and again and never used: no longer picked."""
        return {key for key, row in (usage or self.usage()).items() if int(row.get("streak") or 0) >= self.unused_after}

    def decision(self, project: dict | None, kind: str, item_id: str, prefs: dict | None = None) -> tuple[str | None, str]:
        """→ ("always" | "never" | None, "project" | "machine"): the project's word first, then the machine's."""
        choice = (((project or {}).get("library") or {}).get(kind) or {}).get(item_id)
        if choice is not None:
            return ("always" if choice else "never"), "project"
        return ((prefs or self.prefs()).get(kind) or {}).get(item_id), "machine"

    # -- the library screen -----------------------------------------------------------------
    def items(self, kind: str, query: str = "", source: str | None = None, state: str | None = None,
              offset: int = 0, limit: int = 50, project: dict | None = None) -> dict:
        """One page of the index for the library screen (kind: skills | agents | connectors)."""
        prefs, usage = self.prefs(), self.usage()
        benched = self.benched(usage)
        meta = self.lib.catalog.ensure()
        if kind == "connectors":
            workspace = self.studio.store.workspace(project["id"]) if project and project.get("attach") else None
            found = self.lib.servers() + self.lib.app_servers(workspace)
            asked = set(words(query))
            if asked:
                found = [s for s in found if asked & set(words(f"{s['name']} {s['label']} {s['description']} {s.get('origin') or ''}"))
                         or query.strip().lower() in s["name"]]
            found = [{k: v for k, v in server.items() if k != "_config"} for server in found]
            pref_kind = "mcp_servers"
        else:
            found = [self.lib.public(e) for e in self.lib.catalog.listing("skill" if kind == "skills" else "agent", query)]
            pref_kind = "skills"
        rows = []
        for item in found:
            row = dict(item)
            if kind != "agents":
                row["pref"] = (prefs.get(pref_kind) or {}).get(item["id"])
                row["choice"] = (((project or {}).get("library") or {}).get(pref_kind) or {}).get(item["id"]) if project else None
            if kind == "skills":
                seen = usage.get(item["id"]) or {}
                row.update(picked=int(seen.get("picked") or 0), used=int(seen.get("used") or 0),
                           benched=item["id"] in benched)
            rows.append(row)
        if state:   # always | never | benched | signin
            keep = {"always": lambda r: r.get("pref") == "always" or r.get("choice") is True,
                    "never": lambda r: r.get("pref") == "never" or r.get("choice") is False,
                    "benched": lambda r: r.get("benched"),
                    "signin": lambda r: r.get("state") in ("needs-auth", "needs-setting")}.get(state)
            rows = [row for row in rows if keep and keep(row)]
        counts: dict[str, int] = {}     # per source, before the source filter: the filter's own labels
        for row in rows:
            counts[row["source"]] = counts.get(row["source"], 0) + 1
        if source:
            rows = [row for row in rows if row["source"] == source]
        offset, limit = max(0, offset), max(1, min(200, limit))
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit,
                "counts": counts, "index": self.summary(meta)}

    def summary(self, meta: dict | None = None) -> dict:
        meta = meta or self.lib.catalog.ensure()
        servers = self.lib.servers()
        by_source: dict[str, int] = {}
        for server in servers:
            by_source[server["source"]] = by_source.get(server["source"], 0) + 1
        return {"skills": sum(meta["counts"]["skill"].values()), "agents": sum(meta["counts"]["agent"].values()),
                "connectors": len(servers), "by_source": {"skills": meta["counts"]["skill"],
                                                         "agents": meta["counts"]["agent"], "connectors": by_source},
                "plugins": meta["plugins"], "plugins_enabled": meta["plugins_enabled"],
                "duplicates": meta["duplicates"], "built_at": meta["built_at"], "build_ms": meta["build_ms"]}

    # -- before a turn ----------------------------------------------------------------------
    async def pick(self, project: dict, text: str, connection: dict, workspace: Path | None = None,
                   require: list[str] | tuple = (), check_only: bool = False) -> dict:
        """Choose this request's skills and connectors. Never raises: a failure picks less.
        `check_only` (the "Check this page" request): only what the person chose; nothing by itself."""
        started = time.monotonic()
        await asyncio.to_thread(self.lib.catalog.ensure)
        prefs, usage = self.prefs(), self.usage()
        benched = self.benched(usage)
        scope = {"project": "in this project", "machine": "on this computer"}
        skills: list[dict] = []
        seen: set[str] = set()

        def add(entry: dict, how: str, reason: str, into: list, limit: int) -> bool:
            if entry["id"] in seen or len([i for i in into if i["how"] == how]) >= limit:
                return False
            seen.add(entry["id"])
            into.append({"id": entry["id"], "name": entry["name"], "label": entry.get("label") or entry["name"],
                         "source": entry["source"], "origin": entry.get("origin"), "how": how, "reason": reason,
                         **({"state": entry.get("state"), "fix": entry.get("fix")} if "transport" in entry else {})})
            return True

        pool = self.lib.catalog.skills(("machine", "plugin"))
        # Skills are loaded by folder name and the first of a name wins, so a skill that shares its
        # name with one of the studio's own, or with one that is benched, is not offered again.
        shadowed = {skill["name"] for skill in self.lib.skills()}
        shadowed |= {entry["name"] for entry in pool if entry["id"] in benched}
        switched = (project.get("library") or {}).get("skills") or {}
        for skill_id in require:   # e.g. Deep asks for deep-research; a skill switched off stays off
            entry = self.lib.skill(skill_id)
            if entry and switched.get(skill_id, True) is not False \
                    and self.decision(project, "skills", skill_id, prefs)[0] != "never":
                add(entry, "depth", "Deep was chosen for this request.", skills, 10)
        auto: dict[str, dict] = {}
        for entry in pool:
            said, where = self.decision(project, "skills", entry["id"], prefs)
            if said == "always":
                add(entry, "pinned", f"You said always {scope[where]}.", skills, 200)
            elif said is None and entry.get("enabled", True) and entry["name"] not in shadowed:
                auto[entry["id"]] = entry
        if check_only:
            auto = {}
        for skill_id in [] if check_only else list(project.get("library_used") or [])[:MAX_EARLIER]:
            entry = self.lib.skill(skill_id)
            if entry and self.decision(project, "skills", skill_id, prefs)[0] != "never":
                add(entry, "earlier", "Used earlier in this project.", skills, MAX_EARLIER)

        candidates: list[dict] = []
        workspace = workspace if project.get("attach") else None
        connectors: list[dict] = []
        requested = list((project.get("library") or {}).get("requested") or [])
        signin: list[dict] = []      # need signing in: only ever picked when the request names them
        waiting: list[dict] = []     # asked for by the agent, and need signing in first
        crowded: list[dict] = []     # switched on, and one too many for a request: named in the note
        for server in self.lib.servers() + self.lib.app_servers(workspace):
            if server["source"] == "suggested":
                continue
            said, where = self.decision(project, "mcp_servers", server["id"], prefs)
            if said == "always":
                # What the person switched on is connected, up to what one project may have on
                # (not the smaller limit for what the studio adds by itself).
                if not add(server, "pinned", f"You said always {scope[where]}.", connectors, MAX_ENABLED_SERVERS) \
                        and server["id"] not in seen:
                    crowded.append(server)
            elif check_only:
                continue
            elif said is None and server["id"] in requested:
                if server.get("state") == "needs-auth":
                    waiting.append(server)     # asked for, and joins once it is seen connected
                else:
                    add(server, "requested", "The assistant asked for it in an earlier request.", connectors, MAX_CONNECTORS)
            elif said is None and server["source"] != "app" and server.get("plugin_enabled", True):
                if server.get("state") not in ("needs-setting", "needs-auth", "failed"):
                    candidates.append(server)
                elif server.get("state") == "needs-auth":
                    signin.append(server)
        room = max(0, MAX_CONNECTORS - len(connectors))

        mode, note, shortlist, route = "none", None, [], None
        if check_only:
            note = ("A check of the page: only the skills and connectors you chose are added; the studio "
                    "picked nothing by itself.")
        elif len(auto) <= SMALL_LIBRARY:
            mode = "all"
            for entry in auto.values():
                add(entry, "all", "Your whole library is small enough to load.", skills, SMALL_LIBRARY)
        else:
            names: set[str] = set()
            for found in self.lib.catalog.search(text, "skill", SHORTLIST * 2, allow=lambda e: e["id"] in auto):
                if found[0]["name"] not in names and len(shortlist) < SHORTLIST:   # one skill per name
                    names.add(found[0]["name"])
                    shortlist.append(found)
        if (shortlist or (candidates and room)) and text.strip():
            chosen = None
            if not self.studio.settings.fake and connection.get("kind") != "fake" or self.ask:
                chosen, route, note = await self._ask_model(text, connection, shortlist, candidates[:40], room)
                if chosen is not None:
                    mode = "model" if mode != "all" else mode
            if chosen is None:
                mode, route = "words" if mode != "all" else mode, "words"
                note = note or "No model was asked (the fake agent), so the word search decided."
                chosen = self._by_words(text, shortlist, candidates, room)
                if shortlist and not chosen[0]:
                    note += " No skill matched the request closely enough, so none was added."
            for entry, reason in chosen[0]:
                add(entry, "picked", reason, skills, MAX_SKILLS)
            for server, reason in chosen[1]:
                add(server, "picked", reason, connectors, room)
        left = self._signin(text, signin, add, connectors, room) if signin and room and text.strip() else []
        left += [server for server in waiting if server not in left]
        if left:   # one sentence, however many: the Library's "signin" filter has the rest
            they = "it needs" if len(left) == 1 else "they need"
            fixes = "; ".join(f"`{server['fix']}`" for server in left)
            said = (f"Left out {', '.join(server['label'] for server in left)}: {they} signing in ({fixes}), "
                    "then switch it on for this project or test it in the Library.")
            note = f"{note} {said}" if note else said
        if crowded:
            said = (f"Left out {', '.join(server['label'] for server in crowded)}: a request connects at most "
                    f"{MAX_ENABLED_SERVERS} connectors that are switched on. Switch one off to make room.")
            note = f"{note} {said}" if note else said
        return {"mode": mode, "skills": skills, "connectors": connectors, "library_skills": len(pool),
                "considered": len(shortlist), "ms": int((time.monotonic() - started) * 1000), "note": note,
                "route": route}

    @staticmethod
    def _by_words(text: str, shortlist: list, candidates: list[dict], room: int) -> tuple[list, list]:
        # Words cannot judge relevance: a long request shares a word or two with almost any skill
        # ("report", "data", "start"), so a skill is taken only when it matches a good share of what
        # was asked. Otherwise nothing is picked; the agent has search_library and activate_skill.
        top = shortlist[0][1] if shortlist else 0.0
        asked = set(words(text))
        need = max(min(2, len(asked)), len(asked) * WORDS_SHARE)
        skills = [(entry, "Matches your words: " + ", ".join(sorted(set(hits))[:4]) + ".")
                  for entry, score, hits in shortlist if score >= top * 0.45 and len(set(hits) & asked) >= need]
        skills = skills[:WORDS_ONLY]
        servers = []
        for server in candidates:
            if len(servers) >= min(room, 3):
                break
            hit = sorted(asked & Picker.own_words(server))
            told = sorted(asked & set(words(server.get("description") or "")))
            if hit:
                servers.append((server, f"Its name matches your words: {', '.join(hit)}."))
            elif len(told) >= need:     # the same share as a skill must reach, from its file or its tools
                servers.append((server, f"What it does matches your words: {', '.join(told[:4])}."))
        return skills, servers

    @staticmethod
    def own_words(server: dict) -> set[str]:
        """The words of a connector's own name. A plugin's connector whose name was taken carries
        the plugin's name too (`slack-legal`); that is the studio's suffix, not what the connector
        is, so a word of the plugin's ("legal") must not pick it: its label is its own name."""
        own = server["label"] if server["source"] in ("plugin", "app") else f"{server['name']} {server['label']}"
        return {stem(w) for w in re.findall(r"[a-z0-9]+", own.lower())}

    def _signin(self, text: str, signin: list[dict], add, connectors: list, room: int) -> list[dict]:
        """Connectors that need signing in, named in the request. One never tried is picked (the
        first try shows whether it really needs it); one the agent program reported as needing it
        is left out until it is seen connected, and returned so the note can say so."""
        asked, seen, left = set(words(text)), self.lib.statuses(), []
        labels = {str(item["label"]).lower() for item in connectors}
        for server in signin:
            # Two plugins often ship the same service under one name (`slack`): the first is enough.
            if not asked & self.own_words(server) or server["label"].lower() in labels:
                continue
            labels.add(server["label"].lower())
            if (seen.get(server["name"]) or {}).get("status") == "needs-auth":
                left.append(server)
            else:
                add(server, "picked", f"You named it. It may need signing in first: `{server['fix']}`.",
                    connectors, room)
        return left

    def routes(self, connection: dict) -> list[tuple[str, dict]]:
        """Where the choosing question may be asked: the connection of the request itself, when
        it has a cheap model and that did not fail in the last ROUTE_REST seconds. Never another
        connection: a request's text goes only to the provider the person chose for it."""
        if not picking_model(connection):
            return []
        if self.resting.get((str(connection.get("id")), "connection"), 0.0) > time.monotonic():
            return []
        return [("connection", connection)]

    async def _call(self, route: str, connection: dict, system: str, prompt: str, timeout: float) -> str:
        if self.ask:
            return await asyncio.wait_for(self.ask(connection, system, prompt), timeout)
        from .harness import cheap
        scratch = self.studio.settings.data_dir / "picker"
        scratch.mkdir(parents=True, exist_ok=True)
        endpoint = None
        if connection.get("kind") == "litellm":   # the studio's own LiteLLM (step 3n): its cheap model slot
            harness = self.studio.registry.harness("litellm")
            # A cold LiteLLM takes a few seconds to start. Running out of time here must not
            # cancel the start half way: the request that follows needs the same process.
            start = asyncio.ensure_future(harness.sidecar.ensure(harness.connections()))
            start.add_done_callback(lambda task: task.cancelled() or task.exception())
            endpoint = await asyncio.wait_for(asyncio.shield(start), timeout)
        return await cheap.ask(connection, system, prompt, timeout, secrets=self.studio.settings.secret_store(),
                               workdir=str(scratch), endpoint=endpoint)

    async def _ask_model(self, text: str, connection: dict, shortlist: list, candidates: list[dict],
                         room: int) -> tuple[tuple[list, list] | None, str | None, str | None]:
        """→ (choice or None, the route that answered, a note). Never raises."""
        own = ", ".join(skill["name"] for skill in self.lib.skills())
        lines = [f"Request:\n{' '.join(text.split())[:2000]}", "",
                 f"The assistant already has these skills; do not pick the same thing again: {own}", "", "Skills:"]
        for number, (entry, _score, _hits) in enumerate(shortlist, 1):
            lines.append(f"S{number} {entry['name']} — {entry['description'][:200] or '(no description)'}")
        if not shortlist:
            lines.append("(none)")
        lines += ["", "Connectors:"]
        offered = candidates if room else []
        for number, server in enumerate(offered, 1):
            lines.append(f"C{number} {server['label']} — {server['description'][:140]}")
        if not offered:
            lines.append("(none)")
        system, prompt = SYSTEM.format(skills=MAX_SKILLS, connectors=room), "\n".join(lines)
        cheap = picking_model(connection)
        said = {"connection": f"this connection's cheap model ({cheap})"}
        routes, cid = self.routes(connection), str(connection.get("id"))
        if not cheap:
            note = "This connection has no cheap model for picking, so the word search decided."
            if cid not in self.told:      # how to fix it, said once per connection
                self.told.add(cid)
                note = ("This connection has no cheap model for picking; add `small_model` to it "
                        "(in setup, or in the studio's config file). Until then the word search decides.")
            return None, None, note
        if not routes:
            return None, None, (f"This connection's cheap model ({cheap}) did not answer a few minutes ago "
                                f"(tried again within {ROUTE_REST / 60:g} minutes), so the word search decided.")
        deadline, failed, answer, route = time.monotonic() + self.timeout, [], None, None
        for route, where in routes:
            left = deadline - time.monotonic()
            if left < 0.5 and failed:
                break                                    # the first route used the time up
            try:
                answer = await self._call(route, where, system, prompt, max(left, 0.05))
                break
            except asyncio.TimeoutError:
                failed.append(f"{said[route]} took more than {self.timeout:g} seconds")
            except Exception as exc:  # noqa: BLE001 - picking must never fail a turn
                log.info("Picking through %s failed: %s", route, exc)
                failed.append(f"{said[route]} did not answer")
            self.resting[(cid, route)] = time.monotonic() + ROUTE_REST   # do not pay this delay every request
        tried = " and ".join(failed)
        if answer is None:
            return None, None, f"{tried[:1].upper()}{tried[1:]}, so the word search decided."
        self.resting.pop((cid, route), None)
        note = f"Chosen by {said[route]}" + (f" ({tried})." if tried else ".")
        skills, servers = [], []
        for kind, number, reason in re.findall(r"^\W*([SC])\s?(\d+)\s*[:—–-]\s*(.+?)\s*$", answer, re.M):
            index = int(number) - 1
            reason = reason.strip(" .`*")[:140]
            reason = reason[:1].upper() + reason[1:] + "."
            if kind == "S" and 0 <= index < len(shortlist):
                skills.append((shortlist[index][0], reason))
            elif kind == "C" and 0 <= index < len(offered):
                servers.append((offered[index], reason))
        return (skills, servers), route, note

    # -- during and after a turn ------------------------------------------------------------
    def begin(self, project_id: str, turn_id: str, picked: dict, loaded: list[str], plugin_dir: Path,
              connected: list[str]) -> None:
        names = {}
        for skill_id in loaded:
            entry = self.lib.skill(skill_id)
            if entry:
                names[entry["name"]] = skill_id
        auto = [item["id"] for item in picked["skills"] if item["how"] == "picked" and item["id"] in loaded]
        self.turns[project_id] = {"turn_id": turn_id, "names": names, "auto": auto, "used": set(),
                                  "plugin_dir": plugin_dir, "connected": set(connected)}

    def note_used(self, project_id: str, name: str) -> None:
        """The agent called a skill (the Skill tool, or activate_skill): count it."""
        turn = self.turns.get(project_id)
        skill_id = (turn or {}).get("names", {}).get(str(name).split(":")[-1])
        if not skill_id or skill_id in turn["used"]:
            return
        turn["used"].add(skill_id)
        usage = self.usage()
        row = usage.setdefault(skill_id, {})
        row.update(used=int(row.get("used") or 0) + 1, streak=0, last_used=now_iso())
        write_json_atomic(self.usage_path, usage)
        entry = self.lib.skill(skill_id)
        if entry and not entry["default"]:
            try:
                project = self.studio.store.get(project_id)
                kept = [skill_id] + [i for i in project.get("library_used") or [] if i != skill_id]
                self.studio.store.update(project_id, library_used=kept[:MAX_EARLIER])
            except Exception:  # noqa: BLE001 - the project was deleted meanwhile
                pass

    def finish(self, project_id: str, status: str) -> None:
        turn = self.turns.pop(project_id, None)
        if not turn or status != "completed" or not turn["auto"]:
            return
        usage = self.usage()
        for skill_id in turn["auto"]:
            row = usage.setdefault(skill_id, {})
            row["picked"] = int(row.get("picked") or 0) + 1
            row["last_picked"] = now_iso()
            if skill_id not in turn["used"]:
                row["streak"] = int(row.get("streak") or 0) + 1
        write_json_atomic(self.usage_path, usage)

    # -- the agent's own tools --------------------------------------------------------------
    def _find(self, project: dict, kind: str, wanted: str) -> dict:
        wanted = str(wanted or "").strip()
        if kind == "skills":
            entry = self.lib.skill(wanted)
            if entry is None:
                same = [e for e in self.lib.catalog.skills() if e["name"] == wanted.split(":")[-1]]
                entry = same[0] if same else None
        else:
            workspace = self.studio.store.workspace(project["id"]) if project.get("attach") else None
            servers = [s for s in self.lib.servers() + self.lib.app_servers(workspace) if s["source"] != "suggested"]
            entry = next((s for s in servers if wanted in (s["id"], s["name"], s["label"])), None)
        if entry is None:
            raise LibraryError(404, "not_found", f"There is no {'skill' if kind == 'skills' else 'connector'} “{wanted}” "
                               "in the library. Use search_library to find the right id.")
        if self.decision(project, kind, entry["id"])[0] == "never":
            raise LibraryError(403, "switched_off", f"The person switched “{entry['name']}” off, so it stays off. "
                               "Carry on without it, or tell them what you needed it for.")
        return entry

    def search(self, project: dict, query: str, kind: str = "all", limit: int = 12) -> dict:
        turn = self.turns.get(project["id"]) or {}
        prefs = self.prefs()
        out: dict = {}
        limit = max(1, min(25, int(limit or 12)))
        if kind in ("all", "skill", "skills"):
            found = self.lib.catalog.search(query, "skill", limit * 2)
            out["skills"] = [{"id": e["id"], "name": e["name"], "description": e["description"][:300], "from": e["origin"],
                              "loaded": e["id"] in (turn.get("names") or {}).values()}
                             for e, _s, _h in found if self.decision(project, "skills", e["id"], prefs)[0] != "never"][:limit]
        if kind in ("all", "connector", "connectors"):
            asked = {w for w in words(query)}
            workspace = self.studio.store.workspace(project["id"]) if project.get("attach") else None
            rows = []
            for server in self.lib.servers() + self.lib.app_servers(workspace):
                text = f"{server['name']} {server['label']} {server['description']} {server.get('origin') or ''}"
                score = len(asked & set(words(text)))
                if server["source"] == "suggested" or not score or self.decision(project, "mcp_servers", server["id"], prefs)[0] == "never":
                    continue
                rows.append((score, {"id": server["id"], "name": server["name"], "description": server["description"][:200],
                                     "from": server.get("origin") or server["source"],
                                     "connected": server["name"] in (turn.get("connected") or set()),
                                     **({"state": server["state"], "fix": server["fix"]} if server.get("fix") else {})}))
            out["connectors"] = [row for _score, row in sorted(rows, key=lambda r: -r[0])[:limit]]
        out["next"] = ("To use a skill now, call activate_skill with its id. To get a connector, call request_connector "
                       "with its id: it is connected from the next request.")
        return out

    def activate(self, project: dict, wanted: str) -> dict:
        entry = self._find(project, "skills", wanted)
        turn = self.turns.get(project["id"])
        target = (turn or {}).get("plugin_dir") or self.studio.store.dir(project["id"]) / "library"
        folder = target / "skills" / entry["name"]
        if not (folder / "SKILL.md").is_file():
            if self.lib.copy_skill(entry, target) is None:
                raise LibraryError(413, "too_large", f"“{entry['name']}” carries more than 20 MB of files and is not copied. "
                                   f"Its folder is {entry['dir']}; read SKILL.md there.")
        text = (folder / "SKILL.md").read_text("utf-8", "replace")
        body = re.sub(r"\A\s*---[ \t]*\n.*?\n---[ \t]*\n", "", text, count=1, flags=re.S).strip()
        files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and p.name != "SKILL.md")
        if turn is not None:
            turn["names"][entry["name"]] = entry["id"]
        self.note_used(project["id"], entry["name"])
        return {"skill": entry["name"], "id": entry["id"], "folder": str(folder),
                "instructions": body[:40_000] + ("\n… (cut: read the rest in SKILL.md in the folder)" if len(body) > 40_000 else ""),
                "files": files[:80], "more_files": max(0, len(files) - 80),
                "next": "Follow these instructions now, for the task in hand. Files and scripts the skill mentions are in "
                        "`folder`: read and run them from there (it is outside the project folder, so do not write there). "
                        "The skill stays loaded for the rest of this project."}

    def request(self, project: dict, wanted: str, reason: str = "") -> dict:
        server = self._find(project, "mcp_servers", wanted)
        turn = self.turns.get(project["id"]) or {}
        if server["name"] in (turn.get("connected") or set()):
            return {"connector": server["name"], "connected": True,
                    "next": f"It is already connected in this request: its tools are named mcp__{server['name']}__…."}
        if server.get("state") == "needs-setting":
            raise LibraryError(409, "needs_setting", f"“{server['name']}” cannot be connected yet. {server['fix']} Tell the person.")
        chosen = dict(project.get("library") or {})
        asked = [server["id"]] + [i for i in chosen.get("requested") or [] if i != server["id"]]
        self.studio.store.update(project["id"], library={**chosen, "requested": asked[:MAX_CONNECTORS]})
        if server.get("state") == "needs-auth":
            # Kept as asked for, but not added until it is seen connected (CONTRACT.md, "Signing in").
            self.studio.log(project["id"]).append(
                "notice", turn.get("turn_id"), level="warning",
                message=(f"The assistant asked for the connector {server['label']}. It needs signing in first: run "
                         f"`{server['fix']}` in a terminal, then test it in the Library. It joins the next "
                         "request after that."))
            return {"connector": server["name"], "connected": False, "joins": "after signing in",
                    "sign_in": f"The person runs `{server['fix']}` in a terminal once, then tests it in the Library.",
                    "next": f"“{server['label']}” is NOT connected and will not be until the person signs in: it needs "
                            f"signing in first. The studio has told the person. Finish what you can without it, and say "
                            f"plainly that they need to run `{server['fix']}` and then send another message."}
        out = {"connector": server["name"], "connected": False, "joins": "the next request",
               "next": f"“{server['label']}” is NOT available in this request and its tools cannot be called now. The "
                       "studio connects it when the person sends their next message. Say that plainly: finish what you "
                       "can without it, then tell the person to send a follow-up (for example “go on”) to use it."}
        return out
