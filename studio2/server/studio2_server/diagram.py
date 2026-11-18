"""Diagrams that are checked before they are shown (CONTRACT.md, "Diagrams and conversions").

The agent writes Mermaid source; the studio parses and draws it in its own Chromium with a copy
of Mermaid kept inside the server (static/vendor/mermaid, one pinned version, never a CDN), and
saves the source, an SVG for light pages, an SVG for dark pages and, when asked, a PNG for
Office files. Then it checks the drawing: every label that was asked for is in it, nothing is
cut off or drawn over other text, and it is readable at desktop width.

The configuration is fixed (layout engine, look, font family, labels as SVG text) so the same
source gives the same picture on this computer. Mermaid's hand-drawn outlines call Math.random,
so the page gets a seeded one before each drawing; a Gantt chart gets no "today" line unless its
source asks for one, since that line moves every day.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import workspace as ws

MERMAID_VERSION = "11.15.0"          # the only place the version is written; see static/vendor/mermaid/THIRD_PARTY.md
VENDOR = Path(__file__).parent / "static" / "vendor" / "mermaid" / "mermaid.min.js"
FOLDER = "files/diagrams"
FONT = "Helvetica, Arial, sans-serif"
MAX_SOURCE = 50_000
DESKTOP = 1400                       # wider than this, a diagram is shrunk on a desktop page
MIN_TEXT = 9.0                       # px at desktop width; under this it is hard to read
PNG_MIN, PNG_MAX = 200, 4000
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
COLOR_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]{0,40}")
COLOR_VALUE = re.compile(r"[#A-Za-z0-9(),.%\s-]{1,60}")
TIMEOUT = 30.0
LABELS_KEPT = 80
GANTT_WIDTH = 1200

KINDS = {
    "too_wide": f"Wider than {DESKTOP} px: a desktop page shrinks it to fit, and its text with it.",
    "small_text": f"Text under {MIN_TEXT:g} px once the diagram is shrunk to a desktop page.",
    "clipped_text": "Text runs past the edge of its box or of the picture.",
    "overlapping_text": "Two pieces of text are drawn over each other.",
}


class DiagramError(Exception):
    pass


def reason(looker) -> str | None:
    """None when diagrams can be drawn; otherwise why not, with what to do about it."""
    if not VENDOR.is_file():
        return (f"The studio's own copy of Mermaid ({VENDOR}) is missing, so diagrams cannot be drawn. "
                "Reinstall the studio from its repository.")
    return looker.reason()


def base_config(theme: str, variables: dict | None, dark: bool) -> dict:
    """What every drawing is made with. A source may still set its own (`%%{init: …}%%` or front matter)."""
    config = {"startOnLoad": False, "securityLevel": "strict", "deterministicIds": True,
              "suppressErrorRendering": True, "logLevel": 5, "look": "classic", "layout": "dagre",
              "theme": theme, "fontFamily": FONT, "htmlLabels": False, "maxTextSize": MAX_SOURCE,
              "flowchart": {"htmlLabels": False}, "class": {"htmlLabels": False},
              "state": {"htmlLabels": False},
              # A Gantt chart takes the width of whatever holds it; here that is fixed.
              "gantt": {"useWidth": GANTT_WIDTH}}
    if variables:
        config["theme"] = "base"
        config["themeVariables"] = {**variables, **({"darkMode": True} if dark else {})}
    return config


def clean_colors(colors) -> dict:
    """`colors` {light: {themeVariable: value}, dark: {…}} → the same, with only safe names and values."""
    if not colors:
        return {}
    if not isinstance(colors, dict) or set(colors) - {"light", "dark"}:
        raise DiagramError("colors takes {\"light\": {…}, \"dark\": {…}}: Mermaid theme variables such as "
                           "primaryColor, primaryTextColor, lineColor, background.")
    kept = {}
    for mode, values in colors.items():
        if not isinstance(values, dict) or len(values) > 40:
            raise DiagramError(f"colors.{mode} must be an object of at most 40 theme variables.")
        for key, value in values.items():
            if not COLOR_KEY.fullmatch(str(key)) or not COLOR_VALUE.fullmatch(str(value)):
                raise DiagramError(f"colors.{mode}.{key}: give a colour such as #1f6feb, rgb(…) or a colour name.")
        kept[mode] = {str(k): str(v) for k, v in values.items()}
    return kept


def no_today_line(source: str) -> str:
    """A Gantt chart drawn today and tomorrow must be the same picture: no "today" line unless asked."""
    lines = source.split("\n")
    if re.search(r"^\s*todayMarker\b", source, re.M):
        return source
    for index, line in enumerate(lines):
        if re.match(r"\s*gantt\s*$", line):
            return "\n".join(lines[: index + 1] + ["    todayMarker off"] + lines[index + 1:])
        if line.strip() and not line.strip().startswith("%%") and line.strip() != "---":
            if not re.match(r"\s*[\w-]+\s*:", line):   # front matter keys come before the type line
                break
    return source


# Runs in a blank page that holds Mermaid. → {error, hash} or {type, light, dark, facts}
DRAW_JS = r"""
async (o) => {
  const seed = () => { let x = 20261118; Math.random = () => { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; }; };
  const fail = (e) => ({error: String((e && e.message) || e), hash: e && e.hash ? {line: e.hash.line, loc: e.hash.loc || null} : null});
  let type = null;
  try { mermaid.initialize(o.light); const parsed = await mermaid.parse(o.source); type = parsed && parsed.diagramType || null; }
  catch (e) { return fail(e); }
  const removed = [];
  const strip = (css) => css.replace(/@import[^;]*;?/gi, () => { removed.push('an @import rule'); return ''; })
                            .replace(/url\(\s*(['"]?)(?!#)[^)]*\)/gi, (m) => { removed.push('a style reference ' + m.slice(0, 80)); return 'none'; });
  const clean = (svg) => {
    svg.querySelectorAll('script, iframe, object, embed, audio, video, link, meta, img, image').forEach((n) => {
      removed.push('a <' + n.tagName.toLowerCase() + '> element'); n.remove(); });
    for (const n of [svg, ...svg.querySelectorAll('*')]) {
      for (const a of [...n.attributes]) {
        const name = a.name.toLowerCase();
        if (name.startsWith('on')) { n.removeAttribute(a.name); removed.push('a ' + name + ' handler'); }
        else if ((name === 'href' || name === 'xlink:href' || name === 'src') && !a.value.trim().startsWith('#')) {
          n.removeAttribute(a.name); removed.push('a link to ' + a.value.slice(0, 80)); }
        else if (name === 'style' && /@import|url\(\s*['"]?(?!#)/i.test(a.value)) n.setAttribute('style', strip(a.value));
      }
    }
    svg.querySelectorAll('a').forEach((a) => a.replaceWith(...a.childNodes));
    svg.querySelectorAll('style').forEach((s) => { s.textContent = strip(s.textContent); });
    const box = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    if (box.length === 4 && box.every(Number.isFinite)) {
      svg.setAttribute('width', String(Math.ceil(box[2])));
      svg.setAttribute('height', String(Math.ceil(box[3])));
    }
    svg.removeAttribute('style');
  };
  const draw = async (mode) => {
    seed();
    mermaid.initialize(o[mode]);
    const host = document.createElement('div');
    host.className = 'host-' + mode;
    document.body.append(host);
    const {svg} = await mermaid.render(o.id + (mode === 'dark' ? '-dark' : ''), o.drawn);
    host.innerHTML = svg;
    const el = host.querySelector('svg');
    clean(el);
    return el;
  };
  let light, dark;
  try { light = await draw('light'); dark = await draw('dark'); } catch (e) { return fail(e); }
  dark.parentElement.style.display = 'none';

  // What is in it: labels, shapes and lines.
  const textOf = (t) => {
    const rows = [...t.children].filter((c) => c.tagName.toLowerCase() === 'tspan');
    const parts = rows.length > 1 ? rows.map((r) => r.textContent) : [t.textContent];
    return parts.map((p) => (p || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' ');
  };
  // Labels are SVG text; with HTML labels (a source may ask for them) they are foreignObject content.
  const texts = [...light.querySelectorAll('text, foreignObject')]
    .map((t) => ({t, s: t.tagName.toLowerCase() === 'text' ? textOf(t) : (t.textContent || '').replace(/\s+/g, ' ').trim()}))
    .filter((x) => x.s);
  const count = (sel) => light.querySelectorAll(sel).length;
  const t = type || '';
  let nodes = count('g.node'), edges = count('g.edgePaths > path, g.edgePaths > g > path');
  if (t === 'sequence') { nodes = count('rect.actor-top, g.actor-top'); edges = count('.messageLine0, .messageLine1'); }
  else if (t === 'gantt') { nodes = count('rect.task'); edges = null; }
  else if (t === 'timeline') { nodes = count('g.taskWrapper') + count('g.eventWrapper'); edges = null; }
  if (!nodes) nodes = null;

  // How it reads: size, the smallest text, text cut off or drawn over other text.
  const root = light.getBoundingClientRect();
  const width = Math.ceil(root.width), height = Math.ceil(root.height);
  const found = {};
  const add = (kind, text, detail) => {
    const f = found[kind] = found[kind] || {kind, count: 0, examples: []};
    f.count += 1;
    if (f.examples.length < 5) f.examples.push({text: text.slice(0, 60), detail});
  };
  let smallest = null;
  const rects = [];
  for (const {t: el, s} of texts) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const ctm = el.getScreenCTM();
    const styled = el.tagName.toLowerCase() === 'text' ? el : (el.querySelector('span, div, p') || el);
    const px = parseFloat(getComputedStyle(styled).fontSize) * (ctm ? Math.hypot(ctm.a, ctm.b) : 1);
    if (Number.isFinite(px) && (smallest === null || px < smallest)) smallest = px;
    rects.push({r, s});
    const out = Math.max(root.left - r.left, r.right - root.right, root.top - r.top, r.bottom - root.bottom);
    if (out > 2) add('clipped_text', s, 'runs past the edge of the picture by ' + Math.round(out) + ' px');
    const node = el.closest('g.node');
    if (node) {
      const shape = [...node.querySelectorAll('rect, polygon, circle, ellipse, path')].find((n) => !n.closest('.label') && !n.closest('text'));
      if (shape) {
        const b = shape.getBoundingClientRect();
        const over = Math.max(b.left - r.left, r.right - b.right);
        if (b.width > 4 && over > 2) add('clipped_text', s, 'runs past the side of its box by ' + Math.round(over) + ' px');
      }
    }
  }
  for (let i = 0; i < rects.length; i++) {
    for (let j = i + 1; j < rects.length; j++) {
      const a = rects[i].r, b = rects[j].r;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w <= 0 || h <= 0) continue;
      if (w * h >= 0.25 * Math.min(a.width * a.height, b.width * b.height)) {
        add('overlapping_text', rects[i].s, 'drawn over “' + rects[j].s.slice(0, 40) + '”');
      }
    }
  }
  const labels = [];
  for (const {s} of texts) if (!labels.includes(s)) labels.push(s);
  const serial = new XMLSerializer();
  return {type, light: serial.serializeToString(light), dark: serial.serializeToString(dark),
          facts: {width, height, nodes, edges, labels, smallest_text_px: smallest === null ? null : Math.round(smallest * 10) / 10,
                  found: Object.values(found), removed: [...new Set(removed)].slice(0, 20),
                  foreign: light.querySelectorAll('foreignObject').length}};
}
"""

PNG_JS = r"""
(o) => {
  document.body.innerHTML = '';
  document.body.style.cssText = 'margin:0;background:#fff';
  const host = document.createElement('div');
  host.id = 'png';
  host.style.cssText = 'display:inline-block;background:#fff;width:' + o.width + 'px';
  host.innerHTML = o.svg;
  const svg = host.querySelector('svg');
  svg.style.cssText = 'display:block;width:100%;height:auto';
  document.body.append(host);
  const r = host.getBoundingClientRect();
  return {width: Math.round(r.width), height: Math.round(r.height)};
}
"""


def normal(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).casefold()


def line_of(error: str, hash_: dict | None) -> int | None:
    if hash_:
        loc = hash_.get("loc") or {}
        if isinstance(loc.get("first_line"), int):
            return loc["first_line"]
        if isinstance(hash_.get("line"), int):
            return hash_["line"] + 1
    match = re.search(r"\bline[: ]+(\d+)", error, re.I)
    return int(match.group(1)) if match else None


def type_line(source: str) -> int | None:
    """The line that should name the diagram's type: the first that is not blank, a comment or front matter."""
    lines, front = source.split("\n"), False
    for number, line in enumerate(lines, 1):
        bare = line.strip()
        if number == 1 and bare == "---":
            front = True
            continue
        if front:
            front = bare != "---"
            continue
        if bare and not bare.startswith("%%"):
            return number
    return None


def check_svg(text: str) -> dict:
    """The saved file's own test: well-formed, no script, no outside reference. Raises when not."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DiagramError(f"The drawing is not a well-formed SVG ({exc}); nothing was saved.") from None
    scripts = sum(1 for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() == "script")
    outside = 0
    for el in root.iter():
        for key, value in el.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (name in ("href", "src") and not value.strip().startswith("#")):
                outside += 1
    outside += len(re.findall(r"@import|url\(\s*['\"]?(?!#)", text, re.I))
    if scripts or outside:
        raise DiagramError("The drawing still held a script or an outside reference after cleaning; nothing was saved.")
    return {"scripts": 0, "outside_references": 0}


def embed(svg: str, svg_dark: str, alt: str) -> str:
    """A snippet for a page in the project's root folder (paths are from the project root)."""
    alt = alt.replace('"', "&quot;").replace("<", "&lt;")
    return (f'<picture><source srcset="{svg_dark}" media="(prefers-color-scheme: dark)">'
            f'<img src="{svg}" alt="{alt}" style="max-width:100%;height:auto"></picture>'
            "  (paths are from the project root: make them relative to the page)")


async def render(looker, workspace: Path, *, name: str, source: str | None = None, path: str | None = None,
                 must_show: list | None = None, png_width: int | None = None, colors=None,
                 alt: str | None = None) -> dict:
    """Parse, draw, check and save one diagram. A parse error saves nothing and says which line."""
    started = time.monotonic()
    why = reason(looker)
    if why:
        raise DiagramError(why)
    name = str(name or "").strip().lower()
    if not NAME.fullmatch(name):
        raise DiagramError("name: lower-case letters, digits, - and _ (60 at most), such as order-flow.")
    if path:
        if source:
            raise DiagramError("Give `source` or `path`, not both.")
        try:
            file = ws.safe_path(workspace, str(path))
        except Exception:
            raise DiagramError("path must be a .mmd file inside the project.") from None
        if file.suffix.lower() != ".mmd" or not file.is_file():
            raise DiagramError(f"There is no .mmd file at {path} in the project.")
        source = file.read_text("utf-8", errors="replace")
    source = str(source or "").replace("\r\n", "\n")
    if not source.strip():
        raise DiagramError("Give the Mermaid source as `source`, or the path of a .mmd file as `path`.")
    if len(source) > MAX_SOURCE:
        raise DiagramError(f"The source is longer than {MAX_SOURCE:,} characters: split the diagram.")
    wanted = [str(item) for item in (must_show or []) if str(item).strip()][:100]
    if png_width is not None:
        png_width = int(png_width)
        if not PNG_MIN <= png_width <= PNG_MAX:
            raise DiagramError(f"png_width is the picture's width in pixels, {PNG_MIN} to {PNG_MAX}.")
    palette = clean_colors(colors)
    options = {"source": source, "drawn": no_today_line(source), "id": f"diagram-{name}",
               "light": base_config("default", palette.get("light"), False),
               "dark": base_config("dark", palette.get("dark"), True)}

    await looker.browser()
    blocked: list[str] = []
    png_bytes = None
    async with looker.slot():
        context = await looker.new_context(None, viewport={"width": 1600, "height": 1000})
        try:
            # Nothing leaves the page: Mermaid is put in from the studio's own copy.
            async def refuse(route):
                blocked.append(route.request.url[:120])
                await route.abort()
            await context.route("**/*", refuse)
            page = await context.new_page()
            await page.set_content("<!doctype html><html><head><meta charset=utf-8></head><body></body></html>")
            await page.add_script_tag(path=str(VENDOR))
            drawn = await asyncio.wait_for(page.evaluate(DRAW_JS, options), TIMEOUT)
            if "error" not in drawn and png_width:
                size = await page.evaluate(PNG_JS, {"svg": drawn["light"], "width": png_width})
                await page.set_viewport_size({"width": png_width + 40, "height": max(200, min(size["height"] + 40, 16000))})
                png_bytes = await page.locator("#png").screenshot(type="png", timeout=10_000)
        except asyncio.TimeoutError:
            raise DiagramError(f"Drawing took longer than {int(TIMEOUT)} seconds: simplify the diagram.") from None
        finally:
            await context.close()

    if "error" in drawn:
        line = line_of(drawn["error"], drawn.get("hash"))
        if line is None and "No diagram type detected" in drawn["error"]:
            line = type_line(source)
        lines = source.split("\n")
        message = drawn["error"][:900]
        if blocked:
            message += (f" (The diagram asks for something from outside, {blocked[0]}: the studio fetches nothing "
                        "while drawing, so leave pictures and icons from the web out of diagrams.)")
        return {"rendered": False, "saved": False, "name": name, "error": message, "line": line,
                "line_text": lines[line - 1][:200] if line and 0 < line <= len(lines) else None,
                "next": (f"Nothing was saved. Fix {'line ' + str(line) if line else 'the source'} (the parser's message is "
                         "in `error`) and call render_diagram again. If it still fails after three tries, use a "
                         "simpler diagram or a table instead, and say so in your answer."),
                "ms": int((time.monotonic() - started) * 1000)}

    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    light, dark = header + drawn["light"] + "\n", header + drawn["dark"] + "\n"
    safe = check_svg(light)
    check_svg(dark)
    facts = drawn["facts"]
    folder = ws.safe_path(workspace, FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    rel = lambda p: p.relative_to(workspace.resolve()).as_posix()  # noqa: E731
    files = {"source": folder / f"{name}.mmd", "svg": folder / f"{name}.svg", "svg_dark": folder / f"{name}-dark.svg"}
    files["source"].write_text(source if source.endswith("\n") else source + "\n", "utf-8")
    files["svg"].write_text(light, "utf-8")
    files["svg_dark"].write_text(dark, "utf-8")
    png = None
    if png_bytes:
        files["png"] = folder / f"{name}.png"
        files["png"].write_bytes(png_bytes)
        png = rel(files["png"])

    # Each label is looked for inside one piece of drawn text: "Waiting for payment" drawn as three
    # states ("Waiting", "for", "payment") is not what was asked.
    drawn_labels = [normal(label) for label in facts["labels"]]
    missing = [label for label in wanted if not any(normal(label) in text for text in drawn_labels)]
    width, height = facts["width"], facts["height"]
    scale = min(1.0, DESKTOP / width) if width else 1.0
    smallest = facts["smallest_text_px"]
    at_desktop = round(smallest * scale, 1) if smallest is not None else None
    findings = []
    if width > DESKTOP:
        findings.append({"kind": "too_wide", "count": 1, "means": KINDS["too_wide"],
                         "examples": [{"text": "", "detail": f"{width} px wide; shown at {DESKTOP} px, everything at {scale:.0%}"}]})
    if at_desktop is not None and at_desktop < MIN_TEXT:
        findings.append({"kind": "small_text", "count": 1, "means": KINDS["small_text"],
                         "examples": [{"text": "", "detail": f"the smallest text is {at_desktop} px at desktop width"}]})
    for found in facts["found"]:
        findings.append({**found, "means": KINDS[found["kind"]]})
    hard = any(f["kind"] in ("too_wide", "small_text") for f in findings)

    nexts = []
    if missing:
        nexts.append(f"The diagram rendered but is not what was asked: {len(missing)} label"
                     f"{'s are' if len(missing) != 1 else ' is'} missing ({', '.join(missing[:20])}). Fix the source "
                     "and call render_diagram again.")
    elif not wanted:
        nexts.append(f"Compare what was drawn with what you meant: {facts['nodes'] or 0} shapes, "
                     f"{facts['edges'] if facts['edges'] is not None else 'no'} connecting lines, labels in `labels`. "
                     "Pass `must_show` next time to have the studio check the labels for you.")
    if hard:
        nexts.append("It is hard to read on a desktop page: split it into two diagrams, or change its direction "
                     "(flowchart LR to TB, or the other way), then render again.")
    if any(f["kind"] in ("clipped_text", "overlapping_text") for f in findings):
        nexts.append("Some text is cut off or drawn over other text (`layout`): shorten those labels or break them "
                     "with <br>, then render again.")
    if not nexts:
        nexts.append("All asked-for labels are there and it reads at desktop width.")
    nexts.append("Put the saved SVG in the page (`embed`, or the SVG inline); never load Mermaid in the page. For a "
                 "deck or Word file use the PNG (call again with png_width).")
    if facts["foreign"]:
        nexts.append("Some labels are HTML inside the SVG (foreignObject), because the source asked for HTML labels: "
                     "they show in a browser but not in Office files or the PNG of other tools.")
    return {"rendered": True, "saved": True, "name": name, "type": drawn["type"],
            "source": rel(files["source"]), "svg": rel(files["svg"]), "svg_dark": rel(files["svg_dark"]),
            "png": png, "png_width": png_width if png else None,
            "size": {"width": width, "height": height}, "nodes": facts["nodes"], "edges": facts["edges"],
            "labels": facts["labels"][:LABELS_KEPT], "must_show": len(wanted), "missing": missing,
            "layout": {"width": width, "height": height, "smallest_text_px": smallest,
                       "smallest_text_at_desktop_px": at_desktop, "hard_to_read": hard, "findings": findings},
            "safe": {**safe, "removed": facts["removed"], "text_as_text": facts["foreign"] == 0,
                     "fonts": f"No font is embedded or linked: the text names the system fonts {FONT}. Sizes were "
                              "measured with the fonts on the studio's computer."},
            "embed": embed(rel(files["svg"]), rel(files["svg_dark"]), alt or name.replace("-", " ")),
            "renderer": f"Mermaid {MERMAID_VERSION} (the studio's own copy) in the studio's Chromium",
            "blocked_requests": blocked[:5], "ms": int((time.monotonic() - started) * 1000),
            "next": " ".join(nexts)}
