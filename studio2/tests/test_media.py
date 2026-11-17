"""Video and pictures made from a page (CONTRACT.md, "Video and images from a page")."""

import io
import json

import httpx
import pytest

from studio2_server import media, tools
from studio2_server.look import Looker
from studio2_server.preview import PreviewApp, byte_range
from studio2_server.risk import risky_command

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

_looker = Looker()
needs_browser = pytest.mark.skipif(_looker.reason() is not None, reason="no cached Chromium")
needs_video = pytest.mark.skipif(media.video_reason(_looker) is not None, reason="no cached Chromium or no ffmpeg")

# Four kinds of motion that must agree at every frame: a CSS animation, a Web Animation, a
# requestAnimationFrame loop reading performance.now, and a transition started by a timer.
MOVING = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#000;overflow:hidden} div{position:absolute;left:0;width:20px;height:20px}
#css{top:0;background:#f00;animation:go 2s linear both} #waapi{top:30px;background:#0f0}
#raf{top:60px;background:#00f} #late{top:90px;background:#ff0;transition:transform 1s linear}
#late.on{transform:translateX(300px)} @keyframes go{to{transform:translateX(300px)}}</style>
<div id="css"></div><div id="waapi"></div><div id="raf"></div><div id="late"></div><script>
document.getElementById('waapi').animate([{transform:'translateX(0)'},{transform:'translateX(300px)'}],{duration:2000,fill:'both'});
const t0 = performance.now();
(function loop(){ document.getElementById('raf').style.transform = 'translateX(' + Math.min(300, (performance.now()-t0) * .15) + 'px)'; requestAnimationFrame(loop); })();
setTimeout(() => document.getElementById('late').classList.add('on'), 1000);
window.studioVideo = {width: 320, height: 120, fps: 10, duration: 2};
</script>"""


def left_edge(image, row: int, colour: tuple[int, int, int]) -> int | None:
    pixels = image.convert("RGB")
    for x in range(pixels.width):
        r, g, b = pixels.getpixel((x, row))
        if abs(r - colour[0]) < 60 and abs(g - colour[1]) < 60 and abs(b - colour[2]) < 60:
            return x
    return None


def test_settings_come_from_the_page_and_arguments_override_them():
    declared = {"width": 1080, "height": 1920, "fps": 24, "duration": 8}
    assert media.plan(declared) == {"width": 1080, "height": 1920, "fps": 24, "duration": 8, "start": 0.0,
                                    "scale": 1, "frames": 192}
    chosen = media.plan(declared, width=641, duration=2, fps=30, scale=2)
    assert (chosen["width"], chosen["height"], chosen["frames"], chosen["scale"]) == (640, 1920, 60, 2)
    assert media.plan(None, duration=1)["width"] == 1920        # the default picture


@pytest.mark.parametrize("declared, given, words", [
    (None, {}, "How long"),
    ({"duration": 500}, {}, "between 0.1 and 180"),
    ({"duration": 5}, {"fps": 240}, "fps"),
    ({"duration": 5, "width": 3840, "height": 2160}, {"scale": 2}, "too large"),
])
def test_settings_that_cannot_be_rendered_are_refused_with_the_reason(declared, given, words):
    with pytest.raises(media.MediaError, match=words):
        media.plan(declared, **given)


def test_the_contact_sheet_takes_evenly_spaced_frames_or_the_moments_asked_for():
    settings = media.plan(None, duration=10, fps=30)
    spread = media.sheet_frames(4, None, settings)
    assert list(spread) == [37, 112, 187, 262] and round(spread[37], 2) == 1.23
    assert list(media.sheet_frames(12, [0, 3.2, 99], settings)) == [0, 96, 299]


def test_a_sheet_of_frames_is_one_labelled_image(tmp_path):
    def frame(colour):
        data = io.BytesIO()
        Image.new("RGB", (640, 360), colour).save(data, "PNG")
        return data.getvalue()
    sheet = media.contact_sheet([(0.5, frame("red")), (1.5, frame("blue")), (2.5, frame("green"))], tmp_path / "s.jpg")
    assert sheet["frames"] == 3 and sheet["columns"] == 2 and sheet["frame_width"] == 640
    assert Image.open(tmp_path / "s.jpg").width > 1280


def test_byte_ranges():
    assert byte_range("bytes=0-99", 1000) == (0, 99)
    assert byte_range("bytes=900-", 1000) == (900, 999)
    assert byte_range("bytes=-100", 1000) == (900, 999)
    assert byte_range("bytes=0-5000", 1000) == (0, 999)
    assert byte_range("bytes=1000-", 1000) == "unsatisfiable"
    assert byte_range("", 1000) is None and byte_range("bytes=0-1,5-9", 1000) is None


async def test_project_files_are_served_in_ranges_so_a_video_can_seek(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "clip.mp4").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "index.html").write_text("<p>hi</p>")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=PreviewApp(tmp_path)), base_url="http://p") as http:
        whole = await http.get("/media/clip.mp4")
        assert whole.status_code == 200 and whole.headers["accept-ranges"] == "bytes"
        assert whole.headers["content-type"] == "video/mp4" and len(whole.content) == 1024
        part = await http.get("/media/clip.mp4", headers={"range": "bytes=256-259"})
        assert part.status_code == 206 and part.content == bytes([0, 1, 2, 3])
        assert part.headers["content-range"] == "bytes 256-259/1024" and part.headers["content-length"] == "4"
        tail = await http.get("/__studio/files/media/clip.mp4", headers={"range": "bytes=-2"})
        assert tail.status_code == 206 and tail.content == bytes([254, 255])
        assert (await http.get("/media/clip.mp4", headers={"range": "bytes=5000-"})).status_code == 416
        page = await http.get("/", headers={"range": "bytes=0-3"})
        assert page.status_code == 200 and b"bridge.js" in page.content      # pages are never cut


def test_copying_a_helper_out_of_the_skill_folder_does_not_ask(tmp_path):
    assert risky_command("mkdir -p video && cp ../library/skills/motion-video/stage.js video/stage.js", tmp_path) is None
    assert risky_command("cp video/stage.js ../library/skills/motion-video/stage.js", tmp_path)
    assert risky_command("cp -t ../../elsewhere a.txt b.txt", tmp_path)
    assert risky_command("cp a.txt ../../other/", tmp_path)


def test_the_tools_are_listed_with_why_they_are_unavailable(studio):
    listed = {tool["name"]: tool for tool in tools.describe(studio)}
    assert {"render_video", "render_image"} <= set(listed)
    assert listed["render_video"]["available"] == (media.video_reason(studio.looker) is None)


async def test_a_page_outside_the_project_or_missing_is_an_error_not_a_crash(studio, project):
    for path in ("../../etc/passwd", "video/none.html"):
        result = await tools.call(studio, project["id"], "render_video", {"path": path, "check": True})
        assert result.get("is_error") and "project" in result["content"][0]["text"]


@needs_browser
async def test_every_kind_of_motion_follows_the_stepped_clock(tmp_path):
    (tmp_path / "moving.html").write_text(MOVING)
    looker = Looker()
    try:
        shot = await media.render_image(looker, (tmp_path / "moving.html").as_uri(), tmp_path, "still.png",
                                        width=320, height=120, at=1.5)
    finally:
        await looker.close()
    image = Image.open(tmp_path / shot["file"])
    assert (image.width, image.height) == (320, 120)
    # 1.5 s into a 2 s move across 300 px is 225 px; the transition began at 1 s, so it is half-way (150 px).
    for row, colour, expected in ((10, (255, 0, 0), 225), (40, (0, 255, 0), 225), (70, (0, 0, 255), 225),
                                  (100, (255, 255, 0), 150)):
        assert abs(left_edge(image, row, colour) - expected) <= 12, (row, left_edge(image, row, colour))


@needs_video
async def test_a_page_becomes_a_video_file_with_a_poster_and_a_sheet(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "video").mkdir(parents=True)
    (workspace / "video" / "moving.html").write_text(MOVING)
    looker = Looker()
    try:
        url = (workspace / "video" / "moving.html").as_uri()
        looked = await media.render_video(looker, url, workspace, "video/moving.html", tmp_path / "looks",
                                          check=True, times=[0.5, 1.5])
        assert "file" not in looked and looked["sheet_times"] == [0.5, 1.5] and not (workspace / "media").exists()
        (workspace / "media").mkdir()
        Image.new("RGB", (7, 5), "white").save(workspace / "media" / "moving.jpg")     # a picture the project owns
        made = await media.render_video(looker, url, workspace, "video/moving.html", tmp_path / "looks", frames=4)
        gif = await media.render_video(looker, url, workspace, "video/moving.html", tmp_path / "looks", fmt="gif",
                                       duration=1)
    finally:
        await looker.close()
    assert made["file"] == "media/moving.mp4" and made["frames"] == 20 and made["declared_by_page"]
    data = (workspace / made["file"]).read_bytes()
    assert data[4:8] == b"ftyp" and made["bytes"] == len(data)
    assert made["poster"] == "media/moving.poster.jpg" and Image.open(workspace / made["poster"]).size == (320, 120)
    assert Image.open(workspace / "media" / "moving.jpg").size == (7, 5)        # never overwritten by the poster
    assert 'poster="media/moving.poster.jpg"' in made["embed"]
    assert Image.open(made["sheet"]).width > 320 and "<video" in made["embed"]
    assert (workspace / gif["file"]).read_bytes()[:6] in (b"GIF89a", b"GIF87a") and gif["embed"].startswith("<img")
    json.dumps(made)        # the tool's answer is plain data
