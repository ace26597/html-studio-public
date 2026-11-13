"""Which shell commands the agent must ask about first (CONTRACT.md, "Approvals").

Pattern matching on the command text. It is a seat belt, not a sandbox: it catches the
ordinary ways an agent reaches outside its project, not a determined attempt to hide one.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

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
    (_cmd(r"git\s+(?:-\S+\s+)*push|gh"), "It sends something to GitHub."),
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
REDIRECT = re.compile(r"(?<![0-9&>])(?:>>?|&>>?)\s*(\S+)")
SAFE_ABSOLUTE = ("/dev/null", "/dev/stdout", "/dev/stderr", "/tmp/", "/private/tmp/", "/var/folders/", "/dev/fd/")


def _outside(token: str, workspace: Path) -> bool:
    token = token.strip("'\"")
    if not token or token.startswith("-") or "://" in token:
        return False
    if token.startswith("~") or token.startswith("$HOME") or token.startswith("${HOME}"):
        return True
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
    match = re.match(r"[^\s;&|()<>]+", text[start:])
    return match.group(0) if match else ""


def _parts(command: str) -> list[str]:
    """The command split at `;`, `&`, `|` and new lines that are not inside quotes."""
    masked, parts, at = _mask(command), [], 0
    for match in re.finditer(r"[;&|\n]+", masked):
        parts.append(command[at:match.start()])
        at = match.end()
    parts.append(command[at:])
    return parts


def _path_reason(command: str, workspace: Path) -> str | None:
    masked = _mask(command)
    for match in REDIRECT.finditer(masked):   # a `>` inside quotes is text, not a redirection
        target = _word_at(command, match.start(1))
        if _outside(target, workspace):
            return f"It writes to {target}, outside the project folder."
    for part in _parts(command):
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            words = part.split()
        words = [w for w in words if not re.fullmatch(r"\w+=\S*", w)]
        if not words:
            continue
        name = Path(words[0]).name
        if name in ("sudo", "env", "command", "xargs") and len(words) > 1:
            name, words = Path(words[1]).name, words[1:]
        if name not in MUTATORS or (name == "sed" and not any(w.startswith("-i") for w in words)):
            continue
        targets = words[1:]
        if name == "cp":
            # Copying only reads its sources (the agent copies helper files out of the studio's
            # skill folders); what changes is where the copy lands.
            if "-t" in targets[:-1]:
                targets = [targets[targets.index("-t") + 1]]
            else:
                targets = [w for w in targets if not w.startswith("-")][-1:]
        for word in targets:
            if _outside(word, workspace):
                return f"It changes {word}, outside the project folder."
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
        if reader not in ("cat", "tee"):
            program.extend(lines[i:end])
        i = end + 1
    return "\n".join(shell), "\n".join(program)


def risky_command(command: str, workspace: Path) -> str | None:
    """→ a one-sentence reason when the command needs the user's approval, else None."""
    text, program = _split(command or "")
    for pattern, reason in RULES:
        if pattern.search(text) or pattern.search(program):
            return reason
    return _pip_reason(text) or _pip_reason(program) or _path_reason(text, workspace)
