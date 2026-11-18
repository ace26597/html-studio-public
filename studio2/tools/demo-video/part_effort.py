"""Record a request with a chosen depth and the check switch, set by real clicks in the composer.

    python part_effort.py frames/<name> --name "Project name" --prompt "What to ask" --depth quick|standard|deep [--verify] [--scroll] [--minutes 60]
    python part_effort.py frames/<name> --project <id> --check-page [--minutes 30]
    python part_effort.py frames/<name> --project <id> --after [--scroll]     # a fresh load: only the card and the page

New project (--name/--prompt): a blank page, then Quick/Standard/Deep and "Check the result" are
clicked before the request is typed. --project with --check-page presses "Check this page" on an
existing project instead of typing. Either way it records until the request ends, then scrolls the
chat to the check's result card and opens its details (if there is a card), and with --scroll walks
slowly down the page. Writes <frames dir>/../<name>.project with the project id.
Phases: blank, depth, verify, typing, sent, working, finished, card, details, scroll, end.
"""
import asyncio, json, re, sys, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright
from rec import Rec, open_browser

BASE = "http://127.0.0.1:8200"
DATA = Path(__file__).resolve().parents[3] / "data/studio2/projects"
OUT = Path(sys.argv[1])


def opt(name, default=""):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


NAME, PROMPT, DEPTH, PROJECT = opt("--name", "Demo"), opt("--prompt"), opt("--depth"), opt("--project")
VERIFY, CHECK_PAGE, SCROLL, MINUTES = "--verify" in sys.argv, "--check-page" in sys.argv, "--scroll" in sys.argv, float(opt("--minutes", "60"))


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def events(pid):
    path = DATA / pid / "events.jsonl"          # a new project has none until its first request
    return [json.loads(line) for line in open(path)] if path.exists() else []


async def open_chat(rec, pg):
    opener = pg.locator('[aria-label^="Open chat"]')
    if await opener.count() and await opener.first.is_visible():
        await rec.click_on(opener.first, 0.7)
        await asyncio.sleep(0.7)


async def open_effort(rec, pg):
    """The depth steps and the check switch live in a menu behind the composer's effort button
    (since 17 November 2026; before that they were a row of buttons in the composer)."""
    if not await pg.get_by_role("menuitemradio").count():
        await rec.click_on(pg.locator(".effort-btn").first, 0.7)
        await asyncio.sleep(0.8)


async def main():
    if not PROJECT and not PROMPT:
        raise SystemExit("--prompt (new project) or --project is needed")
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        if PROJECT:
            pid = PROJECT
            await pg.goto(f"{BASE}/p/{pid}", wait_until="load"); await pg.wait_for_timeout(4000)
        else:
            await pg.goto(BASE + "/", wait_until="load"); await pg.wait_for_timeout(2000)
            await pg.get_by_role("button", name="New page").first.click(); await pg.wait_for_timeout(800)
            await pg.get_by_role("dialog").get_by_role("button", name="Create").click()
            await pg.wait_for_url("**/p/**", timeout=15000)
            pid = pg.url.rstrip("/").split("/")[-1]
            api(f"/api/projects/{pid}", "PATCH", {"name": NAME, "model": opt("--model", "opus")})
            await pg.reload(wait_until="load"); await pg.wait_for_timeout(3000)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT.parent / f"{OUT.name}.project").write_text(pid)
        project = api(f"/api/projects/{pid}")
        print("project", pid, "connection", project.get("connection"), "model", project.get("model"), flush=True)
        before = sum(1 for e in events(pid) if e["type"] == "turn_finished")

        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("blank", 0.12); rec.start()
        await asyncio.sleep(1.8)
        await open_chat(rec, pg)
        if DEPTH:
            rec.set("depth", 0.1)
            await open_effort(rec, pg)
            step = pg.get_by_role("menuitemradio", name=re.compile("^" + DEPTH.capitalize())).first
            await rec.click_on(step, 0.6)
            await asyncio.sleep(2.2)             # the button's label changes; let it be read
        if VERIFY:
            rec.set("verify", 0.1)
            await open_effort(rec, pg)
            box = pg.get_by_role("menuitemcheckbox", name=re.compile("Check the result")).first
            if await box.get_attribute("aria-checked") != "true":
                await rec.click_on(box, 0.7)
            await asyncio.sleep(1.2)
            if await box.count() and await box.is_visible():
                await pg.keyboard.press("Escape")
            await asyncio.sleep(1.0)
        if CHECK_PAGE:
            rec.set("typing", 0.1)
            await rec.click_on(pg.get_by_role("button", name="Check this page").first, 0.9)
        else:
            rec.set("typing", 0.1)
            area = pg.locator("textarea").first
            await rec.click_on(area, 0.8, dx=0.3, dy=0.3)
            await pg.keyboard.type(PROMPT, delay=20)
            await asyncio.sleep(1.0)
            await pg.keyboard.press("Enter")
        rec.set("sent", 0.12)
        await asyncio.sleep(6.0)
        rec.set("working", 2.0)
        started, mark, waiting = time.monotonic(), -1, set()
        while time.monotonic() - started < MINUTES * 60:
            await asyncio.sleep(5)
            evs = events(pid)
            if sum(1 for e in evs if e["type"] == "turn_finished") > before:
                break
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
        await after(rec, pg, pid)
        await finish(rec, browser, pid)


async def finish(rec, browser, pid):
    rec.set("end", 0.12)
    await asyncio.sleep(1.5)
    await rec.finish()
    evs = events(pid)
    print("turns:", [(e["type"], e.get("status"), (e.get("usage") or {}).get("duration_ms"), e.get("options")) for e in evs if e["type"] in ("turn_started", "turn_finished")], flush=True)
    print("verification:", [{k: e.get(k) for k in ("checked", "passed", "fixed", "failed", "unverifiable")} for e in evs if e["type"] == "verification"], flush=True)
    print("notices:", [e.get("message") for e in evs if e["type"] == "notice"], flush=True)
    print("frames:", rec.n, flush=True)
    await browser.close()


# Scroll only the chat's own scroll box. scrollIntoView also scrolls the studio's shell, which then
# sits ~280 px too high for the rest of the recording, and a click aimed at "Collapse chat" lands
# on the theme switch.
CHAT_SCROLL_JS = """([el, dy]) => {
  let box = el.parentElement;
  while (box && !(/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight)) box = box.parentElement;
  if (!box) return false;
  if (dy === null) box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 8;
  else box.scrollTop += dy;
  return true;
}"""
SHELL_OK_JS = "() => { const s = document.scrollingElement; return s.scrollTop === 0 }"


async def after(rec, pg, pid):
    """The result card (opened) and, with --scroll, a slow walk down the finished page."""
    await open_chat(rec, pg)
    card = pg.locator(".verification").last
    if await card.count():
        rec.set("card", 0.12)
        handle = await card.element_handle()
        await pg.evaluate(CHAT_SCROLL_JS, [handle, None])
        await asyncio.sleep(1.5)
        b = await card.bounding_box()
        await rec.move(b["x"] + b["width"] * 0.4, b["y"] + 20, 0.8)
        await asyncio.sleep(3.0)
        toggle = card.locator(".verification-toggle")
        if await toggle.count():
            rec.set("details", 0.12)
            if (await toggle.get_attribute("aria-expanded")) == "false":
                await rec.click_on(toggle, 0.7)
                await asyncio.sleep(1.0)
            for _ in range(10):                       # read the findings: the chat scrolls slowly through them
                await pg.evaluate(CHAT_SCROLL_JS, [handle, 90])
                await asyncio.sleep(0.7)
        await asyncio.sleep(2.0)
    else:
        print("no verification card in the chat", flush=True)
    if not await pg.evaluate(SHELL_OK_JS):
        print("WARNING: the studio's shell is scrolled; frames are shifted", flush=True)

    if SCROLL:
        rec.set("scroll", 0.12)
        collapse = pg.get_by_role("button", name="Collapse chat")
        if await collapse.count() and await collapse.first.is_visible():
            await rec.click_on(collapse.first, 0.7)
            await asyncio.sleep(1.0)
        await rec.move(640, 470, 0.6)
        preview = (api(f"/api/projects/{pid}").get("preview_url") or "").rstrip("/")
        last, still = None, 0
        for _ in range(600):                          # wheel down until the page stops moving
            await pg.mouse.wheel(0, 70)
            await asyncio.sleep(0.12)
            frame = next((f for f in pg.frames if preview and f.url.startswith(preview)), None)  # re-found: a reload replaces it
            try:
                y = await frame.evaluate("() => Math.round(scrollY)") if frame else None
            except Exception:
                y = None
            still = still + 1 if (y is not None and y == last) else 0
            if still >= 4:
                break
            last = y
        await asyncio.sleep(2.0)


async def after_only():
    """--after: a fresh load of a finished project, then the card and the page (no request)."""
    async with async_playwright() as p:
        browser, pg = await open_browser(p)
        await pg.goto(f"{BASE}/p/{PROJECT}", wait_until="load"); await pg.wait_for_timeout(6000)
        rec = Rec(pg, OUT)
        await rec.cursor(); await rec.move(900, 420, 0.1)
        rec.set("result", 0.12); rec.start()
        await asyncio.sleep(2.5)
        await after(rec, pg, PROJECT)
        await finish(rec, browser, PROJECT)

asyncio.run(after_only() if "--after" in sys.argv else main())
