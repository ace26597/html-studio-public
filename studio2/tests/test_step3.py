"""Step 3: sources, the page check, studio tools, and the per-project library. No model, no network."""

from __future__ import annotations

import json

import pytest

from studio2_server import tools
from studio2_server.check import check_page, numbers_in, parse_page
from studio2_server.search import parse_results
from studio2_server.sources import SourceError, Sources, extract, html_to_text

ARTICLE = """<html><head><title>Acme annual report</title><style>p{}</style></head><body>
<nav>Home</nav><script>var x = 99999;</script>
<h1>Results</h1><p>Total net revenues of $1,330.4 million in fiscal 2024, down from $2,290.8 million.</p>
<p>Gross margin was 47.3% and the company employed 2,781 people at year end.</p>
<table><tr><th>Region</th><th>Share</th></tr><tr><td>United States</td><td>71%</td></tr></table>
</body></html>"""


def source(workspace):
    title, text = html_to_text(ARTICLE)
    item, created = Sources(workspace).save(text=text, title=title, kind="html", url="https://example.com/ar")
    assert created and item["id"] == "s1" and title == "Acme annual report"
    return item


def test_html_becomes_readable_text_and_is_saved_once(tmp_path):
    item = source(tmp_path)
    text = (tmp_path / item["path"]).read_text()
    assert "99999" not in text and "$1,330.4 million" in text and "United States | 71%" in text
    again, created = Sources(tmp_path).save(text=text, title="x", kind="html", url="https://example.com/ar")
    assert not created and again["id"] == "s1"
    other, created = Sources(tmp_path).save(text=text + " More words here.", title="x", kind="html", url="https://example.com/ar")
    assert created and other["id"] == "s2"
    found = Sources(tmp_path).read("s1", find="gross margin")
    assert found["matches"] == 1 and "47.3%" in found["passages"][0]["text"]
    with pytest.raises(SourceError):
        Sources(tmp_path).save(text="   ", title="", kind="html", url=None)
    with pytest.raises(SourceError):
        Sources(tmp_path).get("s9")
    assert extract(b"plain words in a text file, enough of them", "text/plain", "a.txt")[0] == "text"
    with pytest.raises(SourceError):
        extract(b"\x00\x01\x02binary", "application/octet-stream", "a.bin")


def page(body: str) -> str:
    return f"<html><body><h2>Revenue</h2>{body}</body></html>"


def test_the_check_verifies_quotes_numbers_data_and_derived(tmp_path):
    source(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sales.csv").write_text("region,amount\nus,100\neu,50.5\n")
    (tmp_path / "index.html").write_text(page("""
      <p>Revenue was <span data-src="s1" data-quote="Total net revenues of $1,330.4 million">$1.33 billion</span>
         in 2024, with <span data-src="s1" data-quote="Gross margin was 47.3%">47% margin</span>.</p>
      <p>Wrong: <span data-src="s1" data-quote="Gross margin was 47.3%">52%</span> and
         <span data-src="s1" data-quote="revenues rose to a record nine billion">$9bn</span> and
         <span data-src="s7" data-quote="something long enough here">1</span>.</p>
      <p>Data: <span data-src="data/sales.csv" data-sql="select sum(amount) from 'data/sales.csv'">150.5</span>,
         <span data-src="data/sales.csv" data-sql="select count(*) from 'data/sales.csv'">3</span>,
         <span data-src="data/sales.csv" data-sql="delete from x">1</span>.</p>
      <p>Derived: <span data-src="s1" data-derived="1330.4 / 2290.8 - 1">-41.9%</span>
         <span data-src="s1">no method</span></p>
      <p>The market grew 38% to 4,200 units in 2023. See step 3 of 5.</p>
      <p data-nocheck>Example only: 12345.</p><code>999 888</code>
      <section id="checked-numbers"><p>7777 verified</p></section>"""))
    result = check_page(tmp_path)
    by_text = {item["text"]: item for item in result["items"]}
    assert by_text["$1.33 billion"]["status"] == "verified" and "rounded" in by_text["$1.33 billion"]["reason"]
    assert by_text["47% margin"]["status"] == "verified"
    assert by_text["52%"]["status"] == "failed" and "47.3" in by_text["52%"]["reason"]
    assert by_text["$9bn"]["status"] == "failed" and "not in the saved text" in by_text["$9bn"]["reason"]
    assert by_text["150.5"]["status"] == "verified" and by_text["150.5"]["kind"] == "data"
    assert by_text["3"]["status"] == "failed" and by_text["3"]["found"] == "2"
    assert by_text["-41.9%"]["status"] == "derived" and by_text["no method"]["status"] == "failed"
    assert result["summary"]["verified"] == 3 and result["summary"]["derived"] == 1
    assert result["summary"]["failed"] == 6 and by_text["$9bn"]["where"] == "Revenue"
    unmarked = [n for block in result["unmarked"] for n in block["numbers"]]
    assert unmarked == ["38%", "4,200"]
    assert json.loads((tmp_path / "checks" / "index.html.json").read_text())["summary"] == result["summary"]
    assert result["sources"][0]["cited"] >= 3


def test_number_reading_and_page_parsing():
    assert [str(v) for _r, v, _d in numbers_in("$1,330.4 million and 47.3% but not Q3 or [2]")] == ["1330.4", "47.3"]
    marks, blocks = parse_page('<p>a <b data-src="s1" data-quote="q">1 <i>2</i></b> c</p><svg><text>5</text></svg>')
    assert marks[0]["text"] == "1 2" and "5" not in " ".join(b["text"] for b in blocks)


def test_search_results_are_parsed():
    html_page = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">'
                 'First <b>hit</b></a><a class="result__snippet" href="x">A &amp; B snippet</a>'
                 '<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>')
    assert parse_results(html_page, 5) == [{"title": "First hit", "url": "https://example.com/a", "snippet": "A & B snippet"}]


def test_the_second_search_service_is_parsed():
    from studio2_server.search import parse_yahoo

    page = ('<div class="compTitle options-toggle"><a class="d-ib" href="https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/'
            'RE=1/RO=10/RU=https%3a%2f%2fexample.com%2fa%3fx%3d1/RK=2/RS=c-"><div class="d-ib p-abs">example.com</div>'
            '<h3 class="title"><span>First &amp; <b>best</b></span></h3></a></div><div class="compText aAbs"><p>'
            'A <b>snippet</b> here.</p></div>'
            '<div class="compTitle"><a href="https://search.yahoo.com/search?p=more"><h3>Related</h3></a></div>')
    assert parse_yahoo(page, 5) == [{"title": "First & best", "url": "https://example.com/a?x=1",
                                     "snippet": "A snippet here."}]


@pytest.fixture
def quiet_search(monkeypatch):
    """The search module with no waits, nothing remembered, and stand-in services (no network)."""
    from studio2_server import search

    for name in ("SPACING", "RETRY_WAIT", "BUSY_WAIT"):
        monkeypatch.setattr(search, name, 0.0)
    for store in (search._cache, search._resting, search._last):
        store.clear()
    monkeypatch.setattr(search, "_lock", None)
    asked, plan = [], {}

    async def ask(service, query, limit):
        asked.append((service, query))
        outcome = plan.get(service, "ok")
        outcome = outcome.pop(0) if isinstance(outcome, list) and outcome else outcome
        if outcome != "ok":
            raise search.Refused(str(outcome), busy=str(outcome).startswith("HTTP 5"))
        return [{"title": f"{service} hit", "url": f"https://{service}.example/{len(asked)}", "snippet": query}]
    monkeypatch.setattr(search, "_ask", ask)
    yield search, asked, plan
    for store in (search._cache, search._resting, search._last):
        store.clear()


async def test_search_retries_once_then_asks_the_second_service_and_leaves_the_first_alone(quiet_search):
    search, asked, plan = quiet_search
    plan["duckduckgo"] = ["HTTP 403", "ok"]                      # refused, then the one retry works
    first = await search.search("tides", scope="p:t1")
    assert first["via"] == "duckduckgo" and not first["cached"] and [s for s, _ in asked] == ["duckduckgo"] * 2
    plan["duckduckgo"] = "HTTP 403"                              # refuses for good
    second = await search.search("kites", scope="p:t1")
    assert second["via"] == "yahoo" and [s for s, _ in asked[2:]] == ["duckduckgo", "duckduckgo", "yahoo"]
    third = await search.search("boats", scope="p:t1")          # the refusing service is resting: not asked
    assert third["via"] == "yahoo" and asked[-1] == ("yahoo", "boats") and len(asked) == 6
    # A server error is asked again straight away (three more times at most) and is no reason to rest.
    plan["yahoo"] = ["HTTP 500", "HTTP 500", "ok"]
    busy = await search.search("ships", scope="p:t1")
    assert busy["via"] == "yahoo" and [s for s, _ in asked[6:]] == ["yahoo"] * 3 and "yahoo" not in search._resting
    plan["yahoo"] = ["HTTP 500"] * 4 + ["ok"]
    with pytest.raises(search.SearchError, match="Yahoo: HTTP 500"):
        await search.search("rafts", scope="p:t1")
    assert len(asked) == 14 and asked[-1][0] == "duckduckgo" and "yahoo" not in search._resting   # the resting one, once
    del asked[6:]
    # The same query in the same turn is answered once; a new turn asks again.
    again = await search.search("  Boats ", scope="p:t1")
    assert again["cached"] and again["results"] == third["results"] and len(asked) == 6
    assert not (await search.search("boats", scope="p:t2"))["cached"] and len(asked) == 7


async def test_searches_wait_in_line_and_two_helpers_with_one_query_ask_once(quiet_search, monkeypatch):
    import asyncio

    search, asked, _plan = quiet_search
    running = peak = 0
    inner = search._ask

    async def slow(service, query, limit):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return await inner(service, query, limit)
    monkeypatch.setattr(search, "_ask", slow)
    answers = await asyncio.gather(*(search.search(q, scope="p:t") for q in ["a", "b", "a", "c", "b", "a"]))
    assert peak == 1 and sorted(q for _s, q in asked) == ["a", "b", "c"]
    assert [a["cached"] for a in answers] == [False, False, True, False, True, True]


async def test_when_every_service_refuses_the_answer_says_search_is_unavailable_and_what_to_do(quiet_search, studio, project):
    search, asked, plan = quiet_search
    plan.update(duckduckgo="HTTP 403", yahoo="HTTP 429")
    result = await tools.call(studio, project["id"], "search_web", {"query": "anything"})
    text = result["content"][0]["text"]
    assert result.get("is_error") and "unavailable right now" in text
    assert "DuckDuckGo: HTTP 403" in text and "Yahoo: HTTP 429" in text
    assert "WebSearch" in text and "fetch_source" in text and "Do not make up addresses" in text
    count = len(asked)                                           # both resting: each is still asked once, no retry
    assert (await tools.call(studio, project["id"], "search_web", {"query": "another"})).get("is_error")
    assert [s for s, _ in asked[count:]] == ["duckduckgo", "yahoo"]
    plan.clear()
    ok = json.loads((await tools.call(studio, project["id"], "search_web", {"query": "third"}))["content"][0]["text"])
    assert ok["via"] == "duckduckgo" and ok["results"] and "duckduckgo" not in search._resting


async def test_an_address_already_saved_is_not_downloaded_or_repeated_and_a_source_is_read_in_parts(
        monkeypatch, studio, project):
    pid, downloads = project["id"], []
    body = "<title>Tides</title><p>" + " ".join(f"Sentence {n} about the tide at station {n}." for n in range(400)) + "</p>"

    async def download(url):
        downloads.append(url)
        return body.encode(), "text/html", "https://example.com/tides"          # a redirect: asked ≠ final
    monkeypatch.setattr(tools, "download", download)

    async def call(**args):
        name = args.pop("tool", "fetch_source")
        result = await tools.call(studio, pid, name, args)
        assert not result.get("is_error"), result
        return json.loads(result["content"][0]["text"])

    first = await call(url="http://example.com/tides-old")
    assert first["id"] == "s1" and not first["already_saved"] and "Sentence 0" in first["text_start"]
    for address in ("http://example.com/tides-old", "https://example.com/tides", "https://EXAMPLE.com/tides/#top"):
        again = await call(url=address)
        assert again["id"] == "s1" and again["already_saved"] and again["title"] == "Tides"
        assert "text_start" not in again and "read_source" in again["note"]
    assert downloads == ["http://example.com/tides-old"]                         # no second download
    fresh = await call(url="https://example.com/tides", refresh=True)            # asked for: downloaded again
    assert len(downloads) == 2 and fresh["id"] == "s1" and "Sentence 0" in fresh["text_start"]

    # One figure out of a long source: the passages around a match, or an exact range.
    found = await call(tool="read_source", id="s1", find="station 250.")
    assert found["matches"] == 1 and "Sentence 250" in found["passages"][0]["text"]
    assert sum(len(p["text"]) for p in found["passages"]) < 1200 < found["chars"]
    at = found["passages"][0]["at"]
    part = await call(tool="read_source", id="s1", offset=at, end=at + 12)
    assert part["text"] == "station 250." and part["next_offset"] == at + 12
    by_name = {name: text for name, text, *_rest in tools.TOOLS}
    assert "refresh" in by_name["fetch_source"] and "range" in by_name["read_source"]
    assert all(len(by_name[name]) < 520 for name in ("search_web", "fetch_source", "read_source"))


@pytest.fixture
def slow_index(monkeypatch):
    """Writing the index takes a moment, so saves that run together really overlap."""
    import time

    from studio2_server import sources as sources_module
    real = sources_module.write_json_atomic

    def slow(path, data):
        time.sleep(0.05)
        real(path, data)
    monkeypatch.setattr(sources_module, "write_json_atomic", slow)


async def test_parallel_saves_each_get_their_own_id_paired_with_their_own_text(tmp_path, slow_index):
    import asyncio
    store = Sources(tmp_path)
    pages = [(f"https://example.com/page{n}", f"Page {n} says the figure is {n * 111} units, plainly.")
             for n in range(6)]
    results = await asyncio.gather(*(asyncio.to_thread(store.save, text=text, title=url, kind="text", url=url)
                                     for url, text in pages))
    assert sorted(item["id"] for item, _ in results) == [f"s{n}" for n in range(1, 7)]
    listed = {item["id"]: item for item in store.list()}
    assert len(listed) == 6
    for (item, created), (url, text) in zip(results, pages):
        assert created and listed[item["id"]]["url"] == url and store.text(item["id"]) == text
    assert not list((tmp_path / "sources").glob("*.tmp"))       # no temporary index left behind


async def test_two_fetches_of_one_address_at_once_end_with_one_source(monkeypatch, studio, project, slow_index):
    import asyncio
    calls = []

    async def download(url):  # a page that changes on every load (a clock, an ad), so the texts differ
        calls.append(url)
        await asyncio.sleep(0.02)
        return (f"<title>Live</title><p>The live page, loaded as number {len(calls)}, with enough words.</p>".encode(),
                "text/html", url)
    monkeypatch.setattr(tools, "download", download)
    results = await asyncio.gather(*(tools.call(studio, project["id"], "fetch_source", {"url": "https://example.com/live"})
                                     for _ in range(2)))
    views = [json.loads(result["content"][0]["text"]) for result in results]
    assert len(calls) == 2                                       # both had started before either was saved
    assert {view["id"] for view in views} == {"s1"} and sorted(v["already_saved"] for v in views) == [False, True]
    assert [item["id"] for item in Sources(studio.store.workspace(project["id"])).list()] == ["s1"]


async def test_studio_tools_work_inside_a_project(client, studio, project):
    pid, workspace = project["id"], studio.store.workspace(project["id"])
    (workspace / "data").mkdir(exist_ok=True)
    (workspace / "data" / "t.csv").write_text("a,b\n1,x\n2,y\n3,z\n")
    (workspace / "notes.md").write_text("The plant produced 480 megawatt-hours in March according to the operator.")

    async def call(name, **args):
        result = await tools.call(studio, pid, name, args)
        return result.get("is_error", False), result["content"][0]["text"]

    error, text = await call("query_files", sql="select sum(a) as total from 'data/t.csv'")
    assert not error and json.loads(text)["rows"] == [[6]]
    error, text = await call("query_files", sql="select * from 'data/t.csv'", save_as="data/out.json", limit=2)
    assert not error and json.loads(text)["truncated"] is True and len(json.loads((workspace / "data/out.json").read_text())) == 2
    error, text = await call("query_files", sql="copy (select 1) to 'x.csv'")
    assert error and "Only reading" in text
    error, text = await call("add_source", path="notes.md")
    assert not error and json.loads(text)["id"] == "s1"
    error, text = await call("read_source", id="s1", find="megawatt")
    assert not error and json.loads(text)["matches"] == 1
    (workspace / "index.html").write_text(
        '<p><span data-src="s1" data-quote="produced 480 megawatt-hours in March">480 MWh</span></p>')
    error, text = await call("check_page")
    assert not error and json.loads(text)["summary"]["verified"] == 1 and json.loads(text)["failed"] == []
    assert (await call("add_source", path="../outside.txt"))[0] is True
    assert (await call("fetch_source", url="file:///etc/passwd"))[0] is True
    assert (await call("query_database", source="nope", sql="select 1"))[0] is True
    assert {t["name"] for t in tools.describe(studio)} == set(tools.HANDLERS)


async def test_skills_and_servers_are_listed_added_and_chosen_per_project(client, studio, project, settings):
    pid = project["id"]
    machine = settings.home / ".claude" / "skills" / "pdf-tools"
    machine.mkdir(parents=True)
    (machine / "SKILL.md").write_text("---\nname: pdf-tools\ndescription: Work with PDFs.\n---\nBody")
    (settings.home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "notes": {"command": "notes-mcp", "args": ["--x"], "env": {"NOTES_KEY": "secret-value"}}}}))

    library = (await client.get("/api/library")).json()
    assert {a["name"] for a in library["agents"]} == {"researcher", "checker"}
    assert "check_page" in {t["name"] for t in library["studio_tools"]}
    by_id = {s["id"]: s for s in library["mcp_servers"]}
    assert by_id["machine:notes"]["secret_names"] == ["NOTES_KEY"] and "secret-value" not in json.dumps(library)
    assert by_id["suggested:deepwiki"]["transport"] == "http"

    bad = await client.post("/api/skills", json={"name": "Bad Name", "description": "d", "body": "b"})
    assert bad.status_code == 400
    made = await client.post("/api/skills", json={"name": "house-style", "description": "Our house style.", "body": "Use blue."})
    assert made.status_code == 201 and made.json()["source"] == "yours" and made.json()["default"] is True
    assert (await client.post("/api/skills", json={"name": "house-style", "description": "d", "body": "b"})).status_code == 409

    server = await client.post("/api/mcp-servers", json={
        "name": "docs", "transport": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer abc123"}})
    assert server.status_code == 201 and server.json()["secret_names"] == ["Authorization"]
    sid = server.json()["id"]
    assert "abc123" not in server.text and "abc123" not in (settings.data_dir / "mcp-servers.json").read_text()
    assert (await client.post("/api/mcp-servers", json={"name": "studio", "transport": "http", "url": "https://x.y"})).status_code == 400
    assert (await client.post("/api/mcp-servers", json={"name": "docs", "transport": "http", "url": "https://x.y"})).status_code == 409
    assert (await client.patch("/api/mcp-servers/machine:notes", json={"label": "x"})).status_code == 404
    # Headers are given whole (an empty value no longer keeps a stored one: test_saved_keys.py).
    kept = await client.patch(f"/api/mcp-servers/{sid}", json={"label": "Docs", "headers": {"Authorization": "Bearer abc123", "X-Team": "t1"}})
    assert kept.json()["label"] == "Docs" and kept.json()["secret_names"] == ["Authorization", "X-Team"]
    assert studio.lib.server_config(sid)["headers"] == {"Authorization": "Bearer abc123", "X-Team": "t1"}

    view = (await client.get(f"/api/projects/{pid}/library")).json()
    enabled = {s["id"]: s["enabled"] for s in view["skills"]}
    # A skill found on this machine is listed in a project only once the person decided about it (step 3o).
    assert enabled["studio:deep-research"] and enabled["yours:house-style"] and "machine:pdf-tools" not in enabled
    assert not any(s["enabled"] for s in view["mcp_servers"])
    assert (await client.put(f"/api/projects/{pid}/library", json={"skills": {"nope:x": True}})).status_code == 400
    chosen = await client.put(f"/api/projects/{pid}/library", json={
        "skills": {"machine:pdf-tools": True, "studio:charts": False, "studio:theme": True},
        "mcp_servers": {sid: True, "machine:notes": True}})
    assert chosen.status_code == 200
    assert (await client.get(f"/api/projects/{pid}")).json()["library"] == {
        "skills": {"machine:pdf-tools": True, "studio:charts": False}, "mcp_servers": {sid: True, "machine:notes": True}}

    extras = studio.turn_library(pid)
    names = {p.name for p in (extras["plugin_dir"] / "skills").iterdir()}
    assert {"pdf-tools", "house-style", "deep-research"} <= names and "charts" not in names
    assert (extras["plugin_dir"] / "agents" / "researcher.md").is_file()
    assert extras["mcp_servers"]["docs"]["headers"]["X-Team"] == "t1"
    assert extras["mcp_servers"]["notes"] == {"command": "notes-mcp", "args": ["--x"], "env": {"NOTES_KEY": "secret-value"}, "type": "stdio"}
    assert "docs" in studio.agent_context(pid) and "abc123" not in studio.agent_context(pid)

    too_many = {f"suggested:{s['name']}": True for s in json.loads((settings.library_dir / "mcp-suggestions.json").read_text())}
    for index in range(4):
        made = await client.post("/api/mcp-servers", json={"name": f"extra{index}", "transport": "stdio", "command": "x"})
        too_many[made.json()["id"]] = True
    assert (await client.put(f"/api/projects/{pid}/library", json={"mcp_servers": too_many})).status_code == 400

    assert (await client.delete(f"/api/mcp-servers/{sid}")).status_code == 204
    assert sid not in (await client.get(f"/api/projects/{pid}")).json()["library"]["mcp_servers"]
    assert studio.lib.secrets.get(sid) == {}
    assert (await client.delete("/api/skills/house-style")).status_code == 204
    assert (await client.delete("/api/skills/house-style")).status_code == 404
    failed = await client.post("/api/mcp-servers/machine:notes/test")
    assert failed.status_code == 200 and failed.json()["ok"] is False


def test_every_skill_the_agent_is_told_about_exists():
    """The project facts name skills in backticks; a name with no folder sends the agent looking for nothing."""
    import inspect
    import re
    from pathlib import Path

    from studio2_server.studio import Studio

    source = " ".join(re.findall(r'"([^"]*)"', inspect.getsource(Studio.agent_context)))
    named = set()
    for run in re.finditer(r"((?:`[a-z0-9-]+`(?:, | and | )?)+)\s*skills?\b", source):
        named.update(re.findall(r"`([a-z0-9-]+)`", run.group(1)))
    assert {"three-d-scene", "motion-video", "office-files"} <= named
    skills = Path(__file__).resolve().parents[1] / "library" / "skills"
    missing = sorted(name for name in named if not (skills / name / "SKILL.md").is_file())
    assert not missing, f"agent_context names skills that do not exist: {missing}"
