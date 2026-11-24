/* Studio bridge: injected into every preview page by the studio server.
 * Talks to the studio shell (the parent window) with postMessage; see CONTRACT.md,
 * "Preview and bridge". Plain JS, no dependencies. Does nothing when not framed. */
(function () {
  "use strict";
  // The person's sign-in (CONTRACT.md, "Seeing an app signed in as the person"): a snapshot of
  // this origin's localStorage and sessionStorage goes to the preview server (same origin; it
  // answers the path itself and never passes it to the app). Framed or not; never in the studio's
  // own headless browser. Cookies travel with the request; the server decides what it keeps.
  if (window.__studioSession || navigator.webdriver) return;
  window.__studioSession = true;
  var PATH = "/__studio/session", sent = null, sentAt = 0, timer = null;
  var send = window.fetch && window.fetch.bind(window);
  function entries(store) {
    var out = [];
    try {
      for (var i = 0; i < store.length; i++) {
        var key = store.key(i);
        if (key === null || key.indexOf("studio-scroll:") === 0) continue;
        out.push([key, String(store.getItem(key))]);
      }
    } catch (e) {}
    return out;
  }
  function report(force, leaving) {
    if (!send) return;
    var body = JSON.stringify({ local: entries(window.localStorage), session: entries(window.sessionStorage) });
    var now = Date.now();
    if (!force && body === sent && now - sentAt < 60000) return;
    sent = body;
    sentAt = now;
    try {
      send(PATH, { method: "POST", credentials: "same-origin", keepalive: !!leaving && body.length < 60000,
                   headers: { "content-type": "application/json" }, body: body }).catch(function () {});
    } catch (e) {}
  }
  function soon(ms) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { timer = null; report(true); }, ms);
  }
  soon(400);
  setInterval(function () { if (document.visibilityState === "visible") report(false); }, 5000);
  ["click", "submit"].forEach(function (name) {
    window.addEventListener(name, function () { soon(2000); }, true);
  });
  ["popstate", "hashchange"].forEach(function (name) {
    window.addEventListener(name, function () { soon(400); });
  });
  ["pushState", "replaceState"].forEach(function (name) {
    var original = history[name];
    if (typeof original !== "function") return;
    history[name] = function () {
      var result = original.apply(this, arguments);
      soon(400);
      return result;
    };
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") report(true, true);
  });
  window.addEventListener("pagehide", function () { report(true, true); });
})();

(function () {
  "use strict";
  if (window.parent === window || window.__studioBridge) return;
  window.__studioBridge = true;

  var mode = "browse";
  var overlay = null;      // hover outline; lives outside <body> and never receives pointer events
  var style = null;
  var editing = null;      // {el, before, html, attr}

  function post(type, fields) {
    var message = { source: "studio-bridge", type: type };
    for (var key in fields) message[key] = fields[key];
    // Target origin "*": the frame cannot know the shell's origin; the shell validates ours.
    window.parent.postMessage(message, "*");
  }

  function clip(text, limit) {
    text = String(text == null ? "" : text);
    return text.length > limit ? text.slice(0, limit) : text;
  }

  function isOwn(el) {
    return el === overlay || el === style || isDrawn(el) || isChip(el);
  }

  function isUnique(selector) {
    try { return document.querySelectorAll(selector).length === 1; } catch (e) { return false; }
  }

  // A short unique selector: #id when it is unique, else a tag path with :nth-of-type.
  function selectorFor(el) {
    var parts = [];
    for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
      if (node.id && isUnique("#" + CSS.escape(node.id))) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }
      var tag = node.localName;
      if (node === document.body || node === document.documentElement) {
        parts.unshift(tag);
        break;
      }
      var same = 0, position = 0;
      var siblings = node.parentElement ? node.parentElement.children : [];
      for (var i = 0; i < siblings.length; i++) {
        if (siblings[i].localName === tag) {
          same++;
          if (siblings[i] === node) position = same;
        }
      }
      parts.unshift(same > 1 ? tag + ":nth-of-type(" + position + ")" : tag);
      if (parts.length > 1 && isUnique(parts.join(" > "))) break;
    }
    return parts.join(" > ");
  }

  // Where an element comes from in the app's own source, when the page can say. Development
  // builds of the common frameworks leave this on the element or just above it: a component
  // name (React, Angular), a component file (Vue, Svelte), or a file and line stamped by a
  // build plugin (Astro, code inspectors). A plain page or a production build says nothing.
  var STAMPS = ["data-astro-source-file", "data-insp-path", "data-inspector-relative-path", "data-source-loc",
                "data-source", "data-v-inspector", "data-locatorjs", "data-locatorjs-id", "data-component-file",
                "data-sentry-source-file", "data-sentry-component"];

  function componentName(type) {
    if (!type) return "";
    if (typeof type === "function") return type.displayName || type.name || "";
    if (typeof type === "object") return type.displayName || componentName(type.render || type.type);
    return "";
  }

  function reactHint(node) {
    var key = Object.keys(node).filter(function (k) { return k.indexOf("__reactFiber$") === 0 || k.indexOf("__reactInternalInstance$") === 0; })[0];
    if (!key) return "";
    var names = [], where = "", seen = 0;
    for (var fiber = node[key]; fiber && seen < 60 && names.length < 4; fiber = fiber._debugOwner || fiber.return, seen++) {
      if (!where && fiber._debugSource && fiber._debugSource.fileName) {
        where = fiber._debugSource.fileName + ":" + fiber._debugSource.lineNumber;
      }
      var name = componentName(fiber.type);
      if (name && names.indexOf(name) === -1) names.push(name);
    }
    if (!names.length && !where) return "";
    return "React component " + names.join(" < ") + (where ? " (" + where + ")" : "");
  }

  function vueHint(node) {
    var comp = node.__vueParentComponent, parts = [];
    for (var hops = 0; comp && hops < 3; comp = comp.parent, hops++) {
      var type = comp.type || {};
      var label = type.__file || type.__name || type.name;
      if (label && parts.indexOf(label) === -1) parts.push(label);
    }
    if (!parts.length && node.__vue__ && node.__vue__.$options) {
      var options = node.__vue__.$options;
      if (options.__file || options.name) parts.push(options.__file || options.name);
    }
    return parts.length ? "Vue component " + parts.join(" < ") : "";
  }

  function sourceHint(el) {
    var found = [];
    function add(text) { if (text && found.indexOf(text) === -1) found.push(text); }
    try {
      var depth = 0;
      for (var node = el; node && node.nodeType === 1 && depth < 8; node = node.parentElement, depth++) {
        for (var i = 0; i < STAMPS.length; i++) {
          var value = node.getAttribute(STAMPS[i]);
          if (!value) continue;
          var line = STAMPS[i] === "data-astro-source-file" ? node.getAttribute("data-astro-source-loc") : "";
          add(STAMPS[i] + "=" + value + (line ? ":" + line : ""));
        }
        var meta = node.__svelte_meta;
        if (meta && meta.loc && meta.loc.file) add("Svelte " + meta.loc.file + ":" + (meta.loc.line || ""));
        if (depth === 0 || !found.length) { add(reactHint(node)); add(vueHint(node)); }
        if (window.ng && typeof window.ng.getComponent === "function") {
          var instance = window.ng.getComponent(node);
          if (instance && instance.constructor && instance.constructor.name) add("Angular component " + instance.constructor.name);
        }
        if (found.length >= 3) break;
      }
    } catch (e) { /* a hint is optional; never let it break selecting */ }
    return clip(found.join("; "), 600);
  }

  function describe(el) {
    return {
      path: location.pathname,
      selector: selectorFor(el),
      tag: el.localName,
      text: clip((el.textContent || "").trim(), 500),
      html: clip(el.outerHTML, 4000),
      hint: sourceHint(el)      // not `source`: that names the sender of every message
    };
  }

  function hasOwnText(el) {
    for (var i = 0; i < el.childNodes.length; i++) {
      var node = el.childNodes[i];
      if (node.nodeType === 3 && node.nodeValue.trim()) return true;
    }
    return false;
  }

  // ---- hover outline ---------------------------------------------------------------------
  function ensureChrome() {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.setAttribute("data-studio-bridge", "overlay");
      overlay.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;display:none;" +
        "box-sizing:border-box;border:2px solid #3b82f6;background:rgba(59,130,246,.10);border-radius:3px;";
      document.documentElement.appendChild(overlay);
    }
    if (!style) {
      style = document.createElement("style");
      style.setAttribute("data-studio-bridge", "style");
      document.documentElement.appendChild(style);
    }
  }

  function outline(el) {
    if (!overlay) return;
    if (!el || isOwn(el)) { overlay.style.display = "none"; return; }
    var box = el.getBoundingClientRect();
    overlay.style.display = "block";
    overlay.style.left = box.left + "px";
    overlay.style.top = box.top + "px";
    overlay.style.width = box.width + "px";
    overlay.style.height = box.height + "px";
  }

  // ---- edit mode -------------------------------------------------------------------------
  function startEdit(el) {
    finishEdit(true);
    editing = { el: el, before: (el.textContent || "").trim(), html: el.innerHTML,
                attr: el.getAttribute("contenteditable") };
    el.setAttribute("contenteditable", "plaintext-only");
    if (!el.isContentEditable) el.setAttribute("contenteditable", "true");
    el.style.outline = "2px solid #3b82f6";
    el.style.outlineOffset = "2px";
    el.addEventListener("blur", onEditBlur, true);
    el.addEventListener("keydown", onEditKey, true);
    el.focus();
  }

  function finishEdit(commit) {
    if (!editing) return;
    var session = editing;
    var el = session.el;
    editing = null;
    el.removeEventListener("blur", onEditBlur, true);
    el.removeEventListener("keydown", onEditKey, true);
    if (session.attr === null) el.removeAttribute("contenteditable");
    else el.setAttribute("contenteditable", session.attr);
    el.style.outline = "";
    el.style.outlineOffset = "";
    var after = (el.textContent || "").trim();
    if (!commit) { el.innerHTML = session.html; return; }
    if (after !== session.before) {
      post("text-edit", { path: location.pathname, selector: selectorFor(el), tag: el.tagName.toLowerCase(),
                          before_text: session.before, after_text: after });
    }
  }

  function onEditBlur() { finishEdit(true); }

  function onEditKey(event) {
    event.stopPropagation();
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.target.blur(); }
    if (event.key === "Escape") { event.preventDefault(); finishEdit(false); }
  }

  // ---- draw mode -------------------------------------------------------------------------
  // The person draws on the page; every mark is tied to the element under it (as fractions of
  // that element's box), so it stays with its content through scrolling and reflow. Boxes and
  // moves are layout instructions for the agent: nothing on the page is actually changed.
  var draw = { layer: null, svg: null, tool: "pen", color: "#ef4444", marks: [], current: null,
               pending: null, hover: null, editor: null, frame: 0, timer: null };
  var SVG = "http://www.w3.org/2000/svg";
  var MAX_MARKS = 60, MAX_POINTS = 600;
  var TOOLS = ["pen", "box", "note", "move", "pan"];

  function isDrawn(el) {
    return !!(draw.layer && el && draw.layer.contains(el));
  }

  function isPage(el) {
    return !el || el === document.body || el === document.documentElement;
  }

  function elementAt(x, y) {
    var el = null;
    if (draw.layer) draw.layer.style.display = "none";
    try { el = document.elementFromPoint(x, y); } catch (e) {}
    if (draw.layer) draw.layer.style.display = "";
    return el && el.nodeType === 1 ? el : document.body || document.documentElement;
  }

  function boxOf(points) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (var i = 0; i < points.length; i++) {
      x0 = Math.min(x0, points[i][0]); y0 = Math.min(y0, points[i][1]);
      x1 = Math.max(x1, points[i][0]); y1 = Math.max(y1, points[i][1]);
    }
    return [x0, y0, x1 - x0, y1 - y0];
  }

  // The smallest element that holds most of what was drawn: a circle round a card anchors to
  // the card, not to the word that happens to sit under its centre.
  function anchorFor(box) {
    var el = elementAt(box[0] + box[2] / 2, box[1] + box[3] / 2);
    var area = Math.max(box[2], 1) * Math.max(box[3], 1);
    while (!isPage(el) && el.parentElement) {
      var r = el.getBoundingClientRect();
      var w = Math.min(r.right, box[0] + Math.max(box[2], 1)) - Math.max(r.left, box[0]);
      var h = Math.min(r.bottom, box[1] + Math.max(box[3], 1)) - Math.max(r.top, box[1]);
      if (w > 0 && h > 0 && (w * h) / area >= 0.6) break;
      el = el.parentElement;
    }
    return el || document.body;
  }

  // The heading a mark sits under. For a mark on a big container (a note in the margin of a
  // long report) the container's own first heading is wrong: take the last one above the mark.
  function sectionOf(el, y) {
    var headings = document.querySelectorAll("h1,h2,h3,h4,[role=heading]");
    var before = null, inside = null;
    for (var i = 0; i < headings.length; i++) {
      var heading = headings[i];
      if (el.contains(heading) && heading !== el) {
        if (!inside || heading.getBoundingClientRect().top <= y) inside = heading;
        if (heading.getBoundingClientRect().top > y) break;
      } else if (heading === el || (heading.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) {
        before = heading;
      }
    }
    var found = inside && inside.getBoundingClientRect().top <= y ? inside : (before || inside);
    return found ? clip((found.textContent || "").trim().replace(/\s+/g, " "), 120) : null;
  }

  // A tap in a gutter lands on a page-sized container; a nearby smaller element says more.
  function pointAnchor(x, y) {
    var tries = [[0, 0], [80, 0], [160, 0], [-80, 0], [0, -40], [0, 40]], first = null;
    for (var i = 0; i < tries.length; i++) {
      var el = elementAt(x + tries[i][0], y + tries[i][1]);
      first = first || el;
      if (!isPage(el) && el.getBoundingClientRect().height < window.innerHeight) return el;
    }
    return first;
  }

  function target(el) {
    return { selector: selectorFor(el), tag: el.localName,
             text: clip((el.textContent || "").trim().replace(/\s+/g, " "), 200) };
  }

  // What a circle, scribble or box actually sits on: the elements under a 3 × 3 grid of it.
  function coveredBy(box, anchor) {
    var seen = [], out = [];
    for (var row = 0; row < 3; row++) {
      for (var col = 0; col < 3; col++) {
        var el = elementAt(box[0] + box[2] * (0.2 + 0.3 * col), box[1] + box[3] * (0.2 + 0.3 * row));
        if (el === anchor || isPage(el)) continue;
        if (seen.indexOf(el) !== -1 || !(el.textContent || "").trim() && !el.querySelector("img,svg,canvas")) continue;
        seen.push(el);
        if (out.length < 6) out.push(target(el));
      }
    }
    return out;
  }

  // The thing a person means when they grab at a point: not a word inside a button, nor one
  // path of a chart, but the nearest box-like element.
  function movable(el) {
    if (el && el.ownerSVGElement) el = el.ownerSVGElement;
    while (!isPage(el) && el.parentElement) {
      var r = el.getBoundingClientRect();
      var display = "";
      try { display = getComputedStyle(el).display; } catch (e) {}
      if (display !== "inline" && display !== "contents" && r.width >= 16 && r.height >= 16) break;
      el = el.parentElement;
    }
    // A wrapper that fills its card is the card as far as the person is concerned.
    while (!isPage(el) && !isPage(el.parentElement)) {
      var inner = el.getBoundingClientRect(), outer = el.parentElement.getBoundingClientRect();
      if (inner.width * inner.height < 0.75 * outer.width * outer.height) break;
      el = el.parentElement;
    }
    return el;
  }

  // Where something was dropped: the element there (never the moved one) and on which side.
  function dropTarget(x, y, moved) {
    var stack = [];
    if (draw.layer) draw.layer.style.display = "none";
    try { stack = document.elementsFromPoint(x, y); } catch (e) {}
    if (draw.layer) draw.layer.style.display = "";
    var el = null;
    for (var i = 0; i < stack.length; i++) {
      if (stack[i] !== moved && !moved.contains(stack[i])) { el = stack[i]; break; }
    }
    el = el || document.body;
    var out = target(el), r = el.getBoundingClientRect();
    var dx = (x - (r.left + r.width / 2)) / Math.max(r.width / 2, 1);
    var dy = (y - (r.top + r.height / 2)) / Math.max(r.height / 2, 1);
    if (el.contains(moved) || isPage(el) || (Math.abs(dx) < 0.35 && Math.abs(dy) < 0.35)) out.side = "inside";
    else if (Math.abs(dy) >= Math.abs(dx)) out.side = dy < 0 ? "above" : "below";
    else out.side = dx < 0 ? "left" : "right";
    return out;
  }

  function shapeOf(points) {
    var length = 0;
    for (var i = 1; i < points.length; i++) {
      length += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    }
    var box = boxOf(points), diagonal = Math.hypot(box[2], box[3]);
    var last = points[points.length - 1];
    var gap = Math.hypot(last[0] - points[0][0], last[1] - points[0][1]);
    if (length < 12) return "dot";
    if (length < gap * 1.3) return "line";
    if (gap < diagonal * 0.4 && length > diagonal * 1.8) return "loop";
    return "scribble";
  }

  // points: viewport coordinates at the moment of drawing. kind: stroke, note, box or move.
  function addMark(kind, points, text, moved) {
    if (draw.marks.length >= MAX_MARKS) return;
    while (points.length > MAX_POINTS) points = points.filter(function (_p, i) { return i % 2 === 0; });
    var box = boxOf(points);
    var el = kind === "move" ? moved
      : kind === "note" || points.length === 1 ? pointAnchor(points[0][0], points[0][1]) : anchorFor(box);
    var r = el.getBoundingClientRect(), w = Math.max(r.width, 1), h = Math.max(r.height, 1);
    var mark = { kind: kind, color: draw.color, el: el, text: text == null ? null : text,
                 // A mark on the page itself (an empty page, a gutter) keeps plain document coordinates.
                 free: isPage(el),
                 rel: points.map(function (p) { return [(p[0] - r.left) / w, (p[1] - r.top) / h]; }),
                 doc: points.map(function (p) { return [p[0] + window.scrollX, p[1] + window.scrollY]; }),
                 shape: kind === "stroke" ? shapeOf(points) : null, from: null, to: null,
                 section: sectionOf(el, box[1]), covers: [] };
    if (mark.shape === "line") {
      var a = elementAt(points[0][0], points[0][1]);
      var b = elementAt(points[points.length - 1][0], points[points.length - 1][1]);
      if (a !== b) { mark.from = target(a); mark.to = target(b); }
    }
    if (kind === "box" || (kind === "stroke" && mark.shape !== "line" && mark.shape !== "dot")) {
      mark.covers = coveredBy(box, el);
    }
    if (kind === "move") mark.to = dropTarget(points[1][0], points[1][1], el);
    draw.marks.push(mark);
    changed();
  }

  function anchored(mark) {
    return !mark.free && mark.el && mark.el.isConnected;
  }

  // Where a mark is now, in viewport coordinates.
  function place(mark) {
    if (anchored(mark)) {
      var r = mark.el.getBoundingClientRect(), w = Math.max(r.width, 1), h = Math.max(r.height, 1);
      if (r.width || r.height) {
        return mark.rel.map(function (p) { return [r.left + p[0] * w, r.top + p[1] * h]; });
      }
    }
    return mark.doc.map(function (p) { return [p[0] - window.scrollX, p[1] - window.scrollY]; });
  }

  // Where a moved element would sit: its own box shifted by the drag.
  function ghostOf(el, points) {
    var r = el.getBoundingClientRect();
    return [r.left + points[1][0] - points[0][0], r.top + points[1][1] - points[0][1], r.width, r.height];
  }

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG, name);
    for (var key in attrs) el.setAttribute(key, attrs[key]);
    draw.svg.appendChild(el);
    return el;
  }

  function pathData(points) {
    var d = "";
    for (var i = 0; i < points.length; i++) {
      d += (i ? "L" : "M") + points[i][0].toFixed(1) + " " + points[i][1].toFixed(1);
    }
    return points.length === 1 ? d + "l0.1 0" : d;
  }

  function paintStroke(points, color) {
    svgEl("path", { d: pathData(points), fill: "none", stroke: "#fff", "stroke-width": 7,
      "stroke-opacity": 0.75, "stroke-linecap": "round", "stroke-linejoin": "round" });
    svgEl("path", { d: pathData(points), fill: "none", stroke: color, "stroke-width": 3.5,
      "stroke-linecap": "round", "stroke-linejoin": "round" });
  }

  function paintRect(box, color, filled) {
    svgEl("rect", { x: box[0], y: box[1], width: Math.max(box[2], 1), height: Math.max(box[3], 1), rx: 4,
      fill: filled ? color : "none", "fill-opacity": 0.12, stroke: "#fff", "stroke-width": 5, "stroke-opacity": 0.7 });
    svgEl("rect", { x: box[0], y: box[1], width: Math.max(box[2], 1), height: Math.max(box[3], 1), rx: 4,
      fill: "none", stroke: color, "stroke-width": 2.5, "stroke-dasharray": "8 5" });
  }

  function paintArrow(a, b, color) {
    var angle = Math.atan2(b[1] - a[1], b[0] - a[0]), size = 13;
    paintStroke([a, b], color);
    var left = [b[0] - size * Math.cos(angle - 0.45), b[1] - size * Math.sin(angle - 0.45)];
    var right = [b[0] - size * Math.cos(angle + 0.45), b[1] - size * Math.sin(angle + 0.45)];
    svgEl("path", { d: pathData([left, b, right]) + "Z", fill: color, stroke: "#fff", "stroke-width": 1.5,
      "stroke-linejoin": "round" });
  }

  function paintBadge(x, y, n, color) {
    x = Math.max(11, x); y = Math.max(11, y);
    svgEl("circle", { cx: x, cy: y, r: 10, fill: color, stroke: "#fff", "stroke-width": 2 });
    svgEl("text", { x: x, y: y + 4, "text-anchor": "middle", fill: "#fff", "font-size": 12,
      "font-weight": 700, "font-family": "system-ui, sans-serif" }).textContent = String(n);
  }

  function paintLabel(x, y, text, color, note) {
    var label = document.createElement("div");
    label.setAttribute("data-studio-note", "");
    label.style.cssText = "position:absolute;max-width:220px;border-radius:8px;white-space:pre-wrap;" +
      "font:600 13px/1.35 system-ui,sans-serif;pointer-events:none;left:" + x + "px;top:" + y + "px;" +
      (note ? "padding:5px 8px 5px 26px;background:#fffbe6;color:#1f2328;box-shadow:0 2px 8px rgba(0,0,0,.3);" +
              "border:2px solid " + color + ";"
            : "padding:2px 8px;margin:6px 0 0 20px;background:" + color + ";color:#fff;");
    label.textContent = text;
    draw.layer.appendChild(label);
  }

  function paintMove(el, points, color) {
    var r = el.getBoundingClientRect();
    paintRect([r.left, r.top, r.width, r.height], color, false);
    paintRect(ghostOf(el, points), color, true);
    paintArrow(points[0], points[1], color);
  }

  function paint() {
    draw.frame = 0;
    if (!draw.layer || mode !== "draw") return;
    while (draw.svg.firstChild) draw.svg.removeChild(draw.svg.firstChild);
    var labels = draw.layer.querySelectorAll("[data-studio-note]");
    for (var k = 0; k < labels.length; k++) labels[k].remove();
    draw.marks.forEach(function (mark, index) {
      var points = place(mark), box = boxOf(points);
      if (mark.kind === "note") {
        paintLabel(points[0][0], points[0][1], mark.text, mark.color, true);
        paintBadge(points[0][0] + 2, points[0][1] + 2, index + 1, mark.color);
      } else if (mark.kind === "box") {
        paintRect(box, mark.color, true);
        if (mark.text) paintLabel(box[0], box[1], mark.text, mark.color, false);
        paintBadge(box[0], box[1], index + 1, mark.color);
      } else if (mark.kind === "move") {
        if (mark.el.isConnected) paintMove(mark.el, points, mark.color);
        else paintArrow(points[0], points[1], mark.color);
        paintBadge(points[0][0], points[0][1], index + 1, mark.color);
      } else {
        paintStroke(points, mark.color);
        paintBadge(box[0] - 4, box[1] - 4, index + 1, mark.color);
      }
    });
    var now = draw.current;
    if (now && now.tool === "pen") paintStroke(now.points, draw.color);
    if (now && now.tool === "box") paintRect(boxOf(now.points), draw.color, true);
    if (now && now.tool === "move") paintMove(now.el, now.points, draw.color);
    if (draw.pending) paintRect(boxOf(draw.pending), draw.color, true);
    if (!now && draw.tool === "move" && draw.hover && draw.hover.isConnected) {
      var h = draw.hover.getBoundingClientRect();
      svgEl("rect", { x: h.left, y: h.top, width: h.width, height: h.height, rx: 3, fill: "#3b82f6",
        "fill-opacity": 0.1, stroke: "#3b82f6", "stroke-width": 2 });
    }
  }

  function repaint() {
    if (!draw.frame) draw.frame = requestAnimationFrame(paint);
  }

  function round(n) { return Math.round(n); }

  function toDoc(box) {
    return [round(box[0] + window.scrollX), round(box[1] + window.scrollY), round(box[2]), round(box[3])];
  }

  function serialise() {
    var root = document.documentElement;
    return {
      path: location.pathname + location.search + location.hash,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      dark: !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches),
      page: { width: root.scrollWidth, height: root.scrollHeight },
      marks: draw.marks.map(function (mark, index) {
        var live = place(mark);
        var points = live.map(function (p) { return [round(p[0] + window.scrollX), round(p[1] + window.scrollY)]; });
        var el = mark.el.isConnected ? mark.el : document.body;
        var anchor = target(el), r = el.getBoundingClientRect();
        anchor.rect = toDoc([r.left, r.top, r.width, r.height]);
        return { n: index + 1, kind: mark.kind, shape: mark.shape, color: mark.color, text: mark.text,
                 points: points, box: boxOf(points),
                 rel: anchored(mark) ? mark.rel.map(function (p) { return [+p[0].toFixed(4), +p[1].toFixed(4)]; }) : [],
                 anchor: anchor, section: mark.section, from: mark.from, to: mark.to, covers: mark.covers,
                 ghost: mark.kind === "move" ? toDoc(ghostOf(el, live)) : null };
      })
    };
  }

  function changed() {
    repaint();
    post("drawing", { count: draw.marks.length, drawing: draw.marks.length ? serialise() : null });
  }

  // One small text box for notes and box labels. done(text) decides what a kept entry becomes.
  function closeEditor(keep) {
    var editor = draw.editor;
    if (!editor) return;
    draw.editor = null;
    var text = clip(editor.value.trim(), 500);
    var done = editor.__done;
    editor.remove();
    draw.pending = null;
    if (keep) done(text);
    repaint();
  }

  function openEditor(x, y, placeholder, done) {
    closeEditor(true);
    var editor = document.createElement("textarea");
    editor.rows = 2;
    editor.placeholder = placeholder;
    editor.__done = done;
    editor.style.cssText = "position:absolute;width:200px;padding:6px 8px;border-radius:8px;resize:none;" +
      "background:#fffbe6;color:#1f2328;font:600 16px/1.35 system-ui,sans-serif;outline:none;" +
      "box-shadow:0 2px 10px rgba(0,0,0,.35);pointer-events:auto;border:2px solid " + draw.color + ";" +
      "left:" + Math.max(4, Math.min(x, window.innerWidth - 216)) + "px;" +
      "top:" + Math.max(4, Math.min(y, window.innerHeight - 70)) + "px;";
    editor.addEventListener("keydown", function (event) {
      event.stopPropagation();
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); closeEditor(true); }
      if (event.key === "Escape") { event.preventDefault(); closeEditor(false); }
    });
    editor.addEventListener("blur", function () { closeEditor(true); });
    draw.layer.appendChild(editor);
    draw.editor = editor;
    editor.focus();
  }

  function onDrawDown(event) {
    if (event.target === draw.editor) return;
    if (draw.editor) { closeEditor(true); draw.closedAt = Date.now(); return; }
    if (draw.tool === "note") return;       // placed on click, so the keyboard opens on touch devices
    if (event.button) return;
    var at = [event.clientX, event.clientY], now = null;
    if (draw.tool === "pen") now = { tool: "pen", points: [at] };
    if (draw.tool === "box") now = { tool: "box", points: [at, at] };
    if (draw.tool === "move") {
      var el = movable(elementAt(at[0], at[1]));
      if (!isPage(el)) now = { tool: "move", el: el, points: [at, at] };
    }
    if (!now) return;
    event.preventDefault();
    try { draw.layer.setPointerCapture(event.pointerId); } catch (e) {}
    draw.current = now;
    draw.hover = null;
    repaint();
  }

  function onDrawMove(event) {
    var now = draw.current;
    if (!now) {
      if (draw.tool === "move" && event.pointerType === "mouse") {
        var over = movable(elementAt(event.clientX, event.clientY));
        over = isPage(over) ? null : over;
        if (over !== draw.hover) { draw.hover = over; repaint(); }
      }
      return;
    }
    if (now.tool !== "pen") {
      now.points = [now.points[0], [event.clientX, event.clientY]];
      repaint();
      return;
    }
    var batch = event.getCoalescedEvents ? event.getCoalescedEvents() : [event];
    if (!batch.length) batch = [event];
    for (var i = 0; i < batch.length; i++) {
      var last = now.points[now.points.length - 1];
      if (Math.hypot(batch[i].clientX - last[0], batch[i].clientY - last[1]) >= 3) {
        now.points.push([batch[i].clientX, batch[i].clientY]);
      }
    }
    repaint();
  }

  function onDrawUp() {
    var now = draw.current;
    if (!now) return;
    draw.current = null;
    var points = now.points, box = boxOf(points);
    if (now.tool === "pen") { addMark("stroke", points, null); return; }
    if (box[2] < 8 && box[3] < 8) { repaint(); return; }     // a stray tap, not a box or a move
    if (now.tool === "move") { addMark("move", points, null, now.el); return; }
    var corners = [[box[0], box[1]], [box[0] + box[2], box[1] + box[3]]];
    draw.pending = corners;
    repaint();
    openEditor(box[0] + 6, box[1] + 6, "What goes here?", function (text) { addMark("box", corners, text); });
  }

  function onDrawClick(event) {
    if (draw.tool !== "note" || event.target === draw.editor) return;
    if (Date.now() - (draw.closedAt || 0) < 500) return;   // that tap only finished the previous note
    var at = [event.clientX, event.clientY];
    openEditor(at[0], at[1], "Note", function (text) { if (text) addMark("note", [at], text); });
  }

  function ensureLayer() {
    if (draw.layer) return;
    var layer = document.createElement("div");
    layer.setAttribute("data-studio-bridge", "draw");
    layer.style.cssText = "position:fixed;inset:0;z-index:2147483646;overflow:hidden;";
    draw.layer = layer;
    draw.svg = document.createElementNS(SVG, "svg");
    draw.svg.setAttribute("width", "100%");
    draw.svg.setAttribute("height", "100%");
    draw.svg.style.cssText = "position:absolute;inset:0;pointer-events:none;";
    layer.appendChild(draw.svg);
    layer.addEventListener("pointerdown", onDrawDown);
    layer.addEventListener("pointermove", onDrawMove);
    layer.addEventListener("pointerup", onDrawUp);
    layer.addEventListener("pointercancel", onDrawUp);
    layer.addEventListener("pointerleave", function () { if (draw.hover) { draw.hover = null; repaint(); } });
    layer.addEventListener("click", onDrawClick);
    document.documentElement.appendChild(layer);
    window.addEventListener("scroll", repaint, true);
    window.addEventListener("resize", repaint);
  }

  function applyDrawTool() {
    if (!draw.layer) return;
    var passive = draw.tool === "pan";
    draw.layer.style.pointerEvents = passive ? "none" : "auto";
    draw.layer.style.touchAction = passive ? "auto" : "none";
    draw.layer.style.cursor = draw.tool === "note" ? "text" : draw.tool === "move" ? "grab" : "crosshair";
    draw.hover = null;
    repaint();
  }

  function showDraw(on) {
    if (!on) {
      if (draw.layer) { closeEditor(true); draw.current = null; draw.layer.style.display = "none"; }
      if (draw.timer) { clearInterval(draw.timer); draw.timer = null; }
      return;
    }
    ensureLayer();
    draw.layer.style.display = "";
    applyDrawTool();
    // Charts and images that finish loading move things without a scroll or resize event.
    if (!draw.timer) draw.timer = setInterval(function () { if (draw.marks.length) repaint(); }, 400);
  }

  function onDrawMessage(data) {
    if (data.type === "draw") {
      if (TOOLS.indexOf(data.tool) !== -1 && data.tool !== draw.tool) {
        closeEditor(true);
        draw.current = null;
        draw.tool = data.tool;
      }
      if (typeof data.color === "string" && data.color.length <= 32) draw.color = data.color;
      applyDrawTool();
    } else if (data.type === "draw-undo") {
      closeEditor(false);
      if (draw.marks.pop()) changed();
    } else if (data.type === "draw-clear") {
      closeEditor(false);
      draw.marks = [];
      changed();
    }
  }

  // ---- a picture opens large (CONTRACT.md, "A picture in the page opens large") ------------
  // An "Enlarge" button on a diagram, chart or image; pressing it hands the picture to the shell,
  // which shows it over the whole studio. Off until the shell asks (a thumbnail never does).
  var large = { on: false, touch: false, chips: [], timer: null, scanAt: 0 };
  try { large.touch = !!(window.matchMedia && window.matchMedia("(hover: none)").matches); } catch (e) {}
  var MIN_W = 160, MIN_H = 100, MAX_CHIPS = 6, MAX_SVG_TEXT = 8000000, MAX_SVG_NODES = 4000;
  var DOES_SOMETHING = "a[href],button,label,summary,[role=button],[contenteditable=''],[contenteditable=true]";
  var SVG_STYLES = ["fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray",
                    "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "opacity", "color", "display", "visibility",
                    "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "text-anchor",
                    "dominant-baseline", "text-decoration", "paint-order", "stop-color", "stop-opacity", "mix-blend-mode"];
  var CHIP_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ' +
    'style="display:block;flex:none"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>';

  function isChip(el) {
    for (var i = 0; i < large.chips.length; i++) {
      if (large.chips[i].el === el || large.chips[i].el.contains(el)) return true;
    }
    return false;
  }

  function chipFor(el) {
    for (var i = 0; i < large.chips.length; i++) {
      if (large.chips[i].el === el || large.chips[i].el.contains(el)) return large.chips[i];
    }
    return null;
  }

  // Big enough to be worth opening, visible, and not something that already acts when pressed.
  function isPicture(pic) {
    if (!pic || !pic.isConnected || isDrawn(pic)) return false;
    var box = pic.getBoundingClientRect();
    if (box.width < MIN_W || box.height < MIN_H) return false;
    if (pic.localName === "img" && !(pic.complete && pic.naturalWidth > 0)) return false;
    if (pic.closest && pic.closest(DOES_SOMETHING)) return false;
    var shown = getComputedStyle(pic);
    return shown.visibility !== "hidden" && shown.display !== "none" && +shown.opacity > 0.05;
  }

  // The picture an element belongs to: itself (an image, a canvas) or the outermost <svg> around it.
  function pictureOf(el) {
    if (!el || el.nodeType !== 1 || isOwn(el)) return null;
    var pic = el;
    if (el.localName !== "img" && el.localName !== "canvas") {
      pic = el.closest ? el.closest("svg") : null;
      while (pic && pic.ownerSVGElement) pic = pic.ownerSVGElement;
    }
    return isPicture(pic) ? pic : null;
  }

  function makeChip(pic) {
    var el = document.createElement("button");
    el.type = "button";
    el.setAttribute("data-studio-bridge", "enlarge");
    el.setAttribute("aria-label", "Enlarge this picture");
    // `all:initial` first: whatever the page says about buttons does not reach this one.
    el.style.cssText = "all:initial;position:fixed;z-index:2147483645;display:none;align-items:center;gap:6px;" +
      "box-sizing:border-box;height:" + (large.touch ? 34 : 30) + "px;padding:0 10px;border-radius:9px;cursor:pointer;" +
      "font:600 12px/1 system-ui,-apple-system,'Segoe UI',sans-serif;color:#fff;background:rgba(17,24,39,.8);" +
      "border:1px solid rgba(255,255,255,.3);box-shadow:0 2px 10px rgba(0,0,0,.28);white-space:nowrap;" +
      "-webkit-tap-highlight-color:transparent;-webkit-user-select:none;user-select:none;";
    el.innerHTML = CHIP_ICON + (large.touch ? "" : '<span style="all:unset">Enlarge</span>');
    document.documentElement.appendChild(el);
    return { el: el, pic: pic, said: null };
  }

  function placeChips() {
    for (var i = 0; i < large.chips.length; i++) {
      var chip = large.chips[i], box = chip.pic.isConnected ? chip.pic.getBoundingClientRect() : null;
      var seen = box && isPicture(chip.pic) && box.bottom > 70 && box.top < window.innerHeight - 40 &&
        box.right > 90 && box.left < window.innerWidth - 40;
      if (!seen) { chip.el.style.display = "none"; continue; }
      chip.el.style.display = "inline-flex";
      // The top left corner: the studio's chat floats over the right side of the page.
      var width = chip.el.offsetWidth || 90;
      chip.el.style.top = Math.max(box.top, 0) + 8 + "px";
      chip.el.style.left = Math.min(Math.max(box.left, 0) + 8, window.innerWidth - 8 - width) + "px";
    }
  }

  // The pictures that get a button now: `wanted`, in order. Buttons already there are kept.
  function setChips(wanted) {
    var kept = [];
    for (var i = 0; i < large.chips.length; i++) {
      var at = wanted.indexOf(large.chips[i].pic);
      if (at === -1 && !large.chips[i].said) large.chips[i].el.remove();
      else { kept.push(large.chips[i]); if (at !== -1) wanted[at] = null; }
    }
    for (var j = 0; j < wanted.length; j++) if (wanted[j]) kept.push(makeChip(wanted[j]));
    large.chips = kept;
    placeChips();
  }

  // A touch screen has no hover: the pictures in view carry the button.
  function scanPictures() {
    if (!large.on || mode !== "browse" || !large.touch) return;
    var all = document.querySelectorAll("img,canvas,svg"), found = [];
    for (var i = 0; i < all.length && i < 600 && found.length < MAX_CHIPS; i++) {
      var pic = all[i];
      if (pic.localName === "svg" && pic.ownerSVGElement) continue;
      if (isOwn(pic) || !isPicture(pic)) continue;
      var box = pic.getBoundingClientRect();
      if (box.bottom > 70 && box.top < window.innerHeight - 40) found.push(pic);
    }
    setChips(found);
  }

  function hoverPicture(target) {
    if (!large.on || mode !== "browse" || large.touch || isChip(target)) return;
    var pic = pictureOf(target);
    if (large.chips.length === 1 && large.chips[0].pic === pic) return;
    setChips(pic ? [pic] : []);
  }

  function refreshLarge() {
    if (!large.on || mode !== "browse") {
      setChips([]);
      if (large.timer) { clearInterval(large.timer); large.timer = null; }
      return;
    }
    if (large.touch) {
      scanPictures();
      // Pictures that finish loading, and pages that draw their charts late, bring no event.
      if (!large.timer) large.timer = setInterval(scanPictures, 1200);
    } else placeChips();
  }

  function onLargeScroll() {
    if (!large.on || mode !== "browse") return;
    placeChips();
    if (!large.touch) return;
    var now = Date.now();
    if (now - large.scanAt < 150) return;
    large.scanAt = now;
    scanPictures();
  }

  // The colour the picture sits on, so a drawing made for a dark page is shown on dark.
  function backgroundBehind(el) {
    for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
      var colour = getComputedStyle(node).backgroundColor || "";
      var parts = /^rgba?\(([^)]+)\)$/.exec(colour);
      if (!parts) continue;
      var values = parts[1].split(/[\s,\/]+/).filter(Boolean);
      var alpha = values.length > 3 ? parseFloat(values[3]) * (/%$/.test(values[3]) ? 0.01 : 1) : 1;
      if (alpha >= 0.5) return "rgb(" + values.slice(0, 3).join(", ") + ")";
    }
    return "rgb(255, 255, 255)";
  }

  function copyStyles(from, to) {
    if (!to.style) return;
    var shown = getComputedStyle(from);
    for (var i = 0; i < SVG_STYLES.length; i++) {
      var value = shown.getPropertyValue(SVG_STYLES[i]);
      if (value) to.style.setProperty(SVG_STYLES[i], value);
    }
    // Only a transform the page's CSS gave: one written as an attribute is in the copy already.
    var transform = shown.getPropertyValue("transform");
    if (transform && transform !== "none" && !from.getAttribute("transform")) {
      to.style.setProperty("transform", transform);
      to.style.setProperty("transform-origin", shown.getPropertyValue("transform-origin"));
      to.style.setProperty("transform-box", shown.getPropertyValue("transform-box"));
    }
  }

  // An inline SVG as a file of its own: a copy that carries the styles the page gave it.
  function svgFile(svg, box) {
    var clone = svg.cloneNode(true);
    var from = svg.querySelectorAll("*"), to = clone.querySelectorAll("*");
    if (from.length === to.length && from.length <= MAX_SVG_NODES) {
      copyStyles(svg, clone);
      for (var i = 0; i < from.length; i++) copyStyles(from[i], to[i]);
    }
    var scripts = clone.querySelectorAll("script");
    for (var s = 0; s < scripts.length; s++) scripts[s].remove();
    var width = Math.round(box.width), height = Math.round(box.height);
    if (!clone.getAttribute("viewBox")) clone.setAttribute("viewBox", "0 0 " + width + " " + height);
    clone.setAttribute("width", width);
    clone.setAttribute("height", height);
    clone.style.width = width + "px";
    clone.style.height = height + "px";
    clone.style.maxWidth = "none";
    clone.style.maxHeight = "none";
    clone.style.margin = "0";
    clone.style.position = "static";
    var text = new XMLSerializer().serializeToString(clone);
    return text.length <= MAX_SVG_TEXT ? text : null;
  }

  function sayOnChip(chip, text) {
    if (chip.said) clearTimeout(chip.said);
    var label = chip.el.querySelector("span");
    if (!label) { label = document.createElement("span"); label.style.cssText = "all:unset"; chip.el.appendChild(label); }
    label.textContent = text;
    placeChips();
    chip.said = setTimeout(function () {
      chip.said = null;
      if (large.touch) label.remove(); else label.textContent = "Enlarge";
      refreshLarge();
    }, 2600);
  }

  function sendLarge(chip) {
    var pic = chip.pic;
    if (!pic.isConnected) return;
    var box = pic.getBoundingClientRect();
    var fields = { alt: clip(pic.getAttribute("alt") || pic.getAttribute("aria-label") || "", 300),
                   background: backgroundBehind(pic) };
    function done(blob, url, width, height) {
      if (!blob && !url) { sayOnChip(chip, "Can’t enlarge this one"); return; }
      fields.width = Math.round(width);
      fields.height = Math.round(height);
      if (blob) fields.blob = blob; else fields.url = url;
      post("enlarge", fields);
    }
    if (pic.localName === "canvas") {
      try { pic.toBlob(function (blob) { done(blob, null, box.width, box.height); }, "image/png"); }
      catch (e) { done(null, null, 0, 0); }       // a canvas the page may not read
      return;
    }
    if (pic.localName === "img") {
      var src = pic.currentSrc || pic.src || "";
      var width = pic.naturalWidth || box.width, height = pic.naturalHeight || box.height;
      var web = /^https?:/i.test(src) ? src : null;
      var same = false;
      try { same = new URL(src, location.href).origin === location.origin; } catch (e) {}
      if (!window.fetch || !src) { done(null, web, width, height); return; }
      fetch(src, { credentials: same ? "same-origin" : "omit" })
        .then(function (response) { if (!response.ok) throw new Error("status"); return response.blob(); })
        .then(function (blob) {
          // The shell takes only files that say they are pictures; otherwise it gets the address.
          if (/^image\//i.test(blob.type)) done(blob, null, width, height); else done(null, web, width, height);
        }, function () { done(null, web, width, height); });
      return;
    }
    var text = null;
    try { text = svgFile(pic, box); } catch (e) {}
    done(text ? new Blob([text], { type: "image/svg+xml" }) : null, null, box.width, box.height);
  }

  // ---- capture-phase listeners (page handlers never see events we consume) -----------------
  function swallow(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  function onMove(event) {
    if (mode === "select") outline(event.target);
    else if (mode === "browse") hoverPicture(event.target);
  }

  function onClick(event) {
    var el = event.target;
    var chip = el && el.nodeType === 1 ? chipFor(el) : null;
    if (chip) {                       // the studio's own button: the page never hears of it
      swallow(event);
      if (event.type === "click" && mode === "browse") sendLarge(chip);
      return;
    }
    if (mode === "browse" || mode === "draw" || !el || el.nodeType !== 1 || isOwn(el)) return;
    if (mode === "select") {
      swallow(event);
      outline(el);   // a tap has no hover: show what was picked
      post("select", describe(el));
      return;
    }
    if (editing && editing.el.contains(el)) return;   // caret placement inside the edited text
    swallow(event);
    if (hasOwnText(el) && el !== document.body && el !== document.documentElement) startEdit(el);
    else finishEdit(true);
  }

  function onPointer(event) {
    if (isChip(event.target)) {
      // Hidden from the page, never cancelled: cancelling a touch would cancel its click too.
      event.stopPropagation();
      event.stopImmediatePropagation();
      return;
    }
    if (mode === "browse" || mode === "draw" || isOwn(event.target)) return;
    if (mode === "edit" && editing && editing.el.contains(event.target)) return;
    if (mode === "edit" && event.type === "mousedown" && editing) return;  // let blur commit the edit
    var touch = event.type === "touchstart" || event.type === "touchend" ||
      ((event.type === "pointerdown" || event.type === "pointerup") && event.pointerType === "touch");
    if (touch) {
      // Cancelling a touch would also cancel the click the browser makes from it (so a tap
      // could never select) and would stop the page scrolling. Hide it from the page only.
      event.stopPropagation();
      event.stopImmediatePropagation();
      return;
    }
    swallow(event);
  }

  function setMode(next) {
    if (next !== "browse" && next !== "select" && next !== "edit" && next !== "draw") return;
    finishEdit(true);
    mode = next;
    showDraw(mode === "draw");
    refreshLarge();
    if (mode === "browse" || mode === "draw") {
      if (overlay) overlay.style.display = "none";
      if (style) style.textContent = "";
      return;
    }
    ensureChrome();
    overlay.style.display = "none";
    style.textContent = mode === "select"
      ? "*{cursor:crosshair !important}"
      : "*{cursor:text !important}";
  }

  ["click", "dblclick", "auxclick", "contextmenu"].forEach(function (type) {
    document.addEventListener(type, type === "click" ? onClick : onPointer, true);
  });
  ["mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend", "submit"].forEach(function (type) {
    document.addEventListener(type, onPointer, true);
  });
  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("mouseleave", function (event) {
    outline(null);
    // Heard here for every element the pointer leaves; only leaving the page itself counts
    // (no element the pointer went to: browsers differ on which target they report).
    if (event.target === document || event.relatedTarget == null) hoverPicture(null);
  }, true);
  window.addEventListener("scroll", function () { outline(null); onLargeScroll(); }, true);
  window.addEventListener("resize", onLargeScroll);

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent) return;
    var data = event.data;
    if (!data || data.source !== "studio-shell") return;
    if (data.type === "set-mode") setMode(data.mode);
    else if (data.type === "enlarge-offer") { large.on = true; refreshLarge(); }
    else onDrawMessage(data);
  });

  // ---- navigation ------------------------------------------------------------------------
  // A full page load announces itself with `ready`; in-page navigation sends `navigate`.
  function announceNavigation() {
    post("navigate", { path: location.pathname + location.search + location.hash });
  }
  ["pushState", "replaceState"].forEach(function (name) {
    var original = history[name];
    history[name] = function () {
      var result = original.apply(this, arguments);
      announceNavigation();
      return result;
    };
  });
  window.addEventListener("popstate", announceNavigation);
  window.addEventListener("hashchange", announceNavigation);

  // The shell reloads the frame on every file save; keep the reader's place.
  var scrollKey = "studio-scroll:" + location.pathname;
  function restoreScroll() {
    try {
      var saved = sessionStorage.getItem(scrollKey);
      if (saved) { var p = saved.split(","); window.scrollTo(+p[0] || 0, +p[1] || 0); }
    } catch (e) {}
  }
  var scrollTimer = null;
  window.addEventListener("scroll", function () {
    if (scrollTimer) return;
    scrollTimer = setTimeout(function () {
      scrollTimer = null;
      try { sessionStorage.setItem(scrollKey, window.scrollX + "," + window.scrollY); } catch (e) {}
    }, 100);
  }, { passive: true });

  function ready() {
    restoreScroll();
    window.addEventListener("load", restoreScroll, { once: true });
    // A file dropped on the page would replace the page with that file. Files go on the chat
    // (CONTRACT.md, "Files are dropped on the chat, nowhere else"), so such a drop does nothing
    // here. These run last, on the window: a page with a drop area of its own has handled the
    // event by then and keeps its files.
    function strayFiles(event) {
      var types = event.dataTransfer && event.dataTransfer.types;
      return !event.defaultPrevented && !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
    }
    window.addEventListener("dragover", function (event) {
      if (!strayFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "none";
    });
    window.addEventListener("drop", function (event) {
      if (strayFiles(event)) event.preventDefault();
    });
    window.addEventListener("load", refreshLarge);
    var reloadMeta = document.querySelector('meta[name="studio-reload"]');
    post("ready", { path: location.pathname + location.search + location.hash, title: document.title,
                    auto_reload: !(reloadMeta && String(reloadMeta.content).toLowerCase() === "off") });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
