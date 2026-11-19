"""A connector from a description (CONTRACT.md): what the model names is checked, nothing is added."""

from __future__ import annotations

import asyncio
import json

import pytest

from studio2_server import connector_suggest as cs

NPM_DOC = {"name": "@acme/notes-mcp", "description": "Read and search Acme notes.", "dist-tags": {"latest": "1.4.2"},
           "time": {"created": "2026-01-10T00:00:00.000Z", "1.4.2": "2026-11-01T08:00:00.000Z"},
           "versions": {"1.4.2": {"name": "@acme/notes-mcp", "version": "1.4.2"}},
           "repository": {"type": "git", "url": "git+https://github.com/acme/notes-mcp.git"}}
PYPI_DOC = {"info": {"name": "acme-db-mcp", "version": "0.9.0", "summary": "Query an Acme database.",
                     "project_urls": {"Homepage": "https://example.com/x", "Source": "https://github.com/acme/db-mcp"}},
            "urls": [{"upload_time_iso_8601": "2026-10-02T10:00:00.000000Z"}],
            "releases": {"0.1.0": [{"upload_time_iso_8601": "2026-03-01T10:00:00.000000Z"}],
                         "0.9.0": [{"upload_time_iso_8601": "2026-10-02T10:00:00.000000Z"}]}}


def web(routes: dict, calls: list | None = None):
    """A stand-in for the internet: {address: (status, body) | Exception}. Anything else is a 404."""
    async def get(method, url, **options):
        if calls is not None:
            calls.append((method, url, options))
        found = routes.get(url, (404, b"{}"))
        if isinstance(found, Exception):
            raise found
        status, body = found
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()
    return get


async def public(_host):
    return True


def model(studio, connectors, seen: list | None = None):
    async def ask(connection, system, prompt):
        if seen is not None:
            seen.append((connection, system, prompt))
        return connectors if isinstance(connectors, str) else "Here you go:\n" + json.dumps({"connectors": connectors})
    studio.picker.ask = ask


NOTES = {"name": "Acme Notes", "label": "Acme Notes", "description": "Read and search the notes in an Acme workspace.",
         "why": "You asked to read your notes.", "transport": "stdio", "command": "npx",
         "args": ["@acme/notes-mcp"], "secrets": [{"name": "ACME_TOKEN", "where": "Acme, Settings, API tokens."}]}
HOSTED = {"name": "acme-tickets", "label": "Acme Tickets", "description": "Search and file tickets.",
          "why": "Tickets.", "transport": "http", "url": "https://mcp.acme.example/mcp",
          "secrets": [{"name": "Authorization", "where": "Bearer <token> from Acme, Developer."}], "sign_in": True}
DATABASE = {"name": "acme-db", "label": "Acme DB", "description": "Query an Acme database.", "why": "Data.",
            "transport": "stdio", "command": "uvx", "args": ["acme-db-mcp==0.9.0", "--read-only"], "secrets": []}
ROUTES = {"https://registry.npmjs.org/@acme%2Fnotes-mcp": (200, NPM_DOC),
          "https://api.npmjs.org/downloads/point/last-week/@acme/notes-mcp": (200, {"downloads": 41200}),
          "https://pypi.org/pypi/acme-db-mcp/json": (200, PYPI_DOC),
          "https://mcp.acme.example/mcp": (401, b"")}


async def test_what_the_model_names_is_checked_and_shaped_for_the_form(studio):
    seen, calls = [], []
    model(studio, [NOTES, HOSTED, DATABASE], seen)
    out = await cs.suggest(studio, "  read my Acme notes and tickets  ", get=web(ROUTES, calls), public=public)
    assert out["asked"]["model"] and out["note"] is None and out["left_out"] == []
    notes, hosted, database = out["suggestions"]
    assert notes == {
        "name": "acme-notes", "label": "Acme Notes", "description": NOTES["description"], "why": NOTES["why"],
        "transport": "stdio", "url": None, "command": "npx", "args": ["-y", "@acme/notes-mcp"], "sign_in": False,
        "secrets": [{"name": "ACME_TOKEN", "where": "Acme, Settings, API tokens."}],
        "check": {"kind": "npm", "package": "@acme/notes-mcp", "found": True, "version": "1.4.2",
                  "description": "Read and search Acme notes.", "published": "2026-11-01T08:00:00.000Z",
                  "first_published": "2026-01-10T00:00:00.000Z", "downloads_week": 41200,
                  "repository": "https://github.com/acme/notes-mcp"}}
    assert hosted["transport"] == "http" and hosted["url"] == "https://mcp.acme.example/mcp" and hosted["sign_in"]
    assert hosted["check"] == {"kind": "address", "found": True, "status": 401}
    assert database["args"] == ["acme-db-mcp==0.9.0", "--read-only"] and database["sign_in"] is False
    assert database["check"] == {
        "kind": "pypi", "package": "acme-db-mcp", "found": True, "version": "0.9.0",
        "description": "Query an Acme database.", "published": "2026-10-02T10:00:00.000000Z",
        "first_published": "2026-03-01T10:00:00.000000Z", "downloads_week": None,
        "repository": "https://github.com/acme/db-mcp"}
    # The question: the person's words, what the library has, and no tools or keys.
    _connection, system, prompt = seen[0]
    assert "read my Acme notes and tickets" in prompt and "Already in the library:" in prompt and "DeepWiki" in prompt
    assert "sure exist" in system
    # The address was asked once, with no key and nothing but an initialize.
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][2]["json"]["method"] == "initialize"
    assert "authorization" not in {k.lower() for k in posts[0][2]["headers"]}
    # Nothing was added.
    assert not [s for s in studio.lib.servers() if s["source"] == "yours"]


async def test_what_does_not_exist_is_left_out_and_said(studio):
    ghost = {**NOTES, "label": "Ghost", "name": "ghost", "args": ["-y", "ghost-mcp-server"]}
    gone = {**HOSTED, "label": "Gone", "name": "gone", "url": "https://mcp.gone.example/mcp"}
    down = {**HOSTED, "label": "Down", "name": "down", "url": "https://mcp.down.example/mcp"}
    silent = {**HOSTED, "label": "Silent", "name": "silent", "url": "https://mcp.silent.example/mcp"}
    snake = {**DATABASE, "label": "Snake", "name": "snake", "args": ["no-such-snake"]}
    model(studio, [ghost, gone, down, silent, snake])
    routes = {"https://mcp.down.example/mcp": (503, b""), "https://mcp.silent.example/mcp": OSError("no route")}
    out = await cs.suggest(studio, "ghosts", get=web(routes), public=public)
    assert out["suggestions"] == []
    assert out["left_out"] == [{"label": "Ghost", "reason": "npm does not have that package"},
                               {"label": "Gone", "reason": "nothing answered at its address"},
                               {"label": "Down", "reason": "nothing answered at its address"}]      # at most three are checked
    assert out["note"] == "The model named nothing that could be used."


async def test_a_registry_that_cannot_be_reached_leaves_the_suggestion_unchecked(studio):
    model(studio, [NOTES, DATABASE])
    routes = {"https://registry.npmjs.org/@acme%2Fnotes-mcp": OSError("offline"),
              "https://pypi.org/pypi/acme-db-mcp/json": (502, b"")}
    out = await cs.suggest(studio, "notes", get=web(routes), public=public)
    assert [s["check"]["found"] for s in out["suggestions"]] == [None, None] and out["left_out"] == []


async def test_a_private_address_is_never_asked(studio):
    calls = []
    inside = [{**HOSTED, "label": f"Inside {n}", "name": f"inside-{n}", "url": url}
              for n, url in enumerate(["https://localhost/mcp", "https://127.0.0.1:8200/mcp", "https://10.0.0.5/mcp"])]
    model(studio, inside)
    out = await cs.suggest(studio, "inside", get=web({}, calls))       # the real resolver
    assert calls == [] and [s["check"] for s in out["suggestions"]] == [{"kind": "address", "found": None, "status": None}] * 3
    assert await cs.is_public_host("169.254.169.254") is False and await cs.is_public_host("no-such-host.invalid") is None
    # A name that does not exist is "nothing there", not "unchecked".
    assert await cs.check_address("https://no-such-host.invalid/mcp", web({}, calls)) == {
        "kind": "address", "found": False, "status": None} and calls == []


@pytest.mark.parametrize("change, reason", [
    ({"transport": "stdio", "command": "docker", "args": ["run", "acme/mcp"]}, "not a package started with npx or uvx"),
    ({"transport": "stdio", "command": "/usr/local/bin/acme-mcp", "args": []}, "not a package started with npx or uvx"),
    ({"command": "npx", "args": ["-p", "evil", "@acme/notes-mcp"]}, "package name could not be read"),
    ({"command": "npx", "args": ["-y", "../local-folder"]}, "package name could not be read"),
    ({"command": "npx", "args": ["-y", "@acme/notes-mcp; rm -rf ~"]}, "package name could not be read"),
    ({"command": "npx", "args": ["-y", "@acme/notes-mcp", "--api-key=YOUR_KEY"]}, "put a key in the command"),
    ({"command": "uvx", "args": ["--from", "git+https://example.com/x.git", "run"]}, "package name could not be read"),
    ({"command": "uvx", "args": ["--from", "acme-db-mcp"]}, "package name could not be read"),
    ({"transport": "http", "url": "http://mcp.acme.example/mcp"}, "not an https address"),
    ({"transport": "http", "url": "https://user:pw@mcp.acme.example/mcp"}, "not an https address"),
    ({"transport": "http", "url": "https://mcp.acme.example/mcp?api_key=abc"}, "put a key in the address"),
    ({"transport": "sse"}, cs.BAD_FORM), ({"description": ""}, cs.BAD_FORM), ({"label": "", "name": ""}, cs.BAD_FORM),
    ({"secrets": [{"name": "BAD NAME", "where": "x"}]}, cs.BAD_FORM), ({"secrets": "ACME_TOKEN"}, cs.BAD_FORM),
    ({"secrets": [{"name": f"K{n}", "where": ""} for n in range(7)]}, cs.BAD_FORM),
    ({"args": "-y @acme/notes-mcp"}, cs.BAD_FORM), ({"args": ["@acme/notes-mcp", "a\nb"]}, cs.BAD_FORM),
    ({"name": "studio", "label": "studio"}, cs.BAD_FORM),
])
def test_shapes_that_are_left_out(change, reason):
    said = cs.clean({**NOTES, **change})
    assert isinstance(said, str) and reason in said, said


def test_shapes_that_are_taken():
    assert cs.clean({**NOTES, "args": ["--yes", "@acme/notes-mcp@1.4.2", "/Users/you/Notes"]})["args"] == \
        ["-y", "@acme/notes-mcp@1.4.2", "/Users/you/Notes"]
    assert cs.clean({**DATABASE, "args": ["--from", "acme-db-mcp[extra]>=0.9", "acme-db"]})["_package"] == ("pypi", "acme-db-mcp")
    made = cs.clean({**NOTES, "name": "Acme  Notes (official)!", "sign_in": True,
                     "secrets": [{"name": " ACME_TOKEN ", "where": " a\n b "}, {"name": "ACME_TOKEN", "where": "again"}]})
    assert made["name"] == "acme-notes-official" and made["sign_in"] is False      # a package does not sign in
    assert made["secrets"] == [{"name": "ACME_TOKEN", "where": "a b"}]
    long = cs.clean({**NOTES, "secrets": [{"name": "K", "where": "word " * 100}]})["secrets"][0]["where"]
    assert len(long) <= 300 and long.endswith("word…")          # cut at a word, and said to be cut
    assert cs.clean("npx something") == cs.BAD_FORM
    assert cs.read_answer('```json\n{"connectors": []}\n```') == [] and cs.read_answer("I do not know.") is None
    assert cs.read_answer('{"connectors": "none"}') is None and cs.read_answer("{not json}") is None


async def test_what_the_library_already_has_is_shown_and_not_suggested_again(client, studio):
    added = await client.post("/api/mcp-servers", json={
        "name": "notes", "label": "Acme Notes", "transport": "stdio", "command": "npx",
        "args": ["-y", "@acme/notes-mcp@1.0.0"], "description": "Read the notes in an Acme workspace."})
    assert added.status_code == 201
    await client.post("/api/mcp-servers", json={"name": "acme-db", "transport": "http", "url": "https://db.example/mcp/"})
    again = {**DATABASE, "name": "acme-db", "label": "Acme DB"}
    same_address = {**HOSTED, "label": "Same", "name": "same", "url": "https://DB.example/mcp"}
    model(studio, [NOTES, NOTES, again, same_address])
    out = await cs.suggest(studio, "read my acme notes", get=web(ROUTES), public=public)
    have = {row["label"]: row for row in out["have"]}
    assert have["Acme Notes"]["source"] == "yours" and "acme-db" in have       # by its words, and by its address
    assert set(have["Acme Notes"]) == {"id", "name", "label", "source", "origin", "description", "state", "fix"}
    assert [s["name"] for s in out["suggestions"]] == ["acme-db-2"]            # the name was taken; the package is new
    assert out["left_out"] == []


async def test_the_shipped_list_is_searched_too(studio):
    model(studio, [])
    out = await cs.suggest(studio, "documentation for software libraries", get=web({}), public=public)
    assert "Context7" in [row["label"] for row in out["have"]]
    assert out["suggestions"] == [] and out["note"] == "The model knew of no connector for this."


async def test_words_that_only_say_what_to_do_match_nothing(studio):
    model(studio, [])
    out = await cs.suggest(studio, "Read and search the pages in my Notion workspace", get=web({}), public=public)
    assert out["have"] == []        # "search and read" is in half the shipped descriptions


async def test_when_no_model_answers_the_library_is_still_searched(studio, monkeypatch):
    out = await cs.suggest(studio, "documentation for software libraries")      # the scripted stand-in: nobody to ask
    assert out["asked"] is None and out["suggestions"] == [] and "No model was asked" in out["note"]
    assert "Context7" in [row["label"] for row in out["have"]]

    model(studio, "Sorry, I cannot help with that.")
    out = await cs.suggest(studio, "documentation for software libraries")
    assert out["asked"] and "could not be read" in out["note"] and out["have"]

    async def slow(connection, system, prompt):
        await asyncio.sleep(5)
    studio.picker.ask = slow
    monkeypatch.setattr(cs, "MODEL_SECONDS", 0.05)
    out = await cs.suggest(studio, "documentation for software libraries")
    assert out["asked"] is None and "did not answer" in out["note"] and out["have"]

    async def broken(connection, system, prompt):
        raise RuntimeError("the gateway would not start")
    studio.picker.ask = broken
    out = await cs.suggest(studio, "documentation for software libraries")
    assert out["asked"] is None and "could not be asked (the gateway would not start)" in out["note"]


async def test_the_route(client, studio, monkeypatch):
    model(studio, [NOTES])
    monkeypatch.setattr(cs, "fetch", web(ROUTES))
    answer = await client.post("/api/mcp-servers/suggest", json={"text": "read my acme notes"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"have", "suggestions", "left_out", "asked", "note"}
    assert body["suggestions"][0]["check"]["downloads_week"] == 41200
    for bad in ({}, {"text": "   "}, {"text": "x" * 2_001}, {"text": 5}):
        refused = await client.post("/api/mcp-servers/suggest", json=bad)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_request", bad
    unknown = await client.post("/api/mcp-servers/suggest", json={"text": "notes", "connection": "nobody"})
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "unknown_connection"
    # The suggestion goes into the ordinary form's route and is then a connector like any other.
    suggestion = body["suggestions"][0]
    added = await client.post("/api/mcp-servers", json={
        **{k: suggestion[k] for k in ("name", "label", "transport", "command", "args", "description")},
        "env": {"ACME_TOKEN": "typed-by-the-person"}})
    assert added.status_code == 201 and added.json()["secret_names"] == ["ACME_TOKEN"]


async def test_a_large_registry_answer_falls_back_to_the_latest_version():
    routes = {"https://registry.npmjs.org/big-mcp": (200, b""),          # fetch gives no body when it is too large
              "https://registry.npmjs.org/big-mcp/latest": (200, {"name": "big-mcp", "version": "9.0.0",
                                                                  "description": "Big."}),
              "https://api.npmjs.org/downloads/point/last-week/big-mcp": OSError("slow")}
    assert await cs.check_npm("big-mcp", web(routes)) == {
        "kind": "npm", "package": "big-mcp", "found": True, "version": "9.0.0", "description": "Big.",
        "published": None, "first_published": None, "downloads_week": None, "repository": None}
    # A registry that answers for another name is not a match.
    other = {"https://registry.npmjs.org/big-mcp": (200, {"name": "other", "dist-tags": {"latest": "1"}})}
    assert (await cs.check_npm("big-mcp", web(other)))["found"] is None
