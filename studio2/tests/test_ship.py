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


# -- going back never discards what the studio did not put there ---------------------------------
def shipped_by_merge(tmp_path: Path) -> tuple[Path, dict, dict, str, str]:
    """An original whose main was fast-forwarded to a studio branch, as a merge ship leaves it."""
    site = make_site(tmp_path)
    before = run_git(site, "rev-parse", "HEAD").strip()
    run_git(site, "checkout", "-qb", "studio/site-1")
    (site / "index.html").write_text("<h1>Shipped</h1>\n")
    run_git(site, "commit", "-qam", "Shipped")
    head = run_git(site, "rev-parse", "HEAD").strip()
    run_git(site, "checkout", "-q", "main")
    run_git(site, "merge", "-q", "--ff-only", "studio/site-1")
    project = {"attach": {"path": str(site), "branch": "main", "studio_branch": "studio/site-1"}}
    planned = {"method": "merge", "head": head, "before": {"head": before, "branch": "main", "ref": None}}
    return site, project, planned, before, head


def test_merge_go_back_keeps_the_persons_edits_to_other_files(tmp_path):
    site, project, planned, before, _head = shipped_by_merge(tmp_path)
    with (site / "public" / "robots.txt").open("a") as handle:
        handle.write("Disallow: /drafts\n")       # edited while the after-commands ran
    (site / "notes.txt").write_text("mine\n")    # and an untracked file
    lines = shipping._go_back(project, planned, tmp_path / "backup")
    assert run_git(site, "rev-parse", "HEAD").strip() == before
    assert (site / "index.html").read_text() == "<!doctype html><title>Site</title><h1>Hello</h1>\n"
    assert (site / "public" / "robots.txt").read_text() == "User-agent: *\nDisallow: /drafts\n"
    assert (site / "notes.txt").read_text() == "mine\n"
    assert not run_git(site, "branch", "--list", "studio/site-1").strip()
    assert any("back at" in line for line in lines)


def test_merge_go_back_refuses_to_lose_an_edit_to_a_shipped_file(tmp_path):
    site, project, planned, _before, head = shipped_by_merge(tmp_path)
    (site / "index.html").write_text("edited by hand after the ship\n")
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, planned, tmp_path / "backup")
    assert "index.html" in caught.value.message
    assert run_git(site, "rev-parse", "HEAD").strip() == head
    assert (site / "index.html").read_text() == "edited by hand after the ship\n"


def test_merge_go_back_leaves_commits_made_since_the_ship(tmp_path):
    site, project, planned, before, head = shipped_by_merge(tmp_path)
    (site / "later.txt").write_text("committed after the ship\n")
    run_git(site, "add", "later.txt")
    run_git(site, "commit", "-qm", "Later")
    now = run_git(site, "rev-parse", "HEAD").strip()
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, planned, tmp_path / "backup")
    message = caught.value.message
    assert "could not be undone automatically" in message and before[:7] in message and now[:7] in message
    assert run_git(site, "rev-parse", "HEAD").strip() == now
    assert (site / "later.txt").exists()
    # The studio branch the ship fetched is left as it is too: the person settles this by hand.
    assert run_git(site, "rev-parse", "refs/heads/studio/site-1").strip() == head


def test_branch_go_back_leaves_a_branch_someone_moved(tmp_path):
    site, project, planned, _before, head = shipped_by_merge(tmp_path)
    run_git(site, "reset", "-q", "--hard", planned["before"]["head"])   # main as before: a branch ship
    run_git(site, "checkout", "-q", "studio/site-1")
    (site / "extra.txt").write_text("x\n")
    run_git(site, "add", "extra.txt")
    run_git(site, "commit", "-qm", "On the studio branch, by hand")
    moved = run_git(site, "rev-parse", "HEAD").strip()
    run_git(site, "checkout", "-q", "main")
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, {**planned, "method": "branch"}, tmp_path / "backup")
    assert "studio/site-1" in caught.value.message
    assert run_git(site, "rev-parse", "refs/heads/studio/site-1").strip() == moved != head


def test_files_go_back_keeps_a_file_edited_after_the_ship_wrote_it(tmp_path):
    site = make_site(tmp_path, git=False)
    backup = tmp_path / "ship" / "backup"
    (backup / "public").mkdir(parents=True)
    (backup / "index.html").write_text("<h1>Before</h1>\n")
    (backup / "public" / "robots.txt").write_text("User-agent: *\n")
    # What the ship wrote, as _apply records it.
    (site / "index.html").write_text("<h1>Shipped</h1>\n")
    (site / "public" / "robots.txt").write_text("Shipped robots\n")
    (site / "public" / "new.html").write_text("<p>new</p>\n")
    applied = [{"path": "index.html", "status": "modified", "wrote": shipping._sha(b"<h1>Shipped</h1>\n")},
               {"path": "public/robots.txt", "status": "modified", "wrote": shipping._sha(b"Shipped robots\n")},
               {"path": "public/new.html", "status": "added", "wrote": shipping._sha(b"<p>new</p>\n")}]
    (backup.parent / "applied.json").write_text(json.dumps({"files": applied}))
    (site / "index.html").write_text("edited by hand after the ship\n")
    project = {"attach": {"path": str(site)}}
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, {"method": "files"}, backup)
    assert "index.html" in caught.value.message
    assert (site / "index.html").read_text() == "edited by hand after the ship\n"
    assert (site / "public" / "robots.txt").read_text() == "User-agent: *\n"
    assert not (site / "public" / "new.html").exists()


async def test_a_restart_after_a_merge_ship_leaves_commits_made_since(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge")
    await change(client, pid)
    planned = shipping.plan(studio.store.get(pid), studio.store.workspace(pid))
    record = {"id": "s_0000cafe", "project": pid, "started_at": "x", "phase": "after", "result": None,
              "plan": {k: planned[k] for k in ("method", "base", "head", "before", "where", "writes")},
              "attach": {"path": str(site)}}
    studio.ships.save(pid, record)
    shipping._apply(studio.store.get(pid), studio.store.workspace(pid), planned,
                    studio.ships.folder(pid, record["id"]) / "backup")
    (site / "later.txt").write_text("mine\n")
    run_git(site, "add", "later.txt")
    run_git(site, "commit", "-qm", "Mine, after the ship")
    mine = run_git(site, "rev-parse", "HEAD").strip()
    await studio.ships.recover(pid)
    assert run_git(site, "rev-parse", "HEAD").strip() == mine
    done = studio.ships.record(pid, "s_0000cafe")
    assert done["result"] == "interrupted" and "could not be undone automatically" in done["message"]


async def test_a_restart_after_a_merge_ship_goes_back_when_nothing_moved(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge")
    await change(client, pid)
    before = git_state(site)
    planned = shipping.plan(studio.store.get(pid), studio.store.workspace(pid))
    record = {"id": "s_0000f00d", "project": pid, "started_at": "x", "phase": "after", "result": None,
              "plan": {k: planned[k] for k in ("method", "base", "head", "before", "where", "writes")},
              "attach": {"path": str(site)}}
    studio.ships.save(pid, record)
    shipping._apply(studio.store.get(pid), studio.store.workspace(pid), planned,
                    studio.ships.folder(pid, record["id"]) / "backup")
    assert git_state(site) != before
    await studio.ships.recover(pid)
    assert git_state(site) == before


@pytest.mark.parametrize("git, method", [(True, "branch"), (True, "merge"), (False, "files")])
async def test_an_env_file_in_the_change_is_never_shipped(client, studio, tmp_path, git, method):
    """A copy keeps env files out of its versions; one forced into a version anyway (or a copy made
    before that rule) must not reach the original."""
    site = make_site(tmp_path, git=git)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method=method)
    await change(client, pid)
    workspace = studio.store.workspace(pid)
    (workspace / "config").mkdir()
    (workspace / "config" / ".env.production").write_text("KEY=secret\n")
    run_git(workspace, "add", "-f", "config/.env.production")
    run_git(workspace, "commit", "-qm", "Forced")
    before = snapshot(site)
    refused = await client.post(f"/api/projects/{pid}/ships")
    body = refused.json()["error"]
    assert refused.status_code == 409 and body["code"] == "ship_refused"
    assert "config/.env.production" in body["message"]
    assert snapshot(site) == before


@pytest.mark.parametrize("method", ["branch", "merge"])
async def test_a_key_in_any_version_is_never_shipped_even_when_a_later_version_removed_it(client, studio, tmp_path,
                                                                                           method):
    """A branch or merge ship carries every version, not just the end result."""
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method=method)
    await change(client, pid)
    workspace = studio.store.workspace(pid)
    (workspace / ".envrc").write_text("export KEY=supersecret123\n")
    run_git(workspace, "add", "-f", ".envrc")
    run_git(workspace, "commit", "-qm", "Forced")
    run_git(workspace, "rm", "-q", "--cached", ".envrc")
    run_git(workspace, "commit", "-qm", "Removed again")
    refused = await client.post(f"/api/projects/{pid}/ships")
    body = refused.json()["error"]
    assert refused.status_code == 409 and body["code"] == "ship_refused", body
    assert ".envrc" in body["message"] and "every version" in body["message"]
    assert "remove them from its versions" not in body["message"]
    assert "supersecret123" not in run_git(site, "log", "--all", "-p")


async def test_editing_a_tracked_env_example_ships_like_any_file(client, studio, tmp_path, live_address):
    site = make_site(tmp_path)
    (site / ".env.example").write_text("API_URL=\n")
    run_git(site, "add", "-f", ".env.example")
    run_git(site, "commit", "-qm", "Example settings")
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", address=live_address)
    await change(client, pid, ".env.example", "API_URL=\nNEW_FLAG=\n")
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / ".env.example").read_text() == "API_URL=\nNEW_FLAG=\n"


async def test_a_key_file_the_original_already_tracks_is_not_refused(client, studio, tmp_path, live_address):
    """His repository already holds it; the copy changing it is an ordinary change to his file."""
    site = make_site(tmp_path)
    (site / "config").mkdir()
    (site / "config" / ".env").write_text("MODE=dev\n")
    run_git(site, "add", "-f", "config/.env")
    run_git(site, "commit", "-qm", "Tracked settings")
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="branch")
    await change(client, pid, "config/.env", "MODE=prod\n")
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]


async def test_a_plain_folder_ships_a_new_example_file_but_not_a_key_file(client, studio, tmp_path, live_address):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files", address=live_address)
    workspace = studio.store.workspace(pid)
    (workspace / ".env.sample").write_text("API_URL=\n")
    run_git(workspace, "add", "-f", ".env.sample")
    run_git(workspace, "commit", "-qm", "Sample settings")
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped", record["message"]
    assert (site / ".env.sample").read_text() == "API_URL=\n"


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


def test_key_free_names_are_the_usual_example_files_only():
    from studio2_server import keyfiles
    for path in [".env.example", "api/.env.sample", ".env.local.template", ".env.dist", ".env.defaults",
                 ".dev.vars.example"]:
        assert keyfiles.is_key_free(path), path
    for path in [".env", ".env.local", ".envrc", "id_rsa", ".env.example/real", ".env/x.example", "README.example"]:
        assert not keyfiles.is_key_free(path), path


async def test_bringing_the_copy_up_to_date_runs_no_program_the_copy_names(client, studio, tmp_path):
    """The fetch reads the original by its path; the copy's config is the agent's to write."""
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    marker = tmp_path / "ran"
    command = f"sh -c 'touch {marker}; exec git-upload-pack \"$@\"' upload"
    for key, value in ((f"remote.{site}.url", str(site)), (f"remote.{site}.uploadpack", command),
                       ("core.alternateRefsCommand", f"touch {marker}")):
        subprocess.run(["git", "config", key, value], cwd=workspace, check=True)
    (site / "public" / "other.txt").write_text("someone else\n")
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "Moved on")
    response = await client.post(f"/api/projects/{pid}/ship/update-copy")
    assert not marker.exists()
    assert response.status_code == 200 and response.json()["updated"], response.text
    # The original's path rewritten to git's command transport: the fetch is refused, nothing runs.
    for key, value in (("protocol.ext.allow", "always"), (f"url.ext::sh -c touch% {marker}.insteadOf", str(site))):
        subprocess.run(["git", "config", key, value], cwd=workspace, check=True)
    (site / "public" / "third.txt").write_text("again\n")
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "Again")
    response = await client.post(f"/api/projects/{pid}/ship/update-copy")
    assert not marker.exists()
    assert response.status_code == 409 and response.json()["error"]["code"] == "update_failed"


# -- going back says what happened, and keeps going past a file it must leave --------------------
def test_go_back_says_the_branch_changed_when_the_commit_did_not(tmp_path):
    site, project, planned, _before, head = shipped_by_merge(tmp_path)
    run_git(site, "checkout", "-q", "-b", "feature")
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, planned, tmp_path / "backup")
    message = caught.value.message
    assert "feature" in message and "main" in message and f"{head[:7]}, not at {head[:7]}" not in message
    assert run_git(site, "rev-parse", "HEAD").strip() == head


def test_go_back_does_not_blame_the_person_for_an_after_command(tmp_path):
    site, project, planned, _before, _head = shipped_by_merge(tmp_path)
    (site / "index.html").write_text("rebuilt\n")
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back(project, planned, tmp_path / "backup")
    assert "by you or by an after-command" in caught.value.message


def test_go_back_says_staged_files_are_kept_but_no_longer_staged(tmp_path):
    site, project, planned, before, _head = shipped_by_merge(tmp_path)
    (site / "new.txt").write_text("mine\n")
    run_git(site, "add", "new.txt")
    lines = shipping._go_back(project, planned, tmp_path / "backup")
    assert run_git(site, "rev-parse", "HEAD").strip() == before
    assert (site / "new.txt").read_text() == "mine\n"
    assert any("new.txt" in line and "no longer staged" in line for line in lines), lines


def test_files_go_back_carries_on_past_a_file_that_became_a_link(tmp_path):
    site = make_site(tmp_path, git=False)
    backup = tmp_path / "ship" / "backup"
    (backup / "public").mkdir(parents=True)
    (backup / "index.html").write_text("<h1>Before</h1>\n")
    (backup / "public" / "robots.txt").write_text("User-agent: *\n")
    (site / "index.html").write_text("<h1>Shipped</h1>\n")
    (site / "public" / "robots.txt").write_text("Shipped robots\n")
    applied = [{"path": "index.html", "status": "modified", "wrote": shipping._sha(b"<h1>Shipped</h1>\n")},
               {"path": "public/robots.txt", "status": "modified", "wrote": shipping._sha(b"Shipped robots\n")}]
    (backup.parent / "applied.json").write_text(json.dumps({"files": applied}))
    (site / "public" / "robots.txt").unlink()
    (site / "public" / "robots.txt").symlink_to(tmp_path / "elsewhere.txt")   # last in the list, first undone
    with pytest.raises(shipping.ShipError) as caught:
        shipping._go_back({"attach": {"path": str(site)}}, {"method": "files"}, backup)
    assert caught.value.code == "back_failed" and "public/robots.txt" in caught.value.message
    assert (site / "index.html").read_text() == "<h1>Before</h1>\n"        # put back all the same
    assert (site / "public" / "robots.txt").is_symlink()


def test_a_key_file_added_in_a_merge_itself_is_seen(tmp_path):
    """`git log --name-only` leaves out what a merge commit changes unless asked (-m), so a key
    file that is in neither parent and is added in the merge itself was not seen, though it is in
    the tree the ship carries."""
    import subprocess

    from studio2_server import ship

    repo = tmp_path / "copy"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "-C", str(repo), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    git("add", "a.txt"); git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    git("add", "s.txt"); git("commit", "-q", "-m", "side")
    git("checkout", "-q", "main")
    (repo / "m.txt").write_text("m\n")
    git("add", "m.txt"); git("commit", "-q", "-m", "main")
    git("merge", "-q", "--no-commit", "--no-ff", "side")
    (repo / ".env").write_text("SECRET=only_in_the_merge\n")
    git("add", "-f", ".env"); git("commit", "-q", "-m", "merge")
    head = git("rev-parse", "HEAD")

    refused = ship._key_file_refusal(repo, "merge", base, head, [])
    assert refused and ".env" in refused[0]
    # A history with no key file is still let through.
    assert ship._key_file_refusal(repo, "merge", base, git("rev-parse", "HEAD^1"), []) is None


# -- what the person reads: messages and cards say only what is true for the method -------------
SAM_GIT = "git -c user.name=Sam -c user.email=sam@example.com -c commit.gpgsign=false"


def _reads_cleanly(message: str) -> None:
    assert ".." not in message.replace("...", ""), message
    assert "FAILED" not in message and "file(s)" not in message, message


async def test_merge_back_failed_says_only_what_is_true(client, studio, tmp_path):
    """An after-command edits the shipped file and fails: going back refuses, and the message says
    the ship's change is still there, without claiming a backup or a half-changed original."""
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", after=["echo rebuilt >> index.html; exit 1"])
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "back_failed", record["message"]
    message = record["message"]
    _reads_cleanly(message)
    assert message.startswith("An after-command failed.")
    assert "half" not in message and "overwritten" not in message and "changed nothing" not in message
    assert "index.html" in message and "`git reset --keep " in message
    assert "still" in message      # the ship's change is still in the original


async def test_merge_back_failed_after_a_commit_reads_cleanly(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", after=[f"{SAM_GIT} commit -q --allow-empty -m mine; exit 1"])
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "back_failed", record["message"]
    _reads_cleanly(record["message"])
    assert "could not be undone automatically" in record["message"]
    assert "half" not in record["message"] and "overwritten" not in record["message"]


async def test_files_back_failed_names_what_was_kept_and_put_back(client, studio, tmp_path):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files", after=["echo rebuilt >> index.html; exit 1"])
    await change(client, pid)
    await change(client, pid, "public/new.html", "<p>new</p>\n")
    record = await ship(client, studio, pid)
    assert record["result"] == "back_failed", record["message"]
    message = record["message"]
    _reads_cleanly(message)
    assert "1 file in" in message and "index.html" in message and "half" not in message


async def test_rolled_back_says_staged_files_are_no_longer_staged(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    flag = tmp_path / "ran-once"
    # Stages a file of the person's, then fails the first time only.
    await set_recipe(client, pid, method="merge",
                     after=[f"if [ -f {flag} ]; then exit 0; fi; touch {flag}; echo mine > notes.txt; "
                            "git add notes.txt; exit 1"])
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert "notes.txt" in record["message"] and "no longer staged" in record["message"], record["message"]
    _reads_cleanly(record["message"])


async def test_a_merge_with_nothing_after_does_not_say_anything_ran(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge")
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped" and record["message"] == "The change is in the original.", record["message"]
    await set_recipe(client, pid, method="merge", after=["true"])
    await change(client, pid, text="<h1>Again</h1>\n")
    record = await ship(client, studio, pid)
    assert record["message"] == "The change is in the original and the after-commands ran.", record["message"]


async def test_in_place_with_nothing_to_run_does_not_say_commands_ran(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site, mode="in_place")
    pid = project["id"]
    await set_recipe(client, pid)
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "shipped"
    assert "after-commands ran" not in record["message"] and "checks" not in record["message"].lower(), record["message"]


async def card_for(client, studio, pid: str) -> dict:
    response = await client.post(f"/api/projects/{pid}/ships")
    assert response.status_code == 202, response.text
    card = await wait_card(studio, pid)
    await client.post(f"/api/projects/{pid}/ships/{card['ship_id']}/decision", json={"decision": "deny"})
    await finish(studio, pid)
    return card


async def test_each_card_states_only_the_limits_and_going_back_of_its_method(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await change(client, pid)

    await set_recipe(client, pid, method="branch", after=["npm run build"])
    card = await card_for(client, studio, pid)
    text = " ".join(card["limits"])
    assert "Going back" not in text and "commands do" not in text and "never pushes" in text, text
    assert card["method_label"] == shipping.METHODS["branch"]

    await set_recipe(client, pid, method="merge", after=["npm run build"])
    card = await card_for(client, studio, pid)
    text = " ".join(card["limits"])
    assert "Going back restores the branch only" in text and "files and the branch" not in text, text
    assert "after-commands do" in text, text
    assert "where it was, then runs the after-commands again." in card["going_back"], card["going_back"]
    assert "1 file in the folder changes" in " ".join(card["writes"]), card["writes"]
    assert card["method_label"] == "Merge into the original's checked-out branch (fast-forward only)"

    await set_recipe(client, pid, method="merge")
    card = await card_for(client, studio, pid)
    assert "nothing is restarted or sent anywhere" in " ".join(card["limits"]), card["limits"]
    assert "after the merge fails" not in card["going_back"], card["going_back"]


async def test_an_in_place_card_says_nothing_about_going_back_in_its_limits(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site, mode="in_place")
    pid = project["id"]
    await set_recipe(client, pid, after=["true"])
    await change(client, pid)
    card = await card_for(client, studio, pid)
    assert not any("Going back" in line for line in card["limits"]), card["limits"]
    assert card["method_label"] == "Nothing to move: the folder is edited in place"


async def test_a_files_card_counts_in_words(client, studio, tmp_path):
    site = make_site(tmp_path, git=False)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="files")
    await change(client, pid)
    card = await card_for(client, studio, pid)
    writes = " ".join(card["writes"])
    assert "file(s)" not in writes and "overwrite 1 file" in writes, writes
    assert "Going back restores the files only" in " ".join(card["limits"]), card["limits"]


async def test_a_failed_branch_ship_runs_no_command_of_the_recipe(client, studio, tmp_path, monkeypatch):
    """A branch is only left there: its after-commands and go-back commands never run, so a branch ship
    whose apply step fails must not run them while going back, nor say they ran (commit 4e4b39c4 did)."""
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid, branch = project["id"], project["attach"]["studio_branch"]
    after_mark, back_mark = tmp_path / "after-ran", tmp_path / "back-ran"
    await set_recipe(client, pid, method="branch", after=[f"touch {after_mark}", "touch after-here"],
                     back=[f"touch {back_mark}", "touch back-here"])
    await change(client, pid)
    real_apply = shipping._apply

    def apply_then_fail(*args):
        real_apply(*args)                          # the branch is really fetched, then the step fails
        raise shipping.ShipError("apply_failed", "fetch failed (simulated)")

    monkeypatch.setattr(shipping, "_apply", apply_then_fail)
    before = git_state(site)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert not after_mark.exists() and not back_mark.exists()
    assert not (site / "after-here").exists() and not (site / "back-here").exists()
    assert record["after"] == [] and record["back_after"] == [], record
    assert "ran again" not in record["message"] and "after-commands" not in record["message"], record["message"]
    assert git_state(site) == before
    assert run_git(site, "branch", "--list", branch).strip() == ""      # going back removed the branch


def _fail_after_applying(monkeypatch):
    real_apply = shipping._apply

    def apply_then_fail(*args):
        real_apply(*args)
        raise shipping.ShipError("apply_failed", "fetch failed (simulated)")

    monkeypatch.setattr(shipping, "_apply", apply_then_fail)


async def test_a_failed_apply_runs_the_after_commands_once_and_does_not_say_again(client, studio, tmp_path,
                                                                                  monkeypatch):
    """Applying fails: the after-commands had not run yet, so going back runs them for the first time
    and the message says why, not that they "ran again"."""
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    runs = tmp_path / "after-runs.txt"
    await set_recipe(client, pid, method="merge", after=[f"echo run >> {runs}"])
    await change(client, pid)
    _fail_after_applying(monkeypatch)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert runs.read_text() == "run\n"                  # behaviour unchanged: they run once, after going back
    assert "ran again" not in record["message"], record["message"]
    assert "after going back" in record["message"] and "matches the folder" in record["message"], record["message"]


async def test_a_failed_apply_names_the_go_back_commands_it_ran(client, studio, tmp_path, monkeypatch):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", back=["true"])
    await change(client, pid)
    _fail_after_applying(monkeypatch)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert len(record["back_after"]) == 1
    assert "after-commands" not in record["message"] and "go-back commands ran" in record["message"], \
        record["message"]


async def test_an_address_failure_says_the_after_commands_ran_again(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await set_recipe(client, pid, method="merge", after=["true"], address=dead_address())
    await change(client, pid)
    record = await ship(client, studio, pid)
    assert record["result"] == "rolled_back", record["message"]
    assert "The after-commands ran again." in record["message"], record["message"]


async def test_a_card_with_go_back_commands_and_nothing_after_says_when_they_run(client, studio, tmp_path):
    site = make_site(tmp_path)
    project = await attach(client, site)
    pid = project["id"]
    await change(client, pid)
    for method in ("merge", "files"):
        if method == "files":
            site = make_site(tmp_path, "plain", git=False)
            pid = (await attach(client, site))["id"]
            await change(client, pid)
        await set_recipe(client, pid, method=method, back=["pm2 restart site"])
        card = await card_for(client, studio, pid)
        text = card["going_back"]
        assert "pm2 restart site" in text and "fails" in text, text
