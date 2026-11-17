"""Step 3k: bring an app by its folder (CONTRACT.md, "Bring an app by its folder").

The rule tested hardest: the original folder is never written to. Every test that touches an
original takes a byte-for-byte picture of it (its .git included) before and after.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from studio2_server import attach
from studio2_server.config import REPO_ROOT

GIT = ["git", "-c", "user.name=Owner", "-c", "user.email=owner@example.com", "-c", "commit.gpgsign=false"]


def run_git(folder: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=folder, capture_output=True, check=True, text=True).stdout


def snapshot(folder: Path) -> dict[str, str]:
    """Every file under the folder, .git included, with a hash of its bytes."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def make_app(root: Path, name: str = "site", next_app: bool = True) -> Path:
    """A small git-tracked app with an env file, an ignored build folder and one more commit."""
    app = root / name
    (app / "src").mkdir(parents=True)
    (app / "index.html").write_text("<!doctype html><title>Site</title><h1>Hello</h1>\n")
    (app / "src" / "main.js").write_text("console.log('hi')\n")
    if next_app:
        (app / "package.json").write_text(json.dumps({"dependencies": {"next": "16.1.6", "react": "19"}}))
        (app / "package-lock.json").write_text("{}")
    (app / ".gitignore").write_text(".env*\n.next/\nnode_modules/\n")
    run_git(app, "init", "-q", "-b", "main")
    run_git(app, "add", "-A")
    run_git(app, "commit", "-qm", "First")
    (app / "src" / "main.js").write_text("console.log('hi again')\n")
    run_git(app, "commit", "-qam", "Second")
    (app / ".env").write_text("SECRET=do-not-version\n")
    (app / ".env.local").write_text("LOCAL=1\n")
    (app / ".next").mkdir()
    (app / ".next" / "cache.bin").write_bytes(b"x" * 100)
    (app / "node_modules" / "pkg").mkdir(parents=True)
    (app / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    return app


async def wait_for(predicate, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


# -- inspect ---------------------------------------------------------------------------------
async def test_inspect_reads_a_git_app_without_writing_to_it(client, tmp_path):
    app = make_app(tmp_path / "apps")
    (app / "index.html").write_text("changed but not committed\n")
    (app / "notes.md").write_text("untracked\n")
    before = snapshot(app)
    found = (await client.post("/api/apps/inspect", json={"path": str(app)})).json()
    assert snapshot(app) == before
    assert found["exists"] and found["readable"] and found["writable"]
    assert found["git"]["branch"] == "main" and len(found["git"]["head"]) == 40
    assert found["git"]["subject"] == "Second"
    assert {"path": "index.html", "status": "modified"} in found["git"]["uncommitted"]
    assert {"path": "notes.md", "status": "untracked"} in found["git"]["uncommitted"]
    assert found["framework"] == {"id": "next", "label": "Next.js"}
    assert "next dev -p $PORT -H 127.0.0.1" in found["run"]
    assert found["package_manager"] == "npm" and "npm ci" in found["install"]
    assert found["env_files"] == [".env", ".env.local"]          # names only
    assert "SECRET" not in json.dumps(found)
    assert found["size"]["files"] == 5 and found["size"]["history_bytes"] > 0
    assert found["refused"] == {"copy": None, "in_place": None}
    assert any("not committed" in note for note in found["notes"])


@pytest.mark.parametrize("files,kind", [
    ({"package.json": {"dependencies": {"nuxt": "4", "vite": "7"}}}, "nuxt"),
    ({"package.json": {"devDependencies": {"@sveltejs/kit": "2", "vite": "7"}}}, "sveltekit"),
    ({"package.json": {"devDependencies": {"vite": "7"}}}, "vite"),
    ({"package.json": {"dependencies": {"astro": "6"}}}, "astro"),
    ({"package.json": {"dependencies": {"@angular/core": "20"}}}, "angular"),
    ({"manage.py": "import django"}, "django"),
    ({"main.py": "from fastapi import FastAPI\napi = FastAPI()\n"}, "fastapi"),
    ({"app.py": "from flask import Flask\napp = Flask(__name__)\n"}, "flask"),
    ({"artisan": "#!/usr/bin/env php"}, "laravel"),
    ({"index.html": "<h1>hi</h1>"}, "html"),
    ({"README.md": "nothing to run"}, None),
])
def test_known_kinds_are_detected(tmp_path, files, kind):
    for name, content in files.items():
        (tmp_path / name).write_text(json.dumps(content) if isinstance(content, dict) else content)
    found = attach.detect(tmp_path)
    assert (found["framework"] or {}).get("id") == kind
    if kind == "fastapi":
        assert "uvicorn main:api --host 127.0.0.1 --port $PORT" in found["run"]
    if kind in ("html", None):
        assert found["run"] is None


@pytest.mark.parametrize("files,python", [
    # The install step makes `.venv` before the app starts, so the app must start from it (the
    # folder has no `.venv` yet when it is looked at).
    ({"requirements.txt": "fastapi\nuvicorn\n"}, ".venv/bin/python"),
    ({"requirements.txt": "fastapi\n", "uv.lock": ""}, "uv run python"),
    ({}, "python3"),                                        # nothing to install: the machine's own
])
def test_python_app_starts_from_what_the_install_makes(tmp_path, files, python):
    for name, content in {**files, "main.py": "from fastapi import FastAPI\napi = FastAPI()\n"}.items():
        (tmp_path / name).write_text(content)
    found = attach.detect(tmp_path)
    assert found["run"].startswith(f"exec {python} -m uvicorn main:api"), found["run"]


async def test_inspect_refuses_what_must_not_be_attached(client, settings, tmp_path):
    settings.home.mkdir(parents=True, exist_ok=True)
    cases = {
        str(settings.home): ("home folder", "home folder"),
        str(settings.data_dir): (None, "studio's own files"),
        str(settings.data_dir / "projects"): (None, "studio's own files"),
        str(REPO_ROOT / "studio2"): (None, "studio's own files"),
        "/": ("top of a disk", "top of a disk"),
    }
    for path, (copy_says, in_place_says) in cases.items():
        found = (await client.post("/api/apps/inspect", json={"path": path})).json()
        refused = found["refused"]
        assert in_place_says in (refused["in_place"] or ""), (path, refused)
        if copy_says:
            assert copy_says in (refused["copy"] or "") or refused["copy"], (path, refused)
    missing = (await client.post("/api/apps/inspect", json={"path": str(tmp_path / "nope")})).json()
    assert not missing["exists"] and "no folder" in missing["refused"]["copy"]
    relative = await client.post("/api/apps/inspect", json={"path": "dev/site"})
    assert relative.status_code == 400 and relative.json()["error"]["code"] == "invalid_request"


def test_a_folder_the_studio_cannot_write_is_refused_in_place(tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        reason = attach.in_place_refusal(folder, [], tmp_path / "home", set())
    finally:
        folder.chmod(0o755)
    assert "read and write" in reason


def test_only_loopback_addresses_are_shown():
    assert attach.check_address("localhost:5173") == "http://localhost:5173"
    assert attach.check_address("http://127.0.0.1:3000/") == "http://127.0.0.1:3000"
    assert attach.check_address("http://[::1]:8080") == "http://[::1]:8080"
    for bad in ("http://192.168.1.5:3000", "https://localhost:5173", "http://example.com:80", "http://localhost",
                "http://169.254.169.254:80", "file:///etc/passwd"):
        with pytest.raises(attach.AttachError) as caught:
            attach.check_address(bad)
        assert caught.value.code == "address_refused"


# -- safe copy -------------------------------------------------------------------------------
async def test_a_safe_copy_keeps_history_starts_a_studio_branch_and_never_touches_the_original(client, studio, tmp_path):
    app = make_app(tmp_path / "apps")
    (app / "index.html").write_text("<h1>work in progress</h1>\n")      # uncommitted, must not be taken
    original_head = run_git(app, "rev-parse", "HEAD").strip()
    first_app_commit = run_git(app, "rev-list", "--max-parents=0", "HEAD").strip()
    before = snapshot(app)
    response = await client.post("/api/projects/attach", json={
        "path": str(app), "mode": "copy", "run": "", "env_files": [".env.local"]})
    assert response.status_code == 201, response.text
    project = response.json()
    pid, record = project["id"], project["attach"]
    workspace = studio.store.workspace(pid)
    assert workspace == studio.store.dir(pid) / "workspace"
    assert record["mode"] == "copy" and record["kind"] == "git" and record["path"] == str(app.resolve())
    assert record["branch"] == "main" and record["started_from"] == original_head
    assert record["studio_branch"].startswith("studio/site-") and record["framework"] == "next"
    assert {"path": "index.html", "status": "modified"} in record["uncommitted"]
    assert record["included_uncommitted"] is False and record["env_files"] == [".env.local"]
    # The copy has the app's history, on the studio's branch, and no way to push to the original.
    assert run_git(workspace, "rev-list", "--count", "HEAD").strip() == "3"
    assert run_git(workspace, "branch", "--show-current").strip() == record["studio_branch"]
    assert run_git(workspace, "remote").strip() == ""
    assert (workspace / "index.html").read_text().startswith("<!doctype html>")   # last commit, not the edit
    assert (workspace / ".env.local").read_text() == "LOCAL=1\n" and not (workspace / ".env").exists()
    assert not (workspace / "node_modules").exists() and not (workspace / ".next").exists()
    # Versions start at the import; earlier app commits cannot be shown or restored.
    versions = (await client.get(f"/api/projects/{pid}/versions")).json()
    assert [v["kind"] for v in versions] == ["init"] and versions[0]["sha"] == record["import_commit"]
    assert versions[0]["message"].startswith(f"Import from {app.resolve()} at {original_head[:7]}")
    for old in (original_head, first_app_commit):
        assert (await client.post(f"/api/projects/{pid}/restore", json={"sha": old})).status_code == 404
        assert (await client.get(f"/api/projects/{pid}/versions/{old}/diff")).status_code == 404
    # A studio edit is a version on the studio branch; env files never enter one.
    put = await client.put(f"/api/projects/{pid}/file", json={"path": "src/main.js", "content": "console.log(1)\n"})
    assert put.status_code == 200 and put.json()["checkpoint"]["kind"] == "user"
    tracked = run_git(workspace, "ls-files")
    assert ".env.local" not in tracked and "studio.json" not in tracked
    versions = (await client.get(f"/api/projects/{pid}/versions")).json()
    assert [v["kind"] for v in versions] == ["user", "init"]
    assert (await client.post(f"/api/projects/{pid}/restore", json={"sha": record["import_commit"]})).status_code == 200
    # The original: not one byte changed, .git included.
    assert snapshot(app) == before
    assert run_git(app, "rev-parse", "HEAD").strip() == original_head


async def test_a_copy_can_take_the_uncommitted_work_when_asked(client, studio, tmp_path):
    app = make_app(tmp_path / "apps")
    (app / "index.html").write_text("<h1>work in progress</h1>\n")
    (app / "draft.md").write_text("new file\n")
    before = snapshot(app)
    project = (await client.post("/api/projects/attach", json={
        "path": str(app), "run": "", "include_uncommitted": True})).json()
    workspace = studio.store.workspace(project["id"])
    assert (workspace / "index.html").read_text() == "<h1>work in progress</h1>\n"
    assert (workspace / "draft.md").read_text() == "new file\n"
    assert project["attach"]["included_uncommitted"] is True
    assert "draft.md" in run_git(workspace, "show", "--name-only", "--format=", "HEAD")
    assert snapshot(app) == before


def test_uncommitted_env_files_are_neither_copied_unticked_nor_versioned(tmp_path):
    """The .gitignore does not list them, so `git ls-files --others` offers them as ordinary files."""
    app = make_app(tmp_path / "apps")
    (app / ".gitignore").write_text(".next/\nnode_modules/\n")
    (app / ".env.shared").write_text("SHARED=committed\n")
    run_git(app, "add", "-A", ".gitignore", ".env.shared")
    run_git(app, "commit", "-qm", "Env files no longer ignored")
    (app / ".env.shared").write_text("SHARED=changed, not committed\n")
    (app / "api").mkdir()
    (app / "api" / ".env.production").write_text("DEEP=secret\n")
    workspace = tmp_path / "copy"
    attach.make_copy(app, workspace, "studio/site-1", None, env_files=[".env", ".env.local"],
                     allowed_env=[".env.local"], include_uncommitted=True)
    assert not (workspace / ".env").exists() and not (workspace / "api" / ".env.production").exists()
    assert (workspace / ".env.local").read_text() == "LOCAL=1\n"       # ticked: copied
    assert (workspace / ".env.shared").read_text() == "SHARED=committed\n"   # its uncommitted change is not taken
    tracked = run_git(workspace, "ls-files").split()
    assert not {".env", ".env.local", "api/.env.production"} & set(tracked)   # never in a version
    assert subprocess.run(["git", "show", "HEAD:.env.local"], cwd=workspace, capture_output=True).returncode != 0


def test_a_plain_folder_copy_leaves_unticked_env_files_out_and_never_versions_ticked_ones(tmp_path):
    folder = tmp_path / "plain"
    (folder / "server").mkdir(parents=True)
    (folder / "index.html").write_text("<h1>Plain</h1>")
    (folder / ".env").write_text("SECRET=1\n")
    (folder / ".env.local").write_text("LOCAL=1\n")
    (folder / "server" / ".env").write_text("DEEP=1\n")
    for ticked in ([], [".env.local"]):
        workspace = tmp_path / f"copy-{len(ticked)}"
        attach.make_copy(folder, workspace, "unused", None, env_files=[".env", ".env.local"],
                         allowed_env=ticked, include_uncommitted=False)
        files = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
                       if p.is_file() and ".git" not in p.parts)
        assert files == sorted(["index.html", *ticked])
        assert run_git(workspace, "show", "--name-only", "--format=", "HEAD").split() == ["index.html"]


KEY_FILES = [".envrc", ".dev.vars", ".dev.vars.staging", ".netrc", ".pypirc", "id_ed25519", "sub/.envrc",
             "deploy/id_rsa"]


def test_one_rule_says_which_files_hold_keys():
    from studio2_server import keyfiles
    for path in [".env", ".env.local", "api/.env.production", *KEY_FILES, ".env/lib/site.py"]:
        assert keyfiles.is_key_file(path), path
    for path in ["README.md", ".npmrc", "environment.ts", ".envoy.yaml", "id_rsa.pub", "src/env.js",
                 "config/keys.md", "cert.pem"]:
        assert not keyfiles.is_key_file(path), path
    from studio2_server import export, templates
    assert all(templates.private(tuple(p.split("/"))) for p in KEY_FILES)
    assert export.is_env(("a", ".envrc")) and not export.is_env(("environment.ts",))


async def test_key_files_other_than_env_are_neither_copied_unticked_nor_versioned(client, tmp_path):
    folder = tmp_path / "plain"
    for path in [*KEY_FILES, "index.html", ".npmrc"]:
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(f"{path}: TOKEN=secret\n")
    found = (await client.post("/api/apps/inspect", json={"path": str(folder)})).json()
    assert {".envrc", ".dev.vars", ".netrc", "id_ed25519"} <= set(found["env_files"])
    workspace = tmp_path / "copy"
    attach.make_copy(folder, workspace, "unused", None, env_files=found["env_files"], allowed_env=[".envrc"],
                     include_uncommitted=False)
    files = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
                   if p.is_file() and ".git" not in p.parts)
    assert files == [".envrc", ".npmrc", "index.html"]                 # only the ticked one, and ordinary files
    assert run_git(workspace, "ls-files").split() == [".npmrc", "index.html"]
    # A git folder, taking its untracked files: the same.
    app = make_app(tmp_path / "apps")
    (app / ".gitignore").write_text(".next/\nnode_modules/\n")
    run_git(app, "commit", "-qam", "Nothing ignored")
    for path in KEY_FILES:
        (app / path).parent.mkdir(parents=True, exist_ok=True)
        (app / path).write_text("TOKEN=secret\n")
    copy = tmp_path / "git-copy"
    attach.make_copy(app, copy, "studio/x-1", None, env_files=[], allowed_env=[], include_uncommitted=True)
    assert not [p for p in KEY_FILES if (copy / p).exists()]
    assert not set(KEY_FILES) & set(run_git(copy, "ls-files").split())


async def test_a_plain_folder_is_copied_without_what_its_gitignore_and_build_folders_leave_out(client, studio, tmp_path):
    folder = tmp_path / "plain"
    (folder / "node_modules" / "x").mkdir(parents=True)
    (folder / "node_modules" / "x" / "a.js").write_text("x")
    (folder / "dist").mkdir()
    (folder / "dist" / "out.js").write_text("built")
    (folder / "data").mkdir()
    (folder / "data" / "big.db").write_text("ignored by its .gitignore")
    (folder / ".gitignore").write_text("data/\n")
    (folder / "index.html").write_text("<h1>Plain</h1>")
    before = snapshot(folder)
    project = (await client.post("/api/projects/attach", json={"path": str(folder)})).json()
    workspace = studio.store.workspace(project["id"])
    assert sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
                  if p.is_file() and ".git" not in p.parts) == [".gitignore", "index.html"]
    assert project["attach"]["kind"] == "folder" and project["attach"]["studio_branch"] is None
    assert project["attach"]["framework"] == "html" and project["app"]["configured"] is False
    async with httpx.AsyncClient() as web:
        page = await web.get(project["preview_url"])
    assert "<h1>Plain</h1>" in page.text and "/__studio/bridge.js" in page.text
    assert snapshot(folder) == before


async def test_a_copy_writes_studio_json_outside_its_versions_and_refuses_an_address(client, studio, tmp_path):
    app = make_app(tmp_path / "apps", next_app=False)
    refused = await client.post("/api/projects/attach", json={"path": str(app), "address": "http://localhost:5173"})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "address_refused"
    project = (await client.post("/api/projects/attach", json={
        "path": str(app), "run": "exec python3 -m http.server $PORT --bind 127.0.0.1"})).json()
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    assert json.loads((workspace / "studio.json").read_text()) == {
        "run": "exec python3 -m http.server $PORT --bind 127.0.0.1", "restart": "never"}
    assert "studio.json" not in run_git(workspace, "ls-files")
    await wait_for(lambda: studio.app(pid).state == "running")
    await client.post(f"/api/projects/{pid}/app/stop")


async def test_the_first_start_of_an_attached_app_installs_then_runs_once(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "studio.json").write_text(json.dumps({"run": "exec python3 -m http.server $PORT --bind 127.0.0.1"}))
    studio.store.update(pid, attach={"mode": "copy", "install": "echo installing-packages-now"})
    await studio.sync_app(pid)
    await wait_for(lambda: studio.app(pid).state == "running")
    log = "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])
    assert "Installing packages first" in log and "installing-packages-now" in log
    assert (studio.store.dir(pid) / "setup.done").exists()
    await client.post(f"/api/projects/{pid}/app/restart")
    await wait_for(lambda: studio.app(pid).state == "running")
    log = "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])
    assert "installing-packages-now" not in log
    await client.post(f"/api/projects/{pid}/app/stop")


async def test_a_failed_install_leaves_the_app_crashed_with_its_output(studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "studio.json").write_text(json.dumps({"run": "exec python3 -m http.server $PORT --bind 127.0.0.1"}))
    studio.store.update(pid, attach={"mode": "copy", "install": "echo no-such-package >&2; exit 3"})
    await studio.sync_app(pid)
    await wait_for(lambda: studio.app(pid).state == "crashed")
    assert "no-such-package" in "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])
    assert not (studio.store.dir(pid) / "setup.done").exists()


# -- edit in place ---------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_in_place_keeps_its_own_history_and_leaves_the_apps_repository_alone(client, studio, tmp_path):
    app = make_app(tmp_path / "apps", next_app=False)
    git_dir = app / ".git"
    before_git = snapshot(git_dir)
    project = (await client.post("/api/projects/attach", json={"path": str(app), "mode": "in_place"})).json()
    pid, record = project["id"], project["attach"]
    assert record["mode"] == "in_place" and record["studio_branch"] is None
    assert studio.store.workspace(pid) == app.resolve()
    assert (studio.store.dir(pid) / "history.git").is_dir() and not (studio.store.dir(pid) / "workspace").exists()
    assert project["app_config"] == {} and project["name"] == "site (in place)"
    assert not (app / "studio.json").exists()
    # The first version is the folder as found, without env files, dependencies or build output.
    versions = (await client.get(f"/api/projects/{pid}/versions")).json()
    assert [v["kind"] for v in versions] == ["init"]
    names = {f["path"] for f in versions[0]["files"]}
    assert "index.html" in names and not any(n.startswith((".env", "node_modules", ".next", ".git/")) for n in names)
    # An edit through the studio lands in the folder and in the studio's history only.
    put = await client.put(f"/api/projects/{pid}/file", json={"path": "index.html", "content": "<h1>Edited</h1>\n"})
    assert put.status_code == 200 and put.json()["checkpoint"]["kind"] == "user"
    assert (app / "index.html").read_text() == "<h1>Edited</h1>\n"
    assert (await client.put(f"/api/projects/{pid}/file", json={"path": ".git/config", "content": "x"})).status_code == 400
    restore = await client.post(f"/api/projects/{pid}/restore", json={"sha": versions[0]["sha"]})
    assert restore.status_code == 200
    assert (app / "index.html").read_text().startswith("<!doctype html>")
    # The app's own repository: no new commit, index and refs byte-for-byte the same.
    assert snapshot(git_dir) == before_git
    assert run_git(app, "rev-list", "--count", "HEAD").strip() == "2"
    # Files and download are what the history keeps, not the env file or dependencies.
    files = {f["path"] for f in (await client.get(f"/api/projects/{pid}/files")).json()}
    assert "index.html" in files and ".env" not in files and not any(f.startswith("node_modules") for f in files)
    archive = zipfile.ZipFile(io.BytesIO((await client.get(f"/api/projects/{pid}/download")).content))
    assert ".env" not in archive.namelist() and "index.html" in archive.namelist()
    # The same folder cannot be edited in place twice.
    again = await client.post("/api/projects/attach", json={"path": str(app), "mode": "in_place"})
    assert again.status_code == 400 and "already edits" in again.json()["error"]["message"]
    # Templates would copy the app or write into it.
    assert (await client.post(f"/api/projects/{pid}/template", json={"name": "x", "kind": "app"})).json()["error"]["code"] == "attached_app"
    assert (await client.put(f"/api/projects/{pid}/uses-template", json={"template": "x"})).json()["error"]["code"] == "attached_app"
    # Deleting the project never deletes the folder.
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 204
    assert (app / "index.html").is_file() and (app / ".git").is_dir() and (app / ".env").is_file()
    assert snapshot(git_dir) == before_git


async def test_in_place_shows_a_server_the_studio_did_not_start(client, studio, tmp_path):
    app = make_app(tmp_path / "apps", next_app=False)
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=app,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        bad = await client.post("/api/projects/attach", json={
            "path": str(app), "mode": "in_place", "address": "http://10.0.0.5:3000"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "address_refused"
        project = (await client.post("/api/projects/attach", json={
            "path": str(app), "mode": "in_place", "address": f"localhost:{port}"})).json()
        pid = project["id"]
        assert project["app_config"] == {"address": f"http://localhost:{port}"}
        await wait_for(lambda: studio.app(pid).state == "external")
        described = (await client.get(f"/api/projects/{pid}/app")).json()
        assert described["state"] == "external" and described["address"] == f"http://localhost:{port}"
        assert described["configured"] is True and described["run"] is None
        for _ in range(40):   # the server may still be starting
            async with httpx.AsyncClient() as web:
                page = await web.get(project["preview_url"], headers={"accept": "text/html"})
            if page.status_code == 200:
                break
            await asyncio.sleep(0.1)
        assert "<h1>Hello</h1>" in page.text and "/__studio/bridge.js" in page.text
        assert 'name="studio-reload" content="off"' in page.text
        for action in ("start", "stop", "restart"):
            answer = await client.post(f"/api/projects/{pid}/app/{action}")
            assert answer.status_code == 409 and answer.json()["error"]["code"] == "external_app"
        assert server.poll() is None    # the studio left it running
        assert "server the studio did not start" in studio.agent_context(pid)
    finally:
        server.terminate()
        server.wait(5)
    async with httpx.AsyncClient() as web:
        down = await web.get(project["preview_url"], headers={"accept": "text/html"})
    assert down.status_code == 502 and "Nothing answers" in down.text


async def test_the_agent_sets_up_an_in_place_app_through_the_project_not_a_file(client, studio, tmp_path):
    app = make_app(tmp_path / "apps", next_app=False)
    (app / "index.html").unlink()
    run_git(app, "commit", "-qam", "no page")
    project = (await client.post("/api/projects/attach", json={"path": str(app), "mode": "in_place", "run": ""})).json()
    pid = project["id"]
    context = studio.agent_context(pid)
    assert "edited IN PLACE" in context and str(app.resolve()) in context and "Do not run git" in context
    assert "could not tell how this app starts" in context
    answer = await client.put(f"/api/projects/{pid}/app/config", json={
        "run": "exec python3 -m http.server $PORT --bind 127.0.0.1", "restart": "never"})
    assert answer.status_code == 200
    await wait_for(lambda: studio.app(pid).state == "running")
    assert not (app / "studio.json").exists()
    assert studio.store.get(pid)["app_config"]["restart"] == "never"
    refused = await client.put(f"/api/projects/{pid}/app/config", json={"address": "http://192.168.1.2:80"})
    assert refused.status_code == 400
    await client.post(f"/api/projects/{pid}/app/stop")


async def test_app_config_belongs_to_in_place_projects_only(client, project):
    answer = await client.put(f"/api/projects/{project['id']}/app/config", json={"run": "echo"})
    assert answer.status_code == 409 and answer.json()["error"]["code"] == "not_in_place"


async def test_a_copy_tells_the_agent_where_it_came_from(client, studio, tmp_path):
    app = make_app(tmp_path / "apps", next_app=False)
    (app / "x.txt").write_text("uncommitted")
    project = (await client.post("/api/projects/attach", json={"path": str(app), "run": ""})).json()
    context = studio.agent_context(project["id"])
    assert "safe copy" in context and str(app.resolve()) in context
    assert project["attach"]["studio_branch"] in context and "1 uncommitted change" in context


async def test_in_place_survives_a_studio_restart(settings, tmp_path):
    from studio2_server.api import create_app
    app_folder = make_app(tmp_path / "apps", next_app=False)
    first = create_app(settings)
    await first.state.studio.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://127.0.0.1:8200") as c:
            pid = (await c.post("/api/projects/attach", json={"path": str(app_folder), "mode": "in_place"})).json()["id"]
    finally:
        await first.state.studio.stop()
    second = create_app(settings)
    await second.state.studio.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://127.0.0.1:8200") as c:
            await c.put(f"/api/projects/{pid}/file", json={"path": "index.html", "content": "after restart\n"})
            versions = (await c.get(f"/api/projects/{pid}/versions")).json()
        assert [v["kind"] for v in versions] == ["user", "init"]
        assert run_git(app_folder, "rev-list", "--count", "HEAD").strip() == "2"
    finally:
        await second.state.studio.stop()


async def test_a_key_file_that_is_a_link_is_not_offered_and_a_ticked_one_not_copied_is_said(client, studio, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>x</h1>")
    (tmp_path / "real.env").write_text("KEY=secret\n")
    (folder / ".env").symlink_to(tmp_path / "real.env")
    (folder / ".env.local").write_text("LOCAL=1\n")
    found = (await client.post("/api/apps/inspect", json={"path": str(folder)})).json()
    assert found["env_files"] == [".env.local"] and found["env_links"] == [".env"]
    assert any(".env" in note and "link" in note for note in found["notes"])
    project = (await client.post("/api/projects/attach", json={
        "path": str(folder), "run": "", "env_files": [".env", ".env.local"]})).json()
    assert project["attach"]["env_files"] == [".env.local"]
    skipped = project["attach"]["env_skipped"]
    assert [item["name"] for item in skipped] == [".env"] and "link" in skipped[0]["reason"]
    assert not (studio.store.workspace(project["id"]) / ".env").exists()
