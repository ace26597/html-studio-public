"""Record using what is on a page: scroll to things, type into fields, click.

    python part_use.py <project id> frames/use --do "type|#bill|1400" --do "click|input[value='2.5']" --do "see|#result" --do "pause||1.5"

Each --do is kind|CSS selector inside the page|value. `see` only scrolls there.
"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
import steps
from rec import Rec, open_browser
from steps import BASE, api, collapse_chat

PID, OUT = sys.argv[1], Path(sys.argv[2])
steps.PID = PID
DO = [sys.argv[i + 1].split("|") for i, a in enumerate(sys.argv) if a == "--do"]


async def glide(pg, item, top=300):
    for _ in range(160):
        box = await item.bounding_box()
        if box is None:
            return None
        gap = box["y"] - top
        if abs(gap) < 60:
            return box
        await pg.mouse.wheel(0, max(-240, min(240, gap)))
        await asyncio.sleep(0.1)
    return await item.bounding_box()


async def main():
    project = api(f"/api/projects/{PID}")
    preview = project["preview_url"].rstrip("/")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(6000)
        frame = lambda: next(f for f in pg.frames if f.url.startswith(preview))  # noqa: E731
        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("u_open", 0.12); rec.start()
        await asyncio.sleep(1.2)
        await collapse_chat(rec, pg)
        await rec.move(640, 470, 0.5)
        rec.set("u_use", 0.1)
        for kind, selector, *rest in DO:
            value = rest[0] if rest else ""
            if kind == "pause":
                await asyncio.sleep(float(value or 1))
                continue
            if kind == "phase":
                rec.set(value, 0.1)
                continue
            item = frame().locator(selector).first
            box = await glide(pg, item, float(rest[1]) if len(rest) > 1 else 300)
            if box is None:
                print("not found:", selector, flush=True)
                continue
            if kind == "see":
                await rec.move(box["x"] + min(box["width"] / 2, 300), box["y"] + min(box["height"] / 2, 120), 0.7)
                await asyncio.sleep(float(value or 1.5))
            elif kind == "click":
                await rec.click(box["x"] + min(box["width"] / 2, 14 if box["width"] < 40 else box["width"] / 2), box["y"] + box["height"] / 2, 0.7)
                await asyncio.sleep(0.9)
            elif kind == "type":
                await rec.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, 0.7)
                await pg.keyboard.press("Meta+A")
                await asyncio.sleep(0.3)
                await pg.keyboard.type(value, delay=140)
                await asyncio.sleep(1.4)
        rec.set("u_end", 0.12)
        await asyncio.sleep(1.8)
        await rec.finish()
        print("frames:", rec.n, flush=True)
        await browser.close()

asyncio.run(main())
