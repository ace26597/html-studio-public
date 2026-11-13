"""Model lists at setup: a gateway's own list, a long list narrowed to a suggestion, names the
person gives, and the key kept out of every message (CONTRACT.md, "The four accounts")."""

from __future__ import annotations

import json

import httpx
import pytest

from studio2_server import cli, gateway, keystore, setup
from studio2_server.api import create_app
from studio2_server.config import Settings

KEY = "sk-gw-test-0123456789abcdefKEY"
NO_TAILSCALE = lambda: {"found": False, "address": None, "detail": "none"}     # noqa: E731
LONG = ([f"openrouter/vendor-{i}" for i in range(400)]
        + ["anthropic/claude-opus-4-1", "anthropic/claude-opus-4-10", "anthropic/claude-opus-4-9",
           "anthropic/claude-sonnet-4-5", "anthropic/claude-haiku-4-5", "openai/gpt-5", "openai/gpt-4o",
           "openai/gpt-5-mini", "google/gemini-2.5-pro", "google/gemini-2.5-flash", "openai/text-embedding-3"])


def real_settings(tmp_path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", config_path=tmp_path / "cfg" / "config.json",
                        web_dist=tmp_path / "no-dist", home=tmp_path / "home")
    settings._secrets = keystore.FileStore(tmp_path / "cfg" / "secrets.json")
    return settings


def gateway_answer(ids, status=200, body=None):
    seen: list = []

    def handler(request):
        seen.append(request)
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": "no"})
        return httpx.Response(status, json=body if body is not None else {"data": [{"id": i} for i in ids]})
    return httpx.MockTransport(handler), seen


def test_a_short_list_is_offered_whole_and_a_long_one_is_narrowed():
    short = ["claude-sonnet-4-5", "gpt-x", "local-llama"]
    assert gateway.suggest_models(short) == short
    suggested = gateway.suggest_models(LONG)
    # Claude's Opus and Sonnet first, then GPT, Haiku last as the cheap model; Gemini is not suggested.
    assert suggested == ["anthropic/claude-opus-4-10", "anthropic/claude-opus-4-9", "anthropic/claude-sonnet-4-5",
                         "openai/gpt-5-mini", "openai/gpt-5", "anthropic/claude-haiku-4-5"]
    assert not any("gemini" in name for name in suggested)
    assert set(suggested) <= set(LONG) and len(suggested) <= gateway.SUGGEST_AT_MOST
    assert gateway.suggest_models([f"x/m-{i}" for i in range(40)]) == []      # nothing made up
    assert gateway.found_note(len(LONG), len(suggested)) == "411 models found; 6 suggested."
    assert gateway.found_note(3, 3) == "3 models found."


def test_named_models_win_and_unknown_ones_are_reported():
    picked = gateway.pick_models(LONG, ["openai/gpt-4o", "made-up-model"])
    assert picked["models"] == ["openai/gpt-4o", "made-up-model"] and picked["unknown"] == ["made-up-model"]
    assert picked["total"] == len(LONG)
    assert gateway.pick_models([], ["anything"])["unknown"] == []          # no list: nothing to check against
    assert gateway.pick_models(LONG, [])["models"] == gateway.suggest_models(LONG)


async def test_a_gateway_is_asked_for_its_list_and_says_why_not_without_the_key():
    transport, seen = gateway_answer(["b-model", "a-model", "a-model"])
    async with httpx.AsyncClient(transport=transport) as client:
        assert await gateway.list_gateway_models("http://gw.test:4000/", KEY, client=client) == \
            [{"id": "a-model"}, {"id": "b-model"}]
        assert str(seen[-1].url) == "http://gw.test:4000/v1/models"
        await gateway.list_gateway_models("http://gw.test:4000/v1", KEY, client=client)
        assert str(seen[-1].url) == "http://gw.test:4000/v1/models"
        with pytest.raises(gateway.GatewayError) as refused:
            await gateway.list_gateway_models("http://gw.test:4000", "wrong-key-123", client=client)
        assert "refused the key (HTTP 401)" in str(refused.value)
    for transport, words in ((gateway_answer([], body={"object": "nothing"})[0], "no list of models"),
                             (gateway_answer([], status=500)[0], "HTTP 500")):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(gateway.GatewayError) as failed:
                await gateway.list_gateway_models("http://gw.test:4000", KEY, client=client)
            assert words in str(failed.value) and KEY not in str(failed.value)

    def down(request):
        raise httpx.ConnectError("refused", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(gateway.GatewayError) as unreachable:
            await gateway.list_gateway_models("http://gw.test:4000", KEY, client=client)
    assert "could not be reached" in str(unreachable.value) and "Type the model names" in str(unreachable.value)
    assert KEY not in str(unreachable.value)


async def test_the_setup_screen_gets_a_gateway_list_with_its_suggestion(tmp_path, monkeypatch):
    settings = real_settings(tmp_path)
    transport, _seen = gateway_answer(LONG)
    real = gateway.list_gateway_models

    async def faked(base_url, key, client=None):
        async with httpx.AsyncClient(transport=transport) as own:
            return await real(base_url, key, client=own)
    monkeypatch.setattr(gateway, "list_gateway_models", faked)
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        answer = await client.post("/api/setup/models", json={"account": "gateway", "api_key": KEY,
                                                               "base_url": "http://gw.test:4000"})
        assert answer.status_code == 200
        body = answer.json()
        assert body["total"] == len(LONG) and body["suggested"] == gateway.suggest_models(LONG)
        refused = await client.post("/api/setup/models", json={"account": "gateway", "api_key": "wrong-key-123",
                                                                "base_url": "http://gw.test:4000"})
        assert refused.status_code == 400 and "refused the key" in refused.text and KEY not in refused.text
    assert KEY not in answer.text


def test_terminal_gateway_setup_narrows_searches_and_writes_only_the_picked(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path)
    asked_with: list = []

    def fetch(provider, key, base):
        asked_with.append((provider, key, base))
        return [{"id": i} for i in LONG], None
    monkeypatch.setattr(cli, "fetch_models", fetch)
    gateway_number = str(list(setup.ACCOUNTS).index("gateway") + 1)
    typed = iter([gateway_number, "http://gw.test:4000",   # 1. a gateway I run, its address
                  "/gpt-4o",                               #    search
                  "1, anthropic/claude-sonnet-4-5",        #    the first found, and one by name
                  "",                                      #    default: the first
                  "",                                      #    background: the guess
                  "1", "",                                 # 2. this computer only, port as it is
                  "1"])                                    # 3. just me
    answers = cli.interactive_answers(settings, ask=lambda _prompt: next(typed), secret=lambda _prompt: KEY,
                                      login=lambda: {"found": False, "plan": None, "detail": "No sign-in."},
                                      tailscale=NO_TAILSCALE)
    assert asked_with == [("gateway", KEY, "http://gw.test:4000")]
    assert answers["account"]["models"] == ["openai/gpt-4o", "anthropic/claude-sonnet-4-5"]
    out = capsys.readouterr().out
    assert "411 models found; 6 suggested." in out and "1. openai/gpt-4o" in out and KEY not in out
    result = setup.plan(answers, settings, tailscale=NO_TAILSCALE)
    setup.apply(result, settings)
    text = settings.config_path.read_text()
    written = json.loads(text)["connections"][0]
    assert written["models"] == ["openai/gpt-4o", "anthropic/claude-sonnet-4-5"]
    assert written["default_model"] == "openai/gpt-4o" and KEY not in text
    assert "vendor-1" not in text


def test_terminal_takes_the_suggestion_on_enter_and_types_names_when_the_fetch_fails(monkeypatch, capsys):
    replies = iter([""])
    assert cli.pick_from_list(lambda _p: next(replies), LONG, "Models to offer") == gateway.suggest_models(LONG)
    replies = iter(["made-up", "n", "made-up", "y"])     # not in the list: refused once, then allowed
    assert cli.pick_from_list(lambda _p: next(replies), LONG, "Models to offer") == ["made-up"]
    replies = iter(["", "gw-model-a gw-model-b"])          # no list: something must be typed
    assert cli.pick_from_list(lambda _p: next(replies), [], "Models to offer") == ["gw-model-a", "gw-model-b"]
    replies = iter(["99", "2"])                              # a number not shown is refused
    assert cli.pick_from_list(lambda _p: next(replies), ["a", "b"], "Models to offer") == ["b"]
    out = capsys.readouterr().out
    assert "Name at least one." in out and "A number is one of those shown" in out
