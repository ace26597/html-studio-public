"""Whether the agent can look at pages (CONTRACT.md, "Look"): checked again after a "cannot"."""

import sys
from pathlib import Path

from studio2_server import look
from studio2_server.look import Looker


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_missing_browser_is_noticed_once_it_is_there(monkeypatch, tmp_path):
    clock, calls = Clock(), []
    monkeypatch.setattr(look.time, "monotonic", clock)
    browser: list[Path | None] = [None]

    def find():
        calls.append(1)
        return browser[0]

    monkeypatch.setattr(look, "find_browser", find)
    looker = Looker()
    said = looker.reason()
    assert said and "No Chromium" in said and "playwright install chromium" in said
    assert "PLAYWRIGHT_BROWSERS_PATH" in said
    browser[0] = tmp_path / "chrome"                      # someone installs one
    assert looker.reason() == said and len(calls) == 1    # not asked again within a few seconds
    clock.now += look.RECHECK + 0.1
    assert looker.reason() is None and len(calls) == 2    # asked again after them
    browser[0] = None
    clock.now += look.RECHECK * 10
    assert looker.reason() is None and len(calls) == 2    # a "can work" is remembered


def test_a_missing_package_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)   # makes the import fail
    said = Looker().reason()
    assert said and "playwright==1.62.0" in said and "uv pip install" in said and "[look]" in said
    assert "restart" in said


# -- readable pieces and what is measured (CONTRACT.md, "Look") -------------------------------------
import pytest  # noqa: E402

from studio2_server import layout, tools  # noqa: E402

PILImage = pytest.importorskip("PIL.Image")

needs_browser = pytest.mark.skipif(Looker().reason() is not None, reason="no cached Chromium")


def test_a_tall_page_is_cut_into_pieces_that_cover_it():
    assert layout.tiles(900, 1300) == [(0, 900)]
    assert layout.tiles(1500, 1300) == [(0, 1500)]                 # a little over one piece is still one
    cuts = layout.tiles(6000, 1100)
    assert len(cuts) == 6 and cuts[0] == (0, 1100) and cuts[-1] == (4900, 1100)
    for (top, tall), (below, _t) in zip(cuts, cuts[1:]):
        assert top < below <= top + tall - 60                      # each overlaps the next, with no gap
    for height in (1626, 2291, 3050, 5999, 6000):
        for piece in (1100, 1300):
            cuts = layout.tiles(height, piece)
            assert cuts[0][0] == 0 and cuts[-1][0] + cuts[-1][1] == height
            assert all(tall <= piece * 1.25 for _top, tall in cuts)


def test_old_looks_go_with_their_pieces(tmp_path):
    for stamp in range(1000, 1000 + look.KEEP + 3):
        (tmp_path / f"look-{stamp}.jpg").write_bytes(b"x")
        (tmp_path / f"look-{stamp}-1.jpg").write_bytes(b"x")
        (tmp_path / f"look-{stamp}-2.jpg").write_bytes(b"x")
    (tmp_path / "frame-1.jpg").write_bytes(b"x")                   # another tool's file is left alone
    Looker._prune(tmp_path)
    left = sorted(p.name for p in tmp_path.glob("look-*"))
    assert len(left) == look.KEEP * 3 and left[0] == "look-1003-1.jpg" and (tmp_path / "frame-1.jpg").exists()


def test_the_agent_is_told_which_pictures_to_open():
    plain = tools.look_next({"tiles": [], "layout": {"findings": []}})
    assert "`image`" in plain and "tiles" not in plain
    said = tools.look_next({"tiles": [{}, {}, {}], "cut_off": True, "captured_from": 0, "captured_height": 6000,
                            "page_height": 20975,
                            "layout": {"findings": [{"count": 2}, {"count": 1}]}})
    assert "3 pictures in `tiles`" in said and "top: 6000" in said and "3 possible problems" in said


BROKEN = """<!doctype html><html><head><title>Broken on purpose</title><style>
body{margin:0;font-family:Arial;color:#222}
.card{background:#eef;width:200px;height:40px;overflow:hidden;white-space:nowrap;margin:20px}
.clamp{width:200px;margin:20px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.wide{width:900px;background:#fee;margin-top:20px}
.faint{color:#bbb;background:#fff;padding:20px}
.tiny{font-size:9px;padding:20px}
.over{position:relative;height:60px;margin:20px}
.over span{position:absolute;left:0;top:10px;font-size:20px}
.over b{position:absolute;left:30px;top:14px;font-size:20px}
.box{background:#dfd;border:1px solid #9c9;margin:20px;width:300px}
a.btn{display:block;width:20px;height:18px;margin:20px;background:#ccc}
</style></head><body>
<h1>Edge heading touching the left side</h1>
<div class="card">This sentence is far too long for the little card it lives in</div>
<div class="clamp">An ellipsis is used here on purpose for a long file name.txt</div>
<div class="wide">A block that is wider than a phone screen</div>
<div class="faint">Faint grey text on a white background</div>
<div class="tiny">Tiny text nobody can read on a phone</div>
<div class="over"><span>First label here</span><b>Second label there</b></div>
<div class="box">No padding inside this framed box at all</div>
<a class="btn" href="#x">x</a>
<img src="missing.png" alt="missing picture" width="100" height="50">
</body></html>"""

SOUND = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sound</title><style>
body{margin:0;font:16px/1.5 Arial;color:#1a1a1a;background:#fff}
main{padding:24px;max-width:640px;margin:0 auto}
.card{background:#f3f4f6;border:1px solid #d1d5db;border-radius:10px;padding:16px;margin:16px 0}
button{font:inherit;padding:12px 18px;min-height:44px}
sup a{font-size:10px}
@media (prefers-color-scheme: dark){body{background:#111;color:#eee}.card{background:#1f2937;border-color:#374151}}
</style></head><body><main>
<h1>A page with nothing wrong</h1>
<p>A paragraph that wraps over several lines on a phone, with a <a href="#s">link in the sentence</a>
and a reference mark<sup><a href="#s1">[1]</a></sup>, none of which is a problem to report.</p>
<div class="card"><strong>Card title</strong><p>Text inside a card that has padding.</p></div>
<button>Do it</button>
""" + "<p>More text to make the page tall enough to need several pieces. </p>" * 60 + "</main></body></html>"


@needs_browser
async def test_a_look_measures_what_is_wrong_and_cuts_a_tall_page(tmp_path):
    (tmp_path / "broken.html").write_text(BROKEN)
    (tmp_path / "sound.html").write_text(SOUND)
    looker = Looker()
    try:
        seen = await looker.look((tmp_path / "broken.html").as_uri(), tmp_path / "looks", width=390)
        found = {f["kind"]: f for f in seen["layout"]["findings"]}
        assert set(found) == set(layout.KINDS) - {"long_lines"}, sorted(found)
        assert list(found) == [kind for kind in layout.KINDS if kind in found]        # the worst first
        assert seen["layout"]["viewport_meta"] is False and seen["layout"]["dark_styles"] is False
        assert found["wider_than_screen"]["examples"][0]["where"] == "div.wide"
        clipped = {e["where"]: e["detail"] for e in found["clipped_text"]["examples"]}
        assert "cut on the right" in clipped["div.card"] and "may be meant" not in clipped["div.card"]
        assert "may be meant" in clipped["div.clamp"]
        assert "Second label there" in found["overlapping_text"]["examples"][0]["detail"]
        assert found["low_contrast"]["examples"][0]["detail"].startswith("1.92 to 1 (#bbbbbb on #ffffff")
        assert found["broken_image"]["examples"][0]["detail"] == "missing.png"
        assert {e["where"] for e in found["tight_padding"]["examples"]} == {"div.card", "div.box"}
        assert all(f["means"] == layout.KINDS[f["kind"]] and f["count"] >= len(f["examples"]) >= 0
                   for f in seen["layout"]["findings"])
        assert len(seen["tiles"]) == 1                              # a phone look is always given at full sharpness
        with PILImage.open(seen["tiles"][0]["image"]) as piece, PILImage.open(seen["image"]) as whole:
            assert piece.width == 780 and whole.width == 390        # twice the pixels in the piece, not in the whole

        seen = await looker.look((tmp_path / "sound.html").as_uri(), tmp_path / "looks", width=390)
        assert seen["layout"]["findings"] == [], seen["layout"]["findings"]
        assert seen["layout"]["viewport_meta"] is True and seen["layout"]["dark_styles"] is True
        assert len(seen["tiles"]) > 1 and seen["tiles"][0]["top"] == 0
        last = seen["tiles"][-1]
        assert last["top"] + last["height"] == seen["captured_height"] == seen["page_height"]
        lower = await looker.look((tmp_path / "sound.html").as_uri(), tmp_path / "looks", width=390, top=1500)
        assert lower["captured_from"] == 1500 and lower["tiles"][0]["top"] == 1500
        assert lower["captured_height"] == seen["page_height"] - 1500 and lower["cut_off"] is False

        wide = await looker.look((tmp_path / "sound.html").as_uri(), tmp_path / "looks", width=1440)
        assert wide["layout"]["findings"] == [] and all(t["height"] == 1300 for t in wide["tiles"])
        with PILImage.open(wide["tiles"][0]["image"]) as piece:
            assert piece.size == (1440, 1300)                       # a desktop's pieces are one pixel to one
        short = await looker.look((tmp_path / "broken.html").as_uri(), tmp_path / "looks", width=1440)
        assert short["tiles"] == []                                 # fits one readable picture: `image` is enough
        bare = await looker.look((tmp_path / "sound.html").as_uri(), tmp_path / "looks", width=390,
                                 tiles=False, layout=False)
        assert "tiles" not in bare and "layout" not in bare
    finally:
        await looker.close()
