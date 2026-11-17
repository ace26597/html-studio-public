"""The agent's eyes: load a project page in a headless browser, return a screenshot and what
the browser complained about (CONTRACT.md, "Look").

Uses a Chromium that is already in the Playwright cache on this machine. It never downloads
one: without a cached browser (or without the optional `playwright` package) the tool reports
itself unavailable and the studio simply does not offer it to the agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import re
import sys
import time
from pathlib import Path

MAX_HEIGHT, KEEP = 6_000, 12
TEXT_CAP, LIST_CAP = 300, 30
RECHECK = 5.0       # seconds before a "cannot look" is checked again; a busy turn asks often
PLAYWRIGHT_PIN = "playwright==1.62.0"   # the `look` extra in pyproject.toml


def browser_folders() -> list[Path]:
    """Where a cached Chromium is looked for, in order; the first that exists is searched."""
    return [Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / "Library/Caches/ms-playwright"),
            Path.home() / ".cache/ms-playwright"]


def find_browser() -> Path | None:
    first, second = browser_folders()
    root = first if first.is_dir() else second
    found: list[tuple[int, int, Path]] = []
    for entry in root.glob("chromium*-*") if root.is_dir() else []:
        match = re.fullmatch(r"(chromium_headless_shell|chromium)-(\d+)", entry.name)
        if not match:
            continue
        names = ("chrome-headless-shell", "headless_shell") if match.group(1) != "chromium" else (
            "Chromium", "chrome")
        for exe in entry.rglob("*"):
            if exe.name in names and exe.is_file() and os.access(exe, os.X_OK):
                found.append((int(match.group(2)), 1 if match.group(1) != "chromium" else 0, exe))
                break
    return max(found)[2] if found else None


def why_not() -> str | None:
    """None when the package imports and a cached Chromium is there; otherwise what is missing and how to get it."""
    from .config import STUDIO2_ROOT

    # A package installed after the studio started is in a folder whose listing Python may have
    # cached; forget those listings so the import below sees it.
    importlib.invalidate_caches()
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return (f"The optional `look` extra ({PLAYWRIGHT_PIN}) is not installed in the studio's environment. "
                f"Install it with `cd {STUDIO2_ROOT} && uv pip install -e '.[look]'`; the studio checks again "
                "within a few seconds, and if it still says this, restart the studio (`studio stop`, then "
                "`studio start`). It also needs a cached Chromium; it never downloads one.")
    if find_browser() is None:
        first, second = browser_folders()
        where = str(first) if first.is_dir() else f"{first}, which does not exist, then {second}"
        return (f"No Chromium is cached on this machine (looked in {where}), and the studio never downloads "
                f"one. To get one, run `{sys.executable} -m playwright install chromium` yourself (about 170 MB), "
                "or set PLAYWRIGHT_BROWSERS_PATH to a folder that already holds a Playwright Chromium and "
                "restart the studio.")
    return None


class LookUnavailable(Exception):
    pass


class Looker:
    def __init__(self):
        self._playwright = self._browser = None
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(2)
        self._reason: str | None = None
        self._checked: float | None = None     # when it was last checked (monotonic seconds)

    def reason(self) -> str | None:
        """None when the tool can work; otherwise why not, with what to do about it.

        A "can work" is kept. A "cannot" is checked again after RECHECK seconds, so a package or
        browser installed while the studio runs is found without a restart."""
        now = time.monotonic()
        if self._checked is not None and (self._reason is None or now - self._checked < RECHECK):
            return self._reason
        self._checked, self._reason = now, why_not()
        return self._reason

    async def _ensure(self):
        if self.reason():
            raise LookUnavailable(self._reason)
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright
                self._playwright = self._playwright or await async_playwright().start()
                # On a Mac, draw WebGL with the real graphics chip: the software renderer shows a 3D
                # page correctly but at a few frames a second, which matters when rendering video.
                args = ["--use-angle=metal"] if sys.platform == "darwin" else []
                try:
                    self._browser = await self._playwright.chromium.launch(executable_path=str(find_browser()), args=args)
                except Exception:
                    if not args:
                        raise
                    self._browser = await self._playwright.chromium.launch(executable_path=str(find_browser()))
        return self._browser

    async def browser(self):
        """The shared headless browser, started on first use (media.py renders with it too)."""
        return await self._ensure()

    def slot(self):
        """`async with looker.slot():` — at most two pages are open in the browser at once."""
        return self._slots

    async def look(self, url: str, directory: Path, *, width: int = 1440, height: int = 900,
                   dark: bool = False, full_page: bool = True, wait_ms: int = 1200,
                   actions: list[dict] | None = None) -> dict:
        browser = await self._ensure()
        directory.mkdir(parents=True, exist_ok=True)
        console: list[dict] = []
        failed: list[dict] = []
        async with self._slots:
            context = await browser.new_context(viewport={"width": width, "height": height},
                                                color_scheme="dark" if dark else "light")
            try:
                page = await context.new_page()
                page.on("console", lambda m: console.append({"level": m.type, "text": m.text[:TEXT_CAP]})
                        if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: console.append({"level": "exception", "text": str(e)[:TEXT_CAP]}))
                page.on("requestfailed", lambda r: failed.append(
                    {"url": r.url[:TEXT_CAP], "status": None, "error": (r.failure or "")[:120]}))
                page.on("response", lambda r: failed.append(
                    {"url": r.url[:TEXT_CAP], "status": r.status, "error": None})
                        if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
                status = None
                try:
                    response = await page.goto(url, wait_until="load", timeout=20_000)
                    status = response.status if response else None
                    with contextlib.suppress(Exception):
                        await page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception as exc:
                    console.append({"level": "exception", "text": f"The page did not finish loading: {exc}"[:TEXT_CAP]})
                done = []
                for index, action in enumerate((actions or [])[:30]):
                    try:
                        await self._act(page, action)
                        done.append({"index": index, "ok": True, "error": None})
                    except Exception as exc:
                        done.append({"index": index, "ok": False, "error": " ".join(str(exc).split())[:TEXT_CAP]})
                await page.wait_for_timeout(max(0, min(wait_ms, 10_000)))
                size = await page.evaluate(
                    "() => ({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})")
                shot_height = min(max(size["h"], height), MAX_HEIGHT) if full_page else height
                # JPEG: a tall page as PNG runs to several megabytes, which the agent then has to carry.
                target = directory / f"look-{int(time.time() * 1000)}.jpg"
                await page.screenshot(path=str(target), type="jpeg", quality=82, clip={"x": 0, "y": 0, "width": width, "height": shot_height},
                                      full_page=full_page)
                title = await page.title()
                text = (await page.evaluate("() => document.body ? document.body.innerText : ''"))[:1500]
            finally:
                await context.close()
        for old in sorted(directory.glob("look-*.*"))[:-KEEP]:
            old.unlink(missing_ok=True)
        return {"image": str(target), "url": url, "status": status, "title": title,
                "viewport": {"width": width, "height": height}, "dark": dark,
                "page_height": size["h"], "captured_height": shot_height,
                "cut_off": bool(full_page and size["h"] > MAX_HEIGHT),
                "wider_than_viewport": size["w"] > width + 1,
                "actions": done, "console": console[:LIST_CAP], "failed_requests": failed[:LIST_CAP], "text_start": text}

    async def drawing_shots(self, url: str, drawing: dict, directory: Path, stem: str) -> list[dict]:
        """Redraw the person's marks on a fresh render → [{file, marks}] one image per marked region."""
        from .drawing import REPLAY_JS, regions
        browser = await self._ensure()
        directory.mkdir(parents=True, exist_ok=True)
        width, height = drawing["viewport"]["width"], drawing["viewport"]["height"]
        async with self._slots:
            context = await browser.new_context(
                viewport={"width": width, "height": height}, device_scale_factor=2 if width < 700 else 1,
                color_scheme="dark" if drawing.get("dark") else "light")
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="load", timeout=20_000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=6_000)
                await page.wait_for_timeout(800)
                placed = await page.evaluate(REPLAY_JS, drawing)
                shots = []
                for index, region in enumerate(regions(placed["boxes"], width, placed["page"]["height"]), 1):
                    target = directory / f"{stem}-{index}.jpg"
                    await page.screenshot(path=str(target), type="jpeg", quality=80, full_page=True, clip=region["clip"])
                    shots.append({"file": target, "marks": region["marks"]})
                return shots
            finally:
                await context.close()

    async def rendered(self, url: str) -> tuple[str, str]:
        """→ (final URL, HTML after scripts ran) for a page that builds itself in the browser."""
        browser = await self._ensure()
        async with self._slots:
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                return page.url, await page.content()
            finally:
                await context.close()

    @staticmethod
    async def _act(page, action: dict) -> None:
        timeout = 5_000
        if "fill" in action:
            await page.fill(str(action["fill"]), str(action.get("value", "")), timeout=timeout)
        elif "click" in action:
            await page.click(str(action["click"]), timeout=timeout)
        elif "hover" in action:
            await page.hover(str(action["hover"]), timeout=timeout)
        elif "press" in action:
            await page.keyboard.press(str(action["press"]))
        elif "select" in action:
            await page.select_option(str(action["select"]), str(action.get("value", "")), timeout=timeout)
        elif "wait_for" in action:
            await page.wait_for_selector(str(action["wait_for"]), timeout=10_000)
        elif "wait_ms" in action:
            await page.wait_for_timeout(max(0, min(int(action["wait_ms"]), 10_000)))
        else:
            raise ValueError("Unknown action. Use fill, click, hover, press, select, wait_for or wait_ms.")

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser:
                await self._browser.close()
        with contextlib.suppress(Exception):
            if self._playwright:
                await self._playwright.stop()
        self._browser = self._playwright = None
