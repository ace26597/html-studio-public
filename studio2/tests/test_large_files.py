"""Files too large for versions (CONTRACT.md, "Files too large for versions").

Every test uses a limit of a few hundred bytes, so no test writes a large file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from studio2_server import workspace as ws
from studio2_server.config import Settings

LIMIT = 1000
BIG = "y" * 2000          # over the limit
SMALL = "x" * 200         # under it


def tree(workspace: Path, rev: str = "HEAD") -> set[str]:
    return {name for name in ws.git(workspace, "ls-tree", "-r", "-z", "--name-only", rev).split("\0") if name}


def blobs(workspace: Path) -> set[str]:
    """Every object id in the project's git store."""
    out = ws.git(workspace, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
    return set(out.split())


def blob_id(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def make(tmp_path: Path) -> Path:
    workspace = tmp_path / "project"
    ws.init_workspace(workspace, "Test", max_bytes=LIMIT)
    return workspace


def test_a_large_file_is_left_out_and_never_written_to_the_history(tmp_path):
    workspace = make(tmp_path)
    (workspace / "data.csv").write_text(BIG)
    (workspace / "page.html").write_text("<p>hi</p>")
    made = ws.checkpoint(workspace, "agent", "Add data", max_bytes=LIMIT)
    assert made["files"] == [{"path": "page.html", "status": "added"}]
    assert made["left_out"] == [{"path": "data.csv", "size": 2000}]
    assert made["left_out_count"] == 1
    assert "data.csv" not in tree(workspace)
    assert blob_id(BIG) not in blobs(workspace)          # not even as a loose object
    assert (workspace / "data.csv").read_text() == BIG
    # Nothing else changed: no empty version just because the large file is still there.
    assert ws.checkpoint(workspace, "agent", "Again", max_bytes=LIMIT) is None


def test_without_large_files_the_checkpoint_is_as_before(tmp_path):
    workspace = make(tmp_path)
    (workspace / "a.txt").write_text(SMALL)
    made = ws.checkpoint(workspace, "agent", "Add", max_bytes=LIMIT)
    assert "left_out" not in made and "left_out_count" not in made
    assert ws.checkpoint(workspace, "agent", "Again", max_bytes=LIMIT) is None


def test_no_limit_keeps_every_file(tmp_path):
    workspace = make(tmp_path)
    (workspace / "data.csv").write_text(BIG)
    made = ws.checkpoint(workspace, "agent", "Add", max_bytes=0)
    assert "data.csv" in tree(workspace) and "left_out" not in made


def test_a_tracked_file_that_grows_leaves_the_tree_and_comes_back_when_it_shrinks(tmp_path):
    workspace = make(tmp_path)
    data = workspace / "data.csv"
    data.write_text(SMALL)
    ws.checkpoint(workspace, "agent", "Small", max_bytes=LIMIT)
    assert "data.csv" in tree(workspace)
    data.write_text(BIG)
    grown = ws.checkpoint(workspace, "agent", "Grown", max_bytes=LIMIT)
    assert {"path": "data.csv", "status": "deleted"} in grown["files"]
    assert grown["left_out"] == [{"path": "data.csv", "size": 2000}]
    assert "data.csv" not in tree(workspace)
    assert data.read_text() == BIG                        # still on disk
    data.write_text(SMALL + "again")
    shrunk = ws.checkpoint(workspace, "agent", "Shrunk", max_bytes=LIMIT)
    assert {"path": "data.csv", "status": "added"} in shrunk["files"]
    assert "left_out" not in shrunk
    assert ws.git(workspace, "show", "HEAD:data.csv") == SMALL + "again"


def test_restore_never_deletes_overwrites_or_reverts_a_left_out_file(tmp_path):
    workspace = make(tmp_path)
    data = workspace / "data.csv"
    data.write_text(SMALL)
    old = ws.checkpoint(workspace, "agent", "Small data", max_bytes=LIMIT)      # data.csv in this tree
    before_data = ws.checkpoint(workspace, "agent", "nothing", max_bytes=LIMIT)
    assert before_data is None
    data.write_text(BIG)
    (workspace / "other.bin").write_text(BIG + "other")                          # in no version at all
    (workspace / "index.html").write_text("<p>changed</p>")
    ws.checkpoint(workspace, "agent", "Grown", max_bytes=LIMIT)
    # Restore the version that still holds the small copy of data.csv, and one from before it existed.
    first = ws.versions(workspace)[-1]["sha"]
    for sha in (old["sha"], first):
        made = ws.restore(workspace, sha, max_bytes=LIMIT)
        assert data.read_text() == BIG
        assert (workspace / "other.bin").read_text() == BIG + "other"
        assert {entry["path"] for entry in made["left_out"]} == {"data.csv", "other.bin"}
        assert "data.csv" not in tree(workspace)
    # The rest of the old version did come back.
    assert (workspace / "index.html").read_text() == ws.git(workspace, "show", f"{first}:index.html")


def test_without_the_protection_restore_would_have_lost_the_file(tmp_path):
    """Shows the test above proves something: plain read-tree overwrites the untracked file."""
    workspace = make(tmp_path)
    data = workspace / "data.csv"
    data.write_text(SMALL)
    old = ws.checkpoint(workspace, "agent", "Small", max_bytes=LIMIT)
    data.write_text(BIG)
    ws.checkpoint(workspace, "agent", "Grown", max_bytes=LIMIT)
    ws.git(workspace, "read-tree", "--reset", "-u", old["sha"])
    assert data.read_text() == SMALL


def test_restore_keeps_a_large_ignored_file_and_a_folder_in_its_way(tmp_path):
    workspace = make(tmp_path)
    (workspace / "cache.bin").write_text(SMALL)
    (workspace / "results").write_text(SMALL)                    # a file named like a later folder
    old = ws.checkpoint(workspace, "agent", "Old", max_bytes=LIMIT)
    (workspace / ".gitignore").write_text((workspace / ".gitignore").read_text() + "cache.bin\n")
    ws.git(workspace, "rm", "-q", "--cached", "cache.bin")
    (workspace / "cache.bin").write_text(BIG)
    (workspace / "results").unlink()
    (workspace / "results").mkdir()
    (workspace / "results" / "run 1.parquet").write_text(BIG)
    ws.checkpoint(workspace, "agent", "New", max_bytes=LIMIT)
    ws.restore(workspace, old["sha"], max_bytes=LIMIT)
    assert (workspace / "cache.bin").read_text() == BIG
    assert (workspace / "results" / "run 1.parquet").read_text() == BIG


def test_many_large_files_with_spaces_and_unicode_in_their_names(tmp_path):
    workspace = make(tmp_path)
    names = [f"données/année {n} ü.csv" for n in range(60)] + ["a b/c d.parquet", "日本語.json"]
    for n, name in enumerate(names):
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BIG + "z" * n)
    (workspace / "small file ü.txt").write_text(SMALL)
    made = ws.checkpoint(workspace, "agent", "Many", max_bytes=LIMIT)
    assert made["files"] == [{"path": "small file ü.txt", "status": "added"}]
    assert made["left_out_count"] == len(names)
    assert len(made["left_out"]) == ws.LEFT_OUT_SHOWN
    sizes = [entry["size"] for entry in made["left_out"]]
    assert sizes == sorted(sizes, reverse=True)
    assert not tree(workspace) & set(names)
    first = ws.versions(workspace)[-1]["sha"]
    ws.restore(workspace, first, max_bytes=LIMIT)
    assert all((workspace / name).is_file() for name in names)


def test_a_symbolic_link_to_a_large_file_is_kept(tmp_path):
    workspace = make(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text(BIG)
    (workspace / "link.csv").symlink_to(outside)
    made = ws.checkpoint(workspace, "agent", "Link", max_bytes=LIMIT)
    assert "link.csv" in tree(workspace) and "left_out" not in made


def test_history_that_already_holds_a_large_file_is_not_rewritten(tmp_path):
    workspace = make(tmp_path)
    (workspace / "data.csv").write_text(BIG)
    before = ws.checkpoint(workspace, "agent", "Before the limit", max_bytes=0)
    (workspace / "data.csv").write_text(BIG + "changed")
    ws.checkpoint(workspace, "agent", "After", max_bytes=LIMIT)
    assert ws.git(workspace, "show", f"{before['sha']}:data.csv") == BIG
    assert blob_id(BIG + "changed") not in blobs(workspace)


def test_the_setting_reads_megabytes(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("STUDIO2_VERSION_MAX_FILE_MB", raising=False)
    assert Settings.from_env().version_max_file_bytes == 100 * 1024 * 1024
    monkeypatch.setenv("STUDIO2_VERSION_MAX_FILE_MB", "0")
    assert Settings.from_env().version_max_file_bytes == 0
    monkeypatch.setenv("STUDIO2_VERSION_MAX_FILE_MB", "2.5")
    assert Settings.from_env().version_max_file_bytes == int(2.5 * 1024 * 1024)
    monkeypatch.setenv("STUDIO2_VERSION_MAX_FILE_MB", "lots")
    assert Settings.from_env().version_max_file_bytes == 100 * 1024 * 1024


# -- through the studio --------------------------------------------------------------------------

@pytest.fixture
def settings(settings):
    settings.version_max_file_bytes = LIMIT
    return settings


async def test_the_event_and_the_agent_are_told(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    _, current = studio.project_facts(pid)
    assert "not saved in versions" not in current            # quiet when there are none
    (workspace / "big data.csv").write_text(BIG)
    response = await client.put(f"/api/projects/{pid}/file", json={"path": "notes.txt", "content": "hi"})
    assert response.status_code == 200, response.text
    made = response.json()["checkpoint"]
    assert made["left_out"] == [{"path": "big data.csv", "size": 2000}] and made["left_out_count"] == 1
    event = [e for e in studio.log(pid).read() if e["type"] == "checkpoint"][-1]
    assert event["left_out"] == made["left_out"] and event["left_out_count"] == 1
    _, current = studio.project_facts(pid)
    line = next(line for line in current.splitlines() if "not saved in versions" in line)
    assert "undo cannot bring them back: big data.csv." in line


async def test_the_agent_line_caps_the_list(studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    for n in range(14):
        (workspace / f"part {n:02}.csv").write_text(BIG + "z" * n)
    _, current = studio.project_facts(pid)
    line = next(line for line in current.splitlines() if "not saved in versions" in line)
    assert line.count(".csv") == 10 and line.endswith("and 4 more. Do not commit them with git yourself.")


async def test_restore_through_the_studio_keeps_the_file(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    data = workspace / "data.csv"
    data.write_text(SMALL)
    old = (await client.put(f"/api/projects/{pid}/file", json={"path": "a.txt", "content": "1"})).json()["checkpoint"]
    data.write_text(BIG)
    await client.put(f"/api/projects/{pid}/file", json={"path": "a.txt", "content": "2"})
    response = await client.post(f"/api/projects/{pid}/restore", json={"sha": old["sha"]})
    assert response.status_code == 200, response.text
    assert data.read_text() == BIG
    assert (workspace / "a.txt").read_text() == "1"
    assert response.json()["checkpoint"]["left_out"] == [{"path": "data.csv", "size": 2000}]


async def test_an_attached_app_keeps_saving_every_file(studio, project):
    pid = project["id"]
    studio.store.update(pid, attach={"mode": "copy", "kind": "git"})
    assert studio.version_limit(pid) == 0
    workspace = studio.store.workspace(pid)
    (workspace / "data.csv").write_text(BIG)
    made = await studio.checkpoint(pid, "user", "Big file")
    assert "data.csv" in tree(workspace) and "left_out" not in made
    _, current = studio.project_facts(pid)
    assert "not saved in versions" not in current


async def test_a_saved_template_and_a_project_made_from_it_carry_the_file(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "data.csv").write_text(BIG)
    await studio.checkpoint(pid, "user", "Big file")
    saved = studio.saved.save(studio.store.get(pid), workspace, "With data", kind="app")   # copied, not a pattern
    assert (studio.saved.files(saved["id"]) / "data.csv").read_text() == BIG
    response = await client.post("/api/projects", json={"name": "From it", "template": saved["id"]})
    assert response.status_code == 201, response.text
    copy = studio.store.workspace(response.json()["id"])
    assert (copy / "data.csv").read_text() == BIG
    assert "data.csv" not in tree(copy)
