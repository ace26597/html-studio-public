"""A library connector starts outside the project folder (CONTRACT.md, "A library connector starts
outside the project folder"). The agent program starts every stdio connector in the project folder,
where the agent writes, so `python -m pkg` or `npx -y x` could run a file the agent planted there,
holding the connector's token. No model is called; the launcher and a tiny connector really run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from studio2_server.library import LAUNCHER

SECRET = "s3cret-launch-0123"

# A tiny real connector: its one tool's name says where it started.
SERVER = """
import os, sys
from mcp.server.mcpserver import MCPServer
home = os.environ["EXPECT_HOME"]
server = MCPServer("where")
name = "started_in_studio_folder" if os.getcwd().startswith(os.path.realpath(home) + os.sep) else "started_elsewhere"

@server.tool(name=name)
def where() -> str:
    return os.getcwd()

server.run()
"""


def launch(project: Path, base: Path, *command: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-S", str(LAUNCHER), str(base), *command], cwd=project,
                          env=env, capture_output=True, text=True, timeout=30)


def test_library_stdio_connectors_start_through_the_launcher_and_an_apps_own_do_not(client, studio, settings,
                                                                                    monkeypatch, tmp_path):
    sid = studio.lib.add_server({"name": "tok", "transport": "stdio", "command": "npx", "args": ["-y", "some-mcp"],
                                 "env": {"TOKEN": SECRET}})["id"]
    config = studio.lib.server_config(sid)
    home = settings.data_dir / "run" / "connectors"
    assert config["command"] == sys.executable
    assert config["args"] == ["-I", "-S", str(LAUNCHER), str(home), "npx", "-y", "some-mcp"]
    assert config["env"] == {"TOKEN": SECRET} and SECRET not in json.dumps(config["args"])

    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / ".claude.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "uvx", "args": ["x"]}}}))
    machine = studio.lib.server_config("machine:mine")
    assert machine["args"][:5] == ["-I", "-S", str(LAUNCHER), str(home), "uvx"]

    monkeypatch.setattr(studio.lib.catalog, "ensure", lambda force=False: {})
    studio.lib.catalog.plugin_connectors = [{"plugin": "p@m", "name": "plug", "root": str(tmp_path / "plugin"),
                                             "config": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/serve"}, "enabled": True}]
    plugin = studio.lib.server_config("plugin:p@m:plug")
    assert plugin["args"][4] == f"{tmp_path / 'plugin'}/bin/serve" and plugin["command"] == sys.executable

    # An app's own connector belongs to the project: it starts there, as before.
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"own": {"command": "node", "args": ["srv.js"]}}}))
    view = next(s for s in studio.lib.app_servers(tmp_path) if s["name"] == "own")
    assert studio.lib.server_config(view["id"], tmp_path)["command"] == "node"
    # Http connectors are untouched.
    web = studio.lib.add_server({"name": "web", "transport": "http", "url": "https://example.com/mcp"})["id"]
    assert studio.lib.server_config(web) == {"type": "http", "url": "https://example.com/mcp", "headers": {}}


def test_the_launcher_leaves_the_project_and_what_points_into_it(tmp_path):
    project = (tmp_path / "project").resolve()
    (project / "bin").mkdir(parents=True)
    # What the agent could plant: a module of the name the connector runs, and a program on a PATH entry.
    (project / "planted.py").write_text("print('PLANTED RAN')\n")
    (project / "bin" / "lookup").write_text("#!/bin/sh\necho PLANTED-BIN\n")
    (project / "bin" / "lookup").chmod(0o755)
    base = tmp_path / "run" / "connectors"
    env = {"PATH": f"{project}/bin:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "TOKEN": SECRET,
           "CLAUDE_PROJECT_DIR": str(project), "PWD": str(project), "OLDPWD": str(project / "bin"),
           "PYTHONPATH": str(project)}

    # Before: started in the project, as the agent program starts it, the planted module runs.
    before = subprocess.run(["python3", "-m", "planted"], cwd=project, env=env, capture_output=True, text=True)
    assert "PLANTED RAN" in before.stdout

    shown = launch(project, base, "/bin/sh", "-c", 'pwd; env', env=env)
    assert shown.returncode == 0, shown.stderr
    lines = shown.stdout.splitlines()
    cwd = Path(lines[0]).resolve()
    assert cwd.parent == base.resolve() and not list(cwd.iterdir())
    assert oct(cwd.stat().st_mode & 0o777) == "0o700" and oct(base.stat().st_mode & 0o777) == "0o700"
    seen = dict(line.split("=", 1) for line in lines[1:] if "=" in line)
    assert seen["TOKEN"] == SECRET                       # the connector still has its secret
    assert "CLAUDE_PROJECT_DIR" not in seen and "OLDPWD" not in seen and "PYTHONPATH" not in seen
    assert seen["PATH"] == "/usr/bin:/bin" and Path(seen["PWD"]).resolve() == cwd

    # After: the same commands no longer find what was planted.
    module = launch(project, base, "python3", "-m", "planted", env=env)
    assert "PLANTED RAN" not in module.stdout and module.returncode != 0
    found = launch(project, base, "lookup", env=env)
    assert "PLANTED-BIN" not in found.stdout and found.returncode != 0
    # A relative command is refused with a sentence, not looked up in some folder.
    relative = launch(project, base, "bin/lookup", env=env)
    assert relative.returncode == 127 and "relative path" in relative.stderr and "PLANTED" not in relative.stdout


def test_the_launcher_imports_nothing_from_where_it_starts(tmp_path):
    """`-I -S`: a planted `os.py`, `stat.py` or `sitecustomize.py` in the folder it is started in is never loaded."""
    project = tmp_path / "project"
    project.mkdir()
    for name in ("os", "stat", "sys", "sitecustomize", "usercustomize"):
        (project / f"{name}.py").write_text("open(__file__ + '.loaded', 'w').write('x')\n")
    done = launch(project, tmp_path / "base", "/bin/echo", "ok", env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(project)})
    assert done.stdout.strip() == "ok", done.stderr
    assert not list(project.glob("*.loaded"))


def test_the_launcher_refuses_a_start_folder_that_is_a_link(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "connectors").symlink_to(elsewhere)
    done = launch(tmp_path, tmp_path / "run" / "connectors", "/bin/echo", "ok", env={"PATH": "/usr/bin:/bin"})
    assert done.returncode == 127 and "not a folder of this user" in done.stderr and not list(elsewhere.iterdir())


async def test_a_relative_command_is_refused_when_a_connector_is_added(client):
    response = await client.post("/api/mcp-servers", json={"name": "rel", "transport": "stdio", "command": "./serve.sh"})
    assert response.status_code == 400 and "relative path" in response.json()["error"]["message"]
    ok = await client.post("/api/mcp-servers", json={"name": "abs", "transport": "stdio", "command": "/usr/bin/true"})
    assert ok.status_code == 201


async def test_the_test_button_starts_a_connector_as_a_request_does(client, studio, settings, tmp_path, monkeypatch):
    script = tmp_path / "where_server.py"
    script.write_text(SERVER)
    home = settings.data_dir / "run" / "connectors"
    made = await client.post("/api/mcp-servers", json={"name": "where", "transport": "stdio", "command": sys.executable,
                                                      "args": [str(script)], "env": {"EXPECT_HOME": str(home)}})
    assert made.status_code == 201, made.text
    tested = (await client.post(f"/api/mcp-servers/{made.json()['id']}/test")).json()
    assert tested["ok"], tested
    assert [t["name"] for t in tested["tools"]] == ["started_in_studio_folder"]


def start_folders(home: Path, outside: Path) -> dict[str, Path]:
    """An old start folder holding a file and a link to something outside, a young one, an old
    folder that is not a start folder, and an old link named like a start folder."""
    import time
    old = time.time() - 2 * 86400
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "keep.txt").write_text("keep")
    made = {"old": home / ("c-" + "a" * 16), "young": home / ("c-" + "b" * 16),
            "other": home / "notes", "link": home / ("c-" + "c" * 16)}
    for key in ("old", "young", "other"):
        made[key].mkdir(parents=True, exist_ok=True)
    (made["old"] / "cache.txt").write_text("x")
    (made["old"] / "out").symlink_to(outside)
    made["link"].symlink_to(outside)
    for key in ("old", "other"):
        os.utime(made[key], (old, old))
    os.utime(made["link"], (old, old), follow_symlinks=False)
    return made


def test_old_start_folders_are_removed_when_the_studio_starts(studio, settings, tmp_path):
    home = settings.data_dir / "run" / "connectors"
    made = start_folders(home, tmp_path / "outside")
    studio.registry.sweep()
    assert not made["old"].exists() and made["young"].is_dir() and made["other"].is_dir()
    assert made["link"].is_symlink()                    # not a folder: not touched
    assert (tmp_path / "outside" / "keep.txt").read_text() == "keep"


def test_old_start_folders_are_removed_when_a_connector_starts(tmp_path):
    home = tmp_path / "run" / "connectors"
    made = start_folders(home, tmp_path / "outside")
    project = tmp_path / "project"
    project.mkdir()
    done = launch(project, home, "/bin/echo", "ok", env={"PATH": "/usr/bin:/bin"})
    assert done.stdout.strip() == "ok", done.stderr
    assert not made["old"].exists() and made["young"].is_dir() and made["other"].is_dir()
    assert made["link"].is_symlink() and (tmp_path / "outside" / "keep.txt").read_text() == "keep"


def test_a_variable_that_mentions_the_project_anywhere_is_dropped(tmp_path):
    project = (tmp_path / "proj").resolve()
    project.mkdir()
    sibling = tmp_path / "proj-other"                  # a name that starts the same is not the project
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "NODE_OPTIONS": f"--require {project}/x.js", "JAVA_TOOL_OPTIONS": f"-javaagent:{project}/a.jar=1",
           "EDITOR_ARGS": f"--dir={project}", "FOO": f"x:{project}/lib:y", "KEEP": f"--cache {sibling}/c",
           "TOKEN": SECRET}
    shown = launch(project, tmp_path / "base", "/usr/bin/env", env=env)
    seen = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
    for name in ("NODE_OPTIONS", "EDITOR_ARGS"):
        assert name not in seen, name
    # ":" splits this one like a list of paths; the entry in the project goes, the rest is harmless.
    assert seen.get("JAVA_TOOL_OPTIONS", "") == "-javaagent"
    assert seen["FOO"] == "x:y" and seen["KEEP"] == f"--cache {sibling}/c" and seen["TOKEN"] == SECRET
    assert f"{project}/" not in shown.stdout and f"={project}\n" not in shown.stdout

    # A project that is the home folder (or above it): nothing is dropped, as before.
    env_home = {**env, "HOME": str(project)}
    kept = launch(project, tmp_path / "base", "/usr/bin/env", env=env_home)
    assert f"NODE_OPTIONS=--require {project}/x.js" in kept.stdout
