"""Keys for other services (CONTRACT.md): the key of a service the studio itself calls, kept by the
same rule as every other credential. The service's own environment variable wins; otherwise the
studio's secret store (the keychain, or an owner-only file). A key is listed, never shown."""

from __future__ import annotations

import os

STORE = "studio2-service-key"
SERVICES = {
    "tavily": {"label": "Tavily", "used_for": "Web search: asked first, before the public search pages.",
               "env": "TAVILY_API_KEY", "site": "https://tavily.com"},
}


class UnknownService(KeyError):
    pass


def _known(name: str) -> dict:
    if name not in SERVICES:
        raise UnknownService(name)
    return SERVICES[name]


def source(settings, name: str, environ=None) -> str | None:
    """Where the key is: `env`, `store`, or None when there is none."""
    environ = os.environ if environ is None else environ
    if (environ.get(_known(name)["env"]) or "").strip():
        return "env"
    try:
        return "store" if settings.secret_store().get(STORE, name) else None
    except Exception:   # noqa: BLE001 - a keychain that will not answer is "no key", never a crash
        return None


def key(settings, name: str, environ=None) -> str | None:
    environ = os.environ if environ is None else environ
    value = (environ.get(_known(name)["env"]) or "").strip()
    if value:
        return value
    try:
        return settings.secret_store().get(STORE, name) or None
    except Exception:   # noqa: BLE001
        return None


def listing(settings, environ=None) -> list[dict]:
    found = []
    for name, meta in SERVICES.items():
        where = source(settings, name, environ)
        found.append({"name": name, **meta, "configured": where is not None, "source": where})
    return found


def save(settings, name: str, value: str) -> None:
    _known(name)
    value = str(value or "").strip()
    if not value:
        raise ValueError("The key is empty.")
    settings.secret_store().set(STORE, name, value)


def remove(settings, name: str) -> None:
    _known(name)
    settings.secret_store().delete(STORE, name)


# Connectors that are search services: when one is attached to a request it is the first way to search.
SEARCH_CONNECTOR = ("tavily", "brave", "exa", "perplexity", "serper", "firecrawl", "kagi", "searx")


def search_connectors(names: list[str]) -> list[str]:
    return [name for name in names if any(word in str(name).lower() for word in SEARCH_CONNECTOR)]
