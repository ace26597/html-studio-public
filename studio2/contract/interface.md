# Contract: Interface and the agent's words

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

# Draw on the page

The person draws on the live page (any project page: report, dashboard, app) and sends the
drawing with a message. The agent gets a numbered list of the marks, each tied to the page
element under it, plus screenshots of the marked regions with the marks drawn in.

## Bridge

`set-mode` gains `draw`. In draw mode the bridge puts a transparent layer over the viewport.
Marks are tied to the element under them, so they stay with their content when the page (or
a scrolling panel inside it) scrolls or reflows. Marks are numbered on the page from 1.

| Direction | `type` | Fields |
|---|---|---|
| shell → frame | `draw` | `tool`: `pen`, `box`, `note`, `move`, `pan`; `color` (CSS colour). Default `pen`, `#ef4444` |
| shell → frame | `draw-undo` | removes the last mark |
| shell → frame | `draw-clear` | removes every mark |
| frame → shell | `drawing` | `count`, `drawing` (a Drawing, or null when there are no marks). Sent after every change |

`pen`: drag to draw. `note`: tap, type, Enter (Shift+Enter for a new line, Escape to drop
it). `box`: drag a rectangle, then type what belongs there ("sidebar", "heading here");
Enter keeps it (an empty label is allowed), Escape drops it. Boxes are how the person
sketches a layout, on an existing page or an empty one. `move`: press on an element and drag
it to where it should go; the bridge outlines the element, draws an arrow and a ghost of it
at the new place. Nothing on the page is actually moved: it is an instruction for the agent.
`pan`: the layer lets everything through, so the person can scroll with one finger and use
the page; marks stay visible. Leaving draw mode hides the marks and keeps them;
a page load drops them (the frame sends `ready`, and the shell must forget its copy).

### Drawing

```json
{"path": "/", "viewport": {"width": 390, "height": 760}, "dark": false,
 "page": {"width": 390, "height": 5120},
 "marks": [
  {"n": 1, "kind": "stroke", "shape": "loop", "color": "#ef4444",
   "points": [[120, 2310], [124, 2318]], "box": [96, 2290, 210, 140],
   "rel": [[0.12, 0.3], [0.14, 0.34]],
   "anchor": {"selector": "#revenue > figure", "tag": "figure", "text": "Revenue by quarter…",
              "rect": [16, 2270, 358, 260]},
   "section": "Revenue", "from": null, "to": null},
  {"n": 2, "kind": "note", "text": "make this a bar chart", "color": "#ef4444",
   "points": [[140, 2450]], "box": [140, 2450, 0, 0], "rel": [[0.35, 0.69]],
   "anchor": {…}, "section": "Revenue"}]}
```

A `box` mark has `kind: "box"`, `text` (the label, may be empty), two `points` (opposite
corners) and `covers`. A `move` mark has `kind: "move"`, two `points` (where the element was
grabbed and where it was dropped), `anchor` (the element being moved, with its `rect`),
`ghost: [x, y, w, h]` (where the element would sit) and
`to: {selector, tag, text, side}` — the element at the drop point and which side of it the
drop was on (`above`, `below`, `left`, `right` or `inside`).

Coordinates are CSS pixels in the document (not the viewport). A mark whose anchor is the
page itself (`body`) has an empty `rel` and is redrawn from `points`. `rel` is each point as a
fraction of the anchor's rectangle; it is what lets the marks be redrawn on a fresh render.
`shape` is `dot`, `line`, `loop` or `scribble`; a `line` also carries `from` and `to`
(`{selector, tag, text}`) when its ends sit on different elements. A loop or scribble carries
`covers`: up to six `{selector, tag, text}` for the elements actually under it, which matters
when the anchor is a large container. `anchor.text` ≤ 200
characters, `text` of a note ≤ 500, at most 60 marks and 600 points per stroke.

## REST

| Route | Body | Answer |
|---|---|---|
| `POST /api/projects/{id}/drawings` | a Drawing | 200 `{path, images: [path], marks: n, rendered: bool, note: string or null}` |

The server loads the page in the headless browser at the drawing's viewport width, redraws
the marks on their anchors and saves one screenshot per marked region (at most 5) as
`attachments/drawing-<ms>-<n>.jpg`, and the drawing itself as `attachments/drawing-<ms>.json`
(`path`). The saved file gains `images: [{path, marks: [n]}]` and `note`. When no browser is available `rendered` is false,
`images` is empty and `note` says why; the drawing is still saved and usable.

To send it, put `path` in the turn's `attachments`. An attachment that is a drawing file is
not listed as a plain file: the agent is given the page, the width, every mark with its
element, section and note text, and the images to open. 400 `invalid_drawing` for a drawing
with no marks.

The screenshots come from a fresh load of the page, so state the person had set up in their
own browser (a chosen filter, an open tab, something typed) is not in them; their sign-in is,
when they allow it ("Seeing an app signed in as the person"). The mark list is
taken from the person's live page and is the more reliable of the two.


---

# Voice input

The person can dictate a message. The browser records, the studio transcribes on this machine,
and the text is put in the message box for the person to check and send. Nothing is sent to a
speech service.

- `GET /api/health` gains `transcription: {available: bool, reason: string or null}`.
- `POST /api/transcribe`: multipart `file` (the recording: `audio/webm`, `audio/mp4`,
  `audio/wav`, `audio/ogg`, …; at most 25 MB) and optional form field `language` (`auto` by
  default, else a code such as `en`, `hi`, `mr`). Answer 200
  `{text, seconds}`: `text` is the transcript with noise markers such as `[BLANK_AUDIO]`
  removed (it may be empty when nothing was said), `seconds` is how long transcription took.
  503 `transcription_unavailable` with the reason when there is no transcriber, 413
  `file_too_large`, 422 `transcription_failed` when the transcriber could not read the audio.

The transcriber is a command that takes an audio file path and a language and prints the text:
`STUDIO2_TRANSCRIBE` if set, else `~/scripts/transcribe.sh` when it exists (whisper.cpp on
this Mac). It gets two minutes. Recording needs a secure page (HTTPS or localhost) and the
person's permission for the microphone; inside another app's frame, that frame must allow
the microphone.


---

# Suggested next steps

After a request that completed, the chat can show a few chips under the answer: things the person
might ask for next. A tap puts the chip's text in the chat box. Nothing is sent until the person
sends it, and they can change the text first. No model call is made to produce them: the agent
proposes its own in the request it is already running, and the studio adds its own from what the
request did.

## The agent's part: `suggest_next_steps`

A studio tool the agent may call once, last, when there is an obvious next thing:

```
{steps: [{label (≤ 40 chars), text (≤ 400 chars)}]}
```

`label` is what the chip says ("Add a section on battery life"). `text` is the whole request as
the person would write it; it is what lands in the chat box. White space is folded and both are
cut at a word. A step with an empty label or text is dropped, as is one whose label repeats an
earlier one (letter case ignored). At most 3 are kept. A second call in the same request replaces
the first. `steps` that is not a list → the tool answers with an error. The tool writes no event;
it answers `{kept, cut}`.

When to call it is one line of the system prompt (the agent program loads studio tools on demand,
so a rule kept only in the tool's description is not read before the call): once, as the last tool
call of a request that made or changed the page. What to offer is said once, in the tool's
description, which the agent reads when it loads the tool: requests it could carry out itself in
this project, nothing already done, no questions, no check of the page's content and no general
polish (the studio offers those), none at all when nothing sensible follows, and no repeat of them
in the closing message.

## The studio's part

Rules in one function, `nextsteps.studio_steps(facts)`, with no model call. `facts`:

| Key | Meaning |
|---|---|
| `changed` | The request changed project files (it made a version) |
| `has_page` | The project has `index.html` or a `studio.json` |
| `verify` | A check was asked for (`options.verify`) |
| `reported` | The agent reported a check (`verification` event) |
| `failed` | The `failed` count of the last report, or 0 |
| `still_wrong` | `problems_total` of the studio's own re-check, or 0 |
| `polish` | The request was a polish request (`options.polish`) |
| `can_look` | The studio has a browser to look with |

| Step `id` | Offered when | Chip | Text |
|---|---|---|---|
| `fix` | `still_wrong > 0` | Fix what the check still finds | Fix the N items the studio's own check still finds wrong on this page (they are listed on the check card), then run the check again. |
| `failed` | `reported`, `failed > 0`, no `fix` | Work through the failed items | Work through the N items the check marked as failed: fix those a better source settles, and tell me which ones need my decision. |
| `check` | `has_page`, not `reported`, and `changed` or `verify` | Verify content | The check-only text, with `options: {depth: "standard", verify: true, check_only: true}` |
| `look` | `has_page`, `changed`, `can_look`, not `polish`, and none of `errors`, `fix`, `failed` applies | Polish the look | The polish text, with `options: {depth: "standard", verify: false, polish: true}` |

Of the first three at most one is offered (the first that applies, in this order). Before them
comes `errors` ("Fix the page's errors"), offered when the studio's own look at the page found any
(`page_errors`, below). `look` is offered beside `check` or by itself, never beside a step that
fixes something: a page with errors or wrong figures is put right first.

**The studio's own look at the page.** After a request that completed and changed the project, the
studio opens the project's page once in its own browser (an app once it is running again, up to 8
seconds) and collects what `look_at_page` reports: script errors and failed requests. When there
are any it writes one `notice` (`warning`: "The studio opened the page after this request and it
shows N errors when it loads: …", the first three named) before `next_steps`. No model call. It is
skipped when there is no browser, on the scripted agent, and when nothing changed. This is the
independent check an app gets: the page check (`check_page`) sees marked figures only, and an app
usually has none.

## The event

When a request ends `completed` and there is at least one step, the studio writes one event after
the version is saved and before `turn_finished`:

```json
{"type": "next_steps", "steps": [
  {"id": "check", "from": "studio", "label": "Verify content",
   "text": "Verify content: check every figure and statement on this page against its sources, and change nothing except certain fixes.",
   "options": {"depth": "standard", "verify": true, "check_only": true}},
  {"id": "look", "from": "studio", "label": "Polish the look",
   "text": "Polish the look: see how this page looks on a desktop first, then on a tablet and a phone, fix what is clearly off, and suggest the rest.",
   "options": {"depth": "standard", "verify": false, "polish": true}},
  {"id": "a1", "from": "agent", "label": "Add a section on battery life", "text": "Add a section on …"}]}
```

The studio's steps first (two at most), then the agent's in the order given (`a1`, `a2`, `a3`); at most 5 in all.
An agent step whose label equals the studio step's label (letter case ignored) is dropped.
`options` is present only on a step that needs them. A request that was stopped or failed writes
no `next_steps`. Never more than one per request.

## UI

Chips are shown under the last request only, when it completed and nothing is running or queued;
an older request's chips are not shown again. A tap puts `text` in the chat box (after what is
already typed, on a new line, when the box is not empty), puts the caret there and sends nothing.
When the person sends a message whose text is exactly a shown step's `text` and that step has
`options`, the message goes with those options (so "Verify content" from a chip is the same
check-only request as the button, and "Polish the look" the same polish request); any other text goes with the project's remembered options.
On a phone the chips wrap and each is at least 36 px tall.

# Files in the chat, and files shown as what they are

No new event, field or route: everything here is derived in the interface from what the server
already sends (the request's `checkpoint` event, kind `agent`, and `GET /api/projects/{id}/files`).

## Files in the chat

After a request ends (`completed`, `stopped` or `failed`), the chat shows under its answer a small
card for each deliverable file it made or changed: the kind in plain words, the file's name, its
size, and whether it is new or changed. A tap opens the file in the Files panel's own viewer
(the panel opens if it was closed, the folders above the file unfold, and unsaved edits in the
open file are asked about first, as for any other file). There is no download button in the
chat; the viewer has one.

**What counts as a deliverable.** The files in the request's own `checkpoint` (kind `agent`;
an upload's or a restore's version never counts) with status `added` or `modified` that are:
- anything under `files/` (where the agent is told to put Office files), or
- elsewhere in the project, an `added` (not merely changed) PowerPoint, Excel, Word, PDF,
  raster picture (`png jpg jpeg gif webp avif bmp`), video, sound, CSV or TSV file.

and never:
- in the studio's own and working folders at the top of the project: `sources/`, `checks/`,
  `attachments/`, `tools/`, `research/`, `state/`, `template/`, `notes/`; nor `files/img/`
  (the pictures a deck or document is built from) or `files/diagrams/` (the drawings and their
  sources that `render_diagram` saves, which the page already shows);
- inside a folder whose name starts with a dot, `node_modules/`, `__pycache__/`, or a
  `<file>.preview/` folder written by `render_document`;
- the page's own code or a script (`html htm css scss sass less js mjs cjs jsx ts tsx map py sh
  lock`), wherever it is;
- an SVG outside `files/` (nearly always one of the page's own icons);
- a video's poster (`<name>.poster.jpg`, or `<name>.jpg` beside `<name>.mp4` in the same request);
- a file attached to the request's own message.

Cards under `files/` come first, then the rest, each in the checkpoint's order. At most **8** cards
per request; the rest are counted in one line, "and 3 more in Files", which opens the Files panel.

**Kinds.** "PowerPoint deck" (`pptx ppt odp key`), "Excel workbook" (`xlsx xlsm xls ods`),
"Word document" (`docx doc odt rtf`), "PDF", "Picture", "Video", "Sound", "Table (CSV)",
"Table (TSV)", "Text" (`md markdown txt`), else "File". A picture's card shows the picture as its
thumbnail (from `/__studio/files/`); the others an icon.

**Size, and a file that is gone.** Sizes are the files' sizes now, read from the file listing once
the request has ended and again whenever the project's files change while no request runs (not
during one). A file that is not in the listing any more (deleted later, or a version restored
without it) keeps its card, but the card says "no longer in the project" and offers "Show version"
instead, which opens that request's version in Versions, where the file still is. A listing cut
at its 5,000-file cap, or one that could not be read, never makes a card say a file is gone: the
card then shows no size and still opens the file. A file that was changed again since opens as it
is now.

Known limit: a file dropped at the top of the project (not attached to a message) while a request
runs is saved with that request's version, so it can get a card as if the request had made it.

## Files shown as what they are

In the Files panel, a text file of these kinds opens as what it is, with a "Show source" button to
see (and edit and save) its text, and a button back ("Show formatted", "Show table", "Show
picture"). The shown view follows the text as it is being edited.
- **Markdown** (`.md`, `.markdown`): rendered with the chat's own Markdown renderer. Raw HTML in
  the file is shown as text, never turned into elements; links go only to `http`, `https` and
  `mailto` addresses, in a new tab.
- **CSV and TSV** (`.csv`; `.tsv` and `.tab` split on tabs): a table, the first line as its
  header, which stays in view while the table scrolls; a column whose filled cells are all
  numbers (`12`, `-3.5`, `1,234.50`, `$9.99`, `45%`, `(120)`) is right-aligned. Quoted fields
  may hold the delimiter, doubled quotes and line breaks; blank lines are not rows. At most the
  first **500** rows and **50** columns; a line above the table says how many of each the file
  has when some were left out ("Showing the first 500 of 1,204 rows and 50 of 63 columns.").
- **SVG**: the picture, as an `<img>` loaded from the file's text (`data:image/svg+xml`), which
  never runs a script or an event handler in the file. It is never put inline in the studio's
  page. An SVG larger than 1 MB (too large to open as text) is still shown as the picture, as an
  `<img>` of the file's `/__studio/files/` address, without a source view.

Every cell and every piece of Markdown text is a text node; nothing from the file reaches the
studio's page as markup.

.

.

# The guide and the benchmarks, inside the studio

The repository's root `docs/` folder is a small static site: the guide ("How it works"), the
benchmarks page and the interactive model comparison. The public copy's host serves it as a web
site; the studio serves the same files itself, so they can be read from the app, offline too.

- `GET /site/` and `GET /site/<path>` answer with a file of that folder. A path that ends in `/`,
  or names a folder, is that folder's `index.html` (a folder named without the `/` is redirected
  to the address with it, so the page's own relative links hold). Only pages and what they show
  are served: `.html`, `.css`, `.jpg`, `.jpeg`, `.png`, `.gif`, `.svg`, `.webp`. Anything else in
  the folder (the working notes kept there are `.md`), a name that starts with a dot, a path that
  leaves the folder, and a file that is not there are 404. Responses are `Cache-Control: no-cache`.
  The route is behind the same sign-in as the rest of the studio.
- `GET /api/health` gains `site: true` when `docs/index.html` is there. A studio installed without
  the folder says `false`, serves nothing under `/site/` and shows no links.
- The projects page shows two links in its top bar when `site` is true: **How it works**
  (`/site/`) and **Benchmarks** (`/site/benchmarks/`). They are ordinary links: the page opens
  in the same tab, and a modified click opens a new one.
- The pages carry a link back, **Your projects** (`/`), which they show only when their address
  starts with `/site/`, that is, when a studio is serving them.

# Instructions for a project

What a person always wants in one project, in their own words: "always use Indian number
format", "every report ends with the sources", "keep the tone plain, no exclamation marks". It
is kept with the project and given to the agent with every request there, so it is typed once.
Context (a folder, a file, an address) is *where to look*; this is *how to work*. Each project
has its own; nothing here applies to another project.

- **The field.** `instructions` on the project record: plain text, 4,000 characters at most,
  `""` when there are none. Line ends are stored as `\n`; spaces and empty lines at both ends
  are dropped; control characters other than a new line and a tab are removed.
- **Saving.** `PATCH /api/projects/{id}` with `{"instructions": "<text>"}`. `""` or `null`
  clears them. Anything that is not text, or longer than 4,000 characters once tidied, is a 400
  (`invalid_request`, with the length it had). The answer is the project, as for any `PATCH`.
- **When it applies.** From the next request. A request already running keeps what it started
  with. The text goes with each request as it stands then, like every project fact, so an
  earlier request of the same conversation still holds the text it was given: the agent is told
  that the text given now replaces any earlier one. After the person clears them, each request
  says once that the project has none now and that earlier ones no longer apply (the record
  keeps `instructions_cleared` for that; it is not in the API's project).
- **What the agent receives.** One of the standing project facts (sent again whenever they change; beside the
  attached databases and the context items): that these are the person's standing instructions
  for this project, the text as they wrote it, and three rules. It follows them in every
  request; what the person asks for in a request comes first where the two disagree, and the
  agent says in a sentence which instruction it set aside; and an instruction it could not
  follow is named at the end, never dropped quietly. A project that never had instructions
  gets no such fact.
- **What it cannot do.** The text is the person's, with the standing of a message they typed. It
  changes how the work is done and what it contains; it does not change what the agent may do.
  Approvals still ask, context folders stay read only, and a line in it that claims to come
  from the studio is still the person's line.
- **Where it is not copied.** A template saved from the project and an export do not carry the
  instructions; they stay with the project and go when it is deleted.
- **A shared studio** has them like any other: whoever can open the project can read and change
  them, as they can its conversation.
- **Cost.** The text is sent with every request of the project, like the rest of the facts:
  4,000 characters is about a thousand tokens.

## UI

- **Project menu → Instructions…** (both menus; the row says *On* when the project has some),
  and a button beside *Add context* under the message box, marked when instructions are set.
  Neither is shown while the project the server sends has no `instructions` field (a server
  started before this existed, with the interface built since): a Save there would be dropped.
- The dialog is one text box with examples as its placeholder, a count against 4,000, **Save**
  and **Cancel**, and **Clear** when the box holds text. Clear empties the box only: nothing
  changes until Save, and Save is off while the text is what the project already has or is over
  the limit. Closing with unsaved text asks first. It says under the box that the text is given
  to the agent with every request in this project and only there, that a change applies from
  the next request, and that what is asked for in a message still comes first. While a request
  runs it says that the running request keeps the earlier text.

## Instructions for every project

Some of what a person always wants is about them and not about one project: where their work
is, the currency and number format, how they like things written. The studio keeps one more text
for that and gives it to the agent with every request in every project.

- **The text.** The same rules as a project's own: plain text, 4,000 characters at most, tidied
  the same way. It is kept in the studio's data folder (`instructions.json`), not in a project.
- **REST.** `GET /api/instructions` → `{text}` (`""` when there is none).
  `PUT /api/instructions` with `{"text": "<text>"}` → `{text}`; `""` or `null` clears it; not
  text or too long → 400 `invalid_request`.
- **What the agent receives.** One more project fact, before the project's own instructions:
  the text quoted line by line, that it is about the person and their work and holds in every
  project, that this project's own instructions come first where the two differ and a message's
  request comes first of all, and that it changes nothing about what the agent may do. It says
  that the text as given now replaces any earlier version in the conversation, and after it was
  cleared each request says that none applies now, exactly as for a project's own.
- **A shared studio:** a text that reaches every project is the owner's to write, like a skill.
  Both routes answer 403 `not_for_shared` to anyone who is not the owner ("The owner of a
  shared studio", below). What the owner wrote is given to the agent in every project there.
- **UI.** The Instructions dialog has two parts chosen at its top: **This project** and **Every
  project**. Each has its own box, count, Save and Clear. The second is shown only when
  `GET /api/instructions` answers (not to a guest of a shared studio, not on a server started
  before this existed). The button beside *Add context* is marked when either has text.

# How much to involve the person (the scale, and the agent's cards)

A request can pause so the agent asks the person, shows its plan, or checks in on a part it has
made, instead of deciding quietly. The person sets how much on a scale of five stops; at the
first stop nothing changes and nothing is asked.

## The scale

`involve` is a third option beside `depth` and `verify` ("How much to do"): one of `0`, `25`,
`50`, `75`, `100`. Any other value → 400 `invalid_options` (`null` counts as left out). It is
accepted in a request's `options` and in `PATCH /api/projects/{id}` `turn_options`, remembered
per project, and sent with each request. Left out: `0`.

| `involve` | Name | The agent | Cards per request |
|---|---|---|---|
| `0` | On its own | never asks; the message is byte for byte what it was | 0 |
| `25` | Only if stuck | asks only when it cannot sensibly go on: two readings that lead to different results, or something only the person has | 1 |
| `50` | Ask first | asks before it starts when the answer would change the result | 2 |
| `75` | Agree the plan | asks first, then puts its plan to the person and waits for the go-ahead before building | 4 |
| `100` | Step by step | plan first, then a check-in after each part that stands alone, with what it made, what it found and what it rests on | 12 |

- The scale is five stops, not a hundred: a model does not act differently at 37 than at 42,
  and a stop the person cannot predict is no setting.
- **The asking is matched to the work.** At every stop a small or specific request (a fix, a
  wording change, a selected element, a follow-up to answers it already has) gets no card at
  all. For anything larger the stop is followed, sized to the piece. At `100` the agent builds in
  parts and never all at once: anything that had a plan gets check-ins, the first after the first
  part; a one-page piece two or three in all, a long report one per section, small parts grouped.
  (The first real run at `100` asked, agreed its plan, then built a short report whole: the
  wording now says that this is the one thing the person asked it not to do.)
- Cleaned options carry `involve` only when it is above `0` (`turn_started`, `turn_queued`, the
  stored `turn_options`), so a request without it is recorded exactly as before. A `check_only`
  or `polish` request never asks: `involve` is dropped from it. `ask: true`, the on/off switch
  this scale replaced within the day, is read as `involve: 50` and never written.
- The project the API returns always has `turn_options` with all three keys
  (`{depth, verify, involve}`; a project that never chose has `standard`, `false`, `0`). The
  interface shows the scale only when `turn_options.involve` is a number, so an interface built
  since, on a server started before this existed, hides it (that server would refuse the option).

## What the agent receives

Above `0` the `<studio-effort>` block gains one part, first (the wording is `effort.INVOLVE`,
with the rest): the stop's own instruction, then how to ask (the tool by its full name, choices
with its own pick first and marked, never what the request, the person's instructions, the
project's context or the page already answer, never permission in general), the rule that
matches the asking to the work, and that it names in its closing message what it assumed for
anything it decided itself.

After a field test of 24 real requests (20 November 2026) the wording also says: every choice
is an answer in itself, and what only the person has is asked without choices; how much to do
is already set by Depth and is never asked; where the place, currency or units would change the
result and nothing says which, it asks and does not assume the United States; when a page or
report has real freedom in how it looks, one question may be about the look; and at `75` and
`100` a short or one-page piece still gets its plan (only a small edit goes without).

## The plan is not forgotten

At `75` and `100` the plan card rested on the wording alone, and in the field test one model
skipped it for a short piece. Code cannot tell a small edit from a piece that deserves a plan,
so it does not wall the agent in; it makes sure the choice is made on purpose:

- The first time in a request that the agent goes to change a file (the agent program's own
  Write, Edit, MultiEdit or NotebookEdit) before any `plan` card has been shown in that request,
  the change is refused once, with a reminder: the person asked to agree the plan first; a short
  or one-page piece still gets its plan; show it now with `ask_person` (`kind: "plan"`) and
  build after they answer; if this really is a small edit, a fix, or a follow-up to a plan they
  already agreed in this conversation, make the same change again and it goes through.
- Once per request. Not when a plan card was shown, not after the person told the agent to go on
  by itself, not when the request has no cards left, not below `75`.
- It does not see a file written through the shell, and it does not make a check-in happen at
  `100`: that still rests on the wording.

## The tool `ask_person`

```
{kind?: "questions" | "plan" | "checkpoint",
 title?: string (≤ 80), summary?: Markdown (≤ 1,500 chars),
 references?: [{label (≤ 80), url? (http or https, ≤ 500), path? (a file in the project), note? (≤ 200)}],
 questions?: [{question (≤ 300 chars),
               choices?: [{label (≤ 80), description? (≤ 200), pick?: boolean} | string],
               multiple?: boolean}],
 why?: string (≤ 300)}
```

- **Three kinds of card.** `questions` (the default): things the agent needs to know. `plan`:
  what it means to build, in `summary` (sections in order with a line each; for an app, the
  screens or features), before it builds. `checkpoint`: a part it has made, named in `title`,
  with what it made and found in `summary`. `references` are what the plan or the part rests on:
  a source's address, or a file in the project. An unknown `kind` is `questions`.
- **Cleaning.** The tool's schema asks for objects; a plain string for a question or a choice is
  taken as its text. White space is folded and texts are cut at a word (`summary` keeps its
  lines). A question with no text is dropped. Choices with an empty or repeated label (letter
  case ignored) are dropped; at most 6 are kept; a question left with fewer than 2 has none and
  is answered in words. `multiple` counts only with choices. `pick: true` marks the choice the
  agent would take itself; at most one a question is kept (the first), and the card says so
  beside it. A choice that is no answer but a promise of one (its label starts "I'll" or "I
  will": "I'll paste it", "I will send the targets") is dropped: what only the person has is
  asked in words, without choices. At most 4 questions are kept,
  numbered `q1`…`q4`. A reference without a label is dropped; a `url` that is not http(s) is
  left out of it, and so is a `path` that is not a file inside the project; at most 8 are kept.
- **A card always has a question.** A `plan` or `checkpoint` with none gets the studio's own:
  "Go ahead with this plan?" (*Go ahead* / *Change something*) or "Carry on to the next part?"
  (*Looks good, carry on* / *Change this part*). A `questions` card with none → the tool answers
  with an error.
- **It waits for the person**, like an approval: one `question_request` event, then the request
  is paused until the person answers or skips, or the request ends.
- **When it does not ask** (the tool answers with an error that says what to do instead, and
  writes no event): `involve` is `0` for this request ("decide yourself and say what you
  assumed"); no request is running; this request has used the cards its stop allows; the person
  skipped an earlier card in this request.
- **What it answers.** After an answer: `{answered: true, answers: [{question, chosen: [label],
  text}], cut, left, next}`. A question the person left empty has `chosen: []`, `text: ""` and
  `unanswered: true`. `cut` is how many questions over the limit were not asked; `left` is how
  many more cards this request may show. After a skip: `{answered: false, next}`. `next` tells
  the agent what follows: what the person typed outranks a choice; an unanswered question, or a
  skip, means decide and say what was assumed; after a skip, do not ask again in this request.
- The agent program's own `AskUserQuestion` tool is taken away from the agent (and, should it be
  called all the same, refused with a line that points to `mcp__studio__ask_person` above `0`,
  or says to decide at `0`): there is one card, one path.
- The scripted agent (`STUDIO2_FAKE=1`) asks two questions labelled "(fake)" before its work when
  `involve` is above `0`, unless the message contains "no questions".

## Events

```json
{"type": "question_request", "question_id": "k_1a2b3c4d", "kind": "plan", "title": "",
 "why": "So the page is built once.",
 "summary": "1. **Headline**: the total and the leading region\n2. **By region**: a bar chart …",
 "references": [{"label": "Q3 figures", "path": "data/q3.csv", "note": "the four regions"},
                {"label": "RBI bulletin", "url": "https://example.org/bulletin", "note": ""}],
 "questions": [{"id": "q1", "question": "Go ahead with this plan?", "multiple": false,
                "choices": [{"label": "Go ahead", "description": ""},
                            {"label": "Change something", "description": "Say what in your own words."}]}]}
{"type": "question_resolved", "question_id": "k_1a2b3c4d", "by": "user", "skipped": false,
 "answers": [{"id": "q1", "chosen": ["Go ahead"], "text": "Keep the chart above the table."}]}
```

A reference has `label`, `note` and at most one of `url` and `path`. `by`: `user`, or `turn_end`
(the request ended first, or the studio was restarted while the card waited: `skipped: true`,
`answers: []`). An answer taken in the instant the request ended is still recorded as the
person's (`by: "user"`). A skip by the person is `by: "user"`, `skipped: true`, `answers: []`.
`answers` has one entry per question, in order. Every `question_request` is followed by exactly
one `question_resolved` before the turn's `turn_finished`. While a card waits the project's
`running_turn` stays set; Stop ends the request as at any other point.

## REST

`POST /api/projects/{id}/turns/{turn_id}/questions/{question_id}` with
`{answers: [{id, chosen?: [label], text?: string}]}` or `{skip: true}` → 204.

- 404 `unknown_question` when it is not waiting (answered, skipped, or the request has ended).
- 400 `invalid_answer`: an `id` the card does not have, or one given twice; `chosen` that is not
  a list of that question's labels, or more than one for a question that is not `multiple`;
  `text` that is not text or is over 2,000 characters; no question answered and no `skip`.
- A question left out of `answers` is unanswered. `text` is trimmed and may stand beside a choice
  or instead of one.

## UI

- **The scale** is the last setting in the strip under the composer, **Involve** with its
  percentage. It opens a slider headed **Involve me** with the five stops, the name of the stop
  it is on, its percentage and one line saying what the agent will do there. Arrow keys move it
  a stop.
- A choice the agent marked as its own pick carries the word *Suggested*.
- **The card** appears in the chat where the request paused, like an approval card. Its heading
  follows the kind ("A few questions before I start", "The plan, before I build", "Check-in:
  <title>"); then the agent's reason (`why`), the `summary` as formatted text, and the
  references as a list: an address opens in a new tab, a project file opens in the Files panel.
  Then each question with its choices (one, or several when `multiple`) and a box for an answer
  in the person's own words; a question without choices has only the box. **Send** is off until
  one question has an answer. The second button sends the skip and says what that means for the
  kind: "Skip, decide for me", "Go ahead, no more questions", "Finish it without me". Once
  resolved the card becomes a record: each question with what was answered, "No answer" for one
  left empty, or one line saying the person told the agent to go on by itself, or that the
  request ended first.
- A card taller than the chat panel is shown from its top. A waiting card counts as "waiting for
  you" wherever a waiting approval does (the collapsed chat bar on a phone, the page's title).

## Choices made here

- **`0` unless the person moves it**, per project. Nothing is asked in a project that did not
  choose it.
- **The request waits; it does not end.** The answer goes back into the same request, so the
  agent carries on with everything it had read. A studio restart ends a waiting request like any
  running one. At `100` a long piece of work waits for the person many times; that is the point
  of the stop, and the skip button ends it for the rest of the request.
- **The stop caps the cards** (1, 2, 4, 12), four questions each, so no stop turns a request
  into an endless interview.
- **Answers are not kept as instructions.** They belong to the request; what the person always
  wants goes in "Instructions for a project", which the agent is told to read before asking.

# The chat panel on a desktop: its size, and where the options are

Interface only; nothing here changes the API.

- **Size.** The panel floats at the bottom right of the page, `clamp(380px, 32vw, 460px)` wide
  and as tall as its conversation up to 70% of the window. On a desktop the person drags its
  left edge (wider), its top edge (taller) or their corner (both); it stays at least 360 by 320
  and leaves 20 px around itself. A button in the panel's header switches between that size and
  a large one (680 wide, the full height); a double-click on an edge returns the usual size.
  The size is kept in the browser (`chat-size`, `chat-large`), for every project, not on the
  server. A phone or tablet sheet has none of this.
- **What you do, and how the request runs, are apart.** The composer's own row holds actions:
  attach files, add context, instructions, dictate, *Check this page now* (verify content, polish
  the look), then Stop and Send. Under the composer a strip names each setting with what it is
  set to: **Model**, **Depth**, **Verify** (a switch, one click) and **Involve** (shown only when
  the server sends `turn_options.involve`). Each opens its own small picker. A setting that is
  switched on is shown in the accent colour.
- On a phone the strip wraps to a second line when it must, and it is not shown while the sheet
  is collapsed to its bar.
- **Glass.** The chat panel and the top bar are translucent, with what is behind them blurred
  through (`backdrop-filter`): the panel floats over the page, so the page shows through it. The
  top bar has the same material but sits above the page, not over it, so nothing passes behind
  it and it reads as a soft tint; making it see-through would mean floating it over the top of
  every page, which was not done. Both are opaque enough (80%) that muted text keeps its contrast
  over a dark page, and both are solid where the browser cannot blur or the person asked the
  system for less transparency. Seen in a browser without graphics acceleration at double pixel
  density, the blur stops 1,024 device pixels down a tall panel; with acceleration it does not.

# Files are dropped on the chat, nowhere else

Until 24 November 2026 a drag carrying files anywhere over a project covered the whole screen
with "Drop to add to the project". A browser can report an ordinary picture dragged inside the
page as such a drag, so the cover appeared when nobody was adding a file. It is removed.

- **The chat takes drops**, open or closed: the files are uploaded to `attachments/` and go
  with the next message, as before. A drop on the closed chat opens it with the files attached.
- **The Files panel keeps its own drop area** (a drop there adds to the project's top folder).
- **Anywhere else a dropped file does nothing.** A browser would otherwise replace what is
  shown with the file, so the shell cancels such a drop on its window, and the bridge cancels
  it in the page. A page with a drop area of its own still gets its files: the bridge acts only
  when the page has not handled the event.
- The bridge no longer sends `file-drag`, and the shell ignores it from an older page.

# A picture in the page opens large

A diagram, a chart or an image in a page is as large as the page lets it be; on a phone a wide
diagram is a quarter of its size. The studio offers to open it large. Nothing is added to the
page's files: an exported page, or one opened outside the studio, has no such button.

**The bridge.** It offers nothing until the shell says so (shell → frame `enlarge-offer`, no
fields, sent after every `ready`), so a thumbnail of a page never shows a button. Then, in
`browse` mode only, an "Enlarge" button (one of the bridge's own elements) sits at the top
left corner of a picture (the studio's chat floats over the right side of the page):

- A picture is an `<img>`, a `<canvas>` or an outermost `<svg>`, drawn at least 160 px wide and
  100 px high, visible, and not inside a link, a button, a `label`, a `summary`, something with
  `role="button"` or something being edited. That leaves out icons, logos and pictures that
  already do something when pressed.
- With a mouse the button shows on the picture under the pointer. On a touch screen, which has
  no hover, it shows on the pictures in view (six at most).
- Pressing it sends frame → shell `enlarge`:

  | Field | |
  |---|---|
  | `blob` | the picture as a file: the image's own bytes (fetched again from its address), a canvas as PNG, or an inline SVG written out as a file with the styles the page gave it |
  | `url` | instead of `blob`, when the image's bytes cannot be read (another site without permission): its http(s) address |
  | `alt` | the picture's text alternative, 300 characters at most |
  | `background` | the colour behind the picture in the page, as the browser reports it (`rgb(…)` or `rgba(…)`), so a drawing made for a dark page is shown on dark |
  | `width`, `height` | its own size in CSS pixels |

  A canvas the page may not read, or an image that cannot be fetched and has no http(s) address,
  sends nothing and the button says "Can't enlarge this one".

**The shell.** The page is not trusted, and it can send this message without anyone pressing
anything. The shell accepts `enlarge` from the visible frame only, with a `blob` of 25 MB at
most whose type is a picture's (PNG, JPEG, GIF, WebP, AVIF, BMP, SVG) or an http(s) `url`, and a
`background` only in that form. One picture is open at a time, and a message that arrives
while one is open, or within 0.6 s of one being closed, is dropped. The picture is shown as the
source of an `<img>` and in no other way (an SVG shown so never runs a script), from a `data:`
address made of its bytes and the accepted type: never a `blob:` address, which would belong
to the studio's own origin and, opened in a tab of its own, would run an SVG's script as the
studio. The viewer carries the studio's own words, "A picture from the page", above the
picture. It opens over the whole studio as a modal dialog:

- It starts fitted to the window, never above its own size. When fitting would leave it under
  60% (a wide diagram on a phone) it starts at 80% from the top left instead, so it can be read
  at once.
- Drag moves it; the wheel, a pinch and a double press zoom at that point; `+`, `−`, `Fit` and
  close are buttons, and `+`, `-`, `0`, the arrow keys and Escape do the same. It zooms to four
  times its size and cannot be dragged out of sight. A canvas is pixels and blurs when enlarged;
  an SVG stays sharp.
- Closing it gives the page back as it was; nothing is kept.

# Long pasted text becomes an attachment

Text pasted into the message box that is 2,000 characters or 40 lines long, or longer, is not
put in the box. It is kept as a file, `attachments/pasted-text-<date>-<time>.txt`, uploaded like
any attachment, and shown above the box as "Pasted text" with its size. The agent gets it as
one of the message's `attachments` and reads it from the project folder; the thread shows the
same card, not the text.

- **"Put in message"** on the card writes the text into the box at the cursor and takes the
  card away (the person wanted it typed in after all). The file stays in `attachments/`, as a
  removed attachment's does.
- **"Keep in project"** also saves it in the project's top folder under the same name.
  `attachments/` is in the project folder and in the Files list too, but it is what the studio
  saved for a message and is left out of exports and templates; the copy in the top folder is
  the person's own file and goes with them.
- Shorter text is pasted as before. Nothing here needs the server: it is the upload and the
  `attachments` that exist already.

# Show what changed, when asked

After a request the page may read differently in a place the person is not looking at. Nothing
moves by itself: under the newest answer there is "Show what changed on the page", and pressing
it outlines the places that changed for a few seconds and scrolls to the first.

- **The bridge says it can** with `changes: true` in `ready`. The shell offers the link only then.
- shell → frame `changes-base` (no fields), sent when a request begins. The bridge notes what the
  page says: one line for each element that has words of its own (its tag and its text, 300
  characters at most) and for each image (its address), 4,000 at most, kept in the tab's session
  storage under the page's path. It is never sent anywhere and is left out of the sign-in report.
- shell → frame `show-changes` (no fields). The bridge compares the page as it is now with that
  note. A piece whose line is not in the note is a changed place; one inside another counts once.
  It answers frame → shell `changes` with `count` (a number, or `null` when the tab holds no note:
  a new tab, or a page too long to note), outlines up to 40 places for seven seconds and scrolls
  the first into view.
- **What it cannot show:** text that was only removed leaves nothing to outline, a change on
  another page is not on this one, and a page drawn by scripts after it loads may differ in ways
  the request did not cause. The shell says so when the count is 0.

# What the agent is told once, and what with each message

Everything the agent is told that does not change between requests is said once, in the system
prompt; only what can change rides in each message. This replaces "sent with every message" for the
project facts wherever an earlier section says so.

- **The system prompt** is `library/system-prompt.md` (the page, the back end and its rules, data and
  sources, the studio's tools, skills and helpers, long work, limits, working with the person) plus
  `TurnRequest.context`, the part that depends on this studio's configuration and on no project or
  request (`Studio.system_context`: whether it can see pages, render Office files and video, generate
  media, give apps a model address, and the studio tools it does not have, with the reason, so a
  skill that names one is skipped and said instead of followed blindly). The harness passes the
  system prompt with `snapshot: false`, so it is sent again on every request, a resumed
  conversation included. Tried on CLI 2.1.327 with SDK 0.2.180 (24 November 2026): without it a
  resumed session answered with the marker word of the system prompt it started with; with it, the
  new one; the cached start of the conversation was still read after a commit in the project.
  The text must stay the same from one request to the next, or that cache is written again.
- **Where that text travels.** Never on the agent program's command line, where `ps` shows it to
  every process on the computer and a cleanup job that matches words in command lines once killed a
  request for a tool's name in "On this studio". The harness writes it per request to
  `<data>/run/mcp/prompt-<random>.md` (mode 600, in the folder of mode 700 that holds the
  connector settings, outside every project) and passes only its path, with
  `--append-system-prompt-file` (`extra_args`; the SDK's `append` would put the text in argv). The
  file goes when the request ends, a failed start included, and a studio start removes any left
  behind. `snapshot: false` still reaches the program through its control channel, so a resumed
  conversation gets the current text. What stays on the command line is names and paths only: the
  model, the session id, `--disallowedTools AskUserQuestion`, `--plugin-dir`, the inline
  `--mcp-config` of the studio's own tools (`{"studio": {"type": "sdk"}}`) and the path of the
  connector file. The message goes through standard input; helper definitions go through the
  control channel. The library picker's small model call (`harness/cheap.py`), whose system text
  lists the library's skills in the person's own words, does the same with `--system-prompt-file`
  (a file in the same folder, removed when the call ends).
- **Precedence.** A `<studio-effort>` block and the person's involve setting override the defaults in
  the system prompt, including when to ask and how long the closing reply is (said once there).
- **The `<studio-project>` block** has two parts (`Studio.project_facts`). The standing facts (the
  project's address, how to restart and test its app and where its output is, an example's missing
  sources, the template in use, attached databases, context items, standing instructions, the
  sign-in sentence) go in full in the first message of a
  conversation and again only when they differ from what this conversation was last sent;
  otherwise one line says they are unchanged and the latest full block still holds. The facts about
  this request (what was picked from the library, a search connector to prefer, the app's state) go
  with every message: they are a few lines and change with nearly every request. So do the lines
  about an attached app (edited in place or a safe copy, what must never be touched, its ship
  recipe): they keep the person's own folder safe, a long request can be compacted, and a summary
  must never be all that is left of them.
- **What was sent** is kept in the project record as `facts_sent: {session_id, digest}` (a hash of
  the standing facts), written when a request completes, so a restart of the studio does not
  break it. The standing facts go in full when the project has no session, when the session is
  another one than `facts_sent` names, when the digest differs, after a request that did not
  complete, after the agent program reported a compaction (`compact_boundary`: `facts_sent` is
  cleared), and when a short-line message ended in another session. When a resume fails and the
  harness starts a new conversation it sends `fresh_prompt`, the same message with the facts in full.
- **Studio tools** are registered only where they work (`tools.registered`): `propose_ship_recipe`
  and `make_live` with an attached app, `database_schema` and `query_database` with an attached
  database, `look_at_page` and `render_image` with a browser, `render_video`, `render_document`,
  `render_diagram`, `convert_document`, `generate_media` and `generation_models` when their own
  check passes. The library page's list of studio tools (`tools.describe`) is unchanged.
- **Tool descriptions** say when to use the tool, the arguments that matter and what comes back;
  what is needed only after a call (limits passed, what to do after a skipped card) is in the
  tool's answer. A rule about calling a tool is said once: what to offer in `suggest_next_steps`
  and how to fill an `ask_person` card are in their descriptions; when to call them is in the
  system prompt and in the involve wording of `<studio-effort>`.
- **Helpers** (`library/agents/*.md`) each have a `tools:` line (the researcher a `disallowedTools:` line instead), which the agent program honours
  (tried on CLI 2.1.327: a helper given Read, Glob and one connector tool had no Bash and no Write,
  and its connector tool was there without loading). checker: Read, Grep, Glob, Write (for
  `research/review.md` only; the list cannot name a path), check_page, check_file, read_source,
  fetch_source, query_files, database_schema, query_database, look_at_page. researcher: everything
  the lead has except Bash, Edit and the tools that speak for the lead (ask_person, make_live,
  report_verification, propose_ship_recipe, save_skill, save_template, suggest_next_steps,
  generate_media); it has no list of its own because a list shuts out the tools of a search
  connector the person attached, which are named after the connector. section-writer: Read, Grep, Glob, Write, Edit, Bash (it runs
  its section's build). slide-reviewer: Read, Glob, Write, render_document. None has ask_person,
  make_live, report_verification or suggest_next_steps. Each brief starts with one sentence on the
  goal of the whole request.

