"""A contact sheet of frames from a video, to check it by eye. Usage: sheet.py video.mp4 out.jpg t1 t2 …"""
import subprocess, sys, tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from studio2_server.media import find_ffmpeg  # noqa: E402

video, out, times = sys.argv[1], sys.argv[2], sys.argv[3:]
shots = []
with tempfile.TemporaryDirectory() as tmp:
    for t in times:
        path = f"{tmp}/{t}.jpg"
        subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-ss", t, "-i", video, "-frames:v", "1", "-vf", "scale=960:-1", path], check=True)
        shots.append(Image.open(path).copy())
w, h = shots[0].size
sheet = Image.new("RGB", (w * 2, h * ((len(shots) + 1) // 2)))
for i, shot in enumerate(shots):
    sheet.paste(shot, ((i % 2) * w, (i // 2) * h))
sheet.save(out, quality=78)
print(out, sheet.size)
