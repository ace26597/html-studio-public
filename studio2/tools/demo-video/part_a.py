import asyncio, json, sys, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright
from rec import Rec, open_browser

BASE = "http://127.0.0.1:8200"
OUT = Path(sys.argv[1])
PROMPT = ("Grab all the open data we can find about Manhattan and New York City. A lot is public: street trees, "
          "parking, 311 complaints and much more. Check what can actually be fetched, then build a dashboard I can "
          "navigate, with details about each dataset. If something needs data you cannot reach, tell me.")

def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    return json.loads(raw) if raw else None

async def main():
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(BASE + "/", wait_until="load"); await pg.wait_for_timeout(2000)
        await pg.get_by_role("button", name="New page").first.click(); await pg.wait_for_timeout(800)
        await pg.get_by_role("dialog").get_by_role("button", name="Create").click()
        await pg.wait_for_url("**/p/**", timeout=15000)
        pid = pg.url.rstrip("/").split("/")[-1]
        api(f"/api/projects/{pid}", "PATCH", {"name": "NYC open data explorer"})
        (OUT.parent / "project.txt").write_text(pid)
        print("project", pid, flush=True)
        await pg.reload(wait_until="load"); await pg.wait_for_timeout(3000)

        rec = Rec(pg, OUT)
        await rec.cursor()
        rec.set("blank", 0.12); rec.start()
        await asyncio.sleep(1.8)
        rec.set("typing", 0.1)
        box = pg.locator("textarea").first
        await rec.click_on(box, 0.9, dx=0.3)
        await pg.keyboard.type(PROMPT, delay=24)
        await asyncio.sleep(1.0)
        picker = await pg.locator("button", has_text="Opus").count()
        print("model shows Opus:", bool(picker), flush=True)
        await pg.keyboard.press("Enter")
        rec.set("sent", 0.12)
        await asyncio.sleep(6.0)
        rec.set("working", 2.0)
        started, last = time.monotonic(), ""
        while time.monotonic() - started < 50 * 60:
            await asyncio.sleep(5)
            project = api(f"/api/projects/{pid}")
            if not project.get("running_turn"):
                break
            events = [json.loads(line) for line in open(Path(__file__).resolve().parents[3] / f"data/studio2/projects/{pid}/events.jsonl")]
            asked = {e["approval_id"] for e in events if e["type"] == "approval_request"} - {e.get("approval_id") for e in events if e["type"] == "approval_resolved"}
            if asked:
                print("WAITING FOR APPROVAL", asked, flush=True)
                break
            calls = sum(1 for e in events if e["type"] == "tool_call")
            note = f"{int(time.monotonic() - started)}s, {calls} tool calls"
            if int(time.monotonic() - started) // 120 != getattr(main, "_mark", -1):
                main._mark = int(time.monotonic() - started) // 120
                print(note, flush=True)
        rec.set("finished", 0.12)
        await asyncio.sleep(5.0)
        await rec.finish()
        events = [json.loads(line) for line in open(Path(__file__).resolve().parents[3] / f"data/studio2/projects/{pid}/events.jsonl")]
        print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms")) for e in events if e["type"] in ("turn_started", "turn_finished")], flush=True)
        print("frames:", rec.n, flush=True)
        await browser.close()
asyncio.run(main())
