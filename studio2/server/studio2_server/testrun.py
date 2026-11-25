"""Tests for a project's app: which command, how it runs, what is kept (CONTRACT.md, "Tests for a
project's app: `run_tests`").

The command runs in the project folder with the clean environment a project's app gets, a fresh
STUDIO_TEST_STATE folder outside the project, a time limit and its own process group, which is
ended when the run is over. It never starts or touches the running app.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .apps import clean_env, lasted

DEFAULT_TIMEOUT, MAX_TIMEOUT = 300, 900
TAIL, KEEP = 6_000, 200_000             # characters of output in the answer, and kept while running
FAILURES, MESSAGE = 20, 300
PYTEST_FLAGS = "-q -p no:cacheprovider"
NPM_PLACEHOLDER = "no test specified"
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".json", ".toml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "state", "data", "sources", "checks",
             "attachments", ".pytest_cache", "dist", "build", ".next", ".nuxt"}
FINGERPRINT_CAP = 20_000
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class TestsError(Exception):
    pass


def _json_file(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _has_pytest_tests(workspace: Path) -> bool:
    if (workspace / "tests").is_dir():
        return True
    return any(workspace.glob("test_*.py")) or any(workspace.glob("*_test.py"))


def _npm_script(workspace: Path) -> str | None:
    package = _json_file(workspace / "package.json")
    script = ((package or {}).get("scripts") or {}).get("test") if isinstance(package, dict) else None
    if isinstance(script, str) and script.strip() and NPM_PLACEHOLDER not in script:
        return script
    return None


def find_command(workspace: Path, project: dict) -> dict:
    """→ {"command", "kind": studio_json | recipe | pytest | npm, "note"?} or {"command": None, "next"}."""
    config = _json_file(workspace / "studio.json") if not (project.get("attach") or {}).get("mode") == "in_place" else None
    declared = (config or {}).get("test") if isinstance(config, dict) else None
    if isinstance(declared, str) and declared.strip():
        return {"command": declared.strip(), "kind": "studio_json"}
    if project.get("attach"):
        checks = (((project.get("ship") or {}).get("recipe") or {}).get("checks")) or []
        checks = [c for c in checks if isinstance(c, str) and c.strip()]
        if checks:
            return {"command": " && ".join(checks), "kind": "recipe"}
    if _has_pytest_tests(workspace):
        if (workspace / ".venv" / "bin" / "pytest").is_file():
            return {"command": f".venv/bin/pytest {PYTEST_FLAGS}", "kind": "pytest"}
        requirements = next((name for name in ("requirements.lock", "requirements.txt")
                             if (workspace / name).is_file()), None)
        more = f" --with-requirements {requirements}" if requirements else ""
        return {"command": f"uv run --with pytest --with httpx{more} pytest {PYTEST_FLAGS}", "kind": "pytest"}
    if _npm_script(workspace):
        if not (workspace / "node_modules").is_dir():
            return {"command": None, "kind": "npm", "found": True,
                    "next": "package.json has a test script, but node_modules is missing: install the packages "
                            "first (npm install), then call run_tests again."}
        return {"command": "npm test", "kind": "npm"}
    return {"command": None, "found": False,
            "next": "This project has no tests. Create tests/test_app.py (pytest; FastAPI's TestClient for each route) "
                    "or add \"test\" to studio.json; the app-tests skill says how. Then call run_tests again."}


def narrow(found: dict, workspace: Path, path: str | None, filter_: str | None) -> str:
    """The command with `path` and `filter` added (CONTRACT.md: only the studio's own pytest and npm
    commands take a filter)."""
    command, kind = found["command"], found["kind"]
    if path:
        from .workspace import safe_path
        try:
            target = safe_path(workspace, path)
        except Exception:
            raise TestsError("path must be a file or folder inside the project.") from None
        if not target.exists():
            raise TestsError(f"There is no {path} in the project.")
        relative = target.relative_to(workspace.resolve()).as_posix()
        command += (" --" if kind == "npm" else "") + " " + shlex.quote(relative)
    if filter_:
        if kind == "pytest":
            command += " -k " + shlex.quote(filter_)
        elif kind == "npm":
            command += (" " if path else " -- ") + "-t " + shlex.quote(filter_)
        else:
            raise TestsError("filter works with the studio's own pytest or npm command; this project's test command "
                             "comes from " + ("studio.json" if kind == "studio_json" else "its ship recipe")
                             + ". Pass path, or change the command.")
    return command


def fingerprint(workspace: Path) -> list[int] | None:
    """[count, newest change in ns] of the project's code files, or None past FINGERPRINT_CAP files."""
    count, newest, seen = 0, 0, 0
    stack = [Path(workspace)]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > FINGERPRINT_CAP:
                return None
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS and not (folder == workspace and entry.name.startswith(".")):
                        stack.append(Path(entry.path))
                    continue
                suffix = os.path.splitext(entry.name)[1].lower()
                if suffix in CODE_SUFFIXES or entry.name.startswith("requirements"):
                    count += 1
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime_ns)
            except OSError:
                continue
    return [count, newest]


# -- reading the output ------------------------------------------------------------------------
def _counts_pytest(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        found = dict((word, int(n)) for n, word in re.findall(
            r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b", line))
        if found and re.search(r"\bin [\d.]+s\b", line):
            return {"passed": found.get("passed", 0) + found.get("xpassed", 0),
                    "failed": found.get("failed", 0), "errors": found.get("error", 0) + found.get("errors", 0),
                    "skipped": found.get("skipped", 0) + found.get("xfailed", 0) + found.get("deselected", 0)}
        if re.search(r"\bno tests ran\b", line):
            return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    return None


def _counts_js(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if re.match(r"\s*Tests:?\s", line):            # vitest "Tests  2 failed | 10 passed (12)", jest "Tests: 1 failed, 3 passed, 4 total"
            found = dict((word, int(n)) for n, word in re.findall(r"(\d+) (passed|failed|skipped|todo)\b", line))
            if found:
                return {"passed": found.get("passed", 0), "failed": found.get("failed", 0), "errors": 0,
                        "skipped": found.get("skipped", 0) + found.get("todo", 0)}
    node = dict((word, int(n)) for word, n in re.findall(r"^\s*(?:#|ℹ)\s*(pass|fail|skipped|todo)\s+(\d+)\s*$",
                                                          text, flags=re.M))
    if "pass" in node or "fail" in node:
        return {"passed": node.get("pass", 0), "failed": node.get("fail", 0), "errors": 0,
                "skipped": node.get("skipped", 0) + node.get("todo", 0)}
    return None


def counts_of(text: str) -> dict | None:
    return _counts_pytest(text) or _counts_js(text)


def failures_of(text: str) -> list[dict]:
    out: list[dict] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        pytest = re.match(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", line)
        js = re.match(r"^\s*(?:FAIL|×|✕|✗)\s+(.+?)\s*(?:\d+ms)?$", line) or re.match(r"^\s*●\s+(.+)$", line)
        if pytest:
            name, message = pytest.group(2), pytest.group(3) or pytest.group(1).lower()
        elif js and not re.match(r"^\s*FAIL\s+\d", line):
            name = js.group(1).strip()
            message = next((rest.strip() for rest in lines[index + 1:index + 6] if rest.strip()), "")
        else:
            continue
        if any(item["name"] == name for item in out):
            continue
        out.append({"name": name[:200], "message": message[:MESSAGE]})
        if len(out) >= FAILURES:
            break
    return out


# -- running -----------------------------------------------------------------------------------
def environment(project_id: str, state: Path) -> dict:
    env = clean_env()
    env.update({"CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "STUDIO_TEST_STATE": str(state),
                "STUDIO_PROJECT_ID": project_id})
    return env


def _end_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def run(workspace: Path, project_id: str, command: str, timeout: int) -> dict:
    """Run the command → the answer (without `next`)."""
    state = Path(tempfile.mkdtemp(prefix="studio-test-state-"))
    chunks: list[str] = []
    size = 0
    started, timed_out = time.monotonic(), False
    try:
        process = await asyncio.create_subprocess_shell(
            command, cwd=workspace, env=environment(project_id, state), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)

        async def read():
            nonlocal size
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    return
                chunks.append(data.decode("utf-8", "replace"))
                size += len(chunks[-1])
                while size > KEEP and len(chunks) > 1:
                    size -= len(chunks.pop(0))

        reader = asyncio.create_task(read())
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _end_group(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                pass
        _end_group(process.pid, signal.SIGKILL)       # whatever the tests left running
        try:
            await asyncio.wait_for(reader, 3)
        except asyncio.TimeoutError:
            reader.cancel()
        if process.returncode is None:
            await process.wait()
        code = process.returncode
    except OSError as exc:
        raise TestsError(f"The test command could not start: {exc}") from None
    finally:
        shutil.rmtree(state, ignore_errors=True)
    text = ANSI.sub("", "".join(chunks))
    return {"command": command, "passed": code == 0 and not timed_out, "exit_code": code,
            "counts": counts_of(text), "failures": failures_of(text), "output_tail": text[-TAIL:],
            "seconds": round(time.monotonic() - started, 1), "timed_out": timed_out}


def next_step(result: dict, timeout: int) -> str:
    if result["timed_out"]:
        return (f"The tests ran past the {timeout}-second limit and were stopped. Keep the whole run under about "
                "30 seconds (no real network, no sleeps, small data); pass timeout only if it must be longer.")
    counts = result.get("counts") or {}
    if result["passed"]:
        if counts and not counts.get("passed"):
            return "The command passed but ran no tests. Check that the test files are found (tests/test_*.py)."
        return "All tests pass. Say so in your reply, with the count."
    return ("Fix the code (or the test, when the test itself is wrong) and call run_tests again. Never delete or "
            "skip a test to make the run pass. If something still fails, say plainly what and why.")


def record(result: dict, workspace: Path) -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": result["command"],
            "passed": result["passed"], "counts": result.get("counts"), "seconds": result["seconds"],
            "timed_out": result["timed_out"], "fingerprint": fingerprint(workspace)}


def facts_line(project: dict, workspace: Path, now: float | None = None) -> str | None:
    """The line in the current project facts: the last result, or that tests exist and never ran."""
    last = project.get("tests_last")
    if not isinstance(last, dict):
        found = find_command(workspace, project)
        if found.get("command"):
            return f"- Tests: this project has tests (`{found['command']}`), not run here yet. Run run_tests after changing the app's code."
        return None
    counts = last.get("counts") or {}
    said = ", ".join(f"{counts[key]} {word}" for key, word in (("passed", "passed"), ("failed", "failed"),
                                                                 ("errors", "errors"), ("skipped", "skipped"))
                     if counts.get(key)) if counts else ""
    if last.get("timed_out"):
        said = "stopped at the time limit" + (f" ({said})" if said else "")
    elif not said:
        said = "passed" if last.get("passed") else "failed"
    elif not last.get("passed") and not (counts.get("failed") or counts.get("errors")):
        said += ", but the command failed"
    try:
        age = (now or time.time()) - datetime.fromisoformat(str(last["at"])).timestamp()
        ago = f", {lasted(max(age, 0))} ago" if age >= 60 else ", just now"
    except (KeyError, ValueError, TypeError):
        ago = ""
    line = f"- Tests (`{last.get('command')}`): {said}{ago}"
    if last.get("fingerprint") is not None:
        now_print = fingerprint(workspace)
        if now_print is not None and now_print != last["fingerprint"]:
            line += "; the app's files changed since"
    return line + "."
