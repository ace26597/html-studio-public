"""Models, the check, and the studio's own LiteLLM over HTTP (CONTRACT.md, "Any model (step 3n)")."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import gateway, setup
from .access import is_local
from .config import Settings, connection_key
from .modelcheck import CheckError


class CheckBody(BaseModel):
    connection: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    confirm_cost: bool = False


class ListBody(BaseModel):
    account: str = Field(min_length=1, max_length=40)
    api_key: str | None = Field(default=None, max_length=4000)
    base_url: str | None = Field(default=None, max_length=500)
    id: str | None = Field(default=None, max_length=40)


def register(app: FastAPI, studio, settings: Settings) -> None:
    install: dict = {"task": None, "error": None, "lines": []}

    def problem(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def local_only(request: Request) -> JSONResponse | None:
        # Installing a program and reading a provider with a typed key belong to the person at the
        # machine, as setup does.
        if settings.shared and not is_local(request.scope):
            return problem(403, "setup_is_local", "This can only be done on the computer this studio runs on.")
        return None

    def litellm_status() -> dict:
        status = studio.registry.sidecar.status(studio.registry.connections())
        task = install["task"]
        return {**status, "installing": bool(task and not task.done()), "install_error": install["error"],
                "install_log": install["lines"][-6:]}

    @app.get("/api/models")
    async def models():
        return {**studio.checks.view(), "note": setup.other_models(), "litellm": litellm_status()}

    @app.post("/api/models/check", status_code=202)
    async def check(body: CheckBody):
        try:
            return studio.checks.start(body.connection, body.model, body.confirm_cost)
        except CheckError as exc:
            return problem(exc.status, exc.code, exc.message)

    @app.post("/api/models/check/stop", status_code=202)
    async def stop_check():
        return {"stopping": studio.checks.stop()}

    @app.get("/api/litellm")
    async def get_litellm():
        return litellm_status()

    @app.post("/api/litellm/install", status_code=202)
    async def install_litellm(request: Request):
        refused = local_only(request)
        if refused:
            return refused
        if gateway.installed()["installed"]:
            return litellm_status()
        task = install["task"]
        if not (task and not task.done()):
            install.update(error=None, lines=[])

            async def run() -> None:
                try:
                    await asyncio.to_thread(gateway.install, install["lines"].append)
                except gateway.GatewayError as exc:
                    install["error"] = str(exc)

            install["task"] = asyncio.create_task(run())
        return litellm_status()

    @app.post("/api/setup/models")
    async def list_models(request: Request, body: ListBody):
        """The provider's own list of models, asked for with the key just typed (used once, not
        kept) or the one already saved for that account."""
        refused = local_only(request)
        if refused:
            return refused
        preset = setup.ACCOUNTS.get(body.account)
        own_gateway = bool(preset) and preset["kind"] == "anthropic-gateway"
        if not preset or not (preset.get("provider") or own_gateway):
            return problem(400, "no_model_list", "This kind of account has no list of models to fetch.")
        key = (body.api_key or "").strip() or None
        base_url = (body.base_url or "").strip() or None
        wanted = body.id or preset["id"]
        saved = next((c for c in studio.registry.connections() if c["id"] == wanted and c.get("kind") == preset["kind"]
                      and c.get("provider") == preset.get("provider")), None)
        if not key and saved:
            import os
            key = connection_key(saved, os.environ, settings.secret_store())
        try:
            if own_gateway:
                found = await gateway.list_gateway_models(base_url or str((saved or {}).get("base_url") or ""), key)
            else:
                found = await gateway.list_models(preset["provider"], key, base_url)
        except gateway.GatewayError as exc:
            return problem(400, "no_model_list", str(exc))
        names = [m["id"] for m in found]
        prices = {m["id"]: m["price"] for m in found if "price" in m}
        return {"models": found, "total": len(names), "suggested": gateway.suggest_models(names),
                "suggested_small_model": gateway.cheapest(names, prices)}
