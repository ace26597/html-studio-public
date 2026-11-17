"""Harness registry: connection kind → harness, and the public connection list."""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Settings, load_connections
from .base import Harness, TurnRequest, TurnResult, empty_usage, public_connection

__all__ = ["Harness", "Registry", "TurnRequest", "TurnResult", "empty_usage", "public_connection"]


class Registry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._harnesses: dict[str, Harness] = {}
        self._sidecar = None

    @property
    def sidecar(self):
        """The LiteLLM the studio runs for `litellm` connections (gateway.py). Made on first use;
        nothing starts until a request needs it."""
        if self._sidecar is None:
            from ..gateway import Sidecar
            self._sidecar = Sidecar(self.settings)
        return self._sidecar

    async def stop(self) -> None:
        if self._sidecar is not None:
            await self._sidecar.stop()

    def harness(self, kind: str) -> Harness:
        if kind not in self._harnesses:
            if kind == "fake":
                from .fake import FakeHarness
                self._harnesses[kind] = FakeHarness()
            else:  # imported lazily: the fake/test path never loads the SDK
                from .claude import ClaudeHarness
                self._harnesses[kind] = ClaudeHarness(kind, self.settings.secret_store(), self.sidecar,
                                                      self.connections, self.mcp_dir,
                                                      (self.settings.data_dir, self.settings.config_path.parent))
        return self._harnesses[kind]

    @property
    def mcp_dir(self) -> Path:
        """Where a turn's connector settings are written for the agent program (mode 700, outside
        every project; harness/claude.py write_mcp_config). Each file goes when its turn ends."""
        return self.settings.data_dir / "run" / "mcp"

    def sweep(self) -> None:
        """Remove connector settings left by a studio that was killed mid-turn. Called once at start,
        before any turn runs."""
        for path in self.mcp_dir.glob("mcp-*.json") if self.mcp_dir.is_dir() else []:
            path.unlink(missing_ok=True)
        # The start folders of library connectors (connector_launch.py) untouched for a day; a
        # younger one may still be in use by a connector that outlived the last studio.
        from ..connector_launch import remove_old_folders
        home = self.settings.data_dir / "run" / "connectors"
        if home.is_symlink():
            home.unlink()
        elif home.is_dir() and home.lstat().st_uid == os.geteuid():
            remove_old_folders(str(home))

    def connections(self) -> list[dict]:
        """Resolved configs (server-internal; may name key variables)."""
        return load_connections(self.settings)

    def resolve(self, connection_id: str | None) -> dict:
        """The named connection, or the first one when the name is unknown."""
        connections = self.connections()
        return next((c for c in connections if c["id"] == connection_id), connections[0])

    def describe_all(self) -> list[dict]:
        return [self.harness(c["kind"]).describe(c) for c in self.connections()]
