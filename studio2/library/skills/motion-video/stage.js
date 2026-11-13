/* stage.js: a fixed-size stage that plays like a video and renders to one.

   <div id="stage" data-width="1920" data-height="1080" data-duration="24" data-fps="30"> … </div>
   <script src="stage.js"></script>

   The picture depends only on the time t (seconds). Three ways to say what happens when:
     1. Scenes:  <section data-start="6" data-end="12"> is visible from 6 s to 12 s, and every CSS
        animation inside it runs on the scene's clock (animation-delay: 1s = one second into the scene).
        CSS animations outside a scene run on the stage's clock.
     2. studioStage.timeline(tl): a paused GSAP timeline (or anything with .time(seconds)).
     3. studioStage.onFrame(t => { … }): draw t yourself (canvas, three.js, numbers counting up).
   studioStage.ready(promise) holds the first frame until something has loaded (a model, an image).
   <video data-at="1.5"> inside a scene starts 1.5 s into it. data-audio on the stage names the soundtrack.

   Built in, so a scene needs no keyframes of its own (all run on the scene's clock):
     class="rise | fade | pop | wipe | wipe-up | slide-left | slide-right"   entrances; style="--d:.6s" delays one
     class="draw" on an SVG path, line or circle                        the stroke draws itself (--dur to slow it)
     data-split="words" (or "chars", "lines") + an entrance class        each piece enters in turn (--gap, default 70ms)
     <span data-count="71999" data-at=".5" data-for="1.4" data-locale="en-IN" data-prefix="₹">   counts up to its value
     data-out=".4" on a scene                                            fades the scene out over its last 0.4 s
     class="drift" on a background element                               a slow 6% push-in across the scene

   In a browser it is a player (space, arrows, the bar; ?t=3.5 opens paused at 3.5 s). The
   studio's render_video tool steps it frame by frame instead. */
(() => {
  const el = document.querySelector("[data-stage]") || document.getElementById("stage");
  if (!el || window.studioStage) { if (!el) console.error('stage.js: the page has no element with id="stage".'); return; }
  const number = (key, fallback) => { const v = parseFloat(el.dataset[key]); return Number.isFinite(v) && v > 0 ? v : fallback; };
  const W = number("width", 1920), H = number("height", 1080), FPS = number("fps", 30), DURATION = number("duration", 10);
  const rendering = !!window.__studioRender;
  const BAR = rendering ? 0 : 56;
  const timelines = [], drawers = [], waits = [];
  let scenes = [], animations = [], videos = [], counters = [], sound = null;
  let t = 0, playing = false, last = 0, started = false, bar = null;

  const style = document.createElement("style");
  style.textContent = `html,body{margin:0;height:100%;overflow:hidden;background:#0c0d10}
    #${el.id || "stage"},[data-stage]{position:absolute;left:0;top:0;width:${W}px;height:${H}px;overflow:hidden;transform-origin:0 0}
    [data-stage] .rise,#stage .rise{animation:studio-rise var(--dur,.8s) cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .fade,#stage .fade{animation:studio-fade var(--dur,.6s) ease-out both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .pop,#stage .pop{animation:studio-pop var(--dur,.6s) cubic-bezier(.2,1.3,.3,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .wipe,#stage .wipe{animation:studio-wipe var(--dur,.8s) cubic-bezier(.7,0,.2,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .wipe-up,#stage .wipe-up{animation:studio-wipe-up var(--dur,.8s) cubic-bezier(.7,0,.2,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .slide-left,#stage .slide-left{animation:studio-slide-left var(--dur,.8s) cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .slide-right,#stage .slide-right{animation:studio-slide-right var(--dur,.8s) cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--d,0s) + var(--i,0) * var(--gap,70ms))}
    [data-stage] .draw,#stage .draw{stroke-dasharray:1;stroke-dashoffset:1;animation:studio-draw var(--dur,1.4s) cubic-bezier(.6,0,.2,1) both;animation-delay:var(--d,0s)}
    [data-stage] .drift,#stage .drift{animation:studio-drift var(--dur,8s) linear both}
    .studio-piece{display:inline-block;white-space:pre}
    .studio-line{display:block}
    @keyframes studio-rise{from{opacity:0;transform:translateY(.6em)}}
    @keyframes studio-fade{from{opacity:0}}
    @keyframes studio-pop{from{opacity:0;transform:scale(.6)}}
    @keyframes studio-wipe{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
    @keyframes studio-wipe-up{from{clip-path:inset(100% 0 0 0)}to{clip-path:inset(0 0 0 0)}}
    @keyframes studio-slide-left{from{opacity:0;transform:translateX(12%)}}
    @keyframes studio-slide-right{from{opacity:0;transform:translateX(-12%)}}
    @keyframes studio-draw{to{stroke-dashoffset:0}}
    @keyframes studio-drift{from{transform:scale(1)}to{transform:scale(1.06)}}
    .studio-stage-bar{position:fixed;left:0;right:0;bottom:0;height:${BAR}px;display:flex;align-items:center;gap:14px;
      padding:0 16px;box-sizing:border-box;background:#15171c;color:#e7e9ee;font:14px/1 system-ui,sans-serif;z-index:2147483000}
    .studio-stage-bar button{all:unset;cursor:pointer;width:36px;height:36px;border-radius:8px;display:grid;place-items:center;font-size:16px;background:#262a33}
    .studio-stage-bar button:focus-visible{outline:2px solid #8ab4ff}
    .studio-stage-bar input[type=range]{flex:1;min-width:60px;accent-color:#8ab4ff}
    .studio-stage-bar .time{font-variant-numeric:tabular-nums;white-space:nowrap}
    .studio-stage-bar label{display:flex;align-items:center;gap:6px;opacity:.8;white-space:nowrap}`;
  document.head.appendChild(style);

  function fit() {
    if (rendering) { el.style.transform = "none"; return; }
    const scale = Math.min(window.innerWidth / W, (window.innerHeight - BAR) / H);
    el.style.transform = `translate(${(window.innerWidth - W * scale) / 2}px, ${(window.innerHeight - BAR - H * scale) / 2}px) scale(${scale})`;
  }
  /* data-split: wrap each word, character or line in a span that carries its index (--i), and
     move the entrance class onto the pieces so they come in one after another. */
  const ENTRANCES = ["rise", "fade", "pop", "wipe", "wipe-up", "slide-left", "slide-right"];
  function split(node) {
    const mode = node.dataset.split, moved = ENTRANCES.filter(name => node.classList.contains(name));
    node.classList.remove(...moved);
    node.setAttribute("aria-label", node.textContent.trim());
    let index = 0;
    const piece = (text, extra) => {
      const span = document.createElement("span");
      span.className = ["studio-piece", extra, ...moved].filter(Boolean).join(" ");
      span.style.setProperty("--i", String(index++));
      span.textContent = text;
      return span;
    };
    const walk = parent => {
      for (const child of [...parent.childNodes]) {
        if (child.nodeType === 1) { walk(child); continue; }
        if (child.nodeType !== 3 || !child.textContent.trim()) continue;
        const parts = mode === "chars" ? [...child.textContent]
                    : mode === "lines" ? child.textContent.split("\n").map(line => line.trim()).filter(Boolean)
                    : child.textContent.split(/(\s+)/).filter(Boolean);
        const out = document.createDocumentFragment();
        for (const part of parts) out.appendChild(/^\s+$/.test(part) ? document.createTextNode(" ") : piece(part, mode === "lines" ? "studio-line" : ""));
        child.replaceWith(out);
      }
    };
    walk(node);
  }
  const easeOut = x => 1 - Math.pow(1 - Math.max(0, Math.min(1, x)), 3);
  const clock = s => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  const sceneOf = node => scenes.find(scene => scene.el.contains(node));

  function seek(time) {
    t = Math.max(0, Math.min(DURATION, Number(time) || 0));
    for (const scene of scenes) {
      const on = t >= scene.start && (t < scene.end || (scene.end >= DURATION && t <= scene.end));
      scene.el.style.visibility = on ? "visible" : "hidden";
      if (scene.out) scene.el.style.opacity = String(Math.max(0, Math.min(1, (scene.end - t) / scene.out)));
    }
    for (const counter of counters) {
      const amount = counter.from + (counter.to - counter.from) * easeOut((t - counter.start) / counter.span);
      const shown = counter.format.format(counter.decimals ? amount : Math.round(amount));
      const text = counter.prefix + shown + counter.suffix;
      if (counter.el.textContent !== text) counter.el.textContent = text;
    }
    for (const [animation, offset] of animations) animation.currentTime = Math.max(0, (t - offset) * 1000);
    for (const timeline of timelines) (timeline.time ? timeline.time(t) : timeline.seek(t));
    el.style.setProperty("--t", t.toFixed(4));
    for (const draw of drawers) draw(t);
    if (!rendering) {
      for (const [video, from] of videos) {
        const at = Math.max(0, t - from), on = t >= from && at < (video.duration || Infinity);
        if (!playing || !on) { if (!video.paused) video.pause(); if (video.readyState) video.currentTime = Math.min(at, video.duration || at); }
        else { if (Math.abs(video.currentTime - at) > 0.25) video.currentTime = at; if (video.paused) video.play().catch(() => {}); }
      }
      if (sound) {
        if (!playing) sound.pause();
        else { if (Math.abs(sound.currentTime - t) > 0.25) sound.currentTime = t; if (sound.paused) sound.play().catch(() => {}); }
      }
      if (bar) { bar.range.value = String(t); bar.time.textContent = `${clock(t)} / ${clock(DURATION)}`; bar.button.textContent = playing ? "❚❚" : "▶"; }
      try { sessionStorage.setItem("studioStage", JSON.stringify({ t, playing })); } catch (error) { /* private window */ }
    }
  }
  function tick(now) {
    if (!playing) return;
    let next = t + Math.min(0.25, (now - last) / 1000);
    last = now;
    if (next >= DURATION) { if (bar && bar.loop.checked) next = 0; else { next = DURATION; playing = false; } }
    seek(next);
    if (playing) requestAnimationFrame(tick);
  }
  function play() { if (rendering || playing) return; if (t >= DURATION) t = 0; playing = true; last = performance.now(); requestAnimationFrame(tick); seek(t); }
  function pause() { playing = false; seek(t); }

  function controls() {
    const root = document.createElement("div");
    root.className = "studio-stage-bar";
    root.innerHTML = `<button type="button" aria-label="Play or pause">▶</button>
      <input type="range" min="0" max="${DURATION}" step="${1 / FPS}" value="0" aria-label="Position">
      <span class="time"></span><label><input type="checkbox"> Loop</label>
      <span style="opacity:.6;white-space:nowrap">${W}×${H} · ${FPS} fps</span>`;
    document.body.appendChild(root);
    bar = { button: root.querySelector("button"), range: root.querySelector("input[type=range]"),
            time: root.querySelector(".time"), loop: root.querySelector("input[type=checkbox]") };
    bar.button.addEventListener("click", () => (playing ? pause() : play()));
    bar.range.addEventListener("input", () => { playing = false; seek(parseFloat(bar.range.value)); });
    window.addEventListener("keydown", event => {
      if (event.target instanceof HTMLInputElement && event.target.type !== "range") return;
      if (event.code === "Space") { event.preventDefault(); playing ? pause() : play(); }
      else if (event.code === "ArrowRight") { playing = false; seek(t + (event.shiftKey ? 1 : 1 / FPS)); }
      else if (event.code === "ArrowLeft") { playing = false; seek(t - (event.shiftKey ? 1 : 1 / FPS)); }
      else if (event.code === "Home") { playing = false; seek(0); }
    });
  }

  for (const node of el.querySelectorAll("[data-split]")) split(node);

  const loaded = new Promise(done => (document.readyState === "complete" ? done() : window.addEventListener("load", done, { once: true })));
  const ready = (async () => {
    await loaded;
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    for (let index = 0; index < waits.length; index++) await waits[index];     // more may be added while waiting
    scenes = [...el.querySelectorAll("[data-start]")].filter(node => node.tagName !== "VIDEO").map(node => {
      const start = parseFloat(node.dataset.start) || 0;
      const end = node.dataset.end !== undefined ? parseFloat(node.dataset.end)
                : node.dataset.duration !== undefined ? start + parseFloat(node.dataset.duration) : DURATION;
      return { el: node, start, end, out: parseFloat(node.dataset.out) || 0 };
    });
    for (const path of el.querySelectorAll("svg .draw")) path.setAttribute("pathLength", "1");
    counters = [...el.querySelectorAll("[data-count]")].map(node => {
      const scene = sceneOf(node), to = parseFloat(node.dataset.count) || 0;
      const decimals = (String(node.dataset.count).split(".")[1] || "").length;
      return { el: node, to, from: parseFloat(node.dataset.from) || 0, decimals,
               start: (scene ? scene.start : 0) + (parseFloat(node.dataset.at) || 0), span: parseFloat(node.dataset.for) || 1.2,
               prefix: node.dataset.prefix || "", suffix: node.dataset.suffix || "",
               format: new Intl.NumberFormat(node.dataset.locale || "en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) };
    });
    animations = (document.getAnimations ? document.getAnimations() : []).map(animation => {
      const target = animation.effect && animation.effect.target, scene = target && sceneOf(target);
      animation.pause();
      return [animation, scene ? scene.start : 0];
    });
    videos = [...el.querySelectorAll("video")].map(video => {
      const scene = sceneOf(video), from = (scene ? scene.start : 0) + (parseFloat(video.dataset.at) || 0);
      video.__studioStart = from * 1000;       // the renderer's clock reads this
      video.pause(); video.muted = true; video.playsInline = true;
      return [video, from];
    });
    if (!rendering && el.dataset.audio) { sound = new Audio(el.dataset.audio); sound.preload = "auto"; }
    fit();
    started = true;
    if (!rendering) {
      controls();
      window.addEventListener("resize", fit);
      const asked = parseFloat(new URLSearchParams(location.search).get("t"));
      let saved = null;
      try { saved = JSON.parse(sessionStorage.getItem("studioStage") || "null"); } catch (error) { /* ignore */ }
      if (Number.isFinite(asked)) seek(asked);
      else if (saved && saved.playing === false) seek(saved.t || 0);
      else { seek(saved && saved.t < DURATION ? saved.t : 0); play(); }
    } else seek(0);
  })();

  window.studioStage = {
    width: W, height: H, fps: FPS, duration: DURATION,
    get time() { return t; },
    timeline(timeline) { if (timeline.pause) timeline.pause(); timelines.push(timeline); if (started) seek(t); return timeline; },
    onFrame(draw) { drawers.push(draw); if (started) seek(t); },
    ready(promise) { waits.push(Promise.resolve(promise)); },
    seek, play, pause,
  };
  window.studioVideo = { width: W, height: H, fps: FPS, duration: DURATION, ready, seek,
                         audio: el.dataset.audio || null, poster: el.dataset.poster ? parseFloat(el.dataset.poster) : null };
})();
