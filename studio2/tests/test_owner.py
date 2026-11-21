"""The owner of a shared studio (CONTRACT.md, "The owner of a shared studio"): known by the email
the gate in front signed in, and by nothing else."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import pytest_asyncio

from studio2_server import access
from studio2_server.access import Gate, is_owner
from pathlib import Path

from studio2_server.config import Settings

ME, GUEST = "owner@example.org", "guest@example.org"


def studio_settings(**fields) -> Settings:
    return Settings(data_dir=Path("/nonexistent/studio-data"), **fields)


def scope(email: str | None = None, client: str = "127.0.0.1", forwarded: bool = True) -> dict:
    headers = [(b"host", b"studio.example.org")]
    if forwarded:       # what a tunnel on this machine adds to every request it hands over
        headers.append((b"x-forwarded-for", b"203.0.113.9"))
    return {"type": "http", "client": (client, 5000), "headers": headers,
            **({"state": {"gate_email": email}} if email is not None else {})}


def through_the_gate(app, email: str):
    """The app as the access guard hands it a request the gate signed in as `email`."""
    async def signed_in(received, receive, send):
        if received["type"] == "http":
            received = {**received, "state": {**(received.get("state") or {}), "gate_email": email}}
        await app(received, receive, send)
    return signed_in


@pytest_asyncio.fixture
async def shared(app, settings):
    """A shared studio with one owner, and two people at its door."""
    settings.shared, settings.owners = True, (ME,)
    made = {}
    for name, email in (("owner", ME), ("guest", GUEST)):
        made[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=through_the_gate(app, email)),
                                       base_url="http://127.0.0.1:8200")
    try:
        yield made
    finally:
        for client in made.values():
            await client.aclose()
        settings.shared, settings.owners = False, ()


# -- who the owner is -------------------------------------------------------------------------
def test_only_the_gates_signed_email_makes_an_owner():
    mine = studio_settings(shared=True, owners=(ME,))
    assert is_owner(scope(ME), mine) is True
    assert is_owner(scope(GUEST), mine) is False
    assert is_owner(scope(""), mine) is False                      # a token that carried no email
    assert is_owner(scope(None), mine) is False                    # no gate sign-in at all
    # Not the address: through a tunnel every request arrives from this machine, a guest's too.
    assert is_owner(scope(None, client="127.0.0.1", forwarded=False), mine) is False
    assert is_owner(scope(None, client="100.64.0.9", forwarded=False), mine) is False
    # A shared studio with nobody named has no owner through its door.
    assert is_owner(scope(ME), studio_settings(shared=True)) is False
    # A studio that is not shared has one person, and everything is theirs.
    assert is_owner(scope(None), studio_settings()) is True and is_owner(scope(GUEST), studio_settings(owners=(ME,))) is True


def test_the_owners_list_is_read_from_the_environment_or_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO2_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("STUDIO2_OWNERS", " Owner@Example.org , second@example.org,not-an-email,, ")
    assert Settings.from_env().owners == ("owner@example.org", "second@example.org")
    monkeypatch.delenv("STUDIO2_OWNERS")
    (tmp_path / "config.json").write_text('{"owners": ["File@Example.org", 5, "x"]}')
    assert Settings.from_env().owners == ("file@example.org",)
    (tmp_path / "config.json").write_text("{}")
    assert Settings.from_env().owners == ()


def test_the_gate_says_whose_token_it_passed():
    gate = Gate("https://gate.example.org/certs", ("aud",))
    header = {gate.header.lower().encode() if isinstance(gate.header, str) else gate.header: b"tok.en.one"}
    key = next(iter(header))
    gate.header = key                                             # as the guard reads it from the request
    assert gate.email(header) == ""                               # not accepted yet: nobody
    gate.accepted["tok.en.one"], gate.emails["tok.en.one"] = time.time() + 60, ME
    assert gate.email(header) == ME
    assert gate.email({key: b"another.token"}) == ""


# -- what the owner may do that a guest may not -----------------------------------------------
async def test_health_tells_each_of_them(shared, client):
    assert (await shared["owner"].get("/api/health")).json()["owner"] is True
    assert (await shared["guest"].get("/api/health")).json()["owner"] is False
    assert (await client.get("/api/health")).json()["owner"] is False          # on the machine, without the gate


async def test_the_owner_writes_the_every_project_text_and_a_guest_cannot(shared, studio, project):
    refused = await shared["guest"].put("/api/instructions", json={"text": "Mine."})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
    assert (await shared["guest"].get("/api/instructions")).status_code == 403
    saved = await shared["owner"].put("/api/instructions", json={"text": "Our work is in Maharashtra, India."})
    assert saved.status_code == 200 and (await shared["owner"].get("/api/instructions")).json() == saved.json()
    assert "  | Our work is in Maharashtra, India." in studio.agent_context(project["id"], None)


async def test_the_owner_changes_the_library_and_a_guest_cannot(shared, studio):
    skill = {"name": "house-style", "description": "Use when writing anything for us.", "body": "Write plainly."}
    refused = await shared["guest"].post("/api/skills", json=skill)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_for_shared"
    assert (await shared["owner"].post("/api/skills", json=skill)).status_code == 201
    assert (await shared["guest"].put("/api/skills/house-style", json={"body": "Shout."})).status_code == 403
    assert (await shared["guest"].delete("/api/skills/house-style")).status_code == 403
    assert (await shared["guest"].post("/api/skills/copy", json={"from": "yours:house-style", "name": "mine"})).status_code == 403
    copied = await shared["owner"].post("/api/skills/copy", json={"from": "yours:house-style", "name": "second-style"})
    assert copied.status_code == 201, copied.text
    assert (await shared["owner"].put("/api/skills/house-style", json={"body": "Write plainly and briefly."})).status_code == 200
    assert (await shared["owner"].delete("/api/skills/second-style")).status_code == 204
    # Everything else about a shared studio stays as it is for the owner too.
    assert (await shared["owner"].get("/api/health")).json()["shared"] is True


async def test_a_request_keeps_who_started_it(shared, studio, project):
    pid = project["id"]

    async def started_by(who: str) -> bool:
        turn = (await shared[who].post(f"/api/projects/{pid}/turns", json={"text": "slow, please"})).json()["turn_id"]
        mine = turn in studio.turns.by_owner
        await shared[who].post(f"/api/projects/{pid}/turns/{turn}/stop")
        await studio.turns.wait(pid)
        await asyncio.sleep(0.05)
        assert turn not in studio.turns.by_owner                    # nothing kept once it has ended
        return mine

    assert await started_by("owner") is True
    assert await started_by("guest") is False
    # A guest's message that waits in the queue is still a guest's when its turn comes.
    first = (await shared["owner"].post(f"/api/projects/{pid}/turns", json={"text": "slow, please"})).json()["turn_id"]
    queued = (await shared["guest"].post(f"/api/projects/{pid}/turns", json={"text": "then this", "queue": True})).json()["queued_id"]
    assert queued in studio.turns.queued_guests
    await shared["owner"].post(f"/api/projects/{pid}/turns/{first}/stop")
    await studio.turns.wait(pid)


def test_a_skill_from_the_chat_is_the_owners_to_save(tmp_path):
    from studio2_server import ownskills
    from studio2_server.library import LibraryError

    class Shared:
        settings = studio_settings(shared=True)

    with pytest.raises(LibraryError) as refused:
        ownskills.plan(Shared(), tmp_path, {"name": "x", "description": "y", "body": "z"})
    assert refused.value.code == "not_for_shared"
    assert access.is_owner(scope(ME), studio_settings(shared=True, owners=(ME,)))     # and the owner's request passes that gate
