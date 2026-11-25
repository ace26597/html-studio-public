#!/usr/bin/env python3
"""Print the counts the records used to write by hand, read from the code itself.

    cd studio2 && .venv/bin/python tools/counts.py                     (the main checkout)
    cd studio2 && PYTHONPATH=$PWD/server <main checkout>/studio2/.venv/bin/python tools/counts.py
                                                                       (a worktree or the public copy)

Counts: studio tools, studio skills, helper agents, examples, templates, and the server and interface
tests that are collected (collected only, never run). Nothing is written and no model is called.
In a worktree without its own `web/node_modules`, the interface tests are listed with the main
checkout's packages and this folder's files (vitest leaves its cache in this folder's ignored
`web/node_modules`). When something cannot be counted it says so instead of
printing a number.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

STUDIO2 = Path(__file__).resolve().parents[1]
LIBRARY = STUDIO2 / "library"


def folders(path: Path, marker: str | None = None) -> int:
    return sum(1 for p in path.iterdir() if p.is_dir() and (marker is None or (p / marker).is_file()))


def studio_tools() -> str:
    sys.path.insert(0, str(STUDIO2 / "server"))
    try:
        from studio2_server import tools
    except Exception as error:  # noqa: BLE001 - say why, do not guess a number
        return f"not counted ({error.__class__.__name__}: {error})"
    where = Path(tools.__file__).resolve()
    note = "" if STUDIO2 in where.parents else f" (from {where.parent}, not this folder: set PYTHONPATH)"
    return f"{len(tools.TOOLS)}{note}"


def server_tests() -> str:
    env = {**os.environ, "STUDIO2_FAKE": "1", "PYTHONPATH": str(STUDIO2 / "server")}
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
                         cwd=STUDIO2, env=env, capture_output=True, text=True)
    found = re.search(r"(\d+) tests? collected", run.stdout + run.stderr)
    if not found:
        return "not counted (pytest did not report a collection; is this the studio's Python?)"
    return found.group(1)


def main_checkout_web() -> Path | None:
    run = subprocess.run(["git", "-C", str(STUDIO2), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True)
    if run.returncode != 0:
        return None
    web = Path(run.stdout.strip()).parent / "studio2" / "web"
    return web if (web / "node_modules" / "vitest").is_dir() else None


def interface_tests() -> str:
    web = STUDIO2 / "web"
    npx = shutil.which("npx")
    if not npx:
        return "not counted (no npx on PATH)"
    command = [npx, "vitest", "list", "--json"]
    cwd = web
    if not (web / "node_modules" / "vitest").is_dir():
        other = main_checkout_web()
        if other is None:
            return "not counted (no web/node_modules here or in the main checkout)"
        command += ["--root", str(web), "--config", str(other / "vite.config.ts")]
        cwd = other
    run = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    try:
        listed = json.loads(run.stdout)
    except json.JSONDecodeError:
        return f"not counted (vitest list failed: {(run.stderr or run.stdout).strip()[-200:]})"
    return str(len(listed))


def main() -> int:
    rows = [
        ("studio tools", studio_tools()),
        ("studio skills", str(folders(LIBRARY / "skills", "SKILL.md"))),
        ("helper agents", str(sum(1 for _ in (LIBRARY / "agents").glob("*.md")))),
        ("examples", str(folders(LIBRARY / "examples"))),
        ("templates", str(folders(LIBRARY / "templates"))),
        ("server tests (collected)", server_tests()),
        ("interface tests (collected)", interface_tests()),
    ]
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name.ljust(width)}  {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
