#!/usr/bin/env python3
"""Turn a finished project of this studio into an example that ships with it (CONTRACT.md,
"Examples that come with the studio"). Run with the studio's own Python while the studio runs:

    .venv/bin/python tools/examples/add.py <project-id> <example-id> --kind report --summary "One sentence." \\
        [--title "…"] [--order 10] [--data <data folder>]

It copies the project's files into `library/examples/<example-id>/files` without `.git`, working
notes, attachments, caches and the saved text of sources (other people's writing; the source list
stays), writes `example.json` with facts taken from the project's own record (never typed by hand;
a fact the record does not hold is null), and takes the picture from the project's running preview.
Only pages: a project with its own back end (`studio.json`) is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO2 = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(STUDIO2 / "server"))

from studio2_server.examples import ID, KINDS  # noqa: E402

LEFT_OUT = {".git", "research", "attachments", "template", "notes", "__pycache__", "node_modules", ".venv",
            "looks", ".DS_Store"}


def wanted(relative: Path) -> bool:
    if LEFT_OUT & set(relative.parts):
        return False
    return not (relative.parts[0] == "sources" and relative.as_posix() != "sources/index.json")


def facts(folder: Path) -> tuple[dict, dict | None]:
    """→ (made, check) from the project's event log: completed requests only."""
    started, finished, check = {}, {}, None
    for line in (folder / "events.jsonl").read_text("utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn_started":
            started[event["turn_id"]] = event
        elif event.get("type") == "turn_finished" and event.get("status") == "completed":
            finished[event["turn_id"]] = event
        elif event.get("type") == "verification":
            check = {key: event.get(key) for key in ("checked", "passed", "fixed", "failed", "unverifiable")}
    done = [(started[turn], end) for turn, end in finished.items() if turn in started]
    if not done:
        raise SystemExit("This project has no completed request; it is not an example of anything.")
    longest = max(done, key=lambda pair: (pair[1].get("usage") or {}).get("duration_ms") or 0)[0]
    first = done[0][0]
    durations = [(end.get("usage") or {}).get("duration_ms") for _start, end in done]
    made = {"model": longest.get("model"), "date": done[-1][1]["ts"][:10], "requests": len(done),
            "duration_s": round(sum(durations) / 1000) if all(isinstance(d, (int, float)) for d in durations) else None,
            "depth": (first.get("options") or {}).get("depth"), "request": first.get("text") or None}
    return made, check


async def picture(url: str, target: Path) -> None:
    from PIL import Image
    from studio2_server.look import Looker

    looker = Looker()
    if looker.reason():
        raise SystemExit(f"No picture can be taken: {looker.reason()}")
    with tempfile.TemporaryDirectory() as scratch:
        shot = await looker.look(url, Path(scratch), width=1280, height=800, full_page=False, wait_ms=2500)
        image = Image.open(shot["image"]).convert("RGB")
        image.thumbnail((960, 600))
        image.save(target, "JPEG", quality=82, optimize=True)
    await looker.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("project")
    parser.add_argument("example")
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--title")
    parser.add_argument("--order", type=int, default=50)
    parser.add_argument("--data", type=Path, default=STUDIO2.parent / "data" / "studio2")
    args = parser.parse_args()
    if not ID.fullmatch(args.example):
        raise SystemExit("The example id is lower-case letters, digits and hyphens.")
    folder = args.data / "projects" / args.project
    record, workspace = json.loads((folder / "project.json").read_text("utf-8")), folder / "workspace"
    if (workspace / "studio.json").is_file():
        raise SystemExit("This project has its own back end; examples are pages only.")
    if not (workspace / "index.html").is_file():
        raise SystemExit("This project has no index.html.")
    made, check = facts(folder)
    target = STUDIO2 / "library" / "examples" / args.example
    if target.exists():
        shutil.rmtree(target)
    files = target / "files"
    count = 0
    for source in sorted(workspace.rglob("*")):
        relative = source.relative_to(workspace)
        if source.is_file() and wanted(relative):
            (files / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, files / relative)
            count += 1
    try:
        sources = len(json.loads((files / "sources" / "index.json").read_text("utf-8")).get("sources") or [])
    except (OSError, ValueError):
        sources = 0
    meta = {"title": args.title or record["name"], "summary": args.summary, "kind": args.kind, "order": args.order,
            "made": made, "sources": sources, "check": check}
    (target / "example.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", "utf-8")
    asyncio.run(picture(f"http://127.0.0.1:{record['port']}/", target / "preview.jpg"))
    size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    print(f"{args.example}: {count} files, {size / 1e6:.2f} MB, {made['model']}, {made['requests']} request(s), "
          f"{made['duration_s']} s, {sources} sources, check {check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
