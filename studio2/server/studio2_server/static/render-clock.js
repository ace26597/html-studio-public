/* The page's clock while the studio renders it to video (media.py).
   Time stands still until the studio calls step(ms): timers, animation frames, Date,
   performance.now, CSS and Web Animations, SVG animation and <video> all follow that one
   clock, so frame 212 looks the same however long frame 211 took to draw. */
(() => {
  if (window.__studioClock) return;
  const RealDate = Date, epoch = RealDate.now();
  const realTimeout = window.setTimeout.bind(window);
  let now = 0, serial = 1;
  const timers = new Map(), frames = new Map();
  const born = new WeakMap();          // animation or <video> → the clock time it started at; null = the page drives it
  const report = error => realTimeout(() => { throw error; });

  window.performance.now = () => now;
  function FakeDate(...args) {
    if (!new.target) return new RealDate(epoch + now).toString();
    return args.length ? new RealDate(...args) : new RealDate(epoch + now);
  }
  FakeDate.prototype = RealDate.prototype;
  FakeDate.now = () => Math.round(epoch + now);
  FakeDate.parse = RealDate.parse;
  FakeDate.UTC = RealDate.UTC;
  window.Date = FakeDate;

  const add = (fn, delay, args, every) => {
    const id = serial++;
    timers.set(id, { at: now + Math.max(0, Number(delay) || 0), fn, args, every });
    return id;
  };
  window.setTimeout = (fn, delay, ...args) => add(fn, delay, args, 0);
  window.setInterval = (fn, delay, ...args) => add(fn, delay, args, Math.max(1, Number(delay) || 0));
  window.clearTimeout = window.clearInterval = id => { timers.delete(id); };
  window.requestAnimationFrame = fn => { const id = serial++; frames.set(id, fn); return id; };
  window.cancelAnimationFrame = id => { frames.delete(id); };

  function runTimers(until) {
    for (let guard = 0; guard < 50000; guard++) {
      let due = null, dueId = 0;
      for (const [id, timer] of timers) if (timer.at <= until && (!due || timer.at < due.at)) { due = timer; dueId = id; }
      if (!due) return;
      now = Math.max(now, due.at);
      if (due.every) due.at += due.every; else timers.delete(dueId);
      try { typeof due.fn === "function" ? due.fn(...due.args) : (0, eval)(String(due.fn)); } catch (error) { report(error); }
    }
  }
  function runFrames() {
    const batch = [...frames.values()];
    frames.clear();
    for (const fn of batch) { try { fn(now); } catch (error) { report(error); } }
  }
  function syncAnimations() {
    if (!document.getAnimations) return;
    for (const animation of document.getAnimations()) {
      let start = born.get(animation);
      if (start === undefined) {
        // Paused when first seen: the page is scrubbing it itself (the stage does). Leave it.
        start = animation.playState === "paused" ? null : now;
        born.set(animation, start);
        if (start !== null) animation.pause();
      }
      if (start === null) continue;
      const rate = animation.playbackRate > 0 ? animation.playbackRate : 1;
      const at = (now - start) * rate;
      const end = animation.effect ? animation.effect.getComputedTiming().endTime : Infinity;
      if (Number.isFinite(Number(end)) && at >= Number(end)) {
        born.set(animation, null);
        try { animation.finish(); } catch (error) { animation.currentTime = Number(end); }   // fires finish, settles .finished
      } else {
        animation.currentTime = at;
      }
    }
    for (const svg of document.querySelectorAll("svg")) {
      if (svg.ownerSVGElement || !svg.pauseAnimations) continue;
      try { svg.pauseAnimations(); svg.setCurrentTime(now / 1000); } catch (error) { /* not a rendered svg */ }
    }
  }
  function syncVideos() {
    const waits = [];
    for (const video of document.querySelectorAll("video")) {
      let start = born.get(video);
      if (start === undefined) {
        if (typeof video.__studioStart === "number") start = video.__studioStart;      // the stage placed it on the timeline
        else if (video.autoplay || !video.paused) start = now;
        else continue;                                                                  // not playing yet: look again next frame
        born.set(video, start);
        video.pause();
        video.muted = true;
      }
      if (typeof video.__studioStart === "number") start = video.__studioStart;
      let at = (now - start) / 1000 * (video.playbackRate || 1);
      const length = Number.isFinite(video.duration) ? video.duration : 0;
      if (at < 0) at = 0;
      if (length) at = video.loop ? at % length : Math.min(at, Math.max(0, length - 0.001));
      if (video.readyState < 1 || Math.abs(video.currentTime - at) < 0.004) continue;
      // "seeked" says the position changed; the picture is only on screen once the new frame has
      // been presented, which is what requestVideoFrameCallback reports.
      waits.push(new Promise(done => {
        let over = false;
        const finish = () => { if (!over) { over = true; done(); } };
        if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(() => realTimeout(finish, 0));
        else video.addEventListener("seeked", () => realTimeout(finish, 30), { once: true });
        realTimeout(finish, 800);
        video.currentTime = at;
      }));
    }
    return waits;
  }

  window.__studioClock = {
    get now() { return now; },
    /* Run what is due without moving time (used while the page loads). */
    flush() { runTimers(now); runFrames(); },
    /* Move to `ms`, run everything that falls due, then ask the page to draw that moment. */
    async step(ms) {
      const to = Math.max(now, Number(ms) || 0);
      runTimers(to);
      now = to;
      runFrames();
      syncAnimations();
      const video = window.studioVideo;
      if (video && typeof video.seek === "function") {
        try { await video.seek(now / 1000); } catch (error) { report(error); }
      }
      const waits = syncVideos();
      if (waits.length) await Promise.all(waits);
      await new Promise(done => realTimeout(done, 0));     // let style and layout settle before the picture
      return now;
    },
  };
})();
