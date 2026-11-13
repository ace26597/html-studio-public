"""An MP4 → a GIF small enough for a README. Usage: gif.py in.mp4 out.gif [--width 760] [--speed 1.6] [--fps 8] [--colors 96]"""
import subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from studio2_server.media import find_ffmpeg  # noqa: E402


def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


src, out = sys.argv[1], sys.argv[2]
chain = (f"setpts=PTS/{opt('--speed', '1.6')},fps={opt('--fps', '8')},scale={opt('--width', '760')}:-1:flags=lanczos,split[a][b];"
         f"[a]palettegen=max_colors={opt('--colors', '96')}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-i", src, "-vf", chain, "-loop", "0", out], check=True)
print(out, round(Path(out).stat().st_size / 1e6, 2), "MB")
