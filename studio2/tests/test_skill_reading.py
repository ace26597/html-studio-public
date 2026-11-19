"""Reading any skill, a copy of one's own, asking the agent about one (CONTRACT.md, "Reading any
skill, explaining it, and starting from one"). Zero model calls.
"""
import asyncio

import pytest

from conftest import run_turn  # noqa: F401  (keeps the scripted agent's fixtures in reach)


async def machine_skill(client, settings, name="from-machine", scripts=True):
    """A skill found on this machine, with a page, a picture, a script, a file of another kind,
    and the things an export leaves out."""
    home = settings.home / ".claude" / "skills" / name
    (home / "reference").mkdir(parents=True)
    (home / "scripts").mkdir()
    (home / "node_modules" / "x").mkdir(parents=True)
    (home / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Found here, for weekly reviews.\nallowed-tools: Read, Bash\n"
        "license: MIT\n---\n\n# Weekly review\n\nFirst read `reference/example.html`.\n")
    (home / "reference" / "example.html").write_text("<h1>Example</h1><script>alert(1)</script>")
    (home / "reference" / "look.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(64)))
    (home / "reference" / "deck.pdf").write_bytes(b"%PDF-1.7\n\x00\x01\x02binary")
    (home / "LICENSE").write_text("Do as you like.\n")
    if scripts:
        (home / "scripts" / "total.py").write_text("print(sum(range(10)))\n")
        tool = home / "scripts" / "run"
        tool.write_text("#!/bin/sh\necho hi\n")
        tool.chmod(0o755)
    (home / "node_modules" / "x" / "a.js").write_text("x")
    (home / ".env").write_text("KEY=secret-value")
    secret = settings.home / "secret.txt"
    secret.write_text("outside the skill")
    (home / "link.txt").symlink_to(secret)
    await client.post("/api/library/refresh")
    return f"machine:{name}"


async def test_any_skill_is_read_whole_and_nothing_private_is_listed(client, settings):
    skill = await machine_skill(client, settings)
    found = (await client.get("/api/library/skills/detail", params={"id": skill})).json()
    assert found["id"] == skill and found["source"] == "machine" and found["yours"] is False
    assert found["description"] == "Found here, for weekly reviews."
    assert found["body"].startswith("# Weekly review") and "---" not in found["body"] and found["body_cut"] is False
    assert found["front"] == {"allowed-tools": "Read, Bash", "license": "MIT"}          # not the name or the description
    assert [(f["path"], f["kind"], f["script"]) for f in found["files"]] == [
        ("SKILL.md", "text", False),                                                    # the instructions first
        ("LICENSE", "text", False),                                                     # text by what it holds, not its name
        ("reference/deck.pdf", "other", False), ("reference/example.html", "text", False),
        ("reference/look.png", "picture", False),
        ("scripts/run", "text", True),                                                  # marked executable
        ("scripts/total.py", "text", True)]
    assert found["scripts"] == ["scripts/run", "scripts/total.py"]
    assert found["size"] == sum(f["size"] for f in found["files"]) > 0
    assert (found["picked"], found["used"], found["last_used"]) == (0, 0, None)
    assert "dir" not in found and str(settings.home) not in str(found)                  # where it lies is not said
    shipped = (await client.get("/api/library/skills/detail", params={"id": "studio:make-a-skill"})).json()
    assert shipped["yours"] is False and "Explaining a skill" in shipped["body"] and "Changing a skill" in shipped["body"]
    missing = await client.get("/api/library/skills/detail", params={"id": "machine:nothing"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_skill"


async def test_a_file_is_shown_as_text_a_picture_as_itself_and_nothing_else_leaves(client, settings):
    skill = await machine_skill(client, settings)

    async def get(path, **more):
        return await client.get("/api/library/skills/file", params={"id": skill, "path": path, **more})

    page = (await get("reference/example.html")).json()
    assert page["kind"] == "text" and page["text"] == "<h1>Example</h1><script>alert(1)</script>" and page["cut"] is False
    script = (await get("scripts/total.py")).json()
    assert script["script"] is True and script["text"].startswith("print(")
    picture = (await get("reference/look.png")).json()
    assert picture["kind"] == "picture" and "text" not in picture
    raw = await get("reference/look.png", raw="1")
    assert raw.status_code == 200 and raw.headers["content-type"] == "image/png" and raw.content.startswith(b"\x89PNG")
    assert raw.headers["cache-control"] == "no-store" and raw.headers["x-content-type-options"] == "nosniff"
    # A page or a script is never handed over as something a browser would run.
    for path in ("reference/example.html", "scripts/total.py", "SKILL.md"):
        refused = await get(path, raw="1")
        assert refused.status_code == 415 and refused.json()["error"]["code"] == "not_shown", path
    other = await get("reference/deck.pdf")
    assert other.status_code == 415 and "export the skill" in other.json()["error"]["message"]
    # Only a file the reader lists: not what an export leaves out, not a link, not a way out.
    for path in (".env", "node_modules/x/a.js", "link.txt", "../secret.txt", "/etc/hosts", "", "reference",
                 "reference/../../secret.txt"):
        refused = await get(path)
        assert refused.status_code == 404 and refused.json()["error"]["code"] == "unknown_file", path
        assert "secret" not in refused.text and "outside the skill" not in refused.text


async def test_long_instructions_and_long_files_are_cut_and_say_so(client, settings, monkeypatch):
    from studio2_server import ownskills
    monkeypatch.setattr(ownskills, "SHOWN_CHARS", 50)
    skill = await machine_skill(client, settings)
    home = settings.home / ".claude" / "skills" / "from-machine"
    (home / "SKILL.md").write_text("---\nname: from-machine\ndescription: Long.\n---\n\n" + "word " * 100)
    (home / "notes.md").write_text("line\n" * 100)
    found = (await client.get("/api/library/skills/detail", params={"id": skill})).json()
    assert len(found["body"]) == 50 and found["body_cut"] is True
    notes = (await client.get("/api/library/skills/file", params={"id": skill, "path": "notes.md"})).json()
    assert len(notes["text"]) == 50 and notes["cut"] is True


async def test_how_often_a_skill_was_picked_and_used_is_shown(client, studio, settings):
    skill = await machine_skill(client, settings)
    studio.picker.usage_path.parent.mkdir(parents=True, exist_ok=True)
    studio.picker.usage_path.write_text(
        '{"%s": {"picked": 7, "used": 3, "streak": 1, "last_used": "2026-11-18T10:00:00.000Z"}}' % skill)
    found = (await client.get("/api/library/skills/detail", params={"id": skill})).json()
    assert (found["picked"], found["used"], found["last_used"]) == (7, 3, "2026-11-18T10:00:00.000Z")


async def test_a_copy_becomes_the_persons_own_with_every_file_and_the_first_stays(client, settings):
    skill = await machine_skill(client, settings)
    asked = await client.post("/api/skills/copy", json={"from": skill, "name": "weekly-mine"})
    assert asked.status_code == 409 and asked.json()["error"]["code"] == "scripts_need_confirmation"
    assert asked.json()["error"]["details"]["scripts"] == ["scripts/run", "scripts/total.py"]
    assert not (settings.data_dir / "skills" / "weekly-mine").exists()
    made = await client.post("/api/skills/copy", json={"from": skill, "name": "weekly-mine", "allow_scripts": True})
    assert made.status_code == 201 and made.json()["id"] == "yours:weekly-mine" and made.json()["source"] == "yours"
    mine = (await client.get("/api/library/skills/detail", params={"id": "yours:weekly-mine"})).json()
    assert mine["yours"] is True and mine["name"] == "weekly-mine" and mine["body"].startswith("# Weekly review")
    assert [f["path"] for f in mine["files"]] == ["SKILL.md", "LICENSE", "reference/deck.pdf", "reference/example.html",
                                                  "reference/look.png", "scripts/run", "scripts/total.py"]
    assert mine["scripts"] == ["scripts/run", "scripts/total.py"]                       # still runnable, still marked
    assert mine["front"]["allowed-tools"] == "Read, Bash"
    assert "name: weekly-mine" in (settings.data_dir / "skills" / "weekly-mine" / "SKILL.md").read_text()
    first = (await client.get("/api/library/skills/detail", params={"id": skill})).json()
    assert first["name"] == "from-machine" and first["yours"] is False                  # the one it came from is as it was
    # It can now be changed like any skill of the person's own.
    edited = await client.put("/api/skills/weekly-mine", json={"description": "My weekly review."})
    assert edited.status_code == 200 and edited.json()["description"] == "My weekly review."


@pytest.mark.parametrize("body, status, code", [
    ({"from": "machine:from-machine", "name": "Bad Name"}, 400, "invalid_request"),
    ({"from": "machine:from-machine"}, 400, "invalid_request"),
    ({"from": "machine:nothing", "name": "mine"}, 404, "unknown_skill"),
    ({"from": "machine:from-machine", "name": "make-a-skill", "allow_scripts": True}, 409, "name_taken_by_studio"),
])
async def test_a_copy_that_is_refused(client, settings, body, status, code):
    await machine_skill(client, settings)
    answer = await client.post("/api/skills/copy", json=body)
    assert answer.status_code == status and answer.json()["error"]["code"] == code
    assert not list((settings.data_dir / "skills").glob("[!.]*")) if (settings.data_dir / "skills").exists() else True


async def test_a_name_already_taken_needs_replace(client, settings):
    skill = await machine_skill(client, settings, scripts=False)
    assert (await client.post("/api/skills/copy", json={"from": skill, "name": "mine"})).status_code == 201
    again = await client.post("/api/skills/copy", json={"from": skill, "name": "mine"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "name_taken"
    assert (await client.post("/api/skills/copy", json={"from": skill, "name": "mine", "replace": True})).status_code == 201


async def test_asking_for_an_explanation_starts_one_request_in_a_new_project(client, studio, settings):
    skill = await machine_skill(client, settings)
    asked = await client.post("/api/library/skills/ask", json={"id": skill, "about": "explain",
                                                               "text": "  does it   touch the network? "})
    assert asked.status_code == 202
    pid, turn = asked.json()["project_id"], asked.json()["turn_id"]
    await studio.turns.wait(pid)
    project = studio.store.get(pid)
    assert project["name"] == "About the skill: from-machine"
    started = next(e for e in studio.log(pid).read() if e["type"] == "turn_started")
    folder = (settings.home / ".claude" / "skills" / "from-machine").resolve()
    assert started["turn_id"] == turn and started["text"] == (
        "Explain the skill “from-machine” from my library (use the make-a-skill skill, \"Explaining a skill\"). "
        f"Its folder is {folder}. Read SKILL.md and every other file in it before you write."
        "\n\nWhat I want to know: does it touch the network?")
    plain = await client.post("/api/library/skills/ask", json={"id": "studio:make-a-skill", "about": "explain"})
    assert plain.status_code == 202
    await studio.turns.wait(plain.json()["project_id"])
    text = next(e for e in studio.log(plain.json()["project_id"]).read() if e["type"] == "turn_started")["text"]
    assert text.endswith("before you write.") and "What I want to know" not in text


async def test_asking_for_a_change_is_for_ones_own_and_says_what(client, studio, settings):
    skill = await machine_skill(client, settings, scripts=False)
    theirs = await client.post("/api/library/skills/ask", json={"id": skill, "about": "change", "text": "Shorter."})
    assert theirs.status_code == 409 and theirs.json()["error"]["code"] == "not_yours"
    assert "copy" in theirs.json()["error"]["message"]
    assert (await client.post("/api/skills/copy", json={"from": skill, "name": "mine"})).status_code == 201
    silent = await client.post("/api/library/skills/ask", json={"id": "yours:mine", "about": "change", "text": "  "})
    assert silent.status_code == 400
    asked = await client.post("/api/library/skills/ask", json={"id": "yours:mine", "about": "change",
                                                               "text": "Add a step that checks the totals.\nKeep the rest."})
    assert asked.status_code == 202
    pid = asked.json()["project_id"]
    await studio.turns.wait(pid)
    assert studio.store.get(pid)["name"] == "Skill: mine"
    text = next(e for e in studio.log(pid).read() if e["type"] == "turn_started")["text"]
    folder = (settings.data_dir / "skills" / "mine").resolve()
    assert text == ("Change my skill “mine” (use the make-a-skill skill, \"Changing a skill\"). "
                    f"Its folder is {folder}.\n\nAdd a step that checks the totals.\nKeep the rest.")


@pytest.mark.parametrize("body, status, code", [
    ({"id": "machine:nothing", "about": "explain"}, 404, "unknown_skill"),
    ({"id": "studio:make-a-skill", "about": "delete"}, 400, "invalid_request"),
    ({"id": "studio:make-a-skill", "about": "explain", "text": "x" * 2001}, 400, "invalid_request"),
])
async def test_a_question_that_is_refused_makes_no_project(client, studio, body, status, code):
    before = len(studio.store.list())
    answer = await client.post("/api/library/skills/ask", json=body)
    assert answer.status_code == status and answer.json()["error"]["code"] == code
    assert len(studio.store.list()) == before


async def test_on_a_shared_studio_reading_and_explaining_work_and_changing_does_not(client, studio, settings):
    skill = await machine_skill(client, settings, scripts=False)
    assert (await client.post("/api/skills/copy", json={"from": skill, "name": "mine"})).status_code == 201
    object.__setattr__(settings, "shared", True)
    try:
        assert (await client.get("/api/library/skills/detail", params={"id": skill})).status_code == 200
        assert (await client.get("/api/library/skills/file", params={"id": skill, "path": "SKILL.md"})).status_code == 200
        copy = await client.post("/api/skills/copy", json={"from": skill, "name": "another"})
        assert copy.status_code == 403 and copy.json()["error"]["code"] == "not_for_shared"
        change = await client.post("/api/library/skills/ask", json={"id": "yours:mine", "about": "change", "text": "x"})
        assert change.status_code == 403 and change.json()["error"]["code"] == "not_for_shared"
        explain = await client.post("/api/library/skills/ask", json={"id": skill, "about": "explain"})
        assert explain.status_code == 202
        await studio.turns.wait(explain.json()["project_id"])
    finally:
        object.__setattr__(settings, "shared", False)
