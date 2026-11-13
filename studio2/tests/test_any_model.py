"""Step 3n: presets for other providers' keys, the LiteLLM the studio runs itself, the model
check and the labels. No model is called and no LiteLLM is started: the proxy is a stand-in
program, and every store is a file under tmp_path."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
from pathlib import Path

import httpx
import pytest

from studio2_server import cli, config, doctor, gateway, keystore, modelcheck, setup
from studio2_server.api import create_app
from studio2_server.config import STUDIO2_ROOT, Settings, load_connections
from studio2_server.harness import Registry
from studio2_server.harness.base import public_connection
from studio2_server.harness.claude import ClaudeHarness, HarnessError, child_env

KEY = "sk-or-test-0123456789abcdefKEY"
OTHER_KEY = "sk-azure-test-abcdef0123456789"
NO_TAILSCALE = lambda: {"found": False, "address": None, "detail": "none"}     # noqa: E731

# A program that stands in for LiteLLM: it answers /health/liveliness on the port it is given,
# and prints one of its keys, the way a real proxy might in an error, so the log's blanking is tested.
STAND_IN = '''
import http.server, os, sys
port = int(sys.argv[2])
print("stand-in started with", os.environ.get("STUDIO_KEY_OPENROUTER"), os.environ.get("LITELLM_MASTER_KEY"), flush=True)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/health/liveliness"
        self.send_response(200 if ok else 404); self.end_headers(); self.wfile.write(b'"alive"')
    def log_message(self, *args): pass
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def real_settings(tmp_path, **extra) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", config_path=tmp_path / "cfg" / "config.json",
                        web_dist=tmp_path / "no-dist", home=tmp_path / "home", **extra)
    settings._secrets = keystore.FileStore(tmp_path / "cfg" / "secrets.json")
    return settings


def run_setup(settings, answers):
    result = setup.plan(answers, settings, tailscale=NO_TAILSCALE)
    setup.apply(result, settings)
    return result


@pytest.fixture(autouse=True)
def _no_litellm_here(tmp_path, monkeypatch):
    """Never the LiteLLM that may be installed beside the real studio."""
    monkeypatch.setenv("STUDIO2_LITELLM_HOME", str(tmp_path / "litellm-home"))


def openrouter(settings, **extra):
    return run_setup(settings, {"account": {"account": "openrouter", "api_key": KEY,
                                            "models": ["openai/gpt-x", "openai/gpt-x-mini"], **extra}})


def stand_in(tmp_path):
    program = tmp_path / "stand_in.py"
    program.write_text(STAND_IN)
    return lambda config_file, port: [sys.executable, str(program), str(config_file), str(port)]


# -- presets ------------------------------------------------------------------------------------------
def test_preset_makes_a_connection_and_the_key_is_in_the_store_and_nowhere_else(tmp_path):
    settings = real_settings(tmp_path)
    result = openrouter(settings)
    text = settings.config_path.read_text()
    assert KEY not in text + "\n".join(result.changes) + result.diff(settings.config_path)
    assert json.loads(text)["connections"] == [{
        "id": "openrouter", "kind": "litellm", "provider": "openrouter", "label": "OpenRouter key",
        "models": ["openai/gpt-x", "openai/gpt-x-mini"], "default_model": "openai/gpt-x",
        "small_model": "openai/gpt-x-mini",       # the cheapest-looking one, for background work
        "api_key_secret": "openrouter"}]
    assert settings.secret_store().get(keystore.MODEL_KEYS, "openrouter") == KEY
    assert any("Background work" in change and "gpt-x-mini" in change for change in result.changes)
    assert config.OTHER_MODELS_HEADLINE in result.changes
    assert any("studio litellm install" in change for change in result.changes)     # not installed here
    current = setup.configured(settings)["connections"][0]
    assert current["account"] == "openrouter" and current["key_from"] == "file" and KEY not in json.dumps(current)
    # Nothing else under the studio's folders holds it.
    holders = [p for p in tmp_path.rglob("*") if p.is_file() and KEY in p.read_text("utf-8", "replace")]
    assert holders == [tmp_path / "cfg" / "secrets.json"]


def test_azure_and_openai_presets_take_their_own_fields(tmp_path):
    settings = real_settings(tmp_path)
    run_setup(settings, {"account": {"account": "azure-openai", "api_key": OTHER_KEY, "models": "prod-gpt",
                                     "base_url": "https://res.openai.azure.com/", "api_version": "2024-10-21",
                                     "disable_betas": True, "max_context_tokens": "200000"}})
    run_setup(settings, {"account": {"account": "openai", "api_key_env": "MY_OPENAI", "models": ["gpt-y"],
                                     "small_model": "gpt-y-nano", "make_default": False}})
    azure, openai = json.loads(settings.config_path.read_text())["connections"]
    assert azure == {"id": "azure-openai", "kind": "litellm", "provider": "azure-openai", "label": "Azure OpenAI",
                     "base_url": "https://res.openai.azure.com", "api_version": "2024-10-21", "disable_betas": True,
                     "max_context_tokens": 200000, "models": ["prod-gpt"], "default_model": "prod-gpt",
                     "api_key_secret": "azure-openai"}
    assert openai["api_key_env"] == "MY_OPENAI" and openai["small_model"] == "gpt-y-nano" and "base_url" not in openai
    with pytest.raises(setup.SetupError, match="whole number"):
        setup.plan({"account": {"account": "openai", "api_key": KEY, "models": ["a"], "max_output_tokens": "lots"}},
                   settings, tailscale=NO_TAILSCALE)


def test_setup_lists_the_four_as_real_accounts_with_the_label(tmp_path):
    settings = real_settings(tmp_path)
    accounts = {a["account"]: a for a in setup.accounts(settings)}
    for name in ("openai", "openrouter", "azure-openai", "gemini"):
        assert accounts[name]["available"] and accounts[name]["kind"] == "litellm"
        assert accounts[name]["group"] == "other" and accounts[name]["needs_litellm"]
    assert accounts["gateway"]["group"] == "other" and not accounts["gateway"]["needs_litellm"]
    assert accounts["plan"]["group"] == accounts["api-key"]["group"] == accounts["bedrock"]["group"] == "claude"
    assert not accounts["azure-openai"]["lists_models"] and accounts["openrouter"]["lists_models"]
    note = setup.other_models()
    assert note["headline"] == "Claude models work best with the studio; other models are experimental."
    joined = " ".join(note["limits"])
    assert len(note["limits"]) == 4
    for words in ("does not support", "web search", "caching", "depends on the model"):
        assert words in joined


# -- the generated config -----------------------------------------------------------------------------
def test_generated_config_names_where_keys_come_from_and_holds_none(tmp_path):
    settings = real_settings(tmp_path)
    openrouter(settings)
    run_setup(settings, {"account": {"account": "azure-openai", "api_key": OTHER_KEY, "models": ["prod"],
                                     "base_url": "https://res.openai.azure.com", "api_version": "2024-10-21"}})
    run_setup(settings, {"account": {"account": "openai", "api_key_env": "UPSTREAM", "models": ["local-model"],
                                     "base_url": "http://127.0.0.1:4000/v1"}})
    run_setup(settings, {"account": {"account": "gemini", "api_key": "AIza-test-0123456789", "models": ["gem-flash"]}})
    connections = load_connections(settings)
    rendered = gateway.render(connections)
    text = gateway.config_text(rendered)
    assert KEY not in text and OTHER_KEY not in text and "AIza-test" not in text
    assert json.loads(text.split("\n", 2)[2]) == rendered       # two comment lines, then JSON (which is YAML)
    entries = {m["model_name"]: m["litellm_params"] for m in rendered["model_list"]}
    assert entries["openrouter/openai/gpt-x"] == {"model": "openrouter/openai/gpt-x",
                                                  "api_key": "os.environ/STUDIO_KEY_OPENROUTER"}
    assert entries["azure-openai/prod"] == {"model": "azure/prod", "api_key": "os.environ/STUDIO_KEY_AZURE_OPENAI",
                                            "api_base": "https://res.openai.azure.com", "api_version": "2024-10-21"}
    # An address of its own means "speaks OpenAI's chat format", not OpenAI's newer Responses API.
    assert entries["openai/local-model"]["model"] == "custom_openai/local-model"
    assert entries["gemini/gem-flash"]["model"] == "gemini/gem-flash"
    assert rendered["general_settings"] == {"master_key": "os.environ/LITELLM_MASTER_KEY"}
    assert rendered["litellm_settings"]["drop_params"] and rendered["litellm_settings"]["modify_params"]
    keys = gateway.keys_for(connections, {"UPSTREAM": "from-the-environment"}, settings.secret_store())
    assert keys == {"STUDIO_KEY_OPENROUTER": KEY, "STUDIO_KEY_AZURE_OPENAI": OTHER_KEY,
                    "STUDIO_KEY_OPENAI": "from-the-environment", "STUDIO_KEY_GEMINI": "AIza-test-0123456789"}
    # A gateway the person runs is not the studio's business.
    assert gateway.managed([{"id": "litellm", "kind": "anthropic-gateway"}]) == []


def test_every_model_slot_is_mapped_including_a_cheap_one(tmp_path):
    settings = real_settings(tmp_path)
    openrouter(settings)
    connection = load_connections(settings)[0]
    endpoint = gateway.Endpoint("http://127.0.0.1:45000", "sk-studio-master")
    env, scrub = child_env(connection, {"ANTHROPIC_API_KEY": "leak", "OPENAI_API_KEY": "x"}, "openai/gpt-x",
                           settings.secret_store(), endpoint)
    assert env == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:45000", "ANTHROPIC_AUTH_TOKEN": "sk-studio-master",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "openrouter/openai/gpt-x",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "openrouter/openai/gpt-x",
        "ANTHROPIC_DEFAULT_FABLE_MODEL": "openrouter/openai/gpt-x",
        "CLAUDE_CODE_SUBAGENT_MODEL": "openrouter/openai/gpt-x",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "openrouter/openai/gpt-x-mini",
        "ANTHROPIC_SMALL_FAST_MODEL": "openrouter/openai/gpt-x-mini"}
    assert KEY not in json.dumps(env)        # the provider's key stays with LiteLLM; the agent gets the proxy's own
    assert "ANTHROPIC_API_KEY" in scrub and "CLAUDE_CODE_OAUTH_TOKEN" in scrub
    # The escape hatch and the limits are per connection, and off unless set.
    tuned = {**connection, "disable_betas": True, "max_context_tokens": 128000, "max_output_tokens": 16000}
    extra, _ = child_env(tuned, {}, "openai/gpt-x", settings.secret_store(), endpoint)
    assert extra["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert extra["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "128000" and extra["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16000"
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" not in env
    # With no cheaper model named or guessable, background work uses the request's model.
    plain = {**connection, "small_model": None, "models": [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}]}
    alone, _ = child_env(plain, {}, "a", settings.secret_store(), endpoint)
    assert alone["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "openrouter/a"
    with pytest.raises(HarnessError, match="has not been started"):
        child_env(connection, {}, "openai/gpt-x", settings.secret_store())


def test_a_gateway_the_person_runs_is_started_as_before(tmp_path):
    connection = {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway",
                  "base_url": "http://127.0.0.1:4000", "api_key_env": "LITELLM_API_KEY"}
    env, _ = child_env(connection, {"LITELLM_API_KEY": "k"}, "gpt-5.6-luna")
    assert env == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:4000", "ANTHROPIC_AUTH_TOKEN": "k",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                   "ANTHROPIC_SMALL_FAST_MODEL": "gpt-5.6-luna", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "gpt-5.6-luna"}


def test_cheapest_uses_prices_when_given_and_names_otherwise():
    assert gateway.cheapest(["big", "small"], {"big": 5.0, "small": 0.2}) == "small"
    assert gateway.cheapest(["gpt-x", "gpt-x-mini", "gpt-x-nano"]) == "gpt-x-nano"
    assert gateway.cheapest(["alpha", "beta"]) is None and gateway.cheapest([]) is None


# -- the process ----------------------------------------------------------------------------------------
async def test_sidecar_starts_on_need_answers_restarts_when_dead_and_stops(tmp_path):
    settings = real_settings(tmp_path)
    openrouter(settings)
    connections = load_connections(settings)
    side = gateway.Sidecar(settings, command=stand_in(tmp_path), start_wait=20)
    assert side.status(connections)["state"] == "idle" and not side.running()
    try:
        first = await side.ensure(connections)
        port = side.port
        assert first.base_url == f"http://127.0.0.1:{port}" and first.notice is None
        assert first.key.startswith("sk-studio-") and first.key == settings.secret_store().get(gateway.SERVICE, gateway.MASTER)
        assert gateway.answers(port) and side.status(connections)["state"] == "running"
        assert gateway.read_state(side.folder)["port"] == port
        # The config on disk holds no key, and the log has every key the process printed blanked.
        assert KEY not in side.config_path.read_text() and first.key not in side.config_path.read_text()
        await asyncio.sleep(0.2)
        log = side.log_path.read_text()
        assert "stand-in started with [key] [key]" in log and KEY not in log and first.key not in log
        # Asked again with nothing changed: the same process.
        again = await side.ensure(connections)
        assert side.port == port and again.notice is None
        # It dies: said plainly, and the next request starts it again.
        os.kill(side.process.pid, signal.SIGKILL)
        for _ in range(50):
            if not side.running():
                break
            await asyncio.sleep(0.1)
        status = side.status(connections)
        assert status["state"] == "stopped" and "stopped by itself" in status["detail"] and status["last_exit"]
        assert not gateway.answers(port, 0.5) and gateway.read_state(side.folder) is None
        revived = await side.ensure(connections)
        assert "started again" in revived.notice and side.running() and gateway.answers(side.port)
        # A changed connection (another model) means a new config and a restart.
        pid = side.process.pid
        changed = [{**connections[0], "models": [*connections[0]["models"], {"id": "new/one", "label": "new/one"}]}]
        await side.ensure(changed)
        assert side.process.pid != pid and "openrouter/new/one" in side.config_path.read_text()
    finally:
        last = side.port
        await side.stop()
    assert not side.running() and not gateway.answers(last, 0.5) and gateway.read_state(side.folder) is None
    assert side.status(connections)["state"] == "idle"


async def test_stopping_the_studio_stops_its_litellm(tmp_path):
    settings = real_settings(tmp_path, port_range=(23990, 23999))
    openrouter(settings)
    app = create_app(settings)
    studio = app.state.studio
    studio.registry.sidecar.command = stand_in(tmp_path)
    studio.registry.sidecar.start_wait = 20
    await studio.start()
    await studio.registry.sidecar.ensure(studio.registry.connections())
    port = studio.registry.sidecar.port
    assert gateway.answers(port)
    await studio.stop()
    assert not gateway.answers(port, 0.5)


async def test_a_proxy_that_cannot_start_says_why(tmp_path):
    settings = real_settings(tmp_path)
    openrouter(settings)
    broken = tmp_path / "broken.py"
    broken.write_text("import sys; print('no such option'); sys.exit(3)")
    side = gateway.Sidecar(settings, command=lambda c, p: [sys.executable, str(broken)], start_wait=10)
    with pytest.raises(gateway.GatewayError, match="exit code 3"):
        await side.ensure(load_connections(settings))
    assert not side.running()


def test_not_installed_is_said_with_the_command_and_install_checks_hashes(tmp_path):
    settings = real_settings(tmp_path)
    openrouter(settings)
    public = Registry(settings).describe_all()[0]
    assert public["available"] is False and "studio litellm install" in public["reason"]
    ran = []

    def run(command, **_kwargs):
        ran.append(command)
        if "venv" in command:
            gateway.program().parent.mkdir(parents=True)
            gateway.program().write_text("#!/bin/sh\n")

        class Done:
            returncode, stdout, stderr = 0, "", ""
        return Done()

    if gateway.find_uv():
        found = gateway.install(run=run)
        assert found["installed"] and found["version"] == gateway.VERSION
        assert "--require-hashes" in ran[1] and str(gateway.REQUIREMENTS) in ran[1]
        assert Registry(settings).describe_all()[0]["available"] is True
        # A pin that changed since asks for a reinstall instead of running the old one.
        record = gateway.home() / "installed.json"
        record.write_text(json.dumps({"version": "0.0.1", "requirements_sha256": "other"}))
        assert not gateway.installed()["installed"]


def test_the_committed_list_pins_one_version_and_every_package_has_a_hash():
    text = gateway.REQUIREMENTS.read_text()
    assert f"\nlitellm=={gateway.VERSION} \\\n" in text
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    packages = [block for block in re.split(r"\n(?=[A-Za-z0-9])", body) if block.strip()]
    assert len(packages) > 50
    for block in packages:
        assert re.match(r"[A-Za-z0-9_.\[\]-]+==[^\s]+ ", block), block[:60]
        assert "--hash=sha256:" in block, block[:60]
    assert gateway.requirements_hash()


def test_what_litellm_priced_a_turn_at(tmp_path):
    settings = real_settings(tmp_path)
    side = gateway.Sidecar(settings)
    side.folder.mkdir(parents=True)
    rows = [{"turn": "t_1", "cost": 0.004, "model": "a"}, {"turn": "t_1", "cost": 0.0015}, {"turn": "t_2", "cost": 9},
            {"turn": "t_3", "cost": 0.0}, {"turn": "t_3", "cost": None}]
    side.spend_path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot json\n")
    assert side.spend("t_1") == {"cost_usd": 0.0055, "requests": 2, "unpriced": 0, "basis": "calculated", "backup": None}
    # no price reported is not "free": no figure, and the requests are counted as unpriced
    assert side.spend("t_3") == {"cost_usd": None, "requests": 2, "unpriced": 2, "basis": None, "backup": None}
    side.spend_path.write_text(json.dumps({"turn": "t_r", "cost": 0.2, "reported": True}) + "\n"
                               + json.dumps({"turn": "t_m", "cost": 0.2, "reported": True}) + "\n"
                               + json.dumps({"turn": "t_m", "cost": 0.1}) + "\n")
    assert side.spend("t_r")["basis"] == "reported"       # the provider sent the cost itself
    assert side.spend("t_m")["basis"] == "calculated"     # one request worked out from a price list: say so
    assert side.spend("t_none") is None


# -- a provider's list of models -----------------------------------------------------------------------
async def test_model_lists_are_read_from_the_provider_or_said_to_be_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json={"data": [
                {"id": "openai/gpt-x", "pricing": {"prompt": "0.000005"}},
                {"id": "meta/llama:free", "pricing": {"prompt": "0"}},
                {"id": "openai/text-embedding-x", "pricing": {"prompt": "0.0000001"}}]})
        if request.url.host == "generativelanguage.googleapis.com":
            assert request.headers["x-goog-api-key"] == "g-key" and "key=" not in str(request.url)
            return httpx.Response(200, json={"models": [
                {"name": "models/gem-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gem-embed", "supportedGenerationMethods": ["embedContent"]}]})
        if request.headers.get("authorization") != "Bearer good":
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"data": [{"id": "gpt-y"}, {"id": "whisper-1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        listed = await gateway.list_models("openrouter", None, client=client)
        assert listed == [{"id": "meta/llama:free", "price": 0.0}, {"id": "openai/gpt-x", "price": 5.0}]
        assert await gateway.list_models("gemini", "g-key", client=client) == [{"id": "gem-pro"}]
        assert await gateway.list_models("openai", "good", client=client) == [{"id": "gpt-y"}]
        assert await gateway.list_models("openai", "good", "http://127.0.0.1:9/v1", client=client) == [{"id": "gpt-y"}]
        with pytest.raises(gateway.GatewayError, match="refused the key"):
            await gateway.list_models("openai", "bad", client=client)
        with pytest.raises(gateway.GatewayError, match="Type their names"):
            await gateway.list_models("azure-openai", "k", client=client)
        with pytest.raises(gateway.GatewayError, match="Enter the key first"):
            await gateway.list_models("gemini", None, client=client)


# -- labels ----------------------------------------------------------------------------------------------
def test_models_that_are_not_claude_are_marked_experimental(tmp_path):
    gate = config._normalise({"id": "litellm", "kind": "anthropic-gateway", "base_url": "http://x",
                              "models": ["openrouter/openai/gpt-6.1-sol", "openrouter/anthropic/claude-opus-5.5"]})
    public = public_connection(gate, True, None)
    assert public["experimental"] is True
    assert [m.get("experimental", False) for m in public["models"]] == [True, False]
    plan = public_connection(config._normalise(config.DEFAULT_CONNECTIONS[0]), True, None)
    assert "experimental" not in plan and not any("experimental" in m for m in plan["models"])
    managed = public_connection(config._normalise({"id": "openai", "kind": "litellm", "provider": "openai",
                                                   "models": ["gpt-y"]}), True, None)
    assert managed["experimental"] is True and "base_url" not in managed and "api_key_secret" not in managed


def test_what_a_check_may_cost_is_said_before_it_runs():
    plan = {"id": "subscription", "kind": "claude-subscription", "label": "Claude plan"}
    gate = {"id": "litellm", "kind": "anthropic-gateway", "label": "LiteLLM"}
    openai = {"id": "openai", "kind": "litellm", "provider": "openai", "label": "OpenAI key"}
    assert config.model_cost(plan, "opus") == "plan" and "allowance" in modelcheck.cost_warning(plan, "opus")
    assert config.model_cost(gate, "gpt-5.6-luna") == "unknown" and "cannot tell" in modelcheck.cost_warning(gate, "gpt-5.6-luna")
    assert config.model_cost(gate, "openrouter/openai/gpt-6.1-sol") == "paid"
    assert config.model_cost(gate, "openrouter/free") == "free" and modelcheck.cost_warning(gate, "openrouter/free") is None
    assert config.model_cost(openai, "gpt-y") == "paid" and "billed per use" in modelcheck.cost_warning(openai, "gpt-y")
    assert config.model_cost({"kind": "fake"}, "fake") == "free"


# -- the check ----------------------------------------------------------------------------------------------
async def test_check_runs_on_a_scratch_project_and_the_result_is_kept(client, studio):
    before = (await client.get("/api/models")).json()
    assert before["running"] is None and before["connections"][0]["models"][0]["check"] is None
    assert len(before["parts"]) == 5 and before["note"]["headline"].startswith("Claude models work best")
    assert before["litellm"]["state"] in ("not_installed", "idle")
    started = await client.post("/api/models/check", json={"connection": "fake", "model": "fake"})
    assert started.status_code == 202, started.text
    busy = await client.post("/api/models/check", json={"connection": "fake", "model": "fake"})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "check_running"
    assert (await client.get("/api/projects")).json() == []        # the scratch project is not one of the person's
    await studio.checks.wait()
    after = (await client.get("/api/models")).json()
    result = after["connections"][0]["models"][0]["check"]
    assert after["running"] is None
    assert result["status"] == "passed" and result["reason"] is None, result
    assert [part["ok"] for part in result["parts"]] == [True] * 5
    assert result["steps"] == 8 and len(result["messages"]) == 2 and result["seconds"] >= 0
    assert [m["status"] for m in result["messages"]] == ["completed", "completed"]
    assert result["trace"][0] == {"message": 1, "name": "Read", "title": "Read index.html", "status": "ok"}
    assert result["trace"][-1]["message"] == 2
    # Kept on disk; the scratch project and its trash are gone.
    stored = json.loads((studio.settings.data_dir / "model-checks.json").read_text())
    assert stored["results"]["fake::fake"]["status"] == "passed"
    assert studio.store.list() == [] and not list((studio.store.root / ".trash").glob("*"))
    again = modelcheck.ModelChecks(studio)
    assert again.result_for(studio.registry.connections()[0], "fake")["status"] == "passed"
    # A connection that was pointed somewhere else under the same id does not keep the result.
    assert again.result_for({**studio.registry.connections()[0], "base_url": "http://elsewhere"}, "fake") is None
    for bad, code in (({"connection": "nope", "model": "fake"}, "unknown_connection"),
                      ({"connection": "fake", "model": "nope"}, "unknown_model")):
        refused = await client.post("/api/models/check", json=bad)
        assert refused.status_code == 404 and refused.json()["error"]["code"] == code


async def test_check_asks_before_a_model_that_costs_money(tmp_path, monkeypatch):
    settings = real_settings(tmp_path, port_range=(23980, 23989))
    run_setup(settings, {"account": {"account": "gateway", "api_key": "gateway-key-123", "models": ["gpt-z"],
                                     "base_url": "http://127.0.0.1:9"}})
    app = create_app(settings)
    studio = app.state.studio
    monkeypatch.setattr("studio2_server.harness.claude.find_cli", lambda: "/usr/bin/true")
    with pytest.raises(modelcheck.CheckError) as refused:
        studio.checks.start("gateway", "gpt-z")
    assert refused.value.code == "cost_not_confirmed" and "cannot tell" in refused.value.message
    assert studio.checks.running is None and studio.store.list() == []


def _events(*rows):
    return [{"seq": i, "turn_id": turn, "type": kind, **fields} for i, (turn, kind, fields) in enumerate(rows, 1)]


def _call(turn, tool_id, name, path=None):
    return (turn, "tool_call", {"tool_id": tool_id, "name": name, "title": name, "parent_tool_id": None,
                                "input": {"file_path": path} if path else {}})


def _done(turn, tool_id, status="ok"):
    return (turn, "tool_result", {"tool_id": tool_id, "status": status})


def _workspace(tmp_path, total="42\nquince-417\n", **files):
    for name, content in {**modelcheck.EXPECTED, **files}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    if total is not None:
        (tmp_path / "total.txt").write_text(total)
    return tmp_path


GOOD = [_call("t1", "a", "Read", "index.html"), _call("t1", "b", "Read", "styles.css"), _done("t1", "a"), _done("t1", "b"),
        _call("t1", "c", "Edit", "/w/index.html"), _done("t1", "c"), _call("t1", "d", "Edit", "styles.css"), _done("t1", "d"),
        _call("t1", "e", "mcp__studio__query_files"), _done("t1", "e"), _call("t1", "f", "Write", "total.txt"), _done("t1", "f"),
        _call("t2", "g", "Edit", "total.txt"), _done("t2", "g")]
TURNS = [{"turn_id": "t1", "status": "completed", "error": None}, {"turn_id": "t2", "status": "completed", "error": None}]


def _failed(parts):
    return {part["id"]: part["detail"] for part in parts if not part["ok"]}


def test_verify_passes_what_was_asked_and_names_each_thing_that_was_not(tmp_path):
    assert _failed(modelcheck.verify(_workspace(tmp_path / "ok"), _events(*GOOD), TURNS)) == {}
    # One tool at a time.
    serial = [GOOD[0], GOOD[2], GOOD[1], GOOD[3], *GOOD[4:]]
    assert list(_failed(modelcheck.verify(_workspace(tmp_path / "serial"), _events(*serial), TURNS))) == ["parallel"]
    # The right content, but a file written again whole.
    rewrite = [row if row[2].get("tool_id") != "d" or row[1] != "tool_call" else _call("t1", "d", "Write", "styles.css")
               for row in GOOD]
    failed = _failed(modelcheck.verify(_workspace(tmp_path / "rewrite"), _events(*rewrite), TURNS))
    assert list(failed) == ["in_place"] and "styles.css" in failed["in_place"] and "whole" in failed["in_place"]
    # A file that is not what was asked for.
    mangled = _workspace(tmp_path / "mangled", **{"index.html": modelcheck.EXPECTED["index.html"].replace("North", "N")})
    assert "index.html" in _failed(modelcheck.verify(mangled, _events(*GOOD), TURNS))["in_place"]
    untouched = _workspace(tmp_path / "untouched", **{"styles.css": modelcheck.SEED["styles.css"]})
    assert "not changed" in _failed(modelcheck.verify(untouched, _events(*GOOD), TURNS))["in_place"]
    # The studio tool was not called, or failed, or its number was not used.
    no_tool = [row for row in GOOD if row[2].get("tool_id") != "e"]
    assert "never called" in _failed(modelcheck.verify(_workspace(tmp_path / "a"), _events(*no_tool), TURNS))["studio_tool"]
    errored = [row if row != _done("t1", "e") else _done("t1", "e", "error") for row in GOOD]
    assert "failed" in _failed(modelcheck.verify(_workspace(tmp_path / "b"), _events(*errored), TURNS))["studio_tool"]
    assert "42" in _failed(modelcheck.verify(_workspace(tmp_path / "c", total="41\nquince-417\n"), _events(*GOOD), TURNS))["studio_tool"]
    # The second message did not know the first.
    lost = _failed(modelcheck.verify(_workspace(tmp_path / "d", total="42\nI do not know the code word\n"), _events(*GOOD), TURNS))
    assert list(lost) == ["continued"] and "did not carry the conversation" in lost["continued"]
    # The first message failed: the reason is the provider's own words.
    broke = [{"turn_id": "t1", "status": "failed", "error": "HTTP 400: tools are not supported by this model"}]
    failed = _failed(modelcheck.verify(_workspace(tmp_path / "e", total=None, **modelcheck.SEED), [], broke))
    assert "tools are not supported" in failed["finished"] and set(failed) == {p for p, _l in modelcheck.PARTS}
    assert {failed[part] for part in failed if part != "finished"} == {"Not reached."}       # one reason, not five


# -- the command line and the doctor ---------------------------------------------------------------------------
def test_setup_in_the_terminal_asks_for_models_background_model_and_the_install(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path)
    monkeypatch.setattr(cli, "fetch_models", lambda provider, key, base: (
        [{"id": "openai/gpt-x", "price": 5.0}, {"id": "openai/gpt-x-mini", "price": 0.2}], None))
    asked: list[str] = []
    typed = iter(["7",                                  # 1. OpenRouter key
                  "openai/gpt-x, openai/gpt-x-mini",    #    models
                  "",                                   #    default model: the first
                  "",                                   #    background model: the cheapest by price
                  "n",                                  #    install LiteLLM now? no
                  "1", "",                              # 2. this computer only, port as it is
                  "1"])                                 # 3. just me

    def ask(prompt):
        asked.append(prompt)
        return next(typed)

    answers = cli.interactive_answers(settings, ask=ask, secret=lambda _prompt: KEY,
                                      login=lambda: {"found": False, "plan": None, "detail": "No sign-in."},
                                      tailscale=NO_TAILSCALE)
    assert answers["account"] == {"account": "openrouter", "api_key": KEY, "small_model": "openai/gpt-x-mini",
                                  "models": ["openai/gpt-x", "openai/gpt-x-mini"]}
    out = capsys.readouterr().out
    assert KEY not in out and "Claude models work best with the studio; other models are experimental." in out
    assert "web search" in out and "lists 2 models" in out and "studio litellm install" in out
    assert any("Install it now?" in prompt for prompt in asked)


def test_setup_from_flags_and_the_litellm_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setenv("STUDIO2_SECRETS", "file")
    monkeypatch.setenv("STUDIO2_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("STUDIO2_FAKE", raising=False)
    key_file = tmp_path / "key"
    key_file.write_text(KEY + "\n")
    assert cli.main(["setup", "--account", "gemini", "--key-file", str(key_file), "--models", "gem-pro,gem-flash",
                     "--small-model", "gem-flash", "--local", "--single"]) == 0
    written = json.loads((tmp_path / "cfg" / "config.json").read_text())
    assert written["connections"][0]["provider"] == "gemini" and written["connections"][0]["small_model"] == "gem-flash"
    assert KEY not in (tmp_path / "cfg" / "config.json").read_text() + capsys.readouterr().out
    assert cli.main(["litellm"]) == 0
    out = capsys.readouterr().out
    assert "not installed" in out and "gemini" in out
    assert cli.main(["litellm", "install"]) == 2       # no terminal and no --yes: nothing is fetched
    assert "Nothing was installed" in capsys.readouterr().out and not gateway.program().exists()


def test_doctor_reports_the_studios_litellm(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path, port=65000)
    openrouter(settings)
    report = doctor.collect(settings, login=lambda: {"found": False, "plan": None, "detail": "x"},
                            tailscale=NO_TAILSCALE)
    lite = [item for item in report["items"] if item["area"].startswith("Other models")]
    assert lite[0]["status"] == doctor.FAIL and "openrouter" in lite[0]["detail"] and "litellm install" in lite[0]["fix"]
    account = next(item for item in report["items"] if item["area"] == "Model accounts" and "OpenRouter" in item["name"])
    assert account["status"] == doctor.FAIL and "studio litellm install" in account["detail"]
    assert KEY not in json.dumps(report)


def test_a_gateway_gets_a_cheap_model_for_background_work_and_picking(tmp_path):
    """Setup suggests the cheapest-looking listed model for a gateway the person runs, as it does for
    the studio's own LiteLLM, and says so; a named one is kept; with nothing cheap-looking none is set."""
    from studio2_server.picker import picking_model

    def gateway_with(models, **more):
        settings = real_settings(tmp_path / str(len(models)) / str(len(more)))
        result = run_setup(settings, {"account": {"account": "gateway", "api_key": KEY, "models": models,
                                                  "base_url": "http://127.0.0.1:9", **more}})
        return json.loads(settings.config_path.read_text())["connections"][-1], result.changes

    made, changes = gateway_with(["gpt-big", "gpt-big-mini"])
    assert made["kind"] == "anthropic-gateway" and made["small_model"] == "gpt-big-mini"
    assert picking_model(made) == "gpt-big-mini"
    assert any("picking skills" in change and "gpt-big-mini" in change for change in changes)
    named, _changes = gateway_with(["gpt-big", "gpt-big-mini"], small_model="gpt-big")
    assert named["small_model"] == "gpt-big"
    plain, changes = gateway_with(["gpt-big"])
    assert "small_model" not in plain and picking_model(plain) is None
    assert any("picked by word search" in change for change in changes)

