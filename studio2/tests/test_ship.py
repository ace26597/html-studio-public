"""Step 3l: make it live (CONTRACT.md, "Ship: make it live").

The rule tested hardest: the original is written to only after the person allows the card.
Tests take a byte-for-byte picture of the original (its .git included) before, while the card
waits, and after a Deny.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from studio2_server import ship as shipping

GIT = ["git", "-c", "user.name=Owner", "-c", "user.email=owner@example.com", "-c", "commit.gpgsign=false"]


@pytest.fixture(autouse=True)
def quick_address(monkeypatch):
    monkeypatch.setattr(shipping, "ADDRESS_TRIES", 2)
    monkeypatch.setattr(shipping, "ADDRESS_PAUSE", 0.0)


def run_git(folder: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=folder, capture_output=True, check=True, text=True,
                          env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}).stdout


def snapshot(folder: Path, with_git: bool = True) -> dict[str, str]:
    found = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if not with_git and (rel == ".git" or rel.startswith(".git/")):
            continue
        if path.is_file() and not path.is_symlink():
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def git_state(folder: Path) -> dict:
    return {"head": run_git(folder, "rev-parse", "HEAD").strip(),
            "branch": run_git(folder, "symbolic-ref", "--short", "HEAD").strip(),
            "refs": run_git(folder, "for-each-ref", "--format=%(refname) %(objectname)"),
            "status": run_git(folder, "status", "--porcelain=v1", "--untracked-files=all"),
            "files": snapshot(folder, with_git=False)}


def make_site(root: Path, name: str = "site", git: bool = True) -> Path:
    site = root / name
    (site / "public").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html><title>Site</title><h1>Hello</h1>\n")
    (site / "public" / "robots.txt").write_text("User-agent: *\n")
    (site / ".gitignore").write_text(".env*\nnode_modules/\n")
    if git:
        run_git(site, "init", "-q", "-b", "main")
        run_git(site, "add", "-A")
        run_git(site, "commit", "-qm", "First")
    return site


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


@pytest.fixture
def live_address():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def dead_address() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/"


async def attach(client, path: Path, mode: str = "copy") -> dict:
    response = await client.post("/api/projects/attach", json={"path": str(path), "mode": mode, "run": ""})
    assert response.status_code == 201, response.text
    return response.json()


async def set_recipe(client, pid: str, **recipe) -> dict:
    response = await client.put(f"/api/projects/{pid}/ship/recipe", json={"recipe": recipe})
    assert response.status_code == 200, response.text
    return response.json()


async def change(client, pid: str, path: str = "index.html", text: str = "<h1>Changed in the copy</h1>\n"):
    response = await client.put(f"/api/projects/{pid}/file", json={"path": path, "content": text})
    assert response.status_code == 200, response.text


async def wait_card(studio, pid: str, timeout: float = 20.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        ship_id = studio.ships.running.get(pid)
        if ship_id and ship_id in studio.ships.cards:
            return studio.ships.cards[ship_id]
        if ship_id is None and pid not in studio.ships.tasks:
            raise AssertionError("the ship ended before showing a card")
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for the card")
        await asyncio.sleep(0.05)


async def finish(studio, pid: str) -> dict:
    task = studio.ships.tasks.get(pid)
    if task:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 60)
    return studio.ships.records(pid)[0]


async def ship(client, studio, pid: str, decision: str = "allow", confirm: str = "") -> dict:
    response = await client.post(f"/api/projects/{pid}/ships")
    assert response.status_code == 202, response.text
    card = await wait_card(studio, pid)
    answer = await client.post(f"/api/projects/{pid}/ships/{card['ship_id']}/decision",
                               json={"decision": decision, "confirm": confirm})
    assert answer.status_code == 204, answer.text
    return await finish(studio, pid)


# -- the recipe -------------------------------------------------------------------------------
async def test_recipe_defaults_and_methods_follow_how_the_app_came_in(client, tmp_path):
    git_copy = await attach(client, make_site(tmp_path, "a"))
    folder_copy = await attach(client, make_site(tmp_path, "b", git=False))
    in_place = await attach(client, make_site(tmp_path, "c"), mode="in_place")
    views = [(await client.get(f"/api/projects/{p['id']}/ship")).json() for p in (git_copy, folder_copy, in_place)]
    assert [v["methods"] for v in views] == [["branch", "merge"], ["files"], ["none"]]
    assert [v["recipe"]["method"] for v in views] == ["branch", "files", "none"]
    assert not any(v["confirmed"] for v in views)
    bad = await client.put(f"/api/projects/{folder_copy['id']}/ship/recipe", json={"recipe": {"method": "merge"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_recipe"
    blank = (await client.post("/api/projects", json={"name": "Blank"})).json()
    assert blank["ship"] is None
    assert (await client.get(f"/api/projects/{blank['id']}/ship")).status_code == 404


async def test_no_ship_without_a_recipe(client, tmp_path):
    project = await attach(client, make_site(tmp_path))
    response = await client.post(f"/api/projects/{project['id']}/ships")
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_recipe"


async def test_agent_recipe_is_confirmed_on_a_card_even_when_everything_is_allowed(client, studio, tmp_path):
    project = await attach(client, make_site(tmp_path))
    pid = project["id"]
    await client.patch(f"/api/projects/{pid}", json={"approvals": "allow"})
    text = 'propose ship recipe {"method": "merge", "checks": ["test -f index.html"], "after": ["true"]}'
    started = await client.post(f"/api/projects/{pid}/turns", json={"text": text})
    turn_id = started.json()["turn_id"]
    for _ in range(200):
        asks = [e for e in studio.log(pid).read() if e["type"] == "approval_request" and e["turn_id"] == turn_id]
        if asks:
            break
        await asyncio.sleep(0.05)
    assert asks and asks[0]["tool"] == "ship_recipe" and "fast-forward" in asks[0]["detail"]
    await client.post(f"/api/projects/{pid}/turns/{turn_id}/approvals/{asks[0]['approval_id']}",
                      json={"decision": "deny"})
    await studio.turns.wait(pid)
    assert (await client.get(f"/api/projects/{pid}/ship")).json()["confirmed"] is False
    # Allowed this time; the same recipe again asks nothing; a changed one asks again.
    for decision, expected_asks in (("allow", 1), (None, 0)):
        started = await client.post(f"/api/projects/{pid}/turns", json={"text": text})
        turn_id = started.json()["turn_id"]
        if decision:
            for _ in range(200):
                asks = [e for e in studio.log(pid).read() if e["type"] == "approval_request" and e["turn_id"] == turn_id]
                if asks:
                    break
                await asyncio.sleep(0.05)
            await client.post(f"/api/projects/{pid}/turns/{turn_id}/approvals/{asks[0]['approval_id']}",
                              json={"decision": decision})
        await studio.turns.wait(pid)
        asks = [e for e in studio.log(pid).read() if e["type"] == "approval_request" and e["turn_id"] == turn_id]
        assert len(asks) == expected_asks
    view = (await client.get(f"/api/projects/{pid}/ship")).json()
    assert view["confirmed"] and view["recipe"]["method"] == "merge" and view["recipe"]["checks"] == ["test -f index.html"]
    assert view["confirmed_by"].startswith("agent")


# -- the branch way -----------------------------------------------------------------------------
async def test_branch_way_leaves_a_branch_and_changes_nothing_else(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid, branch = project["id"], project["attach"]["studio_branch"]
    await set_recipe(client, pid, method="branch", after=["touch should-not-run"], address=dead_address())
    await change(client, pid)
    before = git_state(site)
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    after = git_state(site)
    assert after["head"] == before["head"] and after["branch"] == "main"
    assert after["files"] == before["files"] and after["status"] == before["status"] == ""
    copy_head = studio.store.get(pid)["head"]
    assert run_git(site, "rev-parse", f"refs/heads/{branch}").strip() == copy_head
    assert run_git(site, "show", f"{branch}:index.html") == "<h1>Changed in the copy</h1>\n"
    assert not (site / "should-not-run").exists()       # after-commands do not run for a branch
    assert record["after"] == [] and record["address"] is None
    assert "git merge --ff-only" in studio.log(pid).read()[-1]["merge_help"]
    again = await client.post(f"/api/projects/{pid}/ships")
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_ship"


# -- the merge way ------------------------------------------------------------------------------
async def test_merge_way_changes_the_original_runs_after_and_checks_the_address(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    marker = tmp_path / "after-ran.txt"
    await set_recipe(client, pid, method="merge", checks=["test -f index.html"],
                     after=[f"cat index.html > {marker}"], address=live_address)
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / "index.html").read_text() == "<h1>Changed in the copy</h1>\n"
    assert marker.read_text() == "<h1>Changed in the copy</h1>\n"
    assert run_git(site, "rev-parse", "HEAD").strip() == studio.store.get(pid)["head"]
    assert run_git(site, "status", "--porcelain") == ""
    assert record["checks"][0]["exit_code"] == 0 and record["address"]["ok"]
    types = [e["type"] for e in studio.log(pid).read() if e["type"].startswith("ship_") and e["type"] != "ship_recipe"]
    assert types[0] == "ship_started" and "ship_ready" in types and types[-1] == "ship_finished"
    assert (await client.get(f"/api/projects/{pid}/ships")).json()[0]["id"] == record["id"]


async def test_failed_address_goes_back_by_itself_exactly(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    runs = tmp_path / "after-runs.txt"
    await set_recipe(client, pid, method="merge", after=[f"echo run >> {runs}"], address=dead_address())
    await change(client, pid)
    await change(client, pid, "public/new.html", "<p>new</p>\n")
    before = git_state(site)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert git_state(site) == before          # commit, refs, status, every file and its content
    assert runs.read_text() == "run\nrun\n"   # after-commands ran, then ran again after going back


async def test_deny_writes_nothing_and_nothing_is_written_before_allow(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", checks=["true"], after=["touch after.txt"], address=live_address)
    await change(client, pid)
    before = snapshot(site)
    response = await client.post(f"/api/projects/{pid}/ships")
    card = await wait_card(studio, pid)
    assert snapshot(site) == before               # checks ran, the card is up: still untouched
    assert card["files"] == [{"path": "index.html", "status": "modified"}]
    assert card["commands"] == [{"command": "touch after.txt", "where": str(site)}]
    assert any("fast-forward main" in line for line in card["writes"])
    # While the card is open nothing else may change the project.
    busy = await client.post(f"/api/projects/{pid}/turns", json={"text": "hello"})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "ship_running"
    assert (await client.put(f"/api/projects/{pid}/file", json={"path": "x.txt", "content": "x"})).status_code == 409
    await client.post(f"/api/projects/{pid}/ships/{response.json()['ship_id']}/decision", json={"decision": "deny"})
    record = await finish(studio, pid)
    assert record["result"] == "denied" and snapshot(site) == before


async def test_protected_paths_need_the_typed_project_name(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", protected=["public/"], address=live_address)
    await change(client, pid, "public/robots.txt", "User-agent: *\nDisallow: /\n")
    await client.post(f"/api/projects/{pid}/ships")
    card = await wait_card(studio, pid)
    assert card["protected"] == ["public/robots.txt"] and card["needs_name"]
    url = f"/api/projects/{pid}/ships/{card['ship_id']}/decision"
    refused = await client.post(url, json={"decision": "allow", "confirm": "wrong"})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "confirm_needed"
    assert (site / "public" / "robots.txt").read_text() == "User-agent: *\n"
    ok = await client.post(url, json={"decision": "allow", "confirm": project["name"]})
    assert ok.status_code == 204
    assert (await finish(studio, pid))["result"] == "shipped"
    assert "Disallow" in (site / "public" / "robots.txt").read_text()


async def test_merge_is_refused_with_uncommitted_changes_or_when_the_original_moved_on(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge")
    await change(client, pid)
    (site / "index.html").write_text("work in progress\n")
    before = snapshot(site)
    refused = await client.post(f"/api/projects/{pid}/ships")
    assert refused.status_code == 409 and "uncommitted changes" in refused.json()["error"]["message"]
    assert snapshot(site) == before
    run_git(site, "checkout", "-q", "index.html")
    (site / "public" / "other.txt").write_text("someone else\n")
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "Moved on")
    before = snapshot(site)
    refused = await client.post(f"/api/projects/{pid}/ships")
    body = refused.json()["error"]
    assert refused.status_code == 409 and "moved on" in body["message"] and "up to date" in body["message"]
    assert snapshot(site) == before
    # The next step: bring the copy up to date (reads the original only), then the merge works.
    updated = await client.post(f"/api/projects/{pid}/ship/update-copy")
    assert updated.status_code == 200 and updated.json()["updated"], updated.text
    assert snapshot(site) == before
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / "index.html").read_text() == "<h1>Changed in the copy</h1>\n"
    assert (site / "public" / "other.txt").read_text() == "someone else\n"


async def test_update_copy_stops_on_a_conflict(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await change(client, pid)
    (site / "index.html").write_text("their version\n")
    run_git(site, "commit", "-qam", "Theirs")
    head = studio.store.get(pid)["head"]
    response = await client.post(f"/api/projects/{pid}/ship/update-copy")
    assert response.status_code == 409 and response.json()["error"]["code"] == "update_conflict"
    assert "index.html" in response.json()["error"]["message"]
    assert studio.store.get(pid)["head"] == head


async def test_failed_check_writes_nothing(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", checks=["echo checking", "exit 3"])
    await change(client, pid)
    before = snapshot(site)
    assert (await client.post(f"/api/projects/{pid}/ships")).status_code == 202
    record = await finish(studio, pid)
    assert record["result"] == "checks_failed" and snapshot(site) == before
    assert [c["exit_code"] for c in record["checks"]] == [0, 3] and "checking" in record["checks"][0]["output"]
    assert record["decision"] is None


async def test_check_runs_without_studio_secrets_and_with_a_time_limit(client, studio, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO2_ACCESS_KEY", "secret-value")
    monkeypatch.setenv("SOME_API_KEY", "secret-value")
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", checks=["env", "sleep 30"], timeout=1)
    await change(client, pid)
    await client.post(f"/api/projects/{pid}/ships")
    record = await finish(studio, pid)
    assert "secret-value" not in record["checks"][0]["output"] and "PATH=" in record["checks"][0]["output"]
    assert record["checks"][1]["timed_out"] and record["result"] == "checks_failed"


# -- a plain folder and a folder in place -------------------------------------------------------
async def test_plain_folder_copy_ships_files_and_goes_back(client, studio, tmp_path, live_address):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files", address=live_address)
    await change(client, pid)
    await change(client, pid, "public/new.html", "<p>new</p>\n")
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / "index.html").read_text() == "<h1>Changed in the copy</h1>\n"
    assert (site / "public" / "new.html").read_text() == "<p>new</p>\n"
    assert (await client.post(f"/api/projects/{pid}/ships")).json()["error"]["code"] == "nothing_to_ship"
    # A second ship whose address fails: every file goes back, the new one is removed again.
    await set_recipe(client, pid, method="files", address=dead_address())
    await change(client, pid, "index.html", "<h1>Third</h1>\n")
    await change(client, pid, "public/later.html", "<p>later</p>\n")
    before = snapshot(site)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back" and snapshot(site) == before


async def test_plain_folder_refuses_when_the_original_file_changed(client, studio, tmp_path):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files")
    await change(client, pid)
    (site / "index.html").write_text("edited in the original\n")
    refused = await client.post(f"/api/projects/{pid}/ships")
    assert refused.status_code == 409 and "index.html" in refused.json()["error"]["message"]
    assert (site / "index.html").read_text() == "edited in the original\n"


async def test_in_place_ships_checks_and_after_commands(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    project = await attach(client, site, mode="in_place")
    pid = project["id"]
    assert (await client.post(f"/api/projects/{pid}/ships")).json()["error"]["code"] == "no_recipe"
    await set_recipe(client, pid, checks=["test -f index.html"], after=["cp index.html built.html"],
                     address=live_address)
    assert (await client.post(f"/api/projects/{pid}/ships")).json()["error"]["code"] == "nothing_to_ship"
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / "built.html").read_text() == "<h1>Changed in the copy</h1>\n"


# -- turns and ships ---------------------------------------------------------------------------
async def test_agent_asks_and_the_ship_starts_when_its_turn_ends(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", address=live_address)
    await change(client, pid)
    started = await client.post(f"/api/projects/{pid}/turns", json={"text": "please make it live"})
    assert started.status_code == 202
    await studio.turns.wait(pid)
    card = await wait_card(studio, pid)
    started_event = [e for e in studio.log(pid).read() if e["type"] == "ship_started"][-1]
    finished_turn = [e for e in studio.log(pid).read() if e["type"] == "turn_finished"][-1]
    assert started_event["by"] == "agent" and started_event["seq"] > finished_turn["seq"]
    queued = await client.post(f"/api/projects/{pid}/turns", json={"text": "next", "queue": True})
    assert "queued_id" in queued.json()
    await client.post(f"/api/projects/{pid}/ships/{card['ship_id']}/decision", json={"decision": "allow"})
    record = await finish(studio, pid)
    assert record["result"] == "shipped" and record["by"] == "agent"
    await asyncio.sleep(0.2)
    await studio.turns.wait(pid)
    assert any(e["type"] == "turn_started" and e.get("text") == "next" for e in studio.log(pid).read())


async def test_ship_is_refused_while_a_turn_runs(client, studio, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO2_FAKE_DELAY", "0.05")
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge")
    await client.post(f"/api/projects/{pid}/turns", json={"text": "slow"})
    response = await client.post(f"/api/projects/{pid}/ships")
    assert response.status_code == 409 and response.json()["error"]["code"] == "turn_running"
    await client.post(f"/api/projects/{pid}/turns/{studio.turns.active[pid]}/stop")
    await studio.turns.wait(pid)


async def test_a_restart_during_a_folder_copy_puts_the_files_back(client, studio, tmp_path):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files")
    await change(client, pid)
    before = snapshot(site)
    planned = shipping.plan(studio.store.get(pid), studio.store.workspace(pid))
    record = {"id": "s_0000beef", "project": pid, "started_at": "x", "phase": "applying", "result": None,
              "plan": {k: planned[k] for k in ("method", "base", "head", "before", "where", "writes")},
              "attach": {"path": str(site)}}
    studio.ships.save(pid, record)
    shipping._apply(studio.store.get(pid), studio.store.workspace(pid), planned,
                    studio.ships.folder(pid, record["id"]) / "backup")
    assert snapshot(site) != before
    await studio.ships.recover(pid)
    assert snapshot(site) == before
    assert studio.ships.record(pid, "s_0000beef")["result"] == "interrupted"


def test_protected_patterns():
    hits = shipping.protected_hits(["a/.env.local", "db/migrations/1.sql", "src/app.js", ".env"],
                                   [".env*", "db/migrations/"])
    assert hits == ["a/.env.local", "db/migrations/1.sql", ".env"]


def test_writes_to_the_original_happen_only_in_apply_and_go_back():
    """Every git write and every file write, copy or removal in ship.py sits in _apply or _go_back
    (or writes the studio's own records). _apply is called only after an Allow (see _run)."""
    import ast
    tree = ast.parse(Path(shipping.__file__).read_text())
    writers = {"_git_write", "write_bytes", "unlink", "copy2", "chmod", "rmtree"}
    found: dict[str, set[str]] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                    if name in writers:
                        found.setdefault(function.name, set()).add(name)
    assert set(found) - {"_git_write"} == {"_apply", "_go_back"}, found
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "_apply"]
    assert len(uses) == 1      # the one use, in Shipper._run after the decision
    run = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "_run")
    text = ast.unparse(run)
    assert text.index("allowed = await future") < text.index("_apply")
    assert text.index("if not allowed") < text.index("_apply")
