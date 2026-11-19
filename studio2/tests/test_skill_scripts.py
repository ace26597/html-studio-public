"""Scripts, exact paths and removal through `save_skill` (CONTRACT.md, "The tool save_skill")."""

from __future__ import annotations

import os
import stat

from studio2_server import ownskills
from test_own_skills import answer, args, call, refused, tree

SCRIPT = "#!/usr/bin/env python3\n# Adds the amounts of a CSV.\nimport sys\nprint(sum(1 for _ in open(sys.argv[1])))\n"


def skills(settings):
    return settings.data_dir / "skills"


async def test_a_script_is_saved_under_scripts_and_shown_whole_on_the_card(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "skill").mkdir()
    (workspace / "skill" / "totals.py").write_text(SCRIPT)
    (workspace / "example.html").write_text("<h1>Example</h1>")
    result, cards = await call(studio, pid, args(files=[{"path": "skill/totals.py"}, {"path": "example.html"}]))
    out = answer(result)
    assert out["files"] == ["reference/example.html", "scripts/totals.py"] and out["scripts"] == ["scripts/totals.py"]
    assert out["removed"] == []
    saved = tree(skills(settings) / "weekly-review")
    assert saved["scripts/totals.py"] == SCRIPT.encode()
    card = cards[0]
    assert card["reason"] == ownskills.ASK_REASON_SCRIPTS
    assert f"Script scripts/totals.py (new, 4 lines):\n{SCRIPT}".rstrip() in card["detail"]
    shown = card["skill"]
    assert shown["scripts"] == [{"path": "scripts/totals.py", "text": SCRIPT, "lines": 4, "replaces": False}]
    assert shown["files"] == [
        {"path": "scripts/totals.py", "size": len(SCRIPT), "script": True, "replaces": False},
        {"path": "reference/example.html", "size": 16, "script": False, "replaces": False}]
    assert shown["removed"] == [] and shown["kept"] == [] and shown["kept_scripts"] == []
    assert shown["description"].startswith("Use when") and shown["body"].startswith("# Weekly review")


async def test_a_skill_without_scripts_asks_as_before(studio, project):
    _result, cards = await call(studio, project["id"], args())
    assert cards[0]["reason"] == ownskills.ASK_REASON and cards[0]["skill"]["scripts"] == []
    assert "Script " not in cards[0]["detail"]


async def test_what_is_read_for_the_card_is_what_is_saved(studio, project, settings, monkeypatch):
    pid = project["id"]
    path = studio.store.workspace(pid) / "check.sh"
    path.write_text("echo one\n")
    real = studio.ask

    async def swap_then_ask(*a, **k):       # the project's copy changes while the card waits
        path.write_text("rm -rf ~\n")
        return await real(*a, **k)

    monkeypatch.setattr(studio, "ask", swap_then_ask)
    result, cards = await call(studio, pid, args(files=[{"path": "check.sh"}]))
    answer(result)
    assert cards[0]["skill"]["scripts"][0]["text"] == "echo one\n"
    assert (skills(settings) / "weekly-review" / "scripts" / "check.sh").read_text() == "echo one\n"


async def test_a_denied_script_writes_nothing(studio, project, settings):
    pid = project["id"]
    (studio.store.workspace(pid) / "a.py").write_text("print(1)\n")
    result, cards = await call(studio, pid, args(files=[{"path": "a.py"}]), decision="deny")
    assert "did not allow" in refused(result) and len(cards) == 1
    assert not tree(skills(settings))


async def test_scripts_that_are_refused(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "bin.py").write_bytes(b"\x00\x01\x02")
    (workspace / "latin.py").write_bytes("print('caf\xe9')".encode("latin-1"))
    (workspace / "long.py").write_text("x = 1\n" * 7_000)        # 42,000 characters
    (workspace / "half.py").write_text("y = 2\n" * 4_000)        # 24,000: two of them are too many
    (workspace / "half2.py").write_text("y = 2\n" * 4_000)
    (workspace / "ok.py").write_text("print(1)\n")
    (workspace / "note.md").write_text("note")
    (workspace / "data.json").write_text("{}")
    cases = [([{"path": "bin.py"}], "not text"), ([{"path": "latin.py"}], "not text"),
             ([{"path": "long.py"}], "40,000 characters"),
             ([{"path": "half.py"}, {"path": "half2.py"}], "40,000 characters"),
             ([{"path": "ok.py", "as": "ok.md"}], "keeps a script's ending"),
             ([{"path": "note.md", "to": "scripts/run.sh"}], "keeps a script's ending"),
             ([{"path": "ok.py", "as": "a.py", "to": "b.py"}], "not both"),
             ([{"path": "ok.py", "to": "SKILL.md"}], "keeps a script's ending"),
             ([{"path": "note.md", "to": "SKILL.md"}], "the `body`"),
             ([{"path": "note.md", "to": "studio-skill.json"}], "cannot be written"),
             ([{"path": "data.json", "to": "studio-skill.json"}], "cannot be written"),
             ([{"path": "ok.py", "to": "../ok.py"}], "cannot be used"),
             ([{"path": "ok.py", "to": "/tmp/ok.py"}], "cannot be used"),
             ([{"path": "ok.py", "to": ".hidden/ok.py"}], "cannot be used"),
             ([{"path": "ok.py", "to": "node_modules/ok.py"}], "cannot be written"),
             ([{"path": "ok.py", "to": "a/" * 60 + "ok.py"}], "cannot be used"),
             ([{"path": "ok.py"}, {"path": "ok.py", "to": "scripts/ok.py"}], "two files")]
    for files, word in cases:
        result, cards = await call(studio, pid, args(files=files))
        assert word in refused(result) and not cards, (files, result)
    assert not tree(skills(settings))


async def test_a_file_of_the_skill_is_replaced_where_it_is_and_one_is_removed(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    folder = skills(settings) / "weekly-review"
    (folder / "ooxml" / "scripts").mkdir(parents=True)
    (folder / "reference").mkdir()
    (folder / "SKILL.md").write_text("---\nname: weekly-review\ndescription: Old.\nlicense: MIT\n---\n\nOld body.\n")
    (folder / "ooxml" / "scripts" / "pack.py").write_text("print('old')\n")
    os.chmod(folder / "ooxml" / "scripts" / "pack.py", 0o755)
    (folder / "ooxml" / "schema.xsd").write_text("<xsd/>")
    (folder / "reference" / "old.html").write_text("old")
    (folder / "reference" / "keep.md").write_text("keep")
    studio.lib.catalog.ensure(force=True)
    (workspace / "pack.py").write_text("print('new')\n")

    result, cards = await call(studio, pid, args(
        replace=True, files=[{"path": "pack.py", "to": "ooxml/scripts/pack.py"}], remove=["reference/old.html"]))
    out = answer(result)
    assert out["replaced"] and out["removed"] == ["reference/old.html"]
    assert out["files"] == ["ooxml/schema.xsd", "ooxml/scripts/pack.py", "reference/keep.md"]
    assert out["scripts"] == ["ooxml/scripts/pack.py"]
    packed = folder / "ooxml" / "scripts" / "pack.py"
    assert packed.read_text() == "print('new')\n" and packed.stat().st_mode & stat.S_IXUSR     # stays executable
    assert "license: MIT" in (folder / "SKILL.md").read_text()
    shown = cards[0]["skill"]
    assert shown["scripts"] == [{"path": "ooxml/scripts/pack.py", "text": "print('new')\n", "lines": 1, "replaces": True}]
    assert shown["removed"] == ["reference/old.html"]
    assert shown["kept"] == ["ooxml/schema.xsd", "reference/keep.md"] and shown["kept_scripts"] == []
    detail = cards[0]["detail"]
    assert "Files removed:\nreference/old.html" in detail
    assert "Script ooxml/scripts/pack.py (replaces the one in the skill, 1 line):\nprint('new')" in detail
    # The version it replaced is whole in the history.
    history = next((skills(settings) / ".history").iterdir())
    assert (history / "reference" / "old.html").read_text() == "old"
    assert (history / "ooxml" / "scripts" / "pack.py").read_text() == "print('old')\n"


async def test_scripts_that_stay_are_named_and_change_the_reason(studio, project, settings):
    pid = project["id"]
    folder = skills(settings) / "weekly-review"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: weekly-review\ndescription: Old.\n---\n\nOld body.\n")
    (folder / "scripts" / "run.sh").write_text("echo hi\n")
    (folder / "reference").mkdir()
    (folder / "reference" / "last.md").write_text("x")
    studio.lib.catalog.ensure(force=True)
    _result, cards = await call(studio, pid, args(replace=True))
    assert cards[0]["skill"]["kept_scripts"] == ["scripts/run.sh"] and cards[0]["reason"] == ownskills.ASK_REASON_SCRIPTS
    # Removing the only file of a folder takes the folder with it; removing the script drops the sentence.
    result, cards = await call(studio, pid, args(replace=True, remove=["scripts/run.sh", "reference/last.md"]))
    assert answer(result)["files"] == [] and not (folder / "scripts").exists() and not (folder / "reference").exists()
    assert cards[-1]["reason"] == ownskills.ASK_REASON


async def test_removals_that_are_refused(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "new.md").write_text("new")
    result, cards = await call(studio, pid, args(remove=["reference/a.md"]))        # nothing to remove from
    assert "skill that exists" in refused(result) and not cards
    folder = skills(settings) / "weekly-review"
    (folder / "reference").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: weekly-review\ndescription: Old.\n---\n\nOld body.\n")
    (folder / "reference" / "a.md").write_text("a")
    studio.lib.catalog.ensure(force=True)
    before = tree(folder)
    cases = [(dict(remove=["reference/a.md"]), "already have a skill"),                 # no replace
             (dict(replace=True, remove=["reference/none.md"]), "no file at reference/none.md"),
             (dict(replace=True, remove=["SKILL.md"]), "the skill itself"),
             (dict(replace=True, remove=["../weekly-review/reference/a.md"]), "no file at"),
             (dict(replace=True, remove="reference/a.md"), "give a list"),
             (dict(replace=True, remove=["reference/a.md"] * 51), "at most 50"),
             (dict(replace=True, remove=["reference/a.md"], files=[{"path": "new.md", "as": "a.md"}]), "also written"),
             (dict(replace=True, files=[{"path": "new.md", "to": "reference"}]), "not allowed"),
             (dict(replace=True, files=[{"path": "new.md", "to": "Reference/A.md"}]), "that spelling")]
    for extra, word in cases:
        result, cards = await call(studio, pid, args(**extra))
        assert word in refused(result), (extra, result)
    assert tree(folder) == before


async def test_a_file_is_never_written_through_a_link(studio, project, settings):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "a.py").write_text("print('mine')\n")
    outside = settings.data_dir / "outside"
    outside.mkdir()
    (outside / "target.py").write_text("untouched\n")
    folder = skills(settings) / "weekly-review"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: weekly-review\ndescription: Old.\n---\n\nOld body.\n")
    (folder / "linked.py").symlink_to(outside / "target.py")
    (folder / "away").symlink_to(outside, target_is_directory=True)
    studio.lib.catalog.ensure(force=True)
    # A link at the path itself is replaced by the file.
    answer((await call(studio, pid, args(replace=True, files=[{"path": "a.py", "to": "linked.py"}])))[0])
    assert (outside / "target.py").read_text() == "untouched\n"
    assert not (folder / "linked.py").is_symlink() and (folder / "linked.py").read_text() == "print('mine')\n"
    # A folder on the way that is a link is refused, after the person allowed: nothing lands outside.
    result, _cards = await call(studio, pid, args(replace=True, files=[{"path": "a.py", "to": "away/new.py"}]))
    assert "not a plain folder" in refused(result)
    assert sorted(p.name for p in outside.iterdir()) == ["target.py"]
