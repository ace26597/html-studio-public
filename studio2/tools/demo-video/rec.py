"""Shared pieces for recording the studio's real interface as timestamped frames."""
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from studio2_server.look import find_browser  # noqa: E402

W, H, SCALE = 1280, 800, 1.5
CURSOR_JS = """() => {
  if (document.getElementById('__rec_cursor')) return;
  const c = document.createElement('div'); c.id = '__rec_cursor';
  c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);transition:none';
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2l15 8-6.5 1.8L8.2 18z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const r = document.createElement('div'); r.id = '__rec_ring';
  r.style.cssText = 'position:fixed;left:0;top:0;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:3px solid #4f46e5;z-index:2147483646;pointer-events:none;opacity:0;transform:translate(-100px,-100px)';
  document.documentElement.append(r, c);
}"""

class Rec:
    def __init__(self, page, out: Path):
        self.page, self.out = page, out
        out.mkdir(parents=True, exist_ok=True)
        self.frames, self.phase, self.interval, self.stop, self.n = [], "start", 0.12, False, 0
        self.x, self.y = W / 2, H / 2
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        while not self.stop:
            began = time.monotonic()
            name = f"f{self.n:06d}.jpg"
            try:
                await self.page.screenshot(path=str(self.out / name), type="jpeg", quality=90)
                self.frames.append({"f": name, "t": began, "phase": self.phase})
                self.n += 1
                if self.n % 50 == 0:
                    self.save()
            except Exception as exc:  # a navigation in flight; try again on the next tick
                print("frame skipped:", str(exc)[:80], flush=True)
            await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - began)))

    def save(self):
        (self.out / "frames.json").write_text(json.dumps(self.frames))

    async def finish(self):
        self.stop = True
        if self.task:
            await self.task
        self.save()

    def set(self, phase: str, interval: float):
        self.phase, self.interval = phase, interval
        print(f"[{time.strftime('%H:%M:%S')}] phase {phase}", flush=True)

    async def cursor(self):
        await self.page.evaluate(CURSOR_JS)

    async def move(self, x: float, y: float, seconds: float = 0.6):
        await self.cursor()
        steps = max(2, int(seconds / 0.03))
        x0, y0 = self.x, self.y
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)
            self.x, self.y = x0 + (x - x0) * k, y0 + (y - y0) * k
            await self.page.mouse.move(self.x, self.y)
            await self.page.evaluate("([x,y]) => { const c = document.getElementById('__rec_cursor'); if (c) c.style.transform = `translate(${x}px,${y}px)`; }", [self.x, self.y])
            await asyncio.sleep(0.03)

    async def click(self, x: float | None = None, y: float | None = None, seconds: float = 0.6):
        if x is not None:
            await self.move(x, y, seconds)
        await self.page.evaluate("""([x,y]) => { const r = document.getElementById('__rec_ring'); if (!r) return;
          r.style.transition = 'none'; r.style.transform = `translate(${x}px,${y}px) scale(.4)`; r.style.opacity = '1';
          requestAnimationFrame(() => { r.style.transition = 'transform .45s ease-out, opacity .45s ease-out'; r.style.transform = `translate(${x}px,${y}px) scale(1.5)`; r.style.opacity = '0'; }); }""", [self.x, self.y])
        await self.page.mouse.click(self.x, self.y)
        await asyncio.sleep(0.35)

    async def drag(self, points, seconds: float = 1.2, lead: float = 0.6):
        """Press at the first point, pass through the rest, release at the last (drawing on the page)."""
        await self.move(points[0][0], points[0][1], lead)
        await self.page.mouse.down()
        await asyncio.sleep(0.12)
        each = max(0.03, seconds / max(1, len(points) - 1))
        for x, y in points[1:]:
            await self.move(x, y, each)
        await asyncio.sleep(0.12)
        await self.page.mouse.up()
        await asyncio.sleep(0.3)

    async def click_on(self, locator, seconds: float = 0.7, dx: float = 0.5, dy: float = 0.5):
        box = await locator.bounding_box()
        await self.click(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy, seconds)
        return box


async def open_browser(p):
    browser = await p.chromium.launch(executable_path=str(find_browser()), args=["--use-angle=metal"])
    context = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
    return browser, await context.new_page()
