"""Export (CONTRACT.md): one HTML file with its styles, scripts, pictures and data inside, a folder
to host or run, what each leaves out or still needs, and the cases that are refused with a reason."""

import base64
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from studio2_server import export

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sales</title>
<link rel="stylesheet" href="css/app.css">
<link rel="icon" href="img/dot.png">
<link rel="stylesheet" href="https://fonts.example.org/font.css">
<script src="https://cdn.example.com/chart.js"></script>
</head><body>
<h1>Sales</h1><img src="img/dot.png" alt=""><img src="https://img.example.net/logo.png" alt="">
<a href="about.html">About</a><a href="https://example.org/x.html">Elsewhere</a>
<script src="app.js"></script>
</body></html>
"""


def make(workspace, files):
    for path, content in files.items():
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content) if isinstance(content, bytes) else target.write_text(content, "utf-8")


def folder(workspace, project):
    target = workspace.parent / "export-test.zip"
    export.folder_zip(workspace, project, target)
    return zipfile.ZipFile(io.BytesIO(target.read_bytes()))


@pytest.fixture
def site(studio, project):
    workspace = studio.store.workspace(project["id"])
    make(workspace, {
        "index.html": PAGE, "about.html": "<h1>About</h1>",
        "css/app.css": '@import "base.css";\nbody { background: url("../img/dot.png"); }\n.x { background: url(https://img.example.net/bg.png); }\n',
        "css/base.css": "h1 { color: teal; }\n",
        "app.js": 'fetch("data/sales.json").then(r => r.json()).then(d => document.title = d.total);\nvar end = "</script>";\n',
        "img/dot.png": PNG, "data/sales.json": '{"total": 42}', "data/rows.csv": "a,b\n1,2\n",
        "sources/index.json": '{"sources": []}', "sources/s1.txt": "someone else's article",
        "research/notes.md": "working notes", "attachments/upload.pdf": b"%PDF", "state/app.db": b"db",
        ".env": "SECRET=1", "config/.env.local": "SECRET=2", "scripts/build.py": "print('x')\n"})
    return workspace


def test_one_file_holds_the_styles_scripts_pictures_and_data(site):
    built = export.one_file(site)
    html = built["html"]
    assert built["available"] and built["reason"] is None and built["bytes"] == len(html.encode())
    assert "h1 { color: teal; }" in html and '<link rel="stylesheet" href="css/app.css">' not in html     # the sheet and its import
    assert 'url("data:image/png;base64,' in html                                  # a picture named by the sheet
    assert '<img src="data:image/png;base64,' in html and 'rel="icon" href="data:image/png' in html
    assert 'fetch("data/sales.json")' in html and '<script src="app.js">' not in html
    assert 'var end = "<\\/script>";' in html                                     # cannot end the script early
    assert html.count("Studio export: the page's data files are inside") == 1 and html.index("Studio export") < html.index("<title>")
    assert built["inlined"] == 4 and built["embedded"]["files"] == 3              # sales.json, rows.csv, sources/index.json
    assert base64.b64encode(b'{"total": 42}').decode() in html
    # What is still outside is said.
    assert built["external"] == ["cdn.example.com", "fonts.example.org", "img.example.net"]
    assert any("about.html" in note and "1 other page" in note for note in built["notes"])
    assert "It still loads from: cdn.example.com, fonts.example.org, img.example.net." in html
    # What must never leave.
    for secret in ("someone else's article", "working notes", "SECRET", "%PDF"):
        assert secret not in html
    assert html.lstrip().lower().startswith("<!doctype html>")


def test_the_script_that_answers_requests_is_valid_javascript(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not here")
    shim = export.SHIM.replace("__FILES__", "{}").replace("__DIR__", '""')
    source = tmp_path / "shim.js"
    source.write_text(shim[shim.index(">") + 1: shim.rindex("</script>")], "utf-8")
    done = subprocess.run(["node", "--check", str(source)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "window.fetch=" in shim and "XMLHttpRequest" in shim          # both ways a page loads data


def test_a_page_in_a_folder_and_names_that_point_nowhere(site):
    make(site, {"docs/guide.html": '<link rel="stylesheet" href="../css/base.css"><img src="/img/dot.png">'
                                   '<img src="../../outside.png"><img src="gone.png"><script>var t = `${base}/x.png`;</script>'})
    built = export.one_file(site, "docs/guide.html")
    assert built["available"] and "h1 { color: teal; }" in built["html"] and built["html"].count("data:image/png") == 1
    assert '__DIR__' not in built["html"] and '"docs/"' in built["html"]          # requests are resolved from the page's folder
    assert any("gone.png" in note for note in built["notes"]) and not any("outside" in note for note in built["notes"])
    assert export.one_file(site, "nope.html")["reason"].startswith("There is no page")
    assert export.one_file(site, "data/sales.json")["available"] is False
    assert export.one_file(site, "../index.html")["available"] is False


def test_every_address_of_a_picture_is_inside_and_nothing_else_in_the_tag_changes(site):
    make(site, {"img/b.png": PNG, "img/c.png": PNG, "media/clip.mp4": b"\x00\x00\x00\x18ftypmp42", "img/sprite.svg": "<svg/>",
                "pics.html": '<img src="img/dot.png" srcset="img/dot.png 1x, img/b.png 2x" alt="img/dot.png" title=img/dot.png>'
                             '<picture><source srcset="img/c.png" media="(min-width: 10px)">'
                             '<source srcset="img/b.png 2x,https://img.example.net/w.png 3x">'
                             '<img src="img/dot.png" alt=""></picture>'
                             '<img srcset="img/gone.png 2x" alt="">'
                             '<video><source src="media/clip.mp4" type="video/mp4"></video>'
                             '<div style="background-image: url(&quot;img/b.png&quot;); color: red">x</div>'
                             "<p style='background:url(img/c.png)'>y</p>"
                             '<object data="img/c.png" type="image/png"></object>'
                             '<svg><image href="img/b.png" width="1" height="1"/>'
                             '<use href="img/sprite.svg#a"/></svg>'})
    built = export.one_file(site, "pics.html")
    html = built["html"]
    assert built["available"]
    # The only local names left: the alt text, the title, a picture that does not exist and the <use>.
    page = html.split("</script>", 1)[1]                  # after the script that holds the data files
    assert re.findall(r"img/[a-z]+\.[a-z]+", page) == ["img/dot.png", "img/dot.png", "img/gone.png", "img/sprite.svg"]
    assert "media/clip.mp4" not in html
    # Only the value of an address changes: the alt text and the title keep their words.
    assert 'alt="img/dot.png"' in html and "title=img/dot.png" in html
    # Each candidate keeps its descriptor; a picture named twice is inside both times.
    assert re.search(r'srcset="data:image/png;base64,[A-Za-z0-9+/=]+ 1x, data:image/png;base64,[A-Za-z0-9+/=]+ 2x"', html)
    assert re.search(r'<source srcset="data:image/png;base64,[A-Za-z0-9+/=]+" media="\(min-width: 10px\)">', html)
    assert re.search(r'srcset="data:image/png;base64,[A-Za-z0-9+/=]+ 2x, https://img.example.net/w.png 3x"', html)
    assert '<source src="data:video/mp4;base64,' in html and 'data="data:image/png;base64,' in html
    assert '<image href="data:image/png;base64,' in html
    assert "url(&quot;data:image/png;base64," in html and "color: red" in html and "url(&quot;data:image/png" in html
    assert "img.example.net" in built["external"]
    # What one file cannot hold, or names nothing, is said.
    assert any("img/gone.png" in note for note in built["notes"])
    assert any("img/sprite.svg" in note and "<use>" in note for note in built["notes"])


SCRIPTS = {
    "double": 'const u = "<div style=\\"background:url(img/a.png)\\"></div>";',
    "single": "const v = '<img src=\"img/dot.png\" srcset=\"img/dot.png 2x\" style=\"background:url(img/dot.png)\">';",
    "single-escaped": "const w = '<img src=\\'img/dot.png\\'><link rel=\\'icon\\' href=\\'img/dot.png\\'>';",
    "template": "const x = `<p style=\"background:url('img/a.png')\"><img src=\"img/dot.png\"></p><style>.q{background:url(img/a.png)}</style>`;",
}
JSON_BLOB = '{"card": "<img src=\\"img/a.png\\" style=\\"background:url(img/a.png)\\">"}'


def test_html_inside_a_script_or_other_text_is_never_changed(site):
    scripts = "".join(f"<script>{code}</script>" for code in SCRIPTS.values())
    make(site, {"img/a.png": PNG, "ui.js": 'document.body.insertAdjacentHTML("beforeend", "<img src=\\"img/a.png\\">");\n',
                "text.html": "<!doctype html><html><head><title>&lt;img src=\"img/dot.png\"&gt; x</title>"
                             "<style>/* <img src=\"img/dot.png\"> */ .a{background:url(img/a.png)}</style></head><body>"
                             + scripts + f'<script type="application/json" id="blob">{JSON_BLOB}</script>'
                             '<script src="ui.js"></script>'
                             '<textarea><img src="img/dot.png" style="background:url(img/dot.png)"></textarea>'
                             '<!-- <img src="img/dot.png"> --><img id="real" src="img/dot.png" alt=""></body></html>'})
    built = export.one_file(site, "text.html")
    html = built["html"]
    for code in list(SCRIPTS.values()) + [JSON_BLOB, '"<img src=\\"img/a.png\\">"']:
        assert code in html, code                                                     # every script exactly as written
    assert '<textarea><img src="img/dot.png" style="background:url(img/dot.png)"></textarea>' in html
    assert '<!-- <img src="img/dot.png"> -->' in html and '<title>&lt;img src="img/dot.png"&gt; x</title>' in html
    assert '/* <img src="img/dot.png"> */ .a{background:url("data:image/png;base64,' in html   # the style is still CSS
    assert '<img id="real" src="data:image/png;base64,' in html                       # a real tag still goes inside
    # The pictures the scripts build markup with are said, since they cannot go inside.
    assert any("script" in note and "img/a.png" in note and "img/dot.png" in note for note in built["notes"])
    assert not any("“" in note for note in built["notes"])                            # and nothing is reported missing
    if shutil.which("node"):
        for number, code in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            source = site.parent / f"script-{number}.js"
            source.write_text(code, "utf-8")
            done = subprocess.run(["node", "--check", str(source)], capture_output=True, text=True)
            assert done.returncode == 0, (code, done.stderr)


def test_a_base_inside_the_project_is_where_addresses_start(site):
    make(site, {"pages/based.html": '<base href="../"><img src="img/dot.png" alt=""><script>fetch("data/sales.json")</script>',
                "elsewhere.html": '<base href="https://cdn.example.com/site/"><img src="img/dot.png" alt="">'})
    based = export.one_file(site, "pages/based.html")
    assert '<img src="data:image/png;base64,' in based["html"] and not based["notes"]
    assert 'var F=' in based["html"] and ',D=""' in based["html"]                   # requests start from the base too
    elsewhere = export.one_file(site, "elsewhere.html")
    assert any("<base href=" in note and "cdn.example.com" in note for note in elsewhere["notes"])


def test_a_srcset_is_read_the_way_a_browser_reads_it():
    assert export._srcset("a.png 1x, b.png 2x") == [("a.png", " 1x"), ("b.png", " 2x")]
    assert export._srcset("a.png, b.png 2x,c.png") == [("a.png", ""), ("b.png", " 2x"), ("c.png", "")]
    assert export._srcset("a.png,b.png 2x") == [("a.png,b.png", " 2x")]        # a comma inside an address is part of it
    assert export._srcset(" data:image/png;base64,AA== 1x , c.png 640w") == \
        [("data:image/png;base64,AA==", " 1x"), ("c.png", " 640w")]
    assert export._srcset("") == []


def test_what_one_file_cannot_hold_is_refused_with_the_reason(site, monkeypatch):
    make(site, {"mod.html": '<script type="module" src="main.js"></script>', "main.js": 'import { x } from "./lib.js";\n',
                "lib.js": "export const x = 1;\n",
                "inline.html": '<script type="importmap">{"imports": {"three": "https://cdn.example.com/three.js"}}</script>'
                               '<script type="module">import * as T from "three"; import "./lib.js";</script>',
                "fine.html": '<script type="module">import * as T from "https://esm.example.org/three.js";</script>'})
    assert "imports other files" in export.one_file(site, "mod.html")["reason"]
    inline = export.one_file(site, "inline.html")
    assert not inline["available"] and "Use the folder" in inline["reason"] and inline["external"] == ["cdn.example.com"]
    fine = export.one_file(site, "fine.html")
    assert fine["available"] and fine["external"] == ["esm.example.org"]
    monkeypatch.setattr(export, "MAX_BYTES", 1_000)
    assert "the limit is" in export.one_file(site)["reason"]
    make(site, {"studio.json": '{"run": "python3 server.py"}'})
    assert "its own back end" in export.one_file(site)["reason"]


def test_opening_the_dialog_reads_sizes_and_builds_nothing(site, project, monkeypatch):
    make(site, {"media/film.mp4": b"\x01" * 3_000_001, "film.html": '<video src="media/film.mp4" poster="img/dot.png"></video>'})
    exact = {page: export.one_file(site, page) for page in ("index.html", "film.html")}

    def no_reading(*_args, **_kwargs):
        raise AssertionError("a file was read to be put inside")
    monkeypatch.setattr(export.base64, "b64encode", no_reading)
    for page, built in exact.items():
        told = export.describe(site, project, page)["html"]
        assert "html" not in told and told["available"] is built["available"]
        # The sizes it reports are the sizes the file will have, to the byte.
        assert {key: told[key] for key in ("bytes", "inlined", "embedded", "external", "notes")} == \
               {key: built[key] for key in ("bytes", "inlined", "embedded", "external", "notes")}
    assert exact["film.html"]["bytes"] > 4_000_000
    # Past the limit, the file is refused before any of it is read.
    monkeypatch.setattr(export, "MAX_BYTES", 4_000_000)
    refused = export.one_file(site, "film.html")
    assert not refused["available"] and "the limit is 4 MB" in refused["reason"] and refused["html"] is None


def test_the_folder_of_a_page_says_how_to_open_and_host_it(site, project):
    info = export.folder_info(site, project)
    assert info["available"] and info["run"] is None and info["tools"] == [] and info["needs"] == []
    names = folder(site, project).namelist()
    assert {"index.html", "about.html", "css/app.css", "data/sales.json", "sources/index.json", "scripts/build.py",
            "EXPORT-README.md"} <= set(names) and "run.sh" not in names
    for gone in ("sources/s1.txt", "research/notes.md", "attachments/upload.pdf", "state/app.db", ".env", "config/.env.local"):
        assert gone not in names, gone
    assert not [name for name in names if name.startswith(".git/")]
    assert info["files"] == len(names)
    readme = folder(site, project).read("EXPORT-README.md").decode()
    assert project["name"] in readme and "python3 -m http.server" in readme and "static host" in readme
    assert "`about.html`" in readme and "other people's writing" in readme


def test_the_folder_of_an_app_says_how_to_run_it_and_what_it_will_miss(site, project):
    make(site, {"studio.json": json.dumps({"run": "uv run --with fastapi uvicorn server:app --host 127.0.0.1 --port $PORT"}),
                "server.py": 'import os\nMODEL = os.environ["STUDIO_MODEL_URL"]\nDATA = os.environ.get("STUDIO_DATA_URL")\n'})
    info = export.folder_info(site, project)
    assert info["run"].startswith("uv run") and "uv (https://docs.astral.sh/uv/)" in info["tools"][0]
    assert len(info["needs"]) == 2 and "STUDIO_MODEL_URL" in info["needs"][0] and "STUDIO_DATA_URL" in info["needs"][1]
    archive = folder(site, project)
    script = archive.read("run.sh").decode()
    assert script.startswith("#!/bin/sh") and 'PORT="${PORT:-8000}"' in script
    assert "exec sh -c 'uv run --with fastapi uvicorn server:app --host 127.0.0.1 --port $PORT'" in script
    assert archive.getinfo("run.sh").external_attr >> 16 == 0o755            # it can be started as it is
    readme = archive.read("EXPORT-README.md").decode()
    assert "./run.sh" in readme and "PORT=9000" in readme and "What it used from the studio" in readme
    assert "state/" in readme and "python3 -m http.server" not in readme
    make(site, {"studio.json": json.dumps({"run": "node server.js --name 'my app' --port $PORT"})})
    quoted = folder(site, project).read("run.sh").decode()
    assert "exec sh -c 'node server.js --name '\"'\"'my app'\"'\"' --port $PORT'" in quoted     # quotes in the command survive
    assert export.folder_info(site, project)["tools"] == ["Node.js (https://nodejs.org/)"]
    make(site, {"studio.json": json.dumps({"run": "odd 'unbalanced --port $PORT"})})
    assert export.folder_info(site, project)["tools"] == ["“odd” on your PATH"]                       # never a crash


def test_a_project_brought_in_from_a_folder_has_no_export_folder(site, project):
    attached = {**project, "attach": {"mode": "in-place", "path": "/somewhere"}}
    info = export.folder_info(site, attached)
    assert not info["available"] and "its own repository" in info["reason"]
    with pytest.raises(export.ExportUnavailable):
        export.folder_zip(site, attached, site.parent / "never.zip")
    assert not (site.parent / "never.zip").exists()


async def test_the_routes(client, site, project):
    pid = project["id"]
    options = (await client.get(f"/api/projects/{pid}/export")).json()
    assert options["app"] is False and options["page"] == "index.html" and "about.html" in options["pages"]
    assert options["html"]["available"] and "html" not in options["html"] and options["html"]["bytes"] > 0
    assert options["folder"]["available"] and options["folder"]["files"] > 5
    page = await client.get(f"/api/projects/{pid}/export/page")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["content-disposition"] == 'attachment; filename="Test project.html"' and "h1 { color: teal; }" in page.text
    assert (await client.get(f"/api/projects/{pid}/export/page", params={"page": "about.html"})).text.count("About") == 1
    missing = await client.get(f"/api/projects/{pid}/export/page", params={"page": "nope.html"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_page"
    folder = await client.get(f"/api/projects/{pid}/export/folder")
    assert folder.status_code == 200 and folder.headers["content-type"] == "application/zip"
    assert folder.headers["content-disposition"] == 'attachment; filename="Test project-export.zip"'
    assert "EXPORT-README.md" in zipfile.ZipFile(io.BytesIO(folder.content)).namelist()
    assert not list(Path(tempfile.gettempdir()).glob("studio2-export-*"))         # written to a file, sent, removed
    make(site, {"studio.json": '{"run": "python3 server.py"}'})
    refused = await client.get(f"/api/projects/{pid}/export/page")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "export_unavailable"
    assert (await client.get(f"/api/projects/{pid}/export")).json()["html"]["available"] is False
    assert (await client.get("/api/projects/p_nope/export")).status_code == 404


def test_the_agent_is_told_where_export_is(studio, project):
    context = " ".join((studio.settings.library_dir / "system-prompt.md").read_text().split())   # said once
    assert "Export in the page's menu" in context and "one HTML file" in context
