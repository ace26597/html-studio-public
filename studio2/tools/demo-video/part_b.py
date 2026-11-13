import asyncio, json, sys, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright
from rec import Rec, open_browser

BASE = "http://127.0.0.1:8200"
PID, OUT, DRY = sys.argv[1], Path(sys.argv[2]), "--dry" in sys.argv
def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
ASK = opt("--ask", "Under this heading, add two plain sentences on what this data shows, and one thing in it that surprised you.")
TARGET, NAV = opt("--target"), [s for s in opt("--nav").split("|") if s]

def api(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read())

def events():
    return [json.loads(line) for line in open(Path(__file__).resolve().parents[3] / f"data/studio2/projects/{PID}/events.jsonl")]

async def main():
    project = api(f"/api/projects/{PID}")
    preview = project["preview_url"].rstrip("/")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(5000)
        frame = next(f for f in pg.frames if f.url.startswith(preview))
        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("b_result", 0.12); rec.start()
        await asyncio.sleep(2.5)
        await rec.click_on(pg.get_by_role("button", name="Collapse chat").first, 0.9)
        await asyncio.sleep(1.2)

        rec.set("b_tour", 0.1)
        await rec.move(640, 430, 0.7)
        for _ in range(9):
            await pg.mouse.wheel(0, 230); await asyncio.sleep(0.28)
        await asyncio.sleep(1.0)
        for _ in range(9):
            await pg.mouse.wheel(0, -230); await asyncio.sleep(0.16)
        await asyncio.sleep(0.6)
        nav = frame.locator('nav a:visible, nav button:visible, [role="tab"]:visible, aside a:visible, aside button:visible')
        count = await nav.count()
        print("nav items:", count, [(await nav.nth(i).inner_text()).strip()[:24] for i in range(min(count, 30))], flush=True)
        picks = [nav.filter(has_text=name).first for name in NAV] if NAV else [nav.nth(i) for i in ([1, 2, 3, 0] if count >= 4 else list(range(1, count)) + [0])[:4]]
        for item in picks:
            try:
                box = await item.bounding_box()
                if not box or box["y"] < 50 or box["y"] > 760:
                    continue
                await rec.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, 0.7)
                await asyncio.sleep(1.6)
            except Exception as exc:
                print("nav click skipped:", str(exc)[:80], flush=True)

        rec.set("b_select", 0.1)
        await rec.click_on(pg.locator('[aria-label="Preview mode"]').get_by_text("Select", exact=True), 0.8)
        await asyncio.sleep(0.6)
        target = None
        if TARGET:
            wanted = frame.locator("h2, h3").filter(has_text=TARGET).first
            await wanted.scroll_into_view_if_needed(timeout=5000)
            await asyncio.sleep(0.8)
            target = (wanted, await wanted.bounding_box())
        for selector in ([] if target else ["h2:visible", "h3:visible", "h1:visible"]):
            heads = frame.locator(selector)
            for i in range(min(await heads.count(), 12)):
                box = await heads.nth(i).bounding_box()
                if box and 110 < box["y"] < 600 and box["x"] < 820 and box["width"] > 60:
                    target = (heads.nth(i), box); break
            if target:
                break
        if not target:
            raise SystemExit("no heading to select in view")
        head, box = target
        print("selecting:", (await head.inner_text())[:60], flush=True)
        await rec.move(box["x"] + box["width"] / 2 + 260, box["y"] + 170, 0.8); await asyncio.sleep(0.5)
        await rec.move(box["x"] + 140, box["y"] + 90, 0.6); await asyncio.sleep(0.5)
        await rec.click(box["x"] + min(box["width"] / 2, 120), box["y"] + box["height"] / 2, 0.7)
        await asyncio.sleep(0.9)

        rec.set("b_typing", 0.1)
        opener = pg.locator('[aria-label^="Open chat"]')
        if await opener.count() and await opener.first.is_visible():     # selecting usually opens the chat by itself
            await rec.click_on(opener.first, 0.8)
        await asyncio.sleep(0.8)
        area = pg.locator("textarea").first
        await rec.click_on(area, 0.6, dx=0.5, dy=0.3)
        await rec.move(1090, 500, 0.4)
        await pg.keyboard.type(ASK, delay=22)
        await asyncio.sleep(1.0)
        if DRY:
            await asyncio.sleep(1.0); await rec.finish(); print("dry run frames:", rec.n); await browser.close(); return
        before = sum(1 for e in events() if e["type"] == "turn_finished")
        await pg.keyboard.press("Enter")
        rec.set("b_sent", 0.12)
        await asyncio.sleep(5.0)
        rec.set("b_working", 1.0)
        started = time.monotonic()
        while time.monotonic() - started < 20 * 60:
            await asyncio.sleep(4)
            evs = events()
            if sum(1 for e in evs if e["type"] == "turn_finished") > before:
                break
            asked = {e["approval_id"] for e in evs if e["type"] == "approval_request"} - {e.get("approval_id") for e in evs if e["type"] == "approval_resolved"}
            if asked:
                print("WAITING FOR APPROVAL", asked, flush=True); break
        rec.set("b_done", 0.12)
        await asyncio.sleep(3.5)
        await rec.click_on(pg.get_by_role("button", name="Collapse chat").first, 0.8)
        await asyncio.sleep(0.8)
        await rec.click_on(pg.locator('[aria-label="Preview mode"]').get_by_text("Browse", exact=True), 0.7)
        try:
            await head.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        await rec.move(700, 520, 0.6)
        await asyncio.sleep(4.0)

        rec.set("b_versions", 0.12)
        await rec.click_on(pg.locator('[aria-label="Versions"]').first, 0.9)
        await asyncio.sleep(3.2)
        await rec.click_on(pg.locator('[aria-label="Versions"], [aria-label="Hide versions"]').first, 0.5)
        rec.set("b_files", 0.12)
        await rec.click_on(pg.locator('[aria-label="Files"]').first, 0.9)
        await asyncio.sleep(3.0)
        await rec.click_on(pg.locator('[aria-label="Files"], [aria-label="Hide files"]').first, 0.5)
        rec.set("b_end", 0.12)
        await rec.move(640, 400, 0.6)
        await asyncio.sleep(2.5)
        await rec.finish()
        print("frames:", rec.n, flush=True)
        print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms")) for e in events() if e["type"] in ("turn_started", "turn_finished")], flush=True)
        await browser.close()
asyncio.run(main())
