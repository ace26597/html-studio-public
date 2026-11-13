"""Keyless web search for every connection (a gateway has no search tool of its own).

Two public results pages are read, in order: DuckDuckGo's plain HTML page, then Yahoo's when the
first refuses. Neither has a contract. They refuse bursts, so the studio sends one search at a
time, spaced out, retries a refusal once after a short wait, leaves a refusing service alone for
a while, and answers a repeated query of the same turn from memory. A server error (Yahoo answers
HTTP 500 to about one request in three, measured 12 November 2026) is asked again straight away,
up to three times. When both fail, the error says search is unavailable and what to do instead.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .sources import USER_AGENT

DDG_RESULT = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DDG_SNIPPET = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|td)>', re.S)
YAHOO_RESULT = re.compile(r'<div class="compTitle[^"]*">\s*<a[^>]+href="([^"]+)"[^>]*>.*?<h3[^>]*>(.*?)</h3>'
                          r'(.*?)(?=<div class="compTitle|\Z)', re.S)
YAHOO_SNIPPET = re.compile(r'<div class="compText[^"]*">(.*?)</div>', re.S)

# Seconds: between two requests to one service; before the one retry of a refusal; how long a
# service that refused twice is left alone; how long an answer is kept (a turn's length).
SPACING, RETRY_WAIT, REST, CACHE_SECONDS = 1.5, 2.0, 90.0, 1800.0
BUSY_WAIT, BUSY_RETRIES = 0.7, 3     # a server error: asked again this soon, this many times
_lock: asyncio.Lock | None = None
_last: dict[str, float] = {}
_resting: dict[str, float] = {}
_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}


class SearchError(Exception):
    pass


class Refused(Exception):
    """A service that did not answer the search: a refusal or a challenge page, or, with `busy`,
    a server error or no connection (worth asking again at once; no reason to leave it alone)."""

    def __init__(self, reason: str, busy: bool = False):
        super().__init__(reason)
        self.busy = busy


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _ddg_target(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        return (parse_qs(parsed.query).get("uddg") or [href])[0]
    return href


def parse_results(page: str, limit: int) -> list[dict]:
    """DuckDuckGo's HTML results page."""
    snippets = [_clean(s) for s in DDG_SNIPPET.findall(page)]
    results = []
    for index, (href, title) in enumerate(DDG_RESULT.findall(page)):
        url = _ddg_target(href)
        if "duckduckgo.com/y.js" in url or not url.startswith("http"):
            continue  # an advertisement
        results.append({"title": _clean(title), "url": url,
                        "snippet": snippets[index] if index < len(snippets) else ""})
        if len(results) >= limit:
            break
    return results


def parse_yahoo(page: str, limit: int) -> list[dict]:
    """Yahoo's results page: each organic result links through r.search.yahoo.com/…/RU=<address>/."""
    results, seen = [], set()
    for href, title, rest in YAHOO_RESULT.findall(page):
        found = re.search(r"/RU=([^/]+)/", html.unescape(href))
        url = unquote(found.group(1)) if found else ""
        if not url.startswith("http") or "yahoo.com" in urlparse(url).netloc or url in seen:
            continue  # its own pages, advertisements, "related searches"
        seen.add(url)
        snippet = YAHOO_SNIPPET.search(rest)
        results.append({"title": _clean(title), "url": url, "snippet": _clean(snippet.group(1)) if snippet else ""})
        if len(results) >= limit:
            break
    return results


async def _ask(service: str, query: str, limit: int) -> list[dict]:
    """One request to one service. Raises Refused with the reason when it does not answer."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            if service == "duckduckgo":
                response = await client.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "us-en"})
            else:
                response = await client.get("https://search.yahoo.com/search", params={"p": query})
    except httpx.HTTPError as exc:
        raise Refused(f"no answer ({exc.__class__.__name__})", busy=True) from None
    page = response.text
    if response.status_code != 200:
        raise Refused(f"HTTP {response.status_code}", busy=response.status_code >= 500)
    if service == "duckduckgo":
        if "anomaly" in page[:4000].lower():
            raise Refused("a challenge page")
        return parse_results(page, limit)
    return parse_yahoo(page, limit)


SERVICES = ("duckduckgo", "yahoo")
LABELS = {"duckduckgo": "DuckDuckGo", "yahoo": "Yahoo"}


async def _try(service: str, query: str, limit: int, retry: bool) -> list[dict]:
    """A spaced request; a refusal is retried once after a short wait, a server error up to
    BUSY_RETRIES times straight away."""
    refused = busy = 0
    pause = SPACING - (time.monotonic() - _last.get(service, -SPACING))
    while True:
        await asyncio.sleep(max(pause, 0.0))
        try:
            return await _ask(service, query, limit)
        except Refused as exc:
            if exc.busy and busy < BUSY_RETRIES:
                busy, pause = busy + 1, BUSY_WAIT
            elif not exc.busy and retry and not refused:
                refused, pause = 1, max(RETRY_WAIT, SPACING)
            else:
                raise
        finally:
            _last[service] = time.monotonic()


TAVILY_URL = "https://api.tavily.com/search"
TAVILY_REST = 600.0          # a refused key or a used-up limit: left alone for ten minutes


async def _tavily(query: str, limit: int, key: str) -> list[dict]:
    """Tavily's search API (CONTRACT.md, "Web search"). Raises Refused with the reason for the person."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), trust_env=False) as client:
            response = await client.post(TAVILY_URL, headers={"Authorization": f"Bearer {key}"},
                                         json={"query": query, "max_results": limit})
    except httpx.HTTPError as exc:
        raise Refused(f"it did not answer ({exc.__class__.__name__})", busy=True) from None
    if response.status_code == 401:
        raise Refused("it refused the key")
    if response.status_code in (432, 433):
        raise Refused("the plan's limit is used up")
    if response.status_code != 200:
        raise Refused(f"it answered HTTP {response.status_code}", busy=True)
    try:
        items = response.json().get("results") or []
    except (ValueError, AttributeError):
        raise Refused("it answered something that is not a result list", busy=True) from None
    return [{"title": _clean(str(item.get("title") or "")), "url": str(item.get("url")),
             "snippet": " ".join(str(item.get("content") or "").split())[:500]}
            for item in items if isinstance(item, dict) and str(item.get("url") or "").startswith("http")][:limit]


async def search(query: str, max_results: int = 8, scope: str = "", tavily_key: str | None = None) -> dict:
    """→ {results, via, cached, note?}. `scope` names the turn: the same query in it is answered once.
    With `tavily_key`, Tavily is asked first; when it cannot answer, the public pages do and `note` says why."""
    query = " ".join(str(query or "").split())
    if not query:
        raise SearchError("The search query is empty.")
    limit = max(1, min(int(max_results or 8), 20))
    global _lock
    _lock = _lock or asyncio.Lock()
    key = (scope, query.lower(), limit)
    # One search at a time for the whole studio: helpers that search together wait in line here,
    # and the second of two identical queries finds the first one's answer.
    async with _lock:
        now = time.monotonic()
        for old in [k for k, (at, _) in _cache.items() if now - at > CACHE_SECONDS]:
            del _cache[old]
        if key in _cache:
            return {**_cache[key][1], "cached": True}
        note = None
        if tavily_key and _resting.get("tavily", 0.0) <= now:
            try:
                results = await _tavily(query, limit, tavily_key)
                answer = {"results": results, "via": "tavily", "cached": False}
                _cache[key] = (time.monotonic(), answer)
                return answer
            except Refused as exc:
                if not exc.busy:        # the key or the plan: asking again at once would only fail again
                    _resting["tavily"] = time.monotonic() + TAVILY_REST
                note = f"Tavily was asked first and could not answer ({exc}); the public search pages answered instead."
        elif tavily_key:
            note = "Tavily is being left alone for a few minutes after refusing; the public search pages answered instead."
        awake = [s for s in SERVICES if _resting.get(s, 0.0) <= now]
        reasons, answer = {}, None
        # A resting service is asked last and once, rather than giving up with it unasked.
        for service in awake + [s for s in SERVICES if s not in awake]:
            try:
                results = await _try(service, query, limit, retry=service in awake)
            except Refused as exc:
                reasons[service] = str(exc)
                if not exc.busy:
                    _resting[service] = time.monotonic() + REST
                continue
            _resting.pop(service, None)
            answer = {"results": results, "via": service, "cached": False, **({"note": note} if note else {})}
            if results:
                break
        if answer is not None:
            _cache[key] = (time.monotonic(), answer)
            return answer
    why = "; ".join(f"{LABELS[s]}: {r}" for s, r in reasons.items())
    raise SearchError(
        f"Web search is unavailable right now ({why}); the public search pages limit automated use. "
        "Do not call search_web again for a couple of minutes. Instead: use the built-in WebSearch tool "
        "if this connection has it (look it up with ToolSearch if it is not listed); or read an address "
        "you already know exists with fetch_source (an official site, a Wikipedia article, a page a saved "
        "source links to). Do not make up addresses.")


async def search_web(query: str, max_results: int = 8) -> list[dict]:
    return (await search(query, max_results))["results"]
