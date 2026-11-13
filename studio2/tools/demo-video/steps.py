"""Steps the hands-on recordings share: find something on the page, switch mode, ask in the chat."""
import asyncio, json, time, urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8200"
DATA = Path(__file__).resolve().parents[3] / "data/studio2/projects"
PID, DRY = None, False          # set by the script that imports this


def api(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read())


def events():
    return [json.loads(line) for line in open(DATA / PID / "events.jsonl")]


TEXTY = "h1, h2, h3, h4, p, li, figcaption, td, th, summary, blockquote, a, button, label, span, strong"
BLOCKY = "figure, table, svg, canvas, nav, section, article, aside, header, form, div, p, ul, ol, h2, h3"


async def find(frame, words, selector=TEXTY):
    """The smallest visible element holding these words, scrolled to the middle of the view."""
    found = frame.locator(selector).filter(has_text=words)
    count = await found.count()
    best = None
    for i in range(min(count, 40)):
        item = found.nth(i)
        if not await item.is_visible():
            continue
        size = len(await item.inner_text())
        if best is None or size < best[0]:
            best = (size, item)
    if best is None:
        raise SystemExit(f"nothing on the page holds: {words}")
    item = best[1]
    await item.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
    await asyncio.sleep(0.9)
    return item, await item.bounding_box()


async def mode(rec, pg, name):
    await rec.click_on(pg.locator('[aria-label="Preview mode"]').get_by_text(name, exact=True), 0.8)
    await asyncio.sleep(0.7)


async def collapse_chat(rec, pg):
    button = pg.get_by_role("button", name="Collapse chat")
    if await button.count() and await button.first.is_visible():
        await rec.click_on(button.first, 0.7)
        await asyncio.sleep(0.8)


async def ask(rec, pg, text, phase):
    """Type into the chat and send; wait for the turn to end. → False in a dry run."""
    opener = pg.locator('[aria-label^="Open chat"]')
    if await opener.count() and await opener.first.is_visible():
        await rec.click_on(opener.first, 0.7)
        await asyncio.sleep(0.7)
    area = pg.locator("textarea").first
    await rec.click_on(area, 0.6, dx=0.5, dy=0.3)
    await pg.keyboard.type(text, delay=20)
    await asyncio.sleep(1.0)
    if DRY:
        await pg.keyboard.press("Meta+A"); await pg.keyboard.press("Backspace")
        return False
    before = sum(1 for e in events() if e["type"] == "turn_finished")
    await pg.keyboard.press("Enter")
    rec.set(f"{phase}_sent", 0.12)
    await asyncio.sleep(5.0)
    rec.set(f"{phase}_working", 1.0)
    started, waiting = time.monotonic(), set()
    while time.monotonic() - started < 25 * 60:
        await asyncio.sleep(3)
        evs = events()
        if sum(1 for e in evs if e["type"] == "turn_finished") > before:
            break
        asked = {e["approval_id"] for e in evs if e["type"] == "approval_request"} - {e.get("approval_id") for e in evs if e["type"] == "approval_resolved"}
        if asked - waiting:
            card = next(e for e in evs if e["type"] == "approval_request" and e["approval_id"] in asked - waiting)
            print("WAITING FOR APPROVAL", card["turn_id"], card["approval_id"], "|", card.get("title"), "|", card.get("reason"), flush=True)
        waiting = asked
    rec.set(f"{phase}_done", 0.12)
    await asyncio.sleep(3.0)
    return True
