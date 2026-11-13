"""Record a walk through a finished page: scroll from stop to stop, then show the files and versions.

    python part_tour.py <project id> frames/tour --stops "Words in a heading|Words in a chart title|…" [--hover "words"]
"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
import steps
from rec import Rec, open_browser
from steps import BASE, api, collapse_chat

PID, OUT = sys.argv[1], Path(sys.argv[2])
steps.PID = PID


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


async def glide(pg, item, top=190):
    """Scroll with the wheel until the element sits near the top of the view."""
    for _ in range(140):
        box = await item.bounding_box()
        if box is None:
            return None
        gap = box["y"] - top
        if abs(gap) < 50:
            return box
        await pg.mouse.wheel(0, max(-260, min(260, gap)))
        await asyncio.sleep(0.11)
    return await item.bounding_box()


async def main():
    project = api(f"/api/projects/{PID}")
    preview = project["preview_url"].rstrip("/")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(6000)
        frame = next(f for f in pg.frames if f.url.startswith(preview))
        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("t_result", 0.12); rec.start()
        await asyncio.sleep(2.5)
        await collapse_chat(rec, pg)
        await rec.move(640, 470, 0.6)
        await asyncio.sleep(1.2)
        rec.set("t_tour", 0.1)
        for words in [s for s in opt("--stops").split("|") if s]:
            found = frame.locator("h1, h2, h3, h4, figcaption, summary, caption").filter(has_text=words)
            if not await found.count():
                found = frame.get_by_text(words, exact=False)
            if not await found.count():
                print("stop not found:", words, flush=True)
                continue
            box = await glide(pg, found.first)
            print("stop:", words, box and round(box["y"]), flush=True)
            await asyncio.sleep(2.0)
        if opt("--hover"):
            target = frame.get_by_text(opt("--hover"), exact=False).first
            box = await target.bounding_box()
            if box:
                await rec.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, 0.8)
                await asyncio.sleep(1.8)
        rec.set("t_files", 0.12)
        await rec.click_on(pg.locator('[aria-label="Files"]').first, 0.9)
        await asyncio.sleep(3.2)
        await rec.click_on(pg.locator('[aria-label="Files"], [aria-label="Hide files"]').first, 0.5)
        rec.set("t_versions", 0.12)
        await rec.click_on(pg.locator('[aria-label="Versions"]').first, 0.9)
        await asyncio.sleep(3.0)
        await rec.click_on(pg.locator('[aria-label="Versions"], [aria-label="Hide versions"]').first, 0.5)
        rec.set("t_end", 0.12)
        await rec.move(640, 420, 0.6)
        await asyncio.sleep(1.5)
        await rec.finish()
        print("frames:", rec.n, flush=True)
        await browser.close()

asyncio.run(main())
