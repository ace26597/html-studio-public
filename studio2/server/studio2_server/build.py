"""Which code this studio's server is running, and whether newer code is on disk
(CONTRACT.md, "Which code a studio is running").

The interface is files served as they are; the server keeps the code it started with. This lets
a studio say so when the two have drifted apart, instead of quietly hiding what it does not know.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from .config import REPO_ROOT, STUDIO2_ROOT, now_iso

SERVER = STUDIO2_ROOT / "server"
KEEP = 10.0     # seconds an answer about the code on disk is kept


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def last_commit() -> tuple[str | None, str | None]:
    """→ (short hash, ISO date) of the last commit that changed the server's code, or (None, None)
    outside a git checkout."""
    found = _git("log", "-1", "--format=%h %cI", "--", "studio2/server")
    if not found or " " not in found:
        return None, None
    commit, when = found.split(" ", 1)
    return commit, when


def fingerprint(root: Path = SERVER) -> str:
    """A short fingerprint of the server's own source files, for a copy that is not a git checkout."""
    digest = hashlib.sha1()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


class Build:
    """Noted once when the server starts; asked again whenever health is read."""

    def __init__(self, commit=last_commit, source=fingerprint):
        self._commit, self._source = commit, source
        self.commit, self.committed_at = commit()
        self.code = None if self.commit else source()
        self.started_at = now_iso()
        self._seen: tuple[float, bool, str | None] | None = None

    def _now(self) -> tuple[bool, str | None]:
        if self.commit:     # a checkout: only a later commit counts, never work in progress
            current, _when = self._commit()
            return (bool(current) and current != self.commit), (current if current != self.commit else None)
        return self._source() != self.code, None

    def view(self) -> dict:
        if self._seen is None or time.monotonic() - self._seen[0] > KEEP:
            try:
                stale, newer = self._now()
            except Exception:  # noqa: BLE001 - health must answer whatever git or the disk does
                stale, newer = False, None
            self._seen = (time.monotonic(), stale, newer)
        _at, stale, newer = self._seen
        return {"commit": self.commit, "committed_at": self.committed_at, "started_at": self.started_at,
                "stale": stale, "newer": newer}
