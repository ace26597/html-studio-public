"""check_site: all the pages of a project read together (CONTRACT.md, "Projects of many pages").
Zero model calls; nothing is fetched and no browser is opened."""

import json
import os
import time
from pathlib import Path

from studio2_server import tools
from studio2_server.sitecheck import MAX_FINDINGS, check_site

LIBRARY = Path(__file__).resolve().parents[1] / "library"


def page(title: str | None, body: str, css: str | None = "styles/site.css", viewport: bool = True) -> str:
    head = (f"<title>{title}</title>" if title is not None else "") \
        + ('<meta name="viewport" content="width=device-width, initial-scale=1">' if viewport else "") \
        + (f'<link rel="stylesheet" href="{css}">' if css else "")
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">{head}</head><body>{body}</body></html>"


def nav(prefix: str = "", current: str = "") -> str:
    items = [("index.html", "Home"), ("events.html", "Events"), ("plants/index.html", "Plants"), ("contact.html", "Contact")]
    links = "".join(f'<a href="{prefix}{href}"{" aria-current=\"page\"" if href == current else ""}>{label}</a>'
                    for href, label in items)
    return f"<nav>{links}</nav>"


def clean_site(root: Path) -> Path:
    """The garden club: four pages, one stylesheet, the same menu everywhere, nothing wrong."""
    (root / "styles").mkdir(parents=True)
    (root / "plants").mkdir()
    (root / "media").mkdir()
    (root / "styles" / "site.css").write_text(":root{--bg:#fff}body{background:url(../media/leaf.svg) no-repeat}")
    (root / "media" / "leaf.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "media" / "rose.jpg").write_bytes(b"\xff\xd8 a picture")
    (root / "index.html").write_text(page("Garden club", nav(current="index.html")
        + '<main><p>We meet each month.</p><a href="events.html#june">June meeting</a>'
          '<a href="https://example.org/seeds">Seed swap</a><a href="mailto:hello@example.org">Write</a>'
          '<a href="#top">Top</a></main>'))
    (root / "events.html").write_text(page("Events", nav(current="events.html")
        + '<nav aria-label="On this page"><a href="#june">June</a></nav><h2 id="june">June</h2>'))
    (root / "contact.html").write_text(page("Contact", nav(current="contact.html") + "<p>Write to us.</p>"))
    (root / "plants" / "index.html").write_text(page("Plants", nav("../", "plants/index.html")
        + '<img src="../media/rose.jpg" srcset="../media/rose.jpg 2x" alt="A rose">', css="../styles/site.css"))
    # Working folders and pieces are not pages a reader sees.
    (root / "research").mkdir()
    (root / "research" / "notes.html").write_text("<html><body>notes, never linked</body></html>")
    (root / "build" / "sections").mkdir(parents=True)
    (root / "build" / "sections" / "01-intro.html").write_text("<section><h2>Intro</h2></section>")
    (root / ".well-known").mkdir()
    (root / ".well-known" / "hidden.html").write_text("<html><body>hidden</body></html>")
    return root


def test_a_clean_site_has_no_findings(tmp_path):
    result = check_site(clean_site(tmp_path))
    assert result["findings"] == [], result["findings"]
    assert result["pages"] == 4 and result["pieces"] == 1
    assert {p["path"] for p in result["pages_list"]} == {"index.html", "events.html", "contact.html", "plants/index.html"}
    assert all(p["reachable"] for p in result["pages_list"])
    assert result["outside_links"] == {"count": 1, "sites": ["example.org"]}
    assert result["truncated"] is None and result["unchecked"]["count"] == 0
    assert "script builds" in result["covers"] and "No findings" in result["next"]
    assert json.loads((tmp_path / "research" / "site-check.json").read_text())["pages"] == 4


def planted_site(root: Path) -> Path:
    clean_site(root)
    (root / "index.html").write_text(page("Garden club", nav(current="index.html")
        + '<a href="events.html#july">July</a>'               # broken anchor
          '<a href="news.html">News</a>'                      # broken link
          '<a href="../../outside/secret.html">Out</a>'       # leaves the project
          '<a href="research/notes.html">Our notes</a>'       # there, but an export leaves it out
          '<img src="media/missing.png" alt="">'              # missing picture
          '<script src="app.js"></script>'))                  # empty script
    (root / "app.js").write_text("")
    # A differing menu: no Contact, and a page the others do not list.
    (root / "contact.html").write_text(page("Contact", '<nav><a href="index.html">Home</a><a href="events.html">Events</a>'
                                                       '<a href="plants/index.html">Plants</a><a href="shop.html">Shop</a></nav>'))
    (root / "shop.html").write_text(page("Events", "<p>Nothing links back.</p>", css=None, viewport=False))
    (root / "old.html").write_text(page(None, nav(current="")))   # an orphan with no title
    return root


def test_planted_faults_are_each_found_once(tmp_path):
    result = check_site(planted_site(tmp_path))
    found = {(f["page"], f["kind"], f.get("target")) for f in result["findings"]}
    expected = {
        ("index.html", "broken_anchor", "events.html#july"),
        ("index.html", "broken_link", "news.html"),
        ("index.html", "leaves_project", "../../outside/secret.html"),
        ("index.html", "not_exported", "research/notes.html"),
        ("index.html", "missing_file", "media/missing.png"),
        ("index.html", "empty_file", "app.js"),
        ("old.html", "orphan", None),
        ("old.html", "no_title", None),
        ("shop.html", "dead_end", None),
        ("shop.html", "no_viewport", None),
        ("shop.html", "styles_differ", None),
        ("shop.html", "nav_differs", None),
        ("contact.html", "nav_differs", None),
        ("events.html", "duplicate_title", "shop.html"),
    }
    assert found == expected, sorted(found ^ expected)
    contact = next(f for f in result["findings"] if f["page"] == "contact.html")
    assert "lacks contact.html" in contact["detail"] and "adds shop.html" in contact["detail"]
    assert result["findings"][0]["page"] == "index.html"         # the start page first
    assert result["by_kind"]["nav_differs"] == 2 and sum(result["by_kind"].values()) == len(expected)
    assert "run check_site again" in result["next"]
    assert not (tmp_path.parent / "outside").exists()


def test_a_link_out_of_the_project_through_a_symlink_is_not_followed(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.html").write_text(page("Secret", "<p>private</p>"))
    root = clean_site(tmp_path / "project")
    os.symlink(outside, root / "linked")
    (root / "contact.html").write_text(page("Contact", nav(current="contact.html") + '<a href="linked/secret.html">x</a>'))
    result = check_site(root)
    assert [(f["kind"], f.get("target")) for f in result["findings"]] == [("leaves_project", "linked/secret.html")]
    assert "linked/secret.html" not in {p["path"] for p in result["pages_list"]}


def test_a_missing_start_page_is_said_and_no_orphans_are_guessed(tmp_path):
    root = clean_site(tmp_path)
    result = check_site(root, start="home.html")
    assert [f["kind"] for f in result["findings"]] == ["no_start"]


def test_in_an_app_a_path_that_is_no_file_is_unchecked_not_broken(tmp_path):
    root = clean_site(tmp_path)
    (root / "studio.json").write_text(json.dumps({"run": "uv run python server.py"}))
    (root / "contact.html").write_text(page("Contact", nav(current="contact.html")
                                           + '<a href="/api/members">Members</a><img src="/media/live.png">'))
    result = check_site(root)
    assert result["findings"] == []
    assert result["unchecked"]["count"] == 2 and "back end" in result["unchecked"]["why"]


def test_a_page_reached_only_through_a_frame_is_not_a_dead_end(tmp_path):
    root = clean_site(tmp_path)
    (root / "report.pdf.preview").mkdir()
    (root / "report.pdf.preview" / "index.html").write_text(page("report.pdf", "<img src='page-1.png'>", css=None))
    (root / "report.pdf.preview" / "page-1.png").write_bytes(b"\x89PNG")
    (root / "contact.html").write_text(page("Contact", nav(current="contact.html")
                                           + '<iframe src="report.pdf.preview/index.html"></iframe>'))
    assert check_site(root)["findings"] == []


def site_of(root: Path, count: int) -> Path:
    (root / "styles").mkdir(parents=True)
    (root / "styles" / "site.css").write_text("body{margin:0}")
    links = "".join(f'<a href="p{n:03}.html">{n}</a>' for n in range(0, count, 25))
    for n in range(count):
        name = "index.html" if n == 0 else f"p{n:03}.html"
        body = f'<nav><a href="index.html">Home</a>{links}</nav><h1 id="top-{n}">Page {n}</h1>' \
               f'<a href="p{(n + 1) % count:03}.html#top-{(n + 1) % count}">Next</a>'
        (root / name).write_text(page(f"Page {n}", body))
    (root / "p000.html").write_text(page("Page 0 again", f'<nav><a href="index.html">Home</a>{links}</nav>'
                                                          '<h1 id="top-0">Page 0</h1><a href="p001.html">Next</a>'))
    return root


def test_three_hundred_pages_stay_fast_and_the_answer_fits(tmp_path):
    root = site_of(tmp_path, 300)
    started = time.perf_counter()
    result = check_site(root, max_pages=400)
    took = time.perf_counter() - started
    print(f"\ncheck_site, 301 pages: {took * 1000:.0f} ms")
    assert took < 5, took
    assert result["pages"] == 301 and result["findings"] == [], result["findings"][:5]
    assert len(result["pages_list"]) == 100 and result["truncated"] is None
    capped = check_site(root)                     # 200 by default, read from the start page outwards
    assert capped["pages"] == 200 and "101 more pages" in capped["truncated"]["pages"]
    assert capped["findings"] == []               # no page read is an orphan for want of an unread one


def test_findings_are_capped_and_the_rest_are_saved(tmp_path):
    root = tmp_path
    for n in range(400):
        (root / f"p{n}.html").write_text("<html><head></head><body><p>bare</p></body></html>")
    result = check_site(root, max_pages=400)
    assert len(result["findings"]) == MAX_FINDINGS and "more findings" in result["truncated"]["findings"]
    saved = json.loads((root / "research" / "site-check.json").read_text())
    assert len(saved["findings"]) == sum(result["by_kind"].values()) > MAX_FINDINGS


async def test_the_tool_is_on_every_studio_and_its_answer_fits(studio, project):
    workspace = studio.store.workspace(project["id"])
    site_of(workspace, 300)
    assert "check_site" in {name for name, *_ in tools.registered(studio, project["id"])}
    answer = await tools.call(studio, project["id"], "check_site", {"max_pages": 400})
    assert not answer.get("is_error"), answer
    text = answer["content"][0]["text"]
    assert len(text) < tools.RESULT_CAP and json.loads(text)["pages"] == 301
    bad = await tools.call(studio, project["id"], "check_site", {"max_pages": "many"})
    assert bad.get("is_error") and "max_pages" in bad["content"][0]["text"]
    assert "mcp__studio__check_site" in (LIBRARY / "agents" / "checker.md").read_text()


# -- the large-documents joiner, on pages (library/skills/large-documents/reference/join-pages.py) -------
JOINER = LIBRARY / "skills" / "large-documents" / "reference" / "join-pages.py"
APPENDIX = "<section id=\"checked-numbers\"><h2>Checked numbers</h2><script>fetch('{{check}}')</script></section>"


def ledger_project(root: Path) -> None:
    import csv
    from studio2_server.sources import Sources
    sources = Sources(root)
    sources.save(text="The club counted 1,240 members in 2025, up from 980 in 2020.", title="Club yearbook",
                 kind="html", url="https://example.org/yearbook")
    (root / "data").mkdir(exist_ok=True)
    with open(root / "data" / "plots.csv", "w", newline="") as handle:
        csv.writer(handle).writerows([["plot", "beds"], ["north", 30], ["south", 12]])
    (root / "data" / "facts.json").write_text(json.dumps({"facts": [
        {"key": "members_2025", "label": "Members", "value": 1240, "unit": "people", "period": "2025",
         "source": "s1", "obtained": "quoted", "quote": "The club counted 1,240 members in 2025"},
        {"key": "members_2020", "label": "Members", "value": 980, "unit": "people", "period": "2020",
         "source": "s1", "obtained": "quoted", "quote": "up from 980 in 2020"},
        {"key": "beds", "label": "Beds", "value": 42, "unit": "beds", "source": "data/plots.csv", "obtained": "data",
         "sql": "select sum(beds) from 'data/plots.csv'"},
        {"key": "growth", "label": "Growth in members", "value": 260, "unit": "people", "obtained": "computed",
         "from": ["members_2025", "members_2020"], "formula": "members_2025 - members_2020"}]}))
    (root / "styles").mkdir(exist_ok=True)
    (root / "styles" / "site.css").write_text(":root{--bg:#fff}")


def join(root: Path, *extra: str):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-I", str(JOINER), str(root), *extra], capture_output=True, text=True)


def test_a_site_joined_from_the_ledger_passes_check_site_and_check_page(tmp_path):
    from studio2_server.check import check_page
    root = tmp_path
    ledger_project(root)
    (root / "build" / "pages").mkdir(parents=True)
    (root / "build" / "page-shell.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title><!-- join:title --></title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="{{root}}styles/site.css"></head><body><!-- join:nav -->'
        '<main><!-- join:content --></main><h2>Sources</h2><!-- join:sources -->' + APPENDIX + '</body></html>')
    (root / "build" / "pages" / "home.html").write_text(
        "<h1>Garden survey</h1><p>We have {{fact:members_2025}} members, {{fact:growth}} more than in 2020.</p>"
        '<a href="chapters/beds.html#count">Beds</a>')
    (root / "build" / "pages" / "beds.html").write_text(
        '<h1>Beds</h1><p id="count">The plots hold {{fact:beds}} beds.</p><a href="../index.html">Back</a>')
    outline = {"kind": "site", "shell": "build/page-shell.html", "pages": [
        {"id": "home", "path": "index.html", "title": "Garden survey", "nav": "Home", "module": "build/pages/home.html"},
        {"id": "beds", "path": "chapters/beds.html", "title": "Beds · Garden survey", "nav": "Beds",
         "module": "build/pages/beds.html"}]}
    (root / "build" / "outline.json").write_text(json.dumps(outline))
    ran = join(root)
    assert ran.returncode == 0, ran.stderr
    assert "wrote 2 page(s)" in ran.stdout and "3 marked figures" in ran.stdout
    beds = (root / "chapters" / "beds.html").read_text()
    assert 'href="../styles/site.css"' in beds and 'href="../index.html">Home' in beds
    assert 'aria-current="page">Beds' in beds and "../checks/chapters-beds.html.json" in beds
    site = check_site(root)
    assert site["findings"] == [] and site["pages"] == 2 and site["pieces"] == 3, site["findings"]
    for page_path in ("index.html", "chapters/beds.html"):
        checked = check_page(root, page_path)
        assert checked["summary"]["failed"] == 0 and checked["not_listed"] == [], (page_path, checked["items"])
        assert checked["unmarked"] == [], checked["unmarked"]
    home = check_page(root, "index.html")
    assert home["summary"]["verified"] == 1 and home["summary"]["derived"] == 1 and 'id="src-s1"' in (root / "index.html").read_text()
    assert (root / "checks" / "chapters-beds.html.json").is_file()


def test_a_report_joined_from_sections_and_what_stops_the_join(tmp_path):
    from studio2_server.check import check_page
    root = tmp_path
    ledger_project(root)
    (root / "build" / "sections").mkdir(parents=True)
    (root / "build" / "shell.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Garden survey</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="{{root}}styles/site.css">'
        '</head><body><h1>Garden survey</h1><!-- join:toc --><!-- join:sections --><h2>Sources</h2><!-- join:sources -->'
        + APPENDIX + '</body></html>')
    (root / "build" / "sections" / "01-people.html").write_text(
        '<section id="people"><h2>People</h2><p>{{fact:members_2025}} members.</p></section>')
    (root / "build" / "sections" / "02-beds.html").write_text(
        '<section id="beds"><h2>Beds</h2><p>{{fact:beds}} beds.</p></section>')
    outline = {"kind": "report", "page": "index.html", "shell": "build/shell.html", "sections": [
        {"id": "people", "title": "People", "module": "build/sections/01-people.html"},
        {"id": "beds", "title": "Beds", "module": "build/sections/02-beds.html"}]}
    (root / "build" / "outline.json").write_text(json.dumps(outline))
    assert join(root).returncode == 0
    page_text = (root / "index.html").read_text()
    assert page_text.index('href="#people"') < page_text.index('id="people"') < page_text.index('id="beds"')
    assert check_site(root)["findings"] == [] and check_page(root)["summary"]["failed"] == 0
    only = join(root, "--only", "beds")
    assert only.returncode == 0 and str(root) not in only.stdout.split(" to ")[1]
    # An unknown key, and a section without its id, stop the join and nothing is written.
    (root / "index.html").unlink()
    (root / "build" / "sections" / "02-beds.html").write_text('<section><p>{{fact:roses}}</p></section>')
    stopped = join(root)
    assert stopped.returncode != 0 and not (root / "index.html").exists()
    assert "{{fact:roses}} is not in data/facts.json" in stopped.stderr and 'id="beds"' in stopped.stderr


def test_a_link_whose_capitals_differ_is_wrong_case_not_a_working_folder(tmp_path):
    root = clean_site(tmp_path)
    (root / "contact.html").write_text(page("Contact", nav(current="contact.html")
                                           + '<a href="Events.html#june">June</a><img src="media/Rose.JPG" alt="">'))
    result = check_site(root)
    found = [(f["kind"], f["target"]) for f in result["findings"]]
    assert found == [("wrong_case", "Events.html#june"), ("wrong_case", "media/Rose.JPG")], result["findings"]
    assert "events.html" in result["findings"][0]["detail"] and "tells them apart" in result["findings"][0]["detail"]
    # The page it meant is still reached through it, and its anchor is still checked.
    (root / "index.html").write_text(page("Garden club", '<nav><a href="Contact.html">Contact</a></nav>'))
    (root / "events.html").write_text(page("Events", '<a href="index.html">Home</a>'))
    result = check_site(root)
    kinds = {(f["page"], f["kind"]) for f in result["findings"]}
    assert ("index.html", "wrong_case") in kinds and not any(k == "orphan" and p == "contact.html" for p, k in kinds)
    assert not any(k == "not_exported" for _p, k in kinds)


async def test_an_attached_app_gets_no_file_written_into_it(studio, project):
    workspace = studio.store.workspace(project["id"])
    clean_site(workspace)
    studio.store.update(project["id"], attach={"mode": "in_place", "path": str(workspace)})
    answer = await tools.call(studio, project["id"], "check_site", {})
    result = json.loads(answer["content"][0]["text"])
    assert result["findings"] == [] and not (workspace / "research" / "site-check.json").exists()
    saved = Path(result["saved"])
    assert saved == studio.store.dir(project["id"]) / "site-check.json" and json.loads(saved.read_text())["pages"] == 4
    studio.store.update(project["id"], attach=None)
    answer = await tools.call(studio, project["id"], "check_site", {})
    assert json.loads(answer["content"][0]["text"])["saved"] == "research/site-check.json"


def test_a_key_twice_in_the_ledger_stops_the_join(tmp_path):
    root = tmp_path
    ledger_project(root)
    facts = json.loads((root / "data" / "facts.json").read_text())
    facts["facts"].append({**facts["facts"][0], "value": 999, "display": "999"})
    (root / "data" / "facts.json").write_text(json.dumps(facts))
    (root / "build" / "sections").mkdir(parents=True)
    (root / "build" / "shell.html").write_text("<!doctype html><html><body><!-- join:sections --></body></html>")
    (root / "build" / "sections" / "01.html").write_text('<section id="people">{{fact:members_2025}}</section>')
    (root / "build" / "outline.json").write_text(json.dumps({"kind": "report", "sections": [
        {"id": "people", "module": "build/sections/01.html"}]}))
    stopped = join(root)
    assert stopped.returncode != 0 and not (root / "index.html").exists()
    assert "members_2025 is in data/facts.json 2 times (1,240 from s1, 999 from s1)" in stopped.stderr


def test_check_site_never_writes_through_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("ORIGINAL")
    root = clean_site(tmp_path / "file-link")
    (root / "research" / "site-check.json").symlink_to(victim)
    assert check_site(root)["saved"] is None and victim.read_text() == "ORIGINAL"
    root = clean_site(tmp_path / "folder-link")
    import shutil
    shutil.rmtree(root / "research")
    (root / "research").symlink_to(outside)
    assert check_site(root)["saved"] is None and not (outside / "site-check.json").exists()


def test_a_huge_page_and_a_flood_of_links_stay_bounded(tmp_path, monkeypatch):
    import resource
    import studio2_server.sitecheck as sc
    root = tmp_path
    (root / "index.html").write_text("<html><title>Big</title>" + "<a href=m>" * 600_000)     # 6 MB
    flood = "".join(f"<a href=m{i}>" for i in range(150_000))[:1_900_000]
    for n in range(3):
        (root / f"p{n}.html").write_text("<html><title>Flood</title>" + flood)
    (root / "loop").mkdir()
    (root / "loop" / "index.html").symlink_to(root / "loop")        # a folder whose index.html is itself
    (root / "p0.html").write_text((root / "p0.html").read_text() + '<a href="loop/">x</a>')
    started = time.perf_counter()
    result = check_site(root)
    took = time.perf_counter() - started
    assert took < 15, took
    assert [f["kind"] for f in result["findings"] if f["page"] == "index.html"] == ["too_large"]
    saved = json.loads((root / "research" / "site-check.json").read_text())
    assert len(saved["findings"]) == sc.KEPT_FINDINGS and sum(result["by_kind"].values()) > sc.KEPT_FINDINGS
    assert "links" in result["truncated"] and "findings" in result["truncated"]
    assert (root / "research" / "site-check.json").stat().st_size < sc.SAVED_BYTES
    # The repeated link on the big page would be one finding with a count, not 600,000.
    (root / "index.html").write_text("<html><title>Same</title>" + "<a href=gone.html>" * 1000)
    same = [f for f in check_site(root)["findings"] if f["page"] == "index.html" and f["kind"] == "broken_link"]
    assert len(same) == 1 and same[0]["times"] == 1000
    # The time budget ends a run and says so.
    monkeypatch.setattr(sc, "TIME_BUDGET", 0.0)
    assert "time" in check_site(root)["truncated"]


def test_the_joiner_reads_and_writes_only_inside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    ledger_project(root)
    secret = tmp_path / "secret.txt"
    secret.write_text("KEY")
    (root / "build" / "pages").mkdir(parents=True)
    (root / "build" / "page-shell.html").write_text("<!doctype html><html><body><!-- join:content --></body></html>")
    (root / "build" / "pages" / "home.html").write_text("<h1>Home</h1>")
    for module, path in ((str(secret), "index.html"), ("../secret.txt", "index.html"),
                         ("build/pages/home.html", str(tmp_path / "written.html")),
                         ("build/pages/home.html", "../written.html")):
        (root / "build" / "outline.json").write_text(json.dumps({"kind": "site", "shell": "build/page-shell.html",
            "pages": [{"id": "home", "path": path, "module": module}]}))
        stopped = join(root)
        assert stopped.returncode != 0 and "nothing was written" in stopped.stderr, (module, path)
        assert not (root / "index.html").exists() and not (tmp_path / "written.html").exists()
    (root / "build" / "linked.html").symlink_to(secret)
    (root / "build" / "outline.json").write_text(json.dumps({"kind": "site", "shell": "build/page-shell.html",
        "pages": [{"id": "home", "path": "index.html", "module": "build/linked.html"}]}))
    assert join(root).returncode != 0 and not (root / "index.html").exists()
    # A source whose address is not a web address is listed without a link.
    index = json.loads((root / "sources" / "index.json").read_text())
    index["sources"][0]["url"] = "javascript:alert(1)"
    (root / "sources" / "index.json").write_text(json.dumps(index))
    (root / "build" / "page-shell.html").write_text(
        "<!doctype html><html><body><!-- join:content --><!-- join:sources --></body></html>")
    (root / "build" / "pages" / "home.html").write_text("<p>{{fact:members_2025}}</p>")
    (root / "build" / "outline.json").write_text(json.dumps({"kind": "site", "shell": "build/page-shell.html",
        "pages": [{"id": "home", "path": "index.html", "module": "build/pages/home.html"}]}))
    assert join(root).returncode == 0
    assert "javascript:" not in (root / "index.html").read_text() and 'id="src-s1"' in (root / "index.html").read_text()
