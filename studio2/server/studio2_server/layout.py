"""What the studio measures on a loaded page, with no model call (CONTRACT.md, "Look").

A screenshot shows a model that something is off only when it is large enough to see. These
are the things a browser can simply be asked: is text cut by its box, does the page scroll
sideways, is anything too small or too faint to read. The answer is a list of hints, not a
verdict: an item can be intended, and a page with none can still look wrong.
"""

from __future__ import annotations

PER_KIND = 5            # examples kept per kind; `count` has them all
MAX_ELEMENTS = 4_000    # elements measured on one page

# kind → what it means, in the order findings are listed (the worst for a reader first)
KINDS = {
    "no_viewport_meta": "The page has no <meta name=\"viewport\">: a phone lays it out 980 px wide and shrinks it, "
                        "whatever this look shows. Add <meta name=\"viewport\" content=\"width=device-width, "
                        "initial-scale=1\">.",
    "wider_than_screen": "The page scrolls sideways at this width; these elements stick out past the right edge.",
    "clipped_text": "Text is cut by a box that hides its overflow.",
    "overlapping_text": "Two pieces of text are drawn over each other.",
    "text_at_edge": "Text touches, or nearly touches, the edge of the screen.",
    "tight_padding": "Text sits against the left edge of the box that frames it (no padding).",
    "small_text": "Text too small to read comfortably.",
    "low_contrast": "Text is faint against the colour behind it (under 4.5 to 1, or 3 to 1 for large text).",
    "small_tap_target": "A control too small to tap with a finger (under 24 px, the least WCAG 2.2 allows).",
    "broken_image": "An image did not load.",
    "long_lines": "Lines of text too long to read comfortably (about 110 characters or more; 45 to 90 reads best).",
}

NOTE = ("Measured in the browser, not judged: confirm an item in the tiles before acting on it (it can be "
        "intended), and a page with no findings can still look wrong.")

# Runs in the page after the pictures are taken (it may scroll). → {measured, viewport_meta,
# dark_styles, found: {kind: {count, examples}}}
AUDIT_JS = r"""
(opts) => {
  const PER = opts.per, MAX = opts.max;
  const root = document.documentElement;
  const vw = root.clientWidth, vh = window.innerHeight;
  const narrow = vw < 700;
  const found = {};
  const seen = {};
  const short = (s, n) => { s = (s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) return s + '#' + el.id;
    const cls = [...el.classList].find((c) => /^[A-Za-z][\w-]*$/.test(c));
    if (cls) return s + '.' + cls;
    const same = el.parentElement ? [...el.parentElement.children].filter((c) => c.tagName === el.tagName) : [];
    return same.length > 1 ? s + ':nth-of-type(' + (same.indexOf(el) + 1) + ')' : s;
  };
  const path = (el) => {
    const parts = [];
    for (let at = el; at && at !== document.body && parts.length < 4; at = at.parentElement) {
      parts.unshift(name(at));
      if (at.id) break;
    }
    return parts.join(' > ');
  };
  const add = (kind, el, detail, key) => {
    const slot = found[kind] || (found[kind] = {count: 0, examples: []});
    slot.count += 1;
    if (key) { const k = kind + '|' + key; if (seen[k]) return; seen[k] = 1; }
    if (slot.examples.length < PER) {
      const top = el.getBoundingClientRect().top + window.scrollY;
      slot.examples.push({where: path(el), y: Math.max(0, Math.round(top)), text: short(el.innerText || el.alt || '', 70), detail});
    }
  };
  const px = (n) => Math.round(n) + ' px';
  const ownText = (el) => {
    let s = '';
    for (const node of el.childNodes) if (node.nodeType === 3) s += node.nodeValue;
    return s.replace(/\s+/g, ' ').trim();
  };
  // The box around an element's own text, and `lines`: each line of it (wrapped text is several boxes,
  // and only those say where the letters are).
  const textRect = (el) => {
    const range = document.createRange();
    let box = null;
    const lines = [];
    for (const node of el.childNodes) {
      if (node.nodeType !== 3 || !node.nodeValue.trim()) continue;
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        lines.push({left: r.left, top: r.top, right: r.right, bottom: r.bottom});
        box = box ? {left: Math.min(box.left, r.left), top: Math.min(box.top, r.top),
                     right: Math.max(box.right, r.right), bottom: Math.max(box.bottom, r.bottom)}
                  : {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
      }
    }
    if (box) box.lines = lines;
    return box;
  };
  const shown = (el) => {
    try { return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}); }
    catch (e) { return !!el.getClientRects().length; }
  };
  const rgba = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.some(Number.isNaN)) return null;
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) => {      // `top` drawn on `under`, both [r, g, b, a]
    const a = top[3] + under[3] * (1 - top[3]);
    if (!a) return [0, 0, 0, 0];
    return [0, 1, 2].map((i) => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a).concat(a);
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const hex = (c) => '#' + [0, 1, 2].map((i) => Math.round(c[i]).toString(16).padStart(2, '0')).join('');
  const darkCanvas = /dark/.test(getComputedStyle(root).colorScheme || '') && matchMedia('(prefers-color-scheme: dark)').matches;
  const styles = new Map();
  const style = (el) => { let s = styles.get(el); if (!s) { s = getComputedStyle(el); styles.set(el, s); } return s; };
  // The colour behind an element: null when a picture or gradient is in the way (it cannot be measured).
  const behind = (el) => {
    let colour = [0, 0, 0, 0];
    for (let at = el; at; at = at.parentElement) {
      const s = style(at);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      if (at !== el && /absolute|fixed/.test(s.position) && at !== root && at !== document.body) {
        // a positioned layer usually sits on something that is not its ancestor
        if (colour[3] < 0.95) return null;
      }
      const bg = rgba(s.backgroundColor);
      if (bg && bg[3] > 0) colour = over(colour, bg);
      if (colour[3] >= 0.95) return colour;
    }
    return over(colour, darkCanvas ? [18, 18, 18, 1] : [255, 255, 255, 1]);
  };
  const scrolls = (el) => {            // inside a box the person can scroll: its content is reachable
    for (let at = el.parentElement; at && at !== document.body; at = at.parentElement) {
      const s = style(at);
      if (/auto|scroll/.test(s.overflowX) && at.scrollWidth > at.clientWidth + 1) return true;
      if (/auto|scroll/.test(s.overflowY) && at.scrollHeight > at.clientHeight + 1) return true;
    }
    return false;
  };
  const pinned = (el) => {
    for (let at = el; at && at !== document.body; at = at.parentElement) if (/fixed|sticky/.test(style(at).position)) return true;
    return false;
  };

  if (!document.querySelector('meta[name="viewport" i]')) {
    found.no_viewport_meta = {count: 1, examples: []};
  }
  let dark = false;
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    const walk = (list) => {
      for (const rule of list) {
        if (dark) return;
        if (rule.media && /prefers-color-scheme:\s*dark/.test(rule.conditionText || rule.media.mediaText || '')) dark = true;
        else if (rule.cssRules && rule.cssRules.length) walk(rule.cssRules);
      }
    };
    walk(rules);
  }

  const all = document.body ? [...document.body.querySelectorAll('*')].slice(0, MAX) : [];
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'BR', 'HR', 'HEAD', 'META', 'LINK']);
  const sideways = root.scrollWidth > vw + 1;
  const texts = [];
  let measured = 0;
  for (const el of all) {
    if (skip.has(el.tagName) || el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
    if (!shown(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    measured += 1;
    const s = style(el);
    const tag = el.tagName;

    if (tag === 'IMG') {
      if (el.complete && !el.naturalWidth && (el.currentSrc || el.getAttribute('src'))) {
        add('broken_image', el, short(el.getAttribute('src') || el.currentSrc, 90));
      }
      continue;
    }
    if (sideways && rect.right > vw + 1 && !/fixed/.test(s.position) && !scrolls(el)) {
      const parent = el.parentElement && el.parentElement.getBoundingClientRect();
      if (!parent || parent.right <= vw + 1 || el.parentElement === document.body) {
        add('wider_than_screen', el, 'sticks out ' + px(rect.right - vw) + ' past the right edge (' + px(rect.width) + ' wide on a ' + px(vw) + ' screen)');
      }
    }
    if (el !== document.body && rect.width > 8 && rect.height > 8 && (el.innerText || '').trim()) {
      const cutX = /hidden|clip/.test(s.overflowX) ? el.scrollWidth - el.clientWidth : 0;
      const cutY = /hidden|clip/.test(s.overflowY) ? el.scrollHeight - el.clientHeight : 0;
      if (cutX > 4 || cutY > 4) {
        const meant = s.textOverflow === 'ellipsis' || (s.webkitLineClamp && s.webkitLineClamp !== 'none');
        const how = [cutX > 4 ? 'cut on the right by ' + px(cutX) : '', cutY > 4 ? 'cut at the bottom by ' + px(cutY) : ''].filter(Boolean).join(', ');
        add('clipped_text', el, how + (meant ? ' (ends in an ellipsis or line clamp; may be meant)' : ''));
      }
    }
    if (narrow && /^(BUTTON|SELECT|TEXTAREA|SUMMARY)$/.test(tag) || narrow && tag === 'INPUT' && !/hidden|range/.test(el.type)
        || narrow && el.getAttribute('role') === 'button' || narrow && tag === 'A' && el.hasAttribute('href')) {
      // A link in running text, or a reference mark, cannot be made taller than its line.
      const inSentence = tag === 'A' && /^inline/.test(s.display)
        && (el.closest('sup, sub, p, li, td, th, dd, figcaption, blockquote') || el.parentElement && ownText(el.parentElement).length > 0);
      const tick = tag === 'INPUT' && /checkbox|radio/.test(el.type) && el.closest('label');
      if (!inSentence && !tick && !el.disabled && Math.min(rect.width, rect.height) < 24) {
        add('small_tap_target', el, px(rect.width).replace(' px', '') + ' × ' + px(rect.height));
      }
    }

    const text = ownText(el);
    if (text.length < 2) continue;
    const box = textRect(el);
    if (!box) continue;
    const size = parseFloat(s.fontSize) || 0;
    const reachable = scrolls(el);
    // Reference marks and units ("[8]", "kWh") are small on purpose; sentences are not.
    if (text.length >= 6 && size && size < 11 && !el.closest('sup, sub')) add('small_text', el, size.toFixed(1).replace(/\.0$/, '') + ' px');
    const edge = narrow ? 8 : 12;
    if (!reachable && box.right <= vw + 1 && box.left >= -1 && (box.left < edge || box.right > vw - edge)) {
      add('text_at_edge', el, box.left < edge ? px(Math.max(0, box.left)) + ' from the left edge' : px(Math.max(0, vw - box.right)) + ' from the right edge');
    }
    for (let at = el, depth = 0; at && at !== document.body && depth < 4; at = at.parentElement, depth += 1) {
      const f = style(at);
      const bg = rgba(f.backgroundColor);
      const framed = bg && bg[3] > 0.05 || parseFloat(f.borderLeftWidth) > 0 && f.borderLeftStyle !== 'none';
      if (!framed) continue;
      const frame = at.getBoundingClientRect();
      if (!/^inline/.test(f.display) && frame.width >= 120 && frame.height >= 18 && box.left - frame.left < 6
          && box.left - frame.left > -2 && frame.left > 0 && !/^(TD|TH|TR|TABLE|INPUT|TEXTAREA|SELECT)$/.test(at.tagName)) {
        add('tight_padding', el, px(Math.max(0, box.left - frame.left)) + ' from the left edge of ' + name(at));
      }
      break;
    }
    if (!el.closest('[disabled], [aria-disabled="true"]')) {
      const fg = rgba(s.color), bg = behind(el);
      if (fg && bg && fg[3] > 0) {
        const drawn = over(fg, bg);
        const a = lum(drawn), b = lum(bg);
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        const large = size >= 24 || size >= 18.66 && (parseInt(s.fontWeight, 10) || 400) >= 700;
        if (ratio < (large ? 3 : 4.5)) {
          add('low_contrast', el, ratio.toFixed(2) + ' to 1 (' + hex(drawn) + ' on ' + hex(bg) + ', ' + Math.round(size) + ' px)', hex(drawn) + hex(bg));
        }
      }
    }
    if (!narrow && /^(P|LI|BLOCKQUOTE|DD)$/.test(tag) && text.length > 240 && size) {
      const perLine = (box.right - box.left) / (size * 0.5);
      if (perLine >= 110) add('long_lines', el, 'about ' + Math.round(perLine / 5) * 5 + ' characters a line');
    }
    if (texts.length < 1500 && !pinned(el)) {
      for (const line of box.lines.slice(0, 12)) {
        texts.push({el, left: line.left, right: line.right, top: line.top + window.scrollY, bottom: line.bottom + window.scrollY});
      }
    }
  }

  // Text drawn over text. Checked last because it scrolls the page to ask what is on top.
  texts.sort((a, b) => a.top - b.top);
  const startY = window.scrollY;
  const opaqueBetween = (el, stop) => {
    for (let at = el; at && at !== stop; at = at.parentElement) {
      const s = style(at);
      const bg = rgba(s.backgroundColor);
      if (bg && bg[3] > 0.5 || s.backgroundImage && s.backgroundImage !== 'none') return true;
    }
    return false;
  };
  const common = (a, b) => { for (let at = a; at; at = at.parentElement) if (at.contains(b)) return at; return document.body; };
  let pairs = 0;
  const paired = new Set();
  for (let i = 0; i < texts.length && pairs < 40; i += 1) {
    const a = texts[i];
    for (let j = i + 1; j < texts.length && texts[j].top < a.bottom; j += 1) {
      const b = texts[j];
      if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w <= 3 || h <= 3) continue;
      const smaller = Math.min((a.right - a.left) * (a.bottom - a.top), (b.right - b.left) * (b.bottom - b.top));
      if (!smaller || (w * h) / smaller < 0.25) continue;
      if (paired.has(a.el) && paired.has(b.el)) continue;      // one finding per pair of elements
      const cx = Math.max(a.left, b.left) + w / 2, cy = Math.max(a.top, b.top) + h / 2;
      if (cx < 0 || cx > vw) continue;
      window.scrollTo(0, Math.max(0, cy - vh / 2));
      const top = document.elementFromPoint(cx, cy - window.scrollY);
      if (!top) continue;
      const upper = a.el.contains(top) || top.contains(a.el) ? a : b.el.contains(top) || top.contains(b.el) ? b : null;
      if (!upper) continue;                                   // something else covers both
      const lower = upper === a ? b : a;
      if (opaqueBetween(upper.el, common(upper.el, lower.el))) continue;   // one hides the other: stacked, not a collision
      pairs += 1;
      paired.add(a.el); paired.add(b.el);
      add('overlapping_text', lower.el, 'overlaps “' + short(upper.el.innerText, 40) + '” (' + path(upper.el) + ') by ' + px(w) + ' × ' + px(h).replace(' px', '') + ' px');
    }
  }
  window.scrollTo(0, startY);
  return {measured, viewport_meta: !found.no_viewport_meta, dark_styles: dark, found};
}
"""


def report(raw: dict) -> dict:
    """What the page's script returned → the `layout` object of a look."""
    found = raw.get("found") or {}
    findings = [{"kind": kind, "count": int(found[kind].get("count") or 0), "means": means,
                 "examples": list(found[kind].get("examples") or [])[:PER_KIND]}
                for kind, means in KINDS.items() if kind in found]
    return {"measured": int(raw.get("measured") or 0), "viewport_meta": bool(raw.get("viewport_meta")),
            "dark_styles": bool(raw.get("dark_styles")), "findings": findings, "note": NOTE}


def tiles(height: int, piece: int, overlap: int = 60) -> list[tuple[int, int]]:
    """A page `height` px tall cut into pieces of at most `piece` px that overlap a little →
    [(top, height)], top to bottom, covering every pixel. One piece when it fits (or nearly)."""
    height, piece = max(1, int(height)), max(200, int(piece))
    if height <= piece * 1.25:           # a little over one piece is still one piece
        return [(0, height)]
    count = -(-(height - overlap) // (piece - overlap))          # ceil
    step = (height - piece) / (count - 1)
    return [(round(index * step), piece) for index in range(count)]
