"""Find words in a project's pages and across projects (CONTRACT.md, "Find words in a project's
pages and across projects"): matching, text from files, limits, the chat, access and paths."""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

from studio2_server import find
from studio2_server.find import fold, html_page, matches, md_page, parse_query, snippet
from test_access import remote_client
from test_guest_routes import shared  # noqa: F401 - the fixture is used by name


@pytest.fixture(autouse=True)
def _fresh_cache():
    find.clear_cache()
    yield
    find.clear_cache()


def q(text: str):
    query = parse_query(text)
    assert query is not None
    return query


# -- matching -------------------------------------------------------------------------------------
def test_fold_drops_accents_case_and_extra_space():
    assert fold("  Café\n\t ÉLAN  naïve ") == "cafe elan naive"
    assert fold("Straße") == "strasse"
    assert fold("") == ""


def test_query_words_phrases_and_too_short():
    query = q('garden "trip budget" Plan')
    assert set(query.terms) == {"garden", "plan", "trip budget"}
    assert query.whole == "garden trip budget plan"
    assert q("“trip budget”").terms == ("trip budget",)          # curly quotes are quotes
    assert q('note "trip bud').terms == ("trip bud", "note")       # an unclosed quote runs to the end
    for nothing in ("", " ", "a", '"a"', '" "', "é"):
        assert parse_query(nothing) is None, nothing


def test_every_word_must_be_in_the_block():
    query = q("gard plan")
    assert matches(query, fold("The Garden plan for spring"))
    assert matches(query, fold("plan of the GARDEN"))
    assert not matches(query, fold("The garden"))
    assert not matches(q('"garden plan"'), fold("plan of the garden"))
    assert matches(q("cafe"), fold("Le Café"))


def test_snippet_is_cut_around_the_match():
    text = ("word " * 80) + "the Garden plan is here " + ("tail " * 80)
    cut = snippet(text, q("garden"))
    assert "Garden plan" in cut and cut.startswith("…") and cut.endswith("…")
    assert len(cut) <= find.SNIPPET + 2
    assert snippet("Short text", q("short")) == "Short text"
    accented = ("x " * 100) + "un café ici" + (" y" * 100)
    assert "café" in snippet(accented, q("cafe"))


# -- text from files ------------------------------------------------------------------------------
PAGE = """<!doctype html><html><head><title>Trip budget</title><style>.garden{}</style>
<script>var garden = "plan";</script></head><body>
<h1>Trip budget</h1><p>Intro words about the <b>garden</b> plan.</p>
<h2>Food</h2><ul><li>Bread &amp; cheese for the garden</li></ul>
<table><tr><td>Garden tools</td><td>12</td></tr></table>
<noscript>garden hidden</noscript><svg><text>garden in svg</text></svg>
<template><p>garden template</p></template>
<h5>Small garden heading</h5></body></html>"""


def test_html_text_skips_scripts_and_tracks_sections():
    page = html_page(PAGE)
    assert page.title == "Trip budget"
    texts = [(block.kind, block.text, block.section) for block in page.blocks]
    assert ("heading", "Trip budget", None) in texts
    assert ("text", "Intro words about the garden plan.", "Trip budget") in texts
    assert ("text", "Bread & cheese for the garden", "Food") in texts
    assert ("text", "Garden tools", "Food") in texts
    joined = " ".join(block.text for block in page.blocks)
    for hidden in ("var garden", ".garden", "garden hidden", "garden in svg", "garden template"):
        assert hidden not in joined


def test_html_title_falls_back_to_the_first_heading():
    assert html_page("<h2>Garden plan</h2><p>x</p>").title == "Garden plan"
    assert html_page("<p>no heading</p>").title is None


def test_markdown_headings_paragraphs_items_and_cells():
    page = md_page("# Garden plan\n\nSome **bold** words\nand more.\n\n## Tools\n- a [rake](http://x.test) here\n"
                   "| name | count |\n|---|---|\n| spade | 2 |\n```\ncode line\n```\n")
    assert page.title == "Garden plan"
    texts = [(block.kind, block.text, block.section) for block in page.blocks]
    assert ("text", "Some bold words and more.", "Garden plan") in texts
    assert ("text", "a rake here", "Tools") in texts
    assert ("text", "spade", "Tools") in texts
    assert not any(set(text) <= set("-: ") for _, text, _ in texts)


def write(root, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")


def test_headings_then_exact_phrase_then_page_order(tmp_path):
    write(tmp_path, "index.html", "<h1>Home</h1><p>plan the garden</p><p>garden plan here</p><h2>Garden plan</h2>")
    pages, left, capped = find.find_in_project(tmp_path, q("garden plan"))
    assert (left, capped) == (0, False)
    hits = pages[0]["hits"]
    assert [hit["kind"] for hit in hits] == ["heading", "text", "text"]
    assert hits[1]["snippet"] == "garden plan here"          # the whole query as one phrase
    assert hits[2]["snippet"] == "plan the garden"
    assert hits[0]["section"] is None and hits[1]["section"] == "Home"


def test_a_title_that_matches_is_a_heading_hit(tmp_path):
    write(tmp_path, "a.html", "<title>Trip budget</title><h1>Welcome</h1><p>x</p>")
    pages, _, _ = find.find_in_project(tmp_path, q("trip"))
    assert pages[0]["title"] == "Trip budget"
    assert pages[0]["hits"] == [{"kind": "heading", "section": None, "snippet": "Trip budget"}]


def test_pages_with_a_heading_hit_come_first(tmp_path):
    write(tmp_path, "a.html", "<p>garden</p>")
    write(tmp_path, "b.md", "# Garden\n")
    pages, _, _ = find.find_in_project(tmp_path, q("garden"))
    assert [page["path"] for page in pages] == ["b.md", "a.html"]


# -- what is walked, and the limits ---------------------------------------------------------------
def test_ignored_folders_key_files_and_dot_names_are_not_read(tmp_path):
    for rel in ("node_modules/x/index.html", ".next/page.html", ".hidden/a.md", "__pycache__/a.md",
                ".env/notes.md", ".env.md", "notes/.draft.md", "data.txt", "style.css"):
        write(tmp_path, rel, "<p>garden</p>")
    write(tmp_path, "notes/plan.md", "garden")
    pages, _, _ = find.find_in_project(tmp_path, q("garden"))
    assert [page["path"] for page in pages] == ["notes/plan.md"]


def test_links_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    write(outside, "secret.html", "<p>garden secret</p>")
    workspace = tmp_path / "workspace"
    write(workspace, "index.html", "<p>garden here</p>")
    os.symlink(outside / "secret.html", workspace / "link.html")
    os.symlink(outside, workspace / "linked")
    pages, _, _ = find.find_in_project(workspace, q("garden"))
    assert [page["path"] for page in pages] == ["index.html"]


def test_a_large_file_is_not_read_and_capped_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(find, "FILE_LIMIT", 100)
    write(tmp_path, "big.html", "<p>garden</p>" + "x" * 200)
    write(tmp_path, "small.html", "<p>garden</p>")
    pages, _, capped = find.find_in_project(tmp_path, q("garden"))
    assert [page["path"] for page in pages] == ["small.html"] and capped


def test_file_count_hits_and_pages_are_capped(tmp_path, monkeypatch):
    for n in range(5):
        write(tmp_path, f"p{n}.html", "".join(f"<p>garden {i}</p>" for i in range(30)))
    pages, left, capped = find.find_in_project(tmp_path, q("garden"), max_pages=3)
    assert len(pages) == 3 and left == 2 and capped
    assert len(pages[0]["hits"]) == find.HITS_PER_PAGE and pages[0]["more"] == 30 - find.HITS_PER_PAGE
    monkeypatch.setattr(find, "MAX_FILES", 2)
    files, capped = find.page_files(tmp_path, find.MAX_FILES)
    assert len(files) == 2 and capped


def test_the_time_budget_stops_the_scan(tmp_path):
    write(tmp_path, "a.html", "<p>garden</p>")
    pages, _, capped = find.find_in_project(tmp_path, q("garden"), deadline=time.monotonic() - 1)
    assert pages == [] and capped


def test_a_changed_file_is_read_again(tmp_path):
    write(tmp_path, "a.html", "<p>garden</p>")
    assert find.find_in_project(tmp_path, q("garden"))[0]
    write(tmp_path, "a.html", "<p>trip budget, longer now</p>")
    assert find.find_in_project(tmp_path, q("garden"))[0] == []
    assert find.find_in_project(tmp_path, q("trip"))[0]


# -- the chat -------------------------------------------------------------------------------------
def test_chat_reads_the_person_and_the_agents_words_only(tmp_path):
    log = tmp_path / "events.jsonl"
    events = [
        {"seq": 1, "ts": "t1", "type": "turn_started", "turn_id": "a", "text": "Make a garden plan"},
        {"seq": 2, "ts": "t2", "type": "thought_chunk", "turn_id": "a", "message_id": "m0", "text": "garden thinking"},
        {"seq": 3, "ts": "t3", "type": "message_chunk", "turn_id": "a", "message_id": "m1", "text": "Here is the gar"},
        {"seq": 4, "ts": "t4", "type": "tool_call", "turn_id": "a", "tool_id": "x", "input": {"q": "garden"}},
        {"seq": 5, "ts": "t5", "type": "tool_result", "turn_id": "a", "tool_id": "x", "output": "garden output"},
        {"seq": 6, "ts": "t6", "type": "message_chunk", "turn_id": "a", "message_id": "m1", "text": "den page."},
    ]
    log.write_text("".join(json.dumps(event) + "\n" for event in events) + "not json\n", "utf-8")
    hits, capped, _found = find.find_in_chat(log, q("garden"))
    assert not capped
    assert hits == [{"seq": 1, "role": "person", "snippet": "Make a garden plan", "at": "t1"},
                    {"seq": 3, "role": "agent", "snippet": "Here is the garden page.", "at": "t3"}]
    assert find.find_in_chat(log, q("thinking"))[0] == []
    assert find.find_in_chat(log, q("output"))[0] == []
    assert find.find_in_chat(tmp_path / "none.jsonl", q("garden")) == ([], False, 0)


# -- the routes -----------------------------------------------------------------------------------
async def make(client, name: str, pages: dict[str, str]) -> str:
    response = await client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    for path, content in pages.items():
        put = await client.put(f"/api/projects/{project_id}/file", json={"path": path, "content": content})
        assert put.status_code == 200, put.text
    return project_id


async def test_project_route_shape_and_refusals(client):
    project_id = await make(client, "Garden plan", {"index.html": "<title>Garden</title><h1>Beds</h1><p>Tomato rows</p>",
                                                     "notes/ideas.md": "# Ideas\nMore tomato seeds\n"})
    answer = (await client.get(f"/api/projects/{project_id}/find", params={"q": "tomato"})).json()
    assert answer["query"] == "tomato" and answer["attached"] is False and answer["capped"] is False
    assert {page["path"] for page in answer["pages"]} == {"index.html", "notes/ideas.md"}
    page = next(page for page in answer["pages"] if page["path"] == "index.html")
    assert page == {"path": "index.html", "title": "Garden",
                    "hits": [{"kind": "text", "section": "Beds", "snippet": "Tomato rows"}], "more": 0}
    short = (await client.get(f"/api/projects/{project_id}/find", params={"q": "t"})).json()
    assert short == {"query": "t", "attached": False, "pages": [], "capped": False}
    for bad in ("p_00000000", "not-an-id", "..%2F..%2Fetc"):
        response = await client.get(f"/api/projects/{bad}/find", params={"q": "tomato"})
        assert response.status_code == 404, bad
        assert response.json()["error"]["code"] in ("project_not_found", "not_found")
    missing = await client.get("/api/projects/p_00000000/find", params={"q": "tomato"})
    assert missing.json()["error"]["code"] == "project_not_found"


async def test_an_attached_app_is_found_by_name_and_chat_only(client, studio):
    project_id = await make(client, "Trip budget app", {"index.html": "<p>budget page</p>"})
    studio.store.update(project_id, attach={"mode": "copy", "path": "/nowhere"})
    answer = (await client.get(f"/api/projects/{project_id}/find", params={"q": "budget"})).json()
    assert answer["attached"] is True and answer["pages"] == []
    everywhere = (await client.get("/api/find", params={"q": "budget"})).json()
    entry = next(item for item in everywhere["projects"] if item["id"] == project_id)
    assert entry["name_match"] and entry["attached"] and entry["pages"] == []


async def test_across_projects_names_first_with_chat(client, studio):
    from conftest import run_turn
    other = await make(client, "Reading list", {"index.html": "<h1>Books</h1><p>A garden of words</p>"})
    named = await make(client, "Garden plan", {"index.html": "<p>nothing here</p>"})
    await run_turn(client, studio, other, "Add a garden section please")
    answer = (await client.get("/api/find", params={"q": "garden"})).json()
    ids = [item["id"] for item in answer["projects"]]
    assert ids[0] == named and other in ids
    first = answer["projects"][0]
    assert first["name_match"] is True and first["name"] == "Garden plan"
    entry = next(item for item in answer["projects"] if item["id"] == other)
    assert entry["name_match"] is False
    # The fake agent writes the request into the page too, so look for the line written above.
    assert entry["pages"] and all(len(page["hits"]) <= 3 for page in entry["pages"])
    one = (await client.get(f"/api/projects/{other}/find", params={"q": "garden"})).json()
    assert "A garden of words" in [hit["snippet"] for page in one["pages"] for hit in page["hits"]]
    assert entry["chat"][0]["role"] == "person" and "garden" in entry["chat"][0]["snippet"]
    assert isinstance(entry["chat"][0]["seq"], int) and entry["chat"][0]["at"]
    assert set(entry["more"]) == {"pages", "chat"}
    nothing = (await client.get("/api/find", params={"q": "zzqqxx"})).json()
    assert nothing == {"query": "zzqqxx", "projects": [], "capped": False}
    assert (await client.get("/api/find")).json()["projects"] == []


async def test_across_projects_only_what_the_list_shows(client, studio):
    seen = await make(client, "Garden plan", {})
    scratch = await make(client, "Garden check", {"index.html": "<p>garden</p>"})
    studio.store.update(scratch, hidden=True)
    listed = {item["id"] for item in (await client.get("/api/projects")).json()}
    found = {item["id"] for item in (await client.get("/api/find", params={"q": "garden"})).json()["projects"]}
    assert seen in found and scratch not in found and found <= listed


async def test_across_projects_stops_on_time(app, client, monkeypatch):
    await make(client, "Garden plan", {"index.html": "<p>garden</p>"})
    real = find.everywhere_answer
    monkeypatch.setattr(find, "everywhere_answer", lambda *args, **kw: real(*args, **{**kw, "budget": -1}))
    answer = (await client.get("/api/find", params={"q": "garden"})).json()
    assert answer["capped"] is True and answer["projects"] == []


async def test_shared_studio_guest_and_owner_see_what_the_list_shows(shared, studio):  # noqa: F811
    owner, guest = shared["owner"], shared["guest"]
    mine = await make(owner, "Garden plan", {"index.html": "<p>garden beds</p>", ".env": "KEY=garden"})
    theirs = await make(guest, "Trip budget", {"index.html": "<p>garden stop on the trip</p>"})
    scratch = await make(owner, "Garden scratch", {"index.html": "<p>garden</p>"})
    studio.store.update(scratch, hidden=True)
    for client in (owner, guest):
        listed = {item["id"] for item in (await client.get("/api/projects")).json()}
        answer = (await client.get("/api/find", params={"q": "garden"})).json()
        found = {item["id"] for item in answer["projects"]}
        assert found == {mine, theirs} and found <= listed and scratch not in found
        text = json.dumps(answer)
        assert "KEY=" not in text
        one = await client.get(f"/api/projects/{mine}/find", params={"q": "garden"})
        assert one.status_code == 200 and [page["path"] for page in one.json()["pages"]] == ["index.html"]
        assert (await client.get("/api/projects/p_00000000/find", params={"q": "garden"})).status_code == 404


async def test_another_device_without_the_key_gets_nothing(app, client):
    await make(client, "Garden plan", {"index.html": "<p>garden</p>"})
    async with remote_client(app) as far:
        assert (await far.get("/api/find", params={"q": "garden"})).status_code == 403
        projects = (await client.get("/api/projects")).json()
        assert (await far.get(f"/api/projects/{projects[0]['id']}/find", params={"q": "garden"})).status_code == 403


async def test_a_link_inside_a_project_never_reads_outside(client, studio, tmp_path):
    project_id = await make(client, "Garden plan", {"index.html": "<p>inside</p>"})
    outside = tmp_path / "outside.html"
    outside.write_text("<p>secret garden</p>", "utf-8")
    workspace = studio.store.workspace(project_id)
    os.symlink(outside, workspace / "link.html")
    os.symlink(tmp_path, workspace / "up")
    answer = (await client.get(f"/api/projects/{project_id}/find", params={"q": "secret"})).json()
    assert answer["pages"] == []
    everywhere = (await client.get("/api/find", params={"q": "secret"})).json()
    assert everywhere["projects"] == []



def test_the_cache_is_let_go_when_nobody_searches(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<h1>Garden plan</h1><p>Tomatoes by the fence.</p>")
    find.clear_cache()
    find.touch_cache()
    assert find.load_page(page) is not None
    assert find._cache                                  # held for the next query
    assert find.drop_if_idle(idle=3600) is False        # a search began just now
    assert find._cache
    assert find.drop_if_idle(idle=0) is True
    assert not find._cache and find._cache_chars == 0


def test_a_note_cannot_make_the_parser_backtrack():
    """Lines written to stall a pattern (a run of spaces in a heading, brackets that never
    close) are read in a time that grows with their length, not with its square or cube."""
    import time as clock

    hostile = "\n".join([
        "# a" + " " * 200_000 + "b",
        "[" * 200_000,
        "![" + "a" * 200_000,
        "- " + " " * 200_000 + "x",
        "```" + " " * 200_000,
        ("[a](" + "b" * 5000) * 40,
    ])
    began = clock.monotonic()
    page = find.md_page(hostile)
    assert clock.monotonic() - began < 2.0
    assert page.blocks                                   # and it is still read as text


def test_a_heading_loses_only_closing_hashes():
    page = find.md_page("## Costs ##\n\n# C#\n\n### Plain\n")
    assert [block.text for block in page.blocks] == ["Costs", "C#", "Plain"]


def test_the_walk_stops_at_a_folder_of_very_many_files(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Garden plan</h1>")
    data = tmp_path / "zdata"
    data.mkdir()
    for index in range(60):
        (data / f"row{index}.csv").write_text("1")
    files, capped = find.page_files(tmp_path, max_entries=50)
    assert capped is True
    assert [rel for rel, _ in files] == ["index.html"]   # what was reached before the limit is kept
    files, capped = find.page_files(tmp_path)
    assert capped is False
    assert find.page_files(tmp_path, deadline=0.0) == ([], True)


def test_only_the_listed_chat_lines_get_a_snippet(tmp_path, monkeypatch):
    log = tmp_path / "events.jsonl"
    lines = [json.dumps({"type": "turn_started", "seq": n, "text": f"garden note {n} " + "x" * 400, "ts": "t"}) for n in range(50)]
    log.write_text("\n".join(lines) + "\n")
    cut = []
    real = find.snippet
    monkeypatch.setattr(find, "snippet", lambda text, query, size=find.SNIPPET: cut.append(1) or real(text, query, size))
    find.clear_cache()
    hits, capped, found = find.find_in_chat(log, q("garden"), newest=3)
    assert (len(hits), found, len(cut)) == (3, 50, 3)
    assert [hit["seq"] for hit in hits] == [49, 48, 47]  # newest first


def test_accents_follow_the_same_rule_as_the_page():
    # Category Mn is dropped (the page's \p{Mn}); a spacing mark is a letter of its word and stays.
    assert find.fold("café") == find.fold("cafe")
    assert find.fold("का") != find.fold("क")         # U+093E is Mc, not Mn
