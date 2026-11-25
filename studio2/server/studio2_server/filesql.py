"""DuckDB over a project's files, in a process of its own, with limits (CONTRACT.md, "`query_files`
on big files, and what its SQL may read").

The worker (filesql_worker.py) can open only the project folder and the call's temporary folder,
has a memory, thread and spill limit, and is ended with its whole process group at the time limit,
so a runaway query never holds a server thread or reads anything outside the project.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).with_name("filesql_worker.py")
DEFAULT_TIMEOUT, MAX_TIMEOUT = 60, 300
DEFAULT_MEMORY, DEFAULT_THREADS, DEFAULT_SPILL = "2GB", 4, "10GB"
FULL_SHARE, SAMPLE_ROWS = 0.6, 1_000_000   # a profile reads every row when that fits in 60% of the time
BIG_FILE = 100 * 1024 * 1024     # over this a CSV or JSON file should be converted to Parquet once
PROFILED = (".csv", ".tsv", ".txt", ".parquet", ".json", ".jsonl", ".ndjson")
SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]i?B|B)?\s*$", re.I)
NEXT = ("Filter rows (where), select fewer columns, aggregate (group by) instead of returning rows, "
        "profile the file first ({profile: path}), or convert a big CSV to Parquet once "
        "(save_as: 'data/<name>.parquet') and query that.")


# Where each call's temporary folder goes: the studio sets it to <data folder>/run/query when it
# starts (Studio.__init__); outside a studio, the system's temporary folder.
_SCRATCH: list[Path] = []


def set_scratch_root(folder: Path) -> None:
    _SCRATCH[:] = [Path(folder)]


def scratch_root() -> Path:
    import tempfile
    return _SCRATCH[0] if _SCRATCH else Path(tempfile.gettempdir()) / "studio2-query"


class QueryError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _bytes(text: str) -> int | None:
    match = SIZE.match(str(text or ""))
    if not match:
        return None
    unit = (match.group(2) or "B").upper().replace("I", "")
    return int(float(match.group(1)) * 1024 ** "BKMGT".index(unit[0]))


def human(count: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


def limits(scratch_root: Path) -> dict:
    """The limits of one call, from the environment (CONTRACT.md, the table of limits)."""
    memory = os.environ.get("STUDIO2_QUERY_MEMORY") or DEFAULT_MEMORY
    if _bytes(memory) is None:
        memory = DEFAULT_MEMORY
    try:
        threads = max(1, min(int(os.environ.get("STUDIO2_QUERY_THREADS") or DEFAULT_THREADS), 64))
    except ValueError:
        threads = DEFAULT_THREADS
    spill = _bytes(os.environ.get("STUDIO2_QUERY_SPILL") or DEFAULT_SPILL) or _bytes(DEFAULT_SPILL)
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
        spill = min(spill, shutil.disk_usage(scratch_root).free // 2)
    except OSError:
        pass
    return {"memory": memory.replace(" ", ""), "threads": threads, "spill": f"{max(spill // (1024 * 1024), 1)}MB",
            "spill_bytes": spill}


def timeout_of(value) -> int:
    try:
        seconds = int(value) if value not in (None, "") else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        raise QueryError("timeout must be a whole number of seconds (at most 300).") from None
    return max(1, min(seconds, MAX_TIMEOUT))


def _environment() -> dict:
    """The worker gets nothing of the studio's environment beyond these: no key reaches it."""
    keep = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TZ") if name in os.environ}
    keep.update({name: value for name, value in os.environ.items() if name.startswith("LC_")})
    return keep


def _message(answer: dict, limit: dict, seconds: int) -> str:
    kind, text = answer.get("kind"), answer.get("error") or "The query failed."
    if kind == "memory":
        return (f"The query needed more than the {limit['memory']} memory limit, even after spilling to disk. "
                + NEXT)
    if kind == "spill":
        return (f"The query needed more than the {human(limit['spill_bytes'])} of disk the studio allows it for "
                "spilling. " + NEXT)
    if kind == "outside":
        return (f"{text} Only files inside the project folder can be read here, and nothing from the internet "
                "(use fetch_source or a script for that).")
    return text


def run(workspace: Path, parts: list[dict], scratch_root: Path, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Run each part ({sql, limit?} | {profile} | {sql, save_parquet: target path}) in one worker →
    one answer per part: the worker's own, or {"error": message} with the plain message for a limit
    reached. A Parquet result is written in the temporary folder and moved to its target (a path the
    caller already checked is inside the project) only when it is whole."""
    workspace = Path(workspace).resolve()
    scratch_root = Path(scratch_root)
    limit = limits(scratch_root)
    scratch = scratch_root / secrets.token_hex(8)
    scratch.mkdir(parents=True, exist_ok=False)
    staged = {index: scratch / f"result-{index}.parquet" for index, part in enumerate(parts) if part.get("save_parquet")}
    job = {"parts": [{key: value for key, value in part.items() if key != "save_parquet"}
                     | ({"parquet_to": str(staged[index])} if index in staged else {})
                     for index, part in enumerate(parts)],
           "scratch": str(scratch), "memory": limit["memory"], "threads": limit["threads"], "spill": limit["spill"],
           "timeout": timeout, "full_share": FULL_SHARE, "sample_rows": SAMPLE_ROWS}
    try:
        process = subprocess.Popen([sys.executable, "-I", str(WORKER)], cwd=workspace, env=_environment(),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            out, err = process.communicate(json.dumps(job).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            _end(process)
            raise QueryError(
                f"The query ran past the {timeout}-second limit and was stopped. Pass timeout (up to "
                f"{MAX_TIMEOUT}) only if it really needs longer. " + NEXT) from None
        try:
            done = json.loads(out.decode("utf-8", "replace") or "null")
        except ValueError:
            done = None
        if not isinstance(done, dict):
            tail = " ".join(err.decode("utf-8", "replace").split())[-300:]
            raise QueryError(f"The query process stopped without an answer (exit code {process.returncode}"
                             + (f": {tail}" if tail else "") + "). It may have run out of memory. " + NEXT)
        if done.get("fatal"):
            raise QueryError(f"The query could not start: {done['fatal']}")
        answers = done.get("answers") or []
        for index, answer in enumerate(answers):
            if "error" in answer:
                answer["error"] = _message(answer, limit, timeout)
            elif index in staged:
                target = Path(parts[index]["save_parquet"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged[index]), str(target))
        return answers
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _end(process: subprocess.Popen) -> None:
    """End the worker and anything it started: its whole process group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            process.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            continue
