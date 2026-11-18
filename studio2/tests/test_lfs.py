"""Git LFS files: the attach form says, before a safe copy is made, that the copy will hold only
their pointer files (CONTRACT.md, Inspect `lfs`). Filling a copy with the real files is not built.

git-lfs itself is not needed (nor installed on the machine these were written on). The person's
repository gets a stand-in: `filter.lfs.clean/smudge` in its own .git/config naming a small
script that does what git-lfs does (stores the real bytes under .git/lfs/objects by SHA-256 and
commits a pointer; puts the real bytes back on checkout). To git, the person's folder then looks
as a real LFS checkout does: pointers in the commits, real files in the folder, a clean
`git status`. The script logs every run, so the tests also say inspection never ran it.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from studio2_server import attach as attaching, lfs
from test_ship import attach, make_site, run_git

FAKE_LFS = r'''
import hashlib, os, sys
from pathlib import Path
mode, log = sys.argv[1], sys.argv[2]
data = sys.stdin.buffer.read()
with open(log, "a") as out:
    out.write(f"{mode} {os.getcwd()} {sys.argv[3:]}\n")
store = Path(os.getcwd()) / ".git" / "lfs" / "objects"
if mode == "clean":
    if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        result = data
    else:
        oid = hashlib.sha256(data).hexdigest()
        target = store / oid[:2] / oid[2:4] / oid
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        result = f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(data)}\n".encode()
else:
    result = data
    lines = data.decode("ascii", "replace").splitlines()
    if len(lines) == 3 and lines[1].startswith("oid sha256:"):
        oid = lines[1][len("oid sha256:"):]
        target = store / oid[:2] / oid[2:4] / oid
        if target.is_file():
            result = target.read_bytes()
sys.stdout.buffer.write(result)
'''

REAL_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
REAL_BIN = b"model weights " * 300
REAL_DAT = b"never fetched " * 200


def pointer(data: bytes) -> bytes:
    return (f"version https://git-lfs.github.com/spec/v1\noid sha256:{hashlib.sha256(data).hexdigest()}\n"
            f"size {len(data)}\n").encode()


@pytest.fixture
def lfs_log(tmp_path) -> Path:
    return tmp_path / "fake-lfs.log"


def use_fake_lfs(repo: Path, tmp_path: Path, log: Path) -> None:
    script = tmp_path / "fake_lfs.py"
    script.write_text(FAKE_LFS)
    for mode in ("clean", "smudge"):
        run_git(repo, "config", f"filter.lfs.{mode}", f"{sys.executable} {script} {mode} {log} %f")
    run_git(repo, "config", "filter.lfs.required", "true")


def make_lfs_site(tmp_path: Path, log: Path, name: str = "lfs-site") -> Path:
    """A git folder as a real Git LFS checkout looks: three LFS files, of which the folder holds
    two for real and one only as its pointer (never downloaded)."""
    site = make_site(tmp_path, name)
    use_fake_lfs(site, tmp_path, log)
    (site / ".gitattributes").write_text("*.png filter=lfs diff=lfs merge=lfs -text\n")
    (site / "data").mkdir()
    (site / "data" / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n"
                                                  "c.dat filter=lfs diff=lfs merge=lfs -text\n")
    (site / "a.png").write_bytes(REAL_PNG)
    (site / "data" / "b.bin").write_bytes(REAL_BIN)
    (site / "data" / "c.dat").write_bytes(REAL_DAT)
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "Add large files")
    (site / "data" / "c.dat").write_bytes(pointer(REAL_DAT))
    # A checkout records each file's size as written; git trusts a changed size without running
    # the filter, so the index is told the new size (same blob: the clean step passes a pointer on).
    run_git(site, "add", "-A")
    assert run_git(site, "status", "--porcelain") == ""       # what a real LFS checkout shows
    return site


async def inspect(client, folder: Path) -> dict:
    response = await client.post("/api/apps/inspect", json={"path": str(folder)})
    assert response.status_code == 200, response.text
    return response.json()


def lfs_notes(found: dict) -> list[str]:
    return [note for note in found["notes"] if "Git LFS" in note]


def test_pointer_rule():
    assert lfs.parse(pointer(b"abc")) == (hashlib.sha256(b"abc").hexdigest(), 3)
    assert lfs.parse(b"version https://git-lfs.github.com/spec/v1\noid sha256:xyz\nsize 3\n") is None
    assert lfs.parse(b"<h1>Hello</h1>\n") is None and lfs.parse(REAL_PNG) is None


async def test_inspection_tells_before_the_copy_that_lfs_files_will_be_pointers(client, tmp_path, lfs_log):
    site = make_lfs_site(tmp_path, lfs_log)
    found = await inspect(client, site)
    assert found["lfs"] == {"files": 3, "names": ["a.png", "data/b.bin", "data/c.dat"], "not_here": ["data/c.dat"],
                            "not_here_count": 1}
    [note] = lfs_notes(found)
    assert "3 files" in note and "a.png" in note and "data/b.bin" in note, note
    assert "pointer file" in note and "missing or broken in the copy" in note and "edited in place" in note, note
    assert "not downloaded in this folder either (data/c.dat)" in note and "git lfs pull" in note, note
    assert "`" not in note
    # Finding them runs no filter program. (Inspection's `git status`, older than this, may run the
    # person's own clean filter in his own folder, as his git does; that is not this part.)
    lines = lfs_log.read_text().splitlines()
    assert lfs.inspect(site, True, attaching._git_read_bytes)["files"] == 3
    assert lfs_log.read_text().splitlines() == lines


async def test_the_reviewers_cases_follow_what_is_committed(client, tmp_path, lfs_log):
    site = make_site(tmp_path)
    use_fake_lfs(site, tmp_path, lfs_log)
    (site / ".gitattributes").write_text("*.png filter=lfs diff=lfs merge=lfs -text\n"
                                         "icons/*.png -filter -diff -merge text\n")
    (site / ".git" / "info").mkdir(exist_ok=True)
    (site / ".git" / "info" / "attributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (site / "icons").mkdir()
    (site / "Logo.PNG").write_bytes(REAL_PNG)                  # case differs from the pattern
    (site / "icons" / "a.png").write_bytes(REAL_PNG[::-1])     # a negated line: an ordinary file
    (site / "model.bin").write_bytes(REAL_BIN)                 # a pattern only in .git/info/attributes
    (site / "notes.dat").write_bytes(pointer(REAL_DAT))        # a pointer with no attributes line
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "Files")
    assert run_git(site, "cat-file", "-s", "HEAD:icons/a.png").strip() == str(len(REAL_PNG))   # committed whole
    found = await inspect(client, site)
    assert found["lfs"]["files"] == 3, found["lfs"]
    assert found["lfs"]["names"] == ["Logo.PNG", "model.bin", "notes.dat"]
    assert found["lfs"]["not_here"] == ["notes.dat"]


async def test_a_repository_without_lfs_gives_no_note(client, studio, tmp_path):
    site = make_site(tmp_path)
    (site / ".gitattributes").write_text("*.psd filter=lfs diff=lfs merge=lfs -text\n")   # a rule, no file
    run_git(site, "add", "-A")
    run_git(site, "commit", "-qm", "attributes")
    found = await inspect(client, site)
    assert found["lfs"] is None and not any("LFS" in note for note in found["notes"])
    project = await attach(client, site)
    assert "lfs" not in project["attach"]


async def test_a_plain_folder_with_pointer_files_says_the_real_files_are_not_here(client, tmp_path):
    folder = make_site(tmp_path, "plain", git=False)
    (folder / "hero.png").write_bytes(pointer(REAL_PNG))
    found = await inspect(client, folder)
    assert found["lfs"] == {"files": 1, "names": ["hero.png"], "not_here": ["hero.png"], "not_here_count": 1}
    [note] = lfs_notes(found)
    assert "hero.png" in note and "not in this folder" in note and "`" not in note, note


async def test_the_copy_holds_the_pointer_files_as_the_note_said(client, studio, tmp_path, lfs_log):
    site = make_lfs_site(tmp_path, lfs_log)
    project = await attach(client, site)
    workspace = studio.store.workspace(project["id"])
    assert (workspace / "a.png").read_bytes() == pointer(REAL_PNG)
    assert not lfs_log.read_text().count(str(workspace.resolve()))


def test_finding_them_puts_no_path_on_a_command_line(tmp_path, lfs_log):
    site = make_lfs_site(tmp_path, lfs_log)
    calls = []

    def read(folder, args, stdin=None):
        calls.append(args)
        return attaching._git_read_bytes(folder, args, stdin)

    assert lfs.inspect(site, True, read)["files"] == 3
    assert calls == [["ls-tree", "-r", "-l", "-z", "HEAD"], ["cat-file", "--batch"]]
