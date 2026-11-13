"""Harness registry: connection kind → harness, and the public connection list."""

from __future__ import annotations

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
                                                      self.connections)
        return self._harnesses[kind]

    def connections(self) -> list[dict]:
        """Resolved configs (server-internal; may name key variables)."""
        return load_connections(self.settings)

    def resolve(self, connection_id: str | None) -> dict:
        """The named connection, or the first one when the name is unknown."""
        connections = self.connections()
        return next((c for c in connections if c["id"] == connection_id), connections[0])

    def describe_all(self) -> list[dict]:
        return [self.harness(c["kind"]).describe(c) for c in self.connections()]
