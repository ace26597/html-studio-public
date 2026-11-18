"""Video and pictures made from a project page (CONTRACT.md, "Video and images from a page").

A video is a page whose picture depends only on time. The renderer loads it in the headless
browser with the page's clock replaced by one the studio steps frame by frame, takes one
picture per frame and hands them to ffmpeg. Nothing is recorded in real time, so a slow frame
(a heavy 3D scene) costs render time, never a dropped frame.

Uses the same cached Chromium as `look` and an ffmpeg that is already installed. Without
either the tools are listed as unavailable with the reason.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

FPS_MAX, DURATION_MAX, SIDE_MAX, PIXELS_MAX = 60, 180.0, 4096, 3840 * 2160
FORMATS = ("mp4", "gif", "webm")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_TYPES = (".mp3", ".wav", ".m4a", ".aac", ".aiff", ".aif", ".ogg", ".flac")
TEXT_CAP, LIST_CAP, READY_SECONDS, RENDER_SECONDS = 300, 20, 20.0, 900.0
STATIC = Path(__file__).parent / "static"
_one_render = asyncio.Lock()        # one video at a time: a render uses every core ffmpeg can get


class MediaError(Exception):
    pass


def find_ffmpeg() -> str | None:
    override = os.environ.get("STUDIO2_FFMPEG")
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("ffmpeg")


def video_reason(looker) -> str | None:
    """None when a page can be rendered to video here; otherwise why not."""
    if looker.reason():
        return looker.reason()
    return None if find_ffmpeg() else "ffmpeg is not installed on this machine (STUDIO2_FFMPEG, or `ffmpeg` on the path)."


def clock_script() -> str:
    return "window.__studioRender = true;\n" + (STATIC / "render-clock.js").read_text("utf-8")


def _even(value: float) -> int:
    return max(2, int(round(value)) // 2 * 2)


def plan(declared: dict | None, *, width=None, height=None, fps=None, duration=None, start=0.0, scale=1) -> dict:
    """Arguments win over what the page declares (`window.studioVideo`); → the checked settings."""
    declared = declared or {}

    def pick(given, key, default=None):
        value = given if given not in (None, "", 0) else declared.get(key)
        try:
            return float(value) if value not in (None, "", 0) else default
        except (TypeError, ValueError):
            raise MediaError(f"{key} must be a number.") from None

    width, height = pick(width, "width", 1920), pick(height, "height", 1080)
    fps, duration = pick(fps, "fps", 30), pick(duration, "duration")
    if duration is None:
        raise MediaError("How long is the video? The page declares no duration (window.studioVideo.duration, "
                         "or data-duration on the stage); pass `duration` in seconds.")
    scale = 2 if str(scale) == "2" else 1
    width, height = _even(width), _even(height)
    if not (16 <= width <= SIDE_MAX and 16 <= height <= SIDE_MAX) or width * height * scale * scale > PIXELS_MAX:
        raise MediaError(f"The picture is too large ({width}×{height} at scale {scale}). The limit is 3840×2160 in all.")
    if not 1 <= fps <= FPS_MAX:
        raise MediaError(f"fps must be between 1 and {FPS_MAX}.")
    start = max(0.0, float(start or 0))
    if not 0.1 <= duration <= DURATION_MAX:
        raise MediaError(f"duration must be between 0.1 and {DURATION_MAX:.0f} seconds.")
    return {"width": width, "height": height, "fps": fps, "duration": duration, "start": start, "scale": scale,
            "frames": max(1, int(round(duration * fps)))}


def sheet_frames(count: int, times: list | None, settings: dict) -> dict[int, float]:
    """Which frames go on the contact sheet → {frame index: its time in seconds}."""
    total, fps, start = settings["frames"], settings["fps"], settings["start"]
    if times:
        wanted = [max(0, min(total - 1, int(round((float(t) - start) * fps)))) for t in times[:24]]
    else:
        count = max(1, min(int(count or 12), 24, total))
        wanted = [min(total - 1, int((i + 0.5) * total / count)) for i in range(count)]
    return {index: start + index / fps for index in sorted(set(wanted))}


def stamp(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def contact_sheet(shots: list[tuple[float, bytes]], target: Path) -> dict:
    """Lay the frames out in a grid, each with its time, so the agent can read the motion in one image."""
    from PIL import Image, ImageDraw, ImageFont
    images = [(at, Image.open(io.BytesIO(data)).convert("RGB")) for at, data in shots]
    width, height = images[0][1].size
    portrait = height > width
    columns = 1 if len(images) == 1 else 2 if len(images) <= 4 else (6 if portrait else 4)
    columns = min(columns, len(images))
    cell_w = min(width, {1: 1600, 2: 800}.get(columns, 1920 // columns))
    cell_h = round(cell_w * height / width)
    label, gap = 26, 6
    rows = -(-len(images) // columns)
    sheet = Image.new("RGB", (columns * cell_w + (columns + 1) * gap, rows * (cell_h + label) + (rows + 1) * gap), "#111418")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=17)
    except TypeError:               # an older Pillow
        font = ImageFont.load_default()
    for index, (at, image) in enumerate(images):
        x = gap + (index % columns) * (cell_w + gap)
        y = gap + (index // columns) * (cell_h + label + gap)
        sheet.paste(image.resize((cell_w, cell_h), Image.LANCZOS), (x, y + label))
        draw.text((x + 4, y + 3), stamp(at), fill="#e8eaed", font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, "JPEG", quality=84)
    return {"image": str(target), "frames": len(images), "columns": columns, "frame_width": cell_w}


def _out_path(workspace: Path, out: str | None, page: str, fmt: str) -> Path:
    from . import workspace as ws
    relative = out or f"media/{Path(page).stem}.{fmt}"
    try:
        target = ws.safe_path(workspace, relative)
    except Exception:
        raise MediaError("out must be a path inside the project.") from None
    if target.suffix.lower() != f".{fmt}":
        raise MediaError(f"out must end in .{fmt} (or pass format).")
    return target


def _audio_path(workspace: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    from . import workspace as ws
    try:
        path = ws.safe_path(workspace, str(relative))
    except Exception:
        raise MediaError("audio must be a file inside the project.") from None
    if not path.is_file() or path.suffix.lower() not in AUDIO_TYPES:
        raise MediaError(f"There is no sound file at {relative} (use one of {', '.join(AUDIO_TYPES)}).")
    return path


def encode_args(ffmpeg: str, settings: dict, target: Path, audio: Path | None, volume: float) -> list[str]:
    """Frames arrive as PNG on standard input → an H.264 MP4 any phone, browser or editor plays."""
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "image2pipe", "-framerate", f"{settings['fps']:g}", "-c:v", "png", "-i", "pipe:0"]
    if audio:
        args += ["-i", str(audio)]
    args += ["-map", "0:v:0", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "17",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
             "-r", f"{settings['fps']:g}", "-movflags", "+faststart"]
    if audio:
        fade = max(0.0, settings["duration"] - 1.0)
        args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
                 "-af", f"volume={max(0.0, min(float(volume), 2.0)):g},afade=t=out:st={fade:g}:d=1"]
    return [*args, "-t", f"{settings['duration']:g}", str(target)]


def convert_args(ffmpeg: str, master: Path, target: Path, fmt: str, settings: dict) -> list[str]:
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(master)]
    if fmt == "gif":
        side = min(settings["width"], 800)
        rate = min(settings["fps"], 15)
        return [*base, "-vf", f"fps={rate:g},scale={side}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                              "[b][p]paletteuse=dither=sierra2_4a", "-loop", "0", str(target)]
    return [*base, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "31", "-row-mt", "1", "-c:a", "libopus", str(target)]


async def _run(args: list[str], what: str) -> None:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _out, err = await process.communicate()
    if process.returncode:
        raise MediaError(f"{what} failed: {' '.join(err.decode('utf-8', 'replace').split())[-400:]}")


READY_JS = """() => {
  window.__studioReady = 'waiting';
  (async () => {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const video = window.studioVideo;
    if (video && video.ready) await video.ready;
    window.__studioReady = 'ready';
  })().catch(error => { window.__studioReady = 'failed: ' + (error && error.message || error); });
}"""
DECLARED_JS = """() => {
  const v = window.studioVideo;
  if (!v) return null;
  const out = {};
  for (const key of ['width', 'height', 'fps', 'duration', 'audio', 'poster']) if (v[key] != null) out[key] = v[key];
  out.seek = typeof v.seek === 'function';
  return out;
}"""


async def _open(looker, url: str, scale: int, console: list, failed: list):
    """→ (context, page, what the page declares) with the page loaded, its clock held at zero, and ready."""
    context = await looker.new_context(url, viewport={"width": 1920, "height": 1080}, device_scale_factor=scale)
    try:
        await context.add_init_script(clock_script())
        page = await context.new_page()
        page.on("console", lambda m: console.append({"level": m.type, "text": m.text[:TEXT_CAP]})
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: console.append({"level": "exception", "text": str(e)[:TEXT_CAP]}))
        page.on("response", lambda r: failed.append({"url": r.url[:TEXT_CAP], "status": r.status})
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        response = await page.goto(url, wait_until="load", timeout=30_000)
        if response is not None and response.status >= 400:
            raise MediaError(f"The page answered {response.status}.")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)
        await page.evaluate(READY_JS)
        deadline = time.monotonic() + READY_SECONDS
        while True:
            state = await page.evaluate("() => (window.__studioClock.flush(), window.__studioReady)")
            if state == "ready":
                break
            if str(state).startswith("failed"):
                raise MediaError(f"The page's ready promise was rejected ({str(state)[8:][:200]}).")
            if time.monotonic() > deadline:
                raise MediaError(f"The page was not ready after {READY_SECONDS:.0f} seconds: its fonts or its "
                                 "studioVideo.ready promise never settled. Timers do not run while a page loads "
                                 "for rendering; resolve `ready` from load events, not from a timeout.")
            await asyncio.sleep(0.05)
        return context, page, await page.evaluate(DECLARED_JS)
    except BaseException:
        await context.close()
        raise


async def _grabber(context, page, settings: dict):
    """→ a coroutine function returning the current picture as PNG bytes.

    Asking the browser directly with "optimise for speed" takes about 45 ms for a 1920×1080
    frame; the ordinary screenshot call compresses harder and takes about 200 ms. Both are
    lossless, and ffmpeg does the real compression."""
    clip = {"x": 0, "y": 0, "width": settings["width"], "height": settings["height"]}
    try:
        session = await context.new_cdp_session(page)
        await session.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
    except Exception:
        return lambda: page.screenshot(type="png", clip=clip)

    async def grab() -> bytes:
        shot = await session.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
        return base64.b64decode(shot["data"])
    return grab


async def render_video(looker, url: str, workspace: Path, page_path: str, sheet_dir: Path, *, out: str | None = None,
                       fmt: str = "mp4", width=None, height=None, fps=None, duration=None, start=0.0, scale=1,
                       audio: str | None = None, volume: float = 1.0, check: bool = False,
                       times: list | None = None, frames: int = 12, poster_at: float | None = None) -> dict:
    fmt = (fmt or "mp4").lower().lstrip(".")
    if fmt not in FORMATS:
        raise MediaError(f"format must be one of {', '.join(FORMATS)}.")
    ffmpeg = find_ffmpeg()
    if not check and not ffmpeg:
        raise MediaError(video_reason(looker) or "ffmpeg is not available.")
    target = None if check else _out_path(workspace, out, page_path, fmt)
    console: list[dict] = []
    failed: list[dict] = []
    started = time.monotonic()
    async with _one_render, looker.slot():
        context, page, declared = await _open(looker, url, 2 if str(scale) == "2" else 1, console, failed)
        try:
            settings = plan(declared, width=width, height=height, fps=fps, duration=duration, start=start, scale=scale)
            sound = None if check else _audio_path(workspace, audio or (declared or {}).get("audio"))
            await page.set_viewport_size({"width": settings["width"], "height": settings["height"]})
            wanted = sheet_frames(frames, times, settings)
            if poster_at is None:
                poster_at = (declared or {}).get("poster")
            poster_index = None if check else max(0, min(settings["frames"] - 1, int(round(
                ((float(poster_at) if poster_at is not None else settings["start"] + settings["duration"] * 0.4)
                 - settings["start"]) * settings["fps"]))))
            shots: list[tuple[float, bytes]] = []
            poster_bytes, encoder, master, scratch = None, None, None, None
            if not check:
                scratch = Path(tempfile.mkdtemp(prefix="studio2-video-"))
                master = scratch / "master.mp4"
                encoder = await asyncio.create_subprocess_exec(
                    *encode_args(ffmpeg, settings, master, sound, volume),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                errors = asyncio.ensure_future(encoder.stderr.read())
            try:
                grab = await _grabber(context, page, settings)
                for index in range(settings["frames"]):
                    at = settings["start"] + index / settings["fps"]
                    await page.evaluate("ms => window.__studioClock.step(ms)", at * 1000)
                    if check and index not in wanted:
                        continue
                    shot = await grab()
                    if index in wanted:
                        shots.append((at, shot))
                    if index == poster_index:
                        poster_bytes = shot
                    if encoder:
                        if encoder.returncode is not None:
                            raise MediaError("ffmpeg stopped early: " + " ".join((await errors).decode("utf-8", "replace").split())[-400:])
                        encoder.stdin.write(shot)
                        await encoder.stdin.drain()
                    if time.monotonic() - started > RENDER_SECONDS:
                        raise MediaError(f"The render passed {RENDER_SECONDS / 60:.0f} minutes and was stopped at "
                                         f"frame {index + 1} of {settings['frames']}. Make each frame cheaper or the video shorter.")
                result = {"page": page_path, "width": settings["width"] * settings["scale"],
                          "height": settings["height"] * settings["scale"], "fps": settings["fps"],
                          "duration": settings["duration"], "frames": settings["frames"],
                          "declared_by_page": bool(declared), "driven_by_seek": bool((declared or {}).get("seek"))}
                if encoder:
                    encoder.stdin.close()
                    await encoder.wait()
                    if encoder.returncode:
                        raise MediaError("ffmpeg could not encode the video: " + " ".join((await errors).decode("utf-8", "replace").split())[-400:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    partial = target.with_name(f"{target.stem}.partial{target.suffix}")
                    if fmt == "mp4":
                        shutil.move(str(master), partial)
                    else:
                        await _run(convert_args(ffmpeg, master, partial, fmt, settings), f"Converting to {fmt}")
                    os.replace(partial, target)
                    root = workspace.resolve()
                    result["file"] = target.relative_to(root).as_posix()
                    result["bytes"] = target.stat().st_size
                    result["sound"] = sound.relative_to(root).as_posix() if sound else None
                    if poster_bytes:
                        from PIL import Image
                        # `<name>.poster.jpg`, never `<name>.jpg`: that name may be a picture the project
                        # already has (an image export of the same design), which the poster would replace.
                        poster = target.with_name(f"{target.stem}.poster.jpg")
                        Image.open(io.BytesIO(poster_bytes)).convert("RGB").save(poster, "JPEG", quality=90)
                        result["poster"] = poster.relative_to(root).as_posix()
            finally:
                if encoder and encoder.returncode is None:
                    with contextlib.suppress(Exception):
                        encoder.kill()
                if scratch:
                    shutil.rmtree(scratch, ignore_errors=True)
        finally:
            await context.close()
    sheet = await asyncio.to_thread(contact_sheet, shots, sheet_dir / f"frames-{int(time.time() * 1000)}.jpg")
    for old in sorted(sheet_dir.glob("frames-*.jpg"))[:-8]:
        old.unlink(missing_ok=True)
    result.update({"sheet": sheet["image"], "sheet_times": [round(at, 2) for at, _ in shots],
                   "seconds_taken": round(time.monotonic() - started, 1),
                   "console": console[:LIST_CAP], "failed_requests": failed[:LIST_CAP]})
    if result.get("file"):
        name = Path(result["file"]).name
        if fmt == "gif":
            result["embed"] = f'<img src="{result["file"]}" alt="" style="max-width:100%;height:auto">'
        else:
            result["embed"] = (f'<video controls playsinline preload="metadata" poster="{result.get("poster", "")}" '
                               f'src="{result["file"]}" style="width:100%;height:auto;border-radius:10px"></video>\n'
                               f'<p><a href="{result["file"]}" download>Download {name}</a></p>')
    result["next"] = ("Open `sheet` with the Read tool: it shows the frames at `sheet_times`. "
                      + ("Nothing was encoded (check). Fix what is wrong, check again, then render without check."
                         if check else "Look for text that is cut off, empty frames and anything that pops in late "
                                       "before showing the file to the person."))
    return result


async def render_image(looker, url: str, workspace: Path, out: str, *, width: int = 1200, height: int | None = None,
                       scale: int = 1, selector: str | None = None, transparent: bool = False, dark: bool = False,
                       wait_ms: int = 600, at: float | None = None) -> dict:
    """One picture of a page (or of one element) saved into the project as a file to keep."""
    from . import workspace as ws
    try:
        target = ws.safe_path(workspace, out or "")
    except Exception:
        raise MediaError("out must be a path inside the project.") from None
    kind = target.suffix.lower()
    if kind not in IMAGE_TYPES:
        raise MediaError(f"out must end in one of {', '.join(IMAGE_TYPES)}.")
    scale = max(1, min(int(scale or 1), 3))
    width = max(16, min(int(width or 1200), SIDE_MAX))
    view_height = max(16, min(int(height or 900), SIDE_MAX))
    console: list[dict] = []
    await looker.browser()
    async with looker.slot():
        context = await looker.new_context(url, viewport={"width": width, "height": view_height},
                                           device_scale_factor=scale, color_scheme="dark" if dark else "light")
        try:
            if at is not None:
                await context.add_init_script(clock_script())
            page = await context.new_page()
            page.on("pageerror", lambda e: console.append({"level": "exception", "text": str(e)[:TEXT_CAP]}))
            await page.goto(url, wait_until="load", timeout=30_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=8_000)
            with contextlib.suppress(Exception):
                await page.evaluate("() => document.fonts.ready.then(() => true)")
            if at is not None:       # a still from an animated page: step its clock to that moment
                steps = max(1, int(float(at) * 30))
                for index in range(steps + 1):
                    await page.evaluate("ms => window.__studioClock.step(ms)", float(at) * 1000 * index / steps)
            else:
                await page.wait_for_timeout(max(0, min(int(wait_ms or 0), 10_000)))
            options = {"type": "png", "omit_background": bool(transparent) and kind in (".png", ".webp")}
            if selector:
                element = page.locator(str(selector)).first
                try:
                    shot = await element.screenshot(timeout=8_000, **options)
                except Exception:
                    raise MediaError(f"Nothing on the page matches {selector}.") from None
            elif height:
                shot = await page.screenshot(clip={"x": 0, "y": 0, "width": width, "height": view_height}, **options)
            else:
                shot = await page.screenshot(full_page=True, **options)
        finally:
            await context.close()
    from PIL import Image
    image = Image.open(io.BytesIO(shot))
    if image.width * image.height > PIXELS_MAX * 2:
        raise MediaError(f"The picture would be {image.width}×{image.height}, which is too large. Give a height or a selector.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == ".png":
        target.write_bytes(shot)
    elif kind == ".webp":
        image.save(target, "WEBP", quality=92)
    else:
        image.convert("RGB").save(target, "JPEG", quality=92)
    return {"file": target.relative_to(workspace.resolve()).as_posix(), "width": image.width, "height": image.height,
            "bytes": target.stat().st_size, "console": console[:LIST_CAP],
            "next": "Open the file with the Read tool to check it before using it."}
