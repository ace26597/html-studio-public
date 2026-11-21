"""Which code a studio is running (CONTRACT.md, "Which code a studio is running")."""

from __future__ import annotations

from studio2_server import build
from studio2_server.build import Build


def test_a_checkout_is_stale_only_when_a_later_commit_changed_the_server():
    now = {"commit": ("abc1234", "2026-11-18T18:00:00-05:00")}
    stamp = Build(commit=lambda: now["commit"], source=lambda: "unused")
    assert (stamp.commit, stamp.committed_at, stamp.code) == ("abc1234", "2026-11-18T18:00:00-05:00", None)
    first = stamp.view()
    assert first["stale"] is False and first["newer"] is None and first["commit"] == "abc1234" and first["started_at"]
    now["commit"] = ("def5678", "2026-11-20T22:00:00-05:00")       # three days of work land on disk
    assert stamp.view()["stale"] is False                           # the answer is kept for a few seconds
    stamp._seen = None
    assert stamp.view() == {**first, "stale": True, "newer": "def5678"}
    assert stamp.commit == "abc1234"                                # what it runs does not change


def test_a_copy_that_is_no_checkout_goes_by_its_source_files(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "a.py").write_text("ignored\n")
    before = build.fingerprint(tmp_path)
    stamp = Build(commit=lambda: (None, None), source=lambda: build.fingerprint(tmp_path))
    assert stamp.commit is None and stamp.code == before and stamp.view()["stale"] is False
    (tmp_path / "pkg" / "__pycache__" / "a.py").write_text("still ignored\n")
    stamp._seen = None
    assert stamp.view()["stale"] is False
    (tmp_path / "pkg" / "a.py").write_text("x = 2\n")
    stamp._seen = None
    assert stamp.view() == {"commit": None, "committed_at": None, "started_at": stamp.started_at, "stale": True, "newer": None}


def test_health_never_fails_on_it():
    def broken():
        raise OSError("git went away")
    calls = iter([("abc1234", "2026-11-18T18:00:00-05:00")])
    stamp = Build(commit=lambda: next(calls, None) or broken(), source=lambda: "x")
    assert stamp.view()["stale"] is False


async def test_health_says_which_code_is_running(client):
    told = (await client.get("/api/health")).json()["build"]
    assert set(told) == {"commit", "committed_at", "started_at", "stale", "newer"}
    assert told["stale"] is False and told["started_at"]
