"""Step 3m: setup answers, where keys are kept, the shared rule, the `studio` command, doctor.

Nothing here touches the real keychain or the person's config file: every store is a file or
memory store under tmp_path, and every Settings names a config file there.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys

import httpx
import pytest

from studio2_server import access, cli, doctor, keystore, setup
from studio2_server.api import create_app
from studio2_server.attach import NODE_HOMES
from studio2_server.config import STUDIO2_ROOT, Settings, load_connections, model_gateway
from studio2_server.harness import Registry
from studio2_server.harness.claude import HarnessError, child_env

KEY = "sk-ant-test-0123456789abcdef"
ACCESS_KEY = "an-access-key-of-24-chars-or-more"
NO_TAILSCALE = lambda: {"found": False, "address": None, "detail": "none"}     # noqa: E731
TAILSCALE = lambda: {"found": True, "address": "100.101.102.103", "detail": "x"}  # noqa: E731


def real_settings(tmp_path, **extra) -> Settings:
    """A studio that is not the fake one, with its config and its secrets under tmp_path."""
    settings = Settings(data_dir=tmp_path / "data", config_path=tmp_path / "cfg" / "config.json",
                        web_dist=tmp_path / "no-dist", home=tmp_path / "home", **extra)
    settings._secrets = keystore.FileStore(tmp_path / "cfg" / "secrets.json")
    return settings


def run_setup(settings, answers, **kwargs):
    result = setup.plan(answers, settings, tailscale=kwargs.pop("tailscale", NO_TAILSCALE), **kwargs)
    setup.apply(result, settings)
    return result


# -- answers → config ---------------------------------------------------------------------------
def test_api_key_goes_to_the_store_and_the_config_only_names_it(tmp_path):
    settings = real_settings(tmp_path)
    result = run_setup(settings, {"account": {"account": "api-key", "api_key": KEY, "models": ["sonnet", "haiku"]},
                                  "where": {"network": False, "port": 8400}, "who": {"shared": False}})
    text = settings.config_path.read_text()
    config = json.loads(text)
    assert KEY not in text and KEY not in "\n".join(result.changes) and KEY not in result.diff(settings.config_path)
    assert config["connections"] == [{"id": "api", "kind": "anthropic-api", "label": "Anthropic API key",
                                      "models": ["sonnet", "haiku"], "default_model": "sonnet",
                                      "api_key_secret": "api"}]
    assert config["run"] == {"port": 8400, "hosts": []} and config["shared"] is False
    assert config["setup"]["version"] == 1
    assert settings.secret_store().get(keystore.MODEL_KEYS, "api") == KEY
    # The harness finds the key where setup put it, and the agent gets it under Anthropic's name.
    env, _scrub = child_env(load_connections(settings)[0], {}, secrets=settings.secret_store())
    assert env["ANTHROPIC_API_KEY"] == KEY
    assert Registry(settings).describe_all()[0]["available"] is True


def test_secret_file_is_owner_only_and_fixed_when_loosened(tmp_path):
    store = keystore.FileStore(tmp_path / "cfg" / "secrets.json")
    store.set(keystore.MODEL_KEYS, "api", KEY)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    os.chmod(store.path, 0o644)
    assert store.get(keystore.MODEL_KEYS, "api") == KEY
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    store.delete(keystore.MODEL_KEYS, "api")
    assert store.get(keystore.MODEL_KEYS, "api") is None


def test_rerun_changes_one_thing_and_keeps_the_rest(tmp_path):
    settings = real_settings(tmp_path)
    run_setup(settings, {"account": {"account": "api-key", "api_key": KEY}})
    before = json.loads(settings.config_path.read_text())
    result = run_setup(settings, {"where": {"port": 8600}})
    after = json.loads(settings.config_path.read_text())
    assert after["connections"] == before["connections"] and after["run"]["port"] == 8600
    assert result.exists and result.restart_needed and "8600" in result.diff(settings.config_path)
    assert json.loads((settings.config_path.parent / "config.json.bak").read_text()) == before
    # Changing the models of an account that has a key does not ask for the key again.
    run_setup(settings, {"account": {"account": "api-key", "models": "opus", "default_model": "opus"}})
    assert json.loads(settings.config_path.read_text())["connections"][0]["api_key_secret"] == "api"
    assert setup.configured(settings)["connections"][0]["key_from"] == "file"


def test_hand_written_config_is_extended_not_replaced(tmp_path):
    settings = real_settings(tmp_path)
    settings.config_path.parent.mkdir(parents=True)
    hand = {"connections": [
        {"id": "subscription", "kind": "claude-subscription", "models": ["opus", "sonnet"]},
        {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:4000",
         "api_key_env": "LITELLM_API_KEY", "models": ["gpt-sol"]}], "something_else": 1}
    settings.config_path.write_text(json.dumps(hand))
    run_setup(settings, {"account": {"account": "api-key", "api_key": KEY}})
    config = json.loads(settings.config_path.read_text())
    assert [c["id"] for c in config["connections"]] == ["api", "subscription", "litellm"]
    assert config["connections"][2] == hand["connections"][1] and config["something_else"] == 1


@pytest.mark.parametrize("answers, words", [
    ({"account": {"account": "openai", "api_key": KEY}}, "at least one model"),
    ({"account": {"account": "azure-openai", "api_key": KEY, "models": ["prod"]}}, "address of your resource"),
    ({"account": {"account": "api-key"}}, "needs its key"),
    ({"account": {"account": "gateway", "api_key": KEY, "base_url": "http://127.0.0.1:4000"}}, "at least one model"),
    ({"account": {"account": "vertex", "region": "us-east5"}}, "project"),
    ({"account": {"account": "plan"}, "who": {"shared": True}}, "shared"),
    ({"who": {"shared": True}}, "cannot use the Claude plan"),
    ({"where": {"network": True, "host": "8.8.8.8"}}, "public address"),
    ({"where": {"network": True, "host": "0.0.0.0"}}, "one private address"),
    ({"where": {"network": True}}, "Give the private address"),
    ({"where": {"network": True, "host": "192.168.1.20", "open_tailnet": True}}, "Tailscale address"),
    ({"where": {"port": 80}}, "between 1024"),
    ({"who": {"access_key": "short"}, "where": {"network": True, "host": "100.101.102.103"}}, "at least 24"),
])
def test_answers_that_cannot_be_used_are_refused_and_nothing_is_written(tmp_path, answers, words):
    settings = real_settings(tmp_path)
    with pytest.raises(setup.SetupError, match=words):
        setup.plan(answers, settings, tailscale=NO_TAILSCALE)
    assert not settings.config_path.exists() and not (tmp_path / "cfg" / "secrets.json").exists()


def test_network_address_gets_an_access_key_and_tailnet_can_go_without(tmp_path):
    settings = real_settings(tmp_path)
    result = run_setup(settings, {"where": {"network": True, "host": "tailscale"}}, tailscale=TAILSCALE)
    config = json.loads(settings.config_path.read_text())
    assert config["run"]["hosts"] == ["100.101.102.103"] and config["access_key_secret"] == "access-key"
    made = result.generated_access_key
    assert made and len(made) >= access.MIN_KEY and made not in settings.config_path.read_text()
    assert settings.secret_store().get(keystore.ACCESS, "access-key") == made
    assert any("run commands on this computer" in change for change in result.changes)
    again = run_setup(settings, {"where": {"open_tailnet": True}}, tailscale=TAILSCALE)
    assert again.generated_access_key is None
    assert json.loads(settings.config_path.read_text())["run"]["open_tailnet"] is True


# -- the settings a start reads -----------------------------------------------------------------
def test_from_env_reads_what_setup_wrote_and_the_environment_wins(tmp_path, monkeypatch):
    settings = real_settings(tmp_path)
    run_setup(settings, {"account": {"account": "api-key", "api_key": KEY},
                         "where": {"network": True, "host": "100.101.102.103", "port": 8400},
                         "who": {"shared": True, "access_key": ACCESS_KEY}})
    for name in ("STUDIO2_HOSTS", "STUDIO2_ACCESS_KEY", "STUDIO2_OPEN_TAILNET", "STUDIO2_SHARED",
                 "STUDIO2_PORT_RANGE", "STUDIO2_FAKE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STUDIO2_CONFIG", str(settings.config_path))
    monkeypatch.setenv("STUDIO2_SECRETS", "file")
    started = Settings.from_env()
    assert (started.port, started.port_range, started.hosts) == (8400, (8401, 8499), ("100.101.102.103",))
    assert started.shared and started.access_key == ACCESS_KEY and not started.open_tailnet
    assert Settings.from_env(port=8450).port_range == (8201, 8299)      # --port alone: as before
    monkeypatch.setenv("STUDIO2_HOSTS", "")
    monkeypatch.setenv("STUDIO2_SHARED", "0")
    override = Settings.from_env()
    assert override.hosts == () and override.shared is False


def test_owner_configuration_without_setup_behaves_as_before(tmp_path, monkeypatch):
    """No setup keys in the file, the start script's environment: the same settings, the same
    connection list and the same access rules as before step 3m."""
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"connections": [
        {"id": "subscription", "kind": "claude-subscription", "models": ["opus", "sonnet"]},
        {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:4000",
         "api_key_env": "LITELLM_API_KEY", "models": ["claude-opus", "gpt-sol"]}]}))
    for name, value in {"STUDIO2_CONFIG": str(config), "STUDIO2_HOSTS": "100.101.102.103", "STUDIO2_OPEN_TAILNET": "1",
                        "STUDIO2_ACCESS_KEY": ACCESS_KEY, "LITELLM_API_KEY": "gateway-key",
                        "STUDIO2_SECRETS": "file"}.items():
        monkeypatch.setenv(name, value)
    for name in ("STUDIO2_FAKE", "STUDIO2_SHARED", "STUDIO2_PORT_RANGE", "STUDIO2_TLS_CERT", "STUDIO2_TLS_KEY"):
        monkeypatch.delenv(name, raising=False)
    from studio2_server.harness.claude import ClaudeHarness     # signed in; never runs the real `claude`
    monkeypatch.setattr(ClaudeHarness, "login_detector", staticmethod(lambda: {"found": True}))
    settings = Settings.from_env()
    assert (settings.port, settings.port_range, settings.hosts) == (8200, (8201, 8299), ("100.101.102.103",))
    assert settings.open_tailnet and not settings.shared and settings.access_key == ACCESS_KEY
    access.check_settings(settings.hosts, settings.access_key, settings.open_tailnet, settings.shared)
    listed = Registry(settings).describe_all()
    assert [(c["id"], c["available"], c["reason"]) for c in listed] == [
        ("subscription", True, None), ("litellm", True, None)]
    assert model_gateway(settings) == {"base_url": "http://127.0.0.1:4000", "key": "gateway-key"}
    assert not (tmp_path / "secrets.json").exists()        # nothing was looked up or written
    # No file at all: the one subscription connection, as ever.
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "absent.json"))
    assert [c["id"] for c in Registry(Settings.from_env()).describe_all()] == ["subscription"]
    # The same environment-variable message as before when the variable is not set.
    with pytest.raises(HarnessError, match="environment variable LITELLM_API_KEY"):
        child_env({"id": "litellm", "kind": "anthropic-gateway", "base_url": "http://x", "api_key_env": "LITELLM_API_KEY"}, {})


# -- shared -------------------------------------------------------------------------------------
async def test_shared_switches_the_plan_off_and_requires_the_key(tmp_path):
    settings = real_settings(tmp_path, shared=True)
    settings.config_path.parent.mkdir(parents=True)
    settings.config_path.write_text(json.dumps({"shared": True, "connections": [
        {"id": "subscription", "kind": "claude-subscription", "models": ["opus"]},
        {"id": "api", "kind": "anthropic-api", "api_key_env": "SOME_KEY", "models": ["sonnet"]}]}))
    plan_row = Registry(settings).describe_all()[0]
    assert plan_row["available"] is False and "shared with other people" in plan_row["reason"]
    # A turn on it fails with that reason before any program is started.
    from studio2_server.harness import TurnRequest
    harness = Registry(settings).harness("claude-subscription")
    request = TurnRequest("p_1", "t_1", tmp_path, "hi", None, load_connections(settings)[0], "opus")
    result = await harness.run(request, lambda event: None)
    assert result.status == "failed" and "shared with other people" in result.error
    # Starting: the key is required on every address, and "no key on the tailnet" is refused.
    with pytest.raises(SystemExit, match="needs an access key"):
        access.check_settings((), None, False, shared=True)
    with pytest.raises(SystemExit, match="cannot be used"):
        access.check_settings(("100.101.102.103",), ACCESS_KEY, True, shared=True)
    access.check_settings(("100.101.102.103",), ACCESS_KEY, False, shared=True)
    access.check_settings((), None, False, shared=False)    # single owner on this computer: as before


async def test_setup_api_never_returns_a_key_and_is_local_in_a_shared_studio(tmp_path):
    settings = real_settings(tmp_path, fake=True, ping_seconds=0.2)   # fake: no program is started to detect a login
    app = create_app(settings)
    await app.state.studio.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8200") as http:
            first = (await http.get("/api/setup")).json()
            assert first["config_exists"] is False and first["secrets"]["backend"] == "file"
            assert [a["account"] for a in first["accounts"] if a["needs_litellm"]] == [
                "openai", "openrouter", "azure-openai", "gemini"]
            assert not any(a.get("coming") for a in first["accounts"])
            answers = {"account": {"account": "api-key", "api_key": KEY},
                       "where": {"network": True, "host": "100.101.102.103", "port": 8200},
                       "who": {"shared": True, "access_key": ACCESS_KEY}}
            dry = await http.post("/api/setup", json={"answers": answers, "dry_run": True})
            assert dry.status_code == 200 and dry.json()["applied"] is False and not settings.config_path.exists()
            saved = await http.post("/api/setup", json={"answers": answers})
            body = saved.json()
            assert saved.status_code == 200 and body["applied"] and body["restart_needed"] is True
            after = await http.get("/api/setup")
            health = await http.get("/api/health")
            for text in (dry.text, saved.text, after.text, health.text, settings.config_path.read_text()):
                assert KEY not in text and ACCESS_KEY not in text
            assert after.json()["current"]["connections"][0]["key_from"] == "file"
            assert after.json()["current"]["access_key_set"] is True and health.json()["shared"] is True
            bad = await http.post("/api/setup", json={"answers": {"where": {"port": 1}}})
            assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_setup"
        # Shared now: another device, even signed in, cannot open setup.
        assert settings.shared is True
        remote = httpx.ASGITransport(app=app, client=("100.99.1.2", 5000))
        async with httpx.AsyncClient(transport=remote, base_url="http://100.101.102.103:8200") as http:
            refused = await http.get("/api/setup")
            assert refused.status_code == 403
    finally:
        await app.state.studio.stop()


def test_first_run_screen_opens_only_when_nothing_is_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path)
    assert setup.needed(settings, has_projects=False) is True
    assert setup.needed(settings, has_projects=True) is False           # a studio already in use
    assert setup.needed(real_settings(tmp_path, fake=True), False) is False
    monkeypatch.setenv("STUDIO2_HOSTS", "100.101.102.103")                  # started by the owner's script
    assert setup.needed(settings, False) is False
    monkeypatch.delenv("STUDIO2_HOSTS")
    run_setup(settings, {"skip": True})
    assert setup.needed(settings, False) is False
    assert "skipped_at" in json.loads(settings.config_path.read_text())["setup"]
    assert [c["id"] for c in load_connections(settings)] == ["subscription"]


# -- where keys are kept ------------------------------------------------------------------------
def test_no_keychain_means_the_owner_only_file(tmp_path, monkeypatch):
    import keyring
    from keyring.backends import fail
    monkeypatch.delenv("STUDIO2_SECRETS", raising=False)
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    store = keystore.open_store(tmp_path)
    assert isinstance(store, keystore.FileStore) and store.backend == "file"
    assert keystore.open_store(tmp_path, fake=True).backend == "memory"


def test_keychain_is_used_when_there_is_one_and_the_file_when_it_refuses(tmp_path, monkeypatch):
    import keyring
    import keyring.backend
    import keyring.errors

    class Fake(keyring.backend.KeyringBackend):
        priority = 1
        held: dict = {}
        broken = False

        def get_password(self, service, username):
            if self.broken:
                raise keyring.errors.KeyringLocked("locked")
            return self.held.get((service, username))

        def set_password(self, service, username, password):
            if self.broken:
                raise keyring.errors.PasswordSetError("no session")
            self.held[(service, username)] = password

        def delete_password(self, service, username):
            self.held.pop((service, username), None)

    backend = Fake()
    monkeypatch.delenv("STUDIO2_SECRETS", raising=False)
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    store = keystore.open_store(tmp_path)
    assert store.backend == "keychain"
    store.set(keystore.MODEL_KEYS, "api", KEY)
    assert backend.held == {(keystore.MODEL_KEYS, "api"): KEY} and not (tmp_path / "secrets.json").exists()
    backend.broken = True
    store.set(keystore.MODEL_KEYS, "gateway", "second-key")
    assert store.get(keystore.MODEL_KEYS, "gateway") == "second-key"
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600


# -- the studio command -------------------------------------------------------------------------
def test_start_takes_the_configured_port_or_the_next_free_block():
    taken = {8200, 8401}
    free = lambda port, hosts=(): port not in taken and port + 1 not in taken   # noqa: E731
    assert cli.choose_port(8200, free=free) == 8600      # 8200 is taken, 8400's first page port is taken
    assert cli.choose_port(8800, free=free) == 8800
    assert cli.choose_port(65400, free=free) is None
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli.port_free(port) is False and cli.studio_at(port, timeout=0.5) is None


def test_setup_from_an_answers_file_and_flags(tmp_path, monkeypatch, capsys):
    config = tmp_path / "cfg" / "config.json"
    monkeypatch.setenv("STUDIO2_CONFIG", str(config))
    monkeypatch.setenv("STUDIO2_SECRETS", "file")
    monkeypatch.setenv("STUDIO2_DATA", str(tmp_path / "data"))
    for name in ("STUDIO2_FAKE", "STUDIO2_HOSTS", "STUDIO2_SHARED", "STUDIO2_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"account": {"account": "api-key", "api_key": KEY},
                                   "where": {"network": False, "port": 8450}, "who": {"shared": False}}))
    assert cli.main(["setup", "--answers", str(answers)]) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "Wrote" in out and KEY not in config.read_text()
    assert json.loads((tmp_path / "cfg" / "secrets.json").read_text()) == {keystore.MODEL_KEYS: {"api": KEY}}
    # An existing file is shown, not overwritten, until --yes.
    assert cli.main(["setup", "--port", "8650"]) == 2
    assert "would change" in capsys.readouterr().out and json.loads(config.read_text())["run"]["port"] == 8450
    assert cli.main(["setup", "--port", "8650", "--dry-run"]) == 0
    assert cli.main(["setup", "--port", "8650", "--yes"]) == 0
    assert json.loads(config.read_text())["run"]["port"] == 8650
    assert cli.main(["setup", "--account", "plan", "--shared", "--yes"]) == 2
    assert "shared with other people" in capsys.readouterr().out
    key_file = tmp_path / "gateway.key"
    key_file.write_text("gateway-secret-key\n")
    assert cli.main(["setup", "--account", "gateway", "--base-url", "http://127.0.0.1:4000", "--key-file",
                     str(key_file), "--models", "claude-opus,gpt-sol", "--yes"]) == 0
    first = json.loads(config.read_text())["connections"][0]
    assert first["models"] == ["claude-opus", "gpt-sol"] and first["api_key_secret"] == "gateway"
    assert "gateway-secret-key" not in config.read_text() + capsys.readouterr().out


def test_setup_asked_in_the_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path)
    typed = iter(["2",          # 1. Anthropic API key
                  "", "",       #    models, default model: as offered
                  "1", "",      # 2. this computer only, port as it is
                  "2"])         # 3. shared
    answers = cli.interactive_answers(
        settings, ask=lambda _prompt: next(typed), secret=lambda _prompt: KEY,
        login=lambda: {"found": True, "plan": "max", "detail": "Signed in."}, tailscale=NO_TAILSCALE)
    assert answers == {"account": {"account": "api-key", "api_key": KEY}, "where": {"network": False},
                       "who": {"shared": True}}
    assert KEY not in capsys.readouterr().out
    result = run_setup(settings, answers)
    assert result.generated_access_key and json.loads(settings.config_path.read_text())["shared"] is True


def test_doctor_says_what_is_there_and_what_is_not(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    settings = real_settings(tmp_path, port=65000)
    run_setup(settings, {"account": {"account": "api-key", "api_key": KEY}})
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    report = doctor.collect(settings, login=lambda: {"found": False, "plan": None, "detail": "none"},
                            tailscale=NO_TAILSCALE)
    by_name = {item["name"]: item for item in report["items"]}
    text = doctor.render(report)
    assert KEY not in text + json.dumps(report)
    assert by_name["The interface is not built"]["status"] == "fail" and report["status"] == "fail"
    account = next(item for item in report["items"] if item["name"].startswith("Anthropic API key"))
    assert account["status"] == "ok" and "a file only you can read" in account["detail"]
    assert by_name["No browser for looking at pages"]["status"] == "warn"
    assert "playwright install chromium" in by_name["No browser for looking at pages"]["fix"]
    assert f"Python {sys.version.split()[0]}" in by_name and "Port 65000 is free" in by_name
    assert f"Data folder: {settings.data_dir}" in by_name and "Model accounts" in text
    # The plan with no sign-in is a failure with the way out, and a shared studio says why.
    settings.config_path.write_text(json.dumps({"connections": [
        {"id": "subscription", "kind": "claude-subscription", "models": ["opus"]}]}))
    report = doctor.collect(settings, login=lambda: {"found": False, "plan": None, "detail": "No Claude sign-in."},
                            tailscale=NO_TAILSCALE)
    plan_item = next(item for item in report["items"] if "claude-subscription" in item["name"])
    assert plan_item["status"] == "fail" and "studio login" in plan_item["fix"]
    settings.shared = True
    report = doctor.collect(settings, login=lambda: {"found": True, "plan": "max", "detail": "x"}, tailscale=NO_TAILSCALE)
    plan_item = next(item for item in report["items"] if "claude-subscription" in item["name"])
    assert plan_item["status"] == "fail" and "shared with other people" in plan_item["detail"]


def test_status_and_stop_leave_a_studio_they_did_not_start_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("STUDIO2_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    monkeypatch.setattr(cli, "studio_at", lambda port, timeout=2.0: {"ok": True, "connections": [], "version": "x"})
    assert cli.main(["stop"]) == 1 and "left alone" in capsys.readouterr().out
    assert cli.main(["status"]) == 0 and "not started with `studio start`" in capsys.readouterr().out
    assert cli.main(["start", "--no-open"]) == 0 and "already running" in capsys.readouterr().out
    # A stale pid file (the process is gone) is cleared, never acted on.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "studio.pid").write_text(json.dumps({"pid": 2 ** 22 + 12345, "port": 8200}))
    monkeypatch.setattr(cli, "studio_at", lambda port, timeout=2.0: None)
    assert cli.main(["stop"]) == 0 and "No studio is running" in capsys.readouterr().out
    assert not (tmp_path / "data" / "studio.pid").exists()


def test_the_installers_private_node_is_where_attached_apps_look():
    assert NODE_HOMES[0] == str(STUDIO2_ROOT / ".tools" / "node" / "bin")
    assert str(cli.PRIVATE_NODE_BIN) == NODE_HOMES[0] and str(doctor.PRIVATE_NODE.parent) == NODE_HOMES[0]
    script = (STUDIO2_ROOT / "install.sh").read_text()
    assert 'TOOLS="$HERE/.tools"' in script and '"$TOOLS/node"' in script
    assert "playwright install chromium" in script and 'ask "  Download it now?" n' in script
