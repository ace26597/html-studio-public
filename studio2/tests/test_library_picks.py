"""Step 3o: the index of everything the person has, picking per request, the agent's own
library tools, always/never and unused skills. No model is called: the picking call is a stub."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from studio2_server.catalog import Catalog, front_matter, with_description
from studio2_server.library import LIST_BUDGET_CHARS, Library, MemoryJson

from conftest import run_turn

TOPICS = ["contract law review", "clinical trials lookup", "stock screening momentum", "video cutting ffmpeg",
          "sql database querying", "github pull request summaries", "powerpoint decks", "three dimensional scenes",
          "market research reports", "csv dashboards charts"]


def skill(folder, name: str, description: str, extra: str = "") -> None:
    (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\n\nBody of {name}.\n")


def plugin(home, plugin_id: str, enabled: bool = True, servers: dict | None = None):
    name, _, market = plugin_id.partition("@")
    root = home / ".claude" / "plugins" / "cache" / market / name / "1.0.0"
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "hooks").mkdir(exist_ok=True)
    (root / "hooks" / "hooks.json").write_text('{"hooks": {"PreToolUse": [{"command": "never-run-me"}]}}')
    if servers is not None:
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    listed = home / ".claude" / "plugins" / "installed_plugins.json"
    known = json.loads(listed.read_text()) if listed.is_file() else {"version": 2, "plugins": {}}
    known["plugins"][plugin_id] = [{"scope": "user", "installPath": str(root), "version": "1.0.0"}]
    listed.write_text(json.dumps(known))
    settings_file = home / ".claude" / "settings.json"
    current = json.loads(settings_file.read_text()) if settings_file.is_file() else {"enabledPlugins": {}}
    current["enabledPlugins"][plugin_id] = enabled
    settings_file.write_text(json.dumps(current))
    return root


def big_library(settings, count: int = 2000) -> None:
    root = plugin(settings.home, "everything@market")
    for index in range(count):
        topic = TOPICS[index % len(TOPICS)]
        skill(root / "skills", f"{topic.split()[0]}-{index}", f"Helps with {topic}, variant {index}. Use for {topic} work.")
    personal = settings.home / ".claude" / "skills"
    skill(personal, "contract-redlines", "Review a contract clause by clause and mark risky terms for a lawyer.")
    skill(personal, "trial-finder", "Look up clinical trials by condition, drug and phase.")


def test_front_matter_reads_block_scalars_and_is_rewritten_in_place(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: x\ndescription: >\n  First line\n  second line.\nallowed-tools: Read\nmetadata:\n  a: 1\n---\nBody\n")
    assert front_matter(path)["description"] == "First line second line."
    path.write_text(with_description(path.read_text(), "x", 'Short "quoted" one.'))
    fields = front_matter(path)
    assert fields["description"] == 'Short \\"quoted\\" one.' or fields["description"] == 'Short "quoted" one.'
    text = path.read_text()
    assert "allowed-tools: Read" in text and "  a: 1" in text and text.endswith("Body\n") and "second line" not in text


def test_everything_is_found_and_only_skill_folders_and_connector_settings_are_taken(settings, monkeypatch):
    home = settings.home
    skill(home / ".claude" / "skills", "pdf-tools", "Work with PDFs.")
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: Reviews code.\n---\nBe strict.")
    on = plugin(home, "legal@market", servers={
        "contracts": {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${LEGAL_TOKEN}"}},
        "local": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/serve", "args": ["--root", "${CLAUDE_PLUGIN_ROOT}"]}})
    skill(on / "skills", "contract-review", "Review contracts.")
    skill(on / "nested" / "deeper", "odd-place", "A skill outside skills/.")
    (on / "agents").mkdir()
    (on / "agents" / "paralegal.md").write_text("---\nname: paralegal\ndescription: Finds clauses.\n---\n")
    off = plugin(home, "other@market", enabled=False)
    skill(off / "skills", "contract-review", "Review contracts.")          # the same skill from a second plugin
    skill(off / "skills", "poetry", "Write poems.")

    lib = Library(settings, MemoryJson())
    meta = lib.catalog.ensure()
    assert meta["counts"]["skill"] == {"studio": 20, "machine": 1, "plugin": 3} and meta["duplicates"] == 1
    assert meta["counts"]["agent"] == {"studio": 2, "machine": 1, "plugin": 1}
    by_id = lib.catalog.by_id
    assert by_id["plugin:legal@market:odd-place"]["enabled"] and not by_id["plugin:other@market:poetry"]["enabled"]
    assert by_id["plugin:other@market:contract-review"]["duplicate_of"] == "plugin:legal@market:contract-review"
    assert not any("hook" in json.dumps(entry) for entry in lib.catalog.entries)

    servers = {s["id"]: s for s in lib.servers()}
    remote = servers["plugin:legal@market:contracts"]
    assert remote["state"] == "needs-setting" and "LEGAL_TOKEN" in remote["fix"] and remote["source"] == "plugin"
    assert lib.server_config("plugin:legal@market:local") == {
        "command": f"{on}/bin/serve", "args": ["--root", str(on)], "type": "stdio"}
    monkeypatch.setenv("LEGAL_TOKEN", "tok-123")
    assert lib.server_config("plugin:legal@market:contracts")["headers"] == {"Authorization": "Bearer tok-123"}
    assert {s["id"]: s for s in lib.servers()}["plugin:legal@market:contracts"]["state"] is None

    # No record of what is installed: the cache itself is scanned.
    (home / ".claude" / "plugins" / "installed_plugins.json").unlink()
    assert lib.catalog.ensure(force=True)["counts"]["skill"]["plugin"] == 3
    # The index is rebuilt when a file changes, and comes back from the cache file otherwise.
    skill(home / ".claude" / "skills", "new-one", "Brand new.")
    lib.catalog._checked = 0
    assert lib.catalog.ensure()["counts"]["skill"]["machine"] == 2
    fresh = Catalog(settings)
    fresh.build = lambda: pytest.fail("the cached index was not used")
    assert fresh.ensure()["counts"]["skill"]["machine"] == 2


def test_two_thousand_skills_index_search_and_page_quickly(settings):
    big_library(settings)
    lib = Library(settings, MemoryJson())
    started = time.monotonic()
    meta = lib.catalog.ensure()
    assert meta["counts"]["skill"]["plugin"] == 2000 and time.monotonic() - started < 20
    started = time.monotonic()
    for _ in range(50):
        found = lib.catalog.search("review this contract clause by clause", "skill", 60)
    assert (time.monotonic() - started) / 50 < 0.05
    assert found[0][0]["name"] == "contract-redlines" and len(found) == 60
    assert all("contract" in entry["name"] for entry, _s, _h in found[1:10])


def test_the_skill_list_is_kept_inside_its_budget(settings):
    lib = Library(settings, MemoryJson())
    own = lib.catalog.skills(("studio",))
    extra = [{"id": f"x:{i}", "name": f"extra-skill-{i}", "description": "Does a long list of things. " * 20} for i in range(25)]
    fitted, chars, cap = Library.fit(own + extra)
    assert len(fitted) == 45 and chars <= LIST_BUDGET_CHARS and cap >= 60
    assert all(len(skill["listed"]) <= cap for skill in fitted)
    fitted, chars, _cap = Library.fit(own + extra * 8)      # far too many: the last ones are left out
    assert chars <= LIST_BUDGET_CHARS and len(fitted) < len(own) + 200 and fitted[0]["id"] == own[0]["id"]


async def test_a_request_gets_a_picked_set_with_reasons_and_pins_and_nevers_hold(client, studio, project, settings):
    pid = project["id"]
    big_library(settings)
    asked: list[str] = []

    async def choose(_connection, system, prompt):
        asked.append(prompt)
        lines = [line for line in prompt.splitlines() if line[:1] == "S" and line[1:2].isdigit()]
        assert 1 <= len(lines) <= 60 and "at most 8 connectors" in system
        return "S1: reviews contracts clause by clause\nS2: second best match\nS999: not a candidate\nnonsense"

    studio.picker.ask = choose
    events = await run_turn(client, studio, pid, "Review this contract clause by clause")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert [e["type"] for e in events][:2] == ["turn_started", "library_picked"]
    assert picked["mode"] == "model" and picked["library_skills"] == 2002 and picked["considered"] == 60
    assert [s["name"] for s in picked["skills"]][0] == "contract-redlines"
    assert picked["skills"][0]["reason"] == "Reviews contracts clause by clause." and len(picked["skills"]) == 2
    assert picked["list_chars"] <= picked["budget_chars"] and picked["loaded_skills"] == 22
    loaded = {p.name for p in (studio.store.dir(pid) / "library" / "skills").iterdir()}
    assert "contract-redlines" in loaded and "deep-research" in loaded and len(loaded) == 22
    assert not (studio.store.dir(pid) / "library" / "hooks").exists()
    second = picked["skills"][1]["id"]

    # "Never" for this project, "always" for the whole machine; both hold on the next request.
    assert (await client.put(f"/api/projects/{pid}/library", json={"skills": {"machine:contract-redlines": False}})).status_code == 200
    prefs = await client.put("/api/library/prefs", json={"skills": {"machine:trial-finder": "always", second: "never"}})
    assert prefs.json()["skills"] == {"machine:trial-finder": "always", second: "never"}
    assert (await client.put("/api/library/prefs", json={"skills": {"nope": "always"}})).status_code == 400
    events = await run_turn(client, studio, pid, "Review this contract clause by clause")
    picked = next(e for e in events if e["type"] == "library_picked")
    names = {s["name"]: s for s in picked["skills"]}
    assert "contract-redlines" not in names and second not in {s["id"] for s in picked["skills"]}
    assert names["trial-finder"]["how"] == "pinned" and "always on this computer" in names["trial-finder"]["reason"]
    assert "contract-redlines" not in asked[-1] and "trial-finder" not in asked[-1]
    view = (await client.get(f"/api/projects/{pid}/library")).json()
    states = {s["id"]: (s["enabled"], s["choice"], s["pref"]) for s in view["skills"]}
    assert states["machine:contract-redlines"] == (False, False, None) and states["machine:trial-finder"] == (True, None, "always")
    assert view["last_pick"]["mode"] == "model"
    # Back to "leave it to the studio".
    await client.put(f"/api/projects/{pid}/library", json={"skills": {"machine:contract-redlines": None}})
    assert "machine:contract-redlines" not in (await client.get(f"/api/projects/{pid}")).json()["library"]["skills"]

    # The agent is told how this works and what it was given.
    context = studio.agent_context(pid, picked)
    assert "2022 skills" in context and "search_library" in context and "trial-finder" in context

    # A slow or failing choosing call: the word search alone decides, and says so.
    async def slow(*_a):
        await asyncio.sleep(5)
    studio.picker.ask, studio.picker.timeout = slow, 0.05
    events = await run_turn(client, studio, pid, "Look up clinical trials for a drug")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert picked["mode"] == "words" and "word search decided" in picked["note"]
    assert any(s["name"].startswith("clinical-") and s["reason"].startswith("Matches your words") for s in picked["skills"])

    # … and a long request that only shares a word here and there with many skills gets none of
    # them (12 November 2026: a failed choosing call loaded eleven unrelated skills this way).
    events = await run_turn(client, studio, pid, (
        "Do as much research as you can regarding a new game. Its leaked things, things about the map, hidden "
        "gems or overall gameplay. It will be releasing soon, I think next week, and I want to be prepared. So "
        "grab as much data as possible and generate a report or dashboard to get me started before launch."))
    picked = next(e for e in events if e["type"] == "library_picked")
    assert picked["mode"] == "words" and [s for s in picked["skills"] if s["how"] == "picked"] == []
    assert "none was added" in picked["note"]

    # The library screen pages and searches the whole index.
    page = (await client.get("/api/library/items", params={"kind": "skills", "limit": 50, "offset": 100})).json()
    assert page["total"] == 2022 and len(page["items"]) == 50 and page["index"]["skills"] == 2022
    assert page["counts"] == {"studio": 20, "machine": 2, "plugin": 2000} and "dir" not in page["items"][0]
    mine = (await client.get("/api/library/items", params={"source": "machine"})).json()
    assert mine["total"] == 2 and mine["counts"] == page["counts"]     # counts are from before the source filter
    found = (await client.get("/api/library/items", params={"q": "contract", "state": "never"})).json()
    assert [item["id"] for item in found["items"]] == [second]
    assert (await client.get("/api/library")).json()["index"]["by_source"]["skills"]["plugin"] == 2000
    assert len((await client.get("/api/library")).json()["skills"]) == 20


async def test_a_small_library_is_loaded_whole_and_nothing_is_chosen(client, studio, project, settings):
    for index in range(5):
        skill(settings.home / ".claude" / "skills", f"mine-{index}", f"Personal skill {index}.")
    studio.picker.ask = lambda *_a: pytest.fail("nothing should be chosen")
    events = await run_turn(client, studio, project["id"], "Anything at all")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert picked["mode"] == "all" and len(picked["skills"]) == 5 and {s["how"] for s in picked["skills"]} == {"all"}


async def test_the_agent_finds_a_skill_and_asks_for_a_connector_itself(client, studio, project, settings):
    pid = project["id"]
    big_library(settings)
    (settings.home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "tracker": {"type": "http", "url": "https://example.com/mcp"}}}))
    events = await run_turn(client, studio, pid, "find skill review a contract clause by clause")
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [c["name"] for c in calls][-2:] == ["mcp__studio__search_library", "mcp__studio__activate_skill"]
    result = json.loads([e for e in events if e["type"] == "tool_result"][-1]["summary"])
    assert result["skill"] == "contract-redlines" and "Body of contract-redlines." in result["instructions"]
    assert (studio.store.dir(pid) / "library" / "skills" / "contract-redlines" / "SKILL.md").is_file()
    assert studio.store.get(pid)["library_used"] == ["machine:contract-redlines"]
    assert studio.picker.usage()["machine:contract-redlines"]["used"] == 1
    # It stays loaded for the rest of the project.
    events = await run_turn(client, studio, pid, "Now something unrelated about poems")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert {"name": "contract-redlines", "how": "earlier"}.items() <= picked["skills"][0].items()

    from studio2_server import tools
    answer = await tools.call(studio, pid, "request_connector", {"id": "machine:tracker", "reason": "Issues live there."})
    said = json.loads(answer["content"][0]["text"])
    assert said["connected"] is False and "next message" in said["next"]
    assert studio.store.get(pid)["library"]["requested"] == ["machine:tracker"]
    events = await run_turn(client, studio, pid, "go on")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert [(c["name"], c["how"]) for c in picked["connectors"]] == [("tracker", "requested")]
    assert "tracker" in studio.agent_context(pid, picked)
    # A switched-off skill or connector cannot be switched on by the agent.
    await client.put("/api/library/prefs", json={"mcp_servers": {"machine:tracker": "never"}, "skills": {"machine:trial-finder": "never"}})
    events = await run_turn(client, studio, pid, "need connector tracker")
    last = [e for e in events if e["type"] == "tool_result"][-1]
    assert last["status"] == "error" and "switched" in last["summary"]
    assert next(e for e in events if e["type"] == "library_picked")["connectors"] == []
    events = await run_turn(client, studio, pid, "go on")
    assert next(e for e in events if e["type"] == "library_picked")["connectors"] == []
    project_view = studio.store.get(pid)
    assert studio.picker.search(project_view, "clinical trials by condition", "skill")["skills"][0]["name"] != "trial-finder"


async def test_a_skill_picked_again_and_again_and_never_used_stops_being_picked(client, studio, project, settings):
    pid = project["id"]
    big_library(settings)

    async def choose(_connection, _system, prompt):
        return "S1: best match\nS2: second"

    studio.picker.ask, studio.picker.unused_after = choose, 3
    names = []
    for _ in range(4):
        events = await run_turn(client, studio, pid, "Review this contract clause by clause")
        names.append([s["id"] for s in next(e for e in events if e["type"] == "library_picked")["skills"]])
    first = names[0]
    assert names[1] == names[2] == first and not set(first) & set(names[3])
    usage = studio.picker.usage()
    assert usage[first[0]] == {"picked": 3, "last_picked": usage[first[0]]["last_picked"], "streak": 3}
    listed = (await client.get("/api/library/items", params={"state": "benched"})).json()
    assert {item["id"] for item in listed["items"]} == set(first) and listed["items"][0]["picked"] == 3
    # Using a skill (the agent program's Skill tool) resets the count for that skill.
    events = await run_turn(client, studio, pid, f"use skill studio:{names[3][0].split(':')[-1]}")
    assert studio.picker.usage()[names[3][0]]["used"] == 1 and studio.picker.usage()[names[3][0]]["streak"] == 0
    # The person saying "always" brings a benched skill back.
    await client.put("/api/library/prefs", json={"skills": {first[0]: "always"}})
    assert first[0] not in studio.picker.benched()


async def test_connectors_are_picked_and_an_apps_own_are_only_pinned(client, studio, project, settings):
    pid = project["id"]
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "github": {"type": "http", "url": "https://example.com/gh"}, "stripe": {"command": "stripe-mcp"}}}))
    events = await run_turn(client, studio, pid, "Summarise the open GitHub pull requests")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert [(c["name"], c["how"]) for c in picked["connectors"]] == [("github", "picked")]
    assert studio.turn_library(pid, {**picked})["ask_first"] == {"github"}
    await client.put(f"/api/projects/{pid}/library", json={"mcp_servers": {"machine:stripe": True, "machine:github": False}})
    events = await run_turn(client, studio, pid, "Summarise the open GitHub pull requests")
    picked = next(e for e in events if e["type"] == "library_picked")
    assert [(c["name"], c["how"]) for c in picked["connectors"]] == [("stripe", "pinned")]
    assert studio.turn_library(pid, {**picked})["ask_first"] == set()
    # What the agent program reported at start-up is remembered and shown with the fix.
    studio.record_session({"skills": [], "mcp_servers": [{"name": "stripe", "status": "needs-auth"}]})
    view = (await client.get("/api/library/items", params={"kind": "connectors", "state": "signin"})).json()
    assert [(c["name"], c["state"], c["fix"]) for c in view["items"]] == [("stripe", "needs-auth", "claude mcp login stripe")]


async def test_a_pick_never_leaves_the_requests_own_connection(studio, project, settings, monkeypatch):
    """The owner, 12 November 2026: one provider per request. A request on a gateway or a key
    must never have its text sent to the Claude plan (or anywhere else) to pick skills."""
    from studio2_server.picker import picking_model

    big_library(settings)
    plan = {"id": "subscription", "kind": "claude-subscription", "models": ["haiku"]}
    gateway = {"id": "gw", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:9", "models": ["big-model"]}
    key = {"id": "key", "kind": "anthropic-api", "models": ["sonnet"]}
    own = {"id": "ll", "kind": "litellm", "provider": "openai", "models": ["gpt-big", "gpt-big-mini"]}
    monkeypatch.setattr(studio.registry, "connections", lambda: [plan, gateway, key, own])
    asked: list[str] = []
    ever: list[str] = []            # every connection a choosing question was ever sent to
    broken: set[str] = set()

    async def choose(connection, _system, _prompt):
        asked.append(connection["id"])
        ever.append(connection["id"])
        if connection["id"] in broken:
            raise RuntimeError("model not found")
        return "S1: reviews contracts clause by clause"

    studio.picker.ask = choose
    text = "Review this contract clause by clause"

    async def pick(connection):
        return await studio.picker.pick(project, text, connection)

    # No cheap model on the gateway: nothing is asked anywhere, the word search decides.
    first = await pick(gateway)
    assert asked == [] and first["mode"] == "words" and first["route"] == "words"
    # A cheap model that fails, with a working plan beside it: still only the gateway is asked.
    cheap = {**gateway, "small_model": "tiny-model"}
    broken.add("gw")
    failed = await pick(cheap)
    assert asked == ["gw"] and failed["mode"] == "words"
    assert failed["note"].startswith("This connection's cheap model (tiny-model) did not answer, so the word search decided.")
    resting = await pick(cheap)                                   # the failure is remembered: not asked again
    assert asked == ["gw"] and "did not answer a few minutes ago" in resting["note"]
    # Every kind asks itself and nobody else, working or not.
    broken.update({"key", "ll"})
    for connection in (key, own, {**gateway, "id": "gw2", "small_model": "tiny-model"}, plan):
        del asked[:]
        studio.picker.resting.clear()
        assert [where["id"] for _route, where in studio.picker.routes(connection)] == [connection["id"]]
        if connection is not plan:
            assert "subscription" not in ever      # so far only non-plan requests were picked for
        picked = await pick(connection)
        assert asked == [connection["id"]], connection["id"]
        assert picked["route"] == ("connection" if connection["id"] not in broken else "words")
    assert ever.count("subscription") == 1          # the plan's own request, and nothing else
    # When it works, the note names the model that chose.
    broken.clear()
    studio.picker.resting.clear()
    worked = await pick(cheap)
    assert worked["mode"] == "model" and worked["route"] == "connection"
    assert worked["note"] == "Chosen by this connection's cheap model (tiny-model)."
    assert ("gw", "connection") not in studio.picker.resting
    # Which model picks, per kind: always one of the connection's own.
    assert picking_model(plan) == "haiku" and picking_model(key) == "haiku"
    assert picking_model(gateway) is None and picking_model(cheap) == "tiny-model"
    assert picking_model(own) == "gpt-big-mini"
    assert picking_model({**gateway, "model_ids": {"haiku": "my-haiku"}}) == "my-haiku"


async def test_a_connection_without_a_cheap_model_says_so_once_and_how_to_fix_it(studio, project, settings, monkeypatch):
    big_library(settings)
    gateway = {"id": "gw", "kind": "anthropic-gateway", "base_url": "http://127.0.0.1:9", "models": ["big-model"]}
    monkeypatch.setattr(studio.registry, "connections", lambda: [gateway])
    studio.picker.ask = lambda *_a: pytest.fail("no model may be asked")
    first = await studio.picker.pick(project, "Look up clinical trials for a drug", gateway)
    assert first["mode"] == "words" and first["route"] == "words"
    assert first["note"].startswith("This connection has no cheap model for picking; add `small_model` to it")
    again = await studio.picker.pick(project, "Look up clinical trials for a drug", gateway)
    assert again["note"].startswith("This connection has no cheap model for picking, so the word search decided.")
    assert "add `small_model`" not in again["note"]


async def test_a_check_of_the_page_gets_only_what_the_person_chose(client, studio, project, settings):
    # The first real run of checking: the studio picked the machine's Playwright connector by itself.
    pid = project["id"]
    for index in range(3):
        skill(settings.home / ".claude" / "skills", f"mine-{index}", f"Personal skill {index}.")
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / ".claude.json").write_text(json.dumps({"mcpServers": {
        "playwright": {"command": "playwright-mcp"}, "stripe": {"command": "stripe-mcp"},
        "tracker": {"type": "http", "url": "https://example.com/mcp"}}}))
    studio.store.update(pid, library={"skills": {"machine:mine-1": True}, "mcp_servers": {"machine:stripe": True},
                                      "requested": ["machine:tracker"]})
    studio.picker.ask = lambda *_a: pytest.fail("nothing should be chosen for a check")
    events = await run_turn(client, studio, pid, "Check this page with playwright: verify everything on it",
                            options={"depth": "standard", "verify": True, "check_only": True})
    started = next(e for e in events if e["type"] == "turn_started")
    assert started["options"] == {"depth": "standard", "verify": True, "check_only": True}
    picked = next(e for e in events if e["type"] == "library_picked")
    assert [(c["name"], c["how"]) for c in picked["connectors"]] == [("stripe", "pinned")]
    assert [(s["name"], s["how"]) for s in picked["skills"]] == [("mine-1", "pinned")]
    assert picked["mode"] == "none" and "picked nothing by itself" in picked["note"]
    # The same words without check_only still pick by themselves.
    studio.picker.ask = None
    events = await run_turn(client, studio, pid, "Check this page with playwright: verify everything on it",
                            options={"verify": True})
    picked = next(e for e in events if e["type"] == "library_picked")
    assert {c["how"] for c in picked["connectors"]} > {"pinned"}
    assert {s["how"] for s in picked["skills"]} > {"pinned"}
