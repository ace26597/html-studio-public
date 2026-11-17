"""Which shell commands the agent must ask about first (CONTRACT.md, "Approvals").

Pattern matching on the command text. It is a seat belt, not a sandbox: it catches the
ordinary ways an agent reaches outside its project, not a determined attempt to hide one.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import workspace as ws

SEP = r"(?:^|[;&|(){}\n]|\$\(|`)\s*(?:(?:env\s+)?(?:\w+=\S+\s+)*)?(?:command\s+|exec\s+|nohup\s+|time\s+)*"


def _cmd(names: str) -> re.Pattern:
    return re.compile(SEP + rf"(?:\S*/)?(?:{names})(?=\s|$)", re.I)


RULES: list[tuple[re.Pattern, str]] = [
    (_cmd("sudo|doas|su"), "It runs with administrator rights."),
    (re.compile(r"\bplaywright\b[^\n;&|]*\binstall\b|\bpuppeteer\b[^\n;&|]*\binstall\b|"
                r"@puppeteer/browsers|chrome-for-testing|\binstall[- ]chrom", re.I),
     "It downloads a browser onto this machine."),
    (_cmd(r"brew|port|apt|apt-get|dnf|yum|pacman|snap|mas") , "It installs or changes software on this machine."),
    (re.compile(r"\b(?:npm|pnpm|yarn|bun)\b[^\n;&|]*(?:\s-g\b|\s--global\b|\bglobal\s+add\b)", re.I),
     "It installs a package for the whole machine, not just this project."),
    (re.compile(r"\b(?:uv\s+tool|pipx|cargo|go)\s+install\b|\bgem\s+install\b", re.I),
     "It installs a tool for the whole machine, not just this project."),
    (re.compile(SEP + r"(?:\S*/)?git(?:\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace)\s+\S+|-\S+))*\s+push(?=\s|$)|"
                + SEP + r"(?:\S*/)?gh(?=\s|$)", re.I),
     "It sends something to GitHub."),
    (re.compile(r"\b(?:curl|wget)\b[^\n;&]*\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b|"
                r"\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b", re.I),
     "It downloads a script and runs it straight away."),
    (_cmd(r"kill|pkill|killall|pm2|launchctl|crontab|systemctl|service|shutdown|reboot|osascript"),
     "It controls other programs or services on this machine."),
    (_cmd(r"ssh|scp|sftp|rsync\s+[^\n;&|]*\S+:\S*|nc|ncat"), "It connects to another machine."),
    (_cmd(r"docker|podman|kubectl"), "It controls containers on this machine."),
    (_cmd(r"defaults\s+write|chflags|diskutil|dd|mkfs\S*"), "It changes system settings or disks."),
]
PIP = re.compile(r"(?:^|[;&|(\n]\s*)(?P<bin>\S*?)(?:pip3?|python3?(?:\.\d+)?\s+-m\s+pip)\s+install\b(?P<rest>[^\n;&|]*)", re.I)
MUTATORS = {"rm", "rmdir", "mv", "cp", "ln", "chmod", "chown", "touch", "mkdir", "tee", "truncate",
            "install", "unlink", "shred", "sed", "rsync", "unzip", "tar"}
REDIRECT = re.compile(r"(?<![0-9&>])(?:>>?|&>>?)\|?\s*(\S+)")     # `>|` writes like `>`
SAFE_ABSOLUTE = ("/dev/null", "/dev/stdout", "/dev/stderr", "/tmp/", "/private/tmp/", "/var/folders/", "/dev/fd/")


DELETERS = {"rm", "rmdir", "unlink", "shred"}
# A substitution or variable followed somewhere by `..`: `$(pwd)/..` can climb anywhere.
CLIMBS = re.compile(r"(?:\$\(|`|\$\{?\w).*(?:^|/)\.\.(?:/|$)")
SCRATCH = Path("/nonexistent-scratch")     # `here` after a `cd` to a scratch folder such as /tmp


def _home(token: str) -> Path | None:
    """`~`, `~/x`, `$HOME/x`, `${HOME}/x` → the path in the home folder; `~other/x` → "/" (another
    person's home: never inside); anything else → None."""
    for prefix in ("~", "$HOME", "${HOME}"):
        if token == prefix or token.startswith(prefix + "/"):
            return Path.home() / token[len(prefix):].lstrip("/")
    if token.startswith("~"):
        return Path("/")
    return None


def _outside(token: str, workspace: Path, here: Path | None = None) -> bool:
    """Is `token` a path outside the project? Relative paths are read from `here`, where an
    earlier `cd` on the same line left the command (default: the project folder)."""
    token = token.strip("'\"")
    if not token or token.startswith("-") or "://" in token:
        return False
    if CLIMBS.search(token):
        return True
    home = _home(token)
    if home is not None:     # judged by where it leads: an app can live under the home folder
        resolved = home.resolve()
        return workspace.resolve() not in (resolved, *resolved.parents)
    if here is SCRATCH and not token.startswith("/"):
        return False
    if here is not None and not token.startswith("/"):
        resolved = (here / token).resolve()
        return workspace.resolve() not in (resolved, *resolved.parents)
    if token == ".." or token.startswith("../") or "/../" in token:
        resolved = (workspace / token).resolve()
        return workspace.resolve() not in (resolved, *resolved.parents)
    if token.startswith("/"):
        if token in ("/dev/null",) or token.startswith(SAFE_ABSOLUTE):
            return False
        resolved, root = Path(token).resolve(), workspace.resolve()
        return root not in (resolved, *resolved.parents)
    return False


def _pip_reason(command: str) -> str | None:
    for match in PIP.finditer(command):
        binary, rest = match.group("bin"), match.group("rest")
        in_venv = "venv" in binary or "VIRTUAL_ENV" in command or re.search(r"\b(?:source|\.)\s+\S*activate\b", command)
        if not in_venv and "--target" not in rest and not re.search(r"(?:^|\s)-t\s", rest):
            return "It installs Python packages outside a virtual environment."
    return None


def _mask(text: str) -> str:
    """The text with everything inside quotes (and each backslash-escaped character) replaced by
    "_", same length, quote marks kept: what is left is what the shell itself reads as operators."""
    out, i, quote = list(text), 0, None
    while i < len(text):
        char = text[i]
        if quote is None:
            if char == "\\" and i + 1 < len(text):
                out[i + 1] = "_"
                i += 2
                continue
            if char in "'\"":
                quote = char
        elif char == quote:
            quote = None
        elif quote == '"' and char == "\\" and i + 1 < len(text):
            out[i] = out[i + 1] = "_"
            i += 2
            continue
        else:
            out[i] = "_"
        i += 1
    return "".join(out)


def _word_at(text: str, start: int) -> str:
    """The shell word that starts at `start` (a quoted one without its quotes)."""
    if start < len(text) and text[start] in "'\"":
        end = text.find(text[start], start + 1)
        return text[start + 1:end if end != -1 else len(text)]
    match = re.match(r"(?:\$\([^)]*\)|`[^`]*`|[^\s;&|()<>`])+", text[start:])   # `$(pwd)/x` is one word
    return match.group(0) if match else ""


def _parts(command: str) -> list[str]:
    """The command split at `;`, `&`, `|` and new lines that are not inside quotes."""
    masked, parts, at = _mask(command), [], 0
    for match in re.finditer(r"[;&|\n]+", masked):
        parts.append(command[at:match.start()])
        at = match.end()
    parts.append(command[at:])
    return parts


GIT_READS = {"status", "log", "diff", "show", "ls-files", "ls-tree", "rev-parse", "rev-list", "blame", "grep",
             "describe", "shortlog", "cat-file", "show-ref", "for-each-ref", "name-rev", "help", "version",
             "whatchanged"}
# In a project edited in place these reach the person's own repository and can lose work there.
GIT_DISCARDS = {"rebase", "merge", "cherry-pick", "revert", "push", "filter-branch", "filter-repo", "update-ref",
                "prune"}
BRANCH_CHANGES = re.compile(r"-[a-zA-Z]*[dDmMcCf][a-zA-Z]*|--(?:delete|move|copy|force)")
BRANCH_FORCED = re.compile(r"-[a-zA-Z]*[DfMC][a-zA-Z]*|--force")
# Settings with which git runs a program, or reads settings from elsewhere (CONTRACT.md, "Approvals").
GIT_RUNS = re.compile(r"core\.(?:fsmonitor|hookspath|sshcommand|pager|editor|askpass|gitproxy)|filter\..+|"
                      r"diff\.external|diff\..+\.(?:textconv|command)|merge\..+\.driver|alias\..+|credential(?:\..+)?|"
                      r"include\..+|includeif\..+|gpg\..+|url\..+|protocol\..+", re.I)
CONFIG_READS = ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--unset", "--unset-all",
                "--remove-section", "--rename-section", "--show-origin", "--show-scope")
CONFIG_WITH_VALUE = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value", "-t"}
RUNS_A_PROGRAM = "that would let git run a program of the agent's choosing when the studio or you use git here"
XARGS_WITH_VALUE = {"-n", "-I", "-L", "-P", "-d", "-s", "-E", "-a"}


def _git(words: list[str]) -> tuple[list[str], str, list[str]]:
    """`git -C x -c k=v --git-dir=y sub args` → (the folders it names, "sub", args)."""
    folders, i = [], 1
    while i < len(words) and words[i].startswith("-"):
        word = words[i]
        if word in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(words):
            if word != "-c" and word != "--namespace":
                folders.append(words[i + 1])
            i += 2
            continue
        if word.startswith(("--git-dir=", "--work-tree=")):
            folders.append(word.split("=", 1)[1])
        i += 1
    return folders, (words[i] if i < len(words) else ""), words[i + 1:]


def _git_reads(sub: str, args: list[str]) -> bool:
    if sub == "stash":
        return bool(args) and args[0] in ("list", "show")
    if sub == "branch":
        return not any(BRANCH_CHANGES.fullmatch(a) for a in args) and not [a for a in args if not a.startswith("-")]
    return sub in GIT_READS


def _is_path(word: str, base: Path) -> bool:
    """Does `word` name something in the folder (or a glob, or `.`)? Branch names usually do not."""
    return word in (".", "./") or any(c in word for c in "*?[") or (base / word).exists()


def _git_discards(sub: str, args: list[str], base: Path) -> bool:
    """In place: can this git command lose the person's work in their own repository? Where git
    itself refuses to lose anything (`checkout -b`, `switch main` with local changes, `branch -d`
    of an unmerged branch, `pull --ff-only`, `stash`), it does not ask."""
    shorts = "".join(a[1:] for a in args if re.fullmatch(r"-[a-zA-Z]+", a))
    if sub == "stash":
        return bool(args) and args[0] in ("drop", "clear", "pop")
    if sub == "clean":
        return not any(a in ("-n", "--dry-run") for a in args) and "n" not in shorts
    if sub == "commit":
        return "--amend" in args
    if sub == "branch":
        return any(BRANCH_FORCED.fullmatch(a) for a in args)
    if sub == "pull":
        return "--ff-only" not in args
    if sub == "gc":
        return any(a.startswith("--prune") and a != "--prune=never" for a in args)
    if sub == "reflog":
        return bool(args) and args[0] in ("expire", "delete")
    if sub == "tag":
        return any(a in ("-d", "--delete", "-f", "--force") for a in args) or bool(set(shorts) & set("df"))
    if sub == "switch":
        return any(a in ("-C", "--force-create", "-f", "--force", "--discard-changes") for a in args) \
            or bool(set(shorts) & set("Cf"))
    if sub == "restore":
        worktree = "--worktree" in args or "W" in shorts
        staged = "--staged" in args or "S" in shorts
        return worktree or not staged
    if sub == "reset":
        if any(a in ("--hard", "--merge", "--keep") for a in args):
            return True
        plain = [a for a in args if not a.startswith("-")]
        if "--" in args:
            before = args[:args.index("--")]
            plain = [a for a in before if not a.startswith("-")]
            return bool(plain) and plain[0] not in ("HEAD", "@")
        # The first word is a commit unless it is HEAD or a path in the folder: then only the
        # index changes (it unstages). Another commit moves the branch.
        return bool(plain) and plain[0] not in ("HEAD", "@") and not _is_path(plain[0], base)
    if sub == "checkout":
        if "--" in args or any(a in ("-f", "--force", "-B", "-p", "--patch", "--ours", "--theirs", "-m", "--merge")
                               for a in args) or bool(set(shorts) & set("fBpm")):
            return True
        plain, skip = [], False
        for a in args:
            if skip:
                skip = False
            elif a in ("-b", "--orphan", "-t", "--track"):
                skip = a in ("-b", "--orphan")
            elif not a.startswith("-"):
                plain.append(a)
        if len(plain) > 1:      # `checkout <commit> <path>…`: overwrites those files
            return True
        new_branch = any(a in ("-b", "--orphan") for a in args)
        return bool(plain) and not new_branch and _is_path(plain[0], base)
    return sub in GIT_DISCARDS


def _config_runs(args: list[str]) -> str | None:
    """`git config [scope] key value` (or `git config set key value`) → the key it sets when that
    key makes git run a program or read other settings; reading or removing one → None."""
    if any(a in CONFIG_READS or a.startswith(("--get", "--unset")) for a in args):
        return None
    plain, i = [], 0
    while i < len(args):
        a = args[i]
        if a in CONFIG_WITH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            plain.append(a)
        i += 1
    if plain and plain[0] in ("get", "list", "unset", "remove-section", "rename-section", "edit"):
        return None
    if plain and plain[0] == "set":
        plain = plain[1:]
    elif len(plain) < 2:        # `git config key` reads it
        return None
    return plain[0] if plain and GIT_RUNS.fullmatch(plain[0]) else None


def _git_writes(sub: str, args: list[str]) -> list[str]:
    """The paths git's own arguments write to: clone's folder, `worktree add <dir>`, `init <dir>`,
    `archive -o <file>` (and their --separate-git-dir/--template)."""
    found, plain, i = [], [], 0
    takes_value = {"clone": {"-b", "--branch", "-o", "--origin", "-c", "--config", "--depth", "--reference",
                             "-u", "--upload-pack", "-j", "--jobs", "--filter", "--shallow-since",
                             "--shallow-exclude", "--reference-if-able"},
                   "worktree": {"-b", "-B", "--reason"}, "init": {"-b", "--initial-branch", "--object-format"},
                   "archive": {"--format", "--prefix", "--remote", "--exec"}}.get(sub, set())
    paths = {"--separate-git-dir", "--template", "-o", "--output"} if sub != "clone" else {"--separate-git-dir",
                                                                                           "--template"}
    while i < len(args):
        a = args[i]
        if "=" in a and a.split("=", 1)[0] in paths:
            found.append(a.split("=", 1)[1])
        elif a in paths and i + 1 < len(args):
            found.append(args[i + 1])
            i += 1
        elif a in takes_value:
            i += 1
        elif not a.startswith("-"):
            plain.append(a)
        i += 1
    if sub == "clone" and len(plain) > 1:
        found.append(plain[1])
    elif sub == "worktree" and len(plain) > 1 and plain[0] == "add":
        found.append(plain[1])
    elif sub == "init" and plain:
        found.append(plain[0])
    return found if sub in ("clone", "worktree", "init", "archive") else []


def _download_targets(name: str, words: list[str]) -> list[str]:
    """Where curl or wget writes: `-o`/`--output`/`--output-dir` (curl; `-O` the current folder),
    `-O`/`--output-document`/`-P`/`-o` (wget; by default the current folder)."""
    curl = name == "curl"
    value_opts = {"-o", "--output", "--output-dir"} if curl else {"-O", "--output-document", "-P",
                                                                    "--directory-prefix", "-o", "--output-file",
                                                                    "-a", "--append-output"}
    found, here_too, i = [], not curl, 0
    while i < len(words):
        word = words[i]
        key = word.split("=", 1)[0]
        if word.startswith("--") and "=" in word and key in value_opts:
            found.append(word.split("=", 1)[1])
        elif word in value_opts and i + 1 < len(words):
            found.append(words[i + 1])
            i += 1
        elif curl and re.fullmatch(r"-[a-zA-Z]*o", word) and i + 1 < len(words):
            found.append(words[i + 1])
            i += 1
        elif curl and re.fullmatch(r"-[a-zA-Z]*O[a-zA-Z]*|--remote-name(?:-all)?", word):
            here_too = True
        elif not curl and re.fullmatch(r"-O.+", word):
            found.append(word[2:])
        i += 1
    if not curl and any(w in ("-O", "--output-document") or w.startswith(("-O", "--output-document=")) for w in words):
        here_too = False
    return [w for w in found if w != "-"] + (["."] if here_too else [])


def _in_git(token: str, workspace: Path, here: Path | None = None) -> bool:
    """Does `token` lead into the project's own .git folder (its config, hooks, attributes)?"""
    token = token.strip("'\"")
    if not token or token.startswith("-") or "://" in token or (".git" not in token and here is None):
        return False
    home = _home(token)
    if home is not None:
        path = home
    elif token.startswith("/"):
        path = Path(token)
    elif here is SCRATCH:
        return False
    else:
        path = (here or workspace) / token
    git_dir, resolved = workspace.resolve() / ".git", path.resolve()
    return resolved == git_dir or git_dir in resolved.parents


STUDIO_CHANGES = {"setup", "keys", "litellm", "access-key", "login", "stop", "start"}
STUDIO_OWN = "that would change the studio's own settings, not this project"


def _studio_cli(words: list[str]) -> str | None:
    """`studio setup …`, `.venv/bin/studio stop`, `uv run studio login`,
    `python -m studio2_server.cli keys set …` → the subcommand when it changes something."""
    if words and Path(words[0]).name in RUNNERS and "run" in words[1:3]:
        words = [w for w in words[words.index("run") + 1:]]
        while words and words[0].startswith("-"):
            words = words[1:]
    if not words:
        return None
    if Path(words[0]).name == "studio":
        rest = words[1:]
    elif INTERPRETERS.fullmatch(Path(words[0]).name) and "-m" in words[1:-1] \
            and words[words.index("-m") + 1].startswith("studio2_server"):
        rest = words[words.index("-m") + 2:]
    else:
        return None
    rest = [w for w in rest if not w.startswith("-")]
    if not rest:
        return None
    if rest[0] in STUDIO_CHANGES:
        return rest[0]
    if rest[0] == "library" and len(rest) > 1 and rest[1] in ("add", "remove"):
        return f"library {rest[1]}"
    return None


def _studio_place(token: str, workspace: Path, here: Path | None, places: tuple[Path, ...]) -> bool:
    """Does `token` lead into the studio's own settings or state (`~/.config/studio2`, `~/.claude`,
    its data folder), other than the project's own folder?"""
    token = token.strip("'\"")
    if not token or token.startswith("-") or "://" in token or not places:
        return False
    home = _home(token)
    if home is not None:
        path = home
    elif token.startswith("/"):
        path = Path(token)
    elif here is SCRATCH:
        return False
    else:
        path = (here or workspace) / token
    resolved, root = path.resolve(), workspace.resolve()
    for place in places:
        place = place.resolve()
        if resolved != place and place not in resolved.parents:
            continue
        # The project's own folder can sit inside the data folder: working there is ordinary.
        if root in (resolved, *resolved.parents) and root != place and place in root.parents:
            continue
        return True
    return False


def _find_starts(words: list[str]) -> list[str]:
    """`find -L a b -name x` → ["a", "b"]; none named → ["."]."""
    starts: list[str] = []
    for word in words[1:]:
        if word in ("-H", "-L", "-P") and not starts:
            continue
        if word.startswith(("-", "(", "!")):
            break
        starts.append(word)
    return starts or ["."]


def _move(here: Path | None, target: str, workspace: Path) -> Path | None:
    """Where `cd target` leaves the command (None: the project folder)."""
    target = target.strip("'\"")
    if CLIMBS.search(target):
        return Path("/")            # climbs to somewhere unknown: nothing relative is inside from there
    if (home := _home(target)) is not None:
        return home.resolve()
    if "$" in target or "`" in target or target == "-":
        return here                 # cannot be read from the text: judged from where it was
    if target.startswith("/"):
        return SCRATCH if (target.rstrip("/") + "/").startswith(SAFE_ABSOLUTE) else Path(target).resolve()
    if here is SCRATCH:
        return SCRATCH
    return ((here or workspace.resolve()) / target).resolve()


def _path_reason(command: str, workspace: Path, in_place: bool = False,
                 places: tuple[Path, ...] = ()) -> str | None:
    def own(target: str, here: Path | None = None) -> str | None:
        if _studio_place(target, workspace, here, places):
            return f"It changes {target}: {STUDIO_OWN}."
        return None

    masked = _mask(command)
    for match in REDIRECT.finditer(masked):   # a `>` inside quotes is text, not a redirection
        target = _word_at(command, match.start(1))
        if reason := own(target):
            return reason
        if _outside(target, workspace):
            return f"It writes to {target}, outside the project folder."
        if _in_git(target, workspace):
            return f"It writes into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
    here: Path | None = None        # where an earlier `cd` on this line left it; None: the project
    stack: list[Path | None] = []
    before: list[str] = []          # the words of the part before, for `find … | xargs rm`
    for part in _parts(command):
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            words = part.split()
        while words and re.fullmatch(r"\w+=\S*", words[0]):
            words = words[1:]
        while words and Path(words[0]).name in ("sudo", "env", "command", "exec", "nohup", "time") and len(words) > 1:
            words = [w for w in words[1:] if not re.fullmatch(r"\w+=\S*", w)] or words[1:]
        previous, before = before, words
        if not words:
            continue
        if sub := _studio_cli(words):
            return f"It runs the studio's own `{sub}`: {STUDIO_OWN}."
        name = Path(words[0]).name
        if name in ("cd", "pushd"):
            if name == "pushd":
                stack.append(here)
            args = [w for w in words[1:] if not w.startswith("-") or w == "-"]
            here = _move(here, args[0] if args else "~", workspace)
            continue
        if name == "popd":
            here = stack.pop() if stack else here
            continue
        if here is not None:    # the line moved: its redirections are read from there too
            for match in REDIRECT.finditer(_mask(part)):
                target = _word_at(part, match.start(1))
                if reason := own(target, here):
                    return reason
                if _outside(target, workspace, here):
                    return f"It writes to {target} from {here}, outside the project folder."
                if _in_git(target, workspace, here):
                    return f"It writes into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
        if name in ("curl", "wget"):
            for target in _download_targets(name, words[1:]):
                if reason := own(target, here):
                    return reason
                if _outside(target, workspace, here):
                    return f"It downloads to {target}, outside the project folder."
                if _in_git(target, workspace, here):
                    return f"It downloads into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
            continue
        if name == "git":
            folders, sub, args = _git(words)
            if sub == "config" and (key := _config_runs(args)):
                return f"It sets git's {key}: {RUNS_A_PROGRAM}."
            for target in _git_writes(sub, args):
                if reason := own(target, here):
                    return reason
                if _outside(target, workspace, here):
                    return f"It makes git write to {target}, outside the project folder."
            away = any(_outside(f, workspace, here) for f in folders) or (
                here is not None and _outside(".", workspace, here))
            if away and not _git_reads(sub, args):
                return f"It runs git {sub or ''} in a folder outside the project.".replace("  ", " ")
            base = here if here not in (None, SCRATCH) else workspace
            if in_place and _git_discards(sub, args, base):
                return (f"It runs git {sub} in your app's own repository, where it can discard or rewrite work "
                        "(the studio keeps its versions separately).")
            continue
        if name == "find":
            deletes = "-delete" in words or any(
                word in ("-exec", "-execdir", "-ok", "-okdir") and Path(nxt).name in DELETERS
                for word, nxt in zip(words, words[1:]))
            for start in _find_starts(words) if deletes else []:
                if reason := own(start, here):
                    return reason
                if _outside(start, workspace, here):
                    return f"It deletes files it finds in {start}, outside the project folder."
            continue
        if name == "xargs":
            i = 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in XARGS_WITH_VALUE else 1
            if i >= len(words):
                continue
            words, name = words[i:], Path(words[i]).name
            if name in DELETERS and previous and Path(previous[0]).name == "find":   # deletes what find found
                for start in _find_starts(previous):
                    if reason := own(start, here):
                        return reason
                    if _outside(start, workspace, here):
                        return f"It deletes files found in {start}, outside the project folder."
        if name not in MUTATORS or (name == "sed" and not any(w.startswith("-i") for w in words)):
            continue
        targets = words[1:]
        if name in ("cp", "ln"):
            # Copying only reads its sources (the agent copies helper files out of the studio's
            # skill folders); what changes is where the copy lands. A link likewise changes only
            # the link, its last argument, never what it points at.
            if "-t" in targets[:-1]:
                targets = [targets[targets.index("-t") + 1]]
            else:
                plain = [w for w in targets if not w.startswith("-")]
                targets = plain[-1:] if name == "cp" or len(plain) > 1 else ["."]
        for word in targets:
            if reason := own(word, here):
                return reason
            if _outside(word, workspace, here):
                return f"It changes {word}, outside the project folder."
            if _in_git(word, workspace, here):
                return f"It changes {word} in the project's .git folder: {RUNS_A_PROGRAM}."
    return None


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
INTERPRETERS = re.compile(r"python[\d.]*|node|ruby|perl|deno|bun|tsx|ts-node|php|Rscript|julia|lua")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
WRAPPERS = {"env", "exec", "time", "nohup", "command", "sudo", "doas", "nice"}
RUNNERS = {"uv", "uvx", "poetry", "pipenv", "pdm", "hatch", "rye", "conda", "mamba", "micromamba",
           "npx", "bunx", "pnpm", "yarn", "npm"}


def _reader(words: list[str]) -> str:
    """The program that reads a here-document, past `env`/`sudo` and runners such as `uv run`:
    `uv run --with x python -` → "python"."""
    words = [w for w in words if not re.fullmatch(r"\w+=\S*", w)]
    while words and Path(words[0]).name in WRAPPERS:
        words = [w for w in words[1:] if not re.fullmatch(r"\w+=\S*", w)]
        while words and words[0].startswith("-"):
            words = words[1:]
    if not words:
        return ""
    name = Path(words[0]).name
    if name in RUNNERS:
        found = next((Path(w).name for w in words[1:] if INTERPRETERS.fullmatch(Path(w).name)), None)
        return found or name
    return name


def _split(command: str) -> tuple[str, str]:
    """→ (the shell's own text, text that is not shell commands).

    A here-document's body is not always commands:
    - `cat > notes.py <<'EOF' … EOF` (or `tee`) writes a file. The lines between are that file's
      text and are dropped: a script that merely mentions `sudo` is not one. The line itself
      stays, so where it writes is still checked.
    - `python3 - <<'EOF' … EOF` (also `uv run python -`, or `cat <<'EOF' | python3 -`) is a
      program in another language. Shell redirections and file commands mean nothing there
      (`<code>~/x</code>` in a string is not a redirect), so it is checked only against the rules
      that name programs. So is the body of any other reader that is not a shell.
    - A body fed to a shell (`bash <<EOF`) is read as commands.
    """
    lines, shell, program, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        shell.append(line)
        i += 1
        match = HEREDOC.search(line)
        if not match:
            continue
        end = next((j for j in range(i, len(lines)) if lines[j].strip() == match.group(2)), None)
        if end is None:
            continue
        after = _mask(line[match.end():])
        if "|" in after:     # the here-document feeds a pipe: the pipe's last program reads it
            segment = line[match.end():][after.rindex("|") + 1:]
        else:
            segment = re.split(r"[;&|(]+", line[:match.start()])[-1]
        reader = _reader(re.split(r"[;&]", segment)[0].split())
        if reader in SHELLS:
            continue
        if reader not in ("cat", "tee", "git"):     # git reads it as a message (commit/tag -F -, notes)
            program.extend(lines[i:end])
        i = end + 1
    return "\n".join(shell), "\n".join(program)


def risky_command(command: str, workspace: Path, in_place: bool | None = None,
                  studio_places=()) -> str | None:
    """→ a one-sentence reason when the command needs the user's approval, else None.

    `in_place`: the project folder is the person's own app with their own repository (the
    studio's versions are in a separate history.git). Unset, it is read from the studio's record
    of such folders (workspace.history_dir), which the store fills when it loads a project.
    `studio_places`: the studio's own state beyond `~/.config/studio2` and `~/.claude` (its data
    folder, and its config folder when STUDIO2_CONFIG moves it)."""
    if in_place is None:
        in_place = ws.history_dir(workspace) is not None
    places = (Path.home() / ".config" / "studio2", Path.home() / ".claude", *map(Path, studio_places))
    text, program = _split(command or "")
    for pattern, reason in RULES:
        if pattern.search(text) or pattern.search(program):
            return reason
    return _pip_reason(text) or _pip_reason(program) or _path_reason(text, workspace, in_place, places)
