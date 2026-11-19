"""Context from this computer (CONTRACT.md, "Context from this computer: folders, files and addresses").

The rule tested hardest, as for attaching: a folder that is context is never written to. The
tests that read one take a picture of every file in it (its .git included) before and after.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from studio2_server import context as contexts
from studio2_server import tools

GIT = ["git", "-c", "user.name=Owner", "-c", "user.email=owner@example.com", "-c", "commit.gpgsign=false"]


def run_git(folder: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=folder, capture_output=True, check=True, text=True).stdout


def snapshot(folder: Path) -> dict[str, str]:
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def make_code(root: Path, name: str = "crm") -> Path:
    """A git-tracked Next.js app with key files, an ignored dependency folder and two commits."""
    code = root / name
    (code / "src").mkdir(parents=True)
    (code / "api").mkdir()
    (code / "README.md").write_text("# CRM\n")
    (code / "src" / "main.js").write_text("console.log('hi')\n")
    (code / "api" / "main.py").write_text("print('api')\n")
    (code / "package.json").write_text(json.dumps({"dependencies": {"next": "16.1.6", "react": "19"}}))
    (code / ".gitignore").write_text(".env*\nnode_modules/\n")
    run_git(code, "init", "-q", "-b", "main")
    run_git(code, "add", "-A")
    run_git(code, "commit", "-qm", "First")
    (code / "src" / "main.js").write_text("console.log('hi again')\n")
    run_git(code, "commit", "-qam", "Second commit")
    (code / ".env").write_text("SECRET=do-not-read\n")
    (code / ".env.local").write_text("LOCAL=1\n")
    (code / "node_modules" / "left-pad").mkdir(parents=True)
    (code / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
    return code


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the name http.server calls
        body = b"<!doctype html><html><head><title>  My &amp; CRM\n</title></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def running_app():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- what was typed ------------------------------------------------------------------------
@pytest.mark.parametrize("typed, kind, read_as", [
    ("/Users/me/dev/app", "path", "/Users/me/dev/app"),
    ("  ~/dev/app ", "path", "~/dev/app"),
    ("http://localhost:3000", "address", "http://localhost:3000"),
    ("https://staging.example.com/app", "address", "https://staging.example.com/app"),
    ("localhost:3000", "address", "http://localhost:3000"),
    ("127.0.0.1:8000/admin", "address", "http://127.0.0.1:8000/admin"),
    ("[::1]:5173", "address", "http://[::1]:5173"),
])
def test_a_path_and_an_address_are_told_apart(typed, kind, read_as):
    assert contexts.classify(typed) == (kind, read_as)


@pytest.mark.parametrize("typed", ["", "   ", "dev/app", "my app", "localhost", "C:\\code\\app"])
def test_anything_else_says_what_to_type(typed):
    with pytest.raises(contexts.ContextError) as caught:
        contexts.classify(typed)
    assert caught.value.code == "invalid_request" and "~/dev/my-app" in caught.value.message


@pytest.mark.parametrize("typed, kept", [
    ("http://localhost:3000", "http://localhost:3000/"),
    ("HTTP://LocalHost:3000/Admin?tab=1#top", "http://localhost:3000/Admin?tab=1"),
    ("https://example.com:443/x", "https://example.com/x"),
    ("http://[::1]:5173", "http://[::1]:5173/"),
])
def test_an_address_is_kept_normalised(typed, kept):
    assert contexts.check_address(typed) == kept


@pytest.mark.parametrize("typed, words", [
    ("ftp://example.com/x", "Only http and https"),
    ("file:///etc/passwd", "Only http and https"),
    ("http://me:secret@localhost:3000/", "user name and password"),
    ("http://me@localhost:3000/", "user name and password"),
    ("http:///nothing", "no host"),
    ("http://localhost:3000/" + "x" * 2100, "longer than"),
])
def test_addresses_that_cannot_be_context(typed, words):
    with pytest.raises(contexts.ContextError) as caught:
        contexts.check_address(typed)
    assert caught.value.code == "context_refused" and words in caught.value.message


def test_a_look_may_open_only_pages_under_a_context_address():
    items = [{"kind": "address", "address": "http://localhost:3000/"},
             {"kind": "folder", "path": "/x", "address": None}]
    assert contexts.page_under("http://localhost:3000/leads?page=2", items) == "http://localhost:3000/leads?page=2"
    assert contexts.page_under("http://LOCALHOST:3000", items) == "http://localhost:3000/"
    for other in ("http://localhost:3001/", "https://localhost:3000/", "http://example.com/", "/leads",
                  "http://localhost:3000@example.com/", "file:///etc/passwd", ""):
        assert contexts.page_under(other, items) is None, other


# -- a folder ------------------------------------------------------------------------------
async def test_inspect_reads_a_folder_without_writing_to_it(client, tmp_path):
    code = make_code(tmp_path)
    before = snapshot(code)
    response = await client.post("/api/context/inspect", json={"value": str(code)})
    assert response.status_code == 200, response.text
    seen = response.json()
    assert seen["kind"] == "folder" and seen["path"] == str(code.resolve()) and seen["label"] == "crm"
    assert seen["refused"] is None and seen["address"] is None
    found = seen["found"]
    assert found["git"]["branch"] == "main" and found["git"]["subject"] == "Second commit" and len(found["git"]["head"]) == 40
    assert found["framework"] == {"id": "next", "label": "Next.js"}
    assert found["files"] == 5 and found["bytes"] > 0 and found["partial"] is False      # tracked files only
    assert found["key_files"] == [".env", ".env.local"]
    assert found["top"] == ["api/", "src/", "README.md", "package.json", ".gitignore"]   # no .git, keys, packages
    assert found["top_more"] == 0
    assert any(".env" in note and "not to open" in note for note in seen["notes"])
    assert "do-not-read" not in response.text
    assert snapshot(code) == before


async def test_a_folder_without_git_is_counted_without_its_dependencies(client, tmp_path):
    folder = tmp_path / "plain"
    (folder / "node_modules" / "x").mkdir(parents=True)
    (folder / "node_modules" / "x" / "index.js").write_text("x")
    (folder / "notes.txt").write_text("hello")
    (folder / "sub").mkdir()
    (folder / "sub" / "a.py").write_text("print(1)\n")
    seen = (await client.post("/api/context/inspect", json={"value": str(folder)})).json()
    assert seen["found"]["git"] is None and seen["found"]["framework"] is None
    assert seen["found"]["files"] == 2 and seen["found"]["partial"] is False
    assert not (folder / ".git").exists()


def test_a_long_top_of_a_folder_is_cut_at_forty(tmp_path):
    folder = tmp_path / "many"
    folder.mkdir()
    for number in range(45):
        (folder / f"file-{number:02}.txt").write_text("x")
    (folder / "zz-folder").mkdir()
    top, more, keys = contexts.top_names(folder)
    assert len(top) == 40 and top[0] == "zz-folder/" and more == 6 and keys == []
    (folder / ".github").mkdir()
    (folder / "node_modules").mkdir()
    (folder / "aa-folder").mkdir()
    assert contexts.top_names(folder)[0][:3] == ["aa-folder/", "zz-folder/", ".github/"]


def test_a_large_folder_gets_a_note_and_one_that_cannot_be_counted_is_still_context(tmp_path, monkeypatch):
    code = make_code(tmp_path)
    monkeypatch.setattr(contexts, "LARGE_FILES", 3)
    seen = contexts.inspect_path(str(code), [], tmp_path / "home")
    assert any("large folder (5 files)" in note and "does not read it whole" in note for note in seen["notes"])

    def too_slow(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", 20)
    monkeypatch.setattr(contexts.attaching, "_plain_files", too_slow)
    plain = tmp_path / "huge"
    plain.mkdir()
    seen = contexts.inspect_path(str(plain), [], tmp_path / "home")
    assert seen["refused"] is None
    assert seen["found"]["files"] is None and seen["found"]["bytes"] is None and seen["found"]["partial"] is True
    assert any("more than it could count" in note for note in seen["notes"])


async def test_what_cannot_be_context(client, settings, tmp_path):
    async def refused(value: str) -> str:
        response = await client.post("/api/context/inspect", json={"value": value})
        assert response.status_code == 200, response.text
        assert response.json()["found"] is None
        return response.json()["refused"]

    settings.home.mkdir(parents=True, exist_ok=True)
    assert "nothing at this path" in await refused(str(tmp_path / "missing"))
    assert "top of a disk" in await refused("/")
    assert "home folder" in await refused(str(settings.home))
    assert "holds your whole home folder" in await refused(str(settings.home.parent))
    key = tmp_path / "app" / ".env"
    key.parent.mkdir()
    key.write_text("SECRET=1\n")
    assert "key file" in await refused(str(key))
    # Hidden places at the top of the home folder hold keys and settings: neither they nor anything in them.
    (settings.home / ".secrets").mkdir()
    (settings.home / ".secrets" / "credentials.json").write_text("{}")
    (settings.home / ".zsh_history").write_text("x")
    (settings.home / "dev" / ".config").mkdir(parents=True)
    for hidden in (".secrets", ".secrets/credentials.json", ".zsh_history"):
        said = await refused(str(settings.home / hidden))
        assert "hidden part of your home folder" in said and f"(~/{hidden.split('/')[0]})" in said, hidden
    deeper = await client.post("/api/context/inspect", json={"value": str(settings.home / "dev" / ".config")})
    assert deeper.json()["refused"] is None                  # a dot folder inside a code folder is ordinary
    # A folder inside the studio's data is fine (another project's files); the folder that holds it is not.
    elsewhere = Path("/nowhere/home/me")
    assert "studio's own files" in contexts.folder_refusal(settings.data_dir.resolve().parent, [settings.data_dir],
                                                           elsewhere)
    assert contexts.folder_refusal(settings.data_dir.resolve() / "projects", [settings.data_dir], elsewhere) is None
    for bad in ("", "relative/path", "notes.txt"):
        response = await client.post("/api/context/inspect", json={"value": bad})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_a_folder_the_studio_cannot_read_is_refused(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert "cannot read this folder" in contexts.inspect_path(str(locked), [], tmp_path / "home")["refused"]
    finally:
        locked.chmod(0o700)


async def test_a_single_file_can_be_context(client, tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n" * 10)
    seen = (await client.post("/api/context/inspect", json={"value": str(spec)})).json()
    assert seen["kind"] == "file" and seen["label"] == "spec.md" and seen["found"] == {"bytes": 70}


def test_a_shared_studio_says_whose_computer_the_path_is_on(tmp_path):
    code = make_code(tmp_path)
    own = contexts.inspect_path(str(code), [], tmp_path / "home")
    shared = contexts.inspect_path(str(code), [], tmp_path / "home", shared=True)
    assert not any("shared" in note for note in own["notes"])
    assert any("computer the studio runs on" in note for note in shared["notes"])


# -- an address ----------------------------------------------------------------------------
async def test_an_address_that_answers(client, running_app):
    typed = running_app.removeprefix("http://")               # typed without its scheme
    seen = (await client.post("/api/context/inspect", json={"value": typed})).json()
    assert seen["kind"] == "address" and seen["address"] == running_app + "/" and seen["path"] is None
    assert seen["label"] == typed and seen["refused"] is None
    assert seen["found"] == {"reachable": True, "status": 200, "title": "My & CRM", "error": None, "this_computer": True}
    assert seen["notes"] == []


async def test_an_address_that_does_not_answer_can_still_be_added(client, project, closed_port):
    address = f"http://localhost:{closed_port}"
    seen = (await client.post("/api/context/inspect", json={"value": address})).json()
    assert seen["refused"] is None and seen["found"]["reachable"] is False and "Nothing answered" in seen["found"]["error"]
    assert any("can still be added" in note for note in seen["notes"])
    added = await client.post(f"/api/projects/{project['id']}/context", json={"value": address})
    assert added.status_code == 201 and added.json()["found"]["reachable"] is False
    listed = (await client.get(f"/api/projects/{project['id']}/context")).json()
    assert listed[0]["state"] == {"ok": False, "problem": "This address does not answer now."}


async def test_a_refused_address_says_why_and_is_not_added(client, project):
    seen = (await client.post("/api/context/inspect", json={"value": "http://me:pw@localhost:3000/"})).json()
    assert seen["kind"] == "address" and "user name and password" in seen["refused"] and seen["found"] is None
    added = await client.post(f"/api/projects/{project['id']}/context", json={"value": "http://me:pw@localhost:3000/"})
    assert added.status_code == 400 and added.json()["error"]["code"] == "context_refused"
    assert "pw" not in json.dumps((await client.get(f"/api/projects/{project['id']}")).json())


async def test_an_address_on_another_machine_says_where_it_is_reached_from(monkeypatch):
    async def answered(address, *_args, **_kwargs):
        return {"reachable": True, "status": 200, "title": None, "error": None,
                "this_computer": contexts.this_computer(address)}
    monkeypatch.setattr(contexts, "probe", answered)
    far = await contexts.inspect_address("https://staging.example.com/app")
    assert far["label"] == "staging.example.com" and any("another machine" in note for note in far["notes"])
    near = await contexts.inspect_address("http://localhost:3000", shared=True)
    assert any("localhost here is the computer the studio runs on" in note for note in near["notes"])


# -- the project's list --------------------------------------------------------------------
async def test_add_list_edit_and_remove(client, studio, project, tmp_path, running_app):
    code = make_code(tmp_path)
    before = snapshot(code)
    pid = project["id"]
    assert project["context"] == []

    added = await client.post(f"/api/projects/{pid}/context",
                              json={"value": str(code), "note": "  Our CRM. The API is in api/.  "})
    assert added.status_code == 201, added.text
    item = added.json()
    assert item["id"].startswith("cx_") and item["kind"] == "folder" and item["path"] == str(code.resolve())
    assert item["label"] == "crm" and item["note"] == "Our CRM. The API is in api/." and item["added_at"]
    assert item["found"]["framework"]["id"] == "next" and "top" not in item["found"] and "state" not in item

    app = await client.post(f"/api/projects/{pid}/context", json={"value": running_app, "label": "CRM, running"})
    assert app.status_code == 201 and app.json()["label"] == "CRM, running" and app.json()["note"] == ""

    assert [entry["id"] for entry in (await client.get(f"/api/projects/{pid}")).json()["context"]] == \
        [item["id"], app.json()["id"]]
    listed = (await client.get(f"/api/projects/{pid}/context")).json()
    assert [entry["state"] for entry in listed] == [{"ok": True, "problem": None}] * 2

    again = await client.post(f"/api/projects/{pid}/context", json={"value": str(code) + "/"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_added"
    same_app = await client.post(f"/api/projects/{pid}/context", json={"value": running_app + "/#top"})
    assert same_app.status_code == 409

    changed = await client.patch(f"/api/projects/{pid}/context/{item['id']}", json={"label": " The CRM ", "note": ""})
    assert changed.status_code == 200 and changed.json()["label"] == "The CRM" and changed.json()["note"] == ""
    assert changed.json()["path"] == item["path"]
    for bad in ({"label": "  "}, {"label": "x" * 61}, {"note": "x" * 501}):
        refused = await client.patch(f"/api/projects/{pid}/context/{item['id']}", json=bad)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_request", bad
    assert (await client.patch(f"/api/projects/{pid}/context/cx_00000000", json={"label": "x"})).status_code == 404

    assert (await client.delete(f"/api/projects/{pid}/context/{app.json()['id']}")).status_code == 204
    gone = await client.delete(f"/api/projects/{pid}/context/{app.json()['id']}")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "unknown_context"
    assert [entry["label"] for entry in studio.store.get(pid)["context"]] == ["The CRM"]
    assert snapshot(code) == before


async def test_what_a_project_does_not_take(client, studio, project, tmp_path):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    for own in (workspace, workspace / "index.html"):
        refused = await client.post(f"/api/projects/{pid}/context", json={"value": str(own)})
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "context_refused"
        assert "already part of the project" in refused.json()["error"]["message"]
    missing = await client.post(f"/api/projects/{pid}/context", json={"value": str(tmp_path / "missing")})
    assert missing.status_code == 400 and "nothing at this path" in missing.json()["error"]["message"]
    assert (await client.post("/api/projects/p_00000000/context", json={"value": str(tmp_path)})).status_code == 404
    assert (await client.get("/api/projects/p_00000000/context")).status_code == 404
    for number in range(contexts.MAX_ITEMS):
        folder = tmp_path / f"folder-{number}"
        folder.mkdir()
        assert (await client.post(f"/api/projects/{pid}/context", json={"value": str(folder)})).status_code == 201
    extra = tmp_path / "one-too-many"
    extra.mkdir()
    full = await client.post(f"/api/projects/{pid}/context", json={"value": str(extra)})
    assert full.status_code == 400 and full.json()["error"]["code"] == "too_many"


async def test_a_folder_that_went_away_is_said_to_be_missing(client, studio, project, tmp_path):
    folder = tmp_path / "soon-gone"
    folder.mkdir()
    (folder / "a.txt").write_text("a")
    pid = project["id"]
    await client.post(f"/api/projects/{pid}/context", json={"value": str(folder)})
    (folder / "a.txt").unlink()
    folder.rmdir()
    listed = (await client.get(f"/api/projects/{pid}/context")).json()
    assert listed[0]["state"] == {"ok": False, "problem": "This folder is not there now."}
    told = studio.agent_context(pid)
    assert "NOT AVAILABLE NOW" in told and "Say so instead of guessing" in told


# -- the agent -----------------------------------------------------------------------------
async def test_the_agent_is_told_what_the_context_is_and_that_it_is_read_only(client, studio, project, tmp_path,
                                                                             running_app):
    pid = project["id"]
    assert "added context from outside this project" not in studio.agent_context(pid)
    code = make_code(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n")
    await client.post(f"/api/projects/{pid}/context", json={"value": str(code), "note": "Our CRM.\nThe API is in api/."})
    told = studio.agent_context(pid)
    assert "added context from outside this project (1 item)" in told
    assert f"Folder “crm”: {code.resolve()} (Next.js app; git branch main; about 5 files when it was added)" in told
    assert "The person's note: “Our CRM. The API is in api/.”" in told
    assert "At its top now: api/, src/, README.md, package.json, .gitignore." in told
    assert "READ ONLY" in told and "copy the files into the project" in told and "An app on this computer" in told
    assert "Do not open key files there" in told and "do-not-read" not in told
    assert "Request a context address" not in told          # no address yet

    await client.post(f"/api/projects/{pid}/context", json={"value": str(spec)})
    await client.post(f"/api/projects/{pid}/context", json={"value": running_app})
    told = studio.agent_context(pid)
    assert "(3 items)" in told and f"File “spec.md”: {spec.resolve()} (7 bytes when it was added)." in told
    assert f"{running_app}/ (it answered when it was added, with the title “My & CRM”)" in told
    assert "look_at_page and `url`" in told and "submit no form" in told

    (code / "docs").mkdir()                                  # the top of a folder is read at that moment
    assert "At its top now: api/, docs/, src/" in studio.agent_context(pid)


async def test_the_agent_gets_the_context_with_its_next_message(client, studio, project, tmp_path):
    from conftest import run_turn
    from studio2_server.harness.base import TurnResult

    code = make_code(tmp_path)
    await client.post(f"/api/projects/{project['id']}/context", json={"value": str(code)})
    seen = []

    class Recorder:
        kind = "fake"

        async def run(self, request, emit):
            seen.append(request)
            return TurnResult("completed")

    studio.registry._harnesses["fake"] = Recorder()
    await run_turn(client, studio, project["id"], "What does the CRM's API do?")
    assert f"Folder “crm”: {code.resolve()}" in seen[0].prompt and "READ ONLY" in seen[0].prompt


async def test_look_at_page_opens_a_context_address_without_a_sign_in(client, studio, project, running_app):
    pid = project["id"]
    calls = []

    async def look(url, *_args, **kwargs):
        calls.append((url, kwargs.get("signed_in")))
        return {"url": url}
    studio.looker.look = look

    none_yet = await tools.call(studio, pid, "look_at_page", {"url": running_app + "/leads"})
    assert none_yet.get("is_error") and "it has none" in none_yet["content"][0]["text"]

    await client.post(f"/api/projects/{pid}/context", json={"value": running_app})
    seen = await tools.t_look_at_page(studio, pid, {"url": running_app + "/leads?page=2", "width": 390})
    assert seen["url"] == running_app + "/leads?page=2" and calls[-1] == (running_app + "/leads?page=2", False)

    elsewhere = await tools.call(studio, pid, "look_at_page", {"url": "http://127.0.0.1:1/"})
    assert elsewhere.get("is_error") and running_app in elsewhere["content"][0]["text"]

    await tools.t_look_at_page(studio, pid, {"path": "/"})     # the project's own page is looked at as before
    assert calls[-1][1] is True and str(studio.store.get(pid)["port"]) in calls[-1][0]
    schema = {name: schema for name, _text, schema, _handler in tools.TOOLS}["look_at_page"]
    assert "url" in schema["properties"]
    from studio2_server.harness.claude import describe_tool
    workspace = studio.store.workspace(pid)                     # the activity line says which page was looked at
    assert describe_tool("mcp__studio__look_at_page", {"url": running_app + "/leads"}, workspace) == \
        ("read", f"Look at {running_app}/leads")
    assert describe_tool("mcp__studio__look_at_page", {"path": "/"}, workspace) == ("read", "Look at the page /")


async def test_the_library_lists_context_among_what_the_studio_can_do(client):
    listed = {tool["id"]: tool for tool in (await client.get("/api/library")).json()["tools"]}
    assert listed["context"]["available"] is True and "nothing there is changed" in listed["context"]["description"]
