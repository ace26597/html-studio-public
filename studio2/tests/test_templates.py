"""Templates the person saves (CONTRACT.md, "Templates the person saves")."""

import io
import json
import zipfile

from conftest import run_turn


def fill(workspace, app=False):
    (workspace / "index.html").write_text("<h1>Acme revenue rose 12% to $4.2M</h1><link rel=stylesheet href=style.css>")
    (workspace / "style.css").write_text(":root{--accent:#b4541a}")
    for folder, name in (("sources", "s1.txt"), ("data", "sales.csv"), ("checks", "index.html.json"),
                         ("attachments", "photo.png"), ("tools", "build_deck.py"), ("files", "deck.pptx"),
                         ("files/deck.pptx.preview", "page-1.png"), ("files/img", "chart.png")):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
        (workspace / folder / name).write_bytes(b"x")
    if app:
        (workspace / "studio.json").write_text('{"run": "python3 server.py"}')
        (workspace / "server.py").write_text("print('hi')")


async def test_a_page_is_saved_without_its_content_folders_and_listed(client, studio, project):
    workspace = studio.store.workspace(project["id"])
    fill(workspace)
    made = await client.post(f"/api/projects/{project['id']}/template",
                             json={"name": "  Competitor   analysis ", "description": "Dark headings, wide tables"})
    assert made.status_code == 201, made.text
    saved = made.json()
    assert saved["label"] == "Competitor analysis" and saved["kind"] == "page" and saved["mode"] == "pattern"
    assert saved["source"] == "yours" and saved["has_code"] is False and saved["main"] == "index.html"
    files = studio.saved.files(saved["id"])
    kept = sorted(p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file())
    assert kept == [".gitignore", "TEMPLATE.md", "index.html", "style.css"]      # the project's own ignore file travels too
    guide = (files / "TEMPLATE.md").read_text()
    assert "Dark headings, wide tables" in guide and "Do not carry over" in guide
    listed = (await client.get("/api/templates")).json()
    assert [t["id"] for t in listed if t["source"] == "yours"] == [saved["id"]]
    assert all(t["source"] == "studio" and t["mode"] == "copy" for t in listed if t["id"] != saved["id"])
    bad = await client.post(f"/api/projects/{project['id']}/template", json={"name": " "})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_template"


async def test_an_office_file_is_saved_with_its_script_and_first_page(client, studio, project):
    workspace = studio.store.workspace(project["id"])
    fill(workspace)
    saved = (await client.post(f"/api/projects/{project['id']}/template",
                               json={"name": "Board deck", "file": "files/deck.pptx", "notes": "Navy title slides."})).json()
    assert saved["kind"] == "deck" and saved["main"] == "files/deck.pptx" and saved["has_code"] is True
    files = studio.saved.files(saved["id"])
    kept = sorted(p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file())
    assert kept == ["TEMPLATE.md", "files/deck.pptx", "files/deck.pptx.preview/page-1.png", "files/img/chart.png",
                    "tools/build_deck.py"]
    assert "Navy title slides." in (files / "TEMPLATE.md").read_text()
    assert (await client.get(saved["preview"])).status_code == 200
    wrong = await client.post(f"/api/projects/{project['id']}/template", json={"name": "x", "file": "index.html"})
    assert wrong.status_code == 400


async def test_a_pattern_starts_an_empty_page_and_the_agent_is_told_to_follow_it(client, studio, project):
    fill(studio.store.workspace(project["id"]))
    saved = (await client.post(f"/api/projects/{project['id']}/template", json={"name": "House style"})).json()
    started = (await client.post("/api/projects", json={"name": "New report", "template": saved["id"]})).json()
    workspace = studio.store.workspace(started["id"])
    assert started["uses_template"] == {"id": saved["id"], "label": "House style", "kind": "page"}
    assert (workspace / "template/index.html").is_file() and (workspace / "template/TEMPLATE.md").is_file()
    assert "Acme" not in (workspace / "index.html").read_text() if (workspace / "index.html").is_file() else True
    context = studio.agent_context(started["id"])
    assert "“House style”" in context and "template/TEMPLATE.md" in context and "Do not reuse" in context
    # removing it takes the folder and the instruction away
    cleared = (await client.delete(f"/api/projects/{started['id']}/uses-template")).json()
    assert cleared["uses_template"] is None and not (workspace / "template").exists()
    assert "House style" not in studio.agent_context(started["id"])
    # and it can be attached to an existing project
    again = (await client.put(f"/api/projects/{project['id']}/uses-template", json={"template": saved["id"]})).json()
    assert again["uses_template"]["label"] == "House style"
    resaved = (await client.post(f"/api/projects/{project['id']}/template", json={"name": "Again"})).json()
    assert not (studio.saved.files(resaved["id"]) / "template").exists()       # a template never nests another


async def test_an_app_template_is_copied_and_runs_without_the_old_data(client, studio, project):
    fill(studio.store.workspace(project["id"]), app=True)
    saved = (await client.post(f"/api/projects/{project['id']}/template", json={"name": "Extractor"})).json()
    assert saved["kind"] == "app" and saved["mode"] == "copy" and saved["has_code"] is True
    started = (await client.post("/api/projects", json={"name": "Second extractor", "template": saved["id"]})).json()
    workspace = studio.store.workspace(started["id"])
    assert (workspace / "server.py").is_file() and (workspace / "studio.json").is_file()
    assert not (workspace / "data").exists() and not (workspace / "template").exists()
    assert started["uses_template"] is None and started["app"]["configured"] is True


async def test_export_import_edit_and_delete(client, studio, project):
    fill(studio.store.workspace(project["id"]), app=True)
    saved = (await client.post(f"/api/projects/{project['id']}/template", json={"name": "Extractor"})).json()
    exported = await client.get(f"/api/templates/{saved['id']}/export")
    assert exported.status_code == 200 and "Extractor.studio-template.zip" in exported.headers["content-disposition"]
    upload = {"file": ("x.zip", exported.content, "application/zip")}
    unsure = await client.post("/api/templates/import", files=upload)
    assert unsure.status_code == 409 and unsure.json()["error"]["code"] == "needs_confirmation"
    imported = await client.post("/api/templates/import", files=upload, data={"confirm": "1"})
    assert imported.status_code == 201, imported.text
    copy = imported.json()
    assert copy["id"] != saved["id"] and copy["imported"] is True and copy["label"] == "Extractor" and copy["has_code"]
    assert (studio.saved.files(copy["id"]) / "server.py").is_file()

    renamed = (await client.patch(f"/api/templates/{copy['id']}", json={"label": "Extractor (imported)"})).json()
    assert renamed["label"] == "Extractor (imported)"
    assert (await client.patch("/api/templates/report", json={"label": "x"})).json()["error"]["code"] == "builtin_template"
    assert (await client.delete(f"/api/templates/{copy['id']}")).status_code == 204
    assert (await client.delete(f"/api/templates/{copy['id']}")).status_code == 404


async def test_an_import_cannot_write_outside_the_template_or_pass_as_one(client, studio):
    def archive(entries):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return {"file": ("t.zip", out.getvalue(), "application/zip")}

    meta = json.dumps({"label": "Evil", "kind": "page"})
    for entries in ({"template.json": meta, "files/../../escape.txt": "x"}, {"template.json": meta, "/abs.txt": "x"},
                    {"template.json": meta, "other/readme.txt": "x"}, {"files/index.html": "x"},
                    {"template.json": "{}", "files/index.html": "x"}, {"template.json": meta}):
        answer = await client.post("/api/templates/import", files=archive(entries), data={"confirm": "1"})
        assert answer.status_code == 400, entries
    assert (await client.post("/api/templates/import", files={"file": ("t.zip", b"not a zip", "application/zip")})).status_code == 400
    assert not [t for t in (await client.get("/api/templates")).json() if t["source"] == "yours"]
    assert not (studio.settings.data_dir / "escape.txt").exists()
    fine = await client.post("/api/templates/import", files=archive({"template.json": meta, "files/index.html": "<h1>x</h1>"}))
    assert fine.status_code == 201 and fine.json()["has_code"] is False


async def test_the_agent_can_save_a_template(studio, project):
    from studio2_server import tools
    fill(studio.store.workspace(project["id"]))
    answer = await tools.call(studio, project["id"], "save_template", {"name": "From chat", "notes": "Serif headings."})
    assert not answer.get("is_error"), answer
    assert json.loads(answer["content"][0]["text"])["kind"] == "page"
    assert [t["label"] for t in studio.saved.list()] == ["From chat"]
