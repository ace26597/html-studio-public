from studio2_server import workspace as ws


async def test_create_list_get_patch_delete(client, studio, settings):
    created = (await client.post("/api/projects", json={"name": "  First  one "})).json()
    assert created["id"].startswith("p_") and len(created["id"]) == 10
    assert created["name"] == "First one"
    assert settings.port_range[0] <= created["port"] <= settings.port_range[1]
    assert created["preview_url"] == f"http://127.0.0.1:{created['port']}/"
    assert created["connection"] == "fake" and created["running_turn"] is None
    assert created["session_id"] is None and created["model"] is None

    second = (await client.post("/api/projects", json={})).json()
    assert second["name"] == "Untitled" and second["port"] != created["port"]
    listed = (await client.get("/api/projects")).json()
    assert [p["id"] for p in listed] == [second["id"], created["id"]]  # newest first

    fetched = (await client.get(f"/api/projects/{created['id']}")).json()
    assert fetched["id"] == created["id"] and fetched["head"]

    patched = await client.patch(f"/api/projects/{created['id']}", json={"name": "Renamed", "model": "fake"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed" and patched.json()["model"] == "fake"
    bad = await client.patch(f"/api/projects/{created['id']}", json={"connection": "nope"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "unknown_connection"

    deleted = await client.delete(f"/api/projects/{created['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/projects/{created['id']}")).status_code == 404
    trash = settings.projects_dir / ".trash"
    assert [p.name.split("-")[0] for p in trash.iterdir()] == [created["id"]]
    assert created["id"] not in studio.previews.running


async def test_new_workspace_is_a_blank_committed_page(client, studio, project):
    workspace = studio.store.workspace(project["id"])
    page = (workspace / "index.html").read_text()
    assert page.lower().startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page and "viewport" in page and "<main></main>" in page
    assert "node_modules/" in (workspace / ".gitignore").read_text()

    versions = (await client.get(f"/api/projects/{project['id']}/versions")).json()
    assert len(versions) == 1
    assert versions[0]["kind"] == "init" and versions[0]["sha"] == project["head"]
    assert {f["path"] for f in versions[0]["files"]} == {"index.html", ".gitignore"}
    assert ws.git(workspace, "status", "--porcelain") == ""
    assert "Studio <studio@localhost>" in ws.git(workspace, "log", "-1", "--format=%an <%ae>")


async def test_errors_use_the_contract_shape(client):
    missing = await client.get("/api/projects/p_00000000")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "project_not_found", "message": "That project does not exist."}}
    assert (await client.get("/api/projects/not-an-id")).status_code == 404
    invalid = await client.post("/api/projects/p_00000000/turns", json={"nope": 1})
    assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "invalid_request"
    unknown = await client.get("/api/nothing-here")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"


async def test_origin_is_checked_on_mutations(client):
    evil = await client.post("/api/projects", json={}, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "forbidden_origin"
    assert (await client.get("/api/projects")).json() == []  # nothing was created

    for origin in ("http://127.0.0.1:8200", "http://localhost:5200"):
        assert (await client.post("/api/projects", json={}, headers={"Origin": origin})).status_code == 201
    assert (await client.post("/api/projects", json={})).status_code == 201  # curl / tests: no Origin
    read = await client.get("/api/projects", headers={"Origin": "http://evil.example"})
    assert read.status_code == 200


async def test_health_and_ui_placeholder(client):
    health = (await client.get("/api/health")).json()
    assert health["ok"] is True and health["fake"] is True
    assert health["connections"] == [{
        "id": "fake", "label": "Fake (no model calls)", "kind": "fake", "available": True, "reason": None,
        "models": [{"id": "fake", "label": "Fake"}], "default_model": "fake"}]
    page = await client.get("/")
    assert page.status_code == 200 and "not been built" in page.text


async def test_built_ui_is_served_with_spa_fallback(client, settings):
    settings.web_dist.mkdir()
    (settings.web_dist / "index.html").write_text("<title>ui</title>")
    (settings.web_dist / "assets").mkdir()
    (settings.web_dist / "assets" / "app.js").write_text("console.log(1)")
    assert "<title>ui</title>" in (await client.get("/")).text
    assert "<title>ui</title>" in (await client.get("/p/p_12345678")).text
    assert (await client.get("/assets/app.js")).text == "console.log(1)"
    assert (await client.get("/api/missing")).status_code == 404
