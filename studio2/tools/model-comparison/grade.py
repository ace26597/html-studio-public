"""Grade each model's dashboard in a real browser against the ground truth, and take its pictures.

Usage (from studio2/, with PYTHONPATH=server): python tools/model-comparison/grade.py <run folder> [part of a model name ...]
Reads <run folder>/results.jsonl, writes <run folder>/grades.jsonl and
<run folder>/shots/<name>-{desktop,full,dark,phone}.jpg. With names, only those models are graded
and grades.jsonl holds only them. The studio that ran the requests must still be running (STUDIO_URL,
http://127.0.0.1:8200 when not set): the pages are loaded from it. Nothing here judges looks: every
check is something the page either does or does not do.
"""
import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from studio2_server.look import find_browser

HERE = Path(__file__).parent
BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8200").rstrip("/")
ROWS = list(csv.DictReader(open(HERE / "orders.csv")))
TRUTH = json.load(open(HERE / "truth.json"))
NUMBER = re.compile(r"(\$)?\s?(\d[\d,]*(?:\.\d+)?)\s?(million|thousand|[KkMm](?![A-Za-z]))?\s?(%)?")


def facts(rows):
    won = [r for r in rows if r["stage"] == "Won"]
    lost = [r for r in rows if r["stage"] == "Lost"]
    return {"won": len(won), "revenue": sum(float(r["price_usd"]) for r in won),
            "win_rate": 100 * len(won) / (len(won) + len(lost)) if won or lost else 0,
            "avg_kw": sum(float(r["system_kw"]) for r in won) / len(won) if won else 0}


def numbers(text):
    """Every number in the text as (value, is a percentage), with K and M spelled out."""
    out = []
    for _dollar, digits, scale, percent in NUMBER.findall(text):
        try:
            value = float(digits.replace(",", ""))
        except ValueError:
            continue
        scale = (scale or "").lower()
        value *= 1_000_000 if scale in ("m", "million") else 1_000 if scale in ("k", "thousand") else 1
        out.append((value, bool(percent)))
    return out


def headline(text, want):
    """Which of the four headline numbers the text shows: revenue within 0.6 %, the count exactly,
    the win rate within 0.6 points (as a percentage), the average size within 0.06 kW."""
    found = numbers(text)
    return {"revenue": any(abs(v - want["revenue"]) <= want["revenue"] * 0.006 for v, pct in found if not pct),
            "won": any(v == want["won"] for v, pct in found if not pct),
            "win_rate": any(abs(v - want["win_rate"]) <= 0.6 for v, pct in found if pct),
            "avg_kw": any(abs(v - want["avg_kw"]) <= 0.06 for v, pct in found if not pct)}


def wake(pid):
    """Ask the studio for the project's events for a moment: that is what makes it serve the page."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{BASE}/api/projects/{pid}/events", timeout=3) as stream:
            stream.read(64)
    except Exception:
        pass


async def settle(page, ms=1800):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(ms)


async def text_of(page):
    return await page.evaluate("document.body ? document.body.innerText : ''")


async def choose(page, word):
    """Pick `word` in a filter: a select that offers it, else something clickable that says it.
    → how it was done, or None when the page offers no such control."""
    for select in await page.locator("select").all():
        options = await select.locator("option").all_inner_texts()
        match = next((o for o in options if word.lower() in o.lower()), None)
        if match and await select.is_visible():
            await select.select_option(label=match)
            return "select"
    for role in ("button", "checkbox", "radio", "tab", "option"):
        target = page.get_by_role(role, name=re.compile(rf"^\s*{re.escape(word)}\b", re.I)).first
        if await target.count() and await target.is_visible():
            await target.click()
            return role
    target = page.locator("label, [data-city], [data-channel], .chip, .pill, .filter *", has_text=re.compile(rf"^\s*{re.escape(word)}\s*$")).first
    if await target.count() and await target.is_visible():
        await target.click()
        return "label"
    return None


async def filter_check(page, column, word, before):
    """Choose one value of a filter and compare the page with the data for that value (or, for a
    page whose filter is a set of ticked boxes, for everything but that value)."""
    try:
        how = await choose(page, word)
    except Exception as exc:
        return {"result": "error", "detail": str(exc)[:120]}
    if not how:
        return {"result": "no control found"}
    await page.wait_for_timeout(1200)
    after = await text_of(page)
    if after == before:
        return {"result": "nothing changed", "how": how}
    only = facts([r for r in ROWS if r[column] == word])
    rest = facts([r for r in ROWS if r[column] != word])
    for name, want in (("only", only), ("all but", rest)):
        seen = headline(after, want)
        if seen["revenue"] and seen["won"]:
            return {"result": "correct", "how": how, "reading": name, "shown": seen}
    return {"result": "changed, numbers do not match", "how": how}


async def sort_check(page):
    """The table of the largest won orders: the visible table that holds the most order numbers."""
    best, most = None, 0
    for table in await page.locator("table").all():
        if not await table.is_visible():
            continue
        found = len(re.findall(r"BL-\d{4}", await table.inner_text()))
        if found > most:
            best, most = table, found
    if best is None:
        return {"rows": 0, "sortable": False, "top15": 0}
    body = await best.inner_text()
    rows = await best.locator("tbody tr").count()
    paged = False
    if rows < 15:       # a table shown a few rows at a time: read every page
        following = page.get_by_role("button", name=re.compile(r"next", re.I)).first
        for _ in range(6):
            if not await following.count() or not await following.is_visible() or not await following.is_enabled():
                break
            await following.click()
            await page.wait_for_timeout(300)
            more = await best.inner_text()
            if more in body:
                break
            body, paged = body + "\n" + more, True
        if paged:       # back to the first page for the sorting check
            back = page.get_by_role("button", name=re.compile(r"prev", re.I)).first
            for _ in range(6):
                if not await back.count() or not await back.is_enabled():
                    break
                await back.click()
                await page.wait_for_timeout(200)
    top15 = sum(1 for order in TRUTH["top_orders"] if order in body)

    async def order():
        return await best.locator("tbody tr").evaluate_all("rows => rows.slice(0, 6).map(r => r.innerText)")
    first = await order()
    heads = await best.locator("thead th, thead td").all()
    for head in heads[:8]:
        try:
            await head.click(timeout=2000)
            await page.wait_for_timeout(350)
            if await order() != first:
                return {"rows": rows, "sortable": True, "top15": top15, "paged": paged}
        except Exception:
            continue
    return {"rows": rows, "sortable": False, "top15": top15, "paged": paged}


async def grade(browser, row, shots):
    name = row["model"].replace("openrouter/", "").replace("/", "_")
    out = {"model": row["model"], "name": name, "pid": row.get("pid")}
    url = row.get("page")
    if not url:
        return {**out, "loaded": False}
    wake(row["pid"])
    errors, outside = [], set()
    context = await browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    page = await context.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)[:160]))
    page.on("console", lambda msg: errors.append(msg.text[:160]) if msg.type == "error" else None)
    page.on("request", lambda req: outside.add(req.url.split("/")[2]) if req.url.startswith("http") and "127.0.0.1" not in req.url.split("/")[2] else None)
    try:
        await page.goto(url, wait_until="load", timeout=30000)
    except Exception as exc:
        await context.close()
        return {**out, "loaded": False, "error": str(exc)[:160]}
    await settle(page)
    text = await text_of(page)
    out.update(loaded=True, text_chars=len(text), headline=headline(text, facts(ROWS)),
               stands_out=bool(re.search(r"stands?\s+out", text, re.I)),
               charts=await page.evaluate("""() => [...document.querySelectorAll('canvas, svg')].filter(e => {
                   const r = e.getBoundingClientRect(); return r.width >= 200 && r.height >= 100; }).length"""),
               page_height=await page.evaluate("document.documentElement.scrollHeight"))
    await page.screenshot(path=str(shots / f"{name}-desktop.jpg"), type="jpeg", quality=74)
    await page.screenshot(path=str(shots / f"{name}-full.jpg"), type="jpeg", quality=70, full_page=True)
    out["sort"] = await sort_check(page)
    await page.goto(url, wait_until="load", timeout=30000)
    await settle(page)
    before = await text_of(page)
    out["filter_city"] = await filter_check(page, "city", "Portland", before)
    await page.goto(url, wait_until="load", timeout=30000)
    await settle(page)
    out["filter_channel"] = await filter_check(page, "channel", "Referral", await text_of(page))
    out["errors"], out["outside_hosts"] = errors[:6], sorted(outside)
    await context.close()

    dark = await browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    page = await dark.new_page()
    await page.goto(url, wait_until="load", timeout=30000)
    await settle(page)
    out["dark_background"] = await page.evaluate("""() => {
        const lum = c => { const m = c.match(/[\\d.]+/g); if (!m || (m.length > 3 && +m[3] === 0)) return null;
                           return (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255; };
        for (const el of [document.body, document.documentElement]) {
            const value = lum(getComputedStyle(el).backgroundColor); if (value !== null) return value; }
        return 1; }""")
    await page.screenshot(path=str(shots / f"{name}-dark.jpg"), type="jpeg", quality=74)
    await dark.close()

    phone = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, color_scheme="light")
    page = await phone.new_page()
    await page.goto(url, wait_until="load", timeout=30000)
    await settle(page)
    out["phone_overflow_px"] = await page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    await page.screenshot(path=str(shots / f"{name}-phone.jpg"), type="jpeg", quality=72)
    await phone.close()
    out["answer"] = headline(row.get("text") or "", facts(ROWS))
    return out


async def main(run, only):
    shots = run / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    rows = {}
    for line in open(run / "results.jsonl"):
        row = json.loads(line)
        rows[row["model"]] = row                      # a later line for the same model replaces the earlier
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(find_browser()))
        with open(run / "grades.jsonl", "w") as out:
            for model, row in rows.items():
                if only and not any(word in model for word in only):
                    continue
                try:
                    result = await grade(browser, row, shots)
                except Exception as exc:
                    result = {"model": model, "loaded": False, "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}
                out.write(json.dumps(result) + "\n")
                out.flush()
                print(model, {k: result.get(k) for k in ("loaded", "headline", "charts", "filter_city", "sort")}, flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
