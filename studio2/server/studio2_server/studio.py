"""The Studio object: wires store, event logs, previews, watchers, harnesses and turns."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import time

from pathlib import Path

from .effort import DEEP_SKILL
from .build import Build
from . import effort
from . import attach as attaching
from . import context as contexts
from . import workspace as ws
from .access import AccessGuard, gate_for, known_names, preview_url_for
from .apps import Apps
from .config import REPO_ROOT, Settings, known_studios, model_gateway, now_iso
from .datasources import KINDS as DATA_KINDS
from .datasources import DataSources, KeychainSecrets, MemorySecrets, driver_available
from .events import EventHub, EventLog
from .examples import Examples
from .harness import Registry, empty_usage
from . import generate, instructions, media, office, services, ship as shipping, tools
from .modelcheck import ModelChecks
from .costs import CURRENT_TURN, RESTART_ERROR, Costs
from .library import KeychainJson, Library, MemoryJson
from .look import Looker
from .session import SETTING as SIGNED_IN_SETTING, Sessions
from .picker import Picker
from .preview import Previews
from .sources import Sources
from .store import ProjectNotFound, Store
from .templates import TemplateError, Templates
from .templates import private as template_private
from .turns import TurnRunner
from .watcher import Watchers

log = logging.getLogger("studio2")


class Studio:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings)
        self.hub = EventHub()
        self.registry = Registry(settings)
        self.names = known_names(settings)   # the DNS names every port answers to (DNS rebinding)
        # The person's sign-in for each project, in memory only (session.py).
        self.sessions = Sessions(lambda: bool(self.settings.shared), self._project_or_none, self._project_on_port,
                                 folder=self.store.dir, own_names=lambda: self.names | {
                                     h.strip().lower().strip("[]") for h in settings.hosts})
        self.gate = gate_for(settings)       # one for every port: it keeps the gate's keys
        self.previews = Previews(settings.hosts, tls=settings.tls, guard=lambda app: AccessGuard(
            app, settings.access_key, settings.port, own_login=False, open_tailnet=settings.open_tailnet,
            names=self.names, gate=self.gate), sessions=self.sessions)
        self.watchers = Watchers(self._files_changed)
        self.turns = TurnRunner(self)
        self.saved = Templates(settings)
        self.examples = Examples(lambda: self.settings.library_dir)
        self.apps = Apps(self._app_status)
        self.looker = Looker(self.sessions)
        self.ships = shipping.Shipper(self)
        self.generator = generate.Generator(settings.data_dir, settings.generate_daily_credits)
        self.everywhere = instructions.Everywhere(settings.data_dir / "instructions.json")
        self.build = Build()      # the code this process started with (build.py)
        self.checks = ModelChecks(self)     # "check this model" (modelcheck.py)
        self.costs = Costs(self)            # what a gateway turn cost, read back (costs.py)
        self.approvers: dict = {}       # project id → the running turn's approval function
        self.data = DataSources(settings.data_dir / "data-sources.json",
                                MemorySecrets() if settings.fake else KeychainSecrets(settings.secret_store()))
        self.lib = Library(settings, MemoryJson() if settings.fake else KeychainJson(store=settings.secret_store()))
        self.lib.project_folders = lambda: self.store.in_place_folders()   # where the agent writes, besides data/
        self.picker = Picker(self)
        self._locks: dict[str, asyncio.Lock] = {}
        self._background: set[asyncio.Task] = set()
        self.deleting: set[str] = set()   # projects being deleted: nothing new starts for them

    # -- plumbing ---------------------------------------------------------------------------
    def log(self, project_id: str) -> EventLog:
        return self.hub.get(project_id, self.store.events_path(project_id))

    def lock(self, project_id: str) -> asyncio.Lock:
        """Serialises git work in one workspace."""
        return self._locks.setdefault(project_id, asyncio.Lock())

    def watch(self, project_id: str) -> None:
        self.watchers.ensure(project_id, self.store.workspace(project_id))

    def _files_changed(self, project_id: str, paths: list[str]) -> None:
        if project_id in self.hub.logs:
            self.log(project_id).append("files_changed", self.turns.active.get(project_id), paths=paths)
        if "studio.json" in paths and project_id not in self.turns.active:
            self.spawn(self.sync_app(project_id))  # during a turn the app is synced when the turn ends

    def _project_or_none(self, project_id: str) -> dict | None:
        try:
            return self.store.get(project_id)
        except ProjectNotFound:
            return None

    def _project_on_port(self, port: int) -> str | None:
        return next((pid for pid, entry in self.previews.running.items() if entry[2] == port), None)

    def spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _app_status(self, project_id: str, fields: dict) -> None:
        try:
            self.log(project_id).append("app_status", self.turns.active.get(project_id), **fields)
        except Exception:  # the project was deleted while its app was stopping
            log.debug("Dropped an app_status for %s", project_id)

    # -- templates --------------------------------------------------------------------------
    def templates(self) -> list[dict]:
        found = []
        for folder in sorted((self.settings.library_dir / "templates").glob("*/template.json")):
            try:
                meta = json.loads(folder.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            files = folder.parent / "files"
            is_app = (files / "studio.json").is_file()
            found.append({"id": folder.parent.name, "label": str(meta.get("label") or folder.parent.name),
                          "description": str(meta.get("description") or ""),
                          "app": is_app, "order": meta.get("order", 50), "source": "studio",
                          "kind": "app" if is_app else "page", "mode": "copy", "preview": None,
                          "created_at": None, "from_project": None, "main": None, "imported": False,
                          "has_code": is_app})
        found.sort(key=lambda item: (item.pop("order"), item["label"]))
        return found + self.saved.list()

    async def save_template(self, project_id: str, name: str, description: str = "", kind: str | None = None,
                            file: str | None = None, notes: str = "") -> dict:
        """Save the project (or one Office file in it) as a template, with a picture of it."""
        project, workspace = self.store.get(project_id), self.store.workspace(project_id)
        if project.get("attach") and not file:
            raise TemplateError("A project made from an app on this computer cannot be saved as a page or app "
                                "template: it would copy the app, and its env files, into the studio. A single "
                                "Office file from it can be.", "attached_app", 400)
        saved = await asyncio.to_thread(self.saved.save, project, workspace, name, description, kind, file, notes)
        if saved["preview"] is None and saved["kind"] in ("page", "app") and self.looker.reason() is None:
            try:   # a picture for the template list; never a reason to fail the save
                opened = await self.open_preview(project)
                # Never with the person's sign-in: the picture goes with the template.
                shot = await self.looker.look(f"http://127.0.0.1:{opened['port']}/", self.store.dir(project_id) / "looks",
                                              width=1280, height=800, full_page=False, tiles=False, layout=False,
                                              signed_in=False)
                self.saved.set_preview(saved["id"], Path(shot["image"]))
                saved = self.saved.get(saved["id"])
            except Exception:
                log.debug("no preview image for template %s", saved["id"], exc_info=True)
        return saved

    async def use_template(self, project_id: str, template_id: str | None) -> dict:
        """Attach a saved template to a project as a pattern (None removes it)."""
        workspace = self.store.workspace(project_id)
        if self.store.get(project_id).get("attach"):
            # The pattern lives in `template/` and is removed with rmtree: never in a person's app.
            raise TemplateError("A project made from an app on this computer cannot follow a template pattern.",
                                "attached_app", 400)
        if template_id is None:
            shutil.rmtree(workspace / "template", ignore_errors=True)
            self.store.update(project_id, uses_template=None)
            await self.checkpoint(project_id, "user", "Stop using the template")
            return self.store.get(project_id)
        template = self.saved.get(template_id)
        if template is None:
            raise TemplateError("There is no such template.", "unknown_template", 404)
        await asyncio.to_thread(self.saved.place, template_id, workspace)
        self.store.update(project_id, uses_template={"id": template["id"], "label": template["label"],
                                                     "kind": template["kind"]})
        await self.checkpoint(project_id, "user", f"Use the template “{template['label']}”")
        return self.store.get(project_id)

    # -- project apps -----------------------------------------------------------------------
    def view(self, project: dict) -> dict:
        """The Project object the API returns."""
        app = self.apps.get(project["id"])
        known = {item["id"] for item in self.data.list()}
        public = {key: value for key, value in project.items() if key not in ("token", "name_offered", "instructions_cleared")}
        return {**public, "preview_url": preview_url_for(project["port"]),
                "name_suggestion": project.get("name_suggestion") or None,
                "approvals": project.get("approvals", "ask"),
                "data_sources": [i for i in project.get("data_sources") or [] if i in known],
                "context": project.get("context") or [],
                "instructions": project.get("instructions") or "",
                # All three keys always: the interface shows the scale only when `involve` is there.
                "turn_options": self._turn_options(project),
                "library": project.get("library") or {"skills": {}, "mcp_servers": {}},
                "uses_template": project.get("uses_template") or None,
                "from_example": project.get("from_example") or None,
                "attach": project.get("attach") or None, "app_config": project.get("app_config"),
                "signed_in_looks": self.sessions.allowed(project),
                "ship": self.ships.view(project) if project.get("attach") else None,
                "app": {"configured": bool(app and (app.run or app.address)), "state": app.state if app else "none"}}

    @staticmethod
    def _turn_options(project: dict) -> dict:
        try:
            kept = effort.clean(project.get("turn_options"), False)
        except effort.OptionsError:     # a record written by hand: the view never fails on it
            kept = {}
        return {**effort.DEFAULT, "involve": 0, **kept}

    def token(self, project_id: str) -> str:
        project = self.store.get(project_id)
        if not project.get("token"):
            project = self.store.update(project_id, token=secrets.token_urlsafe(24))
        return project["token"]

    def data_url(self, project_id: str) -> str:
        return f"http://127.0.0.1:{self.settings.port}/api/projects/{project_id}/data"

    @staticmethod
    def gateway_client():
        import httpx
        return httpx.AsyncClient(timeout=300.0)

    def model_url(self, project_id: str) -> str:
        return f"http://127.0.0.1:{self.settings.port}/api/projects/{project_id}/model/v1"

    def model_spend_today(self, project_id: str) -> float:
        """Dollars this project's back end has spent on models today, as the gateway reported them."""
        today, total = time.strftime("%Y-%m-%d"), 0.0
        try:
            for line in (self.store.dir(project_id) / "model-usage.jsonl").read_text("utf-8").splitlines():
                row = json.loads(line)
                if row.get("day") == today:
                    total += float(row.get("cost") or 0)
        except (OSError, ValueError):
            pass
        return total

    def record_model_use(self, project_id: str, model: str, cost: float | None, usage: dict | None) -> None:
        row = {"day": time.strftime("%Y-%m-%d"), "at": time.strftime("%H:%M:%S"), "model": model, "cost": cost,
               "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
               if isinstance(usage, dict) else None}
        with (self.store.dir(project_id) / "model-usage.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def attached_sources(self, project_id: str) -> list[dict]:
        wanted = self.store.get(project_id).get("data_sources") or []
        return [item for item in self.data.list() if item["id"] in wanted]

    # -- library ----------------------------------------------------------------------------
    def record_session(self, info: dict) -> None:
        """What the agent CLI reported at start-up: the built-in skills and MCP servers it has."""
        try:
            (self.settings.data_dir / "last-session.json").write_text(
                json.dumps({**info, "observed_at": now_iso()}), "utf-8")
        except OSError:
            log.debug("Could not save the session info")
        self.lib.record_statuses(info.get("mcp_servers") or [])

    async def ask(self, project_id: str, tool: str, title: str, detail: str, reason: str,
                  detail_limit: int | None = None, skill: dict | None = None) -> bool:
        """Ask the person, through the running turn, whatever the project's approval setting.
        With no turn running there is nobody to ask, so the answer is no. `skill`: what
        `save_skill` will write, for the card to show in parts (CONTRACT.md, "The tool save_skill")."""
        approve = self.approvers.get(project_id)
        whole = {"detail_limit": detail_limit} if detail_limit else {}
        if skill is not None:
            whole["skill"] = skill
        return bool(approve and await approve(tool, title, detail, reason, always=True, **whole))

    def turn_library(self, project_id: str, picked: dict | None = None) -> dict:
        """What a turn loads: the project's plugin folder and its MCP servers (TurnRequest fields).
        Without `picked` (nothing was chosen for a request) it is what the person pinned."""
        project = self.store.get(project_id)
        workspace = self.store.workspace(project_id) if project.get("attach") else None
        made = self.lib.materialise(project, self.store.dir(project_id) / "library",
                                    None if picked is None else [item["id"] for item in picked["skills"]])
        servers = self.lib.mcp_configs(project, None if picked is None else [item["id"] for item in picked["connectors"]],
                                       workspace)
        asked = {item["name"] for item in (picked or {}).get("connectors") or [] if item["how"] == "picked"}
        if picked is not None:
            picked["skills"] = [item for item in picked["skills"] if item["id"] in made["loaded"]]
            picked["connectors"] = [item for item in picked["connectors"] if item["name"] in servers]
            picked.update(loaded_skills=len(made["loaded"]), list_chars=made["list_chars"],
                          budget_chars=made["budget_chars"], description_cap=made["description_cap"],
                          left_out=len(made["left_out"]))
            self.picker.begin(project_id, picked.get("turn_id") or "", picked, made["loaded"], made["plugin_dir"],
                              list(servers))
        if not self.settings.fake:
            servers = {tools.SERVER_NAME: tools.build_server(self, project_id), **servers}
        return {"plugin_dir": made["plugin_dir"], "mcp_servers": servers, "ask_first": asked & set(servers)}

    async def prepare_turn(self, project_id: str, turn_id: str, text: str, connection: dict,
                           depth: str = "standard", check_only: bool = False) -> dict:
        """Pick this request's skills and connectors, build the plugin folder, and say what was
        picked (`library_picked`). → {picked, request: the TurnRequest fields}."""
        project = self.store.get(project_id)
        started = time.monotonic()
        token = CURRENT_TURN.set(turn_id)      # the choosing call is a request of this turn: its spend counts
        try:
            picked = await self.picker.pick(project, text, connection, self.store.workspace(project_id),
                                            require=[DEEP_SKILL] if depth == "deep" else [],
                                            check_only=check_only)
        finally:
            CURRENT_TURN.reset(token)
        picked["turn_id"] = turn_id
        request = await asyncio.to_thread(self.turn_library, project_id, picked)
        picked["total_ms"] = int((time.monotonic() - started) * 1000)
        self.store.update(project_id, last_pick={**picked, "at": now_iso()})
        self.log(project_id).append("library_picked", turn_id, **{k: v for k, v in picked.items() if k != "turn_id"})
        return {"picked": picked, "request": request}

    def library(self) -> dict:
        try:
            seen = json.loads((self.settings.data_dir / "last-session.json").read_text("utf-8"))
        except (OSError, ValueError):
            seen = {}
        skills = self.lib.skills()
        own = {skill["name"] for skill in self.lib.catalog.skills()}   # a picked skill is not a built-in one
        for name in seen.get("skills") or []:
            if name.split(":")[-1] not in own:
                skills.append({"id": name, "name": name, "label": name, "description": "", "source": "built-in",
                               "default": True})
        look_reason = self.looker.reason()
        listed = [
            ("files", "Read and write project files", "Creates and edits any file in the project folder. Every turn is saved as a version you can restore.", True, None),
            ("shell", "Run commands and scripts", "Runs shell commands, Python and Node scripts in the project folder. Risky commands ask you first.", True, None),
            ("web_search", "Search the web", "Looks things up with web search.", True, "The studio's own keyless search works on every connection, including gateways; the Claude subscription also has its built-in search."),
            ("web_fetch", "Read web pages and APIs", "Downloads pages, files and API responses and reads them.", True, None),
            ("subagents", "Sub-agents", "Hands parts of a large task to helper agents that work in parallel.", True, None),
            ("look", "Look at the page", "Opens the project page in a real browser, takes a screenshot, reads console errors, and can click and type before looking.", look_reason is None, look_reason),
            ("app", "Give the project a back end", "Writes a server (for example Python or Node) that the studio runs and serves at the project URL, with logs and restart.", True, None),
            ("databases", "Query your databases", "Reads from the data sources you attach to a project. Read-only; the assistant never sees the password.", True, None),
            ("context", "Read your own folders and running apps", "Reads a folder, a file or the address of a running app that you add to a project as context, where it is. Nothing is copied and nothing there is changed.", True, None),
            ("attachments", "Read attached files", "Reads images, PDFs, spreadsheets and text you attach or drop into the project.", True, None),
            ("sources", "Save and quote sources", "Saves every page and document it reads into the project, so each figure can be traced to the words it came from.", True, None),
            ("office", "Make PowerPoint, Word and Excel files", "Builds real files, renders them to check every slide and page, calculates spreadsheet formulas, and shows them inside the page with a download button.", office.reason() is None, office.reason()),
            ("motion", "Make animation, 3D and navigable graphics", "Builds animated pages, 3D scenes you can turn and zoom, and diagrams and maps you can move around in, and looks at the motion frame by frame to check it.", self.looker.reason() is None, self.looker.reason()),
            ("video", "Make video, GIF and image files", "Renders an animated page to an MP4, GIF or WebM frame by frame, with a soundtrack if you give one, and saves any page or part of it as a picture. Shown in the page with a download button.", media.video_reason(self.looker) is None, media.video_reason(self.looker)),
            ("generate", "Generate photos, clips and sound", "Asks a generation service (Higgsfield, on your account) for pictures, short video clips, music or speech. It shows the price in credits and asks you before every one.", generate.reason() is None, generate.reason()),
            ("check", "Check the figures on a page", "Compares every marked figure and quote with its saved source or with the project's data, and lists numbers that have no source.", True, None),
            ("mcp", "Use MCP servers", "Connects to MCP servers you add or switch on for a project.", True, None),
        ]
        return {
            "connections": self.registry.describe_all(),
            "tools": [{"id": i, "label": l, "description": d, "available": a, "note": n} for i, l, d, a, n in listed],
            "skills": skills,
            "agents": self.lib.agents(),
            "studio_tools": tools.describe(self),
            "mcp_servers": self.lib.servers(),
            "index": self.picker.summary(),
            "prefs": self.picker.prefs(),
            "templates": self.templates(),
            "data_kinds": [{"kind": kind, "label": meta["label"], "default_port": meta["default_port"],
                            "available": driver_available(kind),
                            "note": None if driver_available(kind) else "Its driver is not installed in the studio."}
                           for kind, meta in DATA_KINDS.items()],
            "observed_at": seen.get("observed_at"),
        }

    def app(self, project_id: str):
        project = self.store.get(project_id)
        return self.apps.ensure(project, self.store.workspace(project_id), self.store.dir(project_id),
                                {"STUDIO_TOKEN": self.token(project_id), "STUDIO_DATA_URL": self.data_url(project_id),
                                 "STUDIO_MODEL_URL": self.model_url(project_id)})

    async def sync_app(self, project_id: str, restart: bool = False) -> None:
        if project_id in self.deleting:     # its app is being dropped; starting it again would leave it running
            return
        try:
            project, app = self.store.get(project_id), self.app(project_id)
        except ProjectNotFound:
            return
        if project.get("app_stopped"):
            if not restart:         # the person stopped it; a restart of the studio does not start it
                await app.hold()
                return
            self.store.update(project_id, app_stopped=False)    # a turn changed the project: they are working on it
        await app.sync(restart=restart)

    # -- what the agent is told (CONTRACT.md, "What the agent is told once, and what with each message") --
    def system_context(self) -> str:
        """The part of the system prompt that depends on this studio's configuration but on no project or
        request (TurnRequest.context, after library/system-prompt.md). It must stay the same from one
        request to the next, or the cached start of the conversation is written again."""
        lines = ["## On this studio"]
        if self.looker.reason() is None:
            lines.append(
                "- You can see the page: look_at_page loads it in a real browser and returns screenshot files "
                "(open them with the Read tool), what the studio measured on the page (`layout`), console errors "
                "and failed requests; `actions` let you type and click first. One picture of a tall page reaches "
                "you scaled down, so judge text, spacing and anything cut off from the `tiles` (the same page in "
                "readable pieces), not from `image`. Look at every page you create or change before you finish, "
                "in the state the person will see it, at width 1440 (a desktop) first, then 820 (a tablet) and "
                "390 (a phone) when its layout changes with the width, and fix what is broken or looks wrong. "
                "Say plainly if you did not look.")
        else:
            lines.append("- You cannot see the rendered page in this studio; say so instead of guessing.")
        if office.reason() is None:
            lines.append(
                "- You can make real PowerPoint, Word, Excel and PDF files (the `office-files` skill says how, with "
                "the `pptx`, `docx`, `xlsx` and `pdf` skills for each kind). After building one, call render_document "
                "on it: you get page images to look "
                "at, a workbook's formulas are calculated and checked, and a preview page is written that the "
                "report can frame. Never hand over an Office file you have not rendered and looked at.")
        else:
            lines.append(f"- Office files cannot be rendered here ({office.reason()}) Say so if asked for one.")
        if media.video_reason(self.looker) is None:
            lines.append(
                "- You can make motion: animated pages, 3D scenes and navigable graphics (the `page-animation`, "
                "`three-d-scene` and `interactive-graphic` skills), and real video, GIF and picture files made from a "
                "page (the `motion-video` and `image-export` skills, the render_video and render_image tools). "
                "render_video with `check: true` returns a sheet of frames: that is how you see motion, so use it "
                "before calling any animation finished. Never hand over a video you have not looked at.")
        elif self.looker.reason() is None:
            lines.append(f"- Pages cannot be rendered to video here ({media.video_reason(self.looker)}) Animated "
                         "pages, 3D and pictures of a page (render_image) still work. Say so if asked for a video file.")
        if generate.reason() is None and not self.settings.fake:
            lines.append(
                "- Generated media: generate_media asks a generation service for a photograph, a short clip, music "
                "or speech (the `generated-media` skill). Each one spends the person's credits and they are asked "
                "before every one, so use it only when they asked for generated imagery or the piece plainly needs "
                "a photograph you cannot draw; ask for one at a time. A generated picture is an illustration: never "
                "present it as a photo of the person's real product, place, team or customers.")
        if model_gateway(self.settings):
            lines.append(
                "- A back end you write for this project can call language models through the studio. It speaks "
                "the OpenAI chat API: base URL `$STUDIO_MODEL_URL`, API key `$STUDIO_TOKEN` (both are environment "
                "variables of the app; call from the server, never from the page). `GET $STUDIO_MODEL_URL/models` "
                "lists the models; each has `paid` true or false, and paid ones cost the person money per call, so "
                "label them and default to one that is not paid. No streaming (`stream` must be false or absent). "
                f"A project may spend ${self.settings.app_model_daily_usd:.2f} a day; past that the answer is 429 "
                "with a message to show the person. The app gets no other model key: never look for one in the "
                "person's shell files or ask them to put one in a file.")
        missing = tools.missing_here(self)
        if missing:
            lines.append("- Studio tools this studio does not have: " + "; ".join(
                f"{', '.join(names)} ({why.rstrip('.')})" for why, names in missing.items())
                + ". When a skill asks for one, skip that step and say plainly what you could not do.")
        return "\n".join(lines)

    FACTS_HEAD = ("Facts about this project, current as of this message (they replace anything said about "
                  "them earlier in the conversation):")
    FACTS_SAME = ("- The standing facts about this project are unchanged since the last full <studio-project> "
                  "block in this conversation, which still holds. What follows is about this request.")

    def project_facts(self, project_id: str, picked: dict | None = None) -> tuple[str, str]:
        """→ (standing, current): what the agent needs to know about this project. `standing` changes
        only when the project does (it is sent again only then, turns.TurnRunner); `current` is about
        this request (what was picked, the app's state) and goes with every message."""
        project = self.store.get(project_id)
        app = self.app(project_id)
        api = f"http://127.0.0.1:{self.settings.port}/api/projects/{project_id}/app"
        lines = [
            f"- The person sees this project at {project['preview_url']} (the live preview); a back end of "
            "the project is served there too.",
            f"- To test the app during your turn: `curl -s -X POST {api}/restart`, then `curl -s {api}` until "
            f"\"state\" is \"running\" (or \"crashed\"), then request your own pages at http://127.0.0.1:{app.port}/. "
            f"The app's output (stdout and stderr) is in {app.log_path}.",
        ]
        if project.get("from_example"):
            without = Sources(self.store.workspace(project_id)).without_text()
            if without:
                shipped = self.examples.get(project["from_example"]) or {}
                lines.append(
                    f"- This project is a copy of the example “{shipped.get('title') or project['from_example']}” "
                    f"that comes with the studio. The saved text of its sources was not included ({len(without)} "
                    "of them: it is other people's writing), only the list in sources/index.json with each "
                    "address. check_page cannot confirm a quote from a source without its text: before checking, "
                    "call fetch_source with the address of each source the page cites; the text comes back under "
                    "the same id, so the page's marks stay right. Say so plainly if a source can no longer be "
                    "fetched or no longer says what the page quotes.")
        using = project.get("uses_template")
        if using and (self.store.workspace(project_id) / "template").is_dir():
            lines.append(
                f"- This project uses the person's template “{using['label']}” ({using['kind']}). Its files are in "
                "`template/`; read `template/TEMPLATE.md` first and follow the `use-template` skill. Make what the "
                "person asks look like the template and build it the same way. Do not reuse the template's facts, "
                "figures, names, dates or sources, and do not edit anything under `template/`.")
        sources = self.attached_sources(project_id)
        if sources:
            names = ", ".join(f"“{s['name']}” ({DATA_KINDS[s['kind']]['label']}, database {s['database']})" for s in sources)
            lines.append(
                f"- The person attached these databases to this project: {names}. They are read-only and you "
                "never get the password. Use database_schema first, then query_database (aggregate in SQL "
                "instead of downloading whole tables; `save_as` writes a result file into the project), and "
                "say which tables a figure came from. A back end you write for this project reaches the same "
                "databases over HTTP: POST JSON `{\"sql\": …, \"limit\": …}` to `$STUDIO_DATA_URL/<name>/query` "
                "with the header `X-Studio-Token: $STUDIO_TOKEN` (both are environment variables of the app; "
                "send the token from the server, never from the page).")
        told = contexts.facts(project.get("context") or [])
        if told:
            lines.append(told)
        # The person's own words: for every project first (on a shared studio, its owner's), then this one's.
        for standing in (self.everywhere.fact(),
                         instructions.fact(project.get("instructions"), bool(project.get("instructions_cleared")))):
            if standing:
                lines.append(standing)
        if self.looker.reason() is None:
            lines.append(self._signed_in_context(project))
        current = [self._library_context(project, picked)]
        attached = project.get("attach")
        if attached:
            # With every message, not once per conversation: these are the lines that keep the person's
            # own app and folder safe (edit in place, never touch the original, leave its git alone).
            # A long request can be compacted, and a summary must never be all that is left of them.
            current.extend(self._attached_context(project, attached, app))
        searching = services.search_connectors([item.get("name") for item in (picked or {}).get("connectors") or []])
        if searching:       # a search connector attached to this request is the first way to search
            current.append(f"- For web search, use the tools of the connector {' or '.join(f'“{name}”' for name in searching)} "
                           "first: the person attached it for this. `search_web` is the fallback when it cannot answer.")
        if app.address:
            current.append(f"- Current app: a server the studio did not start, at {app.address} (state {app.state}). "
                           "The studio never starts, restarts or stops it; it reloads by itself when files change.")
        elif app.run:
            current.append(f"- Current app: `{app.run}` — state {app.state}"
                           + (f", exit code {app.exit_code}" if app.state == "crashed" else "")
                           + ((" (running for minutes and not answering on its port: read its output and check "
                               "that it listens on $PORT)") if app.not_answering else
                              " (alive, not answering on its port after 90 s)" if app.slow else "") + ".")
        else:
            current.append("- This project has no back end yet (no studio.json); it is served as static files.")
        return "\n".join(lines), "\n".join(current)

    def agent_context(self, project_id: str, picked: dict | None = None, standing: bool = True) -> str:
        """The project facts for one message (turns.compose_prompt): in full, or (standing=False) with
        the standing part replaced by one line saying it is unchanged."""
        kept, current = self.project_facts(project_id, picked)
        return "\n".join([self.FACTS_HEAD, kept if standing else self.FACTS_SAME, current])

    def _signed_in_context(self, project: dict) -> str:
        """The one sentence about seeing the app signed in (CONTRACT.md, "Seeing an app signed in as the person")."""
        if self.sessions.allowed(project):
            said = ("your looks carry the person's sign-in for this app when their browser has one (the "
                    "result's `signed_in` says whether it did); if the page still asks to sign in, ask them to "
                    "open or reload it in the studio signed in")
        else:
            said = (f"your looks do not carry the person's sign-in for this app (the option “{SIGNED_IN_SETTING}” "
                    "in the project's menu is off" + (", and this studio is shared" if self.settings.shared else "")
                    + "); if the page asks to sign in, say so")
        return (f"- Signing in: {said}. Never guess or make up credentials to get past a sign-in. A sign-in "
                "you do with `actions` (with credentials the person gave you) lasts across your looks until "
                "the studio stops.")

    def _library_context(self, project: dict, picked: dict | None) -> str:
        """What this request was given from the library (how the library works is in system-prompt.md)."""
        index = self.picker.summary()
        if picked is None:
            skills = []
            servers = sorted(self.lib.mcp_configs(project))
        else:
            skills = [item["name"] for item in picked["skills"]]
            servers = [item["name"] for item in picked["connectors"]]
        return (
            f"- This request: the library holds {index['skills']} skills and {index['connectors']} connectors; "
            "loaded are the studio's own skills and these picked ones: " + (", ".join(skills) if skills else "none")
            + ". Connectors: " + (", ".join(servers) + " (tools named `mcp__<name>__…`; use them when "
                                  "they fit the task)" if servers else "none") + ".")

    def _attached_context(self, project: dict, attached: dict, app) -> list[str]:
        where = attached.get("path")
        api = f"http://127.0.0.1:{self.settings.port}/api/projects/{project['id']}/app/config"
        lines = []
        if attached.get("mode") == "in_place":
            lines.append(
                f"- This project is an existing app edited IN PLACE: the project folder is the person's own folder "
                f"{where}, their real working copy. Change only files inside it, and nothing outside it. Do not run "
                "git there at all (no commit, checkout, stash, reset, branch or push): the app's repository belongs "
                "to the person; the studio keeps its own versions of your changes elsewhere. Do not write "
                "studio.json into this folder.")
            lines.append(
                "- How the studio shows this app is set in the project, not in a file: to set or change it, "
                f"`curl -s -X PUT {api} -H 'content-type: application/json' -d "
                "'{\"run\": \"exec npx vite --port $PORT --host 127.0.0.1 --strictPort\", \"restart\": \"never\"}'` "
                "(a command the studio starts on $PORT), or `-d '{\"address\": \"http://localhost:5173\"}'` (a dev "
                "server the person already runs).")
        else:
            origin = f"a git clone of {where}" if attached.get("kind") == "git" else f"a copy of the folder {where}"
            lines.append(
                f"- This project is an existing app brought in as a safe copy: {origin}"
                + (f", started from commit {str(attached.get('started_from'))[:7]} on branch "
                   f"{attached.get('branch') or '(detached)'}" if attached.get("started_from") else "")
                + ". The original folder is the person's real app: never read from it for writing, never write to it, "
                "never run commands in it. Change only files inside this project folder, and nothing outside it.")
            if attached.get("studio_branch"):
                lines.append(
                    f"- The studio commits your work on the branch {attached['studio_branch']}. Do not run git commands "
                    "that switch branches, rewrite history, reset, stash or push; the studio's versions depend on them.")
            lines.append("- studio.json in this copy says how the studio runs the app; it is never part of the "
                         "app's own history.")
        lines.extend(self._ship_context(project, attached))
        if app.run is None and not app.address and (attached.get("framework") in (None, "")):
            lines.append(
                "- The studio could not tell how this app starts. If the person asks for anything that needs the app "
                "running, first set it up: read its README and package files, install what it needs inside the "
                "project folder, then declare how it starts (as above), and tell the person what you did.")
        if attached.get("uncommitted") and not attached.get("included_uncommitted") and attached.get("mode") != "in_place":
            lines.append(f"- The original had {len(attached['uncommitted'])} uncommitted change(s) that were NOT "
                         "copied in; say so if the person expects work that is not here.")
        return lines

    @staticmethod
    def _ship_context(project: dict, attached: dict) -> list[str]:
        state = project.get("ship") or {}
        recipe = state.get("recipe")
        lines = [
            "- Making this app live (\"ship\", \"deploy\", \"make it live\", \"publish\") is done by the studio, never by "
            "you: do not run deploy, restart, push or copy commands against the original yourself. The studio follows "
            "a recipe kept in the project: checks run in this project folder, how the change reaches the original "
            f"({', '.join(shipping.methods_for(attached))} for this project), commands run in the original "
            "afterwards, an address to check, paths that need the owner, and how to go back."]
        if recipe:
            lines.append("- The person confirmed this recipe:\n" + "\n".join(
                "  " + line for line in shipping.describe_recipe(recipe, attached).splitlines()))
            lines.append("- When the person asks to make it live, call make_live (after finishing and checking your "
                         "edits); the studio starts when your turn ends and shows them a card to allow. Say that. "
                         "To change the recipe, call propose_ship_recipe with the whole new recipe: they confirm it again.")
        else:
            lines.append(
                "- There is no recipe yet. When the person asks to make it live (or to set up shipping), read the app "
                "first (package.json scripts, README, deploy files such as Dockerfile, Procfile, vercel.json, "
                "ecosystem.config.js, Makefile, CI workflows), then call propose_ship_recipe: checks that prove the "
                "copy works (tests, lint, a type check, a build that does not disturb the running dev server: "
                "`next build` writes the same .next folder as `next dev`), the after-commands that put the change "
                "into service where the original runs, the address to check, and protected path patterns (env "
                "files, migrations, payment or auth code). The person confirms it on a card; then call make_live.")
        if state.get("last"):
            last = state["last"]
            lines.append(f"- Last ship: {last.get('result')} at {last.get('at')} ({last.get('method')}).")
        return lines

    # -- attaching an app by its folder -------------------------------------------------------
    def _protected(self) -> list[Path]:
        return [self.settings.data_dir, REPO_ROOT]

    def reach(self, project_id: str):
        """What this project's agent may not reach (CONTRACT.md, "What an agent's shell may not
        reach"): read again for every request, so a studio started since is known."""
        from . import risk
        import os
        more = [p for p in (os.environ.get("STUDIO2_AGENT_PRIVATE") or "").split(":") if p.strip()]
        return risk.reach_for(self.store.workspace(project_id), self.store.dir(project_id),
                              self.store.get(project_id).get("port"), known_studios(self.settings),
                              more_private=more)

    # -- context from this computer (CONTRACT.md, "Context from this computer") --------------
    async def inspect_context(self, value: str) -> dict:
        return await contexts.inspect(value, self._protected(), self.settings.home, bool(self.settings.shared))

    async def add_context(self, project_id: str, value: str, label: str | None = None,
                          note: str | None = None) -> dict:
        self.store.get(project_id)                      # an unknown project is a 404 before anything is read
        found = await self.inspect_context(value)
        if found["refused"]:
            raise contexts.ContextError("context_refused", found["refused"])
        if contexts.inside(found["path"], self.store.workspace(project_id)):
            raise contexts.ContextError("context_refused", "This is already part of the project.")
        # Read, check and write with no await in between: two adds at once cannot both pass the checks.
        existing = self.store.get(project_id).get("context") or []
        item = contexts.new_item(found, label, note, existing)
        self.store.update(project_id, context=[*existing, item])
        return item

    def update_context(self, project_id: str, item_id: str, changes: dict) -> dict:
        items = self.store.get(project_id).get("context") or []
        current = next((item for item in items if item["id"] == item_id), None)
        if current is None:
            raise contexts.ContextError("unknown_context", "This project has no such context item.", 404)
        changed = dict(current)
        if changes.get("label") is not None:
            changed["label"] = contexts.clean_label(changes["label"])
        if changes.get("note") is not None:
            changed["note"] = contexts.clean_note(changes["note"])
        self.store.update(project_id, context=[changed if item["id"] == item_id else item for item in items])
        return changed

    def remove_context(self, project_id: str, item_id: str) -> None:
        items = self.store.get(project_id).get("context") or []
        if not any(item["id"] == item_id for item in items):
            raise contexts.ContextError("unknown_context", "This project has no such context item.", 404)
        self.store.update(project_id, context=[item for item in items if item["id"] != item_id])

    async def inspect_folder(self, path: str) -> dict:
        return await asyncio.to_thread(attaching.inspect, path, self._protected(), self.settings.home,
                                       self.store.in_place_folders())

    async def attach_project(self, path: str, mode: str = "copy", name: str | None = None,
                             run: str | None = None, address: str | None = None,
                             env_files: list[str] | None = None, include_uncommitted: bool = False) -> dict:
        found = await self.inspect_folder(path)
        if not found["exists"]:
            raise attaching.AttachError("folder_not_found", found["refused"]["copy"] or "There is no folder at this path.")
        reason = found["refused"]["copy" if mode == "copy" else "in_place"]
        if reason:
            raise attaching.AttachError("folder_refused", reason)
        if address and mode != "in_place":
            raise attaching.AttachError("address_refused", "An address is shown only for a folder edited in place; "
                                        "a safe copy runs its own server from the copy.")
        address = attaching.check_address(address) if address else None
        command = found["run"] if run is None else (run.strip() or None)
        folder = Path(found["path"])
        pid = self.store.new_id()
        label = name or f"{found['name']} ({'copy' if mode == 'copy' else 'in place'})"
        directory = self.store.dir(pid)
        directory.mkdir(parents=True)
        try:
            if mode == "copy":
                slug = re.sub(r"[^a-z0-9]+", "-", found["name"].lower()).strip("-")[:40] or "app"
                facts = await asyncio.to_thread(
                    attaching.make_copy, folder, directory / "workspace", f"studio/{slug}-{pid[2:]}",
                    {"run": command, "restart": "never"} if command else None, list(env_files or []),
                    found["env_files"], bool(include_uncommitted and found["git"]))
                app_config = None
            else:
                facts = await asyncio.to_thread(attaching.start_history, folder, directory / "history.git")
                app_config = {"address": address} if address else (
                    {"run": command, "restart": "never"} if command else {})
        except Exception:
            ws.register_history(folder, None) if mode != "copy" else None
            shutil.rmtree(directory, ignore_errors=True)   # our own folder only, never the person's
            raise
        record = {"mode": "copy" if mode == "copy" else "in_place", "path": str(folder),
                  "framework": (found["framework"] or {}).get("id"), **facts,
                  "included_uncommitted": bool(include_uncommitted and found["git"] and mode == "copy"),
                  # A typed start command can be the evidence that a Python part is needed (CONTRACT.md).
                  "install": (found["install"] if run is None or command == found["run"] else
                              attaching.install_for(folder, command)) if mode == "copy" and command else None,
                  "address": address, "attached_at": now_iso()}
        project = self.store.create(label, self.registry.connections()[0]["id"], "attach", project_id=pid,
                                    make_workspace=False, attach=record, app_config=app_config,
                                    head=facts["import_commit"])
        project = await self.open_preview(project)
        self.spawn(self.sync_app(pid))   # a first start may install packages for minutes
        return project

    async def set_app_config(self, project_id: str, config: dict) -> dict:
        project = self.store.get(project_id)
        if (project.get("attach") or {}).get("mode") != "in_place":
            raise attaching.AttachError("not_in_place", "Only a folder edited in place keeps its app in the project; "
                                        "other projects use studio.json.", 409)
        clean: dict = {}
        if config.get("address"):
            clean["address"] = attaching.check_address(str(config["address"]))
        elif isinstance(config.get("run"), str) and config["run"].strip():
            clean["run"] = config["run"].strip()
            if config.get("restart") == "never":
                clean["restart"] = "never"
        project = self.store.update(project_id, app_config=clean)
        await self.sync_app(project_id)
        return project

    def since(self, project_id: str) -> str | None:
        """An attached app's import commit: its versions start there."""
        return (self.store.get(project_id).get("attach") or {}).get("import_commit")

    # -- lifecycle --------------------------------------------------------------------------
    async def start(self) -> None:
        await self.checks.sweep()       # scratch projects of a model check that was cut short
        self.registry.sweep()           # connector settings files of turns a killed studio left behind
        for project in self.store.list():
            try:
                await self._close_orphaned_turns(project)
                await self.ships.recover(project["id"])
                self.costs.reconcile(project["id"])     # a read-back a restart cut short
                project = self._into_range(project)
                project = await self.open_preview(project, patient=True)
                self.spawn(self.sync_app(project["id"]))
            except Exception:
                log.exception("Could not open project %s", project.get("id"))

    async def stop(self) -> None:
        await self.checks.stop_all()
        await self.ships.stop_all()
        await self.turns.stop_all()
        await self.costs.stop()
        await self.watchers.stop_all()
        await self.apps.stop_all()
        await self.previews.stop_all()
        self.sessions.clear()
        await self.looker.close()
        await self.registry.stop()      # the studio's own LiteLLM, when one was started

    def _into_range(self, project: dict) -> dict:
        """A studio that moved to another port: a page port outside the current range gets a new
        one from the range (CONTRACT.md, "Ports and processes, cleanly"). Inside, it is kept."""
        low, high = self.settings.port_range
        old = project.get("port")
        if isinstance(old, int) and low <= old <= high:
            return project
        port = self.store.allocate_port()
        log.info("Project %s: page port %s is outside %s–%s; it now uses %s.", project["id"], old, low, high, port)
        return self.store.update(project["id"], port=port, preview_url=f"http://127.0.0.1:{port}/")

    async def _close_orphaned_turns(self, project: dict) -> None:
        """A turn that was running when the server died gets its one `turn_finished` now."""
        pid = project["id"]
        orphans = self.log(pid).unfinished_turns()
        queued, approvals = self.log(pid).unresolved()
        for turn_id, ids in approvals.items():
            for approval_id in ids:
                self.log(pid).append("approval_resolved", turn_id, approval_id=approval_id,
                                     decision="deny", by="turn_end")
        for turn_id, ids in self.log(pid).unanswered().items():
            for question_id in ids:
                self.log(pid).append("question_resolved", turn_id, question_id=question_id,
                                     by="turn_end", skipped=True, answers=[])
        for item in queued:
            self.log(pid).append("turn_unqueued", None, queued_id=item["queued_id"], reason="restart",
                                 text=item.get("text") or "")
        for index, turn_id in enumerate(orphans):
            if index == len(orphans) - 1:  # keep whatever the interrupted agent left on disk
                await self.checkpoint(pid, "agent", "Changes from an interrupted turn", turn_id)
            self.log(pid).append("turn_finished", turn_id, status="failed", error=RESTART_ERROR,
                                 usage=empty_usage(), session_id=project.get("session_id"))
        if orphans or project.get("running_turn"):
            self.store.update(pid, running_turn=None)

    async def open_preview(self, project: dict, patient: bool = False) -> dict:
        """Make sure the project's URL is being served. A project keeps its port: when it is
        busy at start-up (usually the previous studio still closing) wait up to 10 s before
        giving the project a new one."""
        pid = project["id"]
        if self.previews.port(pid) == project["port"]:
            return project
        await self.previews.stop(pid)
        target = lambda: self.apps.proxy_target(pid)  # noqa: E731
        for attempt in range(40 if patient else 1):
            try:
                await self.previews.start(pid, self.store.workspace(pid), project["port"], target)
                return project
            except OSError:
                if patient and attempt < 39:
                    await asyncio.sleep(0.25)
        port = self.store.allocate_port()
        project = self.store.update(pid, port=port, preview_url=f"http://127.0.0.1:{port}/")
        await self.previews.start(pid, self.store.workspace(pid), port, target)
        return project

    # -- projects ---------------------------------------------------------------------------
    async def create_project(self, name: str, template: str = "blank", example: str | None = None,
                             owner: bool = False) -> dict:
        if example:    # a copy of an example that comes with the studio: its files as they are
            shipped = self.examples.get(example)
            if shipped is None:
                raise KeyError(example)
            project = self.store.create(name, self.registry.first(owner)["id"], "blank", from_example=example)
            pid = project["id"]
            first = await asyncio.to_thread(ws.init_workspace, self.store.workspace(pid), name,
                                            self.examples.files(example), f"From the example “{shipped['title']}”",
                                            True)
            self.store.update(pid, head=first["sha"])
            return await self.open_preview(self.store.get(pid))
        known = {item["id"]: item for item in self.templates()}
        if template != "blank" and template not in known:
            raise KeyError(template)
        project = self.store.create(name, self.registry.first(owner)["id"], template)
        pid = project["id"]
        chosen = known.get(template) or {}
        pattern = chosen.get("source") == "yours" and chosen.get("mode") == "pattern"
        files = (self.saved.files(template) if chosen.get("source") == "yours"
                 else self.settings.library_dir / "templates" / template / "files")
        label = f"New {chosen['label'].lower()}" if template in known else "Blank page"
        # A copied template becomes the project's own files. A pattern leaves the page empty and
        # is put beside it for the agent to follow: its content must not show up as the new page.
        first = await asyncio.to_thread(ws.init_workspace, self.store.workspace(pid), name,
                                        files if files.is_dir() and not pattern else None, label, False,
                                        template_private if chosen.get("source") == "yours" else None)
        self.store.update(pid, head=first["sha"])
        if pattern:
            await self.use_template(pid, template)
        elif chosen.get("source") == "yours":
            self.store.update(pid, uses_template=None)
        project = await self.open_preview(self.store.get(pid))
        await self.sync_app(pid)
        return project

    async def delete_project(self, project_id: str) -> None:
        self.store.get(project_id)
        if project_id in self.ships.running:
            raise shipping.ShipRunning(project_id)
        # Claimed before the first await: a queued message, a ship or an app start that would
        # begin while the project is being taken apart would work in a folder that is going away.
        self.deleting.add(project_id)
        try:
            for item in list(self.turns.queue.get(project_id, [])):
                self.turns.unqueue(project_id, item["queued_id"])
            if project_id in self.turns.active:
                self.turns.stop(project_id, self.turns.active[project_id])
                await self.turns.wait(project_id)
            await self.watchers.stop(project_id)
            await self.apps.drop(project_id)
            await self.previews.stop(project_id)
            self.sessions.forget(project_id)
            self.hub.drop(project_id)
            self._locks.pop(project_id, None)
            self.store.delete(project_id)
        finally:
            self.deleting.discard(project_id)
            self.turns.queue.pop(project_id, None)

    # -- checkpoints ------------------------------------------------------------------------
    def record_checkpoint(self, project_id: str, checkpoint: dict, turn_id: str | None = None) -> None:
        try:
            self.store.update(project_id, head=checkpoint["sha"])
        except ProjectNotFound:
            return
        self.log(project_id).append("checkpoint", turn_id, **checkpoint)

    async def checkpoint(self, project_id: str, kind: str, message: str,
                         turn_id: str | None = None) -> dict | None:
        async with self.lock(project_id):
            made = await asyncio.to_thread(ws.checkpoint, self.store.workspace(project_id), kind, message)
        if made:
            self.record_checkpoint(project_id, made, turn_id)
        return made

    async def restore(self, project_id: str, sha: str) -> dict:
        workspace = self.store.workspace(project_id)
        async with self.lock(project_id):
            before = await asyncio.to_thread(ws.head, workspace)
            made = await asyncio.to_thread(ws.restore, workspace, sha)
            parent = await asyncio.to_thread(ws.git, workspace, "rev-parse", "HEAD~1")
        if parent.strip() != before:  # restore() saved uncommitted work first; announce that too
            saved = await asyncio.to_thread(ws.describe, workspace, parent.strip())
            self.record_checkpoint(project_id, saved)
        self.record_checkpoint(project_id, made)
        return made
