"""Templates the person saves (CONTRACT.md, "Templates the person saves").

A template is a copy of a project's files (or of one Office file with its build script), kept
under the data directory with a manifest, a guide and a preview image. An app template is
copied into a new project; every other kind is a pattern the agent follows.
"""

from __future__ import annotations

import io
import json
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path

from .config import Settings, now_iso

KINDS = {"page": "pattern", "app": "copy", "deck": "pattern", "workbook": "pattern", "document": "pattern"}
FILE_KINDS = {".pptx": "deck", ".xlsx": "workbook", ".xlsm": "workbook", ".docx": "document"}
KIND_WORDS = {"page": "page", "app": "app", "deck": "PowerPoint deck", "workbook": "Excel workbook",
              "document": "Word document"}
ALWAYS_OUT = {".git", "sources", "checks", "attachments", "research", "looks", "node_modules", ".venv", "venv",
              "__pycache__", ".pytest_cache", ".studio", "template", ".DS_Store"}
CODE = (".py", ".sh", ".js", ".mjs", ".ts")
MAX_FILES, MAX_BYTES = 2_000, 40 * 1024 * 1024
ID = re.compile(r"^tpl_[0-9a-f]{8}$")


class TemplateError(Exception):
    def __init__(self, message: str, code: str = "invalid_template", status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _wanted(kind: str, relative: Path, main: str | None) -> bool:
    parts = relative.parts
    if any(part in ALWAYS_OUT for part in parts) or relative.name.endswith((".pyc", ".stamping.xlsx")):
        return False
    top = parts[0]
    if kind == "app":
        return top != "data"
    if kind == "page":
        return top not in ("data", "files", "tools")
    text = relative.as_posix()                       # one Office file, how it looks, and how it was built
    return (text == main or text.startswith(main + ".preview/") or top == "tools"
            or text.startswith("files/img/"))


def _has_code(files: Path) -> bool:
    return (files / "studio.json").is_file() or any(
        p.suffix.lower() in CODE for p in files.rglob("*") if p.is_file() and "tools" in p.relative_to(files).parts)


def _guide(meta: dict, listing: list[str], notes: str) -> str:
    kind, word = meta["kind"], KIND_WORDS[meta["kind"]]
    use = {
        "page": ("Build the new page so it looks and reads like this one: the same theme variables, type, spacing, "
                 "section order, components (cards, tables, callouts, chart styling) and density. Start from "
                 "`template/index.html` and its styles; replace every piece of content."),
        "app": ("A project started from this template begins as these files and runs. Change it to do what the "
                "person asks; keep its structure, interface and conventions unless asked otherwise."),
        "deck": ("Make the new deck with the same build script: copy the script(s) from `template/tools/` into the "
                 "project's `tools/`, keep the grid, colours, type and slide functions, and replace the outline, "
                 "the text and the data. The rendered example in the `.preview` folder shows the result to match."),
        "workbook": ("Make the new workbook with the same build script: copy it from `template/tools/` into the "
                     "project's `tools/`, keep the sheets, their order, the styles, number formats, checks and "
                     "charts, and replace the inputs and data."),
        "document": ("Make the new document with the same build script: copy it from `template/tools/` into the "
                     "project's `tools/`, keep the styles, page set-up, title block, table and figure treatment, "
                     "and replace the text and the data."),
    }[kind]
    lines = [f"# Template: {meta['label']}", "",
             f"A {word} template saved on {meta['created_at'][:10]} from the project “{meta['from_project']}”.", ""]
    if meta.get("description"):
        lines += [meta["description"], ""]
    lines += ["## How to use it", "", use, "",
              "## Do not carry over", "",
              "The facts, figures, names, dates, quotes and sources in these files belong to the work this template "
              "was saved from. Nothing of that kind may appear in new work unless the person supplies it again. "
              "Take the look and the way it is built; bring your own content.", ""]
    if notes.strip():
        lines += ["## How it was made", "", notes.strip(), ""]
    lines += ["## Files", ""] + [f"- `{item}`" for item in listing[:60]]
    if len(listing) > 60:
        lines.append(f"- … and {len(listing) - 60} more")
    return "\n".join(lines) + "\n"


class Templates:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "templates"

    # -- reading ------------------------------------------------------------------------------
    def _meta(self, folder: Path) -> dict | None:
        try:
            meta = json.loads((folder / "template.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("kind") not in KINDS or not ID.match(folder.name):
            return None
        preview = next((p.name for p in (folder / "preview.jpg", folder / "preview.png") if p.is_file()), None)
        return {"id": folder.name, "label": str(meta.get("label") or "Untitled"),
                "description": str(meta.get("description") or ""), "app": meta["kind"] == "app",
                "source": "yours", "kind": meta["kind"], "mode": KINDS[meta["kind"]],
                "created_at": meta.get("created_at"), "from_project": meta.get("from_project"),
                "files": meta.get("files", 0), "bytes": meta.get("bytes", 0),
                "preview": f"/api/templates/{folder.name}/preview" if preview else None,
                "main": meta.get("main"), "imported": bool(meta.get("imported")),
                "has_code": bool(meta.get("has_code"))}

    def list(self) -> list[dict]:
        found = [meta for folder in (self.root.iterdir() if self.root.is_dir() else []) if (meta := self._meta(folder))]
        return sorted(found, key=lambda item: item["created_at"] or "", reverse=True)

    def get(self, template_id: str) -> dict | None:
        return self._meta(self.root / template_id) if ID.match(template_id or "") else None

    def files(self, template_id: str) -> Path:
        return self.root / template_id / "files"

    def preview(self, template_id: str) -> Path | None:
        folder = self.root / template_id
        return next((p for p in (folder / "preview.jpg", folder / "preview.png") if p.is_file()), None)

    # -- saving -------------------------------------------------------------------------------
    def save(self, project: dict, workspace: Path, name: str, description: str = "", kind: str | None = None,
             file: str | None = None, notes: str = "") -> dict:
        label = " ".join(str(name or "").split())[:80]
        if not label:
            raise TemplateError("Give the template a name.")
        main = None
        if file:
            target = (workspace / file).resolve()
            if workspace.resolve() not in target.parents or not target.is_file():
                raise TemplateError(f"There is no file at {file} in the project.")
            if target.suffix.lower() not in FILE_KINDS:
                raise TemplateError("Only a .pptx, .xlsx or .docx file can be saved as a file template.")
            main, kind = target.relative_to(workspace.resolve()).as_posix(), FILE_KINDS[target.suffix.lower()]
        kind = kind or ("app" if (workspace / "studio.json").is_file() else "page")
        if kind not in KINDS:
            raise TemplateError("A template is a page, an app, a deck, a workbook or a document.")
        if kind in ("deck", "workbook", "document") and not main:
            raise TemplateError("Say which file to save as the template.")
        if kind in ("page", "app"):
            main = "index.html" if (workspace / "index.html").is_file() else None
        chosen, total = [], 0
        for path in sorted(workspace.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(workspace)
            if not _wanted(kind, relative, main):
                continue
            total += path.stat().st_size
            chosen.append(relative)
            if len(chosen) > MAX_FILES or total > MAX_BYTES:
                raise TemplateError("This is too big to save as a template (more than 2,000 files or 40 MB). "
                                    "Remove large data or build output first.", "template_too_large", 413)
        if not chosen:
            raise TemplateError("There is nothing in this project to save as a template yet.")
        template_id = "tpl_" + secrets.token_hex(4)
        folder = self.root / template_id
        files = folder / "files"
        for relative in chosen:
            (files / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(workspace / relative, files / relative)
        meta = {"label": label, "description": " ".join(str(description or "").split())[:600], "kind": kind,
                "created_at": now_iso(), "from_project": project.get("name"), "main": main,
                "files": len(chosen) + 1, "bytes": total, "has_code": _has_code(files), "imported": False}
        (files / "TEMPLATE.md").write_text(_guide(meta, [r.as_posix() for r in chosen], str(notes or "")[:8000]), "utf-8")
        if main and kind in ("deck", "workbook", "document"):
            first = workspace / (main + ".preview") / "page-1.png"
            if first.is_file():
                shutil.copyfile(first, folder / "preview.png")
        (folder / "template.json").write_text(json.dumps(meta, indent=1), "utf-8")
        return self._meta(folder)

    def set_preview(self, template_id: str, image: Path) -> None:
        if self.get(template_id) and image.is_file():
            shutil.copyfile(image, self.root / template_id / ("preview" + (image.suffix.lower() or ".jpg")))

    def update(self, template_id: str, label: str | None, description: str | None) -> dict:
        folder = self.root / template_id
        meta = json.loads((folder / "template.json").read_text("utf-8"))
        if label is not None:
            cleaned = " ".join(label.split())[:80]
            if not cleaned:
                raise TemplateError("Give the template a name.")
            meta["label"] = cleaned
        if description is not None:
            meta["description"] = " ".join(description.split())[:600]
        (folder / "template.json").write_text(json.dumps(meta, indent=1), "utf-8")
        return self._meta(folder)

    def delete(self, template_id: str) -> None:
        shutil.rmtree(self.root / template_id, ignore_errors=True)

    # -- using --------------------------------------------------------------------------------
    def place(self, template_id: str, workspace: Path) -> None:
        """Put a template's files in the project's `template/` folder, replacing an earlier one."""
        target = workspace / "template"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.files(template_id), target)

    # -- export and import ----------------------------------------------------------------------
    def export(self, template_id: str) -> bytes:
        folder, out = self.root / template_id, io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    archive.write(path, path.relative_to(folder).as_posix())
        return out.getvalue()

    def import_zip(self, data: bytes, confirm: bool = False) -> dict:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise TemplateError("That file is not a studio template.") from None
        with archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            names = {m.filename for m in members}
            if "template.json" not in names:
                raise TemplateError("That file is not a studio template (it has no template.json).")
            try:
                meta = json.loads(archive.read("template.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise TemplateError("The template's description file cannot be read.") from None
            if not isinstance(meta, dict) or meta.get("kind") not in KINDS:
                raise TemplateError("The template does not say what kind it is.")
            if len(members) > MAX_FILES + 5 or sum(m.file_size for m in members) > MAX_BYTES * 2:
                raise TemplateError("The template is too big to import.", "template_too_large", 413)
            safe = []
            for member in members:
                name = member.filename
                parts = Path(name).parts
                linked = (member.external_attr >> 16) & 0o170000 == 0o120000
                allowed = name in ("template.json", "preview.jpg", "preview.png") or (parts and parts[0] == "files")
                if (linked or name.startswith(("/", "\\")) or ".." in parts or "\\" in name or not allowed
                        or any(part in (".git",) for part in parts)):
                    raise TemplateError(f"The template contains a path that is not allowed ({name[:80]}).")
                safe.append(member)
            code = "files/studio.json" in names or any(
                Path(n).suffix.lower() in CODE and "tools" in Path(n).parts for n in names)
            if code and not confirm:
                raise TemplateError(
                    "This template contains an app or scripts. They run on this machine when a project is started "
                    "from it, so import it only if you trust where it came from.", "needs_confirmation", 409)
            template_id = "tpl_" + secrets.token_hex(4)
            folder = self.root / template_id
            for member in safe:
                if member.filename == "template.json":
                    continue
                target = folder / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        files = folder / "files"
        count = sum(1 for p in files.rglob("*") if p.is_file()) if files.is_dir() else 0
        if not count:
            shutil.rmtree(folder, ignore_errors=True)
            raise TemplateError("The template has no files.")
        clean = {"label": " ".join(str(meta.get("label") or "Imported template").split())[:80],
                 "description": " ".join(str(meta.get("description") or "").split())[:600], "kind": meta["kind"],
                 "created_at": now_iso(), "from_project": str(meta.get("from_project") or "")[:120] or None,
                 "main": str(meta.get("main"))[:300] if meta.get("main") else None, "files": count,
                 "bytes": sum(p.stat().st_size for p in files.rglob("*") if p.is_file()),
                 "has_code": _has_code(files), "imported": True, "imported_at": time.strftime("%Y-%m-%d")}
        (folder / "template.json").write_text(json.dumps(clean, indent=1), "utf-8")
        return self._meta(folder)
