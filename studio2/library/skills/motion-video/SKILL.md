---
name: motion-video
description: Make a real video file (MP4, GIF or WebM) from an animated page - a product or marketing video, an explainer, an animated chart or title sequence, a social clip in 16:9, 9:16 or 1:1. Use whenever the person asks for a video, a motion graphic, a promo, a reel, an animated intro or "export this as a video".
---

# Video made from a page

A video here is a page whose picture depends only on the time `t`. You build it as a fixed-size
**stage**, look at its frames, and the studio renders it frame by frame into a file
(`render_video`). It is motion graphics: type, shapes, charts, screenshots, pictures and 3D that
you can draw or that the person supplies. It is not filmed footage; say so if that is what was
asked for, and use clips or generated pictures only when the person provides them or a
generation tool is available.

## Where things go
- The stage page: `video/<name>.html`, with `video/stage.js` beside it. Copy the helper, do not
  retype it: `cp ../library/skills/motion-video/stage.js video/stage.js`.
- The file: `media/<name>.mp4` (what `render_video` writes by default), poster `media/<name>.poster.jpg`
  (the `poster` in the tool's answer; an older render's poster may be `media/<name>.jpg`).
- `index.html` (or the section of the report the video belongs to) shows the file.

## Plan before you animate
1. **Format.** 1920×1080 (16:9) unless told otherwise. 1080×1920 for Reels, Shorts, TikTok and
   stories; 1080×1080 or 1080×1350 for a feed post. 30 frames a second.
2. **Length.** 15 to 30 seconds for a promo or social clip, up to 60 to 90 for an explainer.
3. **Shot list.** Write it as a comment at the top of the page: one idea per scene, 2.5 to 5
   seconds each, what is on screen and what moves. A promo usually runs: hook (the problem or
   the claim, 3 s) → what it is → two or three proofs (a figure, a feature, the product on
   screen) → end card with the name and one call to action, held at least 2.5 s.
4. **Content.** Names, claims and figures come from the person or the project's sources. No
   invented statistics, customers, quotes or logos.

## The stage
```html
<div id="stage" data-width="1920" data-height="1080" data-duration="18" data-fps="30">
  <section data-start="0" data-end="4.5" data-out=".4" class="scene">
    <div class="bg drift"></div>
    <h1 class="rise" data-split="words">A bike that pays for itself</h1>
    <p class="wipe" style="--d:1.1s">in four years, not nine</p>
    <svg class="mark" viewBox="0 0 100 100"><circle class="draw" cx="50" cy="50" r="30" style="--d:.4s"/></svg>
  </section>
  <section data-start="4.1" data-end="9" data-out=".4" class="scene">
    <span class="figure" data-count="4200" data-at=".4" data-for="1.4" data-prefix="$">$4,200</span>
    <p class="rise" style="--d:1.2s">saved a year on a typical home</p>
  </section>
</div>
<script src="stage.js"></script>
```
`stage.js` gives you the motion, on the scene's clock, with no keyframes to write:

| Write | Get |
|---|---|
| `<section data-start="4" data-end="9">` | a scene, visible from 4 s to 9 s. `data-out=".4"` fades it out over its last 0.4 s; start the next scene 0.3 to 0.4 s before this one ends for a cross-fade |
| `class="rise"`, `fade`, `pop`, `wipe`, `wipe-up`, `slide-left`, `slide-right` | an entrance. `style="--d:.6s"` starts it 0.6 s into the scene; `--dur` changes its length |
| `data-split="words"` (or `chars`, `lines`) with an entrance class | each word enters in turn (`--gap`, 70 ms by default). The headline of every scene |
| `class="draw"` on an SVG `path`, `line`, `circle`, `rect` | the stroke draws itself: icons, underlines, chart lines, a route on a map |
| `<span data-count="71999" data-at=".5" data-for="1.4" data-locale="en-US" data-prefix="$">$71,999</span>` | counts up to the value (write the real, sourced value as the text too) |
| `class="drift"` on a background layer | a slow push-in, so a held scene is never frozen |

Your own CSS `@keyframes` inside a scene also run on the scene's clock (`animation-delay: 1.2s`
is 1.2 s into the scene; always `both` fill). For what CSS cannot say:
`studioStage.timeline(gsapTimeline)` (a paused GSAP timeline on the stage clock, GSAP from
`https://cdn.jsdelivr.net/npm/gsap@3.15.1/dist/gsap.min.js`), and `studioStage.onFrame(t => …)` to
draw anything from `t` (a canvas, a three.js camera, a chart filling in).
`studioStage.ready(promise)` holds the first frame until a model or picture has loaded.

- A soundtrack the person supplied: `data-audio="media/track.mp3"` on the stage. With none,
  the video is silent; say that in your answer rather than leaving it to be discovered.
- A clip the person supplied or you generated: place `<video src="../media/clip.mp4" data-at="0.5">`
  in a scene (it starts 0.5 s into the scene and is stepped frame by frame). If the frames in
  the check sheet show the clip empty, this machine's browser cannot decode MP4: convert it
  once (`ffmpeg -i in.mp4 -an -c:v libvpx-vp9 -b:v 0 -crf 32 -g 15 video/clip.webm`) and use that.

### Rules that keep the render right
- Everything follows `t`. Do not sequence with `setTimeout`, do not start motion by adding a
  class (a transition), and do not hide scenes with `display: none`; all scenes are in the page
  from the start.
- No `Math.random()` per frame. For particles or noise, seed a small generator once and derive
  positions from `t`.
- Text is real text in web-safe or loaded fonts; wait for a web font with `studioStage.ready(document.fonts.load("700 96px Inter"))`.

## Make it a video, not a slide
The common failure is a small block of text in one corner of a dark gradient. Check each scene
against this list before you render.

- **Fill the frame.** The main element of a scene spans at least 60% of the frame's width or
  height. Compose for the whole rectangle: text one side and a picture the other, a figure
  that fills the frame, a full-bleed colour block that wipes across.
- **Type is large.** At 1080p: headline 120 to 200 px, supporting line 56 to 72 px, the
  smallest text on screen 40 px. Heavy weights. In 9:16 the headline is 110 to 150 px and
  wraps to three or four short lines.
- **Something to look at in every scene** besides words: an SVG illustration of the subject
  that draws itself, a chart that fills in, a number counting up, the product itself (take
  pictures of the person's real page or app with `render_image` and pan or zoom across them),
  a picture the person supplied. Draw simple, confident shapes; no clip-art clutter.
- **Few words.** At most eight on screen per beat. Each line stays up 1.5 s plus 0.3 s a
  word. A list of five things is five beats (one enters, the last dims), or a grid of five
  drawn icons with one word each; never five lines of small text.
- **Change the picture.** No two scenes in a row share a layout. Vary the background (a
  brand colour, its dark, a light scene), the side the text sits on, and the scale.
- **One movement at a time.** Entrances 0.5 to 0.9 s, groups staggered, then hold still so it
  can be read. Keep one direction of travel through the piece. No spinning or bouncing.
- **Margins.** Keep text 96 px inside the frame. In 9:16 keep it out of the top 220 px and the
  bottom 420 px, where the apps draw their own buttons.
- **A vertical version is designed, not shrunk.** Make it its own page: stack what was side by
  side, let the figure or picture take the top half, re-set the type for the narrow frame.
- **Colour.** Take the palette from the person's site or brand (two colours and a neutral).
  Check contrast on every scene.
- **End card.** Name, one line, one call to action, on screen at least 2.5 s, still.

## Look, fix, render
1. `render_video` with `path` and `check: true`. Open the `sheet` with the Read tool: twelve
   frames with their times. Look for text cut off or overlapping, an empty or half-built
   frame, elements that pop in late, a scene that changes too fast to read. Then ask of each
   frame: is more than half of it empty, is any text small, does it look like the frame
   before it? Fix those as well; they are the difference between a slide and a video.
2. Look closer where it matters: `check: true, times: [0.4, 4.2, 9.9, 17.5]` (four or fewer
   frames come back large). Check each scene near its start, where things are mid-entrance,
   and at its end.
3. Fix the page and check again. Two or three rounds is normal.
4. Render: `render_video` with `path` (add `format: "gif"` for a short loop, `scale: 2` for
   4K). Open the sheet it returns as well; that is the file the person gets.
5. Show it: put the returned `embed` in the page under a heading that says what the video is,
   with its length, size and whether it has sound, and a link to the live stage
   (`video/<name>.html`) for anyone who wants to scrub it.
6. In your answer: what you rendered, how long, what you looked at, and what you could not do
   (no soundtrack, no footage).
