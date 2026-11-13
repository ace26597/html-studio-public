"""Draw on the page (CONTRACT.md, "Draw on the page")."""

import json

import pytest

from studio2_server import drawing
from studio2_server.turns import compose_prompt

MARKS = [
    {"kind": "stroke", "shape": "loop", "color": "#ef4444", "points": [[100, 2300], [180, 2380], [102, 2302]],
     "rel": [[0.1, 0.1], [0.5, 0.6], [0.11, 0.11]],
     "anchor": {"selector": "#revenue > figure", "tag": "figure", "text": "Revenue by quarter", "rect": [16, 2270, 358, 260]},
     "section": "Revenue", "covers": [{"selector": "#revenue canvas", "tag": "canvas", "text": ""}]},
    {"kind": "note", "text": "make this a bar chart", "color": "#3b82f6", "points": [[140, 2450]],
     "rel": [[0.3, 0.7]], "anchor": {"selector": "#revenue", "tag": "section", "text": "Revenue"}, "section": "Revenue"},
    {"kind": "stroke", "shape": "line", "color": "#ef4444", "points": [[20, 4000], [300, 4010]], "rel": [[0, 0], [1, 1]],
     "anchor": {"selector": "body", "tag": "body", "text": ""},
     "from": {"selector": "#a", "tag": "div", "text": "Costs"}, "to": {"selector": "#b", "tag": "div", "text": "Margin"}},
]
DRAWING = {"path": "/", "viewport": {"width": 390, "height": 760}, "page": {"width": 390, "height": 5120}, "marks": MARKS}


def test_clean_numbers_marks_and_drops_rubbish():
    drawn = drawing.clean({**DRAWING, "path": "//evil.example/x", "marks": [
        *MARKS, {"kind": "note", "text": "  ", "points": [[1, 1]]}, {"kind": "stroke", "points": []}, "junk",
        {"kind": "stroke", "color": "url(javascript:1)", "points": [[1, 2]], "rel": [[0, 0], [1, 1]]}]})
    assert [m["n"] for m in drawn["marks"]] == [1, 2, 3, 4]
    assert drawn["path"] == "/"
    assert drawn["marks"][0]["box"] == [100, 2300, 80, 80]
    assert drawn["marks"][3]["color"] == "#ef4444" and drawn["marks"][3]["rel"] == []
    with pytest.raises(drawing.DrawingError):
        drawing.clean({"marks": []})


def test_marks_that_are_close_share_one_screenshot():
    clips = drawing.regions([{"n": 1, "box": [100, 2300, 80, 80]}, {"n": 2, "box": [140, 2450, 220, 60]},
                             {"n": 3, "box": [20, 4000, 280, 10]}], 390, 5120)
    assert [c["marks"] for c in clips] == [[1, 2], [3]]
    assert clips[0]["clip"]["y"] == 2300 - drawing.REGION_PAD and clips[0]["clip"]["width"] == 390
    assert clips[1]["clip"]["y"] + clips[1]["clip"]["height"] <= 5120


def test_the_agent_is_told_where_each_mark_sits(tmp_path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "index.html").write_text("<h1>x</h1>")
    drawn = drawing.clean(DRAWING)
    drawn["images"] = [{"path": "attachments/drawing-17-1.jpg", "marks": [1, 2]}]
    (tmp_path / "attachments/drawing-17.json").write_text(json.dumps(drawn))
    (tmp_path / "attachments/photo.png").write_bytes(b"x")
    prompt = compose_prompt("Fix these", None, [], tmp_path,
                            ["attachments/drawing-17.json", "attachments/drawing-17-1.jpg", "attachments/photo.png"])
    assert "<studio-drawing>" in prompt and "seen 390 px wide" in prompt and "file: index.html" in prompt
    assert "1. Circle drawn around <figure> `#revenue > figure`" in prompt and "“Revenue”" in prompt
    assert "   - covers <canvas> `#revenue canvas`" in prompt
    assert "2. Note (blue): “make this a bar chart”" in prompt
    assert "3. Line (red) drawn from <div> `#a` “Costs” to <div> `#b` “Margin”" in prompt
    assert "attachments/drawing-17-1.jpg (marks 1, 2)" in prompt
    listing = prompt.split("<studio-attachments>")[1]
    assert "photo.png" in listing and "drawing-17" not in listing


def test_a_json_file_that_is_not_a_drawing_stays_a_plain_attachment(tmp_path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments/drawing-1.json").write_text("{}")
    prompt = compose_prompt("x", None, [], tmp_path, ["attachments/drawing-1.json"])
    assert "<studio-drawing>" not in prompt and "attachments/drawing-1.json" in prompt


async def test_a_drawing_is_saved_photographed_and_sent_with_a_turn(client, studio, project, monkeypatch):
    from conftest import run_turn
    pid = project["id"]
    seen = {}

    async def shots(url, drawn, directory, stem):
        seen["url"] = url
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}-1.jpg").write_bytes(b"jpg")
        return [{"file": directory / f"{stem}-1.jpg", "marks": [1, 2, 3]}]

    monkeypatch.setattr(studio.looker, "drawing_shots", shots)
    saved = await client.post(f"/api/projects/{pid}/drawings", json=DRAWING)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["marks"] == 3 and body["rendered"] is True and len(body["images"]) == 1
    assert seen["url"].startswith("http://127.0.0.1:")      # never the caller's host: that meets the access guard
    events = await run_turn(client, studio, pid, "Change what I marked", attachments=[body["path"]])
    assert [e for e in events if e["type"] == "turn_started"][0]["attachments"] == [body["path"]]

    empty = await client.post(f"/api/projects/{pid}/drawings", json={"marks": []})
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "invalid_drawing"


async def test_without_a_browser_the_drawing_is_still_saved(client, studio, project, monkeypatch):
    from studio2_server.look import LookUnavailable

    async def shots(*_a):
        raise LookUnavailable("No Chromium is cached on this machine.")

    monkeypatch.setattr(studio.looker, "drawing_shots", shots)
    body = (await client.post(f"/api/projects/{project['id']}/drawings", json=DRAWING)).json()
    assert body["rendered"] is False and body["images"] == [] and "Chromium" in body["note"]
    assert (studio.store.workspace(project["id"]) / body["path"]).is_file()


def test_boxes_and_moves_read_as_a_layout_sketch(tmp_path):
    sketch = drawing.clean({"path": "/", "viewport": {"width": 1200, "height": 800}, "page": {"width": 1200, "height": 2000}, "marks": [
        {"kind": "box", "text": "sidebar", "color": "#3b82f6", "points": [[0, 80], [288, 780]], "rel": [],
         "anchor": {"selector": "body", "tag": "body", "text": ""}},
        {"kind": "move", "color": "#ef4444", "points": [[900, 400], [300, 150]], "rel": [[0.5, 0.5], [-1.0, -0.75]],
         "anchor": {"selector": "#kpis", "tag": "section", "text": "Revenue 12"}, "ghost": [100, 60, 400, 180],
         "to": {"selector": "header h1", "tag": "h1", "text": "Sales", "side": "below"}},
        {"kind": "box", "points": [[1, 1]]}, {"kind": "move", "points": [[1, 1], [2, 2], [3, 3]]}]})
    assert [m["kind"] for m in sketch["marks"]] == ["box", "move"]
    text = drawing.describe(sketch, "index.html", "attachments/drawing-1.json")
    assert "1. Box (blue) labelled “sidebar”: from 0% to 24% of the page width, y 80 to 780 px (288 × 700 px)" in text
    assert "2. Move <section> `#kpis` “Revenue 12”: dragged 600 px left, 250 px up and dropped below <h1> `header h1` “Sales”" in text
    assert "Its new place: from 8% to 42% of the page width" in text
    assert "layout sketch" in text
    assert "layout sketch" not in drawing.describe(drawing.clean(DRAWING), "index.html", "x")
