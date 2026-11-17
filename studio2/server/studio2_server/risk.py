"""Which shell commands the agent must ask about first (CONTRACT.md, "Approvals").

Pattern matching on the command text. It is a seat belt, not a sandbox: it catches the
ordinary ways an agent reaches outside its project, not a determined attempt to hide one.
Commands handed to another program to run (`bash -c`, `eval`, `env …`, `$( … )`, `… | sh`) are
judged by the same rules, a few levels deep; when that inner text cannot be read, it asks. The
line is first read as the shell reads it: joined continuation lines, decoded `$'…'`, and the
command after `(`, `{`, `!` and keywords such as `do` and `then`.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import workspace as ws

SEP = r"(?:^|[;&|(){}\n]|\$\(|`)\s*+(?:(?:do|then|else|elif|if|while|until|!)\s+)*(?:(?:env\s+)?(?:\w+=\S+\s+)*)?(?:command\s+|exec\s+|nohup\s+|time\s+)*"


# The folders before a program's name (`/usr/bin/`). Written so that a long run of text with no
# space and no slash (minified JSON in an `echo`) is given up on at once instead of being walked
# again from every bracket in it: that took 25 seconds on 180 KB, and the studio answered nothing.
DIRS = r"(?:[^\s/]{0,255}+/)*+"


def _cmd(names: str) -> re.Pattern:
    return re.compile(SEP + DIRS + rf"(?:{names})(?=[\s;&|)]|$)", re.I)


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
    (re.compile(SEP + DIRS + r"git(?:\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace)\s+\S+|-\S+))*\s+push(?=[\s;&|)]|$)|"
                + SEP + DIRS + r"gh(?=[\s;&|)]|$)", re.I),
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


# What can stand before a command without being one: `(`, `{`, `!`, the shell's keywords and a
# `case` pattern (`a)`). `time` is a wrapper, read with the others.
HEAD = re.compile(r"\s*(?:(?:do|then|else|elif|if|while|until|!|\{)(?=\s)|\((?!\()|"
                  r"case\s+\S+\s+in(?=\s|$)|[^\s()|;&]+\)(?=\s))")


def _head(part: str) -> str:
    """The part from its command on: `(rm -rf x)` → `rm -rf x`, `do ssh h` → `ssh h`."""
    while (match := HEAD.match(part)) and part[:match.end()].strip():
        part = part[match.end():]
    masked = _mask(part)
    extra = masked.count(")") - masked.count("(")
    while extra > 0 and part.rstrip().endswith(")"):    # the `)` that closes a subshell
        part, extra = part.rstrip()[:-1], extra - 1
    return part


def _pieces(command: str) -> list[tuple[str, str]]:
    """→ [(part, the separator before it)]: the command split at `;`, `&`, `|` and new lines that
    are not inside quotes, each part from its command on."""
    masked, pieces, at, sep = _mask(command), [], 0, ""
    for match in re.finditer(r"[;&|\n]+", masked):
        pieces.append((_head(command[at:match.start()]), sep))
        at, sep = match.end(), match.group(0)
    pieces.append((_head(command[at:]), sep))
    return pieces


def _parts(command: str) -> list[str]:
    """The command split at `;`, `&`, `|` and new lines that are not inside quotes."""
    return [part for part, _ in _pieces(command)]


# Commands inside commands (CONTRACT.md, "Approvals"): what a shell's `-c`, `eval`, a here-string,
# a wrapper such as `env` or `timeout`, a substitution, or a pipe into a shell will run.
MAX_DEPTH = 4
UNREADABLE = ("It runs commands that cannot be read before they run (they come from a variable, "
              "a computed value or another program's output).")
TOO_DEEP = "It wraps commands inside other commands too deeply to read."


class _Reason(str):
    """A reason found while reading what a part hands on, given as it is."""


RUNS_IN_CODE = _Reason("It runs code in another language that starts a program, which cannot be read "
                       "before it runs.")
# Read as usual: the person's own, and those the studio gives a project's back end (`$PORT`, …).
KNOWN_VARS = {"HOME", "PWD", "USER", "LOGNAME", "TMPDIR", "PORT", "HOST"}
HARMLESS_SUBS = {"pwd", "pwd -P", "pwd -L"}
# Wrappers that run the rest of the line, and their options that take a value.
HEAD_WRAPPERS = {"env": {"-u", "--unset", "-C", "--chdir"}, "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U"},
                 "doas": {"-u", "-C"}, "nohup": set(), "time": set(), "command": set(), "builtin": set(),
                 "exec": {"-a"}, "nice": {"-n"}, "timeout": {"-s", "--signal", "-k", "--kill-after"},
                 "stdbuf": {"-i", "-o", "-e"}, "caffeinate": {"-t", "-w"}, "setsid": set()}


def _raw_words(part: str) -> list[str]:
    """The part's words as written, quotes kept: split at whitespace that is not quoted."""
    masked = _mask(part)
    return [part[m.start():m.end()] for m in re.finditer(r"\S+", masked)]


def _unquote(raw: str) -> str:
    try:
        return " ".join(shlex.split(raw, posix=True))
    except ValueError:
        return raw


def _filled_in(raw: str) -> list[str]:
    """What the outer shell fills into `raw` before the inner one reads it: `$NAME`, `${NAME…}`
    → NAME; `$( … )` and backticks → "("; nothing inside single quotes; `$((…))` is arithmetic."""
    found, i, quote = [], 0, None
    while i < len(raw):
        char = raw[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
        elif quote != "'":
            if char == "`":
                found.append("(")
            elif char == "$" and i + 1 < len(raw):
                nxt = raw[i + 1]
                if raw.startswith("$((", i):
                    i += 3
                    continue
                if nxt == "(":
                    found.append("(")
                elif nxt == "{":                # `${#X}`, `${!X}`: not a plain name
                    found.append(re.match(r"\w*", raw[i + 2:]).group(0) or "{")
                elif re.match(r"[\w@*#?!$-]", nxt):
                    found.append(re.match(r"\w+", raw[i + 1:]).group(0) if re.match(r"\w", nxt) else nxt)
        i += 1
    return found


def _known(name: str) -> bool:
    return name in KNOWN_VARS or name.startswith("STUDIO_") or name in ("?", "#", "$", "!", "-")


def _assigned(name: str, ctx: tuple[str, int]) -> bool:
    """Does the line (or a line it is nested in) set `name`? Positional ones (`$1`, `$@`) are set
    by whoever runs a nested line, or by `set` or a function on the line."""
    line, depth = ctx
    if re.fullmatch(r"\d+|[@*]", name):
        return depth > 0 or bool(re.search(r"(?:^|[;&|\s(])set\s+\S|\w\s*\(\s*\)|\bfunction\s+\w", line))
    n = re.escape(name)
    return bool(re.search(rf"(?:^|[^\w$]){n}\+?=|\b(?:read|mapfile|readarray|getopts|select|for|export|declare|"
                          rf"typeset|local|readonly)\b[^;&|\n]*\b{n}\b|\bprintf\s+-v\s*{n}\b", line))


def _as_command(text: str) -> bool:
    """Does a variable that is not known stand where a command goes in `text`?"""
    for part, _ in _pieces(text):
        raw = _raw_words(part)
        at = _after_wrappers(raw, [_unquote(w) for w in raw]) if raw else 0
        if at < len(raw) and any(not _known(n) for n in re.findall(r"\$\{?(\w+|[@*])", raw[at])):
            return True
    return False


def _literal_values(raw: str, ctx: tuple[str, int]) -> list[str] | None:
    """`raw` is one variable (`$C`, `"${C}"`) that the line sets only to plain text: → those
    texts. None otherwise (another form, a computed value, `read`, `for`, …)."""
    match = re.fullmatch(r"\$(\w+)|\$\{(\w+)\}", _unquote(raw))
    if not match or raw.startswith("'"):
        return None
    name, line = match.group(1) or match.group(2), ctx[0]
    if re.search(rf"\b(?:read|mapfile|readarray|getopts|select|for)\b[^;&|\n]*\b{name}\b", line):
        return None
    values = []
    for found in re.finditer(rf"(?:^|[;&|\s(]){name}=", line):
        at = found.end()
        word = _word_at(line, at) if at < len(line) else ""
        value = word if at < len(line) and line[at] in "'\"" else _unquote(word)
        if not value or "$" in value or "`" in value or (found.end() < len(line) and line[found.end()] == "("):
            return None
        values.append(value)
    return values or None


def _readable(raw: str, ctx: tuple[str, int] = ("", 0), command: bool = False) -> bool:
    """Can the text the outer shell makes of `raw` be read? `$(pwd)` and the known variables can.
    Another variable can where it stands as an argument and the line does not set it: it comes
    from the environment the agent was started with. `command`: `raw` is itself the program."""
    if any(body.strip() not in HARMLESS_SUBS for body in _substitutions(raw)):
        return False
    other = [n for n in _filled_in(raw) if n != "(" and not _known(n)]
    if not other:
        return True
    if command or "{" in other or any(_assigned(n, ctx) for n in other):
        return False
    return not _as_command(_unquote(raw))


def _substitutions(text: str) -> list[str]:
    """The commands inside `$( … )` and backticks that the shell runs (not those in single quotes)."""
    found, i, quote = [], 0, None
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
        elif quote != "'" and char == "`":
            end = i + 1
            while end < len(text) and text[end] != "`":
                end += 2 if text[end] == "\\" else 1
            found.append(text[i + 1:end])
            i = end
        elif (quote != "'" and text.startswith("$(", i) and not text.startswith("$((", i)) or (
                quote is None and text.startswith(("<(", ">("), i)):    # also a process substitution
            depth, end, inner = 1, i + 2, None
            while end < len(text):
                c = text[end]
                if c == "\\":
                    end += 2
                    continue
                if c in "'\"":
                    close = text.find(c, end + 1)
                    end = close + 1 if close != -1 else len(text)
                    continue
                depth += c == "("
                depth -= c == ")"
                if depth == 0:
                    inner = text[i + 2:end]
                    break
                end += 1
            found.append(inner if inner is not None else text[i + 2:])
            i = end
        i += 1
    return found


def _shell_script(raw: list[str], words: list[str], start: int,
                  ctx: tuple[str, int] = ("", 0)) -> tuple[bool, str | None] | None:
    """`raw[start]` is a shell: → (True, its `-c` text, or None when that cannot be read), or None
    when it is not given text to run with `-c`."""
    j, has_c = start + 1, False
    while j < len(words) and words[j][:1] in ("-", "+") and words[j] not in ("-", "--"):
        if words[j] in ("-o", "+o", "-O", "+O"):
            j += 2
            continue
        if words[j].startswith("-") and not words[j].startswith("--") and "c" in words[j][1:]:
            has_c = True
        j += 1
    if has_c and j < len(words) and words[j] in ("-", "--"):
        j += 1                  # the end of the options: the script is the next word
    if not has_c or j >= len(words):
        return None
    return True, (words[j] if _readable(raw[j], ctx) else None)


def _after_wrappers(raw: list[str], words: list[str]) -> int:
    """The index of the program a line runs, past `NAME=value` and wrappers such as `env`, `sudo`,
    `nohup`, `time`, `nice`, `timeout` and their options."""
    i = 0
    while i < len(words):
        if re.fullmatch(r"\w+=.*", words[i]):
            i += 1
            continue
        name = Path(words[i]).name
        if name not in HEAD_WRAPPERS or i + 1 >= len(words):
            return i
        i += 1
        while i < len(words) and words[i].startswith("-") and words[i] != "-":
            if name == "env" and words[i] in ("-S", "--split-string"):
                return i        # its value is a line of its own: handled by the caller
            i += 2 if words[i] in HEAD_WRAPPERS[name] else 1
        if name == "timeout" and i < len(words):
            i += 1              # the duration
    return i


def _nested(part: str, previous: tuple[str, str] | None, ctx: tuple[str, int] = ("", 0)) -> list[str | None]:
    """The commands one part of a line hands to something else to run (None: one that cannot be
    read; a _Reason: a reason of its own). `previous`: the part before and the separator between
    them, for `… | sh`. `ctx`: the whole line and its depth, for the variables it sets."""
    raw = _raw_words(part)
    words = [_unquote(w) for w in raw]
    found: list[str | None] = list(_substitutions(part))
    if not words:
        return found
    at = _after_wrappers(raw, words)
    if at > 0 and at < len(words):
        if words[at] in ("-S", "--split-string") and at + 1 < len(words):
            found.append(words[at + 1] if _readable(raw[at + 1], ctx) else None)
            return found
        if not re.fullmatch(r"\w+=.*", words[at]) and any(Path(w).name in HEAD_WRAPPERS for w in words[:at]):
            found.append(" ".join(raw[at:]))
            return found        # the rest is judged as a line of its own, wrappers and all
    if at >= len(words):
        return found
    before = _mask(" ".join(raw[:at]))
    inside = before.count("(") > before.count(")") or before.count("`") % 2   # `x=$(a | b`, `B=(-A …)`
    if not inside and not _readable(raw[at], ctx, command=True):
        # The program itself comes from a variable: read it as the plain text the line sets it
        # to (`C=/usr/bin/x; "$C" -v`), else it cannot be read (`$x -rf ~`, `$(tool) …`).
        values = _literal_values(raw[at], ctx)
        if values is None:
            found.append(None)
        else:
            found.extend(" ".join([shlex.quote(v), *raw[at + 1:]]) for v in values)
        return found
    name = Path(words[at]).name
    if name == "eval":
        args = raw[at + 1:]
        found.append(" ".join(_unquote(w) for w in args) if all(_readable(w, ctx) for w in args) else None)
        return found
    if name in RUNS_ITS_ARGS:
        found.append(_run_by(name, raw, words, at, previous, ctx))
    for i in range(at, len(words)):         # also after xargs, find -exec, …
        if Path(words[i]).name in SHELLS and (script := _shell_script(raw, words, i, ctx)):
            found.append(script[1])
        if _code_runs_program(words, i):
            found.append(RUNS_IN_CODE)
    if name in SHELLS | {"source", "."} and "<(" in _mask(part):
        found.append(None)                  # reads another program's output
    elif name in STDIN_SHELLS and (m := re.search(r"<<<\s*", _mask(part))):
        rest = _raw_words(part[m.end():])
        found.append(_unquote(rest[0]) if rest and _readable(rest[0], ctx) else None)
    elif name in SHELLS and previous and previous[1] == "|" and not any(
            not w.startswith("-") and w != "-" for w in words[at + 1:]) and not _shell_script(raw, words, at):
        found.append(_piped_text(previous[0], ctx))
    elif name in ("at", "batch") and previous and previous[1] == "|" and "-f" not in words:
        found.append(_piped_text(previous[0], ctx))
    return found


# Programs that run the command they are given (CONTRACT.md, "Approvals").
RUNS_ITS_ARGS = {"watch", "script", "tmux", "screen", "parallel", "xargs"}
TMUX_RUNS = {"new", "new-session", "new-window", "neww", "split-window", "splitw", "respawn-pane", "respawnp",
             "respawn-window", "respawnw", "run-shell", "run", "send-keys", "send", "pipe-pane", "pipep",
             "display-popup", "popup"}
TMUX_KEYS = {"Enter", "C-m", "C-c", "C-d", "Escape", "Space", "Tab", "KPEnter", "BSpace"}
PARALLEL_WITH_VALUE = {"-j", "--jobs", "-S", "--sshlogin", "-a", "--arg-file", "--colsep", "-d", "--delimiter",
                       "-I", "--joblog", "--results", "--tmpdir", "-N", "-n", "-L", "--timeout", "--delay",
                       "--retries"}


def _run_by(name: str, raw: list[str], words: list[str], at: int, previous: tuple[str, str] | None,
            ctx: tuple[str, int]) -> str | None:
    """The command line `watch`, `script`, `tmux`, `screen`, `parallel` or `xargs` at `at` runs
    ("": none; None: one that cannot be read)."""
    n, i = len(words), at + 1

    def shell_text(rest: list[int]) -> str | None:      # words joined and read again by a shell
        return " ".join(words[k] for k in rest) if all(_readable(raw[k], ctx) for k in rest) else None

    if name == "watch":
        while i < n and words[i].startswith("-"):
            i += 2 if words[i] in ("-n", "--interval", "-q", "--equexit") else 1
        return shell_text(list(range(i, n)))
    if name == "script":
        while i < n and words[i].startswith("-"):
            if words[i] in ("-c", "--command") and i + 1 < n:
                return shell_text([i + 1])
            i += 2 if words[i] in ("-t", "-T", "-I", "-O", "-B", "-E", "-m", "-o", "--timing") else 1
        return " ".join(raw[i + 1:])        # after the file: the program and its arguments
    if name == "tmux":
        while i < n and words[i].startswith("-"):
            if words[i] == "-c" and i + 1 < n:
                return shell_text([i + 1])
            i += 2 if words[i] in ("-L", "-S", "-f", "-T") else 1
        if i >= n or words[i] not in TMUX_RUNS:
            return ""
        sub, i = words[i], i + 1
        while i < n and words[i].startswith("-"):
            i += 2 if words[i] in ("-t", "-s", "-n", "-c", "-F", "-e", "-x", "-y", "-l", "-f", "-N", "-T") else 1
        rest = [k for k in range(i, n) if not (sub in ("send-keys", "send") and words[k] in TMUX_KEYS)]
        return shell_text(rest)
    if name == "screen":
        while i < n and words[i].startswith("-"):
            if words[i] == "-X":
                return None                 # a command sent to a running session
            i += 2 if re.fullmatch(r"-[a-zA-Z]*[Scehpst]", words[i]) else 1
        return " ".join(raw[i:])
    if name == "parallel":
        while i < n and words[i].startswith("-"):
            if words[i] in ("-S", "--sshlogin"):
                return "ssh " + " ".join(raw[i + 1:i + 2])
            i += 2 if words[i] in PARALLEL_WITH_VALUE else 1
        end = next((k for k in range(i, n) if words[k] in (":::", "::::", ":::+", "::::+")), n)
        if end == i:
            return None                     # it reads its commands from elsewhere
        command = shell_text(list(range(i, end)))
        return None if command is None else " ".join([command, *(raw[k] for k in range(end + 1, n)
                                                                  if words[k] not in (":::", ":::+"))])
    # xargs: its command, with the words `echo` or `printf` pipe into it as arguments.
    while i < n and words[i].startswith("-"):
        i += 2 if words[i] in XARGS_WITH_VALUE else 1
    items = _echo_items(previous[0], ctx) if previous and previous[1] == "|" else None
    return " ".join(raw[i:] + (items or [])) if i < n else ""


def _echo_items(part: str, ctx: tuple[str, int]) -> list[str] | None:
    """The words `echo` or `printf` in `part` print, as written; None when it is another program."""
    raw = _raw_words(part)
    words = [_unquote(w) for w in raw]
    at = _after_wrappers(raw, words) if raw else 0
    if at >= len(words) or Path(words[at]).name not in ("echo", "printf"):
        return None
    args = [w for w in raw[at + 1:] if not re.fullmatch(r"-[neE]+", w)]
    if Path(words[at]).name == "printf" and args and "%" in args[0]:
        args = args[1:]
    return args if all(_readable(w, ctx) for w in args) else None


# Code handed to another language on the command line, and the flags that give it.
CODE_FLAGS = {"node": {"-e", "--eval", "-p", "--print"}, "bun": {"-e", "--eval", "-p", "--print"},
              "tsx": {"-e", "--eval", "-p", "--print"}, "ts-node": {"-e", "--eval", "-p", "--print"},
              "php": {"-r"}, "lua": {"-e"}, "Rscript": {"-e"}, "julia": {"-e", "--eval"}}
STARTS_A_PROGRAM = re.compile(
    r"(?<![\w.])system\(|(?<![\w.$])system\s+[\"']|\bos\.(?:system|exec\w*|spawn\w*|popen|fork)\b|"
    r"\bsubprocess\b|\bpopen\b|\bPopen\b|\bchild_process\b|\bspawn\w*\(|(?<![\w.$])spawn\s+[\"']|"
    r"(?<![\w.])exec(?:[lv]p?e?|Sync|File(?:Sync)?)?\b|\bshell_exec\b|\bpassthru\b|\bproc_open\b|"
    r"\bpty\b|\bOpen3\b|\bDeno\.(?:Command|run)\b|\bqx\s*[({/'\"\[|]|%x[({\[]")
AWK_PIPES = re.compile(r"\|\s*getline|\|\s*[\"$\w(]|\|&")


def _inline_code(words: list[str], i: int) -> list[str]:
    """The code text the program at `words[i]` is given on the command line (`python -c`,
    `perl -e`, `node -e`, `awk '…'`, …)."""
    name, n, j, codes = Path(words[i]).name, len(words), i + 1, []
    if re.fullmatch(r"python[\d.]*", name):
        while j < n and words[j].startswith("-") and words[j] != "-":
            word = words[j]
            if word == "-c" or re.fullmatch(r"-[A-Za-z]+c", word):
                return words[j + 1:j + 2]
            if word.startswith("-c") or word == "-m":
                return [word[2:]] if word.startswith("-c") else []
            j += 2 if word in ("-W", "-X") else 1
        return []
    if name in ("perl", "ruby"):
        while j < n and words[j].startswith("-") and words[j] not in ("-", "--"):
            if re.fullmatch(r"-[a-zA-Z0-9]*[eE]", words[j]) and j + 1 < n:
                codes.append(words[j + 1])
                j += 2
                continue
            j += 1
        return codes
    if name in ("awk", "gawk", "mawk", "nawk"):
        while j < n and words[j].startswith("-"):
            if words[j] in ("-f", "--file") or words[j].startswith("-f"):
                return []                   # a program file: not looked inside
            if words[j] == "--":
                j += 1
                break
            j += 2 if words[j] in ("-F", "-v", "--assign", "--field-separator") else 1
        return words[j:j + 1]
    if name == "deno" and j < n and words[j] == "eval":
        return words[j + 1:j + 2]
    flags = CODE_FLAGS.get(name, set())
    while flags and j < n and words[j].startswith("-"):
        if words[j] in flags and j + 1 < n:
            codes.append(words[j + 1])
            j += 2
            continue
        j += 1
    return codes


def _code_runs_program(words: list[str], i: int) -> bool:
    """Does code given to another language on the command line mention starting a program?"""
    name = Path(words[i]).name
    for code in _inline_code(words, i):
        if STARTS_A_PROGRAM.search(code):
            return True
        if name in ("perl", "ruby", "php") and "`" in code:
            return True
        if name in ("awk", "gawk", "mawk", "nawk") and AWK_PIPES.search(code):
            return True
    return False


def _piped_text(part: str, ctx: tuple[str, int] = ("", 0)) -> str | None:
    """What `part` pipes into a shell, when it can be read: the text of `echo` or `printf` (a
    here-document fed through `cat` is read with the line already). None otherwise."""
    if (match := HEREDOC.search(part)) and "<<<" not in part[max(0, match.start() - 1):match.end()]:
        return ""
    raw = _raw_words(part)
    words = [_unquote(w) for w in raw]
    at = _after_wrappers(raw, words)
    if at >= len(words) or Path(words[at]).name not in ("echo", "printf"):
        return None
    args = raw[at + 1:]
    if not all(_readable(w, ctx) for w in args):
        return None
    text = " ".join(_unquote(w) for w in args if not re.fullmatch(r"-[neE]+", w))
    return text.replace("\\n", "\n")


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


def _script_deletes(words: list[str], i: int) -> bool:
    """Does the program at `words[i]` delete what it is given: `rm` and the like, or a shell whose
    `-c` text starts with one (`sh -c 'rm "$@"'`)?"""
    if Path(words[i]).name in DELETERS:
        return True
    # The words are already unquoted: whether the outer shell fills anything in does not matter here.
    script = _shell_script(["''"] * len(words), words, i) if Path(words[i]).name in SHELLS else None
    if not script or not script[1]:
        return False
    first = _raw_words(_parts(script[1])[0])
    return bool(first) and Path(_unquote(first[0])).name in DELETERS


def _path_reason(command: str, workspace: Path, in_place: bool = False,
                 places: tuple[Path, ...] = (), nested: list | None = None,
                 ctx: tuple[str, int] = ("", 0)) -> str | None:
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
    pieces = _pieces(command)
    for index, (part, _sep) in enumerate(pieces):
        if nested is not None:      # what this part hands on to run, read from where it runs
            prefix = "" if here is None else f"cd {'/tmp' if here is SCRATCH else shlex.quote(str(here))} && "
            for inner in _nested(part, pieces[index - 1][0:1] + (_sep,) if index else None, ctx):
                if isinstance(inner, _Reason) or inner is None:
                    nested.append(inner)
                else:
                    nested.append(prefix + inner if inner.strip() else "")
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
                word in ("-exec", "-execdir", "-ok", "-okdir") and _script_deletes(words, i + 1)
                for i, word in enumerate(words[:-1]))
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
            if _script_deletes(words, 0) and previous and Path(previous[0]).name == "find":   # deletes what find found
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
STDIN_SHELLS = SHELLS | {"at", "batch"}      # also read commands given on their input
WRAPPERS = {"env", "exec", "time", "nohup", "command", "sudo", "doas", "nice", "setsid"}
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
        i += 1
        # A backslash at the end of a line joins it to the next, even inside a word (`r\` `m`).
        while i < len(lines) and (len(line) - len(line.rstrip("\\"))) % 2:
            line, i = line[:-1] + lines[i], i + 1
        shell.append(line)
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
        if reader in STDIN_SHELLS:
            continue
        if reader not in ("cat", "tee", "git"):     # git reads it as a message (commit/tag -F -, notes)
            program.extend(lines[i:end])
        i = end + 1
    return "\n".join(shell), "\n".join(program)


def _ansi_c(text: str) -> str:
    """`$'…'` (outside other quotes) decoded and written as plain single-quoted text, the way
    the shell reads it: `$'\\x72m'` → `'rm'`."""
    out, i, quote = [], 0, None
    while i < len(text):
        char = text[i]
        if quote != "'" and char == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if quote is None and text.startswith("$'", i):
            j, body = i + 2, []
            while j < len(text) and text[j] != "'":
                if text[j] == "\\" and j + 1 < len(text):
                    body.append(text[j:j + 2])
                    j += 2
                    continue
                body.append(text[j])
                j += 1
            if j >= len(text):
                out.append(text[i:])
                break
            out.append(shlex.quote(_decode("".join(body))))
            i = j + 1
            continue
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
        out.append(char)
        i += 1
    return "".join(out)


ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
           "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _decode(body: str) -> str:
    """The text of a `$'…'` body: `\\n`, `\\x72`, `\\162`, `\\u0072`, `\\cA`, …"""
    def one(match: re.Match) -> str:
        code = match.group(1)
        try:
            if code[0] in "xuU" and len(code) > 1:
                return chr(int(code[1:], 16))
            if code[0] in "01234567":
                return chr(int(code, 8) & 0xFF)
            if code[0] == "c" and len(code) > 1:
                return chr(ord(code[1]) & 0x1F)
        except (ValueError, OverflowError):     # `\U110000`: past the last character there is
            return "\\" + code
        return ESCAPES.get(code, "\\" + code)
    return re.sub(r"\\(x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|[0-7]{1,3}|c.|.)", one, body,
                  flags=re.S)


CANNOT_READ = "The studio could not read this command, so it asks before running it."


def risky_command(command: str, workspace: Path, in_place: bool | None = None,
                  studio_places=(), _depth: int = 0, _outer: str = "") -> str | None:
    """→ a one-sentence reason when the command needs the user's approval, else None.
    A fault in the reading itself is a reason to ask: it must never let a line through, and
    never end the request."""
    try:
        return _risky(command, workspace, in_place, studio_places, _depth, _outer)
    except RecursionError:
        return TOO_DEEP
    except Exception:
        return CANNOT_READ


def _risky(command: str, workspace: Path, in_place: bool | None = None,
           studio_places=(), _depth: int = 0, _outer: str = "") -> str | None:
    """What `risky_command` answers, without its safety net.

    `in_place`: the project folder is the person's own app with their own repository (the
    studio's versions are in a separate history.git). Unset, it is read from the studio's record
    of such folders (workspace.history_dir), which the store fills when it loads a project.
    `studio_places`: the studio's own state beyond `~/.config/studio2` and `~/.claude` (its data
    folder, and its config folder when STUDIO2_CONFIG moves it)."""
    if in_place is None:
        in_place = ws.history_dir(workspace) is not None
    places = (Path.home() / ".config" / "studio2", Path.home() / ".claude", *map(Path, studio_places))
    text, program = _split(command or "")
    text = _ansi_c(text)
    ctx = (_outer + "\n" + text, _depth)    # the line, and the lines it is nested in
    for pattern, reason in RULES:
        if pattern.search(text) or pattern.search(program):
            return reason
    if STARTS_A_PROGRAM.search(program):     # a here-document given to another language
        return str(RUNS_IN_CODE)
    nested: list[str | None] = []
    if reason := _pip_reason(text) or _pip_reason(program) or _path_reason(text, workspace, in_place, places, nested, ctx):
        return reason
    for inner in nested:     # each is judged as if it had been typed on its own
        if isinstance(inner, _Reason):
            return str(inner)
        if inner is None:
            return UNREADABLE
        if not inner:
            continue
        if _depth >= MAX_DEPTH:
            return TOO_DEEP
        if reason := risky_command(inner, workspace, in_place, studio_places, _depth + 1, ctx[0]):
            return reason
    return None
