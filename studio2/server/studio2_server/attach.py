"""Bring an app by its folder (CONTRACT.md, "Bring an app by its folder").

`inspect` reads a folder and says what the New page dialog shows; `make_copy` and
`start_history` create a project's workspace from it. Nothing here writes to the original
folder: git commands on it run with GIT_OPTIONAL_LOCKS=0 (so not even the index is refreshed)
and only read; a copy is made by `git clone` or by reading files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from . import keyfiles, workspace as ws

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", ".nuxt", ".svelte-kit", ".astro",
             ".turbo", ".vite", ".parcel-cache", ".angular", ".cache", "dist", "build", ".output", "target",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", ".DS_Store"}
COUNT_LIMIT = 50_000
GIT_LIMIT_BYTES, GIT_LIMIT_FILES = 2 * 1024 ** 3, 50_000
FOLDER_LIMIT_BYTES, FOLDER_LIMIT_FILES = 500 * 1024 ** 2, 20_000
UNCOMMITTED_LIMIT = 200
# Key files (keyfiles.py, one rule for the whole studio) are kept out of every version, in a copy
# and in place, and out of a patch of uncommitted changes taken into a copy.
ENV_EXCLUDED = keyfiles.GIT_PATTERNS
ENV_PATHSPEC = keyfiles.PATHSPEC_EXCLUDES
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ALWAYS_EXCLUDED = ["node_modules/", ".venv/", "__pycache__/"]
BUILD_EXCLUDED = [".next/", ".nuxt/", ".svelte-kit/", ".astro/", ".turbo/", ".vite/", ".parcel-cache/",
                  ".angular/", ".cache/"]
# Where Node may live when it is not on PATH. The first is the private copy install.sh fetches
# (studio2/.tools/node) on a machine that had no Node, or too old a one.
NODE_HOMES = (str(Path(__file__).resolve().parents[2] / ".tools" / "node" / "bin"),
              "~/.local/opt/node24/bin", "~/.local/opt/node22/bin", "~/.local/opt/node20/bin",
              "/opt/homebrew/bin", "/usr/local/bin")


class AttachError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# -- reading the original ------------------------------------------------------------------
def _git_read(folder: Path, *args: str) -> tuple[int, str]:
    """A read-only git command on the person's folder. Never writes, never prompts."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=folder, env=env,
                          capture_output=True, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def git_info(folder: Path) -> dict | None:
    """Branch, head and uncommitted changes, or None when the folder is not the top of a work tree."""
    code, top = _git_read(folder, "rev-parse", "--show-toplevel")
    if code != 0 or Path(top.strip()).resolve() != folder.resolve():
        return None
    _code, head = _git_read(folder, "rev-parse", "-q", "--verify", "HEAD")
    _code, branch = _git_read(folder, "symbolic-ref", "-q", "--short", "HEAD")
    _code, subject = _git_read(folder, "log", "-1", "--format=%s")
    _code, status = _git_read(folder, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    changes = []
    entries = status.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code2, path = entry[:2], entry[3:]
        if code2[0] in "RC":   # a rename carries its old name in the next entry
            index += 1
        kind = ("untracked" if code2 == "??" else "added" if "A" in code2 else
                "deleted" if "D" in code2 else "modified")
        changes.append({"path": path, "status": kind})
    return {"branch": branch.strip() or None, "head": head.strip() or None, "subject": subject.strip(),
            "uncommitted": changes[:UNCOMMITTED_LIMIT], "uncommitted_more": max(0, len(changes) - UNCOMMITTED_LIMIT)}


def is_env(name: str) -> bool:
    """A key file (an env file and the like), at any depth: copied into a project only when ticked,
    never versioned."""
    return keyfiles.is_key_file(name)


def _tracked(folder: Path) -> list[str]:
    _code, out = _git_read(folder, "ls-files", "-z")
    return [name for name in out.split("\0") if name]


def _plain_files(folder: Path) -> tuple[list[str], bool]:
    """A plain folder's files that its .gitignore keeps, without dependency and build folders.
    Uses a throwaway git directory in the temp folder with the folder as work tree: git reads
    the .gitignore files and writes nothing beside them."""
    with tempfile.TemporaryDirectory(prefix="studio2-inspect-") as scratch:
        git_dir = Path(scratch) / "g"
        subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], capture_output=True, check=False)
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("".join(f"{name}/\n" for name in sorted(SKIP_DIRS)), "utf-8")
        env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(folder),
               "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        proc = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z", "--others",
                               "--exclude-standard"], cwd=folder, env=env, capture_output=True, timeout=120)
    names = [name for name in proc.stdout.decode("utf-8", "replace").split("\0") if name]
    names = [name for name in names if not SKIP_DIRS.intersection(name.split("/"))]
    return names[:COUNT_LIMIT], len(names) > COUNT_LIMIT


def _sizes(folder: Path, names: list[str]) -> int:
    total = 0
    for name in names:
        try:
            total += (folder / name).lstat().st_size
        except OSError:
            continue
    return total


def _folder_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


# -- what kind of app ----------------------------------------------------------------------
def _package(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / "package.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _deps(package: dict) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        if isinstance(package.get(key), dict):
            names.update(package[key])
    return names


NODE_KINDS = [  # first match wins: a Nuxt or SvelteKit app also depends on vite
    ("next", "Next.js", "next", "exec npx next dev -p $PORT -H 127.0.0.1"),
    ("nuxt", "Nuxt", "nuxt", "exec npx nuxt dev --port $PORT --host 127.0.0.1"),
    ("sveltekit", "SvelteKit", "@sveltejs/kit", "exec npx vite dev --port $PORT --host 127.0.0.1 --strictPort"),
    ("astro", "Astro", "astro", "exec npx astro dev --port $PORT --host 127.0.0.1"),
    ("angular", "Angular", "@angular/core", "exec npx ng serve --port $PORT --host 127.0.0.1"),
    ("vite", "Vite", "vite", "exec npx vite --port $PORT --host 127.0.0.1 --strictPort"),
]


def _node_install(folder: Path) -> tuple[str | None, str | None]:
    """→ (package manager, install command) for a Node part, or (None, None)."""
    for lock, manager, command in (("package-lock.json", "npm", "npm ci"),
                                   ("pnpm-lock.yaml", "pnpm", "pnpm install --frozen-lockfile"),
                                   ("yarn.lock", "yarn", "yarn install --frozen-lockfile"),
                                   ("bun.lock", "bun", "bun install"), ("bun.lockb", "bun", "bun install")):
        if (folder / lock).is_file():
            return manager, command
    if (folder / "package.json").is_file():
        return "npm", "npm install"
    return None, None


def _project_table(folder: Path) -> bool:
    """True when pyproject.toml has a [project] table, which `uv sync` can install from."""
    try:
        text = (folder / "pyproject.toml").read_text("utf-8", "replace")[:200_000]
    except OSError:
        return False
    return re.search(r"^\s*\[project\]\s*(#.*)?$", text, re.M) is not None


def _python_install(folder: Path) -> tuple[str | None, str | None]:
    """→ (install command, the Python that install leaves the packages in) for a Python part.

    The interpreter is said here, beside the command that makes it, because at inspect time the
    folder has no `.venv` yet: the start command must name the environment the install will make."""
    if (folder / "uv.lock").is_file():
        return "uv sync", "uv run python"
    if (folder / "requirements.txt").is_file():
        return "uv venv -q && uv pip install -q -r requirements.txt", ".venv/bin/python"
    if _project_table(folder):
        return "uv sync", "uv run python"
    return None, None


def _python_source(folder: Path) -> str | None:
    """The file the Python install reads, as the notes name it."""
    for name in ("uv.lock", "requirements.txt"):
        if (folder / name).is_file():
            return name
    return "pyproject.toml" if _project_table(folder) else None


# A start command that is Python: evidence that the folder's Python packages are needed.
PYTHON_RUN = re.compile(r"^\s*(?:exec\s+)?(?:[A-Z_]+=\S*\s+)*(?:python3?(?:\.\d+)?|uv\s+run|\.venv/bin/\S+|"
                        r"uvicorn|gunicorn|flask)(?:\s|$)")
PYTHON_FAILED = ('echo "[studio] The Python packages could not be installed (the lines above say why). '
                 'The app starts without them, so its Python part will not work until they are."')


def _is_python_run(run: str | None) -> bool:
    return bool(run and PYTHON_RUN.match(run))


def _install(folder: Path, run: str | None, python_app: bool) -> tuple[str | None, str | None, str | None, str | None]:
    """→ (package manager, install command, the Python the install makes, the Python file left
    uninstalled). CONTRACT.md, "Install commands".

    A Python part beside a Node part is installed only on evidence that a Python app needs it (a
    Django, FastAPI or Flask app in the folder, or a start command that is Python); otherwise it is
    docs or tooling, and installing it could only stop the Node app. When a Node app is what
    starts, a failed Python install does not stop it. A folder with no Node part keeps its
    `uv.lock` or `requirements.txt` install as it always had; a `[project]` table alone needs
    the same evidence, since a pyproject.toml is as often a tool's settings as an app."""
    manager, node = _node_install(folder)
    python, made = _python_install(folder)
    evidence = python_app or _is_python_run(run)
    skipped = None
    if python and not evidence and (node or _python_source(folder) == "pyproject.toml"):
        skipped = _python_source(folder) if node else None
        python = made = None
    if node and python:
        node = _needs_path(node)
        command = f"{node} && {python}" if _is_python_run(run) else f"{node} && ( {python} || {PYTHON_FAILED} )"
        return manager, command, made, None
    if node or python:
        return manager or "uv", _needs_path(node) if node else python, made, skipped
    if (folder / "Gemfile").is_file():
        return "bundler", "bundle install", None, None
    if (folder / "composer.json").is_file():
        return "composer", "composer install", None, None
    return None, None, None, None


def install_for(folder: Path, run: str | None) -> str | None:
    """The install for the start command a person typed instead of the one found."""
    return _install(folder, run, _python_app_kind(folder) is not None)[1]


def _python(folder: Path, made: str | None) -> str:
    """The interpreter for a Python app's start command: the one the install makes, when it makes one."""
    if made:
        return made
    if (folder / "uv.lock").is_file():
        return "uv run python"           # uv run makes the environment from the lock by itself
    if (folder / ".venv").is_dir():
        return ".venv/bin/python"
    return "python3"


def _python_app(folder: Path, maker: str) -> tuple[str, str] | None:
    """→ (module, variable) for the first top-level file that makes `<name> = maker(`."""
    pattern = re.compile(rf"^(\w+)\s*=\s*{maker}\(", re.M)
    for name in ("main.py", "app.py", "server.py", "api.py", "wsgi.py"):
        try:
            match = pattern.search((folder / name).read_text("utf-8", "replace")[:200_000])
        except OSError:
            continue
        if match:
            return name[:-3], match.group(1)
    return None


def _needs_path(command: str) -> str:
    """Prefix a Node command with the folder of a node the studio's own PATH would not find."""
    if "npx " not in command and not command.startswith(("npm ", "pnpm ", "yarn ", "bun ")):
        return command
    if shutil.which("npx"):
        return command
    for home in NODE_HOMES:
        folder = Path(home).expanduser()
        if (folder / "npx").exists():
            # `PATH=… exec npx …` and `PATH=… npm ci` both work in sh: the assignment applies to the command.
            return f"PATH={folder}:$PATH {command}"
    return command


def _detect(folder: Path) -> tuple[dict, str | None, str | None, str | None]:
    """→ (what `detect` says, the label of a Python back end found but not started, the Python
    the started Python app runs from or None, the Python file whose packages are not installed)."""
    package = _package(folder)
    found: tuple[str, str] | None = None
    run: str | None = None
    if package is not None:
        deps = _deps(package)
        for kind, label, dep, command in NODE_KINDS:
            if dep in deps:
                found, run = (kind, label), command
                break
    app = _python_app_kind(folder)
    # A found Python app is evidence for its install, so the interpreter it starts from is the
    # one that install makes.
    made = _install(folder, None, app is not None)[2]
    python = _python(folder, made)
    backend = (app[0], app[1].format(python=python)) if app else None
    if found is None:
        if backend:
            found, run = backend
        elif (folder / "Gemfile").is_file() and (folder / "bin" / "rails").is_file() and \
                "rails" in (folder / "Gemfile").read_text("utf-8", "replace"):
            found, run = ("rails", "Rails"), "exec bin/rails server -b 127.0.0.1 -p $PORT"
        elif (folder / "artisan").is_file():
            found, run = ("laravel", "Laravel"), "exec php artisan serve --host=127.0.0.1 --port=$PORT"
        elif (folder / "index.html").is_file() and package is None:
            found = ("html", "Plain HTML")
    if run:
        run = _needs_path(run)
    manager, install, _made, skipped = _install(folder, run, app is not None)
    found_dict = {"framework": {"id": found[0], "label": found[1]} if found else None, "run": run,
                  "package_manager": manager, "install": install}
    started = backend is not None and backend[1] == run
    return found_dict, (backend[0][1] if backend and not started else None), (python if started else None), skipped


def detect(folder: Path) -> dict:
    """→ {framework, run, package_manager, install}; nulls where nothing matched."""
    return _detect(folder)[0]


def _python_app_kind(folder: Path) -> tuple[tuple[str, str], str] | None:
    """→ ((id, label), run with a `{python}` slot) for a Django, FastAPI or Flask app, or None."""
    if (folder / "manage.py").is_file():
        return ("django", "Django"), "exec {python} manage.py runserver 127.0.0.1:$PORT"
    if (made := _python_app(folder, "FastAPI")):
        return ("fastapi", "FastAPI"), (f"exec {{python}} -m uvicorn {made[0]}:{made[1]} "
                                        "--host 127.0.0.1 --port $PORT --reload")
    if (made := _python_app(folder, "Flask")):
        return ("flask", "Flask"), f"exec {{python}} -m flask --app {made[0]} run --host 127.0.0.1 --port $PORT --debug"
    return None


def _workspaces(folder: Path, package: dict | None) -> bool:
    """True for a monorepo: npm, yarn or bun workspaces, or a pnpm workspace file."""
    return bool((package or {}).get("workspaces")) or (folder / "pnpm-workspace.yaml").is_file()


# -- refusals ------------------------------------------------------------------------------
def _related(a: Path, b: Path) -> bool:
    """True when one folder is the other or contains it."""
    return a == b or a in b.parents or b in a.parents


def _too_big_to_look(folder: Path, protected: list[Path], home: Path) -> str | None:
    if folder == Path(folder.anchor) or os.path.ismount(folder):
        return "This is the top of a disk; choose the app's own folder."
    if folder == home.resolve():
        return "This is your home folder; choose the app's own folder."
    for item in protected:
        if folder in item.resolve().parents:   # a folder that holds the studio (not one inside it)
            return "This folder holds the studio's own files; choose the app's own folder."
    return None


def in_place_refusal(folder: Path, protected: list[Path], home: Path, taken: set[Path]) -> str | None:
    real = folder.resolve()
    if real == Path(real.anchor) or os.path.ismount(real):
        return "This is the top of a disk; choose the app's own folder."
    if real == home.resolve():
        return "This is your home folder; choose the app's own folder."
    for item in protected:
        if _related(real, item.resolve()):
            return ("This folder holds the studio's own files or is inside them; the studio cannot "
                    "edit itself in place.")
    if real in taken:
        return "Another project already edits this folder in place."
    if not (os.access(real, os.R_OK | os.X_OK) and os.access(real, os.W_OK)):
        return "The studio cannot both read and write this folder."
    return None


def copy_refusal(folder: Path, info: dict | None, size: dict, protected: list[Path], home: Path) -> str | None:
    real = folder.resolve()
    if real == Path(real.anchor):
        return "This is the top of a disk; choose the app's own folder."
    if real == home.resolve():
        return "This is your home folder; choose the app's own folder."
    for item in protected:
        if _related(real, item.resolve()):
            return "This folder holds the studio's own files or is inside them."
    if info is not None:
        if not info.get("head"):
            return "This git folder has no commits yet; commit once, or copy it as files."
        if size["bytes"] + size["history_bytes"] > GIT_LIMIT_BYTES or size["files"] > GIT_LIMIT_FILES:
            return ("This app and its history are larger than a safe copy takes "
                    f"({GIT_LIMIT_BYTES // 1024 ** 3} GB or {GIT_LIMIT_FILES:,} files).")
    elif size["partial"] or size["bytes"] > FOLDER_LIMIT_BYTES or size["files"] > FOLDER_LIMIT_FILES:
        return ("This folder is larger than a safe copy takes "
                f"({FOLDER_LIMIT_BYTES // 1024 ** 2} MB or {FOLDER_LIMIT_FILES:,} files, without "
                "node_modules and build output). Edit it in place, or point at a smaller folder.")
    return None


def inspect(path: str, protected: list[Path], home: Path, taken: set[Path]) -> dict:
    raw = os.path.expanduser(str(path or "").strip())
    if not raw or not os.path.isabs(raw):
        raise AttachError("invalid_request", "Type the folder's full path, for example ~/dev/my-app.")
    folder = Path(os.path.normpath(raw))
    result: dict = {"path": str(folder), "name": folder.name or str(folder), "exists": folder.is_dir(),
                    "readable": False, "writable": False, "git": None, "framework": None, "run": None,
                    "package_manager": None, "install": None, "env_files": [], "env_links": [],
                    "size": {"files": 0, "bytes": 0, "history_bytes": 0, "partial": False},
                    "refused": {"copy": None, "in_place": None}, "notes": []}
    if not result["exists"]:
        reason = "There is no folder at this path." if not folder.exists() else "This path is a file, not a folder."
        result["refused"] = {"copy": reason, "in_place": reason}
        return result
    result["readable"] = os.access(folder, os.R_OK | os.X_OK)
    result["writable"] = os.access(folder, os.W_OK)
    if not result["readable"]:
        reason = "The studio cannot read this folder."
        result["refused"] = {"copy": reason, "in_place": reason}
        return result
    folder = folder.resolve()
    result["path"] = str(folder)
    # A disk, the home folder or the studio itself: say no before reading (and sizing) all of it.
    early = _too_big_to_look(folder, protected, home)
    if early:
        result["refused"] = {"copy": early, "in_place": early}
        return result
    info = git_info(folder)
    result["git"] = info
    found, backend, python, skipped = _detect(folder)
    result.update(found)
    try:
        keys = [entry for entry in folder.iterdir() if entry.is_file() and keyfiles.is_key_name(entry.name)]
        # A link is never copied (it could point anywhere), so it is not offered for ticking.
        result["env_files"] = sorted(entry.name for entry in keys if not entry.is_symlink())
        result["env_links"] = sorted(entry.name for entry in keys if entry.is_symlink())
    except OSError:
        pass
    if info is not None:
        names = _tracked(folder)
        result["size"] = {"files": len(names), "bytes": _sizes(folder, names),
                          "history_bytes": _folder_bytes(folder / ".git") if (folder / ".git").is_dir() else 0,
                          "partial": False}
    else:
        names, partial = _plain_files(folder)
        result["size"] = {"files": len(names), "bytes": _sizes(folder, names), "history_bytes": 0, "partial": partial}
    result["refused"] = {"copy": copy_refusal(folder, info, result["size"], protected, home),
                         "in_place": in_place_refusal(folder, protected, home, taken)}
    notes = result["notes"]
    if result["framework"] is None:
        notes.append("The studio did not recognise how this app starts. Create the project anyway and ask "
                     "the agent to set it up, or type the start command.")
    elif result["framework"]["id"] == "html":
        notes.append("Plain HTML: the studio serves the files as they are.")
    if backend:
        label = result["framework"]["label"]
        notes.append(f"This folder also has a {backend} back end. Its packages are installed after the Node ones "
                     f"(if that fails, the {label} app still starts, and its log says why), but the studio starts "
                     f"one command, the {label} server, so the back end is not started. To run both, type a "
                     "start command that starts both (the back end on a port other than $PORT), or ask the agent "
                     "to set it up.")
    if skipped:
        notes.append(f"This folder also has a {skipped}, but no Python app the studio could find (no manage.py, and "
                     "no FastAPI or Flask app in main.py, app.py, server.py, api.py or wsgi.py), so it is taken as "
                     "docs or tooling: only the Node packages are installed, and its Python packages are not "
                     "installed. If the app needs them, type a start command that runs Python, or ask the agent.")
    if _workspaces(folder, _package(folder)):
        notes.append("This folder holds several packages (workspaces). The studio looks only at the top of the "
                     "folder: it installs there and does not choose one of the packages to start. Type the start "
                     "command, or ask the agent to set it up.")
    if python == ".venv/bin/python" and not _python_install(folder)[0]:
        notes.append("A safe copy of this app will not start. Its packages are only in its .venv folder, which a "
                     "copy never takes, and nothing in the folder lists them (no requirements.txt, uv.lock, or "
                     "pyproject.toml with a [project] table), so the copy cannot make them again. Edit it in place, "
                     "or add a requirements.txt (in the folder: uv pip freeze --python .venv > requirements.txt) "
                     "and check again.")
    if info and info["uncommitted"]:
        count = len(info["uncommitted"]) + info["uncommitted_more"]
        notes.append(f"{count} file{'s' if count != 1 else ''} changed here but not committed. A safe copy "
                     "starts from the last commit unless you include them.")
    if result["env_files"]:
        notes.append("Env files are copied only if you tick them, and never kept in versions.")
    if result["env_links"]:
        names = ", ".join(result["env_links"])
        notes.append(f"{names} {'is a link' if len(result['env_links']) == 1 else 'are links'}, which the studio "
                     "never copies, so a copy starts without it. Copy the file itself into the project if the "
                     "app needs it.")
    return result


def check_address(address: str) -> str:
    """A loopback http address with a port, normalised; AttachError otherwise."""
    text = str(address or "").strip()
    parts = urlsplit(text if "://" in text else f"http://{text}")
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != "http" or host not in LOOPBACK or not port:
        raise AttachError("address_refused",
                          "Only an address on this computer is shown: http://localhost:<port>, "
                          "http://127.0.0.1:<port> or http://[::1]:<port>. The studio passes every request "
                          "to it, and passing them to another machine would let a page reach that machine.")
    shown = f"[{host}]" if host == "::1" else host
    return f"http://{shown}:{port}"


# -- making the project's workspace --------------------------------------------------------
def _exclude(git_dir: Path, names: list[str]) -> None:
    target = git_dir / "info" / "exclude"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text("utf-8") if target.is_file() else ""
    lines = [line for line in names if line not in existing.splitlines()]
    target.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "")
                      + "".join(f"{line}\n" for line in lines), "utf-8")


def _copy_env(original: Path, workspace: Path, env_files: list[str], allowed: list[str]) -> tuple[list[str], list[dict]]:
    """→ (copied, skipped): every ticked name that was not copied is said, with why."""
    copied, skipped = [], []
    for name in env_files:
        source = original / name
        if "/" in name or not keyfiles.is_key_name(name):
            reason = "not a key file at the top of the folder"
        elif source.is_symlink():
            reason = "it is a link, and the studio never copies links"
        elif name not in allowed or not source.is_file():
            reason = "not found in the folder"
        else:
            shutil.copyfile(source, workspace / name)
            copied.append(name)
            continue
        skipped.append({"name": name, "reason": reason})
    return copied, skipped


def _take_uncommitted(original: Path, workspace: Path) -> None:
    """The original's uncommitted changes and untracked files, applied to the copy. Env files are
    left out: only the ticked ones are copied (`_copy_env`), and those are kept out of versions."""
    _code, patch = _git_read(original, "diff", "--binary", "HEAD", "--", ".", *ENV_PATHSPEC)
    if patch.strip():
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=workspace, input=patch.encode(),
                              capture_output=True)
        if proc.returncode != 0:
            raise AttachError("copy_failed", "The uncommitted changes could not be applied to the copy: "
                              + proc.stderr.decode("utf-8", "replace").strip()[:300])
    _code, out = _git_read(original, "ls-files", "-z", "--others", "--exclude-standard")
    for name in (n for n in out.split("\0") if n):
        source = original / name
        if source.is_file() and not source.is_symlink() and not SKIP_DIRS.intersection(name.split("/")) \
                and not is_env(name):
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def make_copy(original: Path, workspace: Path, branch: str, studio_json: dict | None, env_files: list[str],
              allowed_env: list[str], include_uncommitted: bool) -> dict:
    """Create a safe copy at `workspace` (which must not exist). → facts for the project record."""
    info = git_info(original)
    if info is not None:
        proc = subprocess.run(["git", "clone", "-q", "--no-hardlinks", "--no-checkout", str(original), str(workspace)],
                              capture_output=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=900)
        if proc.returncode != 0:
            raise AttachError("copy_failed", "git could not copy this folder: "
                              + proc.stderr.decode("utf-8", "replace").strip()[:300])
        # Work leaves the copy by being fetched from it (step 3l); nothing in it can push back.
        ws.git(workspace, "remote", "remove", "origin")
        ws.git(workspace, "checkout", "-q", "-b", branch, info["head"])
        if include_uncommitted:
            _take_uncommitted(original, workspace)
    else:
        names, _partial = _plain_files(original)
        workspace.mkdir(parents=True)
        ws.git(workspace, "init", "-q")
        for name in names:
            source = original / name
            if source.is_symlink() or not source.is_file() or is_env(name):
                continue        # key files come only through `_copy_env`, when ticked
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    copied, skipped = _copy_env(original, workspace, env_files, allowed_env)
    excluded = [*ALWAYS_EXCLUDED, *ENV_EXCLUDED, *copied]
    if studio_json is not None:
        (workspace / "studio.json").write_text(json.dumps(studio_json, indent=2) + "\n", "utf-8")
        excluded.append("studio.json")
    _exclude(workspace / ".git", excluded)
    started = (info or {}).get("head")
    label = f"Import from {original}" + (f" at {started[:7]}" if started else "")
    first = ws.checkpoint(workspace, "init", label, allow_empty=True)
    return {"kind": "git" if info else "folder", "branch": (info or {}).get("branch"), "started_from": started,
            "studio_branch": branch if info else None, "import_commit": first["sha"],
            "uncommitted": (info or {}).get("uncommitted") or [], "env_files": copied,
            "env_skipped": skipped}


def start_history(folder: Path, git_dir: Path) -> dict:
    """Begin the studio's own history of a folder edited in place. → facts for the record."""
    info = git_info(folder)
    proc = subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], capture_output=True)
    if proc.returncode != 0:
        raise AttachError("copy_failed", "The studio could not start its history for this folder.")
    _exclude(git_dir, [*ALWAYS_EXCLUDED, *ENV_EXCLUDED, *BUILD_EXCLUDED])
    ws.register_history(folder, git_dir)
    first = ws.checkpoint(folder, "init", f"Start editing {folder} in place", allow_empty=True)
    return {"kind": "git" if info else "folder", "branch": (info or {}).get("branch"),
            "started_from": (info or {}).get("head"), "studio_branch": None, "import_commit": first["sha"],
            "uncommitted": (info or {}).get("uncommitted") or [], "env_files": [], "env_skipped": []}
