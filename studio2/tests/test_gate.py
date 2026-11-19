"""A gate in front that signs people in: its signed token stands in for the access key."""

from __future__ import annotations

import dataclasses
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from studio2_server.access import COOKIE, check_gate, token_for
from studio2_server.api import create_app

KEY = "a-long-enough-access-key-for-tests"
KEYS_URL = "https://team.gate.example/certs"
ISSUER = "https://team.gate.example"
AUD = "aud-of-this-studio"
NAME = "https://studio.example.com"


def _pair(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": kid, "alg": "RS256", "use": "sig"}


GATE_KEY, GATE_JWK = _pair("gate-1")
OTHER_KEY, OTHER_JWK = _pair("gate-1")       # a forger's key that claims the same id


def token(key=GATE_KEY, kid="gate-1", alg="RS256", **claims) -> str:
    body = {"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600, "email": "person@example.com", **claims}
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
async def gated(settings):
    application = create_app(dataclasses.replace(
        settings, access_key=KEY, shared=True, names=("studio.example.com",),
        gate=(KEYS_URL, (AUD,), "Cf-Access-Jwt-Assertion", None)))
    gate = application.state.studio.gate
    gate.calls = 0

    def fetch(url):
        assert url == KEYS_URL
        gate.calls += 1
        return {"keys": [GATE_JWK]}

    gate.fetch = fetch
    await application.state.studio.start()
    yield application
    await application.state.studio.stop()


def through_the_gate(app, value: str | None):
    headers = {"X-Forwarded-For": "203.0.113.5"}
    if value is not None:
        headers["Cf-Access-Jwt-Assertion"] = value
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))
    return httpx.AsyncClient(transport=transport, base_url=NAME, headers=headers)


async def test_a_token_the_gate_signed_needs_no_key(gated):
    async with through_the_gate(gated, token()) as person:
        assert (await person.get("/api/projects")).status_code == 200
        page = await person.get("/", headers={"Accept": "text/html"})
        assert page.status_code == 200                      # never sent to the sign-in page
        made = await person.post("/api/projects", json={"name": "Through the gate"}, headers={"Origin": NAME})
        assert made.status_code == 201, made.text
        assert (await person.get("/api/setup")).status_code == 403     # signed in is still not local
    assert gated.state.studio.gate.calls == 1               # the keys were fetched once, then kept


@pytest.mark.parametrize("bad", [
    lambda: None,                                           # no header at all
    lambda: "not-a-token",
    lambda: token(aud=["another-app"]),                     # meant for another application
    lambda: token(iss="https://someone-else.example"),
    lambda: token(exp=int(time.time()) - 3600),             # expired
    lambda: token(exp=None),                                # no expiry at all
    lambda: token(key=OTHER_KEY),                           # signed by someone else, same key id
    lambda: token(kid="unknown"),
    lambda: jwt.encode({"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600}, "x" * 40, algorithm="HS256",
                       headers={"kid": "gate-1"}),          # another algorithm
    lambda: jwt.encode({"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600}, None, algorithm="none",
                       headers={"kid": "gate-1"}),          # unsigned
])
async def test_anything_else_is_as_if_the_header_were_absent(gated, bad):
    async with through_the_gate(gated, bad()) as stranger:
        assert (await stranger.get("/api/projects")).status_code == 401
        page = await stranger.get("/", headers={"Accept": "text/html"})
        assert page.status_code == 302 and page.headers["location"].startswith(NAME + "/login?next=")
        stranger.cookies.set(COOKIE, token_for(KEY))        # the key sign-in still works
        assert (await stranger.get("/api/projects")).status_code == 200


async def test_without_the_keys_no_token_is_accepted_and_they_are_asked_for_at_most_once_a_minute(gated):
    gate = gated.state.studio.gate

    def down(url):
        gate.calls += 1
        raise OSError("no network")

    gate.fetch = down
    async with through_the_gate(gated, token()) as person:
        assert (await person.get("/api/projects")).status_code == 401
        assert (await person.get("/api/projects")).status_code == 401
    assert gate.calls == 1


async def test_a_project_port_takes_the_token_too(gated):
    async with through_the_gate(gated, token()) as person:
        made = (await person.post("/api/projects", json={"name": "Page"}, headers={"Origin": NAME})).json()
    preview = gated.state.studio.previews
    guard = preview.guard(lambda scope, receive, send: None)
    assert guard.gate is gated.state.studio.gate and guard.own_login is False and made["port"]


def test_the_two_settings_go_together_and_the_keys_come_over_https():
    check_gate(None)
    check_gate((KEYS_URL, (AUD,), "Cf-Access-Jwt-Assertion", None))
    for wrong in ((KEYS_URL, (), "h", None), ("", (AUD,), "h", None), ("http://team.gate.example/certs", (AUD,), "h", None)):
        with pytest.raises(SystemExit):
            check_gate(wrong)
