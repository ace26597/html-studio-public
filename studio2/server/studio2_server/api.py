"""REST API, SSE endpoint and web UI serving (CONTRACT.md, "REST")."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from uvicorn.server import ServerState

from . import __version__
from . import drawing, effort, generate, instructions, voice
from . import models_api, setup_api
from .attach import AttachError
from . import context as contexts
from . import workspace as ws
from .access import AccessGuard
from .config import Settings, model_gateway
from .datasources import DataError, run_query, run_schema, run_test
from .library import LibraryError, test_server
from .look import LookUnavailable
from .preview import bind, listen
from .store import NoFreePort, ProjectNotFound
from .studio import Studio
from . import export as exporting
from . import ownskills
from . import places, services
from .templates import TemplateError
from .turns import TurnRunning
from .ship import ShipError, ShipRunning

log = logging.getLogger("studio2")
TEXT_LIMIT = 1_000_000
UPLOAD_LIMIT = 25 * 1024 * 1024
# What the guide's folder may hand out: pages and what they show. Never the notes kept beside them.
SITE_FILES = (".html", ".css", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp")
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Studio 2</title>
<body style="font:15px/1.6 system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>Studio 2 server is running</h1>
<p>The web UI has not been built yet. Build it with
<code>cd studio2/web &amp;&amp; npm install &amp;&amp; npm run build</code>, or run the dev server with
<code>npm run dev</code> and open <a href="http://127.0.0.1:5200/">http://127.0.0.1:5200/</a>.</p>
<p>The API is at <a href="/api/health">/api/health</a>.</p></body>"""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body = {"code": code, "message": message, **({"details": details} if details else {})}
    return JSONResponse({"error": body}, status_code=status)


class ProjectCreate(BaseModel):
    name: str | None = None
    template: str | None = None
    example: str | None = None


class InspectBody(BaseModel):
    path: str = ""


class AttachBody(BaseModel):
    path: str = Field(min_length=1)
    mode: str = Field(default="copy", pattern="^(copy|in_place)$")
    name: str | None = None
    run: str | None = None
    address: str | None = None
    env_files: list[str] = Field(default_factory=list, max_length=50)
    include_uncommitted: bool = False


class ContextValue(BaseModel):
    value: str = Field(default="", max_length=4096)
    label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class ContextPatch(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class AppConfigBody(BaseModel):
    run: str | None = None
    restart: str | None = None
    address: str | None = None


class SourceBody(BaseModel):
    name: str | None = None
    kind: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    ssl: str | None = None
    ca_file: str | None = None


class LibraryChoice(BaseModel):
    """Per id: true = always in this project, false = never, null = leave it to the studio."""
    skills: dict[str, bool | None] | None = None
    mcp_servers: dict[str, bool | None] | None = None


class LibraryPrefs(BaseModel):
    """Per id, for the whole machine: "always", "never" or null (no preference)."""
    skills: dict[str, str | None] | None = None
    mcp_servers: dict[str, str | None] | None = None


class QueryBody(BaseModel):
    sql: str
    limit: int | None = None
    save_as: str | None = None


class LookBody(BaseModel):
    path: str = "/"
    width: int = 1440
    height: int = 900
    dark: bool = False
    full_page: bool = True
    wait_ms: int = 1200
    actions: list[dict] = Field(default_factory=list, max_length=30)
    tiles: bool = True
    layout: bool = True
    top: int = 0


class Decision(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")


class ShipDecision(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")
    confirm: str = Field(default="", max_length=300)


class ProjectPatch(BaseModel):
    name: str | None = None
    connection: str | None = None
    model: str | None = None
    approvals: str | None = Field(default=None, pattern="^(ask|allow)$")
    name_suggestion: None = None    # only `null` is accepted: the person dismissed the offered name
    turn_options: dict | None = None   # the remembered {depth, verify, ask} (effort.py)
    signed_in_looks: bool | None = None   # looks carry the person's sign-in (session.py)
    instructions: Any = None   # what the person always wants in this project (instructions.py)


class FilePut(BaseModel):
    path: str
    content: str
    base_hash: str | None = None


class DomEdit(BaseModel):
    path: str
    tag: str | None = None
    before_text: str
    after_text: str


class Restore(BaseModel):
    sha: str


class Selection(BaseModel):
    path: str = "/"
    selector: str = ""
    tag: str = ""
    text: str = ""
    html: str = ""
    source: str = ""     # where the framework says the element comes from (bridge.js sourceHint)


class TurnCreate(BaseModel):
    text: str = Field(min_length=1)
    selection: Selection | None = None
    connection: str | None = None
    model: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=20)
    queue: bool = False
    options: dict | None = None    # {depth?, verify?, check_only?} (effort.py); checked in the route


NO_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Studio · page not shown here</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; min-height: 100dvh; display: grid; place-items: center; font: 16px/1.5 system-ui, sans-serif;
       background: Canvas; color: CanvasText; }
main { width: min(30rem, calc(100vw - 2.5rem)); display: grid; gap: .8rem; }
h1 { font-size: 1.3rem; margin: 0; } p { margin: 0; }
</style></head><body><main>
<h1>This project's page cannot be shown at this address</h1>
<p>You reached the studio through a single address (a tunnel or proxy). Each project's page lives on a
port of its own, and this address does not carry those ports.</p>
<p>Chat, files and history work here. To see the page, open the studio on the computer it runs on or
on its private network.</p>
</main></body></html>"""


class OriginGuard:
    """Mutating /api requests from a browser must come from the studio's own origin."""

    def __init__(self, app, allowed: set[str]):
        self.app, self.allowed = app, allowed

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["path"].startswith("/api/")
                and scope["method"] not in ("GET", "HEAD", "OPTIONS")):
            origin = dict(scope["headers"]).get(b"origin")
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            own = f"{scope.get('scheme') or 'http'}://{host}"
            if origin is not None and origin.decode("latin-1") not in {*self.allowed, own}:
                response = error(403, "forbidden_origin", "This request did not come from the studio.")
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def is_paid_model(model: str) -> bool:
    """Models that bill per call on this gateway: anything routed to OpenRouter, except its free ones."""
    return model.startswith("openrouter/") and not (model.endswith(":free") or model.endswith("/free"))


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_name(name: str | None) -> str:
    return " ".join((name or "").split())[:120] or "Untitled"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    studio = Studio(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await studio.start()
        front = []
        if settings.tls and settings.hosts:
            # With a certificate the extra addresses speak HTTPS; loopback stays plain HTTP
            # (served by the process's main server, see __main__).
            front = await listen(_app, bind(settings.hosts, settings.port), settings.tls, ServerState())
        try:
            yield
        finally:
            for server in front:
                server.close()
            await studio.stop()

    app = FastAPI(title="Studio 2", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.studio = studio
    app.add_middleware(OriginGuard, allowed=settings.allowed_origins)
    app.add_middleware(AccessGuard, key=settings.access_key, studio_port=settings.port,
                       open_tailnet=settings.open_tailnet, names=studio.names, gate=studio.gate,
                       pages=settings.page_hosts)  # outermost

    handlers = {
        ApiError: lambda exc: error(exc.status, exc.code, exc.message),
        DataError: lambda exc: error(exc.status, exc.code, exc.message),
        LibraryError: lambda exc: error(exc.status, exc.code, exc.message),
        ownskills.SkillError: lambda exc: error(exc.status, exc.code, exc.message, exc.details),
        TemplateError: lambda exc: error(exc.status, exc.code, exc.message),
        AttachError: lambda exc: error(exc.status, exc.code, exc.message),
        contexts.ContextError: lambda exc: error(exc.status, exc.code, exc.message),
        ShipError: lambda exc: error(exc.status, exc.code, exc.message),
        ShipRunning: lambda exc: error(409, "ship_running", "The studio is making this project live; wait until it "
                                       "finishes."),
        ProjectNotFound: lambda exc: error(404, "project_not_found", "That project does not exist."),
        TurnRunning: lambda exc: error(409, "turn_running", "The assistant is still working in this project."),
        ws.InvalidPath: lambda exc: error(400, "invalid_path", "That is not a valid path inside the project."),
        ws.GitError: lambda exc: error(500, "git_failed", f"Version history failed: {exc}"),
        NoFreePort: lambda exc: error(503, "no_free_port", str(exc)),
        RequestValidationError: lambda exc: error(400, "invalid_request", "The request body is not valid."),
        StarletteHTTPException: lambda exc: error(
            exc.status_code, {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error"),
            str(exc.detail)),
    }
    for kind, build in handlers.items():
        app.add_exception_handler(kind, lambda _request, exc, build=build: build(exc))

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception):
        log.exception("Unhandled error")
        return error(500, "internal_error", "The studio server hit an unexpected error.")

    def idle_project(project_id: str) -> dict:
        project = studio.store.get(project_id)
        if project_id in studio.turns.active:
            raise TurnRunning(project_id)
        if project_id in studio.ships.running:
            raise ShipRunning(project_id)
        return project

    async def user_checkpoint(project_id: str, message: str) -> dict | None:
        studio.watch(project_id)
        return await studio.checkpoint(project_id, "user", message)

    setup_api.register(app, settings)   # GET and POST /api/setup (the first-run screen)
    models_api.register(app, studio, settings)   # models, "check this model", the studio's own LiteLLM

    # -- health and projects ----------------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__, "fake": settings.fake,
                "setup": {"needed": setup_api.first_run(settings)}, "shared": settings.shared,
                "site": (settings.site_dir / "index.html").is_file(),
                "transcription": {"available": voice.reason() is None, "reason": voice.reason()},
                "connections": studio.registry.describe_all()}

    @app.get("/api/projects")
    async def list_projects():
        # A model check's scratch project is not one of the person's pages.
        return [studio.view(project) for project in studio.store.list() if not project.get("hidden")]

    @app.get("/api/templates")
    async def list_templates():
        return studio.templates()

    @app.get("/api/examples")
    async def list_examples():
        return studio.examples.list()

    @app.get("/api/examples/{example_id}/preview")
    async def example_preview(example_id: str):
        image = studio.examples.preview(example_id)
        if image is None:
            raise ApiError(404, "no_preview", "This example has no picture.")
        return FileResponse(image, media_type="image/jpeg", headers={"cache-control": "no-cache"})

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectCreate | None = None):
        if body and body.example:
            if body.template:
                raise ApiError(400, "invalid_request", "Give an example or a template, not both.")
            example = studio.examples.get(body.example)
            if example is None:
                raise ApiError(400, "unknown_example", "There is no such example.")
            name = _clean_name(body.name) if (body.name or "").strip() else example["title"]
            return studio.view(await studio.create_project(name, example=example["id"]))
        try:
            project = await studio.create_project(_clean_name(body.name if body else None),
                                                  (body.template if body else None) or "blank")
        except KeyError:
            raise ApiError(400, "unknown_template", "There is no such template.") from None
        return studio.view(project)

    @app.post("/api/apps/inspect")
    async def inspect_folder(body: InspectBody):
        return await studio.inspect_folder(body.path)

    @app.post("/api/context/inspect")
    async def inspect_context(body: ContextValue):
        return await studio.inspect_context(body.value)

    @app.get("/api/projects/{project_id}/context")
    async def project_context(project_id: str):
        return await contexts.with_state(studio.store.get(project_id).get("context") or [])

    @app.post("/api/projects/{project_id}/context", status_code=201)
    async def add_context(project_id: str, body: ContextValue):
        return await studio.add_context(project_id, body.value, body.label, body.note)

    @app.patch("/api/projects/{project_id}/context/{item_id}")
    async def update_context(project_id: str, item_id: str, body: ContextPatch):
        return studio.update_context(project_id, item_id, body.model_dump(exclude_unset=True))

    @app.delete("/api/projects/{project_id}/context/{item_id}", status_code=204)
    async def remove_context(project_id: str, item_id: str):
        studio.remove_context(project_id, item_id)
        return Response(status_code=204)

    @app.post("/api/projects/attach", status_code=201)
    async def attach_project(body: AttachBody):
        project = await studio.attach_project(
            body.path, body.mode, _clean_name(body.name) if body.name and body.name.strip() else None,
            body.run, body.address, body.env_files, body.include_uncommitted)
        return studio.view(project)

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        project = await studio.open_preview(studio.store.get(project_id))
        studio.app(project_id)
        return studio.view(project)

    def checked_options(raw, request: bool = True) -> dict:
        try:
            return effort.clean(raw, request)
        except effort.OptionsError as exc:
            raise ApiError(400, "invalid_options", str(exc)) from None

    @app.patch("/api/projects/{project_id}")
    async def patch_project(project_id: str, body: ProjectPatch):
        project = studio.store.get(project_id)
        changes: dict = {}
        if "name" in body.model_fields_set:
            changes["name"] = _clean_name(body.name)
        if {"name", "name_suggestion"} & body.model_fields_set and project.get("name_suggestion"):
            changes["name_suggestion"] = None    # accepted, typed over or dismissed: the offer is over
        if body.connection is not None:
            if body.connection not in {c["id"] for c in studio.registry.connections()}:
                raise ApiError(400, "unknown_connection", f"There is no connection named “{body.connection}”.")
            changes["connection"] = body.connection
            if body.connection != project.get("connection"):
                changes["model"] = None  # a model belongs to its connection
        if "model" in body.model_fields_set:
            changes["model"] = body.model
        if body.approvals is not None:
            changes["approvals"] = body.approvals
        if body.turn_options is not None:
            changes["turn_options"] = checked_options(body.turn_options, request=False)
        if "instructions" in body.model_fields_set:
            try:
                changes["instructions"] = instructions.clean(body.instructions)
                # Cleared after having some: the agent is told once per request that none apply now.
                changes["instructions_cleared"] = not changes["instructions"] and bool(
                    project.get("instructions") or project.get("instructions_cleared"))
            except instructions.InstructionsError as exc:
                raise ApiError(400, "invalid_request", str(exc)) from None
        if body.signed_in_looks is not None:
            if body.signed_in_looks and settings.shared:
                raise ApiError(403, "not_for_shared", "On a shared studio the agent never sees an app signed in "
                               "as a person.")
            changes["signed_in_looks"] = body.signed_in_looks
            if not body.signed_in_looks:
                studio.sessions.forget_person(project_id)
        return studio.view(studio.store.update(project_id, **changes) if changes else project)

    # -- the project's own app --------------------------------------------------------------
    def configured_app(project_id: str):
        project_app = studio.app(project_id)
        project_app.refresh_config() if project_app.state == "none" else None
        if project_app.run is None:
            raise ApiError(409, "no_app", project_app.error or "This project has no studio.json with a run command.")
        return project_app

    @app.get("/api/projects/{project_id}/app")
    async def get_app(project_id: str):
        return studio.app(project_id).describe()

    @app.put("/api/projects/{project_id}/app/config")
    async def set_app_config(project_id: str, body: AppConfigBody):
        studio.store.get(project_id)
        await studio.set_app_config(project_id, body.model_dump())
        return studio.app(project_id).describe()

    @app.post("/api/projects/{project_id}/app/{action}")
    async def control_app(project_id: str, action: str):
        if action not in ("start", "stop", "restart"):
            raise ApiError(404, "not_found", "There is no such API route.")
        current = studio.app(project_id)
        current.refresh_config() if current.state == "none" else None
        if current.address:
            raise ApiError(409, "external_app", "The studio did not start this server and does not start, stop "
                           "or restart it.")
        project_app = configured_app(project_id)
        studio.watch(project_id)
        if action == "stop":
            await project_app.stop()
        elif action == "start":
            await project_app.start()
        else:
            await project_app.sync(restart=True)
        if bool(studio.store.get(project_id).get("app_stopped")) != (action == "stop"):
            studio.store.update(project_id, app_stopped=action == "stop")   # kept across a restart of the studio
        return project_app.describe()

    @app.get("/api/projects/{project_id}/app/logs")
    async def app_logs(project_id: str, after: int = 0):
        return studio.app(project_id).logs(max(after, 0))

    @app.post("/api/projects/{project_id}/look")
    async def look_with_actions(project_id: str, body: LookBody):
        return await look(project_id, body.path, body.width, body.height, int(body.dark), int(body.full_page),
                          body.wait_ms, body.actions, int(body.tiles), int(body.layout), body.top)

    @app.get("/api/projects/{project_id}/look")
    async def look(project_id: str, path: str = "/", width: int = 1440, height: int = 900, dark: int = 0,
                   full_page: int = 1, wait_ms: int = 1200, actions: list[dict] | None = None,
                   tiles: int = 1, layout: int = 1, top: int = 0):
        project = await studio.open_preview(studio.store.get(project_id))
        if not path.startswith("/") or "://" in path or path.startswith("//"):
            raise ApiError(400, "invalid_path", "path must start with / and stay on the project's own URL.")
        try:
            return await studio.looker.look(
                # Loopback, not preview_url: that follows the caller's host and would meet the access guard.
                f"http://127.0.0.1:{project['port']}" + path, studio.store.dir(project_id) / "looks",
                width=max(320, min(width, 2560)), height=max(320, min(height, 2000)), dark=bool(dark),
                full_page=bool(full_page), wait_ms=wait_ms, actions=actions if isinstance(actions, list) else None,
                tiles=bool(tiles), layout=bool(layout), top=max(0, top))
        except LookUnavailable as exc:
            raise ApiError(503, "look_unavailable", str(exc)) from None

    @app.post("/api/projects/{project_id}/drawings")
    async def save_drawing(project_id: str, body: dict = Body(...)):
        project = await studio.open_preview(studio.store.get(project_id))
        try:
            drawn = drawing.clean(body)
        except drawing.DrawingError as exc:
            raise ApiError(400, "invalid_drawing", str(exc)) from None
        workspace = studio.store.workspace(project_id)
        folder, stem = workspace / "attachments", f"drawing-{int(time.time() * 1000)}"
        images, note = [], None
        try:
            shots = await studio.looker.drawing_shots(
                f"http://127.0.0.1:{project['port']}" + drawn["path"], drawn, folder, stem)
            images = [{"path": f"attachments/{shot['file'].name}", "marks": shot["marks"]} for shot in shots]
        except LookUnavailable as exc:
            note = str(exc)
        except Exception as exc:  # the drawing is still worth sending without pictures
            note = f"The page could not be photographed ({' '.join(str(exc).split())[:160]})."
        drawn["images"], drawn["note"] = images, note
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.json").write_text(json.dumps(drawn, ensure_ascii=False), "utf-8")
        if project_id not in studio.turns.active:
            await user_checkpoint(project_id, "Drawing on the page")
        return {"path": f"attachments/{stem}.json", "images": [item["path"] for item in images],
                "marks": len(drawn["marks"]), "rendered": bool(images), "note": note}

    # -- templates the person saves ------------------------------------------------------------
    def own_template(template_id: str) -> dict:
        found = studio.saved.get(template_id)
        if found is None:
            if any(item["id"] == template_id for item in studio.templates()):
                raise ApiError(400, "builtin_template", "A built-in template cannot be changed.")
            raise ApiError(404, "unknown_template", "There is no such template.")
        return found

    @app.post("/api/projects/{project_id}/template", status_code=201)
    async def save_template(project_id: str, body: dict = Body(...)):
        idle_project(project_id)
        return await studio.save_template(project_id, str(body.get("name") or ""), str(body.get("description") or ""),
                                          body.get("kind") or None, body.get("file") or None, str(body.get("notes") or ""))

    @app.patch("/api/templates/{template_id}")
    async def edit_template(template_id: str, body: dict = Body(...)):
        own_template(template_id)
        return studio.saved.update(template_id, body.get("label") if isinstance(body.get("label"), str) else None,
                                   body.get("description") if isinstance(body.get("description"), str) else None)

    @app.delete("/api/templates/{template_id}", status_code=204)
    async def delete_template(template_id: str):
        own_template(template_id)
        studio.saved.delete(template_id)
        return Response(status_code=204)

    @app.get("/api/templates/{template_id}/preview")
    async def template_preview(template_id: str):
        image = studio.saved.preview(template_id) if studio.saved.get(template_id) else None
        if image is None:
            raise ApiError(404, "no_preview", "This template has no picture.")
        return FileResponse(image, headers={"cache-control": "no-store"})

    @app.get("/api/templates/{template_id}/export")
    async def export_template(template_id: str):
        found = own_template(template_id)
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in found["label"]).strip() or "template"
        data = await asyncio.to_thread(studio.saved.export, template_id)
        return Response(content=data, media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{name}.studio-template.zip"'})

    @app.post("/api/templates/import", status_code=201)
    async def import_template(file: UploadFile = File(...), confirm: str = Form("")):
        chunks, size = [], 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 100 * 1024 * 1024:
                raise ApiError(413, "file_too_large", "A template file is limited to 100 MB.")
            chunks.append(chunk)
        return await asyncio.to_thread(studio.saved.import_zip, b"".join(chunks), confirm == "1")

    @app.put("/api/projects/{project_id}/uses-template")
    async def use_template(project_id: str, body: dict = Body(...)):
        idle_project(project_id)
        return studio.view(await studio.use_template(project_id, str(body.get("template") or "")))

    @app.delete("/api/projects/{project_id}/uses-template")
    async def stop_using_template(project_id: str):
        idle_project(project_id)
        return studio.view(await studio.use_template(project_id, None))

    # -- generated media --------------------------------------------------------------------
    @app.get("/api/generation")
    async def generation_status():
        generator = studio.generator
        return {"available": generate.reason() is None, "reason": generate.reason(), "paused": generator.halted(),
                "spent_today": generator.spent_today(), "daily_limit": generator.daily_credits}

    @app.delete("/api/generation/pause", status_code=204)
    async def generation_resume():
        studio.generator.resume()
        return Response(status_code=204)

    # -- voice input ------------------------------------------------------------------------
    @app.post("/api/transcribe")
    async def transcribe(file: UploadFile = File(...), language: str = Form("auto")):
        if voice.reason():
            raise ApiError(503, "transcription_unavailable", voice.reason())
        suffix = Path(file.filename or "").suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".audio"
        with tempfile.TemporaryDirectory(prefix="studio2-voice-") as scratch:
            target, size = Path(scratch) / f"recording{suffix}", 0
            with target.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > UPLOAD_LIMIT:
                        raise ApiError(413, "file_too_large", "A recording is limited to 25 MB.")
                    handle.write(chunk)
            try:
                text, seconds = await asyncio.to_thread(voice.transcribe, target, language)
            except voice.VoiceError as exc:
                raise ApiError(422, "transcription_failed", str(exc)) from None
        return {"text": text, "seconds": seconds}

    # -- models for a project's own back end ---------------------------------------------------
    def model_caller(project_id: str, authorization: str | None, x_studio_token: str | None) -> dict:
        token = x_studio_token or (authorization or "").removeprefix("Bearer ").strip()
        if not token or token != studio.token(project_id):
            raise ApiError(401, "bad_token", "Use the project's STUDIO_TOKEN as the API key.")
        gateway = model_gateway(settings)
        if not gateway:
            raise ApiError(503, "no_gateway", "This studio has no model gateway configured for project back ends.")
        return gateway

    @app.get("/api/projects/{project_id}/model/v1/models")
    async def app_models(project_id: str, authorization: str | None = Header(None),
                         x_studio_token: str | None = Header(None)):
        gateway = model_caller(project_id, authorization, x_studio_token)
        try:
            async with studio.gateway_client() as client:
                answer = await client.get(f"{gateway['base_url']}/v1/models",
                                          headers={"Authorization": f"Bearer {gateway['key']}"})
            listed = answer.json().get("data") or []
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError(502, "gateway_unreachable", f"The model gateway did not answer ({exc.__class__.__name__}).") from None
        return {"object": "list", "data": [
            {**item, "paid": is_paid_model(str(item.get("id") or ""))} for item in listed if isinstance(item, dict)]}

    @app.post("/api/projects/{project_id}/model/v1/chat/completions")
    async def app_chat(project_id: str, body: dict = Body(...), authorization: str | None = Header(None),
                       x_studio_token: str | None = Header(None)):
        gateway = model_caller(project_id, authorization, x_studio_token)
        if body.get("stream"):
            raise ApiError(400, "no_streaming", "Streaming is not supported here; call without stream.")
        model = str(body.get("model") or "")
        spent, cap = studio.model_spend_today(project_id), settings.app_model_daily_usd
        if is_paid_model(model) and spent >= cap:
            raise ApiError(429, "daily_limit", f"This project has used its ${cap:.2f} for paid models today "
                                               f"(${spent:.2f} spent). Use a model that is not paid, or raise the limit.")
        try:
            async with studio.gateway_client() as client:
                answer = await client.post(f"{gateway['base_url']}/v1/chat/completions", json=body,
                                           headers={"Authorization": f"Bearer {gateway['key']}"})
        except httpx.HTTPError as exc:
            raise ApiError(502, "gateway_unreachable", f"The model gateway did not answer ({exc.__class__.__name__}).") from None
        cost = None
        try:
            cost = float(answer.headers.get("x-litellm-response-cost") or "")
        except ValueError:
            pass
        usage = None
        if answer.status_code == 200:
            try:
                usage = answer.json().get("usage")
            except ValueError:
                pass
            studio.record_model_use(project_id, model, cost, usage)
        headers = {"x-studio-model-cost": "" if cost is None else f"{cost:.6f}",
                   "x-studio-model-spent-today": f"{studio.model_spend_today(project_id):.6f}"}
        return Response(content=answer.content, status_code=answer.status_code, headers=headers,
                        media_type=answer.headers.get("content-type", "application/json"))

    # -- library and data sources ------------------------------------------------------------
    @app.get("/api/library")
    async def library():
        return studio.library()

    async def json_object(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_request", "Send a JSON object.")
        return body

    @app.post("/api/skills", status_code=201)
    async def add_skill(request: Request):
        if settings.shared:    # a skill reaches the agent in every project: the same rule as import and PUT
            raise ApiError(403, "not_for_shared", "On a shared studio only its owner adds skills to the library.")
        return studio.lib.add_skill(await json_object(request))

    @app.post("/api/skills/import")
    async def import_skill(file: UploadFile = File(...), dry_run: str = "", replace: str = "", allow_scripts: str = "",
                           name: str = ""):
        if settings.shared:
            raise ApiError(403, "not_for_shared", "On a shared studio only its owner adds skills to the library.")
        chunks, size = [], 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > ownskills.MAX_PACKED_BYTES:
                raise ApiError(400, "invalid_skill_file", "The file is larger than 25 MB.")
            chunks.append(chunk)
        on = ("1", "true")
        found = await asyncio.to_thread(ownskills.import_skill, studio.lib, b"".join(chunks), name.strip() or None,
                                        dry_run in on, replace in on, allow_scripts in on)
        return JSONResponse(found, status_code=200 if dry_run in on else 201)

    @app.get("/api/library/skills/export")
    async def export_skill(id: str = ""):
        filename, data = await asyncio.to_thread(ownskills.export, studio.lib, id)
        return Response(content=data, media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{filename}"', "cache-control": "no-store"})

    # -- reading any skill, a copy of one's own, asking the agent about one ----------------------
    @app.get("/api/library/skills/detail")
    async def skill_detail(id: str = ""):
        return await asyncio.to_thread(ownskills.detail, studio.lib, id, studio.picker.usage())

    @app.get("/api/library/skills/file")
    async def skill_file(id: str = "", path: str = "", raw: str = ""):
        found = await asyncio.to_thread(ownskills.one_file, studio.lib, id, path, raw in ("1", "true"))
        if isinstance(found, tuple):       # a picture, as it is; never anything a browser would run
            return Response(content=found[0], media_type=found[1],
                            headers={"cache-control": "no-store", "x-content-type-options": "nosniff"})
        return found

    @app.post("/api/skills/copy", status_code=201)
    async def copy_skill(request: Request):
        body = await json_object(request)
        return await asyncio.to_thread(ownskills.copy, studio.lib, str(body.get("from") or ""), body.get("name"),
                                       body.get("allow_scripts") is True, body.get("replace") is True)

    @app.post("/api/library/skills/ask", status_code=202)
    async def ask_about_skill(request: Request):
        body = await json_object(request)
        title, text = await asyncio.to_thread(ownskills.question, studio.lib, str(body.get("id") or ""),
                                              str(body.get("about") or ""), body.get("text"))
        project = await studio.create_project(_clean_name(title), "blank")
        turn_id = studio.turns.start(project, text, None, None, None, [], options=checked_options(None))
        return {"project_id": project["id"], "turn_id": turn_id}

    @app.get("/api/skills/{name}")
    async def own_skill(name: str):
        return await asyncio.to_thread(ownskills.view, studio.lib, name)

    @app.put("/api/skills/{name}")
    async def edit_skill(name: str, request: Request):
        if settings.shared:
            raise ApiError(403, "not_for_shared", "On a shared studio only its owner changes the library's skills.")
        return await asyncio.to_thread(ownskills.update, studio.lib, name, await json_object(request))

    @app.delete("/api/skills/{name}", status_code=204)
    async def delete_skill(name: str):
        if settings.shared:
            raise ApiError(403, "not_for_shared", "On a shared studio only its owner changes the library's skills.")
        studio.lib.delete_skill(name)
        return Response(status_code=204)

    @app.post("/api/mcp-servers", status_code=201)
    async def add_server(request: Request):
        return studio.lib.add_server(await json_object(request))

    @app.post("/api/mcp-servers/suggest")
    async def suggest_servers(request: Request):
        # A model names connectors from memory; the studio checks what it can and adds nothing.
        from . import connector_suggest
        body = await json_object(request)
        return await connector_suggest.suggest(studio, body.get("text"), body.get("connection") or None)

    @app.patch("/api/mcp-servers/{server_id}")
    async def update_server(server_id: str, request: Request):
        fields = await json_object(request)
        current = studio.lib.server(server_id)
        if current.get("source") == "yours" and current.get("secret_names"):
            # Stored headers and environment values go only where they were saved for: anything on
            # this computer can call this route, so a new address or command that kept them would
            # hand them to whoever is there (CONTRACT.md, "Where a key is kept").
            transport = fields.get("transport") or current["transport"]
            moved = transport != current["transport"] or any(
                fields.get(name) is not None and fields[name] != current.get(name) for name in ("url", "command")) \
                or (fields.get("args") is not None and [str(a) for a in fields["args"] or []] != current.get("args"))
            given = fields.get("headers" if transport == "http" else "env")
            if moved and not (isinstance(given, dict) and all(str(v) != "" for v in given.values())):
                raise ApiError(400, "secret_needed", "This connector now points somewhere else: give its "
                                                     + ("headers" if transport == "http" else "environment values")
                                                     + " again. Saved ones are only sent where they were saved for.")
        return studio.lib.update_server(server_id, fields)

    @app.delete("/api/mcp-servers/{server_id}", status_code=204)
    async def delete_server(server_id: str):
        studio.lib.delete_server(server_id)
        for project_id, chosen in studio.lib.forget_server(studio.store.list(), server_id):
            studio.store.update(project_id, library=chosen)
        return Response(status_code=204)

    @app.post("/api/mcp-servers/{server_id}/test")
    async def try_server(server_id: str):
        studio.lib.server(server_id)
        # Started as a request starts it: a library connector through the launcher (server_config).
        home = studio.lib.connector_home
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = await test_server(studio.lib.server_config(server_id), cwd=home)
        studio.lib.record_test(server_id, result)
        return result

    def project_view(project: dict) -> dict:
        return studio.lib.for_project(project, studio.store.workspace(project["id"]), studio.picker.prefs())

    @app.get("/api/projects/{project_id}/library")
    async def project_library(project_id: str):
        return project_view(studio.store.get(project_id))

    @app.put("/api/projects/{project_id}/library")
    async def choose_library(project_id: str, body: LibraryChoice):
        project = studio.store.get(project_id)
        workspace = studio.store.workspace(project_id) if project.get("attach") else None
        chosen = studio.lib.choose(project, body.model_dump(), workspace)
        return project_view(studio.store.update(project_id, library=chosen))

    @app.get("/api/library/items")
    async def library_items(kind: str = "skills", q: str = "", source: str | None = None, state: str | None = None,
                            offset: int = 0, limit: int = 50, project: str | None = None):
        if kind not in ("skills", "agents", "connectors"):
            raise ApiError(400, "invalid_request", "kind: skills, agents or connectors.")
        chosen = studio.store.get(project) if project else None
        return await asyncio.to_thread(studio.picker.items, kind, q, source or None, state or None, offset, limit, chosen)

    @app.put("/api/library/prefs")
    async def library_prefs(body: LibraryPrefs):
        return studio.picker.set_prefs(body.model_dump())

    @app.post("/api/library/refresh")
    async def library_refresh():
        meta = await asyncio.to_thread(studio.lib.catalog.ensure, True)
        return studio.picker.summary(meta)

    @app.get("/api/data-sources")
    async def list_sources():
        return studio.data.list()

    @app.post("/api/data-sources", status_code=201)
    async def create_source(body: SourceBody):
        return studio.data.create(body.model_dump())

    @app.post("/api/data-sources/test")
    async def test_unsaved_source(body: SourceBody):
        clean = studio.data.normalise(body.model_dump())
        return await asyncio.to_thread(run_test, clean, body.password)

    @app.patch("/api/data-sources/{source_id}")
    async def update_source(source_id: str, body: SourceBody):
        return studio.data.update(source_id, body.model_dump(exclude_unset=True))

    @app.delete("/api/data-sources/{source_id}", status_code=204)
    async def delete_source(source_id: str):
        studio.data.delete(source_id)
        for project in studio.store.list():
            if source_id in (project.get("data_sources") or []):
                studio.store.update(project["id"], data_sources=[i for i in project["data_sources"] if i != source_id])
        return Response(status_code=204)

    @app.post("/api/data-sources/{source_id}/test")
    async def test_source(source_id: str):
        source = studio.data.raw(source_id)
        result = await asyncio.to_thread(run_test, source, studio.data.password(source_id))
        studio.data.record_test(source_id, result)
        return result

    @app.get("/api/projects/{project_id}/data-sources")
    async def project_sources(project_id: str):
        attached = studio.store.get(project_id).get("data_sources") or []
        return [{**item, "attached": item["id"] in attached} for item in studio.data.list()]

    @app.put("/api/projects/{project_id}/data-sources/{source_id}", status_code=204)
    async def attach_source(project_id: str, source_id: str):
        studio.data.raw(source_id)
        current = studio.store.get(project_id).get("data_sources") or []
        if source_id not in current:
            studio.store.update(project_id, data_sources=[*current, source_id])
        return Response(status_code=204)

    @app.delete("/api/projects/{project_id}/data-sources/{source_id}", status_code=204)
    async def detach_source(project_id: str, source_id: str):
        current = studio.store.get(project_id).get("data_sources") or []
        studio.store.update(project_id, data_sources=[i for i in current if i != source_id])
        return Response(status_code=204)

    def attached(project_id: str, token: str | None, name: str | None = None):
        if not token or token != studio.token(project_id):
            raise ApiError(403, "bad_token", "This request needs the project's X-Studio-Token header.")
        allowed = studio.store.get(project_id).get("data_sources") or []
        return studio.data.by_name(name, allowed) if name is not None else allowed

    @app.get("/api/projects/{project_id}/data")
    async def project_data(project_id: str, x_studio_token: str | None = Header(None)):
        attached(project_id, x_studio_token)
        return [{"name": s["name"], "kind": s["kind"], "database": s["database"]}
                for s in studio.attached_sources(project_id)]

    @app.get("/api/projects/{project_id}/data/{name}/schema")
    async def data_schema(project_id: str, name: str, table: str | None = None,
                          x_studio_token: str | None = Header(None)):
        source = attached(project_id, x_studio_token, name)
        return await asyncio.to_thread(run_schema, source, studio.data.password(source["id"]), table)

    @app.post("/api/projects/{project_id}/data/{name}/query")
    async def data_query(project_id: str, name: str, body: QueryBody, x_studio_token: str | None = Header(None)):
        source = attached(project_id, x_studio_token, name)
        save_to = None
        if body.save_as:
            save_to = ws.safe_path(studio.store.workspace(project_id), body.save_as)
            if save_to.suffix.lower() not in (".csv", ".json"):
                raise ApiError(400, "invalid_path", "save_as must end in .csv or .json.")
        limit = body.limit if body.limit and body.limit > 0 else (500_000 if save_to else 1_000)
        result = await asyncio.to_thread(run_query, source, studio.data.password(source["id"]), body.sql, limit, save_to)
        if save_to:
            result["saved"] = save_to.relative_to(studio.store.workspace(project_id).resolve()).as_posix()
        return result

    @app.get("/api/projects/{project_id}/download")
    async def download(project_id: str):
        project = studio.store.get(project_id)
        handle = tempfile.NamedTemporaryFile(prefix="studio2-", suffix=".zip", delete=False)
        handle.close()
        await asyncio.to_thread(ws.zip_workspace, studio.store.workspace(project_id), Path(handle.name),
                                bool(project.get("attach")))
        name = re.sub(r"[^A-Za-z0-9._ -]+", "", project["name"]).strip() or project_id
        return FileResponse(handle.name, media_type="application/zip", filename=f"{name}.zip",
                            background=BackgroundTask(Path(handle.name).unlink, missing_ok=True))

    # -- more places to look for skills and connectors (CONTRACT.md) --------------------------------
    @app.get("/api/library/places")
    async def library_places_view():
        return await asyncio.to_thread(places.view, settings)

    @app.put("/api/library/places")
    async def library_places_save(body: dict = Body(...)):
        if settings.shared:
            raise ApiError(403, "not_for_shared", "On a shared studio this is set by its owner, in the config file.")
        try:
            saved = await asyncio.to_thread(places.save, settings, body.get("skill_dirs"), body.get("connector_files"))
        except places.PlaceError as exc:
            raise ApiError(400, "invalid_place", str(exc)) from None
        await asyncio.to_thread(studio.lib.catalog.ensure, True)    # the new places are read at once
        return saved

    # -- keys for other services (CONTRACT.md): listed, saved and removed; never shown ------------
    @app.get("/api/services")
    async def list_services():
        return await asyncio.to_thread(services.listing, settings)

    @app.put("/api/services/{name}")
    async def save_service_key(name: str, body: dict = Body(...)):
        try:
            await asyncio.to_thread(services.save, settings, name, str(body.get("key") or ""))
        except services.UnknownService:
            raise ApiError(404, "unknown_service", "There is no such service.") from None
        except ValueError as exc:
            raise ApiError(400, "invalid_key", str(exc)) from None
        return next(item for item in services.listing(settings) if item["name"] == name)

    @app.delete("/api/services/{name}")
    async def remove_service_key(name: str):
        try:
            await asyncio.to_thread(services.remove, settings, name)
        except services.UnknownService:
            raise ApiError(404, "unknown_service", "There is no such service.") from None
        return next(item for item in services.listing(settings) if item["name"] == name)

    # -- export: one HTML file, or a folder to host or run (CONTRACT.md, "Export") ----------------
    def _export_name(project: dict) -> str:
        return re.sub(r"[^A-Za-z0-9._ -]+", "", project["name"]).strip() or project["id"]

    @app.get("/api/projects/{project_id}/export")
    async def export_options(project_id: str, page: str = "index.html"):
        project = studio.store.get(project_id)
        return await asyncio.to_thread(exporting.describe, studio.store.workspace(project_id), project, page)

    @app.get("/api/projects/{project_id}/export/page")
    async def export_page(project_id: str, page: str = "index.html"):
        project = studio.store.get(project_id)
        built = await asyncio.to_thread(exporting.one_file, studio.store.workspace(project_id), page)
        if not built["available"]:
            unknown = (built["reason"] or "").startswith("There is no page")
            raise ApiError(404 if unknown else 409, "unknown_page" if unknown else "export_unavailable", built["reason"])
        return Response(built["html"], media_type="text/html; charset=utf-8", headers={
            "content-disposition": f'attachment; filename="{_export_name(project)}.html"', "cache-control": "no-store"})

    @app.get("/api/projects/{project_id}/export/folder")
    async def export_folder(project_id: str):
        project = studio.store.get(project_id)
        # Written to a temporary file and sent from there (like the project download): a folder with a
        # large video never sits in memory whole. The file is removed once it has been sent.
        handle = tempfile.NamedTemporaryFile(prefix="studio2-export-", suffix=".zip", delete=False)
        handle.close()
        target = Path(handle.name)
        try:
            await asyncio.to_thread(exporting.folder_zip, studio.store.workspace(project_id), project, target)
        except BaseException as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, exporting.ExportUnavailable):
                raise ApiError(409, "export_unavailable", str(exc)) from None
            raise
        return FileResponse(target, media_type="application/zip", headers={
            "content-disposition": f'attachment; filename="{_export_name(project)}-export.zip"', "cache-control": "no-store"},
            background=BackgroundTask(target.unlink, missing_ok=True))

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def delete_project(project_id: str):
        await studio.delete_project(project_id)
        return Response(status_code=204)

    # -- files ------------------------------------------------------------------------------
    @app.get("/api/projects/{project_id}/files")
    async def list_files(project_id: str):
        project = studio.store.get(project_id)
        return await asyncio.to_thread(ws.list_files, studio.store.workspace(project_id), 5000,
                                       bool(project.get("attach")))

    @app.get("/api/projects/{project_id}/file")
    async def read_file(project_id: str, path: str):
        studio.store.get(project_id)
        target = ws.safe_path(studio.store.workspace(project_id), path)
        if not target.is_file():
            raise ApiError(404, "file_not_found", f"There is no file at {path}.")
        if target.stat().st_size > TEXT_LIMIT:
            raise ApiError(415, "unsupported_file", "This file is larger than 1 MB.")
        data = target.read_bytes()
        try:
            return {"path": path, "content": data.decode("utf-8"), "hash": _hash(data)}
        except UnicodeDecodeError:
            raise ApiError(415, "unsupported_file", "This file is not UTF-8 text.") from None

    @app.put("/api/projects/{project_id}/file")
    async def write_file(project_id: str, body: FilePut):
        idle_project(project_id)
        target = ws.safe_path(studio.store.workspace(project_id), body.path)
        data = body.content.encode("utf-8")
        if len(data) > TEXT_LIMIT:
            raise ApiError(413, "file_too_large", "Text files are limited to 1 MB here.")
        if target.is_dir():
            raise ApiError(400, "invalid_path", "That path is a folder.")
        if body.base_hash is not None:
            current = _hash(target.read_bytes()) if target.is_file() else None
            if current != body.base_hash:
                raise ApiError(409, "stale_file", "The file changed since you opened it. Reload it and try again.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        checkpoint = await user_checkpoint(project_id, f"Edit {body.path}")
        return {"path": body.path, "hash": _hash(data), "checkpoint": checkpoint}

    @app.post("/api/projects/{project_id}/upload")
    async def upload(project_id: str, file: UploadFile = File(...), dir: str = Form("")):
        studio.store.get(project_id)
        if project_id in studio.ships.running:
            raise ShipRunning(project_id)
        name = Path((file.filename or "").replace("\\", "/")).name
        if not name or name in (".", ".."):
            raise ApiError(400, "invalid_path", "The uploaded file needs a name.")
        rel = "/".join(part for part in (dir.strip("/"), name) if part)
        target = ws.safe_path(studio.store.workspace(project_id), rel)
        chunks, size = [], 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > UPLOAD_LIMIT:
                raise ApiError(413, "file_too_large", "Uploads are limited to 25 MB.")
            chunks.append(chunk)
        if target.is_dir():
            raise ApiError(400, "invalid_path", "That path is a folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"".join(chunks))
        # During a turn the file rides along with that turn's checkpoint.
        checkpoint = None if project_id in studio.turns.active else await user_checkpoint(project_id, f"Upload {rel}")
        return {"path": rel, "size": size, "checkpoint": checkpoint}

    @app.post("/api/projects/{project_id}/dom-edits")
    async def dom_edit(project_id: str, body: DomEdit):
        idle_project(project_id)
        workspace = studio.store.workspace(project_id)
        rel = ws.page_to_file(workspace, body.path)
        target = ws.safe_path(workspace, rel)
        before = body.before_text.strip()
        if not before:
            raise ApiError(400, "invalid_request", "The original text is empty.")
        try:
            source = target.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            raise ApiError(409, "edit_not_found", f"{rel} could not be read as text.") from None
        updated, count = ws.apply_text_edit(source, before, body.after_text.strip(), tag=body.tag)
        if updated is None:
            if count > 1:
                raise ApiError(409, "ambiguous_edit", f"That text appears {count} times in {rel}.")
            raise ApiError(409, "edit_not_found", f"That text was not found in {rel}.")
        target.write_text(updated, "utf-8")
        checkpoint = await user_checkpoint(project_id, f"Edit text in {rel}")
        return {"path": rel, "checkpoint": checkpoint}

    # -- versions ---------------------------------------------------------------------------
    def commit_or_404(project_id: str, sha: str) -> str:
        full = ws.resolve_commit(studio.store.workspace(project_id), sha, studio.since(project_id))
        if not full:
            raise ApiError(404, "version_not_found", "That version does not exist in this project.")
        return full

    @app.get("/api/projects/{project_id}/versions")
    async def versions(project_id: str):
        studio.store.get(project_id)
        return await asyncio.to_thread(ws.versions, studio.store.workspace(project_id), 500, studio.since(project_id))

    @app.get("/api/projects/{project_id}/versions/{sha}/diff")
    async def version_diff(project_id: str, sha: str):
        studio.store.get(project_id)
        return await asyncio.to_thread(ws.diff, studio.store.workspace(project_id), commit_or_404(project_id, sha))

    @app.post("/api/projects/{project_id}/restore")
    async def restore(project_id: str, body: Restore):
        idle_project(project_id)
        studio.watch(project_id)
        return {"checkpoint": await studio.restore(project_id, commit_or_404(project_id, body.sha))}

    # -- turns and events -------------------------------------------------------------------
    @app.post("/api/projects/{project_id}/turns", status_code=202)
    async def start_turn(project_id: str, body: TurnCreate):
        project = studio.store.get(project_id)
        if not body.text.strip():
            raise ApiError(400, "invalid_request", "The message is empty.")
        if body.connection and body.connection not in {c["id"] for c in studio.registry.connections()}:
            raise ApiError(400, "unknown_connection", f"There is no connection named “{body.connection}”.")
        selection = body.selection.model_dump() if body.selection else None
        if selection:
            selection["text"], selection["html"] = selection["text"][:500], selection["html"][:4000]
            selection["source"] = selection.get("source", "")[:600]
            # A development build reports full paths on this machine; the agent works in the project.
            selection["source"] = selection["source"].replace(str(studio.store.workspace(project_id)) + "/", "")
            if not selection["source"]:
                del selection["source"]   # most pages say nothing; keep the event as it was
        workspace = studio.store.workspace(project_id)
        attachments = []
        for raw in body.attachments:
            target = ws.safe_path(workspace, raw)
            if not target.is_file():
                raise ApiError(400, "missing_attachment", f"The attached file {raw} is not in the project.")
            attachments.append(target.relative_to(workspace.resolve()).as_posix())
        options = checked_options(body.options)
        if body.queue and (project_id in studio.turns.active or project_id in studio.ships.running):
            return {"queued_id": studio.turns.enqueue(project, body.text, selection, body.connection,
                                                       body.model, attachments, options)}
        return {"turn_id": studio.turns.start(project, body.text, selection, body.connection, body.model,
                                              attachments, options=options)}

    # -- what the requests cost --------------------------------------------------------------
    @app.get("/api/projects/{project_id}/cost")
    async def project_cost(project_id: str):
        studio.store.get(project_id)
        return await asyncio.to_thread(studio.costs.project_cost, project_id)

    @app.post("/api/projects/{project_id}/cost/recover")
    async def recover_cost(project_id: str):
        studio.store.get(project_id)
        if project_id in studio.turns.active:
            raise TurnRunning(project_id)
        return await studio.costs.recover(project_id)

    @app.delete("/api/projects/{project_id}/queue/{queued_id}", status_code=204)
    async def unqueue(project_id: str, queued_id: str):
        studio.store.get(project_id)
        studio.turns.unqueue(project_id, queued_id)
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/turns/{turn_id}/approvals/{approval_id}", status_code=204)
    async def answer_approval(project_id: str, turn_id: str, approval_id: str, body: Decision):
        studio.store.get(project_id)
        if studio.turns.active.get(project_id) != turn_id or not studio.turns.answer(turn_id, approval_id, body.decision):
            raise ApiError(404, "unknown_approval", "That request is no longer waiting for an answer.")
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/turns/{turn_id}/questions/{question_id}", status_code=204)
    async def answer_question(project_id: str, turn_id: str, question_id: str, request: Request):
        from . import questions
        studio.store.get(project_id)
        try:
            body = await request.json()
        except ValueError:
            raise ApiError(400, "invalid_answer", "Send {answers: [...]} or {skip: true}.") from None
        try:
            waiting = studio.turns.active.get(project_id) == turn_id and studio.turns.reply(turn_id, question_id, body)
        except questions.QuestionsError as exc:
            raise ApiError(400, "invalid_answer", str(exc)) from None
        if not waiting:
            raise ApiError(404, "unknown_question", "That question is no longer waiting for an answer.")
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/turns/{turn_id}/stop", status_code=202)
    async def stop_turn(project_id: str, turn_id: str):
        studio.store.get(project_id)
        studio.turns.stop(project_id, turn_id)
        return {"turn_id": turn_id}

    # -- ship: make it live ------------------------------------------------------------------
    @app.get("/api/projects/{project_id}/ship")
    async def ship_state(project_id: str):
        project = studio.store.get(project_id)
        if not project.get("attach"):
            raise ApiError(404, "not_attached", "Only a project brought in from a folder can be made live.")
        return studio.ships.view(project)

    @app.put("/api/projects/{project_id}/ship/recipe")
    async def ship_recipe(project_id: str, body: dict = Body(...)):
        studio.ships.set_recipe(project_id, body.get("recipe") if isinstance(body.get("recipe"), dict) else body,
                                "person")
        return studio.ships.view(studio.store.get(project_id))

    @app.post("/api/projects/{project_id}/ships", status_code=202)
    async def start_ship(project_id: str):
        return {"ship_id": await studio.ships.start(project_id, "person")}

    @app.get("/api/projects/{project_id}/ships")
    async def list_ships(project_id: str):
        studio.store.get(project_id)
        return studio.ships.records(project_id)

    @app.get("/api/projects/{project_id}/ships/{ship_id}")
    async def get_ship(project_id: str, ship_id: str):
        studio.store.get(project_id)
        record = studio.ships.record(project_id, ship_id) if re.fullmatch(r"s_[0-9a-f]{8}", ship_id) else None
        if record is None:
            raise ApiError(404, "ship_not_found", "There is no such ship.")
        return record

    @app.post("/api/projects/{project_id}/ships/{ship_id}/decision", status_code=204)
    async def decide_ship(project_id: str, ship_id: str, body: ShipDecision):
        studio.store.get(project_id)
        studio.ships.decide(project_id, ship_id, body.decision, body.confirm)
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/ship/update-copy")
    async def update_copy(project_id: str):
        studio.watch(project_id)
        return await studio.ships.update_copy(project_id)

    @app.get("/api/projects/{project_id}/events")
    async def events(project_id: str, after: int = 0, last_event_id: str | None = Header(None)):
        project = studio.store.get(project_id)
        await studio.open_preview(project)
        studio.watch(project_id)
        if last_event_id and last_event_id.strip().isdigit():
            after = int(last_event_id)
        stream = studio.log(project_id).stream(max(after, 0), settings.ping_seconds)
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # -- reached through one mapped port: project pages are not carried (CONTRACT, "Access from other devices")
    @app.get("/no-page/{rest:path}", include_in_schema=False)
    async def no_page(rest: str):
        return HTMLResponse(NO_PAGE, status_code=404, headers={"Cache-Control": "no-store"})

    # -- the guide and the benchmarks (CONTRACT.md, "The guide and the benchmarks, inside the studio")
    @app.get("/site", include_in_schema=False)
    @app.get("/site/{path:path}", include_in_schema=False)
    async def site(path: str | None = None):
        root = settings.site_dir.resolve()
        missing = ApiError(404, "not_found", "There is no such page in the guide.")
        if path is None:                     # "/site": the folder itself, named without its slash
            return RedirectResponse("/site/", status_code=307)
        if not (root / "index.html").is_file() or any(part.startswith(".") for part in path.split("/")):
            raise missing
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise missing
        if target.is_dir():
            if path and not path.endswith("/"):   # relative links on the page need the folder's own address
                return RedirectResponse(f"/site/{path}/", status_code=307)
            target = target / "index.html"
        if target.suffix.lower() not in SITE_FILES or not target.is_file():
            raise missing
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    # -- web UI -----------------------------------------------------------------------------
    @app.get("/{path:path}", include_in_schema=False)
    async def web_ui(path: str):
        if path == "api" or path.startswith("api/"):
            raise ApiError(404, "not_found", "There is no such API route.")
        dist = settings.web_dist.resolve()
        if not (dist / "index.html").is_file():
            return HTMLResponse(NOT_BUILT)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist in candidate.parents:
            manifest = candidate.suffix == ".webmanifest"
            return FileResponse(candidate, media_type="application/manifest+json" if manifest else None)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
