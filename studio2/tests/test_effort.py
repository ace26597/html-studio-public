"""Depth (quick / standard / deep) and verification: options, the composed message, the picker
hook, the report_verification tool and the notice when a verification was asked for and not given."""

from conftest import run_turn

from studio2_server import effort, tools
from studio2_server.turns import compose_prompt


def test_clean_accepts_known_options_and_refuses_the_rest():
    assert effort.clean(None) == {"depth": "standard", "verify": False}
    assert effort.clean({"depth": "deep"}) == {"depth": "deep", "verify": False}
    assert effort.clean({"verify": True}) == {"depth": "standard", "verify": True}
    for bad in ({"depth": "huge"}, {"verify": "yes"}, {"length": 3}, "deep", {"depth": 2}):
        try:
            effort.clean(bad)
        except effort.OptionsError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_standard_adds_nothing_and_each_depth_has_its_block(tmp_path):
    plain = compose_prompt("Make a report", None, [], tmp_path, context="facts")
    assert compose_prompt("Make a report", None, [], tmp_path, context="facts",
                          options={"depth": "standard", "verify": False}) == plain
    assert compose_prompt("Make a report", None, [], tmp_path, context="facts", options=None) == plain
    quick = compose_prompt("Make a report", None, [], tmp_path, context="facts", options={"depth": "quick"})
    assert quick == plain + "\n\n<studio-effort>\n" + effort.QUICK + "\n</studio-effort>"
    deep = compose_prompt("Make a report", None, [], tmp_path, options={"depth": "deep", "verify": True})
    assert deep == ("Make a report\n\n<studio-effort>\n" + effort.DEEP + "\n\n" + effort.VERIFY
                    + "\n</studio-effort>")
    for text in (effort.QUICK, effort.DEEP, effort.VERIFY):   # intent, never a promised length
        assert "words" not in text and "pages long" not in text
    assert "deep-research" in effort.DEEP and "checker" in effort.VERIFY and "report_verification" in effort.VERIFY


async def test_unknown_options_are_a_400(client, project):
    for bad in ({"depth": "max"}, {"verify": 1}, {"other": True}):
        answer = await client.post(f"/api/projects/{project['id']}/turns", json={"text": "hi", "options": bad})
        assert answer.status_code == 400 and answer.json()["error"]["code"] == "invalid_options", answer.text
    answer = await client.patch(f"/api/projects/{project['id']}", json={"turn_options": {"depth": "max"}})
    assert answer.status_code == 400


async def test_the_project_remembers_the_choice(client, project):
    pid = project["id"]
    saved = await client.patch(f"/api/projects/{pid}", json={"turn_options": {"depth": "deep", "verify": True}})
    assert saved.status_code == 200 and saved.json()["turn_options"] == {"depth": "deep", "verify": True}
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"] == {"depth": "deep", "verify": True}
    await client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})   # other changes keep it
    assert (await client.get(f"/api/projects/{pid}")).json()["turn_options"]["depth"] == "deep"


async def test_options_reach_the_event_the_agent_and_the_picker(client, studio, project):
    pid = project["id"]
    events = await run_turn(client, studio, pid, "Research the market", options={"depth": "deep"})
    started = next(e for e in events if e["type"] == "turn_started")
    assert started["options"] == {"depth": "deep", "verify": False}
    picked = next(e for e in events if e["type"] == "library_picked")
    assert any(s["id"] == effort.DEEP_SKILL and s["how"] == "depth" for s in picked["skills"])
    plain = await run_turn(client, studio, pid, "Research the market")
    assert next(e for e in plain if e["type"] == "turn_started")["options"] == {"depth": "standard", "verify": False}
    picked = next(e for e in plain if e["type"] == "library_picked")
    assert not any(s["how"] == "depth" for s in picked["skills"])
    assert not [e for e in plain if e["type"] == "verification"]


async def test_deep_respects_a_skill_switched_off(client, studio, project):
    pid = project["id"]
    studio.store.update(pid, library={"skills": {effort.DEEP_SKILL: False}, "mcp_servers": {}})
    events = await run_turn(client, studio, pid, "Research the market", options={"depth": "deep"})
    picked = next(e for e in events if e["type"] == "library_picked")
    assert not any(s["id"] == effort.DEEP_SKILL for s in picked["skills"])


async def test_verify_writes_the_verification_event(client, studio, project):
    events = await run_turn(client, studio, project["id"], "Make a page", options={"verify": True})
    found = [e for e in events if e["type"] == "verification"]
    assert len(found) == 1
    card = found[0]
    assert (card["checked"], card["passed"], card["fixed"], card["failed"], card["unverifiable"]) == (3, 1, 1, 0, 1)
    assert [f["status"] for f in card["findings"]] == ["unverifiable", "fixed", "passed"]   # worst first
    assert not [e for e in events if e["type"] == "notice" and e.get("message") == effort.NOT_REPORTED]


async def test_verify_without_a_report_ends_with_a_notice(client, studio, project):
    events = await run_turn(client, studio, project["id"], "Make a page, skip the report", options={"verify": True})
    assert not [e for e in events if e["type"] == "verification"]
    notices = [e for e in events if e["type"] == "notice" and e.get("message") == effort.NOT_REPORTED]
    assert len(notices) == 1 and notices[0]["level"] == "warning"
    kinds = [e["type"] for e in events]
    assert kinds.index("notice") < kinds.index("turn_finished")


async def test_the_tool_caps_sizes_and_refuses_bad_input(studio, project):
    pid = project["id"]
    many = [{"what": "x" * 900, "where": "p", "status": "passed", "note": "n" * 2000} for _ in range(70)]
    many.append({"what": "bad total", "where": "table 2", "status": "failed", "note": ""})
    result = await tools.call(studio, pid, "report_verification",
                              {"checked": 71, "passed": 70, "fixed": 0, "failed": 1, "unverifiable": 0,
                               "summary": "s" * 1000, "findings": many})
    assert not result.get("is_error"), result
    event = [e for e in studio.log(pid).read() if e["type"] == "verification"][-1]
    assert len(event["findings"]) == effort.MAX_FINDINGS and event["findings_total"] == 71
    assert event["findings_cut"] == 11 and event["findings"][0]["status"] == "failed"
    assert len(event["findings"][1]["what"]) <= effort.WHAT_CAP and len(event["findings"][1]["note"]) <= effort.NOTE_CAP
    assert len(event["summary"]) <= effort.SUMMARY_CAP
    bad = await tools.call(studio, pid, "report_verification",
                           {"checked": -1, "passed": 0, "fixed": 0, "failed": 0, "unverifiable": 0, "findings": []})
    assert bad.get("is_error")
    bad = await tools.call(studio, pid, "report_verification",
                           {"checked": 1, "passed": 0, "fixed": 0, "failed": 0, "unverifiable": 0,
                            "findings": [{"what": "a", "status": "ok"}]})
    assert bad.get("is_error")


async def test_a_queued_message_keeps_its_options(client, studio, project):
    pid = project["id"]
    item = studio.turns.enqueue(studio.store.get(pid), "Later", None, None, None, [], {"depth": "quick"})
    queued = [e for e in studio.log(pid).read() if e["type"] == "turn_queued"][-1]
    assert queued["queued_id"] == item and queued["options"] == {"depth": "quick", "verify": False}
    studio.turns._start_next(pid)
    await studio.turns.wait(pid)
    started = [e for e in studio.log(pid).read() if e["type"] == "turn_started"][-1]
    assert started["queued_id"] == item and started["options"] == {"depth": "quick", "verify": False}


# -- check_only, the wording, the cut summary and the studio's own re-check -------------------
def test_check_only_is_a_request_option_that_implies_verify():
    assert effort.clean({"check_only": True}) == {"depth": "standard", "verify": True, "check_only": True}
    assert effort.clean({"verify": True, "check_only": False}) == {"depth": "standard", "verify": True}
    for bad in ({"check_only": "yes"}, {"check_only": 1}):
        try:
            effort.clean(bad)
        except effort.OptionsError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    try:   # the project's remembered choice has no check_only
        effort.clean({"check_only": True}, request=False)
    except effort.OptionsError:
        pass
    else:
        raise AssertionError("a project's turn_options took check_only")


async def test_a_project_cannot_remember_check_only(client, project):
    answer = await client.patch(f"/api/projects/{project['id']}", json={"turn_options": {"check_only": True}})
    assert answer.status_code == 400 and answer.json()["error"]["code"] == "invalid_options"


def test_the_wording_asks_for_a_recheck_and_no_counts_in_the_closing_message():
    text = effort.VERIFY
    assert "run check_page again" in text and "fixed only" in text
    assert "source list" in text
    assert "closing message do not restate the counts" in text and "check card" in text


def test_a_long_summary_is_cut_at_a_sentence_or_a_word():
    words = "The figure on revenue matched its source exactly. " + "Another clause keeps going on " * 30
    cut = effort._cut(words, 60)
    assert cut == "The figure on revenue matched its source exactly. …" and len(cut) <= 60
    plain = "alpha beta gamma delta " * 40
    cut = effort._cut(plain, 400)
    assert len(cut) <= 400 and cut.endswith("…") and cut[:-1].split()[-1] in {"alpha", "beta", "gamma", "delta"}
    assert effort._cut("short", 400) == "short"
    assert effort._cut("x" * 500, 10) == "x" * 8 + "…"     # one word fills the room: cut inside it
    event = effort.verification_event({"checked": 1, "passed": 1, "fixed": 0, "failed": 0, "unverifiable": 0,
                                       "summary": "Checked the totals and they agree " * 30, "findings": []})
    assert len(event["summary"]) <= effort.SUMMARY_CAP and event["summary"].endswith("…")
    assert event["summary"][:-1].split()[-1] in "Checked the totals and they agree".split()


PAGE = """<html><body><main>
<p>Revenue was <span data-src="s1" data-quote="revenue was 4.2 billion dollars">$4.2 billion</span>.</p>
<p>Margin was <span data-src="s1" data-quote="this sentence is not in the source">12%</span>.</p>
<p>Prices <span data-src="s2" data-derived="s2 average">rose</span>; see <span data-src="s27 s1"
 data-derived="s27 and s1 together">both</span>.</p>
<ol><li id="src-s1">Source one</li></ol>
</main></body></html>"""


def _sources(workspace):
    (workspace / "sources").mkdir(exist_ok=True)
    for sid, text in (("s1", "In 2025 revenue was 4.2 billion dollars."), ("s2", "Prices rose."), ("s27", "x y z")):
        (workspace / "sources" / f"{sid}.txt").write_text(text)
    import json
    (workspace / "sources" / "index.json").write_text(json.dumps({"sources": [
        {"id": sid, "title": sid, "url": f"https://example.com/{sid}", "fetched_at": "2026-11-13T00:00:00Z",
         "kind": "html", "chars": 10, "path": f"sources/{sid}.txt"} for sid in ("s1", "s2", "s27")]}))


def test_the_page_check_names_cited_sources_missing_from_the_source_list(tmp_path):
    from studio2_server.check import check_page
    _sources(tmp_path)
    (tmp_path / "index.html").write_text(PAGE)
    result = check_page(tmp_path, "index.html")
    assert result["source_list"] is True and result["not_listed"] == ["s2", "s27"]
    assert result["summary"]["cited_not_listed"] == 2 and result["summary"]["failed"] == 1
    # No source list on the page: nothing is asked of it.
    (tmp_path / "plain.html").write_text(PAGE.replace('<ol><li id="src-s1">Source one</li></ol>', ""))
    result = check_page(tmp_path, "plain.html", save=False)
    assert result["source_list"] is False and result["not_listed"] == [] and result["summary"]["cited_not_listed"] == 0
    assert not (tmp_path / "checks" / "plain.html.json").exists()     # save=False writes nothing
    # A project file in data-src is not a source.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_text("n\n3\n")
    (tmp_path / "file.html").write_text('<p><span data-src="data/a.csv" data-sql="select sum(n) from \'data/a.csv\'">3'
                                        '</span></p><ol><li id="src-s1">one</li></ol>')
    assert check_page(tmp_path, "file.html")["not_listed"] == []


async def test_the_studio_rechecks_the_page_after_a_verification(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    _sources(workspace)
    (workspace / "index.html").write_text(PAGE)
    saved = workspace / "checks" / "index.html.json"
    before = saved.read_text() if saved.exists() else None
    events = await run_turn(client, studio, pid, "Check this page", options={"check_only": True})
    kinds = [e["type"] for e in events]
    assert kinds.index("verification") < kinds.index("verification_recheck") < kinds.index("turn_finished")
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert recheck["agrees"] is False and recheck["problems_total"] == 3 and recheck["problems_cut"] == 0
    assert [p["what"] for p in recheck["problems"]] == ["12%", "Source s2", "Source s27"]
    page = recheck["pages"][0]
    assert (page["page"], page["checked"], page["failed"], page["cited_not_listed"], page["error"]) == (
        "index.html", 4, 1, 2, None)
    assert (saved.read_text() if saved.exists() else None) == before     # the agent's saved check is left alone
    # A page that holds up: the re-check agrees.
    (workspace / "index.html").write_text(
        '<main><p>Revenue was <span data-src="s1" data-quote="revenue was 4.2 billion dollars">$4.2 billion</span>.'
        '</p><ol><li id="src-s1">one</li></ol></main>')
    events = await run_turn(client, studio, pid, "Check this page", options={"check_only": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert recheck["agrees"] is True and recheck["problems"] == [] and recheck["pages"][0]["verified"] == 1
    # No verification reported: no re-check.
    events = await run_turn(client, studio, pid, "Make a page, skip the report", options={"verify": True})
    assert not [e for e in events if e["type"] == "verification_recheck"]
