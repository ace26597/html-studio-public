"""The studio's own git never runs a program a project names (CONTRACT.md, "Data layout").

The agent can write anything in a copy's `.git` and `.gitattributes`. Each test plants one way
git can be told to start a program, makes it create a marker file, then runs what the studio
runs (versions, restore, the changed files, a diff, a merge) and checks the marker is not there.
The first test also shows the same planting does run under plain git, so the others prove
something.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from studio2_server import workspace as ws


def marker_command(marker: Path) -> str:
    return f"sh -c 'touch {marker}; cat'"


def project(tmp_path: Path) -> Path:
    workspace = tmp_path / "project"
    ws.init_workspace(workspace, "Test")
    (workspace / "a.txt").write_text("one\n")
    ws.checkpoint(workspace, "user", "First")
    return workspace


def configure(workspace: Path, *pairs: tuple[str, str]) -> None:
    for key, value in pairs:
        subprocess.run(["git", "config", key, value], cwd=workspace, check=True)


def everything_the_studio_does(workspace: Path) -> None:
    (workspace / "a.txt").write_text("two\n")
    made = ws.checkpoint(workspace, "agent", "Second")
    ws.diff(workspace, made["sha"])
    ws.versions(workspace)
    ws.user_changes_since_agent(workspace)
    ws.git(workspace, "diff", "HEAD~1", "HEAD")        # a patch, as a caller of ws.git could ask for
    (workspace / "a.txt").write_text("three\n")
    ws.checkpoint(workspace, "user", "Third")
    ws.restore(workspace, made["sha"])
    # A merge whose both sides changed the same line, as "bring the copy up to date" can meet.
    ws.git(workspace, "checkout", "-q", "-b", "side", made["sha"])
    (workspace / "a.txt").write_text("side\n")
    ws.checkpoint(workspace, "user", "Side")
    ws.git(workspace, "checkout", "-q", "main")
    (workspace / "a.txt").write_text("main\n")
    ws.checkpoint(workspace, "user", "Main")
    ws.git(workspace, "merge", "--no-ff", "--no-edit", "-m", "Merge", "side", check=False)
    ws.git(workspace, "merge", "--abort", check=False)


DRIVERS = {
    "filter clean": ("* filter=x", [("filter.x.clean", "{cmd}")]),
    "filter smudge": ("* filter=x", [("filter.x.smudge", "{cmd}"), ("filter.x.clean", "cat")]),
    "filter process": ("* filter=x", [("filter.x.process", "{cmd}")]),
    "diff textconv": ("* diff=y", [("diff.y.textconv", "{cmd}")]),
    "diff command": ("* diff=y", [("diff.y.command", "{cmd}")]),
    "diff external": ("", [("diff.external", "{cmd}")]),
    "merge driver": ("* merge=z", [("merge.z.driver", "{cmd}")]),
    "merge default": ("", [("merge.default", "z"), ("merge.z.driver", "{cmd}")]),
}


@pytest.mark.parametrize("where", ["tree", "info"])
@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_no_driver_a_project_names_runs(tmp_path, name, where):
    workspace = project(tmp_path)
    marker = tmp_path / "ran"
    attributes, settings = DRIVERS[name]
    configure(workspace, *[(key, value.format(cmd=marker_command(marker))) for key, value in settings])
    if attributes:
        target = workspace / (".gitattributes" if where == "tree" else ".git/info/attributes")
        target.parent.mkdir(exist_ok=True)
        target.write_text(attributes + "\n")
    elif where == "info":
        pytest.skip("set in config only")
    everything_the_studio_does(workspace)
    assert not marker.exists(), name


def test_the_same_planting_runs_under_plain_git(tmp_path):
    workspace = project(tmp_path)
    marker = tmp_path / "ran"
    configure(workspace, ("filter.x.clean", marker_command(marker)))
    (workspace / ".gitattributes").write_text("* filter=x\n")
    (workspace / ".git" / "info" / "attributes").unlink()     # the agent can remove what the studio holds
    (workspace / "a.txt").write_text("two\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    assert marker.exists()
    marker.unlink()
    (workspace / "a.txt").write_text("three\n")
    ws.checkpoint(workspace, "user", "Held again")
    assert not marker.exists()


def test_a_link_in_place_of_info_attributes_is_replaced_not_written_through(tmp_path):
    workspace = project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    held = workspace / ".git" / "info" / "attributes"
    held.unlink(missing_ok=True)
    held.symlink_to(outside)
    ws.checkpoint(workspace, "user", "Anything")
    assert outside.read_text() == "keep me\n"
    assert not held.is_symlink() and "!filter" in held.read_text()


@pytest.mark.parametrize("hook", ["pre-commit", "commit-msg", "post-commit", "post-checkout", "post-merge",
                                  "pre-merge-commit", "reference-transaction", "post-index-change"])
def test_no_hook_of_the_project_runs(tmp_path, hook):
    workspace = project(tmp_path)
    marker = tmp_path / "ran"
    for folder in (workspace / ".git" / "hooks", tmp_path / "elsewhere"):
        folder.mkdir(exist_ok=True)
        script = folder / hook
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)
    configure(workspace, ("core.hooksPath", str(tmp_path / "elsewhere")))
    everything_the_studio_does(workspace)
    assert not marker.exists(), hook


@pytest.mark.parametrize("key", ["core.fsmonitor", "core.pager", "core.editor", "sequence.editor",
                                 "core.askPass", "credential.helper", "core.sshCommand", "gpg.program",
                                 "core.alternateRefsCommand"])
def test_settings_that_name_a_program_do_not_run(tmp_path, key):
    workspace = project(tmp_path)
    marker = tmp_path / "ran"
    configure(workspace, (key, marker_command(marker)), ("log.showSignature", "true"),
              ("merge.verifySignatures", "true"))
    everything_the_studio_does(workspace)
    assert not marker.exists(), key


def test_the_studios_git_gets_a_short_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("SOME_TOKEN", "secret")
    env = ws.git_env(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env and "SOME_TOKEN" not in env
    assert env["PATH"] == os.environ["PATH"] and env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_an_in_place_project_keeps_working_and_its_own_git_is_not_written(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    (folder / ".gitattributes").write_text("*.bin filter=lfs\n")
    (folder / "index.html").write_text("<h1>x</h1>")
    history = tmp_path / "history.git"
    subprocess.run(["git", "init", "-q", "--bare", str(history)], check=True)
    ws.register_history(folder, history)
    try:
        first = ws.checkpoint(folder, "init", "Start", allow_empty=True)
        (folder / "index.html").write_text("<h1>y</h1>")
        second = ws.checkpoint(folder, "agent", "Edit")
        assert [f["path"] for f in second["files"]] == ["index.html"]
        assert "<h1>y</h1>" in ws.diff(folder, second["sha"])["diff"]
        ws.restore(folder, first["sha"])
        assert (folder / "index.html").read_text() == "<h1>x</h1>"
    finally:
        ws.register_history(folder, None)
    assert "!filter" in (history / "info" / "attributes").read_text()
    assert not (folder / ".git" / "info" / "attributes").exists()
