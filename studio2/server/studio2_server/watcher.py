"""Workspace file watcher: debounced `files_changed` events per open project."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Callable

from watchfiles import awatch

from .workspace import IGNORED_DIRS, git

log = logging.getLogger("studio2")
# Build output and caches a development server writes into the project while it runs. A page
# compile in Next writes dozens of files here; none of it is something the person changed.
BUILD_DIRS = {".next", ".nuxt", ".svelte-kit", ".astro", ".turbo", ".vite", ".parcel-cache", ".angular", ".cache"}
DEBOUNCE_MS = 300
MAX_PATHS = 200
ATOMIC_TMP = re.compile(r"\.tmp\.\d+\.[0-9a-f]+$")


def not_ignored(root: Path, paths: list[str]) -> list[str]:
    """Drop what the project's own .gitignore ignores (an app's database, logs, build output):
    those files are not part of the project's versions, and a change to them is not news."""
    if not paths:
        return paths
    try:
        ignored = set(git(root, "check-ignore", "--", *paths[:MAX_PATHS * 5], check=False).splitlines())
    except Exception:  # noqa: BLE001 - no git, or not a repository yet: report everything
        return paths
    # studio.json is the studio's own file: an attached copy keeps it out of versions, but a change
    # to it still matters (the app is restarted with the new command).
    return [path for path in paths if path not in ignored or path == "studio.json"]


class Watchers:
    def __init__(self, on_change: Callable[[str, list[str]], None]):
        self.on_change = on_change
        self.tasks: dict[str, asyncio.Task] = {}

    def ensure(self, project_id: str, workspace: Path) -> None:
        task = self.tasks.get(project_id)
        if task is None or task.done():
            self.tasks[project_id] = asyncio.create_task(self._watch(project_id, workspace))

    async def _watch(self, project_id: str, workspace: Path) -> None:
        root = workspace.resolve()

        def wanted(_change, path: str) -> bool:
            try:
                parts = Path(path).relative_to(root).parts
            except ValueError:
                return False
            if not parts or IGNORED_DIRS.intersection(parts) or BUILD_DIRS.intersection(parts):
                return False
            # The agent's Write/Edit tools write "<name>.tmp.<pid>.<hex>" and rename it over
            # the target; those files and bare directory events are noise for the UI.
            return not ATOMIC_TMP.search(parts[-1]) and not Path(path).is_dir()

        try:
            async for changes in awatch(root, watch_filter=wanted, debounce=DEBOUNCE_MS, step=50,
                                        recursive=True):
                paths = sorted({Path(path).relative_to(root).as_posix() for _change, path in changes})
                paths = await asyncio.to_thread(not_ignored, root, paths)
                if paths:
                    self.on_change(project_id, paths[:MAX_PATHS])
        except asyncio.CancelledError:
            raise
        except Exception:  # a deleted workspace or an OS watch limit must not take the server down
            log.exception("File watcher for %s stopped", project_id)

    async def stop(self, project_id: str) -> None:
        task = self.tasks.pop(project_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop_all(self) -> None:
        for project_id in list(self.tasks):
            await self.stop(project_id)
