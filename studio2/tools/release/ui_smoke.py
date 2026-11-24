"""Drive a built interface in a real browser before it is put live (tools/release-ui.sh).

    python ui_smoke.py http://127.0.0.1:<port>

The interface's own tests draw each component once and never again, so a build can pass them
all and still blank the page the first time a panel opens or closes. This opens a project, sends
a message to the no-model agent, closes and reopens the chat, reloads, and does the same at phone
size, failing on any page error. It uses the Chromium the studio already looks at pages with.
"""
import asyncio
import json
import sys
import urllib.request

from playwright.async_api import async_playwright

from studio2_server.look import find_browser

BASE = sys.argv[1].rstrip("/")
STEP_MS = 15000


def api(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return json.loads(answer.read() or b"null")


async def main() -> int:
    project = api("/api/projects", {"name": "Release check"})
    pid, steps, errors = project["id"], [], []

    def done(step: str) -> None:
        steps.append(step)
        print("  ok  ", step)

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=find_browser())
        try:
            # ---- a desktop ---------------------------------------------------------------
            desk = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await desk.new_page()
            page.set_default_timeout(STEP_MS)
            page.on("pageerror", lambda error: errors.append(f"desktop: {error}"))
            await page.goto(BASE + "/")
            await page.get_by_text("Release check").first.wait_for()
            done("the home page lists the project")
            await page.goto(f"{BASE}/p/{pid}")
            box = page.locator("#composer-input")
            await box.wait_for()
            done("a project opens with the chat")
            await box.fill("Add a heading that says Release check")
            await box.press("Enter")
            await page.wait_for_function(
                "() => !!document.querySelector('.chat-scroll')?.textContent.includes('Release check')")
            for _ in range(60):                     # the request ends: nothing running
                if not api(f"/api/projects/{pid}").get("running_turn"):
                    break
                await page.wait_for_timeout(500)
            else:
                errors.append("desktop: the request never finished")
            done("a message is sent and the request finishes")
            frame = next((f for f in page.frames if f is not page.main_frame and f.url.startswith("http")), None)
            if frame is None:
                errors.append("desktop: the project's page is not in a frame")
            else:
                await frame.wait_for_function("() => !!document.body && document.body.innerText.length > 0")
                done("the project's page shows in its frame")
            await box.focus()
            await page.keyboard.press("Escape")
            await page.locator(".chat-dock .chat-fab").wait_for()
            await page.locator(".chat-dock .chat-fab").click()
            await box.wait_for()
            done("the chat closes to its button and opens again")
            await page.reload()
            await page.locator(".chat-scroll").get_by_text("Release check").first.wait_for()
            done("after a reload the conversation is still there")
            await desk.close()

            # ---- a phone -----------------------------------------------------------------
            phone = await browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True)
            page = await phone.new_page()
            page.set_default_timeout(STEP_MS)
            page.on("pageerror", lambda error: errors.append(f"phone: {error}"))
            await page.goto(f"{BASE}/p/{pid}")
            await page.locator(".chat-dock.is-compact .chat-fab").wait_for()
            await page.locator(".chat-dock.is-compact .chat-fab").tap()
            await page.locator(".chat.sheet #composer-input").wait_for()
            await page.locator(".sheet-grab").tap()
            await page.locator(".chat-dock.is-compact .chat-fab").wait_for()
            done("on a phone the chat is a button, opens as a sheet and closes again")
            await phone.close()
        except Exception as error:                  # a step that never happened
            errors.append(f"stopped after '{steps[-1] if steps else 'nothing'}': {str(error).splitlines()[0]}")
        finally:
            await browser.close()

    if errors:
        print("\nThe interface did NOT pass:")
        for error in errors:
            print("  -", error)
        return 1
    print(f"\nThe interface passed {len(steps)} steps with no page error.")
    return 0


sys.exit(asyncio.run(main()))
