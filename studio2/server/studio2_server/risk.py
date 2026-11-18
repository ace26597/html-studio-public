"""Which shell commands the agent must ask about first (CONTRACT.md, "Approvals").

Pattern matching on the command text. It is a seat belt, not a sandbox: it catches the
ordinary ways an agent reaches outside its project, not a determined attempt to hide one.
Commands handed to another program to run (`bash -c`, `eval`, `env …`, `$( … )`, `… | sh`) are
judged by the same rules, a few levels deep; when that inner text cannot be read, it asks. The
line is first read as the shell reads it: joined continuation lines, decoded `$'…'`, and the
command after `(`, `{`, `!` and keywords such as `do` and `then`. A variable the line sets to
plain text is put in place where it is used, and the line is judged as if that text had been typed.
"""

from __future__ import annotations

import fnmatch
import functools
import itertools
import math
import os
import re
import shlex
from pathlib import Path

from . import workspace as ws

SEP = r"(?:^|[;&|(){}\n]|\$\(|`)\s*+(?:(?:do|then|else|elif|if|while|until|!|coproc|noglob|nocorrect|-|repeat\s+\S+)\s+)*(?:(?:env\s+)?(?:\w+=[^\s;&|]*+\s++)*+)?(?:command\s+|exec\s+|nohup\s+|time\s+)*"


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
# Every redirection that writes: `>`, `>>`, `>|`, `2>`, `3>>`, `&>`, `&>>`, `>&` and `2>&` to a file,
# and `<>`/`1<>`, which opens one for reading and writing.
REDIRECT = re.compile(r"(?<![<>&\d])(?P<op>(?:\d+|&)?(?:<>|>>?[|&]?))\s*(?P<target>\S+)")
SAFE_ABSOLUTE = ("/dev/null", "/dev/stdout", "/dev/stderr", "/tmp/", "/private/tmp/", "/var/folders/", "/dev/fd/")
SAFE_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/fd/")
SAFE_ROOTS = tuple({Path(p).resolve() for p in ("/tmp", "/private/tmp", "/var/folders")})


def _scratch(path: str, workspace: Path) -> bool:
    """Is `path` inside a scratch folder (/tmp, /var/folders), once `..` is resolved? The folder
    itself, one `..` above it, and a folder that holds the project are not."""
    resolved = Path(path).resolve()
    return any(root in resolved.parents for root in SAFE_ROOTS) and resolved not in workspace.resolve().parents


DELETERS = {"rm", "rmdir", "unlink", "shred"}
# A substitution or variable followed somewhere by `..`: `$(pwd)/..` can climb anywhere.
CLIMBS = re.compile(r"(?:\$\(|`|\$\{?\w).*(?:^|/)\.\.(?:/|$)")
SCRATCH = Path("/nonexistent-scratch")     # `here` after a `cd` to a scratch folder such as /tmp
UNKNOWN = Path("/nonexistent-unknown")     # `here` after a `cd` to a place that cannot be read


def _redirect_targets(text: str) -> list[str]:
    """Where the redirections in `text` write (a `>` inside quotes is text, not a redirection).
    `2>&1`, `>&2` and `>&-` join or close streams and write nothing."""
    found = []
    for match in REDIRECT.finditer(_mask(text)):
        target = _word_at(text, match.start("target"))
        if match.group("op").endswith("&") and re.fullmatch(r"\d+-?|-", target):
            continue
        found.append(target)
    return found


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
        if token.startswith(SAFE_DEVICES) and ".." not in token or _scratch(token, workspace):
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


@functools.lru_cache(maxsize=64)       # the same line is masked several times over
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
HEAD = re.compile(r"\s*(?:(?:do|then|else|elif|if|while|until|!|\{|noglob|nocorrect|-|repeat\s+\S+|"
                  r"coproc(?:\s+\w+(?=\s+[{(]))?)(?=\s)|\((?!\()|for\s+\w+(?:\s+\w+)*\s*\([^()]*\)|"
                  r"case\s+\S+\s+in(?=\s|$)|[^\s()|;&]+\)(?=\s))")
ARITHMETIC = re.compile(r"\s*\(\(.*\)\)\s*", re.S)


def _head(part: str) -> str:
    """The part from its command on: `(rm -rf x)` → `rm -rf x`, `do ssh h` → `ssh h`."""
    while True:
        if (match := HEAD.match(part)) and part[:match.end()].strip():
            part = part[match.end():]
        elif part.lstrip().startswith("((") and not ARITHMETIC.fullmatch(_mask(part)):
            part = part.lstrip()[1:]        # `((rm x) )`: a subshell in a subshell, not arithmetic
        else:
            break
    masked = _mask(part)
    extra = masked.count(")") - masked.count("(")
    while extra > 0 and part.rstrip().endswith(")"):    # the `)` that closes a subshell
        part, extra = part.rstrip()[:-1], extra - 1
    return part


def _pieces(command: str) -> list[tuple[str, str]]:
    """→ [(part, the separator before it)]: the command split at `;`, `&`, `|` and new lines that
    are not inside quotes, each part from its command on."""
    masked, pieces, at, sep = _mask(command), [], 0, ""
    # zsh's `if true { … }` and `() { … }`: a `{` on its own also starts a command.
    for match in re.finditer(r"[;&|\n]+|(?<![^\s;&|()])\{(?=\s)", masked):
        pieces.append((_head(command[at:match.start()]), sep))
        at, sep = match.end(), match.group(0)
    pieces.append((_head(command[at:]), sep))
    return pieces


def _parts(command: str) -> list[str]:
    """The command split at `;`, `&`, `|`, new lines and a lone `{` that are not inside quotes."""
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


PLAIN_RUN = re.compile(r"[^ \t\r\n'\"\\]+")
DOUBLE_QUOTED = re.compile(r'[^"\\]*(?:\\.[^"\\]*)*', re.S)


def _shell_words(text: str) -> list[str]:
    """`shlex.split(text)` (POSIX), in one walk: shlex adds to a word one character at a time,
    which took 17 seconds on a one-megabyte quoted word. Raises ValueError like shlex."""
    words, current, has, i, n = [], [], False, 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            if has:
                words.append("".join(current))
                current, has = [], False
            i += 1
            continue
        has = True
        if c == "\\":
            if i + 1 >= n:
                raise ValueError("No escaped character")
            current.append(text[i + 1])
            i += 2
        elif c == "'":
            close = text.find("'", i + 1)
            if close < 0:
                raise ValueError("No closing quotation")
            current.append(text[i + 1:close])
            i = close + 1
        elif c == '"':
            body = DOUBLE_QUOTED.match(text, i + 1)
            if body.end() >= n or text[body.end()] != '"':     # the end, or a lone `\` before it
                raise ValueError("No closing quotation")
            current.append(re.sub(r'\\(["\\])', r"\1", body.group()))
            i = body.end() + 1
        else:
            run = PLAIN_RUN.match(text, i)
            current.append(run.group())
            i = run.end()
    if has:
        words.append("".join(current))
    return words


def _unquote(raw: str) -> str:
    try:
        return " ".join(_shell_words(raw))
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


def _known(name: str, ctx: tuple[str, int] = ("", 0)) -> bool:
    """A name read as usual, unless the line sets it itself (`PORT=rm; $PORT -rf ~`)."""
    if name in ("?", "#", "$", "!", "-"):
        return True
    return (name in KNOWN_VARS or name.startswith("STUDIO_")) and not _assigned(name, ctx)


def _by_name(raw: str) -> bool:
    """Is the program `raw` written out by name after a folder that comes from a variable
    (`"$VIRTUAL_ENV/bin/python"`, `$BIN/tsc`)? Then it is that program, read by its name like
    `/usr/bin/python`. Not after a substitution that is still there: another program's output
    would choose the folder (the ones that can be read, `$(pwd)`, were put in place already)."""
    word = _unquote(raw)
    return "$" in word and "$(" not in word and "`" not in word and bool(re.fullmatch(r".*/[\w.+-]+", word, re.S))


def _assigned(name: str, ctx: tuple[str, int]) -> bool:
    """Does the line (or a line it is nested in) set `name`? Positional ones (`$1`, `$@`) are set
    by whoever runs a nested line, or by `set` or a function on the line."""
    line, depth = ctx
    if re.fullmatch(r"\d+|[@*]", name):
        return depth > 0 or bool(re.search(r"(?:^|[;&|\s(])set\s+\S|\w\s*\(\s*\)|\bfunction\s+\w", line))
    n = re.escape(name)
    return bool(re.search(rf"(?:^|[^\w$]){n}\+?=|\b(?:read|mapfile|readarray|getopts|select|for|export|declare|"
                          rf"typeset|local|readonly)\b[^;&|\n]*\b{n}\b|\bprintf\s+-v\s*{n}\b", line))


def _as_command(text: str, ctx: tuple[str, int] = ("", 0)) -> bool:
    """Does a variable that is not known stand where a command goes in `text`?"""
    for part, _ in _pieces(text):
        raw = _raw_words(part)
        at = _after_wrappers(raw, [_unquote(w) for w in raw]) if raw else 0
        if at < len(raw) and not _by_name(raw[at]) and any(
                not _known(n, ctx) for n in re.findall(r"\$\{?(\w+|[@*])", raw[at])):
            return True
    return False


def _readable(raw: str, ctx: tuple[str, int] = ("", 0), command: bool = False) -> bool:
    """Can the text the outer shell makes of `raw` be read? `$(pwd)` and the known variables can.
    Another variable can where it stands as an argument and the line does not set it: it comes
    from the environment the agent was started with. `command`: `raw` is itself the program."""
    if any(body.strip() not in HARMLESS_SUBS for body in _substitutions(raw)):
        return False
    other = [n for n in _filled_in(raw) if n != "(" and not _known(n, ctx)]
    if not other:
        return True
    if command or "{" in other or any(_assigned(n, ctx) for n in other):
        return False
    return not _as_command(_unquote(raw), ctx)


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


SPLIT_STRING = re.compile(r"-[a-zA-Z]*S(?P<a>.*)|--split-string(?:=(?P<b>.*))?", re.S)   # `env -S'…'`


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
            if name == "env" and SPLIT_STRING.fullmatch(words[i]):
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
        if split := SPLIT_STRING.fullmatch(words[at]):
            attached = split.group("a") or split.group("b")
            if attached:
                found.append(attached if _readable(raw[at], ctx) else None)
            elif at + 1 < len(words):
                found.append(words[at + 1] if _readable(raw[at + 1], ctx) else None)
            return found
        if not re.fullmatch(r"\w+=.*", words[at]) and any(Path(w).name in HEAD_WRAPPERS for w in words[:at]):
            found.append(" ".join(raw[at:]))
            return found        # the rest is judged as a line of its own, wrappers and all
    if at >= len(words):
        return found
    before = _mask(" ".join(raw[:at]))
    inside = before.count("(") > before.count(")") or before.count("`") % 2   # `x=$(a | b`, `B=(-A …)`
    if not inside and not _by_name(raw[at]) and not _readable(raw[at], ctx, command=True):
        # The program itself comes from a variable or a substitution that cannot be read (`$x -rf ~`,
        # `$(tool) …`). One the line sets to plain text was already read in its place (_expand).
        found.append(None)
        return found
    name = Path(words[at]).name or words[at]        # `.` is a name of its own
    if name == "trap" and at + 1 < len(words) and words[at + 1] not in ("-", "-l", "-p", "--"):
        found.append(words[at + 1] if _readable(raw[at + 1], ctx) else None)     # runs later, on a signal
        return found
    if name == "find":
        starts = [shlex.quote(s) if not s.startswith("~") else s for s in _find_starts(words[at:])]
        for k, word in enumerate(words):
            if word in ("-exec", "-execdir", "-ok", "-okdir"):
                end = next((j for j in range(k + 1, len(words)) if words[j] in (";", "+")), len(words))
                found.append(" ".join(" ".join(starts) if words[j] == "{}" else raw[j] for j in range(k + 1, end)))
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
    # `source /dev/stdin` and `. /dev/stdin` read their input as commands, as a shell does.
    sources_input = name in ("source", ".") and words[at + 1:at + 2] in (["/dev/stdin"], ["/dev/fd/0"], ["-"])
    if name in SHELLS | {"source", "."} and "<(" in _mask(part):
        found.append(None)                  # reads another program's output
    elif (name in STDIN_SHELLS or sources_input) and (m := re.search(r"<<<\s*", _mask(part))):
        rest = _raw_words(part[m.end():])
        found.append(_unquote(rest[0]) if rest and _readable(rest[0], ctx) else None)
    elif previous and previous[1] == "|" and (sources_input or name in SHELLS and not any(
            not w.startswith("-") and w != "-" for w in words[at + 1:]) and not _shell_script(raw, words, at)):
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
        return UNKNOWN              # climbs to somewhere unknown: nothing relative is inside from there
    if (home := _home(target)) is not None:
        return home.resolve()
    if "$" in target or "`" in target or target == "-":
        return here                 # cannot be read from the text: judged from where it was
    if target.startswith("/"):
        resolved = Path(target).resolve()
        return SCRATCH if resolved in SAFE_ROOTS or _scratch(target, workspace) else resolved
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

    for target, readable in _each_path(_redirect_targets(command), ctx):
        if not readable:
            return f"It writes to {target}, a path that cannot be read before it runs."
        if reason := own(target):
            return reason
        if _outside(target, workspace):
            return f"It writes to {target}, outside the project folder."
        if _in_git(target, workspace):
            return f"It writes into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
    here: Path | None = None        # where an earlier `cd` on this line left it; None: the project
    last_dir: Path | None = UNKNOWN  # where `cd -` goes: the folder before the last `cd` (none yet: unknown)
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
            words = _shell_words(part)
        except ValueError:
            words = part.split()
        settings = []                   # `GIT_DIR=… git …`: names set for this command only
        while words and re.fullmatch(r"\w+=\S*", words[0]):
            settings.append(words[0])
            words = words[1:]
        while words and Path(words[0]).name in ("sudo", "env", "command", "exec", "nohup", "time") and len(words) > 1:
            settings.extend(w for w in words[1:] if re.fullmatch(r"\w+=\S*", w))
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
            if args[:1] == ["-"]:
                here, last_dir = last_dir, here
                continue
            forms = _path_forms(args[0], ctx) if args else ["~"]
            last_dir = here
            if forms is None or len(forms) > 1:
                here = UNKNOWN          # somewhere unknown: nothing relative is inside from there
                continue
            here = _move(here, forms[0], workspace)
            continue
        if name == "popd":
            here = stack.pop() if stack else here
            continue
        if here is not None:    # the line moved: its redirections are read from there too
            for target, _readable_too in _each_path(_redirect_targets(part), ctx):
                if reason := own(target, here):
                    return reason
                if _outside(target, workspace, here):
                    where = "a folder that cannot be read" if here is UNKNOWN else here
                    return f"It writes to {target} from {where}, outside the project folder."
                if _in_git(target, workspace, here):
                    return f"It writes into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
        if name in ("curl", "wget"):
            for target, readable in _each_path(_download_targets(name, words[1:]), ctx):
                if not readable:
                    return f"It downloads to {target}, a path that cannot be read before it runs."
                if reason := own(target, here):
                    return reason
                if _outside(target, workspace, here):
                    return f"It downloads to {target}, outside the project folder."
                if _in_git(target, workspace, here):
                    return f"It downloads into the project's .git folder ({target}): {RUNS_A_PROGRAM}."
            continue
        if name == "git":
            folders, sub, args = _git(words)
            # GIT_DIR and GIT_WORK_TREE, set for this command or earlier on the line, name where git works.
            line = _line_of(ctx[0])
            for setting in ("GIT_DIR", "GIT_WORK_TREE"):
                folders += [w.split("=", 1)[1] for w in settings if w.startswith(setting + "=")]
                folders += [v for v in line.path_values(setting) or []] if line.sets(setting) else []
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
            for start, readable in _each_path(_find_starts(words) if deletes else [], ctx):
                if not readable:
                    return f"It deletes files it finds in {start}, a path that cannot be read before it runs."
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
        for word, readable in _each_path(targets, ctx):
            if not readable:
                return f"It changes {word}, a path that cannot be read before it runs."
            if reason := own(word, here):
                return reason
            if _outside(word, workspace, here):
                where = "" if here is None else " from a folder that cannot be read" if here is UNKNOWN else f" from {here}"
                return f"It changes {word}{where}, outside the project folder."
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
    the shell reads it: `$'\\x72m'` → `'rm'`. Also `$"…"` is `"…"`, and zsh's `=rm` (the
    program `rm` found on the path) is `rm`."""
    out, i, quote = [], 0, None
    while i < len(text):
        char = text[i]
        if quote != "'" and char == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if quote is None and text.startswith('$"', i):
            i += 1
            continue
        if quote is None and char == "=" and (i == 0 or text[i - 1] in " \t\n;&|(){}") \
                and re.match(r"[A-Za-z_]", text[i + 1:i + 2]):
            i += 1
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


# Variables the line sets (CONTRACT.md, "Approvals"): followed into the words where they are used,
# so the line is judged as if their text had been typed there.
CONVENTIONAL = {"PYTHON": "python", "PYTHON3": "python3", "NODE": "node", "NPM": "npm", "PIP": "pip", "UV": "uv"}
MAX_VARIANTS = 16
DECLARERS = {"export", "local", "declare", "typeset", "readonly", "env", "eval"}    # `eval X=~/x` sets X too
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "time", "coproc"}
# The names the agent's shell really has that a path may start with (harness/claude.py: the agent
# program inherits the studio's environment, secrets blanked; the shell sets PWD). Any other name
# the line does not set is empty in the agent's fresh shell or holds what the line cannot know, so a
# path that starts with it cannot be read (`rm -rf "$OUT"/*` is `rm -rf /*`).
SHELL_HAS = {"HOME", "PWD", "TMPDIR", "USER", "LOGNAME"}
NUMBERS = {"RANDOM", "SRANDOM", "SECONDS", "EPOCHSECONDS", "LINENO", "UID", "EUID", "PPID", "BASHPID"}
ASSIGN = re.compile(r"(?<![\w$-])([A-Za-z_]\w*)(\+?)=")
SETS_UNREADABLY = re.compile(r"(?<![\w.-])(?:read|mapfile|readarray|getopts|select|vared)(?![\w-])[^;&|\n]*|"
                             r"\bprintf\s+-v\s*\w+|\bfor\s+\w+\s*(?:;|\n|do\b)|"
                             r"\b(?:declare|typeset|local)\s+-\w*n\w*\s+\w+")      # a name that refers to another
# `find . -name x | while read f; do …`: the loop reads the listing before it.
WHILE_READ = re.compile(r"\|\s*while\s+(?:IFS=\S*\s+)?read(?:\s+-[a-zA-Z]+)*\s+([A-Za-z_]\w*)\s*(?=;|\n|do\b)")
UNSET = re.compile(r"(?<![\w-])unset\s+(?:-[vf]\s+)?((?:[A-Za-z_]\w*[ \t]*)+)")
FOR_LIST = re.compile(r"\bfor\s+([A-Za-z_]\w*)\s+in\s+([^;&|\n]*)|\bfor\s+([A-Za-z_]\w*)\s*\(([^()]*)\)")
VAR = re.compile(r"\$(?:([A-Za-z_]\w*)|\{([A-Za-z_]\w*)\})")
SPECIAL = re.compile(r"\$(?:([0-9$!?#])|\{([0-9$!?#])\})")
PARAM = re.compile(r"\$\{([A-Za-z_]\w*)(:?[-=+]|%%?|##?|/[/#%]?|\^\^?|,,?)((?:[^{}$`'\"]|\$(?![({]))*)\}")
NOT_PLAIN = re.compile(r"[\"'\\`\n;&|()<>]")
FIND_ACTS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
FIND_OTHER_OUTPUT = {"-printf", "-print0", "-ls", "-L", "-H", "-follow"}
HARMLESS_TEXT = {"date", "uuidgen", "uname", "nproc", "arch"}     # print a short plain text
SHELL_PATH = os.environ.get("SHELL") or "/bin/sh"   # `$0` in the agent's shell: the shell itself


def _closing(text: str, i: int) -> tuple[int, str]:
    """`text[i:]` starts `$(` or a backtick: → (the index after its end, the body)."""
    if text[i] == "`":
        end = i + 1
        while end < len(text) and text[end] != "`":
            end += 2 if text[end] == "\\" else 1
        return min(end + 1, len(text)), text[i + 1:end]
    depth, end = 1, i + 2
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
            return end + 1, text[i + 2:end]
        end += 1
    return len(text), text[i + 2:]


def _word_from(line: str, at: int) -> str:
    """The shell word that starts at `at`, quotes and substitutions and all."""
    i = at
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
        elif c == "'":
            close = line.find("'", i + 1)
            i = close + 1 if close != -1 else len(line)
        elif c == '"':
            j = i + 1
            while j < len(line) and line[j] != '"':
                if line.startswith("$(", j):
                    j = _closing(line, j)[0]
                    continue
                j += 2 if line[j] == "\\" else 1
            i = j + 1
        elif line.startswith("$(", i) or c == "`":
            i = _closing(line, i)[0]
        elif c.isspace() or c in ";&|<>()":
            break
        else:
            i += 1
    return line[at:i]


def _words_of(text: str) -> list[str]:
    """The shell words of `text`, each with its quotes and substitutions (`$(git ls-files '*.md')`
    is one word)."""
    words, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        word = _word_from(text, i)
        if not word:
            i += 1
            continue
        words.append(word)
        i += len(word)
    return words


def _glob_regex(pattern: str) -> str | None:
    """A shell pattern (`*.md`, `a?`) as a regular expression; None for a `[…]` class."""
    if "[" in pattern:
        return None
    return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)


def _tr_set(spec: str) -> str | None:
    classes = {"[:lower:]": "abcdefghijklmnopqrstuvwxyz", "[:upper:]": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               "[:digit:]": "0123456789", "[:space:]": " \t\n"}
    out, i = [], 0
    while i < len(spec):
        if spec[i] == "[":
            name = next((c for c in classes if spec.startswith(c, i)), None)
            if name is None:
                return None
            out.append(classes[name])
            i += len(name)
        elif spec[i] == "\\":
            return None
        elif i + 2 < len(spec) and spec[i + 1] == "-":
            out.append("".join(chr(c) for c in range(ord(spec[i]), ord(spec[i + 2]) + 1)))
            i += 3
        else:
            out.append(spec[i])
            i += 1
    return "".join(out)


def _tr(text: str, args: list[str]) -> str | None:
    """What `tr` makes of `text` (`tr A-Z a-z`, `tr ' ' '-'`, `tr -d x`); None for another form."""
    if args[:1] == ["-d"] and len(args) == 2 and (gone := _tr_set(args[1])) is not None:
        return "".join(c for c in text if c not in gone)
    if len(args) != 2 or any(re.fullmatch(r"-[a-zA-Z]+", a) for a in args):
        return None
    first, second = _tr_set(args[0]), _tr_set(args[1])
    if not first or not second:
        return None
    second = second + second[-1] * max(0, len(first) - len(second))
    return text.translate(str.maketrans(first, second[:len(first)]))


class _Glob(str):
    """A value that stands for file names: a `for` loop's pattern (`for f in *.md`), or what a
    listing of the project prints (`$(git ls-files)`, `find . -name x`). A file's name can hold
    `;`, so as text it cannot be read; as a path it is judged by the pattern, inside the project."""


class _Line:
    """What one line sets (and the lines it is nested in, through `outer`): each variable's
    values, read once."""

    def __init__(self, line: str, outer: "_Line | None" = None):
        self.line, self.masked, self.outer = line, _mask(line), outer
        self.own: dict[str, list[str | None]] = {}     # NAME → the words it is set to (None: unreadable)
        self.cache: dict[str, list[str] | None] = {}
        for match in ASSIGN.finditer(self.masked):
            if not self._command_word(match.start()):
                continue
            name, value_at = match.group(1), match.end()
            plus_or_array = match.group(2) or self.line[value_at:value_at + 1] == "("
            self.own.setdefault(name, []).append(None if plus_or_array else _word_from(self.line, value_at))
        for match in FOR_LIST.finditer(self.masked):
            name = match.group(1) or match.group(3)
            start, end = match.span(2) if match.group(1) else match.span(4)
            for word in _words_of(self.line[start:end]) or [None]:
                if word is not None and "$(" not in word and "`" not in word and re.search(r"[*?[]", _mask(word)):
                    word = _Glob(_unquote(word))
                self.own.setdefault(name, []).append(word)
        for match in re.finditer(r"\$\{([A-Za-z_]\w*):?=", self.masked):     # `${X:=dir}` sets X
            if param := PARAM.match(self.line, match.start()):
                self.own.setdefault(match.group(1), []).append(param.group(3) or '""')
        for match in UNSET.finditer(self.masked):
            for name in match.group(1).split():
                self.own.setdefault(name, []).append('""')
        listed = []
        for match in WHILE_READ.finditer(self.masked):
            start = max(self.masked.rfind(c, 0, match.start()) for c in ";&|\n(") + 1
            printed = self.printed(self.line[start:match.start()], frozenset())
            listed.append(match.span())
            self.own.setdefault(match.group(1), []).append(printed if isinstance(printed, _Glob) else None)
        for match in SETS_UNREADABLY.finditer(self.masked):
            if any(a <= match.start() < b for a, b in listed):
                continue
            for name in re.findall(r"[A-Za-z_]\w*", self.masked[match.start():match.end()]):
                self.own.setdefault(name, []).append(None)

    def raw(self, name: str) -> list[str | None]:
        return self.own.get(name, []) + (self.outer.raw(name) if self.outer else [])

    def _command_word(self, at: int) -> bool:
        """Does an assignment at `at` stand where the shell reads it as one (not `curl -d a=b`)?"""
        i = at
        for _ in range(12):
            j = i
            while j > 0 and self.masked[j - 1] in " \t":
                j -= 1
            if j == 0 or self.masked[j - 1] in ";&|(){}\n`":
                return True
            k = j
            while k > 0 and not self.masked[k - 1].isspace() and self.masked[k - 1] not in ";&|(){}\n`":
                k -= 1
            word = self.masked[k:j]
            if word in KEYWORDS or word in DECLARERS:
                return True
            if not (re.fullmatch(r"\w+\+?=\S*", word) or (word.startswith("-") and self._declared(k))):
                return False
            i = k
        return False

    def _declared(self, at: int) -> bool:
        """Is the option word at `at` one of `export`'s, `declare`'s and the like?"""
        before = self.masked[max(0, at - 80):at].split()
        while before and before[-1].startswith("-"):
            before.pop()
        return bool(before) and before[-1] in DECLARERS

    def sets(self, name: str) -> bool:
        return name in self.own or bool(self.outer and self.outer.sets(name))

    def read(self, name: str, seen: frozenset = frozenset()) -> list[str] | None:
        """Every value the line sets `name` to (a _Glob for file names), or None when one of them
        cannot be read."""
        if name in self.cache and not seen:
            return self.cache[name]
        found: list[str] | None = []
        if name in seen or len(seen) > MAX_DEPTH:
            found = None
        else:
            for raw in self.raw(name):
                value = raw if raw is None or isinstance(raw, _Glob) else self.text_of(raw, seen | {name})
                if value is None or NOT_PLAIN.search(value):
                    found = None
                    break
                if not any(value == v and type(value) is type(v) for v in found):
                    found.append(value)
        if not seen:
            self.cache[name] = found
        return found

    def values(self, name: str, seen: frozenset = frozenset()) -> list[str] | None:
        """The plain texts the line sets `name` to, or None when one of them is unreadable or a file name."""
        found = self.read(name, seen)
        return None if found is None or any(isinstance(v, _Glob) for v in found) else found

    def path_values(self, name: str) -> list[str] | None:
        found = self.read(name)
        return None if found is None else [str(v) for v in found]

    def has_glob(self, name: str) -> bool:
        return any(isinstance(v, _Glob) for v in self.read(name) or [])

    def single(self, name: str, seen: frozenset) -> str | None:
        found = self.read(name, seen)
        return found[0] if found and len(found) == 1 else None

    def text_of(self, raw: str, seen: frozenset = frozenset()) -> str | None:
        """The text the shell makes of the word `raw`, or None when it cannot be read. A variable
        the line does not set is kept as written (`$HOME`, `$ARGS`); a number is `1`."""
        out, i, quote, names = [], 0, None, False
        while i < len(raw):
            c = raw[i]
            if c == "\\" and quote != "'":
                if i + 1 < len(raw) and (quote is None or raw[i + 1] in '$`"\\'):
                    out.append(raw[i + 1])
                    i += 2
                    continue
                return None
            if quote is None and c in "'\"":
                quote = c
            elif c == quote:
                quote = None
            elif quote == "'":
                out.append(c)
            elif raw.startswith("$((", i):
                i = _closing(raw, i)[0]         # arithmetic: a number
                out.append("1")
                continue
            elif raw.startswith("$(", i) or c == "`":
                i, body = _closing(raw, i)
                value = self.printed(body, seen)
                if value is None:
                    return None
                names = names or isinstance(value, _Glob)
                out.append(value)
                continue
            elif c == "$" and (m := VAR.match(raw, i)):
                name = m.group(1) or m.group(2)
                if self.sets(name):
                    value = self.single(name, seen)
                    if value is None:
                        return None
                    names = names or isinstance(value, _Glob)
                    out.append(value)
                else:
                    out.append("1" if name in NUMBERS else CONVENTIONAL.get(name, m.group(0)))
                i = m.end()
                continue
            elif c == "$" and (m := SPECIAL.match(raw, i)):
                char = m.group(1) or m.group(2)
                out.append(SHELL_PATH if char == "0" and self.outer is None else
                           "1" if char in "$!?#" else m.group(0))
                i = m.end()
                continue
            elif c == "$" and (m := PARAM.match(raw, i)):
                value = self.param(m, seen)
                if value is None:
                    return None
                names = names or isinstance(value, _Glob)
                out.append(value)
                i = m.end()
                continue
            elif c == "$" and re.match(r"\$[{(]", raw[i:i + 2]):
                return None
            else:
                out.append(c)
            i += 1
        if quote is not None:
            return None
        return _Glob("".join(out)) if names else "".join(out)

    def param(self, match: re.Match, seen: frozenset, base: str | None = None) -> str | None:
        """`${NAME:-x}`, `${NAME:=x}`, `${NAME:+x}`, `${NAME%.md}`, `${NAME#*/}`, `${NAME/a/b}`,
        `${NAME,,}` worked out. A name the line does not set is empty in the agent's fresh shell,
        unless it is one the shell has (its value is not known: None for anything but a default)."""
        name, op, word = match.groups()
        if base is None:
            if self.sets(name):
                if (base := self.single(name, seen)) is None:
                    return None
            elif name not in SHELL_HAS and name not in NUMBERS:
                base = ""
        glob = isinstance(base, _Glob)
        if op in ("-", ":-", "=", ":="):
            if base is None:
                return match.group(0)
            return base if base else self.text_of(word, seen)
        if base is None:
            return None
        if op in ("+", ":+"):
            return self.text_of(word, seen) if base else ""
        if "$" in base:
            return None                     # trimming a path that is not known can climb out
        result: str | None = base
        cuts = range(len(base) + 1)
        if op in ("%", "%%"):
            result = next((base[:i] for i in (reversed(cuts) if op == "%" else cuts)
                           if fnmatch.fnmatchcase(base[i:], word)), base)
        elif op in ("#", "##"):
            result = next((base[i:] for i in (cuts if op == "#" else reversed(cuts))
                           if fnmatch.fnmatchcase(base[:i], word)), base)
        elif op.startswith("/"):
            pattern, _, replacement = word.partition("/")
            regex, new = _glob_regex(pattern), self.text_of(replacement, seen) if replacement else ""
            if regex is None or new is None or not pattern:
                return None
            regex = "^" + regex if op == "/#" else regex + "$" if op == "/%" else regex
            result = re.sub(regex, lambda _m: new, base, count=0 if op == "//" else 1)
        elif op in (",,", "^^"):
            result = base.lower() if op == ",," else base.upper()
        elif op in (",", "^"):
            result = (base[:1].lower() if op == "," else base[:1].upper()) + base[1:]
        return _Glob(result) if glob else result

    def printed(self, body: str, seen: frozenset) -> str | None:
        """What a substitution prints, for the few that can be read from their text: `pwd`,
        `dirname X`, `cd X && pwd`, `realpath X`, `mktemp`, `which X`, `git rev-parse
        --show-toplevel`, a listing of the project (`find . …`, `ls`, `git ls-files`), numbers
        (`seq`), `echo … | tr …` and plain text. None for anything else."""
        body = body.strip()
        if m := re.fullmatch(r"cd\s+(\S+)\s*(?:&&|;)\s*pwd(?:\s+-[PL])?", body):
            return self.text_of(m.group(1), seen)
        masked = _mask(body)
        if masked.count("|") == 1 and "||" not in masked and (m := re.fullmatch(r"(.*)\|\s*tr\s+(.*)", body, re.S)):
            first, args = self.printed(m.group(1), seen), [self.text_of(w, seen) for w in _words_of(m.group(2))]
            if first is None or any(a is None for a in args):
                return None
            changed = _tr(first, args)
            return None if changed is None else _Glob(changed) if isinstance(first, _Glob) else changed
        if re.search(r"[;&|\n<>`]|\$\(", masked):
            return None
        raw = _words_of(body)
        if not raw:
            return None
        args = [self.text_of(w, seen) for w in raw[1:]]
        if any(a is None for a in args):
            return None
        name, plain = Path(_unquote(raw[0])).name, [a for a in args if not a.startswith("-")]
        names = any(isinstance(a, _Glob) for a in args)

        def same_kind(text: str) -> str:
            return _Glob(text) if names else text

        def in_project(paths: list[str]) -> bool:
            return all(not p.startswith(("/", "~", "$")) and ".." not in Path(p).parts for p in paths)

        if name == "pwd":
            return "$PWD"
        if name == "mktemp":
            for k, a in enumerate(args):
                if a in ("-p", "--tmpdir") and k + 1 < len(args):
                    return args[k + 1].rstrip("/") + "/tmp.x"
                if a.startswith("--tmpdir="):
                    return a.split("=", 1)[1].rstrip("/") + "/tmp.x"
            return next((a for a in plain if "/" in a), "/tmp/tmp.x")
        if name == "dirname" and len(plain) == 1:
            path = plain[0].rstrip("/")
            return same_kind(path + "/.." if "$" in path else os.path.dirname(path) or ("/" if plain[0][:1] == "/" else "."))
        if name in ("realpath", "grealpath") or name == "readlink" and "-f" in args:
            return plain[0] if len(plain) == 1 else None
        if (name == "which" or name == "command" and "-v" in args) and len(plain) == 1:
            return plain[0] if "/" in plain[0] else "/usr/bin/" + plain[0]
        if name == "basename" and plain:
            base = Path(plain[0]).name or plain[0]
            if len(plain) == 2 and base.endswith(plain[1]) and base != plain[1]:
                base = base[:-len(plain[1])]
            return same_kind(base)
        if name == "git" and args[:1] == ["rev-parse"]:
            if "--show-toplevel" in args:
                return "$PWD"
            if any(a in ("--git-dir", "--git-common-dir", "--absolute-git-dir") for a in args):
                return ".git"
            return "x" if all(re.fullmatch(r"--short(?:=\d+)?|--verify|[\w./^~@-]+", a) and a not in (
                "--show-cdup", "--show-prefix") for a in args[1:]) else None
        if name == "git" and (args[:1] == ["ls-files"] or args[:1] == ["diff"] and "--name-only" in args or
                              args[:1] == ["ls-tree"] and "--name-only" in args):
            return _Glob("*") if in_project([a for a in plain[1:] if not a.startswith(("HEAD", "origin"))]) else None
        if name == "git" and (args[:1] == ["describe"] or args[:2] == ["branch", "--show-current"]):
            return "x"
        if name == "find":
            if any(a in FIND_ACTS or a in FIND_OTHER_OUTPUT for a in args):
                return None             # it changes something, or prints something else than names
            starts = _find_starts([name, *args])
            return _Glob(" ".join(s.rstrip("/") + "/*" for s in starts)) if in_project(starts) else None
        if name == "ls":
            return _Glob("*") if in_project(plain) else None
        if name == "seq" and all(re.fullmatch(r"-?[\d.]+|-w", a) for a in args):
            return "1"
        if name == "npm" and args in (["bin"], ["root"], ["prefix"]):
            return {"bin": "node_modules/.bin", "root": "node_modules", "prefix": "$PWD"}[args[0]]
        if name == "echo":
            return same_kind(" ".join(a for a in args if not re.fullmatch(r"-[neE]+", a)))
        if name == "printf":
            return args[0] if len(args) == 1 and "%" not in args[0] else None
        if name in HARMLESS_TEXT and all(re.fullmatch(r"[+%\w:./-]+", a) for a in args):
            return "x"                  # `date '+; rm x'` would print its format: not this
        return None


_LINES: dict[str, _Line] = {}


def _line_for(outer: str, text: str) -> _Line:
    """What the line `text` sets, nested in `outer` (the lines around it, already read)."""
    key = outer + "\n" + text
    if (found := _LINES.get(key)) is None:
        if len(_LINES) > 4096:
            _LINES.clear()
        # The lines around it are read already; if not (the store was cleared), read them whole.
        found = _LINES[key] = _Line(text, (_LINES.get(outer) or _Line(outer)) if outer else None)
    return found


def _line_of(key: str) -> _Line:
    return _LINES.get(key) or _Line(key)


def _expand(text: str, line: _Line) -> list[str]:
    """`text` with each variable the line sets to plain text (and each substitution that can be
    read) put in its place; one text per combination when a variable has several values (a `for`
    loop's words). A variable that cannot be read, or holds file names, is left as written."""
    used = {m.group(1) or m.group(2) for m in VAR.finditer(text)} | set(re.findall(r"\$\{([A-Za-z_]\w*)", text))
    values = {name: line.values(name) for name in used if line.sets(name)}
    values = {name: v for name, v in values.items() if v}
    several = [name for name, v in values.items() if len(v) > 1]
    if math.prod(len(values[n]) for n in several) > MAX_VARIANTS:
        for name in several:
            del values[name]
        several = []
    if "$" not in text and "`" not in text:
        return [text]
    texts = []
    for combination in itertools.product(*(values[n] for n in several)):
        chosen = {name: v[0] for name, v in values.items()} | dict(zip(several, combination))
        texts.append(_put_in(text, chosen, line))
    return texts


def _put_in(text: str, chosen: dict[str, str], line: _Line, paths: bool = False) -> str:
    """`text` with `chosen` values put in place of their variables, and every substitution and
    `${…}` form that can be read worked out. `paths`: `text` is one path a command writes, so file
    names (a _Glob) may be put in too; as text to run they never are."""
    def value(v: str, quote: str | None) -> str:
        return v if quote == '"' else " ".join(v.split())      # bash splits an unquoted value

    def fits(v: str | None) -> bool:
        return v is not None and not NOT_PLAIN.search(v) and (paths or not isinstance(v, _Glob))

    out, i, quote = [], 0, None
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            out.append(text[i:i + 2])
            i += 2
            continue
        if quote is None and c == "'":
            close = text.find("'", i + 1)
            close = len(text) - 1 if close == -1 else close
            out.append(text[i:close + 1])
            i = close + 1
            continue
        if c == '"':
            quote = None if quote else '"'
        elif text.startswith("$((", i):
            out.append("$((")
            i += 3
            continue
        elif text.startswith("$(", i) or c == "`":
            end, body = _closing(text, i)
            body = _put_in(body, chosen, line, paths)
            printed = line.printed(body, frozenset())
            if fits(printed):
                out.append(value(printed, quote))
            else:
                closer = "`" if c == "`" else ")"
                out.append(text[i:i + (1 if c == "`" else 2)] + body + (closer if text[end - 1:end] == closer else ""))
            i = end
            continue
        elif c == "$" and (m := VAR.match(text, i)):
            name = m.group(1) or m.group(2)
            if name in chosen:
                out.append(value(chosen[name], quote))
                i = m.end()
                continue
            if not line.sets(name) and (name in CONVENTIONAL or name in NUMBERS):
                out.append(CONVENTIONAL.get(name, "1"))
                i = m.end()
                continue
        elif c == "$" and (m := SPECIAL.match(text, i)) and (m.group(1) or m.group(2)) in "0$!?#":
            char = m.group(1) or m.group(2)
            if char != "0" or line.outer is None:
                out.append(SHELL_PATH if char == "0" else "1")
                i = m.end()
                continue
        elif c == "$" and (m := PARAM.match(text, i)):
            if fits(v := line.param(m, frozenset(), chosen.get(m.group(1)))) and v != m.group(0):
                out.append(value(v, quote))
                i = m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _unreadable_path(word: str, ctx: tuple[str, int]) -> bool:
    """Is `word`, a path a command writes or deletes, one that cannot be read before it runs: a
    substitution left unread, a variable the line sets to something unreadable, a trimmed path
    (`${PWD%/*}`, zsh's `$PWD:h`), or a path that starts with a variable the agent's shell does not
    have (`"$OUT"/*`: empty in a fresh shell)?"""
    if "$" not in word and "`" not in word:
        return False
    if re.search(r"\$\((?!\()|`|\$\{(?:[!(~=^]|\w+(?:%|#|/|\^|,|:(?![-=?+])))|\$\{?\w+\}?:[htreaAP]\b", word):
        return True
    line = _line_of(ctx[0])
    if any(line.sets(name) for name in re.findall(r"\$\{?([A-Za-z_]\w*)", word)):
        return True
    if start := re.match(r"\$\{?([A-Za-z_]\w*|[0-9@*])", word):
        name = start.group(1)
        if name[0].isdigit() or name in "@*":
            return ctx[1] == 0          # a nested line's own `$1` is what its caller passes
        return name not in SHELL_HAS and name not in NUMBERS
    return False


def _path_forms(word: str, ctx: tuple[str, int]) -> list[str] | None:
    """The paths `word` can stand for, as a command that writes or deletes reads it: file names
    (a loop's pattern, a listing of the project) put in place. None when it cannot be read."""
    if "$" not in word and "`" not in word:
        return [word]
    line = _line_of(ctx[0])
    names = [n for n in sorted(set(re.findall(r"\$\{?([A-Za-z_]\w*)", word))) if line.has_glob(n)]
    values = [line.path_values(n) for n in names]
    if any(v is None for v in values):
        return None
    combinations = list(itertools.islice(itertools.product(*values), MAX_VARIANTS + 1))
    if len(combinations) > MAX_VARIANTS:
        return None
    forms = [_put_in(word, dict(zip(names, c)), line, paths=True) for c in combinations]
    return None if any(_unreadable_path(form, ctx) for form in forms) else forms


def _each_path(words: list[str], ctx: tuple[str, int]):
    """→ (path, can it be read) for each of `words`, file names put in place."""
    for word in words:
        forms = _path_forms(word, ctx)
        if forms is None:
            yield word, False
        else:
            for form in forms:
                yield form, True


def _quoted_programs(text: str) -> str:
    """The pieces of `text` whose words are written in quotes (`"ssh" host`, `'git' 'push'`),
    with plain words unquoted, for the rules that name programs."""
    out = []
    for part, _ in _pieces(text):
        raw = _raw_words(part)
        words = [_unquote(w) if re.search(r"['\"\\]", w) and re.fullmatch(r"[\w./@%+=:,~-]+", _unquote(w))
                 else w for w in raw]
        if words != raw:
            out.append(" ".join(words))
    return "\n".join(out)


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
    if not _depth:
        _LINES.clear()          # what earlier lines set is read again for each new line
    places = (Path.home() / ".config" / "studio2", Path.home() / ".claude", *map(Path, studio_places))
    text, program = _split(command or "")
    text = _ansi_c(text)
    ctx = (_outer + "\n" + text, _depth)    # the line, and the lines it is nested in
    # The rules that name programs, on the line as typed: a substitution put in place below must
    # not take a rule's text with it (`$(npm root -g)`).
    quoted = _quoted_programs(text)
    for pattern, reason in RULES:
        if pattern.search(text) or pattern.search(program) or (quoted and pattern.search(quoted)):
            return reason
    # Variables the line sets, followed into where they are used: each text is judged in turn.
    for each in _expand(text, _line_for(_outer, text)):
        if reason := _judge(each, program, workspace, in_place, studio_places, places, ctx, text):
            return reason
    return None


def _judge(text: str, program: str, workspace: Path, in_place: bool, studio_places, places: tuple,
           ctx: tuple[str, int], typed: str = "") -> str | None:
    """The rules for one reading of the line (`text`, with `program` the here-document bodies
    given to another language)."""
    _depth = ctx[1]
    if text != typed:           # the line as typed was read by these already
        quoted = _quoted_programs(text)
        for pattern, reason in RULES:
            if pattern.search(text) or (quoted and pattern.search(quoted)):
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
