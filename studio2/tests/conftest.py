"""Shared fixtures. Everything runs on the fake harness: zero model calls, temp data dirs."""

from __future__ import annotations

import os

os.environ["STUDIO2_FAKE"] = "1"
os.environ.setdefault("STUDIO2_FAKE_DELAY", "0")

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from studio2_server.api import create_app  # noqa: E402
from studio2_server.config import Settings  # noqa: E402

# A private port range so the suite never needs 8200-8299 (override with STUDIO2_PORT_RANGE).
_BASE = 21000 + (os.getpid() % 1500) * 20
PORT_RANGE = tuple(int(p) for p in os.environ["STUDIO2_PORT_RANGE"].split("-")) \
    if os.environ.get("STUDIO2_PORT_RANGE") else (_BASE, _BASE + 19)


@pytest.fixture(autouse=True)
def _fake_delay(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0")
    # The studios running on this computer are not the suite's (config.studios_dir).
    monkeypatch.setenv("STUDIO2_STUDIOS_DIR", str(tmp_path_factory.mktemp("studios")))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", fake=True, port_range=PORT_RANGE,
                    config_path=tmp_path / "no-config.json", web_dist=tmp_path / "no-dist", site_dir=tmp_path / "no-site",
                    ping_seconds=0.2, home=tmp_path / "home")


@pytest_asyncio.fixture
async def app(settings):
    application = create_app(settings)
    await application.state.studio.start()
    try:
        yield application
    finally:
        await application.state.studio.stop()


@pytest.fixture
def studio(app):
    return app.state.studio


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8200") as http:
        yield http


@pytest_asyncio.fixture
async def project(client) -> dict:
    response = await client.post("/api/projects", json={"name": "Test project"})
    assert response.status_code == 201, response.text
    return response.json()


async def run_turn(client, studio, project_id: str, text: str, **extra) -> list[dict]:
    """Post a turn, wait for it to finish, return that turn's events (without files_changed)."""
    response = await client.post(f"/api/projects/{project_id}/turns", json={"text": text, **extra})
    assert response.status_code == 202, response.text
    turn_id = response.json()["turn_id"]
    await studio.turns.wait(project_id)
    return [e for e in studio.log(project_id).read()
            if e["turn_id"] == turn_id and e["type"] != "files_changed"]
