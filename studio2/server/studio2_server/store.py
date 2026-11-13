"""Project records on disk: projects/<id>/{project.json, events.jsonl, workspace/}."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import tempfile
import time
from pathlib import Path

from . import workspace as ws
from .config import Settings, now_iso

ID_RE = re.compile(r"^p_[0-9a-f]{8}$")


class ProjectNotFound(Exception):
    pass


class NoFreePort(Exception):
    pass


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def write_json_atomic(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.projects_dir
        self.root.mkdir(parents=True, exist_ok=True)
        # Projects edited in place: id → the person's folder (their versions live in history.git).
        self._in_place: dict[str, Path] = {}
        for record in self.list():
            self._remember(record)

    def _remember(self, record: dict) -> None:
        attach = record.get("attach") or {}
        if attach.get("mode") == "in_place" and attach.get("path"):
            folder = Path(attach["path"])
            self._in_place[record["id"]] = folder
            ws.register_history(folder, self.dir(record["id"]) / "history.git")

    def in_place_folders(self) -> set[Path]:
        return {folder.resolve() for folder in self._in_place.values()}

    def dir(self, project_id: str) -> Path:
        if not ID_RE.match(project_id):
            raise ProjectNotFound(project_id)
        return self.root / project_id

    def workspace(self, project_id: str) -> Path:
        """The project's working folder: its own `workspace/`, or the person's folder in place."""
        return self._in_place.get(project_id) or self.dir(project_id) / "workspace"

    def events_path(self, project_id: str) -> Path:
        return self.dir(project_id) / "events.jsonl"

    def get(self, project_id: str) -> dict:
        try:
            return json.loads((self.dir(project_id) / "project.json").read_text("utf-8"))
        except (OSError, ValueError):
            raise ProjectNotFound(project_id) from None

    def list(self) -> list[dict]:
        records = []
        for child in self.root.iterdir():
            if ID_RE.match(child.name):
                try:
                    records.append(self.get(child.name))
                except ProjectNotFound:
                    continue
        return sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)

    def save(self, record: dict) -> dict:
        write_json_atomic(self.dir(record["id"]) / "project.json", record)
        self._remember(record)
        return record

    def update(self, project_id: str, **changes) -> dict:
        """Read-modify-write with no await in between, so it is atomic on the event loop."""
        record = {**self.get(project_id), **changes, "updated_at": now_iso()}
        return self.save(record)

    def allocate_port(self) -> int:
        used = {record.get("port") for record in self.list()}
        low, high = self.settings.port_range
        for port in range(low, high + 1):
            if port not in used and port_is_free(port):
                return port
        raise NoFreePort(f"No free preview port between {low} and {high}.")

    def new_id(self) -> str:
        while True:
            project_id = "p_" + secrets.token_hex(4)
            if not (self.root / project_id).exists():
                return project_id

    def create(self, name: str, connection: str, template: str = "blank", project_id: str | None = None,
               make_workspace: bool = True, **extra) -> dict:
        """`project_id`/`make_workspace`/`extra`: an attached app, whose folder is made by attach.py."""
        port = self.allocate_port()
        project_id = project_id or self.new_id()
        self.dir(project_id).mkdir(parents=True, exist_ok=True)
        if make_workspace:
            (self.dir(project_id) / "workspace").mkdir(parents=True)
        stamp = now_iso()
        return self.save({
            "id": project_id, "name": name, "created_at": stamp, "updated_at": stamp,
            "port": port, "preview_url": f"http://127.0.0.1:{port}/",
            "connection": connection, "model": None, "session_id": None,
            "running_turn": None, "head": None, "approvals": "ask", "template": template,
            "data_sources": [], "library": {}, "token": secrets.token_urlsafe(24),
            "attach": None, "app_config": None, **extra,
        })

    def delete(self, project_id: str) -> None:
        source = self.dir(project_id)
        if not source.is_dir():
            raise ProjectNotFound(project_id)
        trash = self.root / ".trash"
        trash.mkdir(exist_ok=True)
        # In place, `source` holds the record and history.git only: the person's folder is elsewhere
        # and is never moved or deleted.
        folder = self._in_place.pop(project_id, None)
        if folder is not None:
            ws.register_history(folder, None)
        shutil.move(str(source), str(trash / f"{project_id}-{int(time.time())}"))
