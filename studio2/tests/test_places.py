"""More places to look for skills and connectors (CONTRACT.md): the setting, what it finds, and
what it refuses."""

import json

import pytest

from studio2_server import cli, places
from studio2_server.config import library_places


@pytest.fixture
def elsewhere(tmp_path):
    skills = tmp_path / "team-skills"
    for name in ("alpha", "beta"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Does {name} things.\n---\nBody.\n")
    servers = tmp_path / "mcp" / "servers.json"
    servers.parent.mkdir()
    servers.write_text(json.dumps({"mcpServers": {"tavily-mcp": {"command": "npx", "args": ["tavily-mcp"]},
                                                  "docs": {"url": "https://mcp.example.org/sse"}, "note": {"about": "not a server"}}}))
    return skills, servers


def test_the_variable_wins_and_paths_are_expanded(tmp_path):
    import os
    assert library_places(None, ["~/a", "~/a", "", "/b"]) == (os.path.expanduser("~/a") and __import__("pathlib").Path("~/a").expanduser(), __import__("pathlib").Path("/b"))
    joined = os.pathsep.join(["/x", "/y"])
    assert [str(p) for p in library_places(joined, ["/ignored"])] == ["/x", "/y"]
    assert library_places("  ", "nope") == ()


async def test_skills_and_connectors_kept_elsewhere_are_found(client, studio, settings, elsewhere):
    skills, servers = elsewhere
    before = {item["name"] for item in studio.lib.catalog.skills()}
    saved = await client.put("/api/library/places", json={"skill_dirs": [str(skills)], "connector_files": [str(servers)]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["skill_dirs"] == [{"path": str(skills), "found": True, "skills": 2}]
    assert body["connector_files"] == [{"path": str(servers), "found": True, "connectors": 2}]
    assert json.loads(settings.config_path.read_text())["library"] == {"skill_dirs": [str(skills)], "connector_files": [str(servers)]}
    found = {item["name"]: item for item in studio.lib.catalog.skills()}
    assert {"alpha", "beta"} <= set(found) and not {"alpha", "beta"} & before        # used at once, no restart
    assert found["alpha"]["origin"] == str(skills)
    assert {"tavily-mcp", "docs"} <= set(studio.lib._machine_servers()) and "note" not in studio.lib._machine_servers()
    assert (await client.get("/api/library/places")).json()["skill_dirs"][0]["skills"] == 2
    cleared = await client.put("/api/library/places", json={"skill_dirs": []})
    assert cleared.json()["skill_dirs"] == [] and cleared.json()["connector_files"][0]["connectors"] == 2   # a list left out stays
    assert "alpha" not in {item["name"] for item in studio.lib.catalog.skills()}


async def test_what_is_not_a_place_is_refused_with_the_reason(client, settings, elsewhere, tmp_path):
    skills, servers = elsewhere
    (tmp_path / "plain.json").write_text("[1, 2]")
    for body, words in (({"skill_dirs": ["relative/path"]}, "not a full path"), ({"skill_dirs": [str(tmp_path / "gone")]}, "no folder"),
                        ({"connector_files": [str(tmp_path / "gone.json")]}, "no file"),
                        ({"connector_files": [str(tmp_path / "plain.json")]}, "not a file of connectors"),
                        ({"skill_dirs": "x"}, "list of paths")):
        answer = await client.put("/api/library/places", json=body)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == "invalid_place" and words in answer.json()["error"]["message"], body
    assert not settings.config_path.exists() or "library" not in json.loads(settings.config_path.read_text())
    settings.shared = True
    assert (await client.put("/api/library/places", json={"skill_dirs": [str(skills)]})).status_code == 403


def test_the_command_adds_shows_and_removes(settings, elsewhere, monkeypatch, capsys):
    skills, servers = elsewhere
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    run = lambda *words: (lambda args: args.run(args))(cli.build_parser().parse_args(["library", *words]))  # noqa: E731
    assert run() == 0 and "No other place is set" in capsys.readouterr().out
    assert run("add", str(skills)) == 0 and run("add", str(servers)) == 0
    out = capsys.readouterr().out
    assert f"skills      {skills}  (2 found)" in out and f"connectors  {servers}  (2 found)" in out
    assert run("add", "/no/such/folder") == 2 and run("add") == 2
    assert run("remove", str(skills)) == 0 and places.view(settings)["skill_dirs"] == []
    assert places.view(settings)["connector_files"][0]["path"] == str(servers)
