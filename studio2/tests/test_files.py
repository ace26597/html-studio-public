from studio2_server import workspace as ws


def checkpoints(studio, project_id):
    return [e for e in studio.log(project_id).read() if e["type"] == "checkpoint"]


async def test_list_get_put_and_stale_hash(client, studio, project):
    base = f"/api/projects/{project['id']}"
    workspace = studio.store.workspace(project["id"])
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "x.js").write_text("ignored")

    listed = (await client.get(f"{base}/files")).json()
    assert [f["path"] for f in listed] == [".gitignore", "index.html"]
    assert listed[1]["size"] > 100 and listed[1]["modified_at"].endswith("Z")

    got = (await client.get(f"{base}/file", params={"path": "index.html"})).json()
    assert got["path"] == "index.html" and "<main></main>" in got["content"] and len(got["hash"]) == 64

    saved = await client.put(f"{base}/file", json={
        "path": "index.html", "content": got["content"].replace("<main></main>", "<main>Hi</main>"),
        "base_hash": got["hash"]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["checkpoint"]["kind"] == "user" and body["hash"] != got["hash"]
    assert body["checkpoint"]["files"] == [{"path": "index.html", "status": "modified"}]
    assert "<main>Hi</main>" in (workspace / "index.html").read_text()
    events = checkpoints(studio, project["id"])
    assert len(events) == 1 and events[0]["sha"] == body["checkpoint"]["sha"] and events[0]["turn_id"] is None
    assert studio.store.get(project["id"])["head"] == body["checkpoint"]["sha"]

    stale = await client.put(f"{base}/file", json={"path": "index.html", "content": "x", "base_hash": got["hash"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_file"
    assert "<main>Hi</main>" in (workspace / "index.html").read_text()

    new = await client.put(f"{base}/file", json={"path": "data/notes.txt", "content": "hello"})
    assert new.status_code == 200 and new.json()["checkpoint"]["files"] == [{"path": "data/notes.txt", "status": "added"}]
    same = await client.put(f"{base}/file", json={"path": "data/notes.txt", "content": "hello"})
    assert same.json()["checkpoint"] is None  # nothing changed, no empty version


async def test_paths_are_confined_and_binary_is_refused(client, studio, project):
    base = f"/api/projects/{project['id']}"
    for path in ("../project.json", ".git/config", "a/../../x", "/etc/passwd/../x", "sub/.git/HEAD", ""):
        response = await client.get(f"{base}/file", params={"path": path})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_path", path
        response = await client.put(f"{base}/file", json={"path": path, "content": "x"})
        assert response.status_code == 400, path
    assert (await client.get(f"{base}/file", params={"path": "nope.txt"})).status_code == 404

    # A link is judged by where it ends: one that points into .git is refused though its own name is innocent.
    workspace = studio.store.workspace(project["id"])
    (workspace / "history").symlink_to(workspace / ".git")
    (workspace / "settings.txt").symlink_to(workspace / ".git" / "config")
    for path in ("history/config", "settings.txt"):
        response = await client.get(f"{base}/file", params={"path": path})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_path", path
        assert (await client.put(f"{base}/file", json={"path": path, "content": "x"})).status_code == 400, path
    (workspace / "history").unlink()
    (workspace / "settings.txt").unlink()

    (studio.store.workspace(project["id"]) / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    binary = await client.get(f"{base}/file", params={"path": "blob.bin"})
    assert binary.status_code == 415 and binary.json()["error"]["code"] == "unsupported_file"


async def test_upload(client, studio, project):
    base = f"/api/projects/{project['id']}"
    sent = await client.post(f"{base}/upload", files={"file": ("../sales.csv", b"a,b\n1,2\n", "text/csv")},
                             data={"dir": "data"})
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["path"] == "data/sales.csv" and body["size"] == 8 and body["checkpoint"]["kind"] == "user"
    assert (studio.store.workspace(project["id"]) / "data" / "sales.csv").read_bytes() == b"a,b\n1,2\n"
    escape = await client.post(f"{base}/upload", files={"file": ("x.txt", b"x")}, data={"dir": "../.."})
    assert escape.status_code == 400


async def test_dom_edit_unique_ambiguous_and_not_found(client, studio, project):
    base = f"/api/projects/{project['id']}"
    workspace = studio.store.workspace(project["id"])
    page = (workspace / "index.html").read_text().replace(
        "<main></main>",
        "<main><h1>Market share</h1><p>Twice</p><p>Twice</p><p>Fish &amp; chips</p>\n"
        "<p>A long line that\n   wraps in the source</p></main>")
    (workspace / "index.html").write_text(page)

    ok = await client.post(f"{base}/dom-edits", json={"path": "/", "before_text": "Market share", "after_text": "Share <2026>"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["path"] == "index.html" and ok.json()["checkpoint"]["kind"] == "user"
    assert "<h1>Share &lt;2026&gt;</h1>" in (workspace / "index.html").read_text()

    twice = await client.post(f"{base}/dom-edits", json={"path": "/", "before_text": "Twice", "after_text": "Once"})
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "ambiguous_edit"
    missing = await client.post(f"{base}/dom-edits", json={"path": "/", "before_text": "Not here", "after_text": "x"})
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "edit_not_found"
    assert (workspace / "index.html").read_text().count("Twice") == 2

    escaped = await client.post(f"{base}/dom-edits", json={"path": "/", "before_text": "Fish & chips", "after_text": "Fish & peas"})
    assert escaped.status_code == 200 and "<p>Fish &amp; peas</p>" in (workspace / "index.html").read_text()
    wrapped = await client.post(f"{base}/dom-edits", json={
        "path": "/index.html", "before_text": "A long line that wraps in the source", "after_text": "Short"})
    assert wrapped.status_code == 200 and "<p>Short</p>" in (workspace / "index.html").read_text()

    (workspace / "docs").mkdir()
    (workspace / "docs" / "index.html").write_text("<p>Sub page</p>")
    sub = await client.post(f"{base}/dom-edits", json={"path": "/docs/", "before_text": "Sub page", "after_text": "Edited"})
    assert sub.json()["path"] == "docs/index.html"
    assert (workspace / "docs" / "index.html").read_text() == "<p>Edited</p>"


def test_page_to_file(tmp_path):
    (tmp_path / "docs").mkdir()
    assert ws.page_to_file(tmp_path, "/") == "index.html"
    assert ws.page_to_file(tmp_path, "/foo/") == "foo/index.html"
    assert ws.page_to_file(tmp_path, "/docs") == "docs/index.html"
    assert ws.page_to_file(tmp_path, "/about.html?x=1#top") == "about.html"


async def test_versions_diff_and_restore(client, studio, project):
    base = f"/api/projects/{project['id']}"
    workspace = studio.store.workspace(project["id"])
    first = project["head"]
    await client.put(f"{base}/file", json={"path": "index.html", "content": "<p>changed</p>"})
    await client.put(f"{base}/file", json={"path": "extra.txt", "content": "extra"})

    versions = (await client.get(f"{base}/versions")).json()
    assert [v["kind"] for v in versions] == ["user", "user", "init"]
    diff = (await client.get(f"{base}/versions/{versions[1]['sha']}/diff")).json()
    assert diff["truncated"] is False and "+<p>changed</p>" in diff["diff"]
    assert (await client.get(f"{base}/versions/deadbeef/diff")).status_code == 404

    restored = await client.post(f"{base}/restore", json={"sha": first})
    assert restored.status_code == 200, restored.text
    checkpoint = restored.json()["checkpoint"]
    assert checkpoint["kind"] == "restore" and checkpoint["sha"] != first
    tree = lambda sha: ws.git(workspace, "rev-parse", f"{sha}^{{tree}}").strip()  # noqa: E731
    assert tree(checkpoint["sha"]) == tree(first)                      # same content …
    assert "<main></main>" in (workspace / "index.html").read_text()
    assert not (workspace / "extra.txt").exists()
    after = (await client.get(f"{base}/versions")).json()
    assert [v["kind"] for v in after] == ["restore", "user", "user", "init"]   # … and history only grew
    assert checkpoints(studio, project["id"])[-1]["kind"] == "restore"
    assert (await client.post(f"{base}/restore", json={"sha": "0000000"})).status_code == 404


def test_text_edit_uses_the_tag_when_text_repeats():
    source = "<title>Demo dashboard</title>\n<h1 class=\"t\">\n  Demo dashboard\n</h1><p>Demo dashboard</p>"
    assert ws.apply_text_edit(source, "Demo dashboard", "Sales") == (None, 3)
    updated, count = ws.apply_text_edit(source, "Demo dashboard", "Sales & more", tag="h1")
    assert count == 1
    assert "<h1 class=\"t\">\n  Sales &amp; more\n</h1>" in updated
    assert "<title>Demo dashboard</title>" in updated and "<p>Demo dashboard</p>" in updated
    # Two matching elements of that tag: still ambiguous.
    assert ws.apply_text_edit("<p>a</p><p>a</p>", "a", "b", tag="p") == (None, 2)
