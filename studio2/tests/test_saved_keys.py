"""A saved key or secret is only ever sent to the address saved with it (CONTRACT.md, "Where a key
is kept"). The studio's API on loopback needs no key, so anything on this machine (the agent's own
shell included) can call it; none of these routes may then carry a saved secret somewhere else."""

from __future__ import annotations

import httpx

from studio2_server import gateway, keystore, setup
from studio2_server.api import create_app
from studio2_server.config import Settings

KEY = "sk-saved-0123456789abcdefKEY"
NO_TAILSCALE = lambda: {"found": False, "address": None, "detail": "none"}     # noqa: E731


def real_settings(tmp_path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data", config_path=tmp_path / "cfg" / "config.json",
                        web_dist=tmp_path / "no-dist", home=tmp_path / "home")
    settings._secrets = keystore.FileStore(tmp_path / "cfg" / "secrets.json")
    return settings


def saved(settings, account: dict) -> None:
    setup.apply(setup.plan({"account": account}, settings, tailscale=NO_TAILSCALE), settings)


def recorder(monkeypatch) -> list:
    asked: list = []

    async def gateway_list(base_url, key, client=None):
        asked.append(("gateway", base_url, key))
        return [{"id": "m"}]

    async def provider_list(provider, key, base_url=None, client=None):
        asked.append((provider, base_url, key))
        return [{"id": "m"}]
    monkeypatch.setattr(gateway, "list_gateway_models", gateway_list)
    monkeypatch.setattr(gateway, "list_models", provider_list)
    return asked


async def test_a_saved_key_is_not_sent_to_an_address_the_caller_names(tmp_path, monkeypatch):
    settings = real_settings(tmp_path)
    saved(settings, {"account": "gateway", "base_url": "http://gw.saved:4000", "api_key": KEY, "models": ["m"]})
    saved(settings, {"account": "openai", "api_key": KEY, "models": ["gpt-x"], "make_default": False})
    asked = recorder(monkeypatch)
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        for account in ("gateway", "openai"):
            stolen = await client.post("/api/setup/models", json={"account": account,
                                                                   "base_url": "http://127.0.0.1:19599/v1"})
            assert stolen.status_code == 400, stolen.text
            assert stolen.json()["error"]["code"] == "key_needed" and KEY not in stolen.text
        assert asked == []
        # The saved address (as the screen sends it back, with or without a slash) and no address
        # at all both use the saved key at the saved address.
        for base in ("http://gw.saved:4000/", None):
            ok = await client.post("/api/setup/models", json={"account": "gateway", "base_url": base})
            assert ok.status_code == 200
        assert asked == [("gateway", "http://gw.saved:4000", KEY)] * 2
        ok = await client.post("/api/setup/models", json={"account": "openai"})
        assert ok.status_code == 200 and asked[-1] == ("openai", None, KEY)
        # A key typed with a new address is that key's own business: it goes where it is told.
        typed = await client.post("/api/setup/models", json={"account": "gateway", "api_key": "sk-typed-123456789",
                                                             "base_url": "http://other:4000"})
        assert typed.status_code == 200 and asked[-1] == ("gateway", "http://other:4000", "sk-typed-123456789")


def test_setup_will_not_move_a_saved_key_to_a_new_address(tmp_path):
    settings = real_settings(tmp_path)
    saved(settings, {"account": "gateway", "base_url": "http://gw.saved:4000", "api_key": KEY, "models": ["m"]})
    moved = {"account": "gateway", "base_url": "http://elsewhere:4000", "models": ["m"]}
    try:
        setup.plan({"account": moved}, settings, tailscale=NO_TAILSCALE)
        raise AssertionError("a new address kept the saved key")
    except setup.SetupError as exc:
        assert "key" in str(exc) and "address" in str(exc)
    # The same address, or a new one with its key typed, is fine.
    setup.plan({"account": {**moved, "base_url": "http://gw.saved:4000/"}}, settings, tailscale=NO_TAILSCALE)
    setup.plan({"account": {**moved, "api_key": "sk-new-0123456789"}}, settings, tailscale=NO_TAILSCALE)
    # Foundry's resource is an address too.
    saved(settings, {"account": "foundry", "resource": "mine", "api_key": KEY})
    try:
        setup.plan({"account": {"account": "foundry", "resource": "theirs"}}, settings, tailscale=NO_TAILSCALE)
        raise AssertionError("a new resource kept the saved key")
    except setup.SetupError:
        pass


async def test_the_setup_api_takes_no_environment_variable_name_for_a_key(tmp_path, monkeypatch):
    # Over the API a key named by a variable would let a caller send any of the studio's own
    # variables to an address of its choosing; naming one is for the terminal.
    monkeypatch.delenv("STUDIO2_HOSTS", raising=False)
    monkeypatch.setattr(setup, "detect_claude_login", lambda *a, **k: {"found": False, "plan": None, "detail": "-"})
    monkeypatch.setattr(setup, "detect_tailscale", NO_TAILSCALE)
    settings = real_settings(tmp_path)
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        answer = await client.post("/api/setup", json={"answers": {"account": {
            "account": "gateway", "base_url": "http://127.0.0.1:19599", "api_key_env": "HOME", "models": ["m"]}}})
        assert answer.status_code == 400 and "studio setup" in answer.json()["error"]["message"]
    assert not settings.config_path.exists()
    # The terminal (setup.plan without the API's flag) still can.
    setup.plan({"account": {"account": "gateway", "base_url": "http://gw:4000", "api_key_env": "GW_KEY",
                            "models": ["m"]}}, settings, tailscale=NO_TAILSCALE)


async def test_a_connector_secret_does_not_follow_a_new_address(client, studio):
    made = await client.post("/api/mcp-servers", json={
        "name": "docs", "transport": "http", "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer abc123"}})
    sid = made.json()["id"]
    for change in ({"url": "http://127.0.0.1:19599/mcp"},
                   {"url": "http://127.0.0.1:19599/mcp", "headers": {"Authorization": ""}},
                   {"transport": "stdio", "command": "sh", "args": ["-c", "env"]}):
        refused = await client.patch(f"/api/mcp-servers/{sid}", json=change)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "secret_needed", change
    assert studio.lib.server_config(sid) == {"type": "http", "url": "https://example.com/mcp",
                                             "headers": {"Authorization": "Bearer abc123"}}
    # A label is not an address; a new address with its secrets typed again is allowed.
    assert (await client.patch(f"/api/mcp-servers/{sid}", json={"label": "Docs"})).status_code == 200
    moved = await client.patch(f"/api/mcp-servers/{sid}", json={"url": "https://new.example.com/mcp",
                                                                 "headers": {"Authorization": "Bearer new"}})
    assert moved.status_code == 200
    assert studio.lib.server_config(sid)["headers"] == {"Authorization": "Bearer new"}

    tool = await client.post("/api/mcp-servers", json={"name": "db", "transport": "stdio", "command": "db-mcp",
                                                       "env": {"DB_PASSWORD": "pw-123"}})
    tid = tool.json()["id"]
    for change in ({"command": "sh"}, {"args": ["--dump-env"]}):
        assert (await client.patch(f"/api/mcp-servers/{tid}", json=change)).status_code == 400
    assert (await client.patch(f"/api/mcp-servers/{tid}", json={"args": ["--x"], "env": {"DB_PASSWORD": "pw-2"}})
            ).status_code == 200
    # One without secrets moves freely.
    plain = (await client.post("/api/mcp-servers", json={"name": "open", "transport": "http",
                                                         "url": "https://a.example/mcp"})).json()["id"]
    assert (await client.patch(f"/api/mcp-servers/{plain}", json={"url": "https://b.example/mcp"})).status_code == 200


async def test_a_database_password_does_not_follow_a_new_host(client, studio):
    made = (await client.post("/api/data-sources", json={"name": "Warehouse", "kind": "postgres", "host": "db.saved",
                                                         "database": "d", "user": "u", "password": "s3cret",
                                                         "ssl": "require"})).json()
    sid = made["id"]
    for change in ({"host": "127.0.0.1"}, {"port": 19599}, {"kind": "mysql"}, {"ssl": "disable"}):
        refused = await client.patch(f"/api/data-sources/{sid}", json=change)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "password_needed", change
    assert studio.data.raw(sid)["host"] == "db.saved" and studio.data.password(sid) == "s3cret"
    assert (await client.patch(f"/api/data-sources/{sid}", json={"name": "Store", "user": "v"})).status_code == 200
    moved = await client.patch(f"/api/data-sources/{sid}", json={"host": "db.new", "password": "other"})
    assert moved.status_code == 200 and studio.data.password(sid) == "other"
    # Without a stored password there is nothing to carry.
    assert (await client.patch(f"/api/data-sources/{sid}", json={"password": ""})).status_code == 200
    assert (await client.patch(f"/api/data-sources/{sid}", json={"host": "db.third"})).status_code == 200


async def test_no_read_route_returns_a_saved_secret(client, studio, project):
    secrets = ["Bearer hdr-0123456789", "env-0123456789", "db-0123456789", "tvly-0123456789"]
    await client.post("/api/mcp-servers", json={"name": "a", "transport": "http", "url": "https://a.example/mcp",
                                                "headers": {"Authorization": secrets[0]}})
    made = await client.post("/api/mcp-servers", json={"name": "b", "transport": "stdio", "command": "b",
                                                       "env": {"B_KEY": secrets[1]}})
    source = (await client.post("/api/data-sources", json={"name": "W", "kind": "postgres", "host": "h",
                                                           "database": "d", "user": "u", "password": secrets[2]})).json()
    assert (await client.put("/api/services/tavily", json={"key": secrets[3]})).status_code == 200
    pid = project["id"]
    await client.put(f"/api/projects/{pid}/library", json={"mcp_servers": {made.json()["id"]: True}})
    await client.put(f"/api/projects/{pid}/data-sources/{source['id']}")
    for path in ("/api/health", "/api/library", "/api/data-sources", "/api/services", "/api/models",
                 "/api/litellm", f"/api/projects/{pid}", f"/api/projects/{pid}/library",
                 f"/api/projects/{pid}/data-sources", "/api/library/items?kind=connectors", "/api/library/places"):
        answer = await client.get(path)
        assert answer.status_code == 200, path
        assert not any(secret in answer.text for secret in secrets), path


async def test_an_empty_value_never_keeps_a_stored_connector_secret(client, studio):
    # The second review's exact request: the token kept by "", and PYTHONPATH added so the unchanged
    # command runs the caller's code with it.
    made = await client.post("/api/mcp-servers", json={"name": "pyconn", "transport": "stdio", "command": "python3",
                                                       "args": ["-c", "pass"], "env": {"TOKEN": "s3cret-value"}})
    sid = made.json()["id"]
    refused = await client.patch(f"/api/mcp-servers/{sid}",
                                 json={"env": {"TOKEN": "", "PYTHONPATH": "/private/tmp/evil"}})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "secret_needed"
    assert studio.lib.server_config(sid)["env"] == {"TOKEN": "s3cret-value"}
    for env in ({"TOKEN": ""}, {"TOKEN": "", "NODE_OPTIONS": "--require /x.js"}):
        assert (await client.patch(f"/api/mcp-servers/{sid}", json={"env": env})).status_code == 400
    http = (await client.post("/api/mcp-servers", json={"name": "web", "transport": "http", "url": "https://a.example/mcp",
                                                        "headers": {"Authorization": "Bearer t"}})).json()["id"]
    assert (await client.patch(f"/api/mcp-servers/{http}", json={"headers": {"Authorization": "", "X-A": "1"}})
            ).status_code == 400
    # A new connector with an empty value is refused too; every value typed again is fine, and {} removes them.
    assert (await client.post("/api/mcp-servers", json={"name": "e", "transport": "stdio", "command": "x",
                                                        "env": {"A": ""}})).status_code == 400
    assert (await client.patch(f"/api/mcp-servers/{sid}", json={"env": {"TOKEN": "new", "LOG": "1"}})).status_code == 200
    assert studio.lib.server_config(sid)["env"] == {"TOKEN": "new", "LOG": "1"}
    assert (await client.patch(f"/api/mcp-servers/{sid}", json={"env": {}})).status_code == 200
    assert studio.lib.server_config(sid)["env"] == {}
