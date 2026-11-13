"""Drawings the person makes on the live page (CONTRACT.md, "Draw on the page").

The bridge sends the marks, each tied to a page element. Here they are cleaned, redrawn on a
fresh render of the page for the screenshots, and turned into words for the agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_MARKS, MAX_POINTS, MAX_REGIONS = 60, 600, 5
REGION_GAP, REGION_PAD, REGION_MAX_HEIGHT = 160, 110, 1800
DRAWING_FILE = re.compile(r"^attachments/drawing-\d+\.json$")
COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")
COLOR_NAMES = {"#ef4444": "red", "#f59e0b": "amber", "#22c55e": "green", "#3b82f6": "blue",
               "#a855f7": "purple", "#111827": "black", "#ffffff": "white"}


SIDES = {"above": "above", "below": "below", "left": "to the left of", "right": "to the right of", "inside": "inside"}


class DrawingError(Exception):
    pass


def _num(value, low: float, high: float, default: float = 0.0) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return default


def _pairs(raw, low: float, high: float, digits: int) -> list[list[float]]:
    out = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_POINTS]:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append([round(_num(item[0], low, high), digits), round(_num(item[1], low, high), digits)])
    return out


def _target(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {"selector": str(raw.get("selector") or "")[:400], "tag": str(raw.get("tag") or "")[:40],
           "text": " ".join(str(raw.get("text") or "").split())[:200]}
    if raw.get("side") in SIDES:
        out["side"] = raw["side"]
    rect = raw.get("rect")
    if isinstance(rect, list) and len(rect) == 4:
        out["rect"] = [round(_num(v, -1e6, 1e6)) for v in rect]
    return out


def clean(data: dict) -> dict:
    """Whatever a browser sent → a Drawing that is safe to store and to replay."""
    marks = []
    for raw in (data.get("marks") if isinstance(data.get("marks"), list) else [])[:MAX_MARKS]:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind") if raw.get("kind") in ("note", "box", "move") else "stroke"
        points = _pairs(raw.get("points"), -1e6, 1e6, 0)
        text = str(raw.get("text") or "").strip()[:500]
        if not points or (kind == "note" and not text) or (kind in ("box", "move") and len(points) != 2):
            continue
        ghost = raw.get("ghost") if kind == "move" else None
        rel = _pairs(raw.get("rel"), -50, 50, 4)
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        color = str(raw.get("color") or "")
        marks.append({
            "n": len(marks) + 1, "kind": kind,
            "shape": raw.get("shape") if raw.get("shape") in ("dot", "line", "loop", "scribble") and kind == "stroke" else None,
            "color": color if COLOR.match(color) else "#ef4444", "text": text if kind in ("note", "box") else None,
            "ghost": [round(_num(v, -1e6, 1e6)) for v in ghost] if isinstance(ghost, list) and len(ghost) == 4 else None,
            "points": points, "box": [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)],
            "rel": rel if len(rel) == len(points) else [],
            "anchor": _target(raw.get("anchor")), "section": " ".join(str(raw.get("section") or "").split())[:120] or None,
            "from": _target(raw.get("from")), "to": _target(raw.get("to")),
            "covers": [t for t in map(_target, (raw.get("covers") if isinstance(raw.get("covers"), list) else [])[:6]) if t]})
    if not marks:
        raise DrawingError("The drawing has no marks.")
    path = str(data.get("path") or "/")
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        path = "/"
    viewport = data.get("viewport") if isinstance(data.get("viewport"), dict) else {}
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    return {"path": path[:600],
            "viewport": {"width": round(_num(viewport.get("width"), 280, 2560, 1280)),
                         "height": round(_num(viewport.get("height"), 300, 2000, 800))},
            "dark": bool(data.get("dark")),
            "page": {"width": round(_num(page.get("width"), 0, 1e6)), "height": round(_num(page.get("height"), 0, 1e6))},
            "marks": marks}


def regions(boxes: list[dict], page_width: int, page_height: int) -> list[dict]:
    """Marks that sit close together share a screenshot. boxes: [{n, box: [x, y, w, h]}]."""
    out: list[dict] = []
    for item in sorted(boxes, key=lambda b: b["box"][1]):
        top, bottom = item["box"][1], item["box"][1] + item["box"][3]
        if out and top - out[-1]["bottom"] < REGION_GAP:
            out[-1]["bottom"] = max(out[-1]["bottom"], bottom)
            out[-1]["marks"].append(item["n"])
        else:
            out.append({"top": top, "bottom": bottom, "marks": [item["n"]]})
    clips = []
    for region in out[:MAX_REGIONS]:
        y = max(0, region["top"] - REGION_PAD)
        height = min(region["bottom"] + REGION_PAD, max(page_height, 1)) - y
        clips.append({"marks": sorted(region["marks"]),
                      "clip": {"x": 0, "y": y, "width": max(page_width, 1), "height": max(60, min(height, REGION_MAX_HEIGHT))}})
    return clips


# Runs in the headless page: redraws the marks on their anchors and reports where they landed.
REPLAY_JS = r"""
(drawing) => {
  const SVG = "http://www.w3.org/2000/svg";
  const root = document.documentElement;
  const found = drawing.marks.map((mark) => {
    let el = null;
    try { el = mark.anchor && mark.anchor.selector ? document.querySelector(mark.anchor.selector) : null; } catch (e) {}
    return el;
  });
  const first = found.find(Boolean);
  if (first) first.scrollIntoView({ block: "center" });   // a panel that scrolls on its own
  window.scrollTo(0, 0);
  const layer = document.createElement("div");
  layer.style.cssText = "position:absolute;left:0;top:0;z-index:2147483646;pointer-events:none;overflow:visible;" +
    "width:" + root.scrollWidth + "px;height:" + root.scrollHeight + "px;";
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("width", root.scrollWidth); svg.setAttribute("height", root.scrollHeight);
  svg.style.cssText = "position:absolute;left:0;top:0;overflow:visible;";
  layer.appendChild(svg);
  const add = (name, attrs, text) => {
    const el = document.createElementNS(SVG, name);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    if (text != null) el.textContent = text;
    svg.appendChild(el);
  };
  const badge = (x, y, n, color) => {
    x = Math.max(11, x); y = Math.max(11, y);
    add("circle", { cx: x, cy: y, r: 10, fill: color, stroke: "#fff", "stroke-width": 2 });
    add("text", { x, y: y + 4, "text-anchor": "middle", fill: "#fff", "font-size": 12, "font-weight": 700,
                  "font-family": "system-ui, sans-serif" }, String(n));
  };
  const out = [];
  drawing.marks.forEach((mark, index) => {
    let points = mark.points, placed = false;
    const el = found[index];
    if (el && mark.rel && mark.rel.length === mark.points.length) {
      const r = el.getBoundingClientRect();
      if (r.width || r.height) {
        points = mark.rel.map((p) => [r.left + window.scrollX + p[0] * Math.max(r.width, 1),
                                      r.top + window.scrollY + p[1] * Math.max(r.height, 1)]);
        placed = true;
      }
    }
    const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
    let box = [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
    const rect = (b, filled) => {
      add("rect", { x: b[0], y: b[1], width: Math.max(b[2], 1), height: Math.max(b[3], 1), rx: 4,
                    fill: filled ? mark.color : "none", "fill-opacity": 0.12, stroke: "#fff", "stroke-width": 5,
                    "stroke-opacity": 0.7 });
      add("rect", { x: b[0], y: b[1], width: Math.max(b[2], 1), height: Math.max(b[3], 1), rx: 4, fill: "none",
                    stroke: mark.color, "stroke-width": 2.5, "stroke-dasharray": "8 5" });
    };
    const line = (pts) => {
      let d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
      add("path", { d, fill: "none", stroke: "#fff", "stroke-width": 7, "stroke-opacity": 0.75, "stroke-linecap": "round" });
      add("path", { d, fill: "none", stroke: mark.color, "stroke-width": 3.5, "stroke-linecap": "round" });
    };
    if (mark.kind === "box") {
      rect(box, true);
      if (mark.text) {
        const label = document.createElement("div");
        label.style.cssText = "position:absolute;max-width:220px;padding:2px 8px;border-radius:8px;color:#fff;" +
          "font:600 13px/1.35 system-ui,sans-serif;white-space:pre-wrap;background:" + mark.color + ";left:" +
          (box[0] + 20) + "px;top:" + (box[1] + 6) + "px;";
        label.textContent = mark.text;
        layer.appendChild(label);
      }
      badge(box[0], box[1], mark.n, mark.color);
    } else if (mark.kind === "move") {
      const [a, b] = points;
      let from = null, ghost = mark.ghost;
      if (placed) {
        const r = el.getBoundingClientRect();
        from = [r.left + window.scrollX, r.top + window.scrollY, r.width, r.height];
        ghost = [from[0] + b[0] - a[0], from[1] + b[1] - a[1], from[2], from[3]];
      }
      if (from) rect(from, false);
      if (ghost) rect(ghost, true);
      line([a, b]);
      const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const tip = [-0.45, 0.45].map((t) => [b[0] - 13 * Math.cos(angle + t), b[1] - 13 * Math.sin(angle + t)]);
      add("path", { d: `M${tip[0][0]} ${tip[0][1]}L${b[0]} ${b[1]}L${tip[1][0]} ${tip[1][1]}Z`, fill: mark.color,
                    stroke: "#fff", "stroke-width": 1.5 });
      badge(a[0], a[1], mark.n, mark.color);
      const all = [a, b];
      for (const r of [from, ghost]) if (r) all.push([r[0], r[1]], [r[0] + r[2], r[1] + r[3]]);
      const ax = all.map((p) => p[0]), ay = all.map((p) => p[1]);
      box = [Math.min(...ax), Math.min(...ay), Math.max(...ax) - Math.min(...ax), Math.max(...ay) - Math.min(...ay)];
    } else if (mark.kind === "note") {
      const note = document.createElement("div");
      note.style.cssText = "position:absolute;max-width:220px;padding:5px 8px 5px 26px;border-radius:8px;" +
        "background:#fffbe6;color:#1f2328;font:600 13px/1.35 system-ui,sans-serif;white-space:pre-wrap;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.3);border:2px solid " + mark.color + ";left:" + points[0][0] +
        "px;top:" + points[0][1] + "px;";
      note.textContent = mark.text;
      layer.appendChild(note);
      badge(points[0][0] + 2, points[0][1] + 2, mark.n, mark.color);
      box = [points[0][0], points[0][1], 220, 60];
    } else {
      let d = points.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
      if (points.length === 1) d += "l0.1 0";
      add("path", { d, fill: "none", stroke: "#fff", "stroke-width": 7, "stroke-opacity": 0.75,
                    "stroke-linecap": "round", "stroke-linejoin": "round" });
      add("path", { d, fill: "none", stroke: mark.color, "stroke-width": 3.5, "stroke-linecap": "round",
                    "stroke-linejoin": "round" });
      badge(box[0] - 4, box[1] - 4, mark.n, mark.color);
    }
    out.push({ n: mark.n, box: box.map(Math.round), placed });
  });
  root.appendChild(layer);
  return { boxes: out, page: { width: root.scrollWidth, height: root.scrollHeight } };
}
"""


def _element(target: dict | None) -> str:
    if not target:
        return "the page"
    text = f" “{target['text'][:120]}”" if target.get("text") else ""
    return f"<{target.get('tag') or 'element'}> `{target.get('selector') or '?'}`{text}"


def _area(box: list, width: int) -> str:
    """A rectangle the way a person would say it: share of the page width, and where down the page."""
    x, y, w, h = box
    width = max(width, 1)
    left, right = max(0, round(100 * x / width)), min(100, round(100 * (x + w) / width))
    return f"from {left}% to {right}% of the page width, y {round(y)} to {round(y + h)} px ({round(w)} × {round(h)} px)"


def _mark_line(mark: dict, width: int) -> str:
    color = COLOR_NAMES.get(mark["color"].lower(), mark["color"])
    where = _element(mark.get("anchor"))
    section = f" — under the heading “{mark['section']}”" if mark.get("section") else ""
    covers = "".join(f"\n   - covers {_element(item)}" for item in mark.get("covers") or [])
    if mark["kind"] == "note":
        return f"{mark['n']}. Note ({color}): “{mark['text']}” placed on {where}{section}"
    if mark["kind"] == "box":
        label = f"labelled “{mark['text']}”" if mark.get("text") else "with no label"
        return (f"{mark['n']}. Box ({color}) {label}: {_area(mark['box'], width)}, drawn over {where}{section}. "
                f"A labelled box says what should occupy that area.{covers}")
    if mark["kind"] == "move":
        (x0, y0), (x1, y1) = mark["points"]
        dx, dy = round(x1 - x0), round(y1 - y0)
        drag = ", ".join(part for part in (
            f"{abs(dx)} px {'right' if dx > 0 else 'left'}" if abs(dx) >= 8 else "",
            f"{abs(dy)} px {'down' if dy > 0 else 'up'}" if abs(dy) >= 8 else "") if part)
        to = mark.get("to") or {}
        drop = f" and dropped {SIDES.get(to.get('side'), 'on')} {_element(to)}" if to else ""
        place = f" Its new place: {_area(mark['ghost'], width)}." if mark.get("ghost") else ""
        return f"{mark['n']}. Move {where}: dragged {drag or 'a little'}{drop}{section}.{place}"
    shape = mark.get("shape") or "scribble"
    if shape == "line" and mark.get("from") and mark.get("to"):
        return (f"{mark['n']}. Line ({color}) drawn from {_element(mark['from'])} to {_element(mark['to'])}"
                f"{section}. Often means: move or connect, or an arrow pointing at the second element.")
    verb = {"loop": "Circle drawn around", "line": "Line drawn across (underline or strike-through)",
            "dot": "Dot placed on", "scribble": "Scribble over"}[shape]
    return f"{mark['n']}. {verb} {where} ({color}){section}{covers}"


def describe(drawing: dict, page_file: str, path: str) -> str:
    """The block the agent reads for one drawing attachment."""
    images = drawing.get("images") or []
    lines = [
        "<studio-drawing>",
        "The user drew on the live page to show where and what to change. The message text says what "
        "they want; the marks say where. Marks are numbered, and the numbers are painted in the images.",
        f"Page: {drawing['path']} (file: {page_file}), seen {drawing['viewport']['width']} px wide"
        f"{', dark theme' if drawing.get('dark') else ''}.",
    ]
    if images:
        lines.append("Screenshots of the marked regions with the marks drawn in; open each with the Read tool "
                     "before you answer:")
        lines += [f"- {item['path']} (marks {', '.join(str(n) for n in item['marks'])})" for item in images]
        lines.append("The screenshots are a fresh load of the page, so a filter or tab the user had set is not "
                     "in them. Where an image and the list disagree, trust the list.")
    else:
        lines.append(f"No screenshot could be made ({drawing.get('note') or 'no browser available'}); work from the list.")
    lines.append("Marks:")
    width = drawing.get("page", {}).get("width") or drawing["viewport"]["width"]
    lines += [_mark_line(mark, width) for mark in drawing["marks"]]
    if any(mark["kind"] in ("box", "move") for mark in drawing["marks"]):
        lines.append("Boxes and moves are a layout sketch. Put each named thing in its area, keep the order and "
                     "rough proportions the user drew, and change the real layout (grid, flex, source order) rather "
                     "than positioning by pixel. Nothing was moved on the page itself. Keep the result sound at "
                     "other widths, and say what you did with each mark.")
    lines.append(f"Raw coordinates and anchors are in {path}. After changing the page, look at it at the same "
                 "width to confirm the marked places now look the way the user asked.")
    lines.append("</studio-drawing>")
    return "\n".join(lines)


def load(workspace: Path, relative: str) -> dict | None:
    """A drawing attachment → its saved Drawing, or None when it is not one."""
    if not DRAWING_FILE.match(relative):
        return None
    try:
        data = json.loads((workspace / relative).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("marks"), list) and data.get("viewport") else None
