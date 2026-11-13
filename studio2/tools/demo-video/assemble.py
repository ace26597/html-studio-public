"""Frames + a plan → an MP4 with a caption strip. Usage: assemble.py plan.json out.mp4"""
import io, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from studio2_server.media import find_ffmpeg  # noqa: E402

plan = json.loads(Path(sys.argv[1]).read_text())
OUT, FPS, STRIP = sys.argv[2], 30, 84
WIDTH, HEIGHT = 1920, 1200
BG, FG, DIM, ACCENT = (15, 23, 42), (248, 250, 252), (148, 163, 184), (129, 140, 248)

def font(size, bold=False):
    for path, index in (("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0), ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0)):
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()
F_CAP, F_SMALL, F_BIG, F_MID = font(34, True), font(26), font(76, True), font(36)

def clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"

def strip(canvas, caption, right):
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, HEIGHT, WIDTH, HEIGHT + STRIP], fill=BG)
    draw.text((36, HEIGHT + STRIP // 2), caption, font=F_CAP, fill=FG, anchor="lm")
    if right:
        draw.text((WIDTH - 36, HEIGHT + STRIP // 2), right, font=F_SMALL, fill=ACCENT, anchor="rm")

def card(lines):
    canvas = Image.new("RGB", (WIDTH, HEIGHT + STRIP), BG)
    draw = ImageDraw.Draw(canvas)
    y = (HEIGHT + STRIP) // 2 - 60 * len(lines) // 2 - 40
    for i, text in enumerate(lines):
        draw.text((WIDTH // 2, y), text, font=F_BIG if i == 0 else F_MID, fill=FG if i == 0 else DIM, anchor="mm")
        y += 110 if i == 0 else 58
    return canvas

# Build the output timeline: (seconds of output, image source)
timeline = []
t0 = {}                                          # first real time of each clock group, across items
for item in plan["items"]:
    if "card" in item:
        timeline.append((item["seconds"], ("card", tuple(item["card"]))))
        continue
    folder = Path(item["dir"])
    frames = json.loads((folder / "frames.json").read_text())
    rules = item["phases"]                       # phase → {speed, caption, right?, hold?, skip?, after?, until?}
    still_for, last_thumb = 0.0, None
    began = {}                                   # first real time of each phase in this item
    for frame in frames:
        began.setdefault(frame["phase"], frame["t"])
    for i, frame in enumerate(frames):
        rule = rules.get(frame["phase"])
        if not rule or rule.get("skip"):
            continue
        into = frame["t"] - began[frame["phase"]]    # `after` / `until`: keep only part of a phase (real seconds)
        if into < rule.get("after", 0.0) or into > rule.get("until", float("inf")):
            continue
        real = (frames[i + 1]["t"] - frame["t"]) if i + 1 < len(frames) else rule.get("hold", 1.5)
        real = min(real, rule.get("max_gap", 6.0))
        group = rule.get("clock")
        right = rule.get("right", "")
        if group:
            t0.setdefault(group, frame["t"])
            right = f"{clock(frame['t'] - t0[group])} elapsed  ·  {right}"
        seconds = real / rule["speed"]
        if rule.get("still_cap") is not None:
            # A stretch where nothing on screen changes (the agent waiting on a slow source) is cut
            # down to `still_cap` seconds of output; the elapsed clock keeps the real time.
            thumb = Image.open(folder / frame["f"]).convert("L").resize((48, 30)).tobytes()
            same = last_thumb is not None and sum(abs(a - b) > 24 for a, b in zip(thumb, last_thumb)) < 6
            last_thumb = thumb
            if same:
                if still_for >= rule["still_cap"]:
                    continue
                still_for += seconds
            else:
                still_for = 0.0
        timeline.append((seconds, ("frame", str(folder / frame["f"]), rule["caption"], right)))

total = sum(seconds for seconds, _ in timeline)
print(f"output length {total:.1f}s, {len(timeline)} source images")
ffmpeg = find_ffmpeg()
proc = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS), "-i", "-",
                         "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
                         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                         "-c:v", "libx264", "-preset", "slow", "-crf", str(plan.get("crf", 21)),
                         "-movflags", "+faststart", OUT], stdin=subprocess.PIPE)
cache_key, cache_bytes, at, tick = None, b"", 0.0, 0
for seconds, source in timeline:
    end = at + seconds
    while tick / FPS < end:
        if source != cache_key:
            if source[0] == "card":
                canvas = card(source[1])
            else:
                canvas = Image.new("RGB", (WIDTH, HEIGHT + STRIP), BG)
                shot = Image.open(source[1]).convert("RGB")
                if shot.size != (WIDTH, HEIGHT):
                    shot = shot.resize((WIDTH, HEIGHT), Image.LANCZOS)
                canvas.paste(shot, (0, 0))
                strip(canvas, source[2], source[3])
            buffer = io.BytesIO(); canvas.save(buffer, "JPEG", quality=92)
            cache_key, cache_bytes = source, buffer.getvalue()
        proc.stdin.write(cache_bytes)
        tick += 1
    at = end
proc.stdin.close()
print("ffmpeg exit", proc.wait(), "frames written", tick)
