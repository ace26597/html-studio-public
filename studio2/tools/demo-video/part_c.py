"""Record changing a finished page by hand: an inline text edit, select-and-ask, and a drawing.

    python part_c.py <project id> frames/c \\
        --edit "Old heading text" --edit-to "New heading text" \\
        --select "Words in the element to select" --select-ask "What to do with it" \\
        --box-at "Words in the element to draw over" --box-label "What goes there" \\
        --circle "Words in the element to circle" --note "A note beside it" \\
        --draw-ask "What to say with the drawing" [--dry]

Every part is optional. `--dry` rehearses: nothing is sent to the agent and the text edit is abandoned.
"""
import asyncio, math, sys
from pathlib import Path
from playwright.async_api import async_playwright
import steps
from rec import Rec, open_browser, W, H
from steps import BASE, BLOCKY, TEXTY, api, ask, collapse_chat, events, find, mode

PID, OUT, DRY = sys.argv[1], Path(sys.argv[2]), "--dry" in sys.argv
steps.PID, steps.DRY = PID, DRY


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


async def main():
    project = api(f"/api/projects/{PID}")
    preview = project["preview_url"].rstrip("/")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(5000)

        def frame():      # the page reloads after an edit or a turn: always the frame that is there now
            return next(f for f in pg.frames if f.url.startswith(preview))

        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("c_open", 0.12); rec.start()
        await asyncio.sleep(1.5)
        await collapse_chat(rec, pg)

        # ---- 1. edit a line of text in place -------------------------------------------------
        if opt("--edit"):
            rec.set("c_edit", 0.1)
            await mode(rec, pg, "Edit")
            item, box = await find(frame(), opt("--edit"))
            print("editing:", (await item.inner_text())[:70], flush=True)
            await rec.click(box["x"] + min(box["width"] * 0.5, 220), box["y"] + box["height"] / 2, 0.8)
            await asyncio.sleep(0.8)
            await pg.keyboard.press("Meta+A")
            await asyncio.sleep(0.5)
            await pg.keyboard.type(opt("--edit-to"), delay=45)
            await asyncio.sleep(0.9)
            await pg.keyboard.press("Escape" if DRY else "Enter")
            rec.set("c_edit_saved", 0.12)
            await rec.move(box["x"] + box["width"] / 2 + 200, box["y"] + 160, 0.6)
            await asyncio.sleep(2.2)
            if not DRY:
                await rec.click_on(pg.locator('[aria-label="Versions"]').first, 0.9)
                await asyncio.sleep(3.0)
                await rec.click_on(pg.locator('[aria-label="Versions"], [aria-label="Hide versions"]').first, 0.5)
                await asyncio.sleep(0.6)

        # ---- 2. point at something and ask ----------------------------------------------------
        if opt("--select"):
            rec.set("c_select", 0.1)
            await mode(rec, pg, "Select")
            item, box = await find(frame(), opt("--select"))
            print("selecting:", (await item.inner_text())[:70], flush=True)
            await rec.move(box["x"] + box["width"] / 2 + 180, box["y"] + box["height"] + 90, 0.7)
            await asyncio.sleep(0.4)
            await rec.click(box["x"] + min(box["width"] / 2, 260), box["y"] + min(box["height"] / 2, 60), 0.7)
            await asyncio.sleep(1.0)
            rec.set("c_select_typing", 0.1)
            sent = await ask(rec, pg, opt("--select-ask"), "c_select")
            await collapse_chat(rec, pg)
            await mode(rec, pg, "Browse")
            if sent:
                await asyncio.sleep(1.5)
                try:
                    await find(frame(), opt("--select-after") or opt("--select"), BLOCKY + ", " + TEXTY)
                except (SystemExit, Exception) as exc:
                    print("after select:", str(exc)[:80], flush=True)
                await rec.move(700, 430, 0.6)
                await asyncio.sleep(3.5)

        # ---- 3. draw on the page ---------------------------------------------------------------
        if opt("--box-at") or opt("--circle"):
            rec.set("c_draw", 0.1)
            await mode(rec, pg, "Draw")
            if opt("--box-at"):
                item, box = await find(frame(), opt("--box-at"), BLOCKY)
                await rec.click_on(pg.get_by_role("radio", name="Box"), 0.7)
                left = max(box["x"] + 10, 40)
                right = min(box["x"] + box["width"] - 10, W - 60)
                top = max(box["y"] + 6, 150)
                bottom = min(top + max(120, min(box["height"] - 12, 190)), H - 60)
                await rec.drag([(left, top), ((left + right) / 2, (top + bottom) / 2), (right, bottom)], 1.1)
                await asyncio.sleep(0.6)
                await pg.keyboard.type(opt("--box-label"), delay=28)
                await asyncio.sleep(0.7)
                await pg.keyboard.press("Enter")
                await asyncio.sleep(0.9)
            if opt("--circle"):
                item, box = await find(frame(), opt("--circle"), TEXTY)
                await rec.click_on(pg.get_by_role("radio", name="Pen"), 0.7)
                cx, cy = box["x"] + min(box["width"], 420) / 2, box["y"] + box["height"] / 2
                rx, ry = min(box["width"], 420) / 2 + 22, box["height"] / 2 + 16
                loop = [(cx + rx * math.cos(a / 20 * 2 * math.pi - 2.4), cy + ry * math.sin(a / 20 * 2 * math.pi - 2.4)) for a in range(23)]
                await rec.drag(loop, 1.3)
                if opt("--note"):
                    await rec.click_on(pg.get_by_role("radio", name="Note"), 0.6)
                    await rec.click(min(cx + rx + 30, W - 260), min(cy + ry + 26, H - 90), 0.7)
                    await asyncio.sleep(0.5)
                    await pg.keyboard.type(opt("--note"), delay=28)
                    await asyncio.sleep(0.6)
                    await pg.keyboard.press("Enter")
                    await asyncio.sleep(0.8)
            await asyncio.sleep(1.2)
            await rec.click_on(pg.get_by_role("button", name="Done", exact=True), 0.8)
            await asyncio.sleep(1.2)
            rec.set("c_draw_typing", 0.1)
            sent = await ask(rec, pg, opt("--draw-ask"), "c_draw")
            await collapse_chat(rec, pg)
            await mode(rec, pg, "Browse")
            if sent:
                await rec.move(700, 430, 0.6)
                await asyncio.sleep(4.0)
                rec.set("c_versions", 0.12)
                await rec.click_on(pg.locator('[aria-label="Versions"]').first, 0.9)
                await asyncio.sleep(3.5)
                await rec.click_on(pg.locator('[aria-label="Versions"], [aria-label="Hide versions"]').first, 0.5)

        rec.set("c_end", 0.12)
        await rec.move(640, 420, 0.6)
        await asyncio.sleep(2.0)
        await rec.finish()
        print("frames:", rec.n, flush=True)
        print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms")) for e in events() if e["type"] in ("turn_started", "turn_finished")], flush=True)
        await browser.close()

asyncio.run(main())
