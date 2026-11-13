"""Record using a finished app, looking at its log, then changing it by pointing at part of it.

    python part_app.py <project id> frames/app-use --city "Tokyo" --select "Temperature comparison" \\
        --ask "What to change" [--toggles "°F|Lows|Highs|°C"] [--after "words in what was added"] [--dry]

Written for an app with a text field and an Add button; `--toggles` are buttons to press in turn.
"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
import steps
from rec import Rec, open_browser
from steps import BASE, BLOCKY, TEXTY, api, ask, collapse_chat, events, find, mode

PID, OUT, DRY = sys.argv[1], Path(sys.argv[2]), "--dry" in sys.argv
steps.PID, steps.DRY = PID, DRY


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


async def wheel(pg, total, step=220, pause=0.22):
    for _ in range(abs(total) // step):
        await pg.mouse.wheel(0, step if total > 0 else -step)
        await asyncio.sleep(pause)


async def main():
    project = api(f"/api/projects/{PID}")
    preview = project["preview_url"].rstrip("/")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(6000)

        def frame():      # the page reloads when the app saves its data: always the frame that is there now
            return next(f for f in pg.frames if f.url.startswith(preview))

        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("d_result", 0.12); rec.start()
        await asyncio.sleep(2.0)
        await collapse_chat(rec, pg)
        await asyncio.sleep(1.0)

        rec.set("d_use", 0.1)
        field = frame().locator("input[type=text], input:not([type])").first
        await rec.click_on(field, 0.9, dx=0.25)
        await pg.keyboard.type(opt("--city", "Tokyo"), delay=90)
        await asyncio.sleep(0.6)
        await rec.click_on(frame().get_by_role("button", name="Add", exact=True), 0.7)
        await asyncio.sleep(4.0)                      # the back end looks the city up; the page reloads and redraws
        for name in [s for s in opt("--toggles", "°F|Lows|Highs|°C").split("|") if s]:
            await rec.click_on(frame().get_by_role("button", name=name, exact=True), 0.7)
            await asyncio.sleep(1.3)
        await rec.move(640, 520, 0.6)
        await wheel(pg, 1100)
        await asyncio.sleep(1.6)
        await wheel(pg, -1100, pause=0.14)
        await asyncio.sleep(0.6)

        rec.set("d_logs", 0.12)
        await rec.click_on(pg.locator('[aria-label="App logs"]').first, 0.9)
        await asyncio.sleep(3.2)
        await rec.click_on(pg.locator('[aria-label="App logs"]').first, 0.5)
        await asyncio.sleep(0.6)

        if opt("--select"):
            rec.set("d_select", 0.1)
            await mode(rec, pg, "Select")
            item, box = await find(frame(), opt("--select"))
            print("selecting:", (await item.inner_text())[:70], flush=True)
            await rec.move(box["x"] + box["width"] / 2 + 220, box["y"] + 150, 0.7)
            await asyncio.sleep(0.4)
            await rec.click(box["x"] + min(box["width"] / 2, 200), box["y"] + box["height"] / 2, 0.7)
            await asyncio.sleep(1.0)
            rec.set("d_typing", 0.1)
            sent = await ask(rec, pg, opt("--ask"), "d")
            await collapse_chat(rec, pg)
            await mode(rec, pg, "Browse")
            if sent:
                await asyncio.sleep(2.5)              # the app restarts with the change
                if opt("--after"):
                    try:
                        await find(frame(), opt("--after"), BLOCKY + ", " + TEXTY)
                    except SystemExit as exc:
                        print("after:", exc, flush=True)
                        await wheel(pg, 660)
                else:
                    await wheel(pg, 660)
                await rec.move(700, 430, 0.6)
                await asyncio.sleep(4.5)
        rec.set("d_end", 0.12)
        await asyncio.sleep(1.5)
        await rec.finish()
        print("frames:", rec.n, flush=True)
        print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms")) for e in events() if e["type"] in ("turn_started", "turn_finished")], flush=True)
        await browser.close()

asyncio.run(main())
