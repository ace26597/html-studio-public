"""A project's own back end calls language models through the studio (CONTRACT.md, "Models for a project's back end")."""

import dataclasses
import json

import httpx
import pytest

from studio2_server.api import create_app, is_paid_model

GATEWAY_KEY = "sk-gateway-secret"


@pytest.fixture
async def gated(settings, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"connections": [
        {"id": "litellm", "kind": "anthropic-gateway", "base_url": "http://gateway.test:4000", "api_key_env": "TEST_GATEWAY_KEY"}]}))
    monkeypatch.setenv("TEST_GATEWAY_KEY", GATEWAY_KEY)
    application = create_app(dataclasses.replace(settings, config_path=config, app_model_daily_usd=0.05))
    studio = application.state.studio
    seen = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == f"Bearer {GATEWAY_KEY}"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-local"}, {"id": "openrouter/openai/gpt-x"},
                                                      {"id": "openrouter/free"}]})
        model = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 9}},
                              headers={"x-litellm-response-cost": "0.03" if is_paid_model(model) else "0"})

    studio.gateway_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    await studio.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1:8200") as client:
        project = (await client.post("/api/projects", json={"name": "Model app"})).json()
        yield client, studio, project["id"], seen
    await studio.stop()


async def test_the_app_gets_an_address_and_never_the_gateway_key(gated):
    client, studio, pid, _seen = gated
    env = studio.app(pid).extra_env
    assert env["STUDIO_MODEL_URL"].endswith(f"/api/projects/{pid}/model/v1") and GATEWAY_KEY not in json.dumps(env)
    told = studio.system_context()      # a fact of this studio's configuration: in the system prompt
    assert "$STUDIO_MODEL_URL" in told and GATEWAY_KEY not in told and GATEWAY_KEY not in studio.agent_context(pid)
    assert (await client.get(f"/api/projects/{pid}/model/v1/models")).status_code == 401
    wrong = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", json={"model": "gpt-local"},
                              headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


async def test_models_are_listed_with_paid_marked_and_calls_are_forwarded(gated):
    client, studio, pid, seen = gated
    auth = {"Authorization": f"Bearer {studio.token(pid)}"}        # what an OpenAI client sends
    listed = (await client.get(f"/api/projects/{pid}/model/v1/models", headers=auth)).json()["data"]
    assert [(m["id"], m["paid"]) for m in listed] == [("gpt-local", False), ("openrouter/openai/gpt-x", True),
                                                      ("openrouter/free", False)]
    answer = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth,
                               json={"model": "gpt-local", "messages": [{"role": "user", "content": "hello"}]})
    assert answer.status_code == 200 and answer.json()["choices"][0]["message"]["content"] == "hi"
    assert json.loads(seen[-1].content)["messages"][0]["content"] == "hello"
    stream = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth,
                               json={"model": "gpt-local", "stream": True})
    assert stream.status_code == 400 and stream.json()["error"]["code"] == "no_streaming"


async def test_paid_models_stop_at_the_daily_limit_and_free_ones_do_not(gated):
    client, studio, pid, _seen = gated
    auth = {"X-Studio-Token": studio.token(pid)}
    paid = {"model": "openrouter/openai/gpt-x", "messages": []}
    first = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth, json=paid)
    assert first.status_code == 200 and first.headers["x-studio-model-cost"] == "0.030000"
    assert (await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth, json=paid)).status_code == 200
    assert studio.model_spend_today(pid) == pytest.approx(0.06)     # the cap is 0.05: the call that crosses it still runs
    blocked = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth, json=paid)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "daily_limit"
    free = await client.post(f"/api/projects/{pid}/model/v1/chat/completions", headers=auth,
                             json={"model": "gpt-local", "messages": []})
    assert free.status_code == 200


async def test_without_a_gateway_the_route_says_so(client, studio, project):
    answer = await client.get(f"/api/projects/{project['id']}/model/v1/models",
                              headers={"X-Studio-Token": studio.token(project["id"])})
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "no_gateway"
    assert "$STUDIO_MODEL_URL" not in studio.system_context()
