---
name: large-documents
description: Build a very large deck, Word document or workbook that holds together - a plan first (outline, look, glossary, a ledger of every figure), a helper written once, section helpers in parallel, one assembler, deterministic checks, then one visual review in batches. Use for a deck over about 25 slides, a document over about 30 pages, a workbook over about 8 sheets, or a "large", "full" or "complete" deck or a board pack.
---

# Large documents

One agent writing one script holds together at ten slides and falls apart at a hundred: the
script outgrows what can be re-read, the look drifts, one figure appears with two values, and
nobody can say which slide is wrong. This procedure fixes each of those with a file or a check.

**When.** A deck of more than about 25 slides, a document of more than about 30 pages, a workbook
of more than about 8 sheets, or when the person says "large", "full", "complete" or "board pack".
Below that, the `office-files` skill and the kind's own skill (`pptx`, `docx`, `xlsx`) are faster
and this is overhead. Read those skills too: their rules (structured decks, safe fonts, the
workbook order, rendering and `check_file`) hold here.

**Cost, said first.** A large build takes many agent turns and many minutes: the plan, several
helpers, the checks and a review. Before starting, tell the person how big the job is (slides or
pages, sections, how many helpers) and that it will take a while. Never quote a time or a price
you have not measured in this project.

## 1. The plan, before any slide

Write these, in the project, so a later request can change one slide without redoing the rest.
Their shapes, with an example: `reference/plan-files.md`.

- `build/outline.json`: sections; per slide an `id`, one `message` (the sentence it proves), a
  `layout` (one of the helper's functions), the ledger `facts` it shows and its `visual`.
- `build/look.json`: the look of this one document as named values (fonts, sizes, colours,
  margins, footer), written from what the person asked for, their template or brand, or chosen for
  this subject and audience. There is no house style: never reuse another document's look.
- `build/glossary.md`: names, terms and abbreviations, each written one way.
- `data/facts.json`, **the ledger**: every figure the document will show, with `key`, `label`,
  `value` (in full), `unit`, `period`, `source` and `obtained` (`quoted` from a saved source,
  `data` from a file under `data/`, `computed` with `from` and `formula`, or `given` by the person).
  Fetch and save the sources first (`fetch_source`); no figure enters the ledger without one.

`check_file` checks the ledger each time it runs on a file (its `ledger` block): `problems` names
every fact that is not in its source, not in its data file, or whose formula does not give its
value, and a fact with a problem vouches for nothing. The first time is the first section you
build in step 2: fix every fact it names before the helpers start.

**Show the outline and wait.** Make `index.html` the outline while the job is large: the title,
each section with its slides (number, message, layout, the figures and their sources), the facts
still missing, and the look in a sentence. End the request asking whether to build it. Build only
after a yes (or when the person said to go ahead without asking). While it builds, keep that page
as the progress board: each section's state (written, assembled, checked, reviewed).

## 2. The frame, written once

Write the document's own helper from `look.json`, one function per layout, with placeholders
filled by name; it is the only code that sets a colour, a font, a size or a position. Section code
calls its functions and passes ledger keys, never numbers.

- **Deck** (pptxgenjs, as the `pptx` skill installs it): copy `reference/deck-helper.js` to
  `tools/deck_helper.js` and `reference/assemble-deck.js` to `tools/assemble_deck.js`, then change
  the helper's layouts to this document's look and layout types. `h.fact("<key>", {scale})` is
  the only way a figure reaches a slide; an unknown key stops the build with its name. A section
  module looks like `reference/section-example.js`.
- **Word** (one reference file, styles by name) and **Excel** (an Assumptions sheet written from
  the ledger, one tested builder per sheet type, formulas only for derived cells):
  `reference/word-and-excel.md`.

Build one section yourself with the helper (`--only <section id>`) and render it before handing out
the rest: a fault in the frame is cheaper to fix once than in every module.

## 3. Section helpers

Start one `section-writer` helper per section, several in one message so they run together.
Each brief holds: the section's slice of the outline (pasted in), the module path to write
(`build/sections/<nn>-<id>.js`, or `build/chapters/<nn>-<id>.md`), the helper's function list, the
paths of `data/facts.json` and `build/glossary.md`, and this sentence: "Use numbers only by key;
if a number you need is not in the ledger, do not invent it: write the slide without it and list
it in your reply." Each returns only the module's path, its slide count and the missing facts;
the content stays in the files. Never paste a helper's content back through yourself.

**At most four at once**, then the next four as they finish. Helpers run inside the agent program
that started them and the request ends only when all have reported, so memory is not the limit on
a 16 GB computer; the account's rate of model requests and your own reading of their replies are.
Helpers never render: the studio draws one file at a time, so the lead renders once.

Missing facts: find their sources, add them to the ledger, and send the module back to its writer
with the keys; or leave the figure out and say so.

## 4. Assemble, then check without pictures, in this order

1. **Assemble with one command** (`NODE_PATH=tools/node_modules node tools/assemble_deck.js`). It
   stops when a module's slides do not match the outline, and writes `build/slide-map.json`.
2. **Structure**: the kind's own check (`validate.py` for a deck or document, as its skill runs it).
3. **`check_file`** on the file: `not_found` figures (not in the ledger, data or sources),
   `placeholders` (prompt text, template marks, TBD, lorem ipsum, empty placeholders), `conflicts`
   (one label with two values), `ledger.problems`. `by_part` says which slides; the slide map says
   which modules. Fix those modules (or their writers do), reassemble, check again.
4. **`render_document`**: a workbook's formula errors; for a deck or document the PDF and up to
   60 page pictures a call (`pages: "61-120"` for more; `not_drawn` says what has no picture yet),
   and `overview`, every page small.
5. **`check_file` again** after the last render, before anything is handed over.

## 5. One visual review, by someone else

Start a `slide-reviewer` helper (not a writer) with the file, the slide numbers to look at and why,
the overview pictures and the outline. Up to 60 slides it looks at all; past that, every flagged
slide, the first of each layout and section, every chart, table and picture slide, and one in four
of the rest. It works in batches of 20 and writes defects by slide to `build/review.json`.
How, and the questions: `reference/review.md`. Redo only the modules with defects, reassemble,
re-run step 4 on the changed slides, then one second look at the changed slides only. Then stop
and report what remains.

## 6. What to tell the person

Each gate on its own, with numbers, and what was not covered:

> Structure passed. 2 formula errors fixed. Figures: 412 checked, 409 from the ledger, 3 not in
> any source (slides 17, 41, 77). 0 leftover placeholders. 40 of 104 slides looked at, 6 defects
> fixed, 1 left (slide 63: a crowded table). Renders in LibreOffice.

Say "renders in LibreOffice", never "opens in PowerPoint" or "works in Word" or "Excel": nothing here
can test those programs. Name every check you did not run and every cap the results reported.

## 7. Changing one part later

Change the ledger (a figure) or the one module (a slide), never the built file. Reassemble, run
`check_file` on the whole file (it is quick), render only the changed slides' range, and look at
those. A change to `look.json` or the helper changes every slide: say so and review the overview.
