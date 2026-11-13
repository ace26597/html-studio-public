"""More places to look for skills and connectors (CONTRACT.md, "More places to look").

The studio always reads `~/.claude/skills`, the installed plugins and `~/.claude.json`. A person who
keeps skills in other folders, or connectors in another file, names those here; they are kept in
the config file (`library.skill_dirs`, `library.connector_files`), never copied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import library_places, read_config
from .store import write_json_atomic

ALWAYS = ["~/.claude/skills", "the plugins installed for Claude Code", "~/.claude.json"]


class PlaceError(ValueError):
    pass


def _skills_in(folder: Path) -> int:
    return len(list(folder.glob("*/SKILL.md"))) + (folder / "SKILL.md").is_file()


def _connectors_in(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    servers = data.get("mcpServers", data) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return None
    return sum(1 for value in servers.values() if isinstance(value, dict) and ("command" in value or "url" in value))


def view(settings, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    return {
        "always": ALWAYS,
        "skill_dirs": [{"path": str(path), "found": Path(path).is_dir(),
                        "skills": _skills_in(Path(path)) if Path(path).is_dir() else 0} for path in settings.skill_dirs],
        "connector_files": [{"path": str(path), "found": Path(path).is_file(),
                             "connectors": _connectors_in(Path(path)) or 0} for path in settings.connector_files],
        "from_env": {"skill_dirs": bool((environ.get("STUDIO2_SKILL_DIRS") or "").strip()),
                     "connector_files": bool((environ.get("STUDIO2_CONNECTOR_FILES") or "").strip())},
    }


def _checked(items, what: str) -> list[str]:
    if not isinstance(items, list):
        raise PlaceError(f"{what} must be a list of paths.")
    out = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise PlaceError(f"“{text}” is not a full path. Start it with / or ~.")
        if what == "skill_dirs":
            if not path.is_dir():
                raise PlaceError(f"There is no folder at {text}.")
        else:
            if not path.is_file():
                raise PlaceError(f"There is no file at {text}.")
            if _connectors_in(path) is None:
                raise PlaceError(f"{text} is not a file of connectors: it must be JSON with “mcpServers”, "
                                 "or a map of names to their settings.")
        if text not in out:
            out.append(text)
    return out


def save(settings, skill_dirs=None, connector_files=None, environ=None) -> dict:
    """Keep the lists in the config file and use them at once. A list left out stays as it is."""
    environ = os.environ if environ is None else environ
    current = read_config(settings.config_path)
    library = dict(current.get("library") if isinstance(current.get("library"), dict) else {})
    for key, given in (("skill_dirs", skill_dirs), ("connector_files", connector_files)):
        if given is not None:
            library[key] = _checked(given, key)
    settings.config_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(settings.config_path, {**current, "library": library})
    settings.skill_dirs = library_places(environ.get("STUDIO2_SKILL_DIRS"), library.get("skill_dirs"))
    settings.connector_files = library_places(environ.get("STUDIO2_CONNECTOR_FILES"), library.get("connector_files"))
    return view(settings, environ)
