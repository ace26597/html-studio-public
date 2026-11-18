"""Git LFS files, as the attach form tells about them (CONTRACT.md, Inspect `lfs`).

A repository that keeps files in Git LFS commits a small pointer file in place of each one, and the
LFS program (a git filter) swaps the real file in on checkout. The studio's own git never runs a
filter (workspace.HELD_ATTRIBUTES), so a safe copy holds the pointer files. Filling a copy with
the real files is not built; the person is told before the copy is made.

A file counts when what is committed is a pointer, recognised by its content. That is exactly
what a copy will hold, whatever the `.gitattributes` rules say (case, negated lines,
`.git/info/attributes`, files committed before a rule existed), so no pattern rule of git's is
re-implemented here. The commit is read with `ls-tree` and `cat-file --batch` (object names on
standard input, never paths on a command line); neither runs a filter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

SPEC = b"version https://git-lfs.github.com/spec/v1\n"
POINTER_MIN, POINTER_MAX = 100, 1024      # the LFS spec keeps a pointer under 1024 bytes
POINTER_RE = re.compile(rb"\Aversion https://git-lfs\.github\.com/spec/v1\n"
                        rb"(?:[a-z0-9.-]+ [^\n]*\n)*?"
                        rb"oid sha256:([0-9a-f]{64})\n(?:[a-z0-9.-]+ [^\n]*\n)*?size (\d+)\n"
                        rb"(?:[a-z0-9.-]+ [^\n]*\n)*\Z")
SHOWN = 5

# (folder, git arguments, standard input) → (exit code, standard output as bytes)
Reader = Callable[[Path, list[str], bytes | None], tuple[int, bytes]]


def parse(data: bytes | None) -> tuple[str, int] | None:
    """→ (oid, size) for an LFS pointer file's bytes, else None."""
    if not data or not POINTER_MIN <= len(data) < POINTER_MAX or not data.startswith(SPEC):
        return None
    match = POINTER_RE.match(data)
    return (match.group(1).decode(), int(match.group(2))) if match else None


def _committed(folder: Path, read: Reader) -> dict[str, bytes]:
    """path → pointer bytes for every committed blob at HEAD that is an LFS pointer."""
    code, out = read(folder, ["ls-tree", "-r", "-l", "-z", "HEAD"], None)
    if code != 0:
        return {}
    candidates: dict[str, list[str]] = {}
    for entry in out.split(b"\0"):
        head, _, path = entry.partition(b"\t")
        parts = head.split()
        if (len(parts) == 4 and parts[0] in (b"100644", b"100755") and parts[1] == b"blob"
                and parts[3].isdigit() and POINTER_MIN <= int(parts[3]) < POINTER_MAX):
            candidates.setdefault(parts[2].decode(), []).append(path.decode("utf-8", "replace"))
    if not candidates:
        return {}
    names = list(candidates)
    code, out = read(folder, ["cat-file", "--batch"], "".join(f"{name}\n" for name in names).encode())
    found, at = {}, 0
    for name in names:
        end = out.find(b"\n", at)
        if end < 0:
            break
        header = out[at:end].split()
        at = end + 1
        if len(header) < 3 or header[1] != b"blob" or not header[2].isdigit():
            continue
        size = int(header[2])
        data = out[at:at + size]
        at += size + 1
        if parse(data):
            for path in candidates[name]:
                found[path] = data
    return found


def _is_pointer_file(path: Path, pointer: bytes) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(pointer):
            return False
        return path.read_bytes() == pointer
    except OSError:
        return False


def inspect(folder: Path, git: bool, read: Reader, plain_names: list[str] | None = None) -> dict | None:
    """→ {files, names, not_here} or None. `not_here`: those whose file in the folder is only the
    pointer too (never downloaded there). For a plain folder every pointer file is not here."""
    if git:
        found = _committed(folder, read)
        not_here = sorted(path for path, pointer in found.items() if _is_pointer_file(folder / path, pointer))
    else:
        found = {}
        for name in plain_names or []:
            target = folder / name
            try:
                if target.is_symlink() or not target.is_file() or \
                        not POINTER_MIN <= target.stat().st_size < POINTER_MAX:
                    continue
                data = target.read_bytes()
            except OSError:
                continue
            if parse(data):
                found[name] = data
        not_here = sorted(found)
    if not found:
        return None
    return {"files": len(found), "names": sorted(found)[:SHOWN], "not_here": not_here[:SHOWN],
            "not_here_count": len(not_here)}


def _list(names: list[str], total: int) -> str:
    return ", ".join(names) + (f" and {total - len(names)} more" if total > len(names) else "")


def note(found: dict | None, git: bool) -> str | None:
    """The attach form's note, in plain words (no code marks: the form shows text as it is)."""
    if not found:
        return None
    count = found["files"]
    many = count != 1
    names = _list(found["names"], count)
    if not git:
        return (f"{count} file{'s' if many else ''} in this folder {'are' if many else 'is'} only Git LFS pointer "
                f"file{'s' if many else ''} ({names}): small text files that stand in for large files kept "
                "elsewhere. The real files are not in this folder, so images, models or data kept that way will be "
                "missing in the app, copied or not. Get the real files from where this folder came from.")
    text = (f"{count} file{'s' if many else ''} in this repository {'are' if many else 'is'} kept in Git LFS "
            f"(large files git stores apart): {names}. A safe copy holds only the small pointer file in place of "
            f"each, so images, models or data kept that way will be missing or broken in the copy. To work with "
            "them, bring the folder in to be edited in place instead.")
    missing = found["not_here_count"]
    if missing:
        text += (f" {missing} of them {'are' if missing != 1 else 'is'} not downloaded in this folder either "
                 f"({_list(found['not_here'], missing)}): run git lfs pull in the folder first.")
    return text
