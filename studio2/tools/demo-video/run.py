"""Record one request from a blank page until the turn ends.

    python run.py frames/<name> --name "Project name" --prompt "What to ask" [--minutes 60]

Like part_a.py, but the request and the project's name are arguments, and an approval card does not
end the recording: it is filmed, and the run carries on once somebody answers it in the studio.
Writes <frames dir>/../<name>.project with the project id.
"""
import asyncio, json, sys, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright
from rec import Rec, open_browser

BASE = "http://127.0.0.1:8200"
OUT = Path(sys.argv[1])


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


NAME, PROMPT, MINUTES = opt("--name", "Demo"), opt("--prompt"), float(opt("--minutes", "60"))
DATA = Path(__file__).resolve().parents[3] / "data/studio2/projects"


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def events(pid):
    return [json.loads(line) for line in open(DATA / pid / "events.jsonl")]


async def main():
    if not PROMPT:
        raise SystemExit("--prompt is needed")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(BASE + "/", wait_until="load"); await pg.wait_for_timeout(2000)
        await pg.get_by_role("button", name="New page").first.click(); await pg.wait_for_timeout(800)
        await pg.get_by_role("dialog").get_by_role("button", name="Create").click()
        await pg.wait_for_url("**/p/**", timeout=15000)
        pid = pg.url.rstrip("/").split("/")[-1]
        api(f"/api/projects/{pid}", "PATCH", {"name": NAME})
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT.parent / f"{OUT.name}.project").write_text(pid)
        print("project", pid, flush=True)
        await pg.reload(wait_until="load"); await pg.wait_for_timeout(3000)

        rec = Rec(pg, OUT)
        await rec.cursor()
        rec.set("blank", 0.12); rec.start()
        await asyncio.sleep(1.8)
        rec.set("typing", 0.1)
        box = pg.locator("textarea").first
        await rec.click_on(box, 0.9, dx=0.3)
        await pg.keyboard.type(PROMPT, delay=20)
        await asyncio.sleep(1.0)
        await pg.keyboard.press("Enter")
        rec.set("sent", 0.12)
        await asyncio.sleep(6.0)
        rec.set("working", 2.0)
        started, mark, waiting = time.monotonic(), -1, set()
        while time.monotonic() - started < MINUTES * 60:
            await asyncio.sleep(5)
            if not api(f"/api/projects/{pid}").get("running_turn"):
                break
            evs = events(pid)
            asked = {e["approval_id"] for e in evs if e["type"] == "approval_request"} - {e.get("approval_id") for e in evs if e["type"] == "approval_resolved"}
            if asked - waiting:
                card = next(e for e in evs if e["type"] == "approval_request" and e["approval_id"] in asked - waiting)
                print("WAITING FOR APPROVAL", card["turn_id"], card["approval_id"], "|", card.get("title"), "|", card.get("reason"), flush=True)
            waiting = asked
            minute = int(time.monotonic() - started) // 120
            if minute != mark:
                mark = minute
                print(f"{int(time.monotonic() - started)}s, {sum(1 for e in evs if e['type'] == 'tool_call')} tool calls", flush=True)
        rec.set("finished", 0.12)
        await asyncio.sleep(5.0)
        await rec.finish()
        print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms")) for e in events(pid) if e["type"] in ("turn_started", "turn_finished")], flush=True)
        print("frames:", rec.n, "still running:", bool(api(f"/api/projects/{pid}").get("running_turn")), flush=True)
        await browser.close()

asyncio.run(main())
