"""Examples that come with the studio (CONTRACT.md): the list, a copy as a project, and a source
whose text was not shipped being fetched again under its own id."""

import json
from pathlib import Path

import pytest

from studio2_server import tools
from studio2_server.sources import Sources

LIBRARY = Path(__file__).resolve().parents[1] / "library"
KINDS = {"report", "dashboard", "explainer", "interactive"}


@pytest.fixture
def examples_dir(settings, tmp_path):
    """A library with two examples and three broken ones, in place of the real one."""
    root = tmp_path / "library"
    made = {"model": "opus", "date": "2026-11-13", "requests": 1, "duration_s": 61, "depth": "quick",
            "request": "Make a page."}

    def add(name, meta, page="<main><h1>Hi</h1></main>", extra=None):
        folder = root / "examples" / name
        (folder / "files").mkdir(parents=True)
        if meta is not None:
            (folder / "example.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))
        if page is not None:
            (folder / "files" / "index.html").write_text(page)
        for path, text in (extra or {}).items():
            target = folder / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return folder

    index = {"sources": [{"id": "s1", "url": "https://example.org/a", "path": "sources/s1.txt", "original": None,
                          "title": "A", "kind": "html", "fetched_at": "2026-11-13T00:00:00Z", "chars": 10,
                          "sha256": "x", "pages": None}]}
    first = add("b-report", {"title": "A report", "summary": "One.", "kind": "report", "order": 20, "made": made,
                             "sources": 1, "check": None},
                '<main><p>Revenue was <span data-src="s1" data-quote="revenue was 4.2 billion dollars">$4.2 billion</span>.</p>'
                '<ol><li id="src-s1">A</li></ol></main>', {"sources/index.json": json.dumps(index)})
    (first / "preview.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    add("a-dashboard", {"title": "A dashboard", "summary": "Two.", "kind": "dashboard", "order": 10, "made": made,
                        "sources": 0, "check": None}, extra={"data/x.csv": "a,b\n1,2\n"})
    add("no-title", {"summary": "x", "kind": "report"})
    add("bad-json", "{not json")
    add("no-page", {"title": "No page", "summary": "x", "kind": "report"}, page=None)
    add("Bad_Name", {"title": "Bad name", "summary": "x", "kind": "report"})
    settings.library_dir = root
    return root


async def test_the_list_is_ordered_and_leaves_out_broken_ones(examples_dir, client):
    listed = (await client.get("/api/examples")).json()
    assert [e["id"] for e in listed] == ["a-dashboard", "b-report"]
    report = listed[1]
    assert report["title"] == "A report" and report["kind"] == "report" and report["made"]["model"] == "opus"
    assert report["preview"] == "/api/examples/b-report/preview" and listed[0]["preview"] is None
    assert report["files"] == 2 and report["bytes"] > 0 and report["sources"] == 1 and report["check"] is None
    picture = await client.get("/api/examples/b-report/preview")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    missing = await client.get("/api/examples/a-dashboard/preview")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "no_preview"
    for bad in ("nope", "..", "Bad_Name"):
        assert (await client.get(f"/api/examples/{bad}/preview")).status_code == 404


async def test_open_a_copy_makes_an_ordinary_project(examples_dir, client, studio):
    made = await client.post("/api/projects", json={"example": "a-dashboard"})
    assert made.status_code == 201, made.text
    project = made.json()
    assert project["name"] == "A dashboard" and project["from_example"] == "a-dashboard"
    workspace = studio.store.workspace(project["id"])
    assert (workspace / "index.html").read_text() == "<main><h1>Hi</h1></main>"
    assert (workspace / "data" / "x.csv").is_file() and not (workspace / "example.json").exists()
    assert project["head"]                                   # one first version
    named = (await client.post("/api/projects", json={"example": "b-report", "name": "Mine"})).json()
    assert named["name"] == "Mine"
    plain = (await client.post("/api/projects", json={"name": "Plain"})).json()
    assert plain["from_example"] is None
    # The shipped example is not touched by work in the copy.
    (workspace / "index.html").write_text("changed")
    assert (examples_dir / "examples" / "a-dashboard" / "files" / "index.html").read_text() != "changed"
    for body, code in (({"example": "nope"}, "unknown_example"), ({"example": "../templates/report"}, "unknown_example"),
                       ({"example": "a-dashboard", "template": "report"}, "invalid_request")):
        answer = await client.post("/api/projects", json=body)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == code, answer.text


async def test_a_source_without_its_text_is_fetched_again_under_its_id(examples_dir, client, studio, monkeypatch):
    project = (await client.post("/api/projects", json={"example": "b-report"})).json()
    pid, workspace = project["id"], studio.store.workspace(project["id"])
    sources = Sources(workspace)
    assert [s["id"] for s in sources.list()] == ["s1"] and not (workspace / "sources" / "s1.txt").exists()
    context = studio.agent_context(pid)
    assert "A report" in context and "fetch_source" in context and "not included" in context

    async def fake_download(url):
        return (b"<html><title>A</title><body><p>In 2025 revenue was 4.2 billion dollars, the company said.</p></body></html>",
                "text/html", url)

    monkeypatch.setattr(tools, "download", fake_download)
    answer = await tools.call(studio, pid, "fetch_source", {"url": "https://example.org/a"})
    assert "is_error" not in answer, answer
    view = json.loads(answer["content"][0]["text"])
    assert view["id"] == "s1" and view.get("restored") is True
    assert [s["id"] for s in sources.list()] == ["s1"]                 # no second entry
    assert "revenue was 4.2 billion dollars" in sources.text("s1")
    again = json.loads((await tools.call(studio, pid, "fetch_source", {"url": "https://example.org/a"}))["content"][0]["text"])
    assert again.get("already_saved") is True and again["id"] == "s1"   # now it is there: no second download
    assert "not included" not in studio.agent_context(pid)             # nothing is missing any more
    checked = json.loads((await tools.call(studio, pid, "check_page", {"path": "index.html"}))["content"][0]["text"])
    assert checked["summary"]["failed"] == 0 and checked["summary"]["verified"] == 1


def test_the_examples_that_ship_are_whole():
    """Every shipped example: facts present, a page, no source text, no leftovers of the working copy."""
    folders = sorted((LIBRARY / "examples").glob("*/example.json"))
    assert folders, "the studio ships at least one example"
    for manifest in folders:
        meta, files = json.loads(manifest.read_text("utf-8")), manifest.parent / "files"
        assert meta["title"] and meta["summary"] and meta["kind"] in KINDS, manifest
        assert meta["made"]["model"] and meta["made"]["date"] and meta["made"]["request"], manifest
        assert (files / "index.html").is_file() and not (files / "studio.json").exists(), manifest
        assert (manifest.parent / "preview.jpg").is_file(), manifest
        for path in files.rglob("*"):
            relative = path.relative_to(files)
            assert not ({".git", "research", "attachments", "__pycache__", "node_modules"} & set(relative.parts)), path
            if relative.parts[0] == "sources" and path.is_file():
                assert relative.name == "index.json", f"{path}: saved source text must not be shipped"
        assert sum(p.stat().st_size for p in manifest.parent.rglob("*") if p.is_file()) < 4_000_000, manifest


def test_git_keeps_every_file_of_a_shipped_example():
    """A rule such as `data/` in an ignore file would drop an example's data from the repository
    without a word (it did once): nothing under library/examples may be ignored."""
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git is not here")
    root = LIBRARY / "examples"
    inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if inside.returncode != 0:
        pytest.skip("not a git work tree")
    ignored = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "."],
                             capture_output=True, text=True, check=True).stdout.split("\n")
    assert [line for line in ignored if line] == []
