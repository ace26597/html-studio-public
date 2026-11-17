"""The project workspace: safe paths, file listing and git checkpoints.

Every function here is synchronous and fast; async callers run the git ones in a thread.
History is only ever appended to: restore is a new commit, never a reset.
"""

from __future__ import annotations

import html
import shutil
import os
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
KINDS = ("init", "agent", "user", "restore")
SUBJECT_RE = re.compile(r"^\[(init|agent|user|restore)\] ?(.*)$", re.S)
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
STATUS = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}
# `/state/` is what a project's app writes while it runs (CONTRACT.md, "What an app writes while it runs").
GITIGNORE = "node_modules/\n.venv/\n__pycache__/\n/state/\n"


def ensure_ignored(workspace: Path) -> bool:
    """Make sure the project's .gitignore has the studio's lines. → True when it added any."""
    ignore = workspace / ".gitignore"
    try:
        existing = ignore.read_text("utf-8") if ignore.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return False
    missing = [line for line in GITIGNORE.split() if line not in existing.split()]
    if missing:
        gap = "" if not existing or existing.endswith("\n") else "\n"
        ignore.write_text(existing + gap + "".join(line + "\n" for line in missing), "utf-8")
    return bool(missing)
GIT_BASE = [
    "git", "-c", "user.name=Studio", "-c", "user.email=studio@localhost",
    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
    "-c", "core.quotepath=false", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main",
    # A project's own .git/config must never make the studio's git run a command (the agent can
    # write that file): these settings name programs, or make git look for one, and a value given
    # here wins over the repository's. Programs named under a name of the project's choosing
    # (filter.<x>, diff.<x>, merge.<x>) cannot be listed here: see HELD_ATTRIBUTES.
    "-c", "core.fsmonitor=false", "-c", "core.sshCommand=ssh", "-c", "core.pager=cat",
    "-c", "core.editor=true", "-c", "sequence.editor=true", "-c", "core.askPass=true",
    "-c", "credential.helper=", "-c", "core.alternateRefsCommand=true", "-c", "merge.default=text",
    "-c", "log.showSignature=false", "-c", "merge.verifySignatures=false", "-c", "gpg.program=false",
    "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false",
    # The studio only ever reads other repositories by their path on this disk: no network, no
    # `ext::` command transport, whatever `url.<x>.insteadOf` the project sets.
    # A `protocol.<name>.allow` outranks `protocol.allow`, so each transport git has is named.
    "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
    *[item for name in ("ext", "fd", "git", "ssh", "http", "https", "ftp", "ftps")
      for item in ("-c", f"protocol.{name}.allow=never")],
]
# Held as the whole of `info/attributes` in the git directory before every studio git command.
# That file outranks every `.gitattributes` in the tree, so no attribute the project sets can name
# a filter, diff or merge driver (each runs a program from config). Works on every git version
# (`GIT_ATTR_SOURCE`, git 2.41 and later, would leave `info/attributes` itself, which the agent can
# also write). `!x` means "as if never set": git's own text handling, diffs and merges.
HELD_ATTRIBUTES = ("# Written by the studio before each of its git commands, so that no attribute names a\n"
                   "# program. Edits here are replaced.\n* !filter !diff !merge\n")
# What git gets from the studio's environment: enough to find programs and its temp folder, never
# the studio's keys (a program that did run would have them).
GIT_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SYSTEMROOT")
# Diffs: no external diff program and no text conversion, whatever the config says.
_DIFF_COMMANDS = {"diff", "show", "log"}
# Code points HFS+ ignores in a name, so ".g\u200cit" is ".git" there (git refuses them the same way).
_HFS_IGNORED = dict.fromkeys([0x200C, 0x200D, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x206A, 0x2070),
                              0xFEFF])


def is_git_name(part: str) -> bool:
    """A path part a Mac disk would treat as `.git` (case, Unicode form and ignored code points aside).
    Written into a project, such a folder replaces the studio's own repository, and its config can
    run commands."""
    folded = unicodedata.normalize("NFC", part).translate(_HFS_IGNORED).casefold()
    return folded == ".git"

BLANK_INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  html {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.5; }}
  body {{ margin: 0; background: Canvas; color: CanvasText; }}
  main {{ max-width: 72rem; margin: 0 auto; padding: 3rem 2rem; }}
</style>
</head>
<body>
<main></main>
</body>
</html>
"""


class GitError(Exception):
    pass


# A project edited in place keeps its versions in a git directory of the studio's own, with the
# person's folder as the work tree, so the app's own repository is never touched. Keyed by the
# resolved folder; filled by the store (CONTRACT.md, "Edit in place").
_HISTORIES: dict[str, Path] = {}


def register_history(workspace: Path, git_dir: Path | None) -> None:
    key = str(Path(workspace).resolve())
    if git_dir is None:
        _HISTORIES.pop(key, None)
    else:
        _HISTORIES[key] = Path(git_dir)


def history_dir(workspace: Path) -> Path | None:
    return _HISTORIES.get(str(Path(workspace).resolve()))


class InvalidPath(Exception):
    pass


def safe_path(workspace: Path, rel: str) -> Path:
    """Resolve a workspace-relative path; refuse traversal, .git and symlink escapes."""
    parts = [part for part in str(rel or "").split("/") if part not in ("", ".")]
    if (not parts or "\\" in rel or "\x00" in rel
            or any(part == ".." or part.lower() == ".git" for part in parts)):
        raise InvalidPath(rel)
    root = workspace.resolve()
    target = root.joinpath(*parts).resolve()
    if root not in target.parents:
        raise InvalidPath(rel)
    # The names in the request were checked above; a link is judged by where it ends, so one
    # with an innocent name that points into .git is refused as well.
    if any(part.lower() == ".git" for part in target.relative_to(root).parts):
        raise InvalidPath(rel)
    return target


def _file_entry(workspace: Path, path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    return {"path": path.relative_to(workspace).as_posix(), "size": stat.st_size,
            "modified_at": modified.isoformat(timespec="milliseconds").replace("+00:00", "Z")}


def list_files(workspace: Path, limit: int = 5000, kept_only: bool = False) -> list[dict]:
    """`kept_only` (an attached app): the files its history keeps, not build output or caches."""
    if kept_only:
        root = workspace.resolve()
        found = (_file_entry(root, root / name) for name in kept_files(root))
        return [entry for entry in found if entry][:limit]
    files = []
    for folder, dirs, names in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(names):
            path = Path(folder) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            files.append({
                "path": path.relative_to(workspace).as_posix(), "size": stat.st_size,
                "modified_at": modified.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            })
            if len(files) >= limit:
                return files
    return files


def git_env(workspace: Path) -> dict:
    """The environment of the studio's git on a project: short, and with the studio's history
    directory for a project edited in place."""
    env = {name: os.environ[name] for name in GIT_ENV_KEEP if name in os.environ}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    outside = history_dir(workspace)
    if outside is not None:
        env.update(GIT_DIR=str(outside), GIT_WORK_TREE=str(Path(workspace).resolve()))
    return env


def hold_attributes(workspace: Path) -> None:
    """Make `info/attributes` in the project's git directory exactly HELD_ATTRIBUTES (see there).
    Never writes through a link: one the agent put there is removed first."""
    git_dir = history_dir(workspace) or Path(workspace) / ".git"
    if not git_dir.exists() and not git_dir.is_symlink():
        return                              # before `git init`
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise GitError(f"{git_dir} is not a folder, so the studio will not run git there.")
    info = git_dir / "info"
    if info.is_symlink() or (info.exists() and not info.is_dir()):
        info.unlink()
    info.mkdir(exist_ok=True)
    target = info / "attributes"
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    try:
        if target.read_text("utf-8") == HELD_ATTRIBUTES:
            return
    except (OSError, UnicodeDecodeError):
        pass
    target.write_text(HELD_ATTRIBUTES, "utf-8")


def git(workspace: Path, *args: str, check: bool = True) -> str:
    hold_attributes(workspace)
    if args and args[0] in _DIFF_COMMANDS:
        args = (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
    proc = subprocess.run([*GIT_BASE, *args], cwd=workspace, env=git_env(workspace), capture_output=True)
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return proc.stdout.decode("utf-8", "replace")


def init_workspace(workspace: Path, title: str, template: Path | None = None, label: str = "Blank page",
                   exact: bool = False, leave_out=None) -> dict:
    """Create the workspace. `template` is a folder whose files are copied in; "{{title}}" in
    text files becomes the project name, unless `exact` (an example: its files are copied as they are).
    `leave_out(parts)` → True for a template path not to copy."""
    workspace.mkdir(parents=True, exist_ok=True)
    git(workspace, "init", "-q")
    if template and template.is_dir():
        for source in sorted(template.rglob("*")):
            relative = source.relative_to(template)
            # Never over the studio's own .git, whatever case the template spells it in.
            if source.is_dir() or source.is_symlink() or any(is_git_name(part) for part in relative.parts):
                continue
            if leave_out is not None and leave_out(relative.parts):
                continue
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if exact:
                shutil.copyfile(source, target)
                continue
            try:
                target.write_text(source.read_text("utf-8").replace("{{title}}", html.escape(title)), "utf-8")
            except UnicodeDecodeError:
                shutil.copyfile(source, target)
    if not (workspace / "index.html").is_file() and not (workspace / "studio.json").is_file():
        (workspace / "index.html").write_text(BLANK_INDEX.format(title=html.escape(title)), "utf-8")
    ensure_ignored(workspace)
    return checkpoint(workspace, "init", label)


def kept_files(workspace: Path) -> list[str]:
    """What the project's history would keep: tracked files plus untracked ones not ignored."""
    out = git(workspace, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({name for name in out.split("\0") if name and (workspace / name).is_file()})


def zip_workspace(workspace: Path, target: Path, kept_only: bool = False) -> None:
    """Everything a person would want to take away: no git history, no installed dependencies.
    `kept_only` (an attached app): only what the history keeps, not build output or env files."""
    import zipfile
    root = workspace.resolve()
    names = kept_files(root) if kept_only else None
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        if names is not None:
            for name in names:
                path = root / name
                if not path.is_symlink():
                    archive.write(path, name)
            return
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if IGNORED_DIRS.intersection(rel.parts) or not path.is_file() or path.is_symlink():
                continue
            archive.write(path, rel.as_posix())


def head(workspace: Path) -> str | None:
    return git(workspace, "rev-parse", "-q", "--verify", "HEAD", check=False).strip() or None


def _parse_subject(subject: str) -> tuple[str, str]:
    match = SUBJECT_RE.match(subject)
    # A commit without our prefix was made by the agent running git itself.
    return (match.group(1), match.group(2)) if match else ("agent", subject)


def _name_status(text: str) -> list[dict]:
    files = []
    for line in text.splitlines():
        status, _, path = line.partition("\t")
        if path:
            files.append({"path": path, "status": STATUS.get(status[:1], "modified")})
    return files


def describe(workspace: Path, sha: str, base: str | None = None) -> dict:
    """The checkpoint object for a commit (files relative to `base` or its parent)."""
    meta = git(workspace, "show", "-s", "--format=%H%x1f%ct%x1f%s", sha).strip().split("\x1f")
    if base:
        changed = git(workspace, "diff", "--name-status", "--no-renames", base, sha)
    else:
        changed = git(workspace, "diff-tree", "--no-commit-id", "--name-status", "--no-renames",
                      "-r", "--root", sha)
    kind, message = _parse_subject(meta[2])
    created = datetime.fromtimestamp(int(meta[1]), timezone.utc).isoformat().replace("+00:00", "Z")
    return {"sha": meta[0], "kind": kind, "message": message, "created_at": created,
            "files": _name_status(changed)}


def checkpoint(workspace: Path, kind: str, message: str, allow_empty: bool = False) -> dict | None:
    """Commit everything in the workspace. Returns None when there is nothing to commit."""
    git(workspace, "add", "-A")
    if not allow_empty and not git(workspace, "status", "--porcelain").strip():
        return None
    subject = " ".join(message.split())[:200] or kind
    git(workspace, "commit", "-q", "--allow-empty", "-m", f"[{kind}] {subject}")
    return describe(workspace, "HEAD")


def _since(workspace: Path, base: str | None) -> list[str]:
    """Revision arguments for "HEAD and its history back to `base`, inclusive"."""
    if not base:
        return ["HEAD"]
    parent = git(workspace, "rev-parse", "-q", "--verify", f"{base}^", check=False).strip()
    return ["HEAD", f"^{parent}"] if parent else ["HEAD"]


def versions(workspace: Path, limit: int = 500, since: str | None = None) -> list[dict]:
    """Newest first. `since`: an attached app's import commit; nothing older is listed."""
    out = git(workspace, "log", f"-n{limit}", "--format=%x1e%H%x1f%ct%x1f%s%x1f",
              "--name-status", "--no-renames", "--root", *_since(workspace, since))
    result = []
    for record in out.split("\x1e")[1:]:
        sha, stamp, subject, changed = record.split("\x1f", 3)
        kind, message = _parse_subject(subject)
        created = datetime.fromtimestamp(int(stamp), timezone.utc).isoformat().replace("+00:00", "Z")
        result.append({"sha": sha, "kind": kind, "message": message, "created_at": created,
                       "files": _name_status(changed)})
    return result


def resolve_commit(workspace: Path, sha: str, since: str | None = None) -> str | None:
    """The full sha, or None. With `since` (an attached app's import commit), a commit that is
    not that one or a descendant of it does not exist as far as the studio is concerned."""
    if not SHA_RE.match(sha or ""):
        return None
    full = git(workspace, "rev-parse", "-q", "--verify", f"{sha}^{{commit}}", check=False).strip() or None
    if full and since and full != since:
        proc = git_ok(workspace, "merge-base", "--is-ancestor", since, full)
        if not proc:
            return None
    return full


def git_ok(workspace: Path, *args: str) -> bool:
    """True when the git command exits 0 (for yes/no questions such as merge-base --is-ancestor)."""
    try:
        git(workspace, *args)
    except GitError:
        return False
    return True


def diff(workspace: Path, sha: str, cap: int = 200_000) -> dict:
    text = git(workspace, "show", "--format=", "--no-renames", "--root", sha)
    return {"sha": sha, "diff": text[:cap], "truncated": len(text) > cap}


def restore(workspace: Path, sha: str) -> dict:
    """New commit whose tree equals `sha`. Uncommitted work is checkpointed first, never lost."""
    checkpoint(workspace, "user", "Changes before restore")
    git(workspace, "read-tree", "--reset", "-u", sha)
    return checkpoint(workspace, "restore", f"Restore version {sha[:7]}", allow_empty=True)


def user_changes_since_agent(workspace: Path) -> list[dict]:
    """Files changed by `user`/`restore` checkpoints after the agent's last checkpoint."""
    log = git(workspace, "log", "-n200", "--format=%H%x1f%s").splitlines()
    touched = False
    for line in log:
        sha, _, subject = line.partition("\x1f")
        kind = _parse_subject(subject)[0]
        if kind in ("agent", "init"):
            if not touched:
                return []
            return _name_status(git(workspace, "diff", "--name-status", "--no-renames", sha, "HEAD"))
        touched = True
    return []


def page_to_file(workspace: Path, url_path: str) -> str:
    """Map a preview URL path to a workspace file: `/` → index.html, `/foo/` → foo/index.html."""
    from urllib.parse import unquote
    clean = unquote(str(url_path or "/").split("?")[0].split("#")[0]).strip("/")
    if not clean:
        return "index.html"
    if str(url_path).split("?")[0].split("#")[0].endswith("/") or (workspace / clean).is_dir():
        return f"{clean}/index.html"
    return clean


def apply_text_edit(source: str, before: str, after: str, tag: str | None = None) -> tuple[str | None, int]:
    """Replace `before` with `after` if it occurs exactly once. → (new source or None, occurrences).

    Tries the raw text, then its HTML-escaped form, then the escaped form with any run of
    whitespace matching any other (source files wrap lines; rendered text does not).
    The replacement is HTML-escaped: the user typed text, not markup.
    """
    replacement = html.escape(after, quote=False)
    escaped = html.escape(before, quote=False)
    # When the edited element's tag is known, prefer the one place where that text is the
    # whole content of such an element (a heading's text often repeats in <title>).
    if tag and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", tag) and escaped.split():
        body = r"\s+".join(re.escape(word) for word in escaped.split())
        scoped = re.compile(rf"(<{tag}\b[^>]*>\s*){body}(\s*</{tag}\s*>)", re.IGNORECASE)
        if len(scoped.findall(source)) == 1:
            return scoped.sub(lambda m: m.group(1) + replacement + m.group(2), source), 1
    for needle in dict.fromkeys((before, escaped)):
        count = source.count(needle) if needle else 0
        if count == 1:
            return source.replace(needle, replacement), 1
        if count > 1:
            return None, count
    words = escaped.split()
    if not words:
        return None, 0
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words))
    count = len(pattern.findall(source))
    return (pattern.sub(lambda _match: replacement, source), 1) if count == 1 else (None, count)
