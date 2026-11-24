"""Skills a person makes: the save_skill tool, the REST routes and the skill file (CONTRACT.md)."""

from __future__ import annotations

import asyncio
import io
import json
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from conftest import run_turn
from studio2_server import ownskills, tools

SKILL = "---\nname: shared-one\ndescription: Use when testing imports.\n---\n\n# Shared\n\nDo the thing.\n"


def tree(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()} \
        if folder.is_dir() else {}


def args(name="weekly-review", **extra) -> dict:
    return {"name": name, "description": "Use when the person asks for a weekly review.",
            "body": "# Weekly review\n\n1. Get the data.", **extra}


async def call(studio, project_id, fields, decision="allow"):
    """Run the tool inside a stand-in request, through the real approval path → (result, the cards)."""
    turn_id = "turn_skilltest"
    studio.approvers[project_id] = studio.turns._approver(project_id, turn_id)
    task = asyncio.create_task(tools.call(studio, project_id, "save_skill", fields))
    try:
        while not task.done():
            waiting = list(studio.turns.pending.get(turn_id, {}))
            if waiting:
                assert studio.turns.answer(turn_id, waiting[0], decision)
            await asyncio.sleep(0.005)
        result = await task
    finally:
        studio.approvers.pop(project_id, None)
    cards = [e for e in studio.log(project_id).read() if e["type"] == "approval_request" and e["turn_id"] == turn_id]
    return result, cards


def answer(result) -> dict:
    assert not result.get("is_error"), result
    return json.loads(result["content"][0]["text"])


def refused(result) -> str:
    assert result.get("is_error"), result
    return result["content"][0]["text"]


def make_zip(entries: dict, attrs: dict | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            if attrs and path in attrs:
                info.create_system, info.external_attr = 3, attrs[path] << 16
            archive.writestr(info, content)
    return out.getvalue()


async def upload(client, data: bytes, **params):
    return await client.post("/api/skills/import", params=params,
                             files={"file": ("one.skill.zip", data, "application/zip")})


# -- the tool ---------------------------------------------------------------------------------
@pytest.mark.parametrize("change, word", [
    ({"name": "Bad Name"}, "name:"), ({"name": "x" * 61}, "name:"), ({"name": "pdf"}, "studio's own"),
    ({"description": ""}, "description:"), ({"description": "d" * 601}, "description:"),
    ({"body": "  "}, "body:"), ({"body": "b" * 12_001}, "body:"), ({"body": "---\nname: x\n---\nHi"}, "front-matter"),
])
async def test_the_tool_refuses_bad_fields_before_asking(studio, project, settings, change, word):
    result, cards = await call(studio, project["id"], {**args(), **change})
    assert word in refused(result) and not cards and not tree(settings.data_dir / "skills")


async def test_it_asks_even_when_the_project_allows_everything(client, studio, project):
    await client.patch(f"/api/projects/{project['id']}", json={"approvals": "allow"})
    assert studio.store.get(project["id"])["approvals"] == "allow"
    result, cards = await call(studio, project["id"], args())
    assert answer(result)["saved"] is True and len(cards) == 1
    card = cards[0]
    assert card["tool"] == "save_skill" and card["title"] == "Save the skill “weekly-review” to your library"
    assert card["reason"] == "A skill in your library is given to the agent in every project from now on."
    assert "Use when the person asks" in card["detail"] and "1. Get the data." in card["detail"]


async def test_allow_saves_it_for_the_next_request(client, studio, project, settings):
    done = answer((await call(studio, project["id"], args()))[0])
    assert done["id"] == "yours:weekly-review" and done["replaced"] is False and done["files"] == [] and done["next"]
    listed = (await client.get("/api/library")).json()["skills"]
    assert next(s for s in listed if s["id"] == "yours:weekly-review")["source"] == "yours"
    await run_turn(client, studio, project["id"], "Anything")
    assert (studio.store.dir(project["id"]) / "library" / "skills" / "weekly-review" / "SKILL.md").is_file()


async def test_a_description_with_a_colon_survives(client, studio, project):
    fields = args(description='Use for: "weekly" reviews, always: yes')
    answer((await call(studio, project["id"], fields))[0])
    got = (await client.get("/api/skills/weekly-review")).json()
    assert got["description"] == 'Use for: "weekly" reviews, always: yes'


async def test_deny_writes_nothing_and_says_not_to_ask_again(studio, project, settings):
    result, cards = await call(studio, project["id"], args(), "deny")
    assert "Do not ask again" in refused(result) and len(cards) == 1
    assert not tree(settings.data_dir / "skills")


async def test_with_no_request_running_nothing_is_saved(studio, project, settings):
    result = await tools.call(studio, project["id"], "save_skill", args())
    assert "Nothing was saved" in refused(result) and not tree(settings.data_dir / "skills")


async def test_replacing_needs_replace_and_keeps_five_versions(studio, project, settings):
    pid = project["id"]
    answer((await call(studio, pid, args()))[0])
    result, _cards = await call(studio, pid, args(body="Second."))
    assert "replace: true" in refused(result)
    for number in range(7):
        done = answer((await call(studio, pid, args(body=f"Version {number}.", replace=True)))[0])
        assert done["replaced"] is True
    history = settings.data_dir / "skills" / ".history"
    kept = sorted(p.name for p in history.iterdir())
    assert len(kept) == 5 and all(name.startswith("weekly-review-") for name in kept)
    assert "Version 5." in (history / kept[-1] / "SKILL.md").read_text()
    assert "Version 6." in (settings.data_dir / "skills" / "weekly-review" / "SKILL.md").read_text()
    card = [c for c in studio.log(pid).read() if c["type"] == "approval_request"][-1]
    assert card["title"] == "Replace your skill “weekly-review”"


async def test_history_of_one_name_does_not_touch_a_longer_name(studio, project, settings):
    pid = project["id"]
    for name in ("a", "a-b"):
        answer((await call(studio, pid, args(name=name)))[0])
    answer((await call(studio, pid, args(name="a-b", body="Two.", replace=True)))[0])
    for number in range(6):
        answer((await call(studio, pid, args(name="a", body=f"V{number}.", replace=True)))[0])
    kept = [p.name for p in (settings.data_dir / "skills" / ".history").iterdir()]
    assert sum(name.startswith("a-b-") for name in kept) == 1 and len(kept) == 6


async def test_reference_files_are_copied_and_stay_on_replace(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "example.html").write_text("<h1>Example</h1>")
    (workspace / "look.png").write_bytes(b"\x89PNG-bytes")
    done = answer((await call(studio, pid, args(files=[{"path": "example.html"}, {"path": "look.png", "as": "the-look.png"}])))[0])
    assert done["files"] == ["reference/example.html", "reference/the-look.png"]
    folder = settings.data_dir / "skills" / "weekly-review"
    assert (folder / "reference" / "the-look.png").read_bytes() == b"\x89PNG-bytes"
    result, cards = await call(studio, pid, args(body="Changed.", replace=True))
    assert answer(result)["files"] == done["files"] and "reference/example.html" in cards[-1]["detail"]


async def test_reference_files_that_are_refused(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "run.py").write_text("print(1)")
    (workspace / "note.md").write_text("note")
    (workspace / "big.csv").write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    for number in range(3):
        (workspace / f"part{number}.csv").write_bytes(b"x" * (4 * 1024 * 1024))
    outside = settings.data_dir / "outside.md"
    outside.write_text("secret")
    (workspace / "link.md").symlink_to(outside)
    (workspace / "tool.bin").write_bytes(b"\x7fELF")
    cases = [([{"path": "tool.bin"}], "tool.bin is not allowed"), ([{"path": "run.py", "as": "run.txt"}], "run.txt"),
             ([{"path": "note.md", "as": "note.sh"}], "note.sh"), ([{"path": "../outside.md"}], "not a file inside"),
             ([{"path": "link.md"}], "not a file inside"), ([{"path": "missing.md"}], "no file at"),
             ([{"path": "note.md", "as": "../../x.md"}], "cannot be used"),
             ([{"path": "note.md"}] * 13, "at most 12"), ([{"path": "big.csv"}], "at most 5 MB"),
             ([{"path": f"part{n}.csv"} for n in range(3)], "10 MB together"),
             ([{"path": "note.md"}, {"path": "note.md"}], "two files")]
    for files, word in cases:
        result, cards = await call(studio, pid, args(files=files))
        assert word in refused(result) and not cards, files
    assert not tree(settings.data_dir / "skills")


async def test_the_whole_body_reaches_the_card_and_other_cards_are_still_cut(studio, project):
    pid = project["id"]
    body = "é" * 9_000 + " the end of the instructions"
    _result, cards = await call(studio, pid, args(body=body))
    assert body in cards[0]["detail"]
    other = studio.turns._approver(pid, "turn_other")
    task = asyncio.create_task(other("Bash", "Run", "x" * 9_000, "Why"))
    await asyncio.sleep(0.01)
    studio.turns.answer("turn_other", next(iter(studio.turns.pending["turn_other"])), "deny")
    await task
    card = [e for e in studio.log(pid).read() if e["type"] == "approval_request" and e["turn_id"] == "turn_other"][0]
    assert len(card["detail"]) <= 4_000 and card["detail"].endswith("[truncated]")


async def test_the_tool_is_listed_and_the_agent_is_told(client, studio, project):
    tools_listed = (await client.get("/api/library")).json()["studio_tools"]
    assert any(tool["name"] == "save_skill" for tool in tools_listed) and len(tools.TOOLS) == 27
    from studio2_server.harness.claude import _studio_tool
    assert _studio_tool("save_skill", {"name": "x"}.get) == ("other", "Save the skill “x”")
    assert "save_skill" in (studio.settings.library_dir / "system-prompt.md").read_text()     # said once, in the system prompt


# -- REST -------------------------------------------------------------------------------------
async def test_get_and_put_a_skill(client, studio, project, settings):
    made = await client.post("/api/skills", json={"name": "house", "description": "Our style.", "body": "Use blue."})
    assert made.status_code == 201
    folder = settings.data_dir / "skills" / "house"
    (folder / "reference").mkdir()
    (folder / "reference" / "a.txt").write_text("abc")
    got = (await client.get("/api/skills/house")).json()
    assert got["id"] == "yours:house" and got["body"] == "Use blue." and got["files"] == [{"path": "reference/a.txt", "size": 3}]
    changed = await client.put("/api/skills/house", json={"body": "Use green."})
    assert changed.status_code == 200 and changed.json()["body"] == "Use green." and changed.json()["description"] == "Our style."
    assert (folder / "reference" / "a.txt").read_text() == "abc"
    assert len(list((settings.data_dir / "skills" / ".history").iterdir())) == 1
    same = await client.put("/api/skills/house", json={"body": "Use green."})
    assert same.status_code == 200 and len(list((settings.data_dir / "skills" / ".history").iterdir())) == 1
    renamed = (await client.put("/api/skills/house", json={"description": "Our new style."})).json()
    assert renamed["description"] == "Our new style." and renamed["body"] == "Use green."


async def test_put_keeps_other_front_matter(client, settings):
    folder = settings.data_dir / "skills" / "kept"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: kept\ndescription: Old.\nlicense: MIT\n---\n\nBody.\n")
    await client.post("/api/library/refresh")
    assert (await client.put("/api/skills/kept", json={"description": "New."})).status_code == 200
    assert "license: MIT" in (folder / "SKILL.md").read_text()


@pytest.mark.parametrize("method, path, body, status, code", [
    ("get", "/api/skills/nothing", None, 404, "unknown_skill"), ("get", "/api/skills/pdf", None, 404, "unknown_skill"),
    ("put", "/api/skills/nothing", {"body": "x"}, 404, "unknown_skill"),
    ("put", "/api/skills/mine", {}, 400, "invalid_request"),
    ("put", "/api/skills/mine", {"body": "b" * 12_001}, 400, "invalid_request"),
    ("put", "/api/skills/mine", {"description": "d" * 601}, 400, "invalid_request"),
])
async def test_routes_refuse(client, method, path, body, status, code):
    await client.post("/api/skills", json={"name": "mine", "description": "Mine.", "body": "Body."})
    response = await client.request(method.upper(), path, json=body) if body is not None else await client.request(method.upper(), path)
    assert response.status_code == status and response.json()["error"]["code"] == code


async def test_post_takes_the_full_limits_and_refuses_a_studio_name(client):
    fine = {"name": "long-one", "description": "d" * 600, "body": "b" * 12_000}
    assert (await client.post("/api/skills", json=fine)).status_code == 201
    assert (await client.post("/api/skills", json={**fine, "name": "other", "body": "b" * 12_001})).status_code == 400
    taken = await client.post("/api/skills", json={**fine, "name": "pdf"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "name_taken_by_studio"


async def test_export_then_import_gives_the_same_bytes(client, studio, project, settings):
    workspace = studio.store.workspace(project["id"])
    (workspace / "look.png").write_bytes(bytes(range(256)) * 40)
    answer((await call(studio, project["id"], args(files=[{"path": "look.png"}])))[0])
    folder = settings.data_dir / "skills" / "weekly-review"
    before = tree(folder)
    response = await client.get("/api/library/skills/export", params={"id": "yours:weekly-review"})
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="weekly-review.skill.zip"'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("studio-skill.json"))
    assert manifest["format"] == 1 and manifest["name"] == "weekly-review" and manifest["source"] == "yours"
    assert manifest["files"] == ["SKILL.md", "reference/look.png"] and manifest["scripts"] == [] and manifest["exported_at"]
    assert (await client.delete("/api/skills/weekly-review")).status_code == 204
    made = await upload(client, response.content)
    assert made.status_code == 201 and made.json()["id"] == "yours:weekly-review"
    assert tree(folder) == before


async def test_export_covers_every_source_and_leaves_out_what_it_should(client, settings):
    home = settings.home / ".claude" / "skills" / "from-machine"
    (home / "node_modules" / "x").mkdir(parents=True)
    (home / "node_modules" / "x" / "a.js").write_text("x")
    (home / "SKILL.md").write_text("---\nname: from-machine\ndescription: Found here.\n---\n\nBody.\n")
    (home / ".env").write_text("KEY=secret")
    secret = settings.home / "secret.txt"
    secret.write_text("secret")
    (home / "link.txt").symlink_to(secret)
    await client.post("/api/library/refresh")
    found = await client.get("/api/library/skills/export", params={"id": "machine:from-machine"})
    with zipfile.ZipFile(io.BytesIO(found.content)) as archive:
        assert sorted(archive.namelist()) == ["SKILL.md", "studio-skill.json"]
    studio_one = await client.get("/api/library/skills/export", params={"id": "studio:make-a-skill"})
    assert studio_one.status_code == 200
    missing = await client.get("/api/library/skills/export", params={"id": "yours:nothing"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_skill"


async def test_export_refuses_above_the_limit(client, settings, monkeypatch):
    monkeypatch.setattr(ownskills, "MAX_EXPORT_BYTES", 10)
    await client.post("/api/skills", json={"name": "big", "description": "Big.", "body": "Long enough to pass ten bytes."})
    response = await client.get("/api/library/skills/export", params={"id": "yours:big"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "too_large"


async def test_the_preview_writes_nothing(client, settings):
    data = make_zip({"shared-one/SKILL.md": SKILL, "shared-one/notes/a.md": "abc", "shared-one/run.sh": "echo"})
    response = await upload(client, data, dry_run=1)
    assert response.status_code == 200
    preview = response.json()
    assert preview["name"] == "shared-one" and preview["description"] == "Use when testing imports."
    assert preview["body_start"].startswith("# Shared") and preview["scripts"] == ["run.sh"]
    assert {"path": "notes/a.md", "size": 3} in preview["files"] and preview["size"] == len(SKILL) + 3 + 4
    assert preview["exists"] is False and preview["taken_by_studio"] is False
    assert not tree(settings.data_dir / "skills")


async def test_scripts_need_confirmation(client, settings):
    data = make_zip({"SKILL.md": SKILL, "tool.bin": "x"}, {"tool.bin": stat.S_IFREG | 0o755})
    held = await upload(client, data)
    assert held.status_code == 409 and held.json()["error"]["code"] == "scripts_need_confirmation"
    assert held.json()["error"]["details"]["scripts"] == ["tool.bin"] and not tree(settings.data_dir / "skills")
    assert (await upload(client, data, allow_scripts=1)).status_code == 201
    assert (settings.data_dir / "skills" / "shared-one" / "tool.bin").stat().st_mode & 0o111


async def test_names_already_taken(client, settings):
    data = make_zip({"SKILL.md": SKILL})
    assert (await upload(client, data)).status_code == 201
    again = await upload(client, data)
    assert again.status_code == 409 and again.json()["error"]["code"] == "name_taken"
    assert (await upload(client, data, dry_run=1)).json()["exists"] is True
    assert (await upload(client, data, replace=1)).status_code == 201
    assert len(list((settings.data_dir / "skills" / ".history").iterdir())) == 1
    studio_named = make_zip({"SKILL.md": SKILL.replace("shared-one", "pdf")})
    taken = await upload(client, studio_named)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "name_taken_by_studio"
    assert (await upload(client, studio_named, dry_run=1)).json()["taken_by_studio"] is True
    renamed = await upload(client, studio_named, name="my-pdf")
    assert renamed.status_code == 201 and renamed.json()["id"] == "yours:my-pdf"
    assert "name: my-pdf" in (settings.data_dir / "skills" / "my-pdf" / "SKILL.md").read_text()
    bad = await upload(client, studio_named, name="Bad Name")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request"


def lying_zip() -> bytes:
    """A zip whose directory says one small file while the entry unpacks to much more."""
    data = bytearray(make_zip({"SKILL.md": SKILL, "a.txt": b"x" * 100_000}))
    at = data.find(b"PK\x01\x02", data.find(b"PK\x01\x02") + 4)
    data[at + 24:at + 28] = struct.pack("<I", 10)
    return bytes(data)


@pytest.mark.parametrize("data", [
    pytest.param(b"not a zip", id="not-a-zip"),
    pytest.param(make_zip({"notes.md": "x"}), id="no-skill"),
    pytest.param(make_zip({"../x": "x", "SKILL.md": SKILL}), id="dot-dot"),
    pytest.param(make_zip({"/etc/x": "x", "SKILL.md": SKILL}), id="absolute"),
    pytest.param(make_zip({"a\\..\\x": "x", "SKILL.md": SKILL}), id="backslash"),
    pytest.param(make_zip({"SKILL.md": SKILL, "link": "/etc/passwd"}, {"link": stat.S_IFLNK | 0o777}), id="link"),
    pytest.param(make_zip({"a/SKILL.md": SKILL, "b/SKILL.md": SKILL}), id="two-skills"),
    pytest.param(make_zip({"SKILL.md": SKILL, "b/SKILL.md": SKILL}), id="two-skills-top"),
    pytest.param(make_zip({"a/SKILL.md": SKILL, "b/x.md": "x"}), id="two-folders"),
    pytest.param(make_zip({"SKILL.md": "# No front matter\n"}), id="no-front-matter"),
    pytest.param(make_zip({"SKILL.md": "---\nname: only-name\n---\n\nBody\n"}), id="no-description"),
    pytest.param(make_zip({"SKILL.md": "---\ndescription: Only this.\n---\n\nBody\n"}), id="no-name"),
    pytest.param(make_zip({"SKILL.md": SKILL.replace("shared-one", "Not A Name")}), id="bad-name"),
    pytest.param(make_zip({"SKILL.md": SKILL, "A.md": "x", "a.md": "y"}), id="same-path"),
    pytest.param(make_zip({"SKILL.md": SKILL, "a": "x", "a/b.md": "y"}), id="file-and-folder"),
    pytest.param(make_zip({"SKILL.md": SKILL, **{f"f{n}.md": "x" for n in range(600)}}), id="too-many"),
    pytest.param(make_zip({"SKILL.md": SKILL, "huge.txt": b"\0" * (60 * 1024 * 1024 + 1)}), id="too-large-unpacked"),
    pytest.param(lying_zip(), id="lying-header"),
])
async def test_bad_skill_files_are_refused_and_change_nothing(client, settings, data):
    await client.post("/api/skills", json={"name": "already", "description": "Here.", "body": "Stay."})
    before = tree(settings.data_dir / "skills")
    for params in ({"dry_run": 1}, {"allow_scripts": 1, "replace": 1}):
        response = await upload(client, data, **params)
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_skill_file", response.text
    assert tree(settings.data_dir / "skills") == before
    assert not list((settings.data_dir / "run" / "skill-stage").glob("*")) if (settings.data_dir / "run" / "skill-stage").exists() else True


async def test_a_file_larger_than_the_packed_limit_is_refused(client, monkeypatch):
    monkeypatch.setattr(ownskills, "MAX_PACKED_BYTES", 50)
    response = await upload(client, make_zip({"SKILL.md": SKILL}))
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_skill_file"


async def test_a_folder_zipped_on_a_mac_is_read(client, settings):
    data = make_zip({"shared-one/SKILL.md": SKILL, "shared-one/.DS_Store": "x", "__MACOSX/shared-one/._SKILL.md": "x"})
    assert (await upload(client, data)).status_code == 201
    assert sorted(tree(settings.data_dir / "skills" / "shared-one")) == ["SKILL.md"]


async def test_history_is_never_a_skill(client, studio, project, settings):
    answer((await call(studio, project["id"], args()))[0])
    answer((await call(studio, project["id"], args(body="Two.", replace=True)))[0])
    assert (settings.data_dir / "skills" / ".history").is_dir()
    await client.post("/api/library/refresh")
    items = (await client.get("/api/library/items", params={"kind": "skills", "limit": 200})).json()["items"]
    ids = [item["id"] for item in items]
    assert ids.count("yours:weekly-review") == 1 and not any(".history" in i or "weekly-review-2" in i for i in ids)
    assert (await client.get("/api/skills/.history")).status_code == 404


async def test_a_shared_studio_exports_for_everyone_and_changes_nothing(client, studio, project, settings):
    await client.post("/api/skills", json={"name": "mine", "description": "Mine.", "body": "Body."})
    settings.shared = True
    assert (await client.get("/api/library/skills/export", params={"id": "yours:mine"})).status_code == 200
    for response in (await client.put("/api/skills/mine", json={"body": "New."}), await upload(client, make_zip({"SKILL.md": SKILL})),
                     await upload(client, make_zip({"SKILL.md": SKILL}), dry_run=1),
                     await client.post("/api/skills", json={"name": "other", "description": "Other.", "body": "Body."}),
                     await client.delete("/api/skills/mine")):
        assert response.status_code == 403 and response.json()["error"]["code"] == "not_for_shared"
    assert (settings.data_dir / "skills" / "mine" / "SKILL.md").is_file()      # still there
    result, cards = await call(studio, project["id"], args())
    assert "shared studio" in refused(result) and not cards
