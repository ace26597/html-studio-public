"""Start a library connector outside the project folder, then become it.

CONTRACT.md, "A library connector starts outside the project folder". The agent program starts
every stdio connector in the project folder, where the agent writes. The studio therefore gives
it this file to run instead:

    <python> -I -S connector_launch.py <base folder> <command> [args...]

It makes a new, empty, owner-only folder under <base folder>, changes to it, drops variables that
point into the project (the folder it was started in), and replaces itself with <command>.

Run with -I -S and import only Python's own modules: nothing from the folder it starts in or the
folder it goes to is ever loaded. It never prints a variable's value (a connector's secrets are
in its environment) and puts nothing on a command line that was not already there.
"""

import os
import shutil
import stat
import sys
import time

START_FOLDER_AGE = 86400          # a start folder untouched for a day is removed


def fail(message: str) -> "None":
    sys.stderr.write(f"studio connector launcher: {message}\n")
    sys.stderr.flush()
    os._exit(127)


def inside(path: str, project: str) -> bool:
    if not path or not os.path.isabs(path):
        return False
    for candidate in {os.path.normpath(path), os.path.realpath(path)}:
        if candidate == project or candidate.startswith(project.rstrip(os.sep) + os.sep):
            return True
    return False


PATH_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-~+@")


def mentions(value: str, spelling: str) -> bool:
    """The folder `spelling` appears in `value` as a whole path: followed by the end, a separator or
    a character no file name continues with ("/a/proj" is in "--require /a/proj/x.js", not in "/a/project2")."""
    start = value.find(spelling)
    while start != -1:
        end = start + len(spelling)
        if end == len(value) or value[end] == os.sep or value[end] not in PATH_CHARS:
            return True
        start = value.find(spelling, start + 1)
    return False


def cleaned(env: dict, project: str | None, folder: str) -> dict:
    """The environment without what points into the project. Nothing is dropped when the project
    is the home folder or above it (that would take HOME and the person's own tools with it)."""
    out = dict(env)
    home = os.path.realpath(os.path.expanduser("~"))
    if project and not (home == project or inside(home, project)):
        # The project as the agent program may have spelled it (PWD before links are resolved, such
        # as /tmp for /private/tmp on a Mac) and as it really is.
        spellings = {project}
        given = env.get("PWD") or ""
        if os.path.isabs(given) and os.path.realpath(given) == project:
            spellings.add(os.path.normpath(given))
        for name, value in env.items():
            if name == "PWD":
                continue
            entries = value.split(os.pathsep)
            kept = [entry for entry in entries if not inside(entry, project)]
            rest = os.pathsep.join(kept)
            removed = len(kept) != len(entries)
            if (removed and not any(kept)) or any(mentions(rest, spelling) for spelling in spellings):
                del out[name]          # NODE_OPTIONS=--require <project>/x.js: no part of it is kept
            elif removed:
                out[name] = rest
    out["PWD"] = folder
    out.pop("OLDPWD", None)
    return out


def is_start_folder(name: str) -> bool:
    return len(name) == 18 and name.startswith("c-") and all(c in "0123456789abcdef" for c in name[2:])


def remove_old_folders(base: str, now: float | None = None) -> None:
    """Remove start folders under `base` whose last change is more than a day old: each by its exact
    path, only a real folder (never a link), and without following a link inside it. `base` itself
    must already be checked (a real folder of this user)."""
    limit = (time.time() if now is None else now) - START_FOLDER_AGE
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not is_start_folder(name):
            continue
        path = os.path.join(base, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mtime > limit:
            continue
        if shutil.rmtree.avoids_symlink_attacks:
            shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        fail("usage: connector_launch.py <start folder> <command> [args...]")
    base, command, args = argv[1], argv[2], argv[3:]
    try:
        project = os.path.realpath(os.getcwd())
    except OSError:
        project = None
    if not os.path.isabs(base):
        fail("the start folder must be a full path.")
    if project and inside(project, os.path.realpath(base)):
        project = None      # started from the studio's own folder (the Test button): no project to leave
    if os.sep in command and not os.path.isabs(command):
        fail(f"the command {command!r} is a relative path. A connector no longer starts in the project "
             "folder: give the command's full path.")
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        info = os.lstat(base)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            fail(f"{base} is not a folder of this user (a link, or someone else's); not starting there.")
        os.chmod(base, 0o700)
        remove_old_folders(base)
        folder = os.path.join(base, "c-" + os.urandom(8).hex())
        os.mkdir(folder, 0o700)
        os.chdir(folder)
    except OSError as exc:
        fail(f"could not make the start folder under {base}: {exc.strerror or exc}")
    env = cleaned(dict(os.environ), project, folder)
    try:
        os.execvpe(command, [command, *args], env)
    except OSError as exc:
        fail(f"could not start {command!r}: {exc.strerror or exc}")


if __name__ == "__main__":
    main(sys.argv)
