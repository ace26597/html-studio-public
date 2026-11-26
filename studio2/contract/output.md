# Contract: Output and checking

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

# Office documents

The agent makes PowerPoint, Word and Excel files with ordinary libraries, as project files
(by convention under `files/`, built by a script under `tools/`). The studio's part is
`render_document`, which makes such a file visible: to the agent, so it can check its own
work, and to the person, inside the page.

`render_document(path)` writes `<path>.preview/` next to the file:

| File type | What is written | What the tool returns |
|---|---|---|
| `.pptx` `.ppt` `.odp`, `.docx` `.doc` `.rtf` `.odt` | `document.pdf`, `page-<n>.png` (1600 px wide for slides, 1240 px for pages, at most 60 a call: `pages` and the rest in "Very large documents"), `index.html` | `{file, kind, pdf, pages: [path], page_count, preview, embed, next}` |
| `.pdf` | `page-<n>.png`, `index.html` | the same, with `pdf` the file itself |
| `.xlsx` `.xlsm` `.xls` `.ods` | `index.html` | `{file, kind, sheets: [{name, rows, columns, cut, formulas, unchecked, errors: [{cell, formula, value}], error_count, start: [text rows]}], formula_results_stored, preview, embed, next}` |

`kind` is `presentation`, `document`, `workbook` or `pdf`. `index.html` is a self-contained
viewer (slides with a film strip, pages in a scroll, or sheets as tables with a formula bar),
with Download and, where there is one, Open PDF. It needs nothing from the studio, so it works
in a downloaded project. `embed` is an `<iframe>` for it.

For a workbook every formula is calculated (LibreOffice, set to recalculate on load) and the
results are compared cell by cell: a formula whose result is an error value or empty is listed
under `errors` (at most 30 in the tool's answer; `error_count` is how many there are). The
viewer and `start` show at most the first 400 rows and 50 columns of a sheet (`cut` is true
when the sheet is bigger), but the error check covers every formula in the workbook (array
formulas and data tables included), up to 200,000 formulas; `unchecked` counts a sheet's formulas past that bound, and `next` then says
how many were checked and how many were not instead of saying there are no errors. `next` also
says which sheets' previews are cut. With `store_results` (the default) the results are also written into the
`.xlsx` beside their formulas, because a file written by a library holds formulas without
results and shows empty cells in anything that does not calculate (a phone's file preview).
Nothing else in the file is touched.

Rendering uses a LibreOffice that is already installed (`STUDIO2_SOFFICE`, the macOS
application, or `soffice` on the path), with a profile of its own. Without one the tool is
listed as unavailable with the reason, and the agent is told it cannot render Office files.


---

# Video and images from a page

Motion, 3D and navigable graphics are ordinary pages: the agent writes them with CSS, the Web
Animations API, GSAP, three.js, D3 and the like, and the person uses them in the preview. The
studio's part is to turn such a page into files that can leave the studio: a video, a GIF, a
picture.

## `render_video`

A video is a page whose picture depends only on time. `render_video(path)` loads that page in
the studio's headless browser with the page's clock replaced by one the studio steps: for
each frame it moves the clock forward by `1/fps`, runs the timers and animation frames that
fall due, sets every CSS animation, transition, Web Animation, SVG animation and `<video>` to
that moment, takes a lossless picture, and passes it to ffmpeg. Nothing is recorded in real
time, so a frame that takes a second to draw costs render time and never a dropped frame.
`Date`, `performance.now`, `setTimeout`, `setInterval` and `requestAnimationFrame` all follow
the stepped clock; `Math.random` is left alone, so a page that wants the same picture on every
render seeds its own.

The page may declare itself, and arguments override what it declares:

```js
window.studioVideo = { width: 1920, height: 1080, fps: 30, duration: 24,   // seconds
                       seek(t) { /* draw second t */ },   // optional; may return a promise
                       ready,                              // optional promise: models, images loaded
                       audio: "media/track.mp3",           // optional soundtrack, a project file
                       poster: 9.5 };                      // optional: the second used as the poster
```

While a page loads for rendering its timers do not run (time stands at zero), so `ready` must
settle from load events, not from a timeout; a page that is not ready after 20 seconds fails
with that explanation. `window.__studioRender` is `true` in the renderer, for a page that
wants to hide its player controls.

| Argument | Default | Meaning |
|---|---|---|
| `path` | | The page, a project file (`video/launch.html`) |
| `out` | `media/<page name>.<format>` | Where the file goes |
| `format` | `mp4` | `mp4` (H.264, yuv420p, BT.709, faststart: plays on phones, in browsers and in editors), `gif` (at most 800 px wide and 15 frames a second) or `webm` (VP9) |
| `width`, `height` | 1920 × 1080 | The picture in CSS pixels; even numbers. `scale: 2` doubles the output (3840 × 2160). At most 3840 × 2160 in all |
| `fps` | 30 | 1 to 60 |
| `duration`, `start` | declared; 0 | Seconds. At most 180. Required when the page declares none |
| `audio`, `volume` | declared; 1 | A sound file of the project, cut to the video's length with a one-second fade out |
| `check` | false | Take only the contact sheet, encode nothing: the quick way to see the motion |
| `times`, `frames` | 12 frames, evenly spaced | Which moments the contact sheet shows: `times: [3.2, 7]` (up to 24; four or fewer are drawn large), or a count |
| `poster_at` | declared, or 40% in | The second saved as the poster picture |

It answers `{page, width, height, fps, duration, frames, declared_by_page, driven_by_seek,
file, bytes, sound, poster, embed, sheet, sheet_times, seconds_taken, console,
failed_requests, next}`. `sheet` is a contact sheet (a grid of frames, each labelled with its
time) saved outside the project, which the agent opens to see its own motion; `poster` is
`<name>.poster.jpg` beside the file (`media/intro.mp4` → `media/intro.poster.jpg`), a name that
cannot replace a picture of the project called `intro.jpg`; a video rendered before this has its
poster at `<name>.jpg`, which is left as it is and still works wherever a page names it; `embed` is a `<video>` (or `<img>` for a GIF) with a download
link. With `check` there is no `file`, `poster` or `embed`. A render runs for at most 15
minutes and one runs at a time. On this Mac a 12-second 1920 × 1080 video takes about 18
seconds.

`<video>` elements inside the page are stepped to the frame (the picture is taken only after
the new frame is on screen). Which clips the studio's browser can decode depends on the
machine: on a Mac it plays MP4 (H.264) and WebM; a Chromium built without H.264 (most Linux
builds) plays only WebM (VP9, AV1), and there a clip is converted to WebM first.

The `motion-video` skill carries `stage.js`, which the agent copies into the project: a
fixed-size stage with scenes (`data-start`, `data-end`) whose CSS animations run on the
scene's clock, `studioStage.timeline(gsapTimeline)`, `studioStage.onFrame(t => …)`, and a
player (play, scrub, loop, `?t=3.5`) for the preview. It sets `window.studioVideo`.

## `render_image`

`render_image(path, out)` saves a picture of a project page into the project: the whole page
at `width` (default 1200), exactly `width` × `height` when both are given, or one element
(`selector`). `scale` 1 to 3 multiplies the pixels; `transparent` leaves out the page
background (PNG and WebP); `dark` asks for the dark colour scheme; `at` steps an animated
page's clock to that second first. Answers `{file, width, height, bytes, console, next}`.

## Serving

A project's static files are served with byte ranges (`Accept-Ranges: bytes`, 206 answers),
on the project URL and under `/__studio/files/`, because a video player seeks with them and
Safari does not play a video without them. An open-ended range is answered 8 MB at a time.

Both tools use the cached Chromium that `look` uses; `render_video` also needs an ffmpeg that
is already installed (`STUDIO2_FFMPEG`, or `ffmpeg` on the path). Without them the tool is
listed as unavailable with the reason. On a Mac the browser draws WebGL with the graphics
chip (`--use-angle=metal`); elsewhere with the software renderer, which is correct but slow.



---


# Generated media

Photographs, footage, music and speech cannot be drawn by code. For those the studio can ask a
generation service. There is one provider: the Higgsfield command-line tool (`higgsfield`, or
`STUDIO2_HIGGSFIELD`), signed in on this machine as the person. Without it both tools are
listed as unavailable.

Every generation spends the person's credits, so:

1. `generate_media` first asks the service for the price of exactly this request (free).
2. It refuses when the price is more than the credits left, or would take the studio past
   `STUDIO2_GENERATE_DAILY_CREDITS` (default 100) for the day, across all projects.
3. It asks the person, through the running turn, **every time and whatever the project's
   `approvals` setting**: an `approval_request` with `tool: "generate_media"`, a title that
   carries the price ("Generate a picture for 6.5 credits"), the prompt, model, settings and
   target file as `detail`, and the reason "It spends 6.5 of your 372.3 Higgsfield credits."
   Denied, or no turn running: nothing is spent and the tool says so.
4. One job runs at a time. Just before buying, with no other job running, the studio checks
   again that generation is not paused, that the credits left still cover the price, and that
   the price still fits under the daily limit given what was spent since the quote; two quotes
   approved together cannot pass the limit between them, and a pause that began after the quote
   is honoured. A refusal here spends nothing and says so. The result is downloaded into the
   project at `out` (the extension follows what the service returned) and the spend (the fall
   in the balance) is appended to `data/studio2/generation-usage.jsonl` with the project, model,
   prompt and outcome. A job the service made but the studio could not download or save still
   counts: it is recorded with outcome `download_failed` or `save_failed` and the credits it
   cost (the fall in the balance, or the quoted price when the balance cannot be read), and the
   tool says the credits were spent. A job the studio stopped waiting for is recorded as
   `timed_out` at its quoted price (`credits_basis: "quoted"`), because the service may still
   finish it and charge. A balance that rose while a job ran (a refund, the monthly credits) is
   no measure of the job: the quoted price is recorded, never a negative amount. A job that did
   not complete is recorded with whatever the balance fell by, usually nothing.
5. **A server fault stops everything.** If the service fails with a 5xx error, generation is
   paused for six hours (recorded in `data/studio2/generation.json`) and the tool tells the
   agent not to try another model: such a failure can charge credits and deliver nothing, and
   a retry usually repeats it. Any other failure (a refused prompt, bad settings) is returned
   as it is, with the instruction not to switch models.

`kind` is `image` (default model `gpt_image_2`), `video` (`seedance_2_0`) or `audio` (no
default: the agent picks one with `generation_models`). `aspect_ratio`, `resolution`,
`quality` and `duration` are passed when given; `params` carries anything else the model
takes. `references`, `start_image` and `end_image` are project files the service is given.
It answers `{file, kind, model, credits_spent, credits_left, width, height, next}`.

`generation_models(kind?)` lists `{model, name, kind}` with the defaults, the credits left,
what the studio spent today and the daily limit; `generation_models(model)` returns that
model's parameters.

| Route | Answer |
|---|---|
| `GET /api/generation` | `{available, reason, paused, spent_today, daily_limit}`; `paused` is the explanation while a server fault holds generation off, else null |
| `DELETE /api/generation/pause` | 204. Lifts the pause |

The agent is told that a generated picture is an illustration: it is labelled as generated
where it is shown and is never presented as a photograph of the person's real product, place,
team or customers.


# How much to do, and checking what is on the page (depth and verify)

The person says how much a request should do (**depth**) and whether the result is checked
before the request ends (**verify**). Both are remembered per project and sent with each request.

## REST

- `POST /api/projects/{id}/turns` takes `options: {depth?: "quick"|"standard"|"deep", verify?: boolean,
  check_only?: boolean, polish?: boolean}`. Unknown keys or values → 400 `invalid_options`. Left out:
  `standard`, no verify.
  `check_only: true` marks a request that only verifies the page's content (the **Verify content**
  button): it implies `verify: true`, and the studio picks no skills or connectors by itself for it (see
  "Verification" below). `check_only: false` is the same as leaving it out.
  `polish: true` marks a request about how the page looks (the **Polish the look** button, see
  "Polishing the look" below). `polish: false` is the same as leaving it out; `check_only` and
  `polish` together → 400 `invalid_options`.
- `PATCH /api/projects/{id}` takes `turn_options` (`{depth?, verify?}`, same 400; `check_only` and
  `polish` are properties of one request and are refused here): the project's remembered choice, kept in the
  project record as `turn_options` (`{depth, verify}`; absent = standard, off). Both take `involve`
  too ("How much to involve the person"), and the project the API returns always has all three keys.
  A request's own `options` are not saved by sending it; the UI saves the choice when it changes.
- `turn_started` carries `options` (`{depth, verify}`, always both keys, plus `check_only: true`
  only for a check-only request and `polish: true` only for a polish request). `turn_queued` carries the options of the queued message, and they
  are the ones it runs with.

## What the agent receives

The options become one `<studio-effort>` block in the composed message, after the text and the
project facts. The wording lives in one place, `server/studio2_server/effort.py`. `standard` with
verify off adds nothing: the message is byte for byte what it was before this step. The wording
describes intent and never promises a length in words or pages.

With `depth: "deep"` the picker also adds the studio's `deep-research` skill to the request
(`how: "depth"`), unless the person switched it off in the project.

## Verification

With verify on, the agent checks everything the person can see on the project's page(s), and the
figures in the project's Office and PDF files ("Verify content reaches files"), after its work, through the `checker` sub-agent (an independent context), fixes what is certainly wrong and
lists the rest, then calls the studio tool **`report_verification`** once:

```
{checked, passed, fixed, failed, unverifiable: integers ≥ 0,
 summary?: string (≤ 400 chars),
 findings: [{what (≤ 200), where (≤ 200), status: "passed"|"fixed"|"failed"|"unverifiable", note (≤ 400)}]}
```

At most 60 findings are kept (failed first, then unverifiable, fixed, passed); counts are kept as
given. It writes one event:

```json
{"type": "verification", "checked": 12, "passed": 9, "fixed": 1, "failed": 1, "unverifiable": 1,
 "summary": "…", "findings": [{"what": "…", "where": "…", "status": "failed", "note": "…"}],
 "findings_total": 4, "findings_cut": 0}
```

A second call in the same request replaces nothing: it writes a second event (the UI shows the
last). If verify was on and the request ends `completed` without a `verification` event, the studio
writes `notice {level: "warning", message: "Verification was asked for and was not reported."}`
before `turn_finished`. Never a silent pass.

What a pass means, said wherever the result is shown: the figure was found in the saved source or
recomputed from the page's own data. It does not prove the source is right. Unverifiable items are
counted, not hidden.

The wording tells the agent to run `check_page` again after its fixes and before reporting, to
report an item as fixed only when that re-check passes, and not to restate the counts in its
closing message (the card has them; the message points to it).

### The studio's own re-check

When a request wrote at least one `verification` event and was not stopped, the studio itself runs the page check
once more after the agent has finished (before the version is saved), on `index.html` when it
exists and on every other page `check_page` was run on during the request. It does not write
`checks/<page>.json` (the agent's last check stays the saved one) and it never sends the agent
back. It runs `check_file` on files too, and the event's entries say which are pages and which are
files: "Verify content reaches files". It writes one event:

```json
{"type": "verification_recheck",
 "pages": [{"page": "index.html", "checked": 37, "verified": 31, "derived": 4, "failed": 2,
            "cited_not_listed": 2, "source_list": true, "error": null}],
 "problems": [{"page": "index.html", "what": "$4.2 billion", "where": "Revenue",
               "why": "The quote is not in the saved text of s3."},
              {"page": "index.html", "what": "Source s27", "where": "",
               "why": "Cited on the page but has no entry in the page's source list."}],
 "problems_total": 4, "problems_cut": 0, "agrees": false}
```

`problems` are the check's failed items and its cited-but-not-listed sources (at most 40 kept,
`problems_cut` counts the rest); `agrees` is true when there are none and at least one page was
read, and no page or file was left out past the caps (`not_rechecked`, "Very large documents"). A page (or file) the check could not read has `error` set and adds no problems. No event when there is no page
or file to check. What the re-check covers is only what the page check and the file check can see:
marked figures against saved sources and project data, cited sources against the source list, and
the figures in the project's Office and PDF files against checked page figures, data and saved
sources. It knows nothing of charts, totals that are not marked, or an app's behaviour; the card
says so.

`check_only` requests: the picker adds only what the person chose (skills and connectors they
said "always" for, here or for the machine). Nothing is added by itself: no `all`, `earlier` or
`picked` skills and no `requested` or `picked` connectors. `library_picked` then has `mode: "none"`
and a `note` saying so.

## Polishing the look

Verification is about what the page says. **Polish the look** is about how it looks and reads: a
request with `polish: true` adds one more part to the `<studio-effort>` block (the wording is in
`effort.py`, like the rest). The agent is told to:

1. look at every page with `look_at_page` at 1440 px wide first (a desktop, where the page is
   mostly read and has to look best), then 820 (a tablet), then 390 (a phone), once more at 1440
   with `dark` when `layout.dark_styles` is true, and at an app's main states;
2. open every tile of each look and read its `layout` list, confirming a measured item in the
   tiles before acting on it;
3. fix what is clearly a defect (text cut off, overlapping or off the screen, a page that scrolls
   sideways, broken or missing spacing, unreadable type, a layout that wastes or overfills a
   desktop window) with the smallest change and in the page's own style, never by hiding content.
   The desktop view comes first and the tablet second; on a phone it fixes what is broken and
   never trades the desktop view for it (the owner, 18 November 2026: the studio is used on a
   desktop, sometimes an iPad, rarely a phone);
4. leave matters of taste alone (another layout, palette or typeface) and offer them as next steps
   instead (`suggest_next_steps`);
5. look again after its fixes, and end with what it fixed, what it looked at and what it could not check.

It adds and removes no content. There is no card and no new event: the answer and the chips carry
the result. The picker works as for any request. When the studio has no browser the agent says it
cannot see the page and changes nothing. A polish request is an ordinary request in every other
way (it can be queued, stopped and undone as a version).

## UI

The settings are in the strip under the composer ("The chat panel on a desktop", at the end):
**Depth** names the depth ("Standard") and opens a menu with the three steps **Quick / Standard /
Deep**, each with a few words on what it means (Deep is slower and costs more of the model
allowance or money); **Verify** is a switch for the content check after each request. The two
things done to the page now are a button in the composer's row, *Check this page now*: **Verify
content**, which sends a request that only verifies (verify on, no new content; it sends
`options: {depth: "standard", verify: true, check_only: true}`), and **Polish the look**, which
sends the polish request (`options: {depth: "standard", verify: false, polish: true}`). The chat shows
a `verification` event as a card: counts first, findings expandable, failed ones first. A
`verification_recheck` belongs to the turn's last card and is shown at the top of it: when it
disagrees, "N items the studio's own check still finds wrong" with each one listed, before the
agent's own counts; when it agrees, one short line. Either way one line says what the re-check
covers. The summary is cut (≤ 400 characters) at a sentence or word, never mid-word, and ends
with "…".

## Ports and processes, cleanly

Every app the studio runs gets its ports back cleanly when it stops, and gets the same ports
again when it starts.

- **`studio stop`** ends the studio and returns only when it has gone: the process has exited and
  the studio's port and its first page port can be bound again (up to 10 s after the stop signal).
  If the process will not go, or something still holds the port, it says so plainly with the pid
  (and exits 1). It says "a request that was still running was ended" only when a project had a
  running request.
- **The free-port test** binds with `SO_REUSEADDR`, so a socket left in TIME_WAIT by a studio that
  just closed does not count as "in use". `studio start` takes a block only when the studio port and
  the first page port are both free, and prints both ranges: pages P+1–P+99, back ends P+101–P+199.
- **Pages follow the studio's port.** At start, a project whose page port is outside the current
  page range gets a new port from the range (its `preview_url` follows) and the studio logs that once
  for the project. A project inside the range keeps its port.
- **An app's stop.** The process group gets SIGTERM, then SIGKILL after 5 s. The studio then checks
  (up to about 2 s) that nothing answers on the app's port. If something still does, the app's log
  gets one line naming the holder (pid and command, found with `lsof` where the computer has it, otherwise with `ss` on Linux).
- **An app's start.** If the app's port is held by something the studio did not just start: when
  that process's working folder is inside the project's folder, it is a leftover of the same app and
  is stopped first. Otherwise the app is not started, its state is `crashed`, and its log says
  "Port N is in use by pid P (command), which the studio did not start." A process outside the
  project's folder is never stopped.
- **Restart keeps the port.** A stop and start, or a restart, comes back on the same port, with the
  earlier process group (children included) gone.
- **Deleting a project frees its ports:** the page listener is closed and the app's group is ended,
  so the next new project can take them.
- **The Claude plan connection** is available only when this computer is signed in to a Claude plan
  (`claude auth status`, the same check as `studio doctor`). Otherwise it is unavailable with
  "No Claude sign-in on this computer. Sign in with `studio login`, or use a key." The answer is
  checked in the background and kept for about a minute; until the first answer arrives the
  connection is unavailable with "Checking the Claude sign-in on this computer." Under
  `STUDIO2_FAKE=1` nothing changes.

# Verify content reaches files

Lane `verify-files`. A wrong number in a slide, a spreadsheet cell, a Word paragraph or a PDF is
found the way a wrong number on the page is: the studio tool `check_file` compares every figure in
the file with what the project can vouch for, the checker runs it, and the studio's own re-check
runs it again. The code is `server/studio2_server/filecheck.py`.

## The tool `check_file {path}`

`path`: a `.pptx`, `.docx`, `.xlsx` or `.pdf` file inside the project (anything else, a missing
file or a path outside the project → a tool error). It reads the file itself; it needs no
LibreOffice and calls no model.

**What is read, and where each figure is said to be (`where`).**

| File | Read with | Read | `where` |
|---|---|---|---|
| `.pptx` | the standard library (the file is a zip of XML) | every slide in the deck's order: each shape's text and each table cell. Not read: speaker notes, charts' own data, slide-number, date and footer placeholders | `Slide 3, title`, `Slide 3, Content 2`, `Slide 3, table row 2, column 3` |
| `.docx` | the standard library | the body: paragraphs and table cells (nested tables too), in order, with the nearest heading (a style named `Title` or `Heading n`, or based on one, or with an outline level). Not read: headers, footers, footnotes, comments | `Paragraph 12 under "Results"`, `Table 2, row 3, column 2 under "Results"` |
| `.xlsx` | openpyxl (in the studio's environment), twice: formulas, and the stored results | every sheet, every cell that is not empty | `Summary!B4 (Revenue, 2025)`: the cell, then the row's label (the nearest text to its left) and the column's heading (the top text above it) when there are any |
| `.pdf` | pypdf (in the studio's environment, as for saved sources) | the text layer, page by page | `Page 3` |

A PDF without a text layer (scanned pictures) and a file that cannot be opened end with `error`
set and nothing checked: never a pass. A workbook cell that holds a **formula** is `derived`: its
value is the result LibreOffice stored (`render_document` stores it, and reports formula errors);
a formula with no stored result is still `derived` and its `note` says to run `render_document`.
A typed-in number, and every figure in a text cell, is compared like any other. A cell's number
is read as the sheet shows it (its number format: `0.123` under `0.0%` is the figure `12.3%`).

**What a figure is.** A number in the text, with its sign, currency (`$ € £ ₹ ¥`, `US$`, `Rs`,
`USD`, `EUR`, `GBP`, `INR`), percent sign and scale (`thousand`, `k`, `million`, `m`, `mn`,
`billion`, `b`, `bn`, `trillion`, `tn`, `lakh`, `crore`, `cr`; single letters only when written
against the number: `4.2m`, `300k`). Thousands separators are dropped (`1,234,567` and the Indian
`12,34,567`); a negative may be written `-1,234`, `−1,234` or `(1,234)`. Its value is the number
times its scale: `$4.2 billion` is 4,200,000,000 shown to a tenth of a billion. Only the comma is
read as a thousands separator: `4.218.950` (read as a version) and `4 218 950` are not one figure.

**What is not a figure to check** (it is skipped; `summary.skipped` counts the numbers skipped in
the text that was read, so superscript text and the placeholders that are not read are not in it):

- years standing alone (four digits from 1900 to 2100 with no separator, sign, currency, percent
  or scale: `in 2025`; `2,025 units` and `$2025` are figures), year ranges (`2024–25`) and
  fiscal years (`FY24`);
- dates and times (`3 March 2025`, `11–18 November 2026`, `Mar 2025`, `2025-03-03`, `3/3/25`, `10:30`); in a workbook a
  cell holding a date;
- slide, page and list numbers: the number after `slide`, `page`, `p.`, `pp.`, `figure`, `fig.`,
  `table`, `chart`, `exhibit`, `appendix`, `section`, `chapter`, `step`, `item`, `no.`, `#`;
  numbering at the start of a paragraph or line (`1.`, `2)`, `(3)`); a PDF line that is only a
  page number (`3`, `Page 3`, `3 of 12`, `3 / 12`);
- reference markers: `[1]`, `[12]`, and superscript text in a slide or a Word document;
- ordinals (`3rd`, `21st`);
- phone-like strings (`+1 415 555 0100`, `(415) 555-0100`, `415-555-0100`), id-like strings (digits
  joined by hyphens, `#4411`, a number with letters against it that are not a unit or scale such
  as `4411A`, a run of nine or more digits with no separator, sign, currency or decimal point in
  text), versions (`v2`, `version 3.1`, `1.2.3`), web addresses and e-mail addresses;
- whole numbers from 0 to 12 with no decimals, sign, currency, percent or scale ("3 regions"): too
  small to tell one source from another;
- in a workbook, `TRUE`/`FALSE` and errors.

**What vouches for a figure**, in this order:

1. `page`: the figures the page check found `verified` on the project's pages, as last saved in
   `checks/<page>.json` by `check_page` (the shown text of each item, and a data item's value);
   a page figure marked `derived` there makes the same figure in the file `derived`;
2. `data`: the project's data files, `data/**/*.csv`, `data/**/*.json` and `data/**/*.geojson`
   (each at most 20 MB, 200 files): every number in them, including the numbers written inside a
   cell or value of text ("148 km WSW of Tual"), and the total of each numeric CSV column;
3. `source`: every number in the saved text of every source (`sources/`).

**Matching.** Values are compared as absolute values (a sign written differently is not a
difference: sources often state a loss without one). A figure is `found` when a vouched value is
equal (`match: "exact"`), or when a vouched value rounded to the figure's precision is the figure
(`match: "rounded"`, and `rounded_from` names the value: `4.2 million` for 4,218,950; `12%` for
12.4). A percentage also matches the same value written as a fraction (`12.3%` for 0.123).
Truncation is not rounding (4.21 million is not found from 4,218,950). A figure written at another
scale than its source states it without the unit word (a table "in millions") is not found.
Otherwise the figure is `not_found`: no page, data file or source has it.

A `found` figure is in a vouched place; it is not proof that it is the right figure for that
sentence (the same number may stand for something else), and the source can be wrong.

**The saved result**, `checks/<file with / and other characters as ->.json` (for
`files/deck.pptx`: `checks/files-deck.pptx.json`):

```json
{"file": "files/deck.pptx", "kind": "presentation", "checked_at": "…", "error": null, "not_read": null,
 "summary": {"figures": 14, "found": 9, "rounded": 2, "derived": 3, "not_found": 2, "skipped": 21},
 "vouched": {"pages": ["index.html"], "sources": 4, "data_files": ["data/sales.csv"]},
 "items": [{"n": 1, "text": "$4.3 billion", "value": "4300000000", "status": "not_found",
            "where": "Slide 3, title", "context": "Revenue reached $4.3 billion in 2025",
            "match": null, "via": null, "ref": null, "rounded_from": null, "note": null},
           {"n": 2, "text": "4.2 million", "value": "4200000", "status": "found",
            "where": "Slide 4, Content 2", "context": "…", "match": "rounded", "via": "data",
            "ref": "data/sales.csv, column units (total)", "rounded_from": "4218950", "note": null}]}
```

`kind`: `presentation`, `document`, `workbook` or `pdf`. `items` are in the file's order (at most
10,000, every `not_found` kept; `summary` counts all: "Very large documents"). `via`: `page`, `data` or `source`; `ref`: the page and where on it,
the data file and column (and row, or `(total)`), or the source id; `rounded` counts the `found`
items that matched as a rounding; `derived` items carry `note` (the formula, or the page's
derivation). `context` is the text around the figure (at most 160 characters). `not_read` says
when part of a workbook was not read (more than 200,000 cells), so a clean result never hides it.

**The tool's answer** is short: `{file, kind, summary, error, not_read, not_found: [items, at most 40],
derived: [items, at most 15], rounded: [items, at most 15], saved, next}`, with the additions in
"Very large documents" (items without
`value`, `n` and the fields that are null). `next`: when something is not found, "correct each figure under not_found in
the file's build script, from its source or data (or fetch the source it came from), build and
render again, and run check_file again; a figure you calculated stays not_found here: recompute
it from vouched figures and say so in your report"; when nothing is, that every figure is found
or derived and that the derived ones (formulas, page derivations) are not recomputed by the
check; with `error`, the error.

## The agent's instructions

- The `VERIFY` wording (`effort.py`) sends the checker to the project's files as well as its
  pages: every Office or PDF file the page shows or the request made, with `check_file`, and
  derived figures in files (formulas, totals, changes) recomputed as for pages. After fixes, the
  agent runs `check_file` again on each file it changed.
- `library/agents/checker.md` has the same step: run `check_file` on each file, judge `found`
  items as it judges `verified` ones (is it the right figure for that sentence), recompute
  `derived` and `not_found` figures from vouched inputs, and give each one a status.
- The `office-files` skill: run `check_file` on the file before handing it over, after the last
  render.
- The project facts name `check_file` in one sentence beside `check_page`.

## The studio's own re-check, on files

After a reported verification the studio's re-check ("The studio's own re-check") also runs
`check_file` (without saving) on every file `check_file` ran on in the request and on every
`.pptx`, `.docx`, `.xlsx` or `.pdf` file of the project that `index.html` links to or frames
(`href`, `src` and `data` attributes; a framed preview, `files/deck.pptx.preview/index.html`,
counts as its file), at most 10 files. In the `verification_recheck` event:

- every entry of `pages` gains `kind: "page"` or `kind: "file"`. A file's entry keeps the page
  keys (`page` is the file's path, `checked` the figures checked, `verified` the found ones,
  `derived`, `failed` the not-found ones, `cited_not_listed: 0`, `source_list: false`, `error`)
  and adds `found`, `not_found` and `rounded`;
- every entry of `problems` gains `kind`; a file's not-found figure is
  `{"page": "files/deck.pptx", "kind": "file", "what": "$4.3 billion", "where": "Slide 3, title",
  "why": "Not in any checked page figure, data file or saved source."}`. Pages' problems come
  first, then files', within the same cap of 40;
- `agrees` is true when there are no problems and at least one page or file was read (and, since
  "Very large documents", nothing was left out past the caps: `not_rechecked`). The event is
  written when there is a page or a file to check.

## UI

- The card's line on what the re-check covers (`RECHECK_COVERS`) says it covers the page's marked
  figures and the figures in the project's Office and PDF files, against saved sources, checked
  page figures and project data.
- A file's problem reads as its place, then the figure: `Slide 3, title: $4.3 billion`, with the
  file's name first when the re-check covered more than one page or file
  (`deck.pptx · Slide 3, title: $4.3 billion`), and the reason under it.

.

.

# Diagrams and conversions

Two studio tools: `render_diagram` draws a diagram from Mermaid source and checks it before it is
shown, and `convert_document` turns a finished file into another format. The `diagrams` skill
says how the agent uses the first; the `office-files` skill points at the second.

## `render_diagram`

Arguments: `name` (required; lower-case letters, digits, `-` and `_`, 60 at most), and `source`
(Mermaid text, 50,000 characters at most) **or** `path` (a `.mmd` file of the project), plus
`must_show?` (labels the diagram has to contain), `png_width?` (200 to 4,000 pixels), `colors?`
(`{"light": {…}, "dark": {…}}`: Mermaid theme variables such as `primaryColor`, `lineColor`,
`primaryTextColor`; names are letters and digits, values colours) and `alt?` (the picture's text
for the `embed` snippet).

**One renderer, one pinned version.** Mermaid 11.15.0 (MIT) is kept inside the server at
`server/studio2_server/static/vendor/mermaid/mermaid.min.js` (3.3 MB, byte for byte the file in
the npm package; version and licence in `THIRD_PARTY.md` beside it; the version is written in
code once, `diagram.MERMAID_VERSION`). It runs in the studio's own Chromium (the one `look_at_page`
uses), in a blank page whose every network request is refused: nothing is fetched at render time,
never from a CDN. Each call gets a fresh page and one fixed configuration: `securityLevel:
strict`, `look: classic`, `layout: dagre`, `htmlLabels: false` (labels are SVG text, not HTML),
font family `Helvetica, Arial, sans-serif`, deterministic ids, a Gantt chart 1,200 px wide. Two
more things keep the picture the same between runs: Mermaid's drawing code calls `Math.random`, so
the page gets a seeded one before each drawing, and a Gantt chart gets no "today" line unless its
source asks for one (`todayMarker`), since that line moves every day. The same source then gives
the same bytes (SVG and PNG) on the same computer; another computer's fonts can change the sizes.
A source may still set its own configuration (`%%{init: …}%%` or front matter); the studio does
not forbid it. Without the vendored file or without the Playwright Chromium the tool is listed as
unavailable with the reason (the look tool's reason, which says how to get one).

**Both schemes, every time.** Each call draws the diagram twice, with Mermaid's `default` theme and
its `dark` theme (or the `base` theme with the given `colors`), because a page in this studio
usually has dark styles and a picture drawn for light text on white is unreadable on dark. It
costs one more drawing in a page that is already open (about 20 ms of a call of 0.2 to 0.6 s).

**On a parse error nothing is saved.** The answer is
`{rendered: false, saved: false, name, error, line, line_text, next, ms}`: `error` is the parser's
message, `line` its line number in the source (from the parser, or read from the message; `null`
when it gives none), and `next` tells the agent to fix that line and call again, and after three
failed tries to use a simpler diagram or a table and say so. The studio does not count the tries.

**On success** it writes `files/diagrams/<name>.mmd` (the source), `<name>.svg` (light),
`<name>-dark.svg` and, with `png_width`, `<name>.png` (the light one on white at that width, for
decks and Word files), replacing earlier files of the same name, and answers:

```json
{"rendered": true, "saved": true, "name": "order-flow", "type": "flowchart-v2",
 "source": "files/diagrams/order-flow.mmd", "svg": "files/diagrams/order-flow.svg",
 "svg_dark": "files/diagrams/order-flow-dark.svg", "png": "files/diagrams/order-flow.png" | null,
 "png_width": 1200 | null, "size": {"width": 483, "height": 162}, "nodes": 4, "edges": 4 | null,
 "labels": ["Start here", "Is it ok?", "…"], "must_show": 2, "missing": ["Refund"],
 "layout": {"width": 483, "height": 162, "smallest_text_px": 16, "smallest_text_at_desktop_px": 16,
            "hard_to_read": false, "smallest_text_at_phone_px": 11.9, "small_on_phone": false,
            "findings": [{"kind": "too_wide" | "small_text" | "clipped_text" | "overlapping_text",
                          "count": 1, "means": "…", "examples": [{"text": "…", "detail": "…"}]}]},
 "safe": {"scripts": 0, "outside_references": 0, "removed": [], "text_as_text": true, "fonts": "…"},
 "embed": "<picture>…</picture>", "renderer": "Mermaid 11.15.0 (the studio's own copy) in the studio's Chromium",
 "blocked_requests": [], "ms": 240, "next": "…"}
```

- **The second check, `must_show`.** Every label must appear in the drawing's text (case,
  white space and typographic quotes ignored; a label may be part of a longer one). Those not
  found are in `missing`, and `next` says the diagram rendered but is not what was asked. Without
  `must_show`, `next` asks the agent to compare `nodes` (shapes: boxes, participants, tasks,
  periods), `edges` (connecting lines and messages; `null` for a Gantt chart or a timeline) and
  `labels` (every piece of text drawn, at most 80) with what it meant.
- **The layout check.** Measured in the page on the light drawing at its own size: `too_wide`
  (wider than 1,400 px, so a desktop page shrinks it), `small_text` (the smallest text under 9 px
  once shrunk to 1,400), `clipped_text` (text past the side of its box, or past the edge of the
  picture, by more than 2 px) and `overlapping_text` (two pieces of text covering a quarter of the
  smaller one). Either of the first two sets `hard_to_read`, and `next` gives the remedy: split it
  or change its direction. The kinds are this tool's own; `look_at_page`'s `layout` measures HTML
  text, not SVG.
- **On a phone.** A page 390 px wide leaves a picture about 358 px. `smallest_text_at_phone_px`
  is the smallest text once the drawing is shrunk to that, and `small_on_phone` is true when it
  is under 9 px. It is not a finding and does not set `hard_to_read`: a reader in the studio can
  open the picture large ("A picture in the page opens large"). `next` says how far it is shrunk
  and, for a flowchart, state, class or entity diagram drawn left to right, to draw it top to
  bottom when the reader may be on a phone.
- **A safe file.** Mermaid's strict mode leaves out click handlers and links; the studio then
  removes from both SVGs every `script`, `iframe`, `object`, `embed`, `img`, `image`, `link`,
  `meta`, `audio` and `video` element, every `<a>` (its content kept), any `on…` attribute, any `href` or
  `src` that does not point inside the file (`#…`), and any `@import` or `url(…)` in a style that
  does not point inside the file (`removed` lists what was taken out). The saved text is then
  parsed again as XML and refused, with nothing saved, if a script or an outside reference is
  left. No font file is embedded or linked: the text names system fonts, and `safe.fonts` says
  so. `text_as_text` is false only when the source asked for HTML labels (`foreignObject`), which
  show in a browser but not in Office files.
- **Embedding.** `embed` is a `<picture>` that shows the dark SVG under `prefers-color-scheme:
  dark` and the light one otherwise, with `max-width:100%;height:auto`; paths are from the project
  root. The agent puts the saved SVG in the page, inline or as `<img>`, never the Mermaid script.

## `convert_document`

Arguments: `path` (a file of the project), `to` (the target extension, with or without the dot),
`sheet?` (the sheet to write as CSV), `reference?` (a `.docx` or `.odt` of the project whose styles
pandoc follows, for Markdown to that format), `replace?` (default false).

| From | To | Program | Verified (`tests/test_convert.py`, on a Mac with LibreOffice 26.8 and pandoc 3.9) |
|---|---|---|---|
| `.docx` | `.rtf`, `.odt`, `.pdf` | LibreOffice | each opens: RTF header, ODT archive with `content.xml` holding the text and the table, PDF pages |
| `.docx` | `.md` | pandoc (GitHub Markdown) | the text and the table come back as Markdown, a tracked insertion accepted |
| `.odt`, `.rtf` | `.docx` (and `.odt` ↔ `.rtf`, either to `.pdf`) | LibreOffice | `.odt` and `.rtf` to `.docx` open with the text; the others were run once by hand here (each opened), not in the tests |
| `.odt` | `.md` | pandoc | run once by hand here (text and table came back), not in the tests |
| `.pptx` | `.pdf`, `.odp` | LibreOffice | both open; the PDF has one page per slide |
| `.odp` | `.pptx`, `.pdf` | LibreOffice | run once by hand here (each opened; the PDF had a page per slide), not in the tests |
| `.xlsx` | `.ods`, `.pdf`, `.csv` | LibreOffice | ODS opens; a CSV of the named second sheet holds that sheet's values (UTF-8); a CSV of the first holds a formula's calculated result; `.pdf` was run once by hand here, not in the tests |
| `.ods` | `.xlsx`, `.csv` | LibreOffice | run once by hand here (each opened), not in the tests |
| `.md` | `.docx`, `.odt`, `.rtf`, `.epub`, `.html` | pandoc | each opens and holds the text; `reference` styles a `.docx`; a Mermaid block becomes a picture |

- **Where the result goes.** Beside the source under the same name with the new extension
  (`report.md` → `report.docx`); a source under `template/` or `sources/` is never written beside,
  so its result goes under `files/`. A CSV of one sheet of a workbook with several is named
  `<name>-<sheet>.csv`. When that file exists the tool refuses unless `replace: true` (the
  project's history keeps the old one); the source itself is never the target.
- **LibreOffice** is the one `render_document` uses (same profile, same lock: one at a time), with
  its time limit (180 s). A CSV is comma-separated UTF-8 of one sheet, with the values LibreOffice
  calculates; a workbook of several sheets needs `sheet`.
- **pandoc** reads Markdown from standard input (the source's folder and the project are where it
  finds pictures), with `--standalone` for RTF and HTML, `--embed-resources` for HTML (one file),
  a title from the file name when an EPUB has none, and `--reference-doc` for `reference`. A Word
  or OpenDocument file to Markdown is GitHub Markdown, unwrapped, its pictures saved in
  `<name>.media/` beside the result (`media`). Time limit 120 s.
- **Mermaid blocks** (```` ```mermaid ```` or `~~~mermaid`) in Markdown are drawn with
  `render_diagram`'s renderer (as `files/diagrams/<file>-diagram-<n>`, a PNG 1,200 px wide, or the
  SVG for HTML) and put in as pictures; a block that does not parse is left as code and named in
  `not_carried` with its line in the Markdown. Without the renderer they are left as code and
  `not_carried` says why.
- No shell line is built from the person's text: each program gets an argument list; paths are
  confined to the project with the helper every tool uses.

The answer:

```json
{"path": "files/report.docx", "from": "files/report.md", "size": 10897, "tool": "pandoc" | "LibreOffice",
 "version": "pandoc 3.9.0.2", "replaced": false, "opens": true, "pages": 3, "detail": null,
 "media": null, "diagrams": [{"n": 1, "picture": "files/diagrams/report-diagram-1.png"}],
 "not_carried": ["The styles are pandoc's plain defaults: …"], "ms": 410, "next": "…"}
```

`opens` is the result read back (an Office or EPUB archive with its main part, a PDF with its
pages counted in `pages`, RTF by its header, text formats as UTF-8); `detail` says what failed.
`not_carried` lists what the file holds that does not come across or was not checked, found by
reading the source: tracked changes (pandoc accepts them; for LibreOffice, not checked), comments,
a contents list (carried over as saved, not rebuilt: page numbers a script never filled in stay
empty), speaker notes and animations (not in a PDF), charts and conditional formats (not checked
in a workbook conversion), HTML inside Markdown (left out), pandoc's plain styles without
`reference`, fonts named but not included, and, for a PDF from `.docx`, `.pptx` or `.xlsx`, the
fonts the file names that are not among the PDF's fonts ("replaced by others"). pandoc's warnings
are added as they are. `next` tells the agent to call `render_document` on an Office or PDF result
and look at it, and `check_file` when it is a `.pptx`, `.docx`, `.xlsx` or `.pdf` that holds
figures.

When neither program is installed, the tool is listed as unavailable; when only one is, the pairs
of the other answer with what to install (macOS `brew install pandoc` or `brew install --cask
libreoffice`; Debian or Ubuntu `apt-get`; Fedora `dnf`). `studio doctor` and `install.sh` list
pandoc as optional, beside LibreOffice.

.

.

# Very large documents

Lane `big-documents`. A deck of a hundred slides, a long Word document or a workbook of many
sheets is built from a plan, by helpers working one section each, and checked deterministically
before anyone looks at a picture. The procedure is the studio's skill `large-documents`
(`library/skills/large-documents/`, with reference files beside it); the helpers are the library
agents `section-writer` and `slide-reviewer`. No new studio tool: `check_file` and
`render_document` gained what such a file needs, and every cap a large file reaches says so in
the result.

## The plan files

Kept in the project, so a later request can change one slide without redoing the rest. Only the
ledger is read by the studio; the others are the agent's own, and their shapes are what the skill
tells it to write.

| File | What it holds |
|---|---|
| `build/outline.json` | `{title, audience, purpose, kind: "deck"\|"document"\|"workbook", sections: [{id, title, module, slides: [{id, message, layout, facts: [keys], visual}]}]}`: one message per slide (a document: per chapter part, `pages` instead of `slides`; a workbook: `sheets: [{name, type, facts}]`) |
| `build/look.json` | The look of this one document, as named values, written from what the person asked for: `{fonts: {heading, body}, sizes: {…}, colors: {…}, layouts: {<name>: {…}}}`. Never a house theme shared between documents |
| `build/glossary.md` | The terms, names and abbreviations, each written one way |
| `build/slide-map.json` | Written by the assembler: `[{slide, section, id, module, layout}]`, so a defect found at "Slide 41" names the module to redo |
| `data/facts.json` | **The ledger**: every figure the document shows |

**The ledger**, `data/facts.json`:

```json
{"facts": [
  {"key": "revenue_2025", "label": "Revenue", "value": 4218950, "unit": "USD", "period": "FY2025",
   "source": "s3", "obtained": "quoted", "quote": "revenue was $4,218,950"},
  {"key": "units_2025", "label": "Units sold", "value": 2500, "unit": "units", "period": "2025",
   "source": "data/sales.csv", "obtained": "data", "how": "total of the column units"},
  {"key": "revenue_per_unit", "label": "Revenue per unit", "value": 1687.58, "unit": "USD",
   "period": "FY2025", "obtained": "computed", "from": ["revenue_2025", "units_2025"],
   "formula": "revenue_2025 / units_2025"},
  {"key": "target_margin", "label": "Target margin", "value": 18, "unit": "%", "period": "2026",
   "source": "person", "obtained": "given", "note": "Said in the request of 18 November"}]}
```

- `key`: a lower-case name (`[a-z][a-z0-9_]*`), unique. `value`: the figure in full, as a number
  (or a string holding one: `"12.4%"`), in its `unit`; a percentage is written as its number of
  percent (`12.4` with unit `%`). `label`, `unit` and `period` say what it is; `source` and
  `obtained` say where it came from.
- `obtained`: `quoted` (`source` is a saved source id; `quote` the words), `data` (`source` is a
  project data file under `data/`), `computed` (`from`: the keys it is made of, `formula`: an
  expression of those keys with numbers, `+ - * / **`, brackets and `abs`, `min`, `max`, `round`,
  `sum`), or `given` (the person stated it; `source: "person"`).

What `check_file` does with it: see below. The ledger lives under `data/`, so before this lane its
numbers already vouched as data; now it is read as a ledger whenever its top level is an object
with a `facts` list, and a fact vouches only when it passes these checks:

| Fact | Checked | A problem |
|---|---|---|
| any | a valid unique `key`, a number in `value`, an `obtained` of the four | `key` missing or repeated, no number, no `obtained` |
| `quoted` | the value is a number in the saved text of that source, exactly or as a rounding of it at the value's own precision (trailing zeros count as rounding: `4200000` is 4,218,950 rounded) | the source is not saved, or the value is not in it |
| `data` | the value is a number (or a column total) of that data file, the same way | the file is not a CSV or JSON under `data/`, or the value is not in it |
| `computed` | the formula is evaluated from the ledger values of `from` and agrees with `value` to half of the last digit `value` is written to | an unknown key, a name not in `from`, anything else in the formula, a fact that depends on itself, an input with a problem, or a result that disagrees (the recomputed value is named) |
| `given` | nothing: it is the person's own statement | — |

A ledger that is not JSON, or whose top level has no `facts` list, is read as an ordinary data
file and `ledger.error` says why.

## `check_file`: additions

Nothing was renamed; everything below is new in the saved result (`checks/<file>.json`) and the
tool's answer. The rules in "Verify content reaches files" hold unchanged for everything else.

**Ledger agreement.** Ledger facts are added before the pages' figures, so a figure that matches a
ledger value (exactly or as a rounding, as any figure) has `via: "ledger"` and `ref: <key>`; a
computed fact's item has `note: "computed in the ledger: <formula>"` and a given one's `note:
"given by the person; not checked against a source"`. `summary.ledger` counts the items found
through the ledger. A fact with a problem vouches for nothing: a figure that only it would have
matched falls to the data files, the sources, or `not_found`.

```json
"ledger": {"file": "data/facts.json", "facts": 42, "vouching": 40, "computed": 6, "given": 1,
           "problems": [{"key": "revenue_2025", "why": "4300000 is not in the saved text of s3."}],
           "problems_total": 1, "error": null}
```

`ledger` is null when the project has no `data/facts.json`. `problems` keeps at most 200
(`problems_total` counts all).

**Leftover placeholders.** `placeholders: [{text, where, context}]` (at most 500 kept) and
`summary.placeholders` (all). Read in the same text as the figures (slides' shapes and tables,
Word paragraphs and tables, text cells of a workbook, PDF pages), and found by:

| Kind | Found when | Not found (so a real subject is not flagged) |
|---|---|---|
| Office prompt text | `Click to add …`, `Click to edit …`, `Click icon to add …`, `Tap to add …` | |
| Filler text | `Lorem ipsum …`, `dolor sit amet` (any case) | |
| Template marks | `{{anything}}`, `${name}`, `{name}`, `<<name>>`, `«name»` (a name: letters, digits, `_`, `.`, `-`, no spaces) | `{ }` around words with spaces |
| Fill-ins in brackets | `[insert …]`, `[add …]`, `[enter …]`, `[placeholder]`, `[TBD]`, `[TODO]`, `[your …]`, `[source]`, `[citation needed]`, `[XX]` | `[1]` and other reference markers |
| Fill-ins with "here" | `your`/`insert`/`add`/`enter`/`type` + up to two words + `text`, `title`, `name`, `content`, `image`, `picture`, `logo`, `chart`, `data`, `heading`, `caption`, `quote`, `copy`, `number`, `date` or `value` + `here` | "your journey starts here" |
| Markers in capitals | `TBD`, `TBC`, `FIXME`, `XXX` (three or more X, or x), a masked figure (`$X.X million`, `XX%`), and `TODO` at the start of a text or followed by `:` | `TODO` inside a sentence ("the TODO app"), `todo`, `to-do` |
| An empty placeholder on a slide | a slide's placeholder shape (title, body, chart, picture or other; not the slide number, date, footer or header) with no text in it: PowerPoint shows its prompt there while the deck is edited. `text` is `(empty title)` or `(empty <type> placeholder)` | a filled chart or picture placeholder, which is no longer a text shape |

`{name}` in a technical document that shows template syntax on purpose is flagged; the agent says
so instead of removing it.

**Two figures for one label.** `conflicts: [{label, figures: [{text, where}]}]` (at most 200
kept, each with at most 6 figures) and `summary.conflicts` (all). A figure's label is read only
where it is unambiguous: the words written right before it and a colon or `=` (`Revenue 2025:
$4.2m`, back to the start of the line or the last `.`, `;`, `•`, `|`, bracket or comma), or, in a
table of a slide or a Word document, the row's first cell and the column's first cell
(`Revenue` and `2025` → `revenue 2025`). A label counts only with at least two words and a
letter (`Revenue` alone never does). Labels are compared without case, spaces or punctuation.
Two figures with one label conflict when they are both percentages or both not, and neither is
the other shown at its precision (`4.2m` and `4,218,950` agree; `4.2m` and `4.3m` do not), and
they are in different places: several figures in one cell or one line are one statement (a range,
`70% to 158%`), not a disagreement. Workbook
cells are not compared (a model repeats row and column names on every sheet; its consistency is
its formulas). A conflict is a warning to look at, not a not-found figure: the same label may
measure two different things on two slides.

**Where the problems are.** `by_part: [{part, not_found, placeholders, conflicts}]`: every slide,
page, sheet or heading (a Word document's nearest heading; `(before the first heading)`) with at
least one problem, in the file's order, so a large file's problems can be handed back to the
module that wrote them.

**The cap on items.** `items` keeps at most 10,000 figures (was 2,000); every `not_found` item is
kept even past it; `items_cut` counts those left out (0 when none).

**The tool's answer** adds, after `rounded`: `placeholders` (at most 30), `conflicts` (at most 15),
`ledger` (with at most 20 problems), `by_part` (at most 120) and `lists_cut` (true when a list of
problems in the answer, not found, placeholders, conflicts, ledger problems or parts, is shorter
than its count; the full lists are in the saved file).
`next` now names every kind of problem found, in this order: the file's error (then nothing else),
`not_read`, `not_found`, placeholders, conflicts, ledger problems, and where the full lists are
when the answer's are cut.

## `render_document`: additions

- **`pages`** (`"41-60"`, `"41"`, `"61-"`): which pages to draw as images, 1-based and inclusive;
  at most 60 in one call (a longer range is cut to 60 and said). Without it, pages 1 to 60, as
  before. For a workbook it picks the chart pages (default 1 to 20).
- **The PDF is reused.** The preview folder keeps `render.json` (the file's SHA-256). When the file
  is unchanged, a later call draws its pages from the PDF already made (no LibreOffice) and keeps
  the page images drawn before, so the viewer shows every page drawn so far; a changed file is
  converted again and the old images are removed. A workbook is always calculated again.
- **What was drawn, and what was not.** New result keys for decks, documents and PDFs:
  `pages_drawn` (`"41-60"`, this call), `not_drawn` (null, or a sentence naming the page ranges
  that have no image yet and the call that draws them: `call render_document again with pages:
  "61-104"`), `reused_pdf` (true or false). `next` starts with `not_drawn` when it is set (after the note on a cut range, when there is one), so
  "open every page image" never reads as the whole file.
- **An overview.** When the file has more than 20 pages: `overview: [paths]`, contact sheets
  (`<file>.preview/overview-<n>.png`) of every page, 20 to a sheet in five columns, each small
  page numbered, drawn from the PDF whatever `pages` is. They show the whole file's look at once;
  they are too small to read text.
- **A workbook with many sheets.** The answer stays readable: `start` is given for sheets in order
  until 12,000 characters of it are used; later sheets have `start: []` and `start_cut: true`.
  `errors` lists at most 30 per sheet and 100 in all (`error_count` per sheet is complete;
  `errors_listed` and `error_total` say how many were listed of how many). Chart pages: `chart_pages`
  as before plus `chart_page_count`, and `next` says which chart pages were not drawn.
- **Time.** LibreOffice gets 180 seconds plus 60 more for each 10 MB of the file (was 180 in all).

## The studio's own re-check: additions

- The `verification_recheck` event gains `not_rechecked: [paths]`: the pages past the first 10 and
  the files past the first 10 that the re-check did not read. `agrees` is false when it is not
  empty, and the card says how many were not re-checked and names them.
- A file's leftover placeholders are problems like its not-found figures: `{"page": "files/deck.pptx",
  "kind": "file", "what": "Click to add text", "where": "Slide 4, body", "why": "Leftover placeholder
  text."}`, after that file's not-found figures, within the same cap of 40. Conflicts and ledger
  problems are not re-check problems (a conflict may be two real figures; the ledger is not the file).

## The caps a large file reaches

| Cap | Value | What happens at it |
|---|---|---|
| `render_document` page images per call | 60 | `not_drawn` names the pages without an image and the call that draws them; `overview` shows every page small |
| `render_document` workbook chart pages per call | 20 by default, up to 60 with `pages` | `next` names the chart pages not drawn |
| `render_document` workbook answer | `start` up to 12,000 characters; errors 30 per sheet, 100 in all | `start_cut` per sheet; `errors_listed` of `error_total`; full grids in the preview page |
| `render_document` formulas checked | 200,000 per workbook (unchanged) | `unchecked` per sheet and `next` say how many were not checked |
| `render_document` preview grid | 400 rows × 50 columns per sheet (unchanged) | `cut` and `next` |
| LibreOffice time | 180 s + 60 s per 10 MB | the tool's error names the time |
| `check_file` items kept | 10,000 (every `not_found` kept) | `items_cut`; `summary` counts all |
| `check_file` cells read | 200,000 per workbook (unchanged) | `not_read` |
| `check_file` answer lists | not found 40, derived 15, rounded 15, placeholders 30, conflicts 15, ledger problems 20, parts 120 | `lists_cut: true` and `next` points at the saved file |
| A tool's answer | 60,000 characters (unchanged) | the answers above are sized to stay under it; a test builds a 104-slide deck and a 50-sheet workbook and checks they do |
| The re-check | 10 pages and 10 files (unchanged) | `not_rechecked`, `agrees: false`, said on the card |
| Pictures one agent opens | not a studio cap: each slide picture costs the reader up to about 1,600 tokens of its context (a picture is scaled to about 1.15 megapixels) | the skill has a reviewer look at 20 slides at a time and says which slides to look at past 60 |

# Projects of many pages

A project can be a site, documentation or a report in chapters: many pages that link to each other
and share one look. `check_page` and `look_at_page` see one page at a time, so nothing used to see
a broken link between pages, a page nobody can reach, or a page whose menu or styles drifted. The
tool `check_site` reads all the pages together. The skill `multi-page-site` says how to lay such a
project out, and `large-documents` now covers a long HTML report or a site as well as Office files.

## The tool `check_site {start?, max_pages?}`

It reads the project's files. It opens no browser and fetches nothing from the internet, so it is
on every studio. `start` is the start page (`index.html`); `max_pages` is how many pages it reads
(200 by default, 2,000 at most).

- **Which start page, when none is given.** `index.html` at the project's root, the page the
  project's address shows when the studio serves the folder (`/` is `index.html`,
  `workspace.page_to_file`). An app serves its own pages, and the studio does not know from which
  folder (the `fastapi-app` template serves `static/`). So when the root has no `index.html`, the
  check takes the index page (`index.html` or `index.htm`) nearest the root, if exactly one is
  nearest: `static/index.html` in a project made from `fastapi-app`. Two or more at the same depth
  are not guessed between: the answer is `no_start`, and its detail names them. The answer says
  which start was used (`start`) and whether the check chose it (`start_chosen`: true when no
  `start` was given). `start_note` says why, when the chosen page is not the root's `index.html`.
- **A given start that is not there** is `no_start`, as before; its detail now names up to 3
  index pages that are there (nearest the root first), so the next call can name the right one.

- **Which pages.** Every `.html` and `.htm` file that an export keeps (`export.kept`: no `.git`,
  `research`, `attachments`, `template`, `notes`, `state`, `looks`, `node_modules` and the rest
  of `export.LEFT_OUT`, no key files, no links), also leaving out any folder or file whose name
  starts with `.`. A file with no `<html>`, `<head>` or `<body>` tag and no doctype is a piece of
  a page (a section written to be joined), not a page; so is a file that still holds the joiner's
  marks (`<!-- join:…-->` or `{{fact:…}}`, see "Long documents, Office or HTML"). Pieces are counted
  in `pieces` and not checked.
- **What it reads in each page** (`html.parser`, no browser): links (`a` and `area` `href`),
  pictures (`img` `src` and `srcset`, `source`, `video` and `audio` and `track`, `poster`,
  `input type=image`, icons), scripts, stylesheets, frames (`iframe`, `frame`, `object`, `embed`),
  every `id` and every `a name`, the `<title>`, the viewport `<meta>`, the links inside `<nav>`,
  and a local `<base href>`. The `url(…)` and `@import` of a local stylesheet are read once.
- **How an address is read.** Its `?query` and `#fragment` are taken off and it is decoded. A
  leading `/` is the project's root; anything else is from the page's folder. An address ending in
  `/` or naming a folder means that folder's `index.html`. `mailto:`, `tel:`, `javascript:`,
  `data:`, `blob:` and any address with a scheme or `//` are not files of the project: other sites
  are counted in `outside_links`, the rest ignored. `#`, `#top` and `#:~:text=` need no id.
- **Never outside the project.** An address that climbs out of the project folder (`../../x`), or a
  link (symlink) that ends outside it, is the finding `leaves_project` and is not read
  (`workspace.safe_path`).

### Findings

Each is `{page, kind, target?, detail}`, the detail in plain words.

| kind | When |
|---|---|
| `broken_link` | A link names a file of the project that is not there |
| `broken_anchor` | `#id` or `page.html#id` where that page has no such `id` or `name` |
| `missing_file` | A picture, script, stylesheet, frame, or a stylesheet's `url(…)`, that is not there (for a stylesheet's own address, `page` is the stylesheet) |
| `empty_file` | One of those that is there with 0 bytes |
| `leaves_project` | An address that leaves the project folder |
| `too_large` | A page of more than 2 MB: it is not read, so nothing else is said about it |
| `wrong_case` | A link or file whose name differs from the file's only in capitals (`About.html` for `about.html`): it works on this Mac, whose disk ignores case, and breaks on a host that tells capitals apart |
| `not_exported` | A link to a file that is there but that an export leaves out (in `research/`, `notes/` and the like), so it breaks once exported |
| `no_start` | The start page is not there (no orphans are then reported); the detail names up to 3 index pages that are |
| `orphan` | A page that no chain of links or frames reaches from the start page |
| `dead_end` | A page with no link to any other page of the project (only when there are two pages or more; a page reached only through a frame is not one) |
| `no_title` | No `<title>`, or an empty one |
| `duplicate_title` | Two or more pages with the same title (one finding per title: `page` is the first, `target` names the others) |
| `no_viewport` | No `<meta name="viewport">` |
| `nav_differs` | Most pages (more than half of those with three pages or more) have the same set of `<nav>` links and this page's set is another: the detail says which links it lacks and which it adds. A nav link is the page it leads to; links to a place on the same page (a jump menu) are not counted |
| `styles_differ` | More than half of the pages link the same local stylesheet and this page links none of the shared ones |

The same finding twice (one page, one kind, one target) is kept once, with `times`.

**Limits, held while it collects** (a page of 500,000 broken links once took 3 GB and 157 s): a page
over 2 MB is `too_large` and not read; 50 MB of pages and stylesheets in one run; 5,000 links and files
kept per page; 2,000 findings kept, the rest only counted in `by_kind`; addresses and titles cut at
300 characters; 1,000 outside host names; 20 seconds for the whole run; a saved file of at most
5 MB. Each limit reached is said in `truncated` (`time`, `bytes`, `links`, `findings`, `pages`). It
runs in a worker thread (`asyncio.to_thread`), never on the studio's event loop.

A project with a back end (`studio.json` with a `run` command) answers addresses that are no file:
there a missing target is counted in `unchecked` (`{count, examples}`) instead of being a finding.

### The answer

`{start, start_chosen, start_note?, pages, pieces, findings, by_kind, pages_list, outside_links,
unchecked, truncated, covers, saved, next}`.

- `pages`: how many pages were read. `pages_list`: `{path, title, links_out, reachable}` for each,
  the first 100. `outside_links`: `{count, sites}` (the first 20 host names).
- `findings`: the first 300, start page first, then in page order; `by_kind` counts all of them.
  `truncated` says what was cut: `pages` (more pages than `max_pages`, with the number left
  unread) and `findings` (how many past 300). The whole result is saved in
  `research/site-check.json` (`saved`), through the project's path rule (`workspace.safe_path`) and
  never through a link: when `research` or the file is a link, nothing is written and `saved` is
  null. In a project brought in from a folder (attached, in place or
  as a safe copy) nothing is written into the project: the folder is the person's own app, and a
  file in a safe copy could travel back with a ship. The result is saved in the project's record
  folder instead, beside the pictures of `look_at_page` (`projects/<id>/site-check.json`), and
  `saved` is that full path.
- A run that writes `research/site-check.json` does not reload the preview: `research/` and
  `notes/` never hold page content, nor does any file under `sources/` except `sources/index.json`,
  which a page may read for its source list (`web/src/reload.ts`, `NOT_PAGE_FOLDERS`). `checks/`
  still reloads it, because the checked-numbers appendix shows the saved check;
  `attachments/` too, because a page may show a picture from it; `state/` is never reported.
- `covers`: one sentence on what this check does not see: links a script builds, an app's own
  routes, how the pages look, whether the facts on them are right.
- `next`: one or two sentences on what to do now (fix the findings and run it again; then
  `check_page` on the pages that state facts and `look_at_page` on the start page and an inner page).

## The skill `multi-page-site`

A studio skill (under 150 lines) for a site, documentation, a report in chapters or anything of more
than one page: `index.html` as the start page, one shared stylesheet with the `theme` tokens, the
same `<nav>` on every page with `aria-current="page"`, relative links only, every page complete by
itself, the site's conventions kept in the project notes (`notes/project.md`), when one long page
beats several, `check_site` until it is clean, then `check_page` and `look_at_page`, and what each
export does with several pages (one HTML file holds one page and names the others it links to; the
folder keeps them all).

## Long documents, Office or HTML

`large-documents` states its method once (plan, one ledger of facts in `data/facts.json` for the
whole project, sections or pages written by `section-writer` helpers, one join, checks, one review)
and keeps what is specific to Office files and to pages in two reference files
(`reference/office.md`, `reference/html.md`).

- **Pages.** A long report is one page joined from a shell (`build/shell.html`) and sections
  (`build/sections/<nn>-<id>.html`); a site is one shell (`build/page-shell.html`) filled with each
  page's module (`build/pages/<id>.html`). `build/outline.json` says which (`kind: report` or
  `site`) and in what order.
- **The joiner** `reference/join-pages.py` (standard library only; the agent copies it to
  `tools/join_pages.py`) fills the shell's marks: `<!-- join:toc -->`, `<!-- join:sections -->`,
  `<!-- join:title -->`, `<!-- join:nav -->` (the site menu, the same on every page, the current
  page with `aria-current="page"`), `<!-- join:content -->`, `<!-- join:sources -->` (the sources
  that page cites, `id="src-<id>"`), `{{root}}` (`""` or `../`), `{{check}}` (the page's saved
  check) and `{{fact:<key>}}`: the ledger's figure with its `check_page` mark (`data-quote` for
  `quoted`, `data-sql` for `data`, `data-derived` with the sources of its inputs for `computed`,
  `data-nocheck` for `given`). Pages add `display` (the figure as shown, which must be in the
  quote) and `sql` (for `data` facts) to the ledger. An unknown key, a key listed twice in the
  ledger, a missing module, a section without its id, or any path in the outline that is absolute or
  leaves the project (by `..` or through a link: every path is resolved and must stay inside the
  folder it is run in) stops the join and nothing is written. Only an `http:` or `https:` source
  address becomes a link. `--only <id>` joins one piece into a temporary folder.
- **The finish for pages** is `check_site` until it reports nothing, `check_page` on every page that
  states facts, and `look_at_page` on the start page and inner pages; the review is the `checker`
  helper. Office files keep their finish (`check_file`, `render_document`, `slide-reviewer`).
- **Helpers.** `section-writer` keeps its tools (Read, Grep, Glob, Write, Edit, Bash: Bash runs the
  joiner or the deck assembler on its own piece); its description and steps now cover an HTML
  section or page. `checker` gains `check_site`. This replaces those two lists in "What the agent
  is told once, and what with each message".
- **Where the work is tracked.** Both skills keep the plan in `set_plan` (one item per section or
  page) and the conventions in the project notes. `large-documents`' outline page in `index.html` is
  for the person only, shows no progress the plan lacks, and goes when the first join or assembly
  writes the real page.
- **The picker.** Every studio skill is in the agent's skill list; `large-documents`' description
  now names a long report and a site of many pages (290 characters), and `multi-page-site` names a
  site, several pages, docs and chapters (209). The two descriptions are kept short because the list has a budget
  (`library.LIST_BUDGET_CHARS`).

`make-a-skill` keeps every rule but moves the detail of scripts and of keeping a skill's look to
`reference/scripts-and-looks.md`, and is 150 lines.

# `query_files` on big files, and what its SQL may read

Until 25 November 2026 `query_files` ran DuckDB inside the studio's own process with no limits and
with full access to the computer: `read_csv('/etc/hosts')`, `'../<other project id>/…'`, any file of
the user and any `https://` address could be read through it, and the rules in "What an agent's
shell may not reach" never saw it (they read shell commands and file tools, not SQL). A long query
also held a server thread until it ended.

**Where a query runs.** Each call starts a separate process (`filesql_worker.py`, run with Python's
`-I` so nothing in the project is imported) whose working folder is the project folder. It gets no
environment from the studio beyond `PATH`, `HOME` and the locale. Before the agent's statement runs,
the worker:

- sets `allowed_directories` to the project folder (as `./`, and by its full path) and to the call's
  own temporary folder, then `enable_external_access = false` and `lock_configuration = true`. From
  then on DuckDB opens no file outside those folders, follows a link only where it really ends (a link
  in the project to a file outside it is refused), refuses `..` out of the project, and refuses every
  `http://`, `https://`, `s3://` and other remote address. No later `SET` can change this.
- does not install or load extensions (`autoinstall_known_extensions` and
  `autoload_known_extensions` off). CSV, Parquet and JSON are built in; Excel (`read_xlsx`) is an
  extension and is not offered: convert a workbook with a script first.
- the statement must still be one read (`split_statement`: SELECT, WITH, SHOW, EXPLAIN, VALUES,
  TABLE, DESCRIBE), so `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `SET` and `PRAGMA` are refused before
  DuckDB sees them, and DuckDB itself has no `getenv()` in this build.

Reading the project's own files is unchanged: `'data/x.csv'`, `read_parquet('data/*.parquet')`, a
full path inside the project, and patterns all work. Fetching from the internet inside SQL is not
possible any more; `fetch_source` or a script does that (no studio text asked agents to do it in SQL).

**Limits.** A call can no longer take the computer with it:

| Limit | Default | Change it with | When it is reached |
|---|---|---|---|
| Memory | 2 GB | `STUDIO2_QUERY_MEMORY` (`4GB`, `512MB`) | DuckDB spills to disk first; past that the call fails: "needed more than the 2 GB memory limit" and what to do |
| Threads | 4 | `STUDIO2_QUERY_THREADS` | (a speed limit only) |
| Disk for spilling | 10 GB, never more than half the free disk | `STUDIO2_QUERY_SPILL` | the call fails and says so |
| Time | 60 s | `timeout` argument, up to 300 s | the worker's whole process group is ended, the server thread is free at once, and the answer says the limit and what to do |

Every limit message ends with what to do next: filter (`where`), select fewer columns, aggregate
(`group by`) instead of returning rows, `profile` the file first, convert it to Parquet once and
query that. The temporary folder is `<data folder>/run/query/<random>/`, outside every project, and
is removed when the call ends, whatever happened.

**`profile` instead of `sql`.** `query_files {profile: "data/big.csv"}` (also `.tsv`, `.parquet`,
`.json`, `.jsonl`, `.ndjson`) answers:

```json
{"path": "data/big.csv", "bytes": 2148855922, "size": "2.0 GB", "format": "csv", "rows": 35400000,
 "columns": [{"name": "region", "type": "VARCHAR", "missing": 0, "distinct_about": 5, "min": "central", "max": "west"}],
 "sample": {"columns": ["…"], "rows": [["…"]]}, "sampled": false, "seconds": 7.4, "next": "…"}
```

- Every row is read, in one pass (per column `count`, `approx_count_distinct`, `min`, `max`; so
  `missing` is exact), when that finishes in 60% of the time limit. Otherwise the
  first 1,000,000 rows are profiled instead and the answer says `sampled: {"how": "the first 1,000,000
  rows", "rows_read": …}`; `rows` is then counted on its own (Parquet: from its metadata) or, if even
  that does not fit, left out with `rows_note`.
- `distinct_about` is DuckDB's estimate, not an exact count. `min` and `max` are as DuckDB writes them.
- `next` says to convert to Parquet when a CSV or JSON file is over 100 MB.
- A wide file stays under the answer cap: the first 250 columns are described (`columns_cut` says
  how many more), the sample shows the first 30 columns (`sample.columns_cut`), sample values of
  80 characters at most and a min or max of 60.

**`save_as` also takes `.parquet`.** The worker writes the whole result (no row limit; `limit` caps
it only when given) to a file in its temporary folder through DuckDB's own Parquet writer, and the
studio then moves it to the path, which must be inside the project (`safe_path`, as for `.csv` and
`.json`). The agent's statement is never turned into a `COPY`: it is run as a relation and the
relation is written. A call that fails or runs out of time leaves no half-written file in the project.

**`check_page`** runs every `data-sql` of a page in one such process with the same limits (60 s for
the whole page); each mark still gets its own verdict.

**Not covered.** The project's own files are all open to the SQL, including anything the person put
there. A back end or a script the agent runs is a separate program and is judged by the shell rules
alone. Big files are still committed into the project's history like any file (versions are git);
nothing here changes that.

# Tests for a project's app: `run_tests`

A project with a back end, or with logic worth protecting, should have tests, and the studio should
know whether they pass. `run_tests {path?, filter?, timeout?}` runs them and keeps the result.

**Which command.** In this order:
1. `"test"` in the project's `studio.json` (a command string), run with `sh -c`.
2. In an attached app without that: the checks of its confirmed ship recipe, joined with `&&`.
3. pytest, when the project has a `tests/` folder or a `test_*.py` / `*_test.py` file at its top:
   `.venv/bin/pytest` when the project has one, else `uv run --with pytest --with httpx
   [--with-requirements requirements.lock | requirements.txt] pytest`, always with
   `-q -p no:cacheprovider` (nothing written into the project). `httpx` is there for FastAPI's and
   Starlette's `TestClient`.
4. `npm test`, when `package.json` has a `test` script that is not npm's placeholder ("no test
   specified"). Without `node_modules` it is not run and the answer says to install first.
5. None: the answer says the project has no tests, and what to create (`tests/test_app.py`).

`path` (a file or folder inside the project) is added at the end of any of these commands (after
`--` for npm). `filter` (pytest's `-k`, vitest's and jest's `-t`) works only with the studio's own
pytest or npm command; with a command from `studio.json` or a recipe the answer says to pass `path`.

**How it runs.** In the project folder, with the environment a project's app gets (the short list
in `apps.py`, never the studio's own: no keys, no access key) minus the app's own variables (no
`PORT`, `STUDIO_TOKEN`, `STUDIO_DATA_URL`, `STUDIO_MODEL_URL`), plus `CI=1`,
`PYTHONDONTWRITEBYTECODE=1` and **`STUDIO_TEST_STATE`**: a new empty folder for this run, outside
the project, removed afterwards. An app reads its state folder as `STUDIO_TEST_STATE` when that is
set, else `state/`, so tests never write into the live app's records (the `app-tests` skill and the
templates do this). The command never starts, stops or restarts the app, and gets no port. Time limit
300 s by default, `timeout` up to 900; at the limit the whole process group is ended. One run per
project at a time.

**The shell rules apply.** The command is judged by the same rules as an agent's shell command
(`risk.risky_command`, with this project's reach). Any reason at all, a guarded one or not, and
`run_tests` does not run it: the answer gives the reason and says the agent may run it with Bash,
where the person is asked (or, on a shared studio, a guest is refused as for any such command). So a
`"test"` the agent wrote into `studio.json` reaches nothing its shell could not reach without a card.
The tests themselves are the project's own code, which the agent's shell can already run; a program
that sets out to reach further gets as far as its shell would (see "Not caught").

**An attached app.** A safe copy runs its tests in the copy, never in the original. A folder edited
in place runs them in that folder, the person's own, exactly as the agent's shell would; the command
is judged with the in-place rules. A recipe's checks are used only once the person confirmed the recipe.

**The answer.**

| Field | |
|---|---|
| `command` | what was run (or `null` when nothing was) |
| `passed` | true only when the command ended with exit code 0 |
| `counts` | `{passed, failed, errors, skipped}` read from pytest's summary line; vitest, jest and `node --test` as well as can be read; `null` when nothing could be read |
| `failures` | `[{name, message}]`, 20 at most, messages 300 characters at most |
| `output_tail` | the last 6,000 characters of the output |
| `seconds`, `timed_out`, `exit_code` | |
| `next` | what to do: on a failure, fix the code (or the test, when the test is wrong) and run again; never delete a test to make the run pass; say plainly what still fails |

**What the studio keeps.** The last result goes into the project record as `tests_last` (`at`,
`command`, `passed`, `counts`, `seconds`, `timed_out`, `fingerprint`), not into the project's files,
so it reloads nothing and makes no version. The current part of the project facts then has one line:
"Tests (`<command>`): 12 passed, 2 failed, 8 minutes ago; the app's files changed since." A project
with tests that were never run here gets "Tests: this project has tests (`…`), not run here yet."

"Changed since" is cheap to know: `fingerprint` is the count and the newest change time of the
project's code files (`.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.mjs`, `.cjs`, `.vue`, `.svelte`,
`.json`, `.toml`, `requirements*`), outside `.git`, `node_modules`, `.venv`, `state/`, `data/`,
`sources/`, `checks/` and `attachments/`, stopping after 20,000 files (then the line leaves "changed
since" out). Taken at the end of the run and again for each message: a few milliseconds.

**Helpers.** The `checker` gets `run_tests` in its `tools:` line, so a review of an app can run
its tests.

