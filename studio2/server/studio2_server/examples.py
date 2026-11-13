"""Examples that come with the studio (CONTRACT.md, "Examples that come with the studio").

Finished pages made with the studio, kept in `library/examples/<id>/` as `example.json`, `files/`
and `preview.jpg`. They are listed on the home page and opened as a copy; nothing here changes them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
KINDS = ("report", "dashboard", "explainer", "interactive")


class Examples:
    def __init__(self, library_dir):
        self._library = library_dir          # a callable or a path: tests point the settings elsewhere

    @property
    def root(self) -> Path:
        return Path(self._library() if callable(self._library) else self._library) / "examples"

    def _read(self, folder: Path) -> dict | None:
        if not ID.fullmatch(folder.name):
            return None
        try:
            meta = json.loads((folder / "example.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None
        files = folder / "files"
        if not isinstance(meta, dict) or not str(meta.get("title") or "").strip() or not (files / "index.html").is_file():
            return None
        kept = [path for path in files.rglob("*") if path.is_file()]
        made = meta.get("made") if isinstance(meta.get("made"), dict) else {}
        check = meta.get("check") if isinstance(meta.get("check"), dict) else None
        return {"id": folder.name, "title": str(meta["title"]).strip(), "summary": str(meta.get("summary") or ""),
                "kind": meta.get("kind") if meta.get("kind") in KINDS else "report",
                "order": meta.get("order") if isinstance(meta.get("order"), (int, float)) else 50,
                "made": {key: made.get(key) for key in ("model", "date", "requests", "duration_s", "depth", "request")},
                "sources": meta.get("sources") if isinstance(meta.get("sources"), int) else None, "check": check,
                "files": len(kept), "bytes": sum(path.stat().st_size for path in kept),
                "preview": f"/api/examples/{folder.name}/preview" if (folder / "preview.jpg").is_file() else None}

    def list(self) -> list[dict]:
        found = [item for item in (self._read(folder) for folder in sorted(self.root.glob("*")) if folder.is_dir())
                 if item]
        found.sort(key=lambda item: (item["order"], item["title"]))
        return found

    def get(self, example_id: str) -> dict | None:
        if not isinstance(example_id, str) or not ID.fullmatch(example_id):
            return None
        folder = self.root / example_id
        return self._read(folder) if folder.is_dir() else None

    def files(self, example_id: str) -> Path:
        return self.root / example_id / "files"

    def preview(self, example_id: str) -> Path | None:
        path = self.root / example_id / "preview.jpg"
        return path if self.get(example_id) and path.is_file() else None
