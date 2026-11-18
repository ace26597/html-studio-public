"""Skills a person makes: saving, editing, history, and the skill file that carries one elsewhere.

CONTRACT.md, "Skills a person makes". Everything here works on the library's folders and takes
no running studio, so each rule is tested on its own. A write is built in a folder of its own
and moved into place whole: a refused or failed save leaves `data/skills/` as it was.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import threading
import unicodedata
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import workspace as ws
from .catalog import parse_front_matter
from .config import now_iso
from .library import NAME, Library, LibraryError

MAX_DESCRIPTION, MAX_BODY = 600, 12_000
MAX_REFERENCES, MAX_REFERENCE_BYTES, MAX_REFERENCES_BYTES = 12, 5 * 1024 * 1024, 10 * 1024 * 1024
MAX_REFERENCE_PATH = 80     # keeps twelve file lines inside the card's limit
CARD_LIMIT = 14_000
HISTORY_KEEP = 5
MAX_EXPORT_BYTES = 40 * 1024 * 1024
MAX_IMPORT_FILES, MAX_PACKED_BYTES, MAX_UNPACKED_BYTES = 600, 25 * 1024 * 1024, 60 * 1024 * 1024
MANIFEST = "studio-skill.json"
ASK_REASON = "A skill in your library is given to the agent in every project from now on."
REFERENCE_TYPES = frozenset(""".md .txt .html .css .json .csv .tsv .svg .png .jpg .jpeg .webp .gif .pdf .docx
 .pptx .xlsx""".split())
SCRIPT_TYPES = frozenset(".py .js .mjs .cjs .ts .sh .bash .zsh .rb .pl .php .ps1 .bat .cmd".split())
LEFT_OUT_FOLDERS = frozenset({".git", "node_modules", "__pycache__", ".history", ".venv", "__MACOSX"})
_writing = threading.Lock()


class SkillError(LibraryError):
    """A library error that may carry `details` (the preview of a file that needs a decision)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(status, code, message)
        self.details = details


def _invalid(message: str) -> SkillError:
    return SkillError(400, "invalid_request", message)


def _bad_file(reason: str) -> SkillError:
    return SkillError(400, "invalid_skill_file", reason)


def _left_out_file(name: str) -> bool:
    """Never carried in a skill file: the system's clutter, the manifest, and key files."""
    return name in (".DS_Store", MANIFEST) or name == ".env" or name.startswith(".env.")


# -- the fields of a skill --------------------------------------------------------------------
def is_studio_name(library: Library, name: str) -> bool:
    return (library.settings.library_dir / "skills" / name / "SKILL.md").is_file()


def check_name(library: Library, name: str) -> str:
    if not NAME.fullmatch(name):
        raise _invalid("name: use lower-case letters, digits, - and _ (at most 60 characters).")
    if is_studio_name(library, name):
        raise SkillError(409, "name_taken_by_studio", f"One of the studio's own skills is called {name}: "
                                                      "choose another name.")
    return name


def clean_description(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("description: say when the assistant should use this skill.")
    description = " ".join(value.split())
    if len(description) > MAX_DESCRIPTION:
        raise _invalid(f"description: at most {MAX_DESCRIPTION} characters (this one has {len(description)}).")
    return description


def clean_body(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("body: the instructions are empty.")
    body = value.strip()
    if len(body) > MAX_BODY:
        raise _invalid(f"body: at most {MAX_BODY:,} characters (this one has {len(body):,}).")
    if body.startswith("---"):
        raise _invalid("body: leave out the front-matter block; the studio writes it.")
    return body


def _split(text: str) -> tuple[list[str], str]:
    """A SKILL.md → (its front-matter lines other than name and description, its body)."""
    text = text.replace("\r\n", "\n")
    block = re.match(r"\s*---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
    kept, skipping = [], False
    for line in (block.group(1) if block else "").splitlines():
        top = re.match(r"([A-Za-z_][\w-]*):", line)
        if top and not line[:1].isspace():
            skipping = top.group(1).lower() in ("name", "description")
        if not skipping:
            kept.append(line)
    return kept, (text[block.end():] if block else text).strip()


def compose(name: str, description: str, body: str, kept: list[str] | tuple = ()) -> str:
    """The description is a folded block: as plain YAML a colon in it would break the file."""
    head = "\n".join([f"name: {name}", "description: >-", f"  {description}", *kept])
    return f"---\n{head}\n---\n\n{body}\n"


# -- reading a skill of the person's ----------------------------------------------------------
def _walk(folder: Path):
    """→ (path inside the skill, full path, stat) of each file carried, in order. Links are
    skipped: one could point at anything on this computer."""
    found = []
    for root, folders, names in os.walk(folder, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in LEFT_OUT_FOLDERS)
        for name in names:
            path = Path(root) / name
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and not _left_out_file(name):
                found.append((path.relative_to(folder).as_posix(), path, info))
    return sorted(found, key=lambda item: item[0])


def _own_folder(library: Library, name: str) -> Path:
    folder = library.own_skills / name
    if not NAME.fullmatch(name) or not (folder / "SKILL.md").is_file():
        raise SkillError(404, "unknown_skill", "There is no skill of yours with that name.")
    return folder


def _other_files(folder: Path) -> list[dict]:
    return [{"path": path, "size": info.st_size} for path, _full, info in _walk(folder) if path != "SKILL.md"]


def view(library: Library, name: str) -> dict:
    """One of the person's skills with its instructions and files."""
    folder = _own_folder(library, name)
    entry = library.skill(f"yours:{name}")
    if entry is None:
        library.catalog.ensure(force=True)
        entry = library.skill(f"yours:{name}")
    if entry is None:
        raise SkillError(404, "unknown_skill", "There is no skill of yours with that name.")
    _kept, body = _split((folder / "SKILL.md").read_text("utf-8", "replace"))
    return {**library.public(entry), "body": body, "files": _other_files(folder)}


# -- writing ----------------------------------------------------------------------------------
def _stage(library: Library) -> Path:
    """A folder on the same disk as the library and outside it, so the index never sees it."""
    return library.settings.data_dir / "run" / "skill-stage" / uuid.uuid4().hex


def _prune(history: Path, name: str) -> None:
    mine = re.compile(re.escape(name) + r"-\d{8}T\d{12}Z")
    versions = sorted(path for path in history.iterdir() if mine.fullmatch(path.name))
    for path in versions[:-HISTORY_KEEP]:
        shutil.rmtree(path, ignore_errors=True)


def _move_in(library: Library, name: str, stage: Path) -> bool:
    """Put a finished folder in place, the version it replaces going to `.history`. → replaced?"""
    target, history = library.own_skills / name, library.own_skills / ".history"
    target.parent.mkdir(parents=True, exist_ok=True)
    retired = None
    if target.exists():
        history.mkdir(exist_ok=True)
        retired = history / f"{name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        os.replace(target, retired)
    try:
        os.replace(stage, target)
    except OSError:
        if retired is not None:
            os.replace(retired, target)
        raise
    if retired is not None:
        _prune(history, name)
    return retired is not None


def save(library: Library, name: str, description: str, body: str, files: list[dict] | tuple = (),
         replace: bool = False) -> dict:
    """Write the skill. Files of a version it replaces stay unless `files` brings the same path.
    → {replaced, files: [path]}."""
    target = library.own_skills / name
    stage = _stage(library)
    with _writing:
        exists = (target / "SKILL.md").is_file()
        if exists and not replace:
            raise SkillError(409, "name_taken", f"You already have a skill called {name}.")
        try:
            kept: list[str] = []
            stage.parent.mkdir(parents=True, exist_ok=True)
            if exists:
                kept, _body = _split((target / "SKILL.md").read_text("utf-8", "replace"))
                shutil.copytree(target, stage, symlinks=True)
            else:
                stage.mkdir()
            (stage / "SKILL.md").write_text(compose(name, description, body, kept), "utf-8")
            for item in files:
                path = stage / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(item["data"])
            replaced = _move_in(library, name, stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    library.catalog.ensure(force=True)
    return {"replaced": replaced, "files": [item["path"] for item in _other_files(target)]}


def update(library: Library, name: str, fields: dict) -> dict:
    """`PUT`: a new description, new instructions or both; every other file and field stays."""
    folder = _own_folder(library, name)
    if fields.get("description") is None and fields.get("body") is None:
        raise _invalid("Give a description, a body or both.")
    text = (folder / "SKILL.md").read_text("utf-8", "replace")
    current = parse_front_matter(text.replace("\r\n", "\n")[:12_000]).get("description", "")
    _kept, body = _split(text)
    new_description = current if fields.get("description") is None else clean_description(fields["description"])
    new_body = body if fields.get("body") is None else clean_body(fields["body"])
    if new_description != current or new_body != body:
        save(library, name, new_description, new_body, replace=True)
    return view(library, name)


# -- the tool ---------------------------------------------------------------------------------
def _size_text(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, round(size / 1024))} KB"


def read_references(workspace: Path, items) -> list[dict]:
    """The project's files for a skill, read now: what the person is shown is what is saved,
    whatever happens to the project's copy while the card waits."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise _invalid("files: give a list of {path, as}.")
    if len(items) > MAX_REFERENCES:
        raise _invalid(f"files: at most {MAX_REFERENCES} reference files (this has {len(items)}).")
    out, seen, total = [], set(), 0
    for item in items:
        relative = item.get("path") if isinstance(item, dict) else None
        if not isinstance(relative, str) or not relative.strip():
            raise _invalid("files: each one needs the `path` of a file in this project.")
        try:
            source = ws.safe_path(workspace, relative)
        except ws.InvalidPath:
            raise _invalid(f"files: {relative} is not a file inside this project.") from None
        wanted = item.get("as") or source.name
        parts = PurePosixPath(str(wanted)).parts
        if (not parts or len(str(wanted)) > MAX_REFERENCE_PATH or "\\" in str(wanted) or str(wanted).startswith("/")
                or any(part.startswith(".") or any(ord(c) < 32 for c in part) for part in parts)):
            raise _invalid(f"files: {wanted} cannot be used as a file name in a skill.")
        for name in (source.name, parts[-1]):
            if PurePosixPath(name).suffix.lower() not in REFERENCE_TYPES:
                raise _invalid(f"files: {name} is not allowed. A skill made here holds material to read or follow "
                               f"({' '.join(sorted(REFERENCE_TYPES))}), and no scripts.")
        if not source.is_file():
            raise _invalid(f"files: there is no file at {relative} in this project.")
        size = source.stat().st_size
        if size > MAX_REFERENCE_BYTES:
            raise _invalid(f"files: {relative} is {_size_text(size)}; each file may be at most 5 MB.")
        total += size
        if total > MAX_REFERENCES_BYTES:
            raise _invalid("files: the reference files may be at most 10 MB together.")
        path = "reference/" + "/".join(parts)
        if path.casefold() in seen:
            raise _invalid(f"files: two files would be saved as {path}.")
        seen.add(path.casefold())
        out.append({"path": path, "data": source.read_bytes(), "size": size})
    return out


def plan(library: Library, workspace: Path, args: dict) -> dict:
    """Check everything `save_skill` was given → what to ask the person and what to save."""
    if library.settings.shared:
        raise SkillError(403, "not_for_shared", "On a shared studio only its owner adds skills to the library.")
    name = check_name(library, str(args.get("name") or "").strip())
    description, body = clean_description(args.get("description")), clean_body(args.get("body"))
    files = read_references(workspace, args.get("files"))
    replace = args.get("replace") is True
    folder = library.own_skills / name
    exists = (folder / "SKILL.md").is_file()
    if exists and not replace:
        raise SkillError(409, "name_taken", f"You already have a skill called {name}. Nothing was saved. To change it, "
                                            "read it first and call save_skill again with replace: true.")
    given = {item["path"] for item in files}
    kept = [item["path"] for item in _other_files(folder) if item["path"] not in given] if exists else []
    parts = [f"When it is used: {description}", f"Instructions:\n{body}"]
    if files:
        parts.append("Files:\n" + "\n".join(f"{item['path']} ({_size_text(item['size'])})" for item in files))
    if kept:
        more = f", and {len(kept) - 12} more" if len(kept) > 12 else ""
        parts.append("Files kept from the version it replaces: " + ", ".join(kept[:12]) + more)
    title = f"Replace your skill “{name}”" if exists else f"Save the skill “{name}” to your library"
    return {"name": name, "description": description, "body": body, "files": files, "replace": replace,
            "title": title, "detail": "\n\n".join(parts)}


# -- the skill file ---------------------------------------------------------------------------
def is_script(path: str, mode: int = 0) -> bool:
    return PurePosixPath(path).suffix.lower() in SCRIPT_TYPES or bool(mode & 0o111)


def export(library: Library, skill_id: str) -> tuple[str, bytes]:
    """Any skill in the index → (file name, the bytes of its `.skill.zip`)."""
    entry = library.skill(skill_id)
    if entry is None:
        raise SkillError(404, "unknown_skill", "There is no such skill in the library.")
    files = _walk(Path(entry["dir"]).resolve())
    if sum(info.st_size for _path, _full, info in files) > MAX_EXPORT_BYTES:
        raise SkillError(413, "too_large", "This skill is larger than 40 MB, too large to export as a file.")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, full, info in files:
            when = datetime.fromtimestamp(info.st_mtime, timezone.utc).timetuple()[:6]
            member = zipfile.ZipInfo(path, date_time=when if 1980 <= when[0] <= 2107 else (1980, 1, 1, 0, 0, 0))
            member.compress_type, member.create_system = zipfile.ZIP_DEFLATED, 3
            member.external_attr = (stat.S_IFREG | stat.S_IMODE(info.st_mode)) << 16
            archive.writestr(member, full.read_bytes())
        archive.writestr(MANIFEST, json.dumps({
            "format": 1, "name": entry["name"], "description": entry["description"], "source": entry["source"],
            "exported_at": now_iso(), "files": [path for path, _full, _info in files],
            "scripts": [path for path, _full, info in files if is_script(path, info.st_mode)]},
            ensure_ascii=False, indent=2))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", entry["name"]).strip("-.") or "skill"
    return f"{safe}.skill.zip", out.getvalue()


def _check_member_path(raw: str) -> None:
    path = raw[:-1] if raw.endswith("/") else raw
    parts = path.split("/")
    if (path.startswith("/") or "\\" in path or re.match(r"[A-Za-z]:", path)
            or any(part in ("", ".", "..") or len(part) > 240 or any(ord(c) < 32 for c in part) for part in parts)):
        raise _bad_file(f"The file holds a path that is not allowed: {raw[:120]}")


def _members(archive: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, str]], str | None]:
    """Everything is judged from the zip's directory, before a byte is unpacked. → ([(entry, path
    inside the skill)], where the zip holds its manifest, or None)."""
    infos = archive.infolist()
    files = [info for info in infos if not info.is_dir()]
    if len(files) > MAX_IMPORT_FILES:
        raise _bad_file(f"The file holds {len(files)} files; a skill may have at most {MAX_IMPORT_FILES}.")
    if sum(info.file_size for info in files) > MAX_UNPACKED_BYTES:
        raise _bad_file("The file unpacks to more than 60 MB.")
    for info in infos:
        _check_member_path(info.orig_filename)
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind == stat.S_IFLNK:
            raise _bad_file(f"The file holds a link, which is not allowed: {info.filename[:120]}")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise _bad_file(f"The file holds something that is not a file: {info.filename[:120]}")
        if info.flag_bits & 0x1:
            raise _bad_file("The file is protected with a password.")
    wanted = [info for info in files
              if not _left_out_file(info.filename.rsplit("/", 1)[-1])
              and not LEFT_OUT_FOLDERS.intersection(info.filename.split("/")[:-1])]
    candidates = [info.filename for info in wanted if info.filename.count("/") <= 1
                  and info.filename.rsplit("/", 1)[-1] == "SKILL.md"]
    if not candidates:
        raise _bad_file("There is no SKILL.md at the top of the file or inside its one top folder.")
    if len(candidates) > 1:
        raise _bad_file("The file holds more than one SKILL.md: " + ", ".join(candidates[:4]))
    prefix = candidates[0][:-len("SKILL.md")]
    if any(not info.filename.startswith(prefix) for info in wanted):
        raise _bad_file("Everything in the file must be inside the one folder that holds SKILL.md.")
    members, seen = [], set()
    for info in wanted:
        path = info.filename[len(prefix):]
        key = unicodedata.normalize("NFC", path).casefold()
        if key in seen:
            raise _bad_file(f"The file holds the same path twice: {path[:120]}")
        seen.add(key)
        members.append((info, path))
    folders = {"/".join(key.split("/")[:n]) for key in seen for n in range(1, key.count("/") + 1)}
    if folders & seen:
        raise _bad_file("The file holds a path that is both a file and a folder.")
    return members, prefix + MANIFEST if prefix + MANIFEST in archive.namelist() else None


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, room: int) -> bytes:
    """Read one entry, counting what really comes out: the sizes in a zip's directory can lie."""
    chunks, size = [], 0
    with archive.open(info) as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            if size > room:
                raise _bad_file("The file unpacks to more than 60 MB.")
            chunks.append(chunk)
    return b"".join(chunks)


def _each_file(archive: zipfile.ZipFile, members: list):
    room = MAX_UNPACKED_BYTES
    for info, path in members:
        try:
            data = _read_member(archive, info, room)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError) as exc:
            raise _bad_file(f"{path[:120]} cannot be read from the file ({exc.__class__.__name__}).") from None
        room -= len(data)
        yield info, path, data


def _named(manifest_name, front: dict, given: str | None) -> str:
    if given:
        return given
    for candidate in (manifest_name, front.get("name")):
        if isinstance(candidate, str) and NAME.fullmatch(candidate):
            return candidate
    raise _bad_file(f"“{str(front.get('name'))[:60]}” cannot be a skill's name here: give it another name.")


def inspect(library: Library, data: bytes, given: str | None = None) -> tuple[dict, bool]:
    """Read a skill file through without writing anything → (the preview, must SKILL.md's name change?)."""
    if len(data) > MAX_PACKED_BYTES:
        raise _bad_file("The file is larger than 25 MB.")
    if given is not None and not NAME.fullmatch(given):
        raise _invalid("name: use lower-case letters, digits, - and _ (at most 60 characters).")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError, EOFError):
        raise _bad_file("This is not a zip file.") from None
    with archive:
        members, manifest_path = _members(archive)
        listed, scripts, text = [], [], ""
        for info, path, content in _each_file(archive, members):
            listed.append({"path": path, "size": len(content)})
            if is_script(path, info.external_attr >> 16):
                scripts.append(path)
            if path == "SKILL.md":
                text = content.decode("utf-8", "replace").replace("\r\n", "\n")
        manifest_name = None
        if manifest_path:
            try:
                manifest_name = json.loads(archive.read(manifest_path)[:65_536]).get("name")
            except (ValueError, AttributeError, zipfile.BadZipFile, zlib.error, OSError):
                manifest_name = None
    front = parse_front_matter(text[:12_000])
    if not front.get("name") or not front.get("description"):
        raise _bad_file("SKILL.md must begin with front matter that gives a name and a description.")
    name = _named(manifest_name, front, given)
    _kept, body = _split(text)
    preview = {"name": name, "description": front["description"], "body_start": body[:1_200],
               "files": sorted(listed, key=lambda item: item["path"]), "scripts": sorted(scripts),
               "size": sum(item["size"] for item in listed),
               "exists": (library.own_skills / name / "SKILL.md").is_file(),
               "taken_by_studio": is_studio_name(library, name)}
    return preview, bool(given and front["name"] != given)


def _renamed(content: bytes, name: str) -> bytes:
    text = content.decode("utf-8", "replace")
    block = re.match(r"\s*---[ \t]*\r?\n.*?\r?\n---", text, re.S)
    head = re.sub(r"(?m)^name:.*?(\r?)$", lambda found: f"name: {name}{found.group(1)}", block.group(0), count=1)
    return (head + text[block.end():]).encode("utf-8")


def import_skill(library: Library, data: bytes, name: str | None = None, dry_run: bool = False,
                 replace: bool = False, allow_scripts: bool = False) -> dict:
    """A skill file → its preview (`dry_run`), or the skill as saved in the library."""
    if library.settings.shared:
        raise SkillError(403, "not_for_shared", "On a shared studio only its owner adds skills to the library.")
    preview, rename = inspect(library, data, name or None)
    if dry_run:
        return preview
    final = preview["name"]
    if preview["taken_by_studio"]:
        raise SkillError(409, "name_taken_by_studio", f"One of the studio's own skills is called {final}: import this "
                                                      "one under another name.", preview)
    if preview["exists"] and not replace:
        raise SkillError(409, "name_taken", f"You already have a skill called {final}.", preview)
    if preview["scripts"] and not allow_scripts:
        count = len(preview["scripts"])
        raise SkillError(409, "scripts_need_confirmation",
                         f"This skill brings {count} script{'' if count == 1 else 's'} the agent may run.", preview)
    stage = _stage(library)
    with _writing:
        if (library.own_skills / final / "SKILL.md").is_file() and not replace:
            raise SkillError(409, "name_taken", f"You already have a skill called {final}.", preview)
        try:
            stage.mkdir(parents=True)
            root = stage.resolve()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members, _manifest = _members(archive)
                for info, path, content in _each_file(archive, members):
                    target = (root / path).resolve()
                    if not target.is_relative_to(root):
                        raise _bad_file(f"The file holds a path that is not allowed: {path[:120]}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(_renamed(content, final) if rename and path == "SKILL.md" else content)
                    target.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
            _move_in(library, final, stage)
        except OSError as exc:
            raise _bad_file(f"The skill could not be unpacked ({exc.__class__.__name__}).") from None
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    library.catalog.ensure(force=True)
    return library.public(library.skill(f"yours:{final}"))
