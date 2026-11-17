"""`GET` and `POST /api/setup`: the first-run screen's side of setup (CONTRACT.md, "Install and
first run"). The answers and what they do are in setup.py; the terminal uses the same."""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__, gateway, setup
from .access import is_local
from .config import Settings

RESTART_COMMAND = "studio stop && studio start"
DETECT_SECONDS = 60.0
NOT_CHECKED = "Not checked: this studio runs the scripted agent."


class SetupBody(BaseModel):
    answers: dict = Field(default_factory=dict)
    dry_run: bool = False


def first_run(settings: Settings) -> bool:
    """`health.setup.needed`: the interface opens the first-run screen by itself when true."""
    projects = settings.projects_dir
    has_projects = projects.is_dir() and any(p.name.startswith("p_") for p in projects.iterdir())
    return setup.needed(settings, has_projects)


def register(app: FastAPI, settings: Settings) -> None:
    found: dict = {"at": 0.0, "value": None}

    async def detected(fresh: bool = False) -> dict:
        if settings.fake:   # the fake studio and the tests start no program and read no login
            return {"claude_login": {"found": False, "plan": None, "detail": NOT_CHECKED},
                    "tailscale": {"found": False, "address": None, "detail": NOT_CHECKED}}
        if fresh or found["value"] is None or time.monotonic() - found["at"] > DETECT_SECONDS:
            login, tailscale = await asyncio.gather(asyncio.to_thread(setup.detect_claude_login),
                                                    asyncio.to_thread(setup.detect_tailscale))
            found.update(at=time.monotonic(), value={"claude_login": login, "tailscale": tailscale})
        return found["value"]

    def refuse(request: Request) -> JSONResponse | None:
        # A shared studio's settings (where it listens, whose key pays) belong to the person at the
        # machine, not to everyone who holds the access key.
        if settings.shared and not is_local(request.scope):
            return JSONResponse({"error": {"code": "setup_is_local", "message":
                                           "Setup can only be opened on the computer this studio runs on."}},
                                status_code=403)
        return None

    async def describe(fresh: bool = False) -> dict:
        store = settings.secret_store()
        return {
            "needed": first_run(settings),
            "fake": settings.fake,
            "version": __version__,
            "config_path": str(settings.config_path),
            "config_exists": settings.config_path.is_file(),
            "secrets": {"backend": store.backend, "label": store.label},
            "detected": await detected(fresh),
            "accounts": setup.accounts(settings),
            # What is said wherever a model that is not Claude's is chosen, and whether the LiteLLM
            # those accounts run through is installed (models_api installs it on a click).
            "other_models": setup.other_models(),
            "litellm": {"installed": gateway.installed()["installed"], "version": gateway.VERSION,
                        "size": gateway.SIZE_NOTE, "install_command": gateway.INSTALL_COMMAND},
            "current": setup.configured(settings),
            "running": {"port": settings.port, "hosts": list(settings.hosts), "open_tailnet": settings.open_tailnet,
                        "shared": settings.shared, "https": bool(settings.tls)},
            "network_warning": setup.NETWORK_WARNING,
            "restart_command": RESTART_COMMAND,
        }

    @app.get("/api/setup")
    async def get_setup(request: Request, fresh: bool = False):
        return refuse(request) or await describe(fresh)

    @app.post("/api/setup")
    async def post_setup(request: Request, body: SetupBody):
        refused = refuse(request)
        if refused:
            return refused

        tailscale = (await detected())["tailscale"]
        try:
            result = setup.plan(body.answers, settings, tailscale=lambda: tailscale, from_api=True)
        except setup.SetupError as exc:
            return JSONResponse({"error": {"code": "invalid_setup", "message": str(exc)}}, status_code=400)
        running = (settings.port, tuple(settings.hosts), settings.open_tailnet, settings.shared)
        restart = result.restart_for(running)
        if not body.dry_run:
            await asyncio.to_thread(setup.apply, result, settings)
        # Never the keys themselves, typed or made here: only what was done with them.
        return {"applied": not body.dry_run, "changed": result.changed, "changes": result.changes,
                "diff": result.diff(settings.config_path), "restart_needed": restart,
                "restart_command": RESTART_COMMAND, "setup": await describe()}
