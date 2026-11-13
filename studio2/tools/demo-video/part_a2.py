import asyncio, json, sys, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright
from rec import Rec, open_browser

BASE = "http://127.0.0.1:8200"
PID, OUT = sys.argv[1], Path(sys.argv[2])

def api(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read())

async def main():
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PID}", wait_until="load"); await pg.wait_for_timeout(5000)
        rec = Rec(pg, OUT)
        rec.set("working", 2.0); rec.start()
        started = time.monotonic()
        while time.monotonic() - started < 90 * 60:
            await asyncio.sleep(5)
            if not api(f"/api/projects/{PID}").get("running_turn"):
                break
            if int(time.monotonic() - started) // 300 != getattr(main, "_mark", -1):
                main._mark = int(time.monotonic() - started) // 300
                print(f"{int(time.monotonic() - started)}s more", flush=True)
        rec.set("finished", 0.12)
        await asyncio.sleep(5.0)
        await rec.finish()
        print("frames:", rec.n, "running:", api(f"/api/projects/{PID}").get("running_turn"), flush=True)
        await browser.close()
asyncio.run(main())
