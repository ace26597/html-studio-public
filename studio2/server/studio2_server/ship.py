"""Ship: make an attached app's change live (CONTRACT.md, "Ship: make it live").

A project brought in by its folder carries a ship recipe in its record. Shipping is done by
the studio, never by the agent: checks run in the copy, the person sees a card with exactly
what will be written where and allows it (every time, whatever the project's approval
setting), the change reaches the original, the recipe's after-commands run there, the address
is checked, and when a step after the change fails the studio goes back by itself.

The only functions here that write to the person's original folder are `_apply` and
`_go_back`, and `_apply` is reached only from `Shipper._run` after an Allow on the card.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from . import keyfiles, workspace as ws
from .apps import APP_ENV
from .attach import NODE_HOMES, _git_read
from .config import now_iso
from .store import ProjectNotFound, write_json_atomic

if TYPE_CHECKING:
    from .studio import Studio

log = logging.getLogger("studio2")

METHODS = {
    "branch": "Leave a branch in the original's repository",
    "merge": "Merge into the original's checked-out branch (fast-forward only)",
    "files": "Copy the changed files into the original folder",
    "none": "Nothing to move: the folder is edited in place",
}
MAX_COMMANDS, MAX_COMMAND_CHARS, MAX_GLOBS = 10, 2_000, 40
DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT = 600, 10, 3_600
OUTPUT_KEEP, EVENT_OUTPUT = 8_000, 4_000
FILE_LIST_CAP = 500
ADDRESS_TRIES, ADDRESS_PAUSE, ADDRESS_TIMEOUT = 6, 5.0, 10.0   # tests make these shorter
NEVER_PUSHES = "The studio itself never pushes to GitHub or any remote."
UNSTAGED = "Kept, but no longer staged (stage them again if you want): "


def limits_for(method: str, recipe: dict) -> list[str]:
    """The limits a card states: only those that hold for its method and recipe."""
    if method == "branch":
        return ["The branch stays in the original's own repository until you merge or delete it. " + NEVER_PUSHES]
    lines = []
    if method in ("merge", "files"):
        lines.append(f"Going back restores the {'branch' if method == 'merge' else 'files'} only: data and "
                     "database changes made by the app or by a command are not undone.")
    if recipe.get("after"):
        lines.append("Where the change ends up is whatever the after-commands do (a restart on this machine, a "
                     "push that a build service picks up, a copy to a server). " + NEVER_PUSHES)
    else:
        lines.append("There are no after-commands, so nothing is restarted or sent anywhere. " + NEVER_PUSHES)
    return lines


def _count(number: int, word: str) -> str:
    """1 file, 2 files."""
    return f"{number} {word}{'' if number == 1 else 's'}"


class ShipError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ShipRunning(Exception):
    pass


# -- the recipe ----------------------------------------------------------------------------
def methods_for(attach: dict | None) -> list[str]:
    if not attach:
        return []
    if attach.get("mode") == "in_place":
        return ["none"]
    return ["branch", "merge"] if attach.get("kind") == "git" else ["files"]


def default_recipe(attach: dict) -> dict:
    return {"checks": [], "method": methods_for(attach)[0], "after": [], "address": None,
            "protected": [".env", ".env.*"], "back": None, "timeout": DEFAULT_TIMEOUT, "notes": ""}


def _commands(value, what: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [line for line in value.splitlines()]
    if not isinstance(value, list):
        raise ShipError("invalid_recipe", f"{what} must be a list of commands.", 400)
    items = [str(item).strip() for item in value if str(item or "").strip()]
    if len(items) > MAX_COMMANDS:
        raise ShipError("invalid_recipe", f"At most {MAX_COMMANDS} {what.lower()}.", 400)
    for item in items:
        if len(item) > MAX_COMMAND_CHARS or "\x00" in item:
            raise ShipError("invalid_recipe", f"One of the {what.lower()} is too long.", 400)
    return items


def clean_recipe(raw: dict, attach: dict) -> dict:
    """A recipe as stored, or ShipError(400)."""
    if not isinstance(raw, dict):
        raise ShipError("invalid_recipe", "The recipe must be an object.", 400)
    allowed = methods_for(attach)
    method = str(raw.get("method") or allowed[0])
    if method not in allowed:
        names = " or ".join(allowed)
        raise ShipError("invalid_recipe", f"This project can ship by {names} only "
                        f"({'edited in place' if attach.get('mode') == 'in_place' else attach.get('kind')}).", 400)
    address = raw.get("address")
    if address not in (None, ""):
        address = str(address).strip()
        if not re.match(r"^https?://[^\s/]+", address) or len(address) > 500:
            raise ShipError("invalid_recipe", "The address to check must start with http:// or https://.", 400)
    else:
        address = None
    globs = raw.get("protected") or []
    if isinstance(globs, str):
        globs = [part for part in re.split(r"[\n,]", globs)]
    if not isinstance(globs, list):
        raise ShipError("invalid_recipe", "Protected paths must be a list of patterns.", 400)
    globs = [str(item).strip() for item in globs if str(item or "").strip()][:MAX_GLOBS]
    back = raw.get("back")
    back = None if back is None or back == "" else _commands(back, "Go-back commands")
    try:
        timeout = int(raw.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        raise ShipError("invalid_recipe", "timeout is a number of seconds.", 400) from None
    return {"checks": _commands(raw.get("checks"), "Checks"), "method": method,
            "after": _commands(raw.get("after"), "After-commands"), "address": address, "protected": globs,
            "back": back or None, "timeout": max(MIN_TIMEOUT, min(timeout, MAX_TIMEOUT)),
            "notes": str(raw.get("notes") or "").strip()[:2_000]}


def describe_recipe(recipe: dict, attach: dict) -> str:
    """The whole recipe as plain text, for the card that confirms it."""
    where = attach.get("path")
    lines = [f"How the change reaches {where}: {METHODS[recipe['method']]}."]
    lines.append("Checks (run in the copy before every ship): "
                 + ("; ".join(recipe["checks"]) if recipe["checks"] else "none"))
    lines.append(f"After-commands (run in {where}): " + ("; ".join(recipe["after"]) if recipe["after"] else "none"))
    lines.append(f"Address to check afterwards: {recipe['address'] or 'none'}")
    lines.append("Paths that need the owner: " + (", ".join(recipe["protected"]) if recipe["protected"] else "none"))
    lines.append("Going back: " + ("; ".join(recipe["back"]) if recipe["back"] else
                                   "the studio puts the original back and runs the after-commands again"))
    lines.append(f"Time limit per command: {recipe['timeout']} s")
    if recipe["notes"]:
        lines.append(f"Notes: {recipe['notes']}")
    return "\n".join(lines)


def protected_hits(paths: list[str], globs: list[str]) -> list[str]:
    """Changed paths that match a protected pattern. A pattern without "/" also matches a file's
    name in any folder; one ending in "/" matches everything under that folder."""
    hits = []
    for path in paths:
        name = PurePosixPath(path).name
        for pattern in globs:
            folder = pattern.rstrip("/")
            if (fnmatch.fnmatchcase(path, pattern) or ("/" not in pattern and fnmatch.fnmatchcase(name, pattern))
                    or (pattern.endswith("/") and (path == folder or path.startswith(folder + "/")))):
                hits.append(path)
                break
    return hits


# -- running commands ----------------------------------------------------------------------
def command_env() -> dict:
    """What a check or an after-command gets: the same short list an app gets, no studio secrets."""
    env = {name: value for name, value in os.environ.items() if name in APP_ENV}
    extra = [str(Path(home).expanduser()) for home in NODE_HOMES if Path(home).expanduser().is_dir()]
    path = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    env["PATH"] = ":".join([path, *[item for item in extra if item not in path.split(":")]])
    return env


async def run_command(command: str, cwd: Path, timeout: float, log_file: Path) -> dict:
    """Run one command with sh in its own process group; output goes to a file (a server the
    command leaves running in the background then cannot hold the studio waiting on a pipe)."""
    started = time.monotonic()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    with log_file.open("wb") as out:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", command, cwd=str(cwd), env=command_env(), stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            return {"command": command, "exit_code": None, "output": f"Could not start: {exc}",
                    "duration_ms": 0, "timed_out": False}
        try:
            code = await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            code = await proc.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            raise
    raw = log_file.read_bytes()
    text = raw[-OUTPUT_KEEP:].decode("utf-8", "replace")
    if len(raw) > OUTPUT_KEEP:
        text = "… (earlier output cut)\n" + text
    if timed_out:
        text += f"\n(stopped after {int(timeout)} s: the recipe's time limit)"
    return {"command": command, "exit_code": None if timed_out else code, "output": text,
            "duration_ms": int((time.monotonic() - started) * 1000), "timed_out": timed_out}


def _ok(result: dict) -> bool:
    return result.get("exit_code") == 0


async def check_address(address: str, tries: int | None = None, pause: float | None = None) -> dict:
    import httpx
    tries, pause = tries or ADDRESS_TRIES, ADDRESS_PAUSE if pause is None else pause
    attempts = []
    async with httpx.AsyncClient(timeout=ADDRESS_TIMEOUT, follow_redirects=True, trust_env=False) as client:
        for attempt in range(1, tries + 1):
            try:
                response = await client.get(address)
                attempts.append(f"try {attempt}: HTTP {response.status_code}")
                if response.status_code < 400:
                    return {"ok": True, "address": address, "status": response.status_code, "attempts": attempts}
            except Exception as exc:  # noqa: BLE001 - every failure is a failed try
                attempts.append(f"try {attempt}: {exc.__class__.__name__}: {str(exc)[:200]}")
            if attempt < tries:
                await asyncio.sleep(pause)
    return {"ok": False, "address": address, "status": None, "attempts": attempts}


# -- git on the original -------------------------------------------------------------------
def _git_write(folder: Path, *args: str) -> tuple[int, str]:
    """A git command that writes to the person's repository. Only `_apply` and `_go_back` call it.
    The person's own hooks do not run (after-commands are the place for that)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.quotepath=false", *args],
                          cwd=folder, env=env, capture_output=True, timeout=600)
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()


def _read(folder: Path, *args: str) -> str:
    code, out = _git_read(folder, *args)
    return out.strip() if code == 0 else ""


def original_state(folder: Path) -> dict:
    """What the merge way needs to know about the original, read without writing."""
    head = _read(folder, "rev-parse", "-q", "--verify", "HEAD")
    branch = _read(folder, "symbolic-ref", "-q", "--short", "HEAD")
    _code, status = _git_read(folder, "status", "--porcelain=v1", "--untracked-files=no")
    return {"head": head or None, "branch": branch or None,
            "changed": [line[3:] for line in status.splitlines() if len(line) > 3]}


def _ref(folder: Path, branch: str) -> str | None:
    return _read(folder, "rev-parse", "-q", "--verify", f"refs/heads/{branch}") or None


def _changes(workspace: Path, base: str | None, head: str) -> list[dict]:
    if base == head:
        return []
    if base:
        text = ws.git(workspace, "diff", "--name-status", "--no-renames", base, head)
    else:
        text = ws.git(workspace, "diff-tree", "--no-commit-id", "--name-status", "--no-renames", "-r", "--root", head)
    return ws._name_status(text)


def _has_commit(workspace: Path, sha: str | None) -> bool:
    return bool(sha) and ws.git_ok(workspace, "cat-file", "-e", f"{sha}^{{commit}}")


def _blob(workspace: Path, commit: str, path: str) -> bytes | None:
    # Raw bytes (no --filters or --textconv), the studio's own settings, and its short environment.
    proc = subprocess.run([*ws.GIT_BASE, "cat-file", "blob", f"{commit}:{path}"], cwd=workspace,
                          env=ws.git_env(workspace), capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def _mode(workspace: Path, commit: str, path: str) -> str:
    out = ws.git(workspace, "ls-tree", commit, "--", path, check=False).split()
    return out[0] if out else "100644"


def _target(folder: Path, rel: str) -> Path:
    """A path inside the original folder; never through a symlink, never outside it."""
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." or part.lower() == ".git" for part in parts):
        raise ShipError("unsafe_path", f"The path {rel} cannot be shipped.")
    root = folder.resolve()
    target = root.joinpath(*parts)
    probe = root
    for part in parts:
        probe = probe / part
        if probe.is_symlink():
            raise ShipError("unsafe_path", f"{rel} goes through a link in the original; the studio does not "
                            "write through links.")
    if root not in target.resolve().parents:
        raise ShipError("unsafe_path", f"The path {rel} is outside the original folder.")
    return target


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


# -- the plan: what a ship would do now ----------------------------------------------------
def plan(project: dict, workspace: Path) -> dict:
    """Read-only. → {method, base, head, files, writes, commands, refusal, next, before}.
    `refusal` is a plain sentence when shipping is not possible now; `next` the step that helps."""
    attach, recipe = project["attach"], project["ship"]["recipe"]
    method, folder = recipe["method"], Path(attach["path"])
    head = ws.head(workspace)
    state = project.get("ship") or {}
    result = {"method": method, "head": head, "base": None, "files": [], "writes": [], "refusal": None,
              "next": None, "before": {}, "where": str(folder)}
    if not folder.is_dir():
        result["refusal"] = f"The original folder {folder} is not there any more."
        return result
    if method in ("branch", "merge"):
        branch = attach["studio_branch"]
        if _read(folder, "rev-parse", "--show-toplevel") == "":
            result["refusal"] = f"{folder} is not a git folder any more."
            return result
        ref = _ref(folder, branch)
        result["before"]["ref"] = ref
        if method == "branch":
            base = ref if ref and _has_commit(workspace, ref) else attach.get("started_from")
            if ref and not _has_commit(workspace, ref):
                result["refusal"] = (f"The branch {branch} in the original has commits the copy does not have "
                                     "(someone committed on it there). The studio will not overwrite them.")
                result["next"] = f"Rename or delete the branch {branch} in the original, then ship again."
                return result
            result["base"] = base
            result["files"] = _changes(workspace, base, head) if base else []
            if ref == head:
                result["refusal"] = "Nothing new since the last ship: the branch in the original already has this version."
                return result
            result["writes"] = [
                f"In {folder}/.git: {'update' if ref else 'create'} the branch {branch} "
                f"({(ref or 'new')[:7]} → {head[:7]}) by fetching it from the copy.",
                f"Nothing else in {folder} changes: not its files, not its checked-out branch "
                f"({_read(folder, 'symbolic-ref', '-q', '--short', 'HEAD') or 'detached'}).",
            ]
        else:
            orig = original_state(folder)
            result["before"].update(head=orig["head"], branch=orig["branch"])
            expected = attach.get("branch")
            if orig["branch"] != expected:
                result["refusal"] = (f"The original is on {orig['branch'] or 'a detached commit'}, not on {expected}, "
                                     "the branch this copy started from.")
                result["next"] = f"Check out {expected} in the original, or ship by leaving a branch."
                return result
            if orig["changed"]:
                shown = ", ".join(orig["changed"][:5]) + (" …" if len(orig["changed"]) > 5 else "")
                result["refusal"] = (f"The original has uncommitted changes ({shown}). Merging could mix them "
                                     "with this change, so the studio will not.")
                result["next"] = ("Commit or put away those changes in the original yourself, or ship by leaving "
                                  "a branch instead.")
                return result
            if not _has_commit(workspace, orig["head"]) or not ws.git_ok(
                    workspace, "merge-base", "--is-ancestor", orig["head"], head):
                result["refusal"] = (f"The original's {expected} has moved on since the copy last matched it "
                                     f"(it is at {str(orig['head'])[:7]}). A fast-forward is not possible.")
                result["next"] = ("Bring the copy up to date with the original first (the studio merges the "
                                  "original's new commits into the copy and stops if they conflict), then ship.")
                return result
            result["base"] = orig["head"]
            result["files"] = _changes(workspace, orig["head"], head)
            if orig["head"] == head:
                result["refusal"] = "Nothing new since the last ship: the original already has this version."
                return result
            result["writes"] = [
                (f"In {folder}/.git: the branch {branch} is already at {head[:7]}." if ref == head else
                 f"In {folder}/.git: {'update' if ref else 'create'} the branch {branch} ({(ref or 'new')[:7]} → "
                 f"{head[:7]}) by fetching it from the copy."),
                f"In {folder}: fast-forward {expected} from {orig['head'][:7]} to {head[:7]}; "
                f"{_count(len(result['files']), 'file')} in the folder "
                f"{'changes' if len(result['files']) == 1 else 'change'}.",
            ]
    elif method == "files":
        base = state.get("shipped") or attach.get("import_commit")
        result["base"] = base
        result["files"] = _changes(workspace, base, head)
        if not result["files"]:
            result["refusal"] = "Nothing new since the last ship: no file changed since then."
            return result
        moved = []
        for item in result["files"]:
            try:
                target = _target(folder, item["path"])
            except ShipError as exc:
                result["refusal"] = exc.message
                return result
            current = target.read_bytes() if target.is_file() else None
            if _sha(current) != _sha(_blob(workspace, base, item["path"])):
                moved.append(item["path"])
        if moved:
            shown = ", ".join(moved[:5]) + (" …" if len(moved) > 5 else "")
            result["refusal"] = (f"These files changed in the original since the copy last matched it: {shown}. "
                                 "Copying would overwrite that work, so the studio will not.")
            result["next"] = ("Bring those changes into the copy (or undo them in the original) yourself, "
                              "then ship again.")
            return result
        counts = {kind: sum(1 for f in result["files"] if f["status"] == kind) for kind in ("added", "modified", "deleted")}
        parts = [f"write {_count(counts['added'], 'new file')}" if counts["added"] else "",
                 f"overwrite {_count(counts['modified'], 'file')}" if counts["modified"] else "",
                 f"delete {_count(counts['deleted'], 'file')}" if counts["deleted"] else ""]
        parts = [part for part in parts if part]
        said = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
        kept = (" Every file overwritten or deleted is kept first, so the studio can put it back."
                if counts["modified"] or counts["deleted"] else " Going back removes them again.")
        result["writes"] = [f"In {folder}: {said}.{kept}"]
    else:   # in place: nothing moves
        base = state.get("shipped") or attach.get("import_commit")
        result["base"] = base
        result["files"] = _changes(workspace, base, head)
        if not result["files"]:
            result["refusal"] = "Nothing new since the last ship: no file changed since then."
            return result
        result["writes"] = [f"Nothing is moved: the project is {folder} itself, edited in place."]
        return result
    refusal = _key_file_refusal(workspace, method, result["base"], head, result["files"])
    if refusal:
        result["refusal"], result["next"] = refusal
        result["writes"] = []
    return result


def _key_file_refusal(workspace: Path, method: str, base: str | None, head: str,
                      files: list[dict]) -> tuple[str, str] | None:
    """Key files (keyfiles.py) are never versioned in a copy, so none should reach a ship; one that
    does (a version forced past the rule, or a copy made before it) would put a secret in the
    original. → (refusal, next) or None.

    A branch or merge ship carries every version between `base` and `head` into the original's
    repository, so every version is looked at, not only the end result: a key added in one version
    and removed in a later one is still in the history. A key file the original already tracks
    at `base` is his own file, and the usual key-free names (`.env.example`) are ordinary files."""
    if method in ("branch", "merge"):
        span = f"{base}..{head}" if base else head
        # -m: without it git lists nothing for a merge commit, so a file that is in neither parent
        # and was added in the merge itself would be carried unseen.
        touched = {line for line in ws.git(workspace, "log", "-m", "--format=", "--name-only", "--no-renames",
                                           span).splitlines() if line}
        keys = sorted(path for path in touched if keyfiles.is_key_file(path) and not keyfiles.is_key_free(path))
        if keys and base:
            tracked = set(ws.git(workspace, "ls-tree", "-r", "--name-only", base, "--", *keys).splitlines())
            keys = [path for path in keys if path not in tracked]
        if not keys:
            return None
        shown = ", ".join(keys[:5]) + (" …" if len(keys) > 5 else "")
        return ((f"A version this ship would carry adds or changes key files ({shown}). A {method} ship carries "
                 "every version into the original's repository, not only the end result, so a key removed in a "
                 "later version would still arrive there. The studio never ships key files."),
                ("Removing the file in a new version is not enough. Make the change again in a fresh copy (bring "
                 "the app in again), or make it in the original by hand; put keys into the original yourself."))
    keys = [item["path"] for item in files
            if keyfiles.is_key_file(item["path"]) and not keyfiles.is_key_free(item["path"])]
    if not keys:
        return None
    shown = ", ".join(keys[:5]) + (" …" if len(keys) > 5 else "")
    return ((f"The change includes key files ({shown}). The studio never ships key files into the original."),
            ("Restore those files in the copy to how they were, then ship again (this way of shipping carries "
             "no history, so that is enough). Change key files in the original by hand."))


# -- the person's original, written only here ----------------------------------------------
def _apply(project: dict, workspace: Path, planned: dict, backup: Path) -> list[str]:
    """Make the change in the original. → lines saying what was done. Raises ShipError."""
    attach, method, folder = project["attach"], planned["method"], Path(project["attach"]["path"])
    head, done = planned["head"], []
    if method in ("branch", "merge"):
        branch = attach["studio_branch"]
        code, out = _git_write(folder, "fetch", "--no-tags", "--no-write-fetch-head", str(workspace.resolve()),
                               f"refs/heads/{branch}:refs/heads/{branch}")
        if code != 0:
            raise ShipError("apply_failed", f"git fetch into the original failed: {out[:500]}")
        done.append(f"Branch {branch} in {folder} is now at {head[:7]}.")
        if method == "merge":
            code, out = _git_write(folder, "merge", "--ff-only", "--no-edit", f"refs/heads/{branch}")
            if code != 0:
                raise ShipError("apply_failed", f"The fast-forward in the original failed: {out[:500]}")
            done.append(f"{attach['branch']} in {folder} fast-forwarded from {planned['base'][:7]} to {head[:7]}.")
    elif method == "files":
        written = []
        for item in planned["files"]:
            target = _target(folder, item["path"])
            data = None
            if item["status"] != "deleted":
                data = _blob(workspace, head, item["path"])
                if data is None:
                    raise ShipError("apply_failed", f"Could not read {item['path']} from the copy.")
            if target.exists():
                keep = backup / item["path"]
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, keep)
            # What is about to be there, so going back can tell the ship's write from a later edit.
            written.append({**item, "wrote": _sha(data)})
            # Record each step before taking it, so a crash half way can still be undone.
            write_json_atomic(backup.parent / "applied.json", {"files": written})
            if item["status"] == "deleted":
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, 0o755 if _mode(workspace, head, item["path"]) == "100755" else 0o644)
        done.append(f"{_count(len(written), 'file')} written in {folder}; what they replaced is kept in the "
                    "ship's record.")
    return done


def _in_the_way(folder: Path, before: str, shipped: str) -> list[str]:
    """Files the ship changed that also have local changes in the original now (read only)."""
    _code, changed = _git_read(folder, "diff", "--name-only", "--no-renames", before, shipped)
    _code, status = _git_read(folder, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames")
    local = {line[3:].strip('"') for line in status.splitlines() if len(line) > 3}
    return [path for path in changed.splitlines() if path in local]


def _not_undone(folder: Path, expected: str, on: str | None, before: str | None, now: str | None,
                shipped: str | None) -> ShipError:
    if on != expected:
        moved = (f"{folder} is now on {on or 'a detached commit'}, not on {expected} where the ship left it "
                 f"(someone switched branches since)")
    else:
        moved = (f"{expected} in {folder} is at {(now or 'nothing')[:7]}, not at {(shipped or '?')[:7]} where the "
                 f"ship left it (someone committed since)")
    return ShipError("back_failed", (
        f"The ship could not be undone automatically: {moved}. Before the ship {expected} was at "
        f"{(before or 'nothing')[:7]}. Nothing was put back, so that work is kept, and so is the ship's "
        "change; settling it is yours to do by hand."))


def _go_back(project: dict, planned: dict, backup: Path) -> list[str]:
    """Put the original back as it was before `_apply`, without ever discarding what the studio did not
    put there: the person may have edited or committed in his folder while the after-commands ran.
    → lines saying what was done. Raises ShipError (`back_failed`) when something is left for him."""
    done: list[str] = []
    try:
        attach, method, folder = project["attach"], planned["method"], Path(project["attach"]["path"])
        shipped = planned.get("head")
        if method in ("branch", "merge"):
            branch, before = attach["studio_branch"], planned["before"]
            if method == "merge" and before.get("head"):
                now = _read(folder, "rev-parse", "-q", "--verify", "HEAD") or None
                on = _read(folder, "symbolic-ref", "-q", "--short", "HEAD") or None
                if now == before["head"] and on == attach["branch"]:
                    done.append(f"The branch {attach['branch']} in {folder} was still at {before['head'][:7]}.")
                elif now != shipped or on != attach["branch"]:
                    raise _not_undone(folder, attach["branch"], on, before["head"], now, shipped)
                else:
                    # --keep, never --hard: it refuses rather than lose a local change to a file the ship
                    # changed, and leaves every other edit and untracked file as it is. It does unstage
                    # what was staged (the content stays), so that is said.
                    _code, staged = _git_read(folder, "diff", "--cached", "--name-only", "--no-renames")
                    code, out = _git_write(folder, "reset", "--keep", "-q", before["head"])
                    if code != 0:
                        in_way = _in_the_way(folder, before["head"], shipped)
                        named = ", ".join(in_way[:10]) if in_way else out[:500]
                        raise ShipError("back_failed", (
                            f"The branch {attach['branch']} in {folder} was not put back: these files changed by the ship have "
                            f"been edited there since (by you or by an after-command), and going back would lose "
                            f"that: {named}. Nothing was put back: the ship's change is still there, and so "
                            f"are those edits. To go back by hand, keep those edits somewhere, then run "
                            f"`git reset --keep {before['head'][:7]}` in {folder}."))
                    done.append(f"The branch {attach['branch']} in {folder} is back at {before['head'][:7]}; other edits there are kept.")
                    unstaged = [line for line in staged.splitlines() if line]
                    if unstaged:
                        done.append(UNSTAGED
                                    + ", ".join(unstaged[:10]) + (" …" if len(unstaged) > 10 else "") + ".")
            current, target = _ref(folder, branch), before.get("ref")
            if current == target:
                done.append(f"Branch {branch} in {folder} was " + (f"still at {target[:7]}." if target else "not there."))
            elif current != shipped or _read(folder, "symbolic-ref", "-q", "--short", "HEAD") == branch:
                raise ShipError("back_failed", (
                    f"The branch {branch} in {folder} was left as it is: it is at {(current or 'nothing')[:7]}"
                    f"{' and checked out' if current == shipped else ''}, not where the ship left it "
                    f"({(shipped or '?')[:7]}), so someone is using it. Before the ship it was at "
                    f"{(target or 'nothing')[:7]}."))
            else:
                # The old value makes git refuse if the branch moves between the look above and this write.
                if target:
                    code, out = _git_write(folder, "update-ref", f"refs/heads/{branch}", target, current)
                else:
                    code, out = _git_write(folder, "update-ref", "-d", f"refs/heads/{branch}", current)
                if code != 0:
                    raise ShipError("back_failed", f"Putting the branch {branch} back failed: {out[:500]}")
                done.append(f"Branch {branch} in {folder} is " + (f"back at {target[:7]}." if target else "removed again."))
        elif method == "files":
            try:
                applied = json.loads((backup.parent / "applied.json").read_text("utf-8"))["files"]
            except (OSError, ValueError, KeyError):
                applied = []
            left = []
            for item in reversed(applied):
                try:
                    target = _target(folder, item["path"])
                except ShipError:
                    left.append(item["path"])         # now a link, or under one: never written through
                    continue
                keep = backup / item["path"]
                if "wrote" in item:   # records from before 17 November 2026 do not say what was written
                    if target.exists() and not target.is_file():
                        left.append(item["path"])
                        continue
                    current = _sha(target.read_bytes()) if target.is_file() else None
                    original = _sha(keep.read_bytes()) if keep.is_file() else None
                    if current not in (item["wrote"], original):
                        left.append(item["path"])     # edited after the ship wrote it: his now
                        continue
                if keep.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(keep, target)
                else:
                    target.unlink(missing_ok=True)
            put = len(applied) - len(left)
            if put or not left:
                done.append(f"{_count(put, 'file')} in {folder} put back as {'it was' if put == 1 else 'they were'}.")
            if left:
                shown = ", ".join(left[:10]) + (" …" if len(left) > 10 else "")
                one = len(left) == 1
                raise ShipError("back_failed", (
                    f"{'This file' if one else 'These files'} changed in {folder} after the ship wrote "
                    f"{'it' if one else 'them'} (edited, or now a link), so the studio left "
                    f"{'it as it is' if one else 'them as they are'}: {shown}. What "
                    f"{'it' if one else 'each'} was before the ship is kept in the ship's record (its backup folder)."))
        return done
    except ShipError as exc:
        # What was already put back is said first, so the message never reads as if nothing was.
        if done:
            exc.message = " ".join(done) + " " + exc.message
        raise


def _shipped_message(method: str, recipe: dict) -> str:
    """What a ship that went through did, and nothing it did not do."""
    ran = [part for part, there in (("the after-commands ran", recipe.get("after")),
                                    ("the address answered", recipe.get("address"))) if there]
    if method == "none":
        if not ran:
            return "Nothing to move or run: the folder already has the change."
        return "In the folder, " + " and ".join(ran) + "."
    return "The change is in the original" + "".join(
        (", " if index < len(ran) - 1 else " and ") + part for index, part in enumerate(ran)) + "."


# -- the runner ----------------------------------------------------------------------------
class Shipper:
    """One ship at a time per project. All state changes happen on the event loop."""

    def __init__(self, studio: "Studio"):
        self.studio = studio
        self.running: dict[str, str] = {}             # project id → ship id
        self.tasks: dict[str, asyncio.Task] = {}
        self.waiting: dict[str, asyncio.Future] = {}  # ship id → the person's decision
        self.cards: dict[str, dict] = {}              # ship id → the card being shown
        self.from_agent: dict[str, str] = {}          # project id → turn that asked for a ship

    # -- records ---------------------------------------------------------------------------
    def folder(self, project_id: str, ship_id: str) -> Path:
        return self.studio.store.dir(project_id) / "ships" / ship_id

    def save(self, project_id: str, record: dict) -> None:
        target = self.folder(project_id, record["id"])
        target.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target / "record.json", record)

    def records(self, project_id: str) -> list[dict]:
        found = []
        for path in (self.studio.store.dir(project_id) / "ships").glob("*/record.json"):
            try:
                found.append(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda item: item.get("started_at") or "", reverse=True)

    def record(self, project_id: str, ship_id: str) -> dict | None:
        try:
            return json.loads((self.folder(project_id, ship_id) / "record.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None

    # -- the recipe ------------------------------------------------------------------------
    def view(self, project: dict) -> dict:
        attach = project.get("attach")
        state = project.get("ship") or {}
        running = self.running.get(project["id"])
        return {"available": bool(attach), "methods": methods_for(attach),
                "recipe": state.get("recipe") or (default_recipe(attach) if attach else None),
                "confirmed": bool(state.get("recipe")), "confirmed_by": state.get("confirmed_by"),
                "confirmed_at": state.get("confirmed_at"), "last": state.get("last"),
                "running": running, "card": self.cards.get(running) if running else None}

    def set_recipe(self, project_id: str, raw: dict, by: str) -> dict:
        project = self.studio.store.get(project_id)
        if not project.get("attach"):
            raise ShipError("not_attached", "Only a project brought in from a folder can be made live.", 400)
        if project_id in self.running:
            raise ShipRunning(project_id)
        recipe = clean_recipe(raw, project["attach"])
        state = {**(project.get("ship") or {}), "recipe": recipe, "confirmed_by": by, "confirmed_at": now_iso()}
        self.studio.store.update(project_id, ship=state)
        self.studio.log(project_id).append("ship_recipe", self.studio.turns.active.get(project_id),
                                           recipe=recipe, confirmed_by=by)
        return recipe

    async def propose(self, project_id: str, raw: dict) -> dict:
        """The agent proposes a recipe: the person confirms it on a card, every time it changes."""
        project = self.studio.store.get(project_id)
        attach = project.get("attach")
        if not attach:
            raise ShipError("not_attached", "This project was not brought in from a folder; there is nowhere to ship to.")
        recipe = clean_recipe(raw, attach)
        current = (project.get("ship") or {}).get("recipe")
        if current == recipe:
            return {"recipe": recipe, "changed": False}
        title = "Use this recipe to make the app live" if not current else "Change how this app is made live"
        allowed = await self.studio.ask(project_id, "ship_recipe", title, describe_recipe(recipe, attach),
                                        "Shipping writes to your original app with these commands. You allow every "
                                        "ship again on its own card; this only sets the recipe.")
        if not allowed:
            raise ShipError("recipe_denied", "The person did not accept this recipe. Nothing was saved.")
        self.set_recipe(project_id, recipe, "agent, confirmed by the person")
        return {"recipe": recipe, "changed": True}

    # -- starting --------------------------------------------------------------------------
    def request_from_agent(self, project_id: str, turn_id: str) -> None:
        project = self.studio.store.get(project_id)
        if not project.get("attach"):
            raise ShipError("not_attached", "This project was not brought in from a folder; there is nowhere to ship to.")
        if not (project.get("ship") or {}).get("recipe"):
            raise ShipError("no_recipe", "There is no confirmed ship recipe yet. Read the app (its scripts, notes and "
                            "deploy files), then propose one with propose_ship_recipe.")
        self.from_agent[project_id] = turn_id

    async def after_turn(self, project_id: str) -> None:
        """Called when a turn has ended and its version is saved: start the ship the agent asked for."""
        turn_id = self.from_agent.pop(project_id, None)
        if turn_id is None:
            return
        try:
            await self.start(project_id, "agent", turn_id)
        except Exception as exc:  # noqa: BLE001 - the turn is over; say why nothing started
            message = (exc.message if isinstance(exc, ShipError) else "A ship is already running."
                       if isinstance(exc, ShipRunning) else f"It could not start ({exc}).")
            self.studio.log(project_id).append("notice", None, level="warning", message=f"Make it live: {message}")

    async def start(self, project_id: str, by: str = "person", turn_id: str | None = None) -> str:
        studio = self.studio
        project = studio.store.get(project_id)
        if project_id in studio.deleting:   # it is being deleted; there is nothing left to ship from
            raise ProjectNotFound(project_id)
        if not project.get("attach"):
            raise ShipError("not_attached", "Only a project brought in from a folder can be made live.", 400)
        if not (project.get("ship") or {}).get("recipe"):
            raise ShipError("no_recipe", "Set the ship recipe first (the assistant can draft it, or edit it here).")
        if project_id in studio.turns.active:
            raise ShipError("turn_running", "The assistant is still changing files. Make it live when it is done.")
        if project_id in self.running:
            raise ShipRunning(project_id)
        ship_id = "s_" + secrets.token_hex(4)
        self.running[project_id] = ship_id      # claimed before the first await
        try:
            await studio.checkpoint(project_id, "user", "Changes before making it live")
            planned = await asyncio.to_thread(plan, studio.store.get(project_id), studio.store.workspace(project_id))
        except Exception:
            self.running.pop(project_id, None)
            raise
        if planned["refusal"]:
            self.running.pop(project_id, None)
            message = planned["refusal"] + (f" Next: {planned['next']}" if planned["next"] else "")
            code = "nothing_to_ship" if planned["refusal"].startswith("Nothing new") else "ship_refused"
            raise ShipError(code, message)
        project = studio.store.get(project_id)
        record = {"id": ship_id, "project": project_id, "by": by, "turn_id": turn_id, "started_at": now_iso(),
                  "finished_at": None, "phase": "checks", "result": None, "message": None,
                  "recipe": project["ship"]["recipe"], "attach": {k: project["attach"].get(k) for k in
                                                                    ("mode", "path", "kind", "branch", "studio_branch")},
                  "plan": {k: planned[k] for k in ("method", "base", "head", "before", "where", "writes")},
                  "files": planned["files"][:FILE_LIST_CAP], "files_more": max(0, len(planned["files"]) - FILE_LIST_CAP),
                  "checks": [], "decision": None, "applied": [], "after": [], "address": None, "back": [],
                  "back_after": []}
        self.save(project_id, record)
        studio.log(project_id).append("ship_started", None, ship_id=ship_id, by=by, method=planned["method"],
                                      base=planned["base"], head=planned["head"],
                                      files=record["files"], files_more=record["files_more"])
        task = asyncio.create_task(self._run(project_id, record, planned))
        self.tasks[project_id] = task
        return ship_id

    # -- deciding --------------------------------------------------------------------------
    def decide(self, project_id: str, ship_id: str, decision: str, confirm: str = "") -> None:
        future = self.waiting.get(ship_id)
        if self.running.get(project_id) != ship_id or future is None or future.done():
            raise ShipError("not_waiting", "That ship is not waiting for an answer.", 404)
        card = self.cards.get(ship_id) or {}
        if decision == "allow" and card.get("needs_name"):
            name = self.studio.store.get(project_id)["name"]
            if " ".join(str(confirm or "").split()) != " ".join(name.split()):
                raise ShipError("confirm_needed", f"This change touches protected paths. Type the project name "
                                f"“{name}” to allow it.", 400)
        future.set_result(decision == "allow")

    # -- the run ---------------------------------------------------------------------------
    def _step(self, project_id: str, ship_id: str, step: str, state: str, **fields) -> None:
        self.studio.log(project_id).append("ship_step", None, ship_id=ship_id, step=step, state=state, **fields)

    async def _commands(self, project_id: str, record: dict, step: str, commands: list[str], cwd: Path,
                        into: list) -> bool:
        timeout = record["recipe"]["timeout"]
        for index, command in enumerate(commands):
            self._step(project_id, record["id"], step, "running", index=index, command=command)
            result = await run_command(command, cwd, timeout,
                                       self.folder(project_id, record["id"]) / "logs" / f"{step}-{index}.log")
            into.append(result)
            self.save(project_id, record)
            self._step(project_id, record["id"], step, "ok" if _ok(result) else "failed", index=index,
                       command=command, exit_code=result["exit_code"], output=result["output"][-EVENT_OUTPUT:],
                       duration_ms=result["duration_ms"])
            if not _ok(result):
                return False
        return True

    def _card(self, project: dict, record: dict, planned: dict) -> dict:
        recipe, attach = record["recipe"], project["attach"]
        where = attach["path"]
        hits = protected_hits([f["path"] for f in planned["files"]], recipe["protected"])
        commands = [] if planned["method"] == "branch" else [{"command": c, "where": where} for c in recipe["after"]]
        return {"ship_id": record["id"], "method": planned["method"], "method_label": METHODS[planned["method"]],
                "where": where, "head": planned["head"], "base": planned["base"],
                "files": record["files"], "files_more": record["files_more"], "checks": record["checks"],
                "writes": planned["writes"], "commands": commands,
                "address": None if planned["method"] == "branch" else recipe["address"],
                "protected": hits, "needs_name": bool(hits), "project_name": project["name"],
                "going_back": self._going_back_text(planned["method"], recipe),
                "limits": limits_for(planned["method"], recipe), "merge_help": self._merge_help(attach) if planned["method"] == "branch" else None}

    @staticmethod
    def _going_back_text(method: str, recipe: dict) -> str:
        again = (", then runs: " + "; ".join(recipe["back"])) if recipe["back"] else (
            ", then runs the after-commands again" if recipe["after"] else "")
        if method in ("merge", "files"):
            undo = ("puts the branch back where it was" if method == "merge"
                    else "puts back every file it wrote or removed")
            if not recipe["after"] and not recipe["address"]:
                step = "merge" if method == "merge" else "copy"
                if recipe["back"]:
                    return (f"Nothing runs after the {step}. If the {step} itself fails, the studio {undo}{again}. "
                            f"If the studio stops half way, it {undo} when it starts again.")
                return (f"Nothing runs after the {step}, so there is no step that can fail afterwards. If the "
                        f"studio stops half way, it {undo} when it starts again.")
            return f"If a step after the {'merge' if method == 'merge' else 'copy'} fails, the studio {undo}{again}."
        if method == "branch":
            return "The branch is only left there; delete it in the original to go back."
        return "Nothing is moved, so there is nothing for the studio to put back; restore a version if needed."

    @staticmethod
    def _merge_help(attach: dict) -> str:
        branch, base = attach["studio_branch"], attach.get("branch") or "your branch"
        return (f"In {attach['path']}: review with `git log {base}..{branch}` and `git diff {base}...{branch}`, "
                f"then `git checkout {base} && git merge --ff-only {branch}` (or open a pull request from it). "
                f"To go back, delete it: `git branch -D {branch}`.")

    async def _run(self, project_id: str, record: dict, planned: dict) -> None:
        studio, ship_id = self.studio, record["id"]
        workspace = studio.store.workspace(project_id)
        project = studio.store.get(project_id)
        folder = Path(project["attach"]["path"])
        recipe = record["recipe"]
        backup = self.folder(project_id, ship_id) / "backup"
        result, message = "failed", ""
        try:
            # 1. checks, in the copy
            if recipe["checks"]:
                if not await self._commands(project_id, record, "check", recipe["checks"], workspace.resolve(),
                                            record["checks"]):
                    result, message = "checks_failed", "A check failed in the copy, so nothing was written to the original."
                    return
            # 2. the card: the person allows or denies, every time
            record["phase"] = "waiting"
            self.save(project_id, record)
            card = self._card(project, record, planned)
            self.cards[ship_id] = card
            future = asyncio.get_running_loop().create_future()
            self.waiting[ship_id] = future
            studio.log(project_id).append("ship_ready", None, ship_id=ship_id, card=card)
            allowed = await future
            self.waiting.pop(ship_id, None)
            record["decision"] = {"decision": "allow" if allowed else "deny", "at": now_iso(), "by": "person",
                                  "protected": card["protected"]}
            studio.log(project_id).append("ship_decided", None, ship_id=ship_id,
                                          decision=record["decision"]["decision"])
            if not allowed:
                result, message = "denied", "Not allowed. Nothing was written to the original."
                return
            # 3. the original may have changed while the card was open: plan again and compare
            again = await asyncio.to_thread(plan, studio.store.get(project_id), workspace)
            if again["refusal"] or again["head"] != planned["head"] or again["base"] != planned["base"] \
                    or again["before"] != planned["before"]:
                result = "refused"
                message = ((again["refusal"] or "The original or the copy changed while the card was open.")
                           + " Nothing was written to the original.")
                return
            # 4. apply
            record["phase"] = "applying"
            self.save(project_id, record)
            self._step(project_id, ship_id, "apply", "running")
            try:
                record["applied"] = await asyncio.to_thread(_apply, project, workspace, planned, backup)
            except Exception as exc:  # noqa: BLE001 - a half-done apply is undone below
                record["applied"].append(f"Failed: {getattr(exc, 'message', None) or exc}")
                self._step(project_id, ship_id, "apply", "failed", lines=record["applied"])
                result, message = await self._back(project_id, record, planned, backup, folder,
                                                   f"Making the change failed ({getattr(exc, 'message', None) or exc}).")
                return
            self._step(project_id, ship_id, "apply", "ok", lines=record["applied"])
            record["phase"] = "after"
            self.save(project_id, record)
            # 5. after-commands in the original, then the address (not for a branch left there)
            if planned["method"] != "branch":
                if recipe["after"] and not await self._commands(project_id, record, "after", recipe["after"],
                                                                folder, record["after"]):
                    result, message = await self._back(project_id, record, planned, backup, folder,
                                                       "An after-command failed.")
                    return
                if recipe["address"]:
                    record["phase"] = "address"
                    self.save(project_id, record)
                    self._step(project_id, ship_id, "address", "running", address=recipe["address"])
                    record["address"] = await check_address(recipe["address"])
                    self._step(project_id, ship_id, "address", "ok" if record["address"]["ok"] else "failed",
                               address=recipe["address"], attempts=record["address"]["attempts"])
                    if not record["address"]["ok"]:
                        result, message = await self._back(project_id, record, planned, backup, folder,
                                                           f"{recipe['address']} did not answer.")
                        return
            result = "shipped"
            if planned["method"] == "branch":
                message = f"The branch {project['attach']['studio_branch']} is in the original's repository. " \
                          + self._merge_help(project["attach"])
            else:
                message = _shipped_message(planned["method"], recipe)
            state = {**(studio.store.get(project_id).get("ship") or {}), "shipped": planned["head"]}
            studio.store.update(project_id, ship=state)
        except asyncio.CancelledError:
            result, message = "interrupted", "The studio stopped during this ship."
            if record["phase"] in ("applying", "after", "address"):
                try:
                    record["back"] = await asyncio.to_thread(_go_back, project, planned, backup)
                    message += " The original was put back; its after-commands were not run again."
                except ShipError as exc:
                    message += f" {exc.message}"
                except Exception as exc:  # noqa: BLE001
                    message += f" Putting the original back stopped on an error ({exc}). Check {folder} by hand."
        except Exception as exc:  # noqa: BLE001 - a ship always ends with a record
            log.exception("Ship %s failed", ship_id)
            result, message = "failed", f"The studio hit an error: {exc}"
        finally:
            self.waiting.pop(ship_id, None)
            self.cards.pop(ship_id, None)
            record.update(result=result, message=message, finished_at=now_iso(), phase="done")
            self.save(project_id, record)
            last = {"ship_id": ship_id, "result": result, "at": record["finished_at"], "head": planned["head"],
                    "method": planned["method"]}
            with contextlib.suppress(Exception):
                studio.store.update(project_id, ship={**(studio.store.get(project_id).get("ship") or {}), "last": last})
            self.running.pop(project_id, None)
            self.tasks.pop(project_id, None)
            with contextlib.suppress(Exception):
                studio.log(project_id).append("ship_finished", None, ship_id=ship_id, result=result, message=message,
                                              merge_help=self._merge_help(project["attach"])
                                              if planned["method"] == "branch" and result == "shipped" else None)
            with contextlib.suppress(Exception):
                studio.turns._start_next(project_id)   # messages sent during the ship run now

    async def _back(self, project_id: str, record: dict, planned: dict, backup: Path, folder: Path,
                    why: str) -> tuple[str, str]:
        """Go back by itself after a failed step. → (result, message)."""
        if planned["method"] in ("none",):
            return "failed", (f"{why} Nothing was moved (the project is the folder itself), so there was nothing "
                              "to put back. The folder has the edited files; restore a version if needed.")
        record["phase"] = "back"
        self.save(project_id, record)
        self._step(project_id, record["id"], "back", "running")
        try:
            record["back"] = await asyncio.to_thread(_go_back, self.studio.store.get(project_id), planned, backup)
        except ShipError as exc:
            # Its message says what was put back, what was left and why: true as it stands.
            self._step(project_id, record["id"], "back", "failed", lines=[exc.message])
            return "back_failed", f"{why} {exc.message}"
        except Exception as exc:  # noqa: BLE001
            self._step(project_id, record["id"], "back", "failed", lines=[str(exc)])
            kept = (" What each file was before the ship is kept in the ship's record."
                    if planned["method"] == "files" else "")
            return "back_failed", (f"{why} Going back stopped on an error ({exc}), so {folder} may be only partly "
                                   f"put back: check it by hand.{kept}")
        self._step(project_id, record["id"], "back", "ok", lines=record["back"])
        unstaged = next((line[len(UNSTAGED):] for line in record["back"] if line.startswith(UNSTAGED)), None)
        staged = (f" Files you had staged there are kept but no longer staged: {unstaged.rstrip('.')}. Stage them "
                  "again if you want." if unstaged else "")
        restored = "branch is back where it was" if planned["method"] == "merge" else "files are back as they were"
        # A branch is only left there: none of its commands ever run, going back included.
        again = None if planned["method"] == "branch" else (record["recipe"]["back"] or record["recipe"]["after"])
        if again:
            if not await self._commands(project_id, record, "back_after", again, folder, record["back_after"]):
                return "back_failed", (f"{why} The original's {restored}, but a command run after going back "
                                       f"failed, so the running app may not match it. Check it by hand.{staged}")
        if not again:
            ran = ""
        elif record["recipe"]["back"]:
            ran = " The go-back commands ran."
        elif record["after"]:     # they had run (or started) in this ship before it went back
            ran = " The after-commands ran again."
        else:                     # applying failed: they had not run yet
            ran = " The after-commands were run after going back, so that what is live matches the folder."
        return "rolled_back", (f"{why} The studio went back: the original is as it was before this ship."
                               + staged + ran)

    # -- startup and shutdown --------------------------------------------------------------
    async def recover(self, project_id: str) -> None:
        """A ship cut off by a restart: undo a half-done change, then close its record."""
        for record in self.records(project_id):
            if record.get("result"):
                continue
            project = self.studio.store.get(project_id)
            message = "The studio restarted during this ship."
            if record.get("phase") in ("applying", "after", "address", "back") and project.get("attach"):
                planned = {"method": record["plan"]["method"], "before": record["plan"]["before"],
                           "head": record["plan"].get("head")}
                try:
                    record["back"] = await asyncio.to_thread(
                        _go_back, project, planned, self.folder(project_id, record["id"]) / "backup")
                    message += " The original was put back; its after-commands were not run again (run them by hand)."
                except ShipError as exc:
                    message += f" {exc.message}"
                except Exception as exc:  # noqa: BLE001
                    message += (f" Putting the original back stopped on an error ({exc}). "
                                f"Check {record['attach']['path']} by hand.")
            else:
                message += " Nothing had been written to the original."
            record.update(result="interrupted", message=message, finished_at=now_iso(), phase="done")
            self.save(project_id, record)
            self.studio.log(project_id).append("ship_finished", None, ship_id=record["id"], result="interrupted",
                                               message=message, merge_help=None)

    async def stop_all(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- bring the copy up to date -----------------------------------------------------------
    async def update_copy(self, project_id: str) -> dict:
        """Merge the original's new commits into the copy (a version); stop on a conflict."""
        studio = self.studio
        project = studio.store.get(project_id)
        attach = project.get("attach") or {}
        if attach.get("mode") != "copy" or attach.get("kind") != "git":
            raise ShipError("not_git_copy", "Only a safe copy of a git folder can be brought up to date this way.", 400)
        if project_id in studio.turns.active:
            raise ShipError("turn_running", "The assistant is still changing files.")
        if project_id in self.running:
            raise ShipRunning(project_id)
        folder, workspace = Path(attach["path"]), studio.store.workspace(project_id)
        branch = attach.get("branch")
        orig = await asyncio.to_thread(original_state, folder)
        if not orig["head"]:
            raise ShipError("ship_refused", "The original has no commit to bring in.")
        await studio.checkpoint(project_id, "user", "Changes before bringing the copy up to date")
        async with studio.lock(project_id):
            # The original is only read by the fetch. The copy's config is the agent's to write, so the
            # program that reads the original is named here (a `remote.<path>.uploadpack` there would
            # otherwise be used), and git gets the studio's short environment, not its keys.
            env = {**ws.git_env(workspace), "GIT_OPTIONAL_LOCKS": "0"}
            await asyncio.to_thread(ws.hold_attributes, workspace)
            proc = await asyncio.to_thread(subprocess.run, [*ws.GIT_BASE, "fetch", "--no-tags", "-q",
                                                            "--upload-pack=git-upload-pack", str(folder),
                                                            f"refs/heads/{branch}" if branch else "HEAD"],
                                           cwd=workspace, env=env, capture_output=True)
            if proc.returncode != 0:
                raise ShipError("update_failed", "Reading the original's commits failed: "
                                + proc.stderr.decode("utf-8", "replace")[:300])
            fetched = ws.git(workspace, "rev-parse", "FETCH_HEAD").strip()
            if ws.git_ok(workspace, "merge-base", "--is-ancestor", fetched, "HEAD"):
                return {"updated": False, "message": "The copy already has every commit of the original."}
            try:
                ws.git(workspace, "merge", "--no-ff", "--no-edit", "-m",
                       f"[user] Bring in {branch or 'the original'} at {fetched[:7]} from the original", fetched)
            except ws.GitError:
                conflicts = [line for line in ws.git(workspace, "diff", "--name-only", "--diff-filter=U",
                                                     check=False).splitlines() if line]
                ws.git(workspace, "merge", "--abort", check=False)
                shown = ", ".join(conflicts[:8]) or "some files"
                raise ShipError("update_conflict", f"The original's new commits conflict with the copy's changes in "
                                f"{shown}. Nothing was changed. Ask the assistant to bring in the original's version "
                                "of those files and redo the change, or merge by hand in the original.") from None
            made = ws.describe(workspace, "HEAD")
        studio.record_checkpoint(project_id, {**made, "kind": "user"})
        return {"updated": True, "message": f"Brought in the original's {branch or 'commits'} at {fetched[:7]}."}
