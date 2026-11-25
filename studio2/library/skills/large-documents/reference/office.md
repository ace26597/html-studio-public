# Office files: the frame, the assembly, the checks and the review

The parts of the method that are only for a deck, a Word document or a workbook. Read the
`office-files` skill and the kind's own skill (`pptx`, `docx`, `xlsx`) too: their rules (structured
decks, safe fonts, the workbook order, rendering and `check_file`) hold here.

## The look

`build/look.json`: the look of this one document as named values (fonts, sizes, colours, margins,
footer), written from what the person asked for, their template or brand, or chosen for this
subject and audience. Its shape: `plan-files.md`.

## The frame, written once

Write the document's own helper from `look.json`, one function per layout, with placeholders
filled by name; it is the only code that sets a colour, a font, a size or a position. Section code
calls its functions and passes ledger keys, never numbers.

- **Deck** (pptxgenjs, as the `pptx` skill installs it): copy `deck-helper.js` to
  `tools/deck_helper.js` and `assemble-deck.js` to `tools/assemble_deck.js`, then change the
  helper's layouts to this document's look and layout types. `h.fact("<key>", {scale})` is the only
  way a figure reaches a slide; an unknown key stops the build with its name. A section module looks
  like `section-example.js`.
- **Word** (one reference file, styles by name) and **Excel** (an Assumptions sheet written from the
  ledger, one tested builder per sheet type, formulas only for derived cells): `word-and-excel.md`.

Build one section yourself with the helper (`--only <section id>`) and render it before handing out
the rest. Its `check_file` run is the ledger's first check: fix every fact `ledger.problems` names
before the helpers start.

## What a section helper is given

The module path (`build/sections/<nn>-<id>.js`, or `build/chapters/<nn>-<id>.md`) and the helper's
function list (for a deck, the functions of `tools/deck_helper.js`). Helpers never render: the
studio draws one file at a time, so the lead renders once.

## Assemble, then check without pictures, in this order

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

## The visual review

Start a `slide-reviewer` helper (not a writer) with the file, the slide numbers to look at and why,
the overview pictures and the outline. Up to 60 slides it looks at all; past that, every flagged
slide, the first of each layout and section, every chart, table and picture slide, and one in four
of the rest. It works in batches of 20 and writes defects by slide to `build/review.json`.
How, and the questions: `review.md`. Redo only the modules with defects, reassemble, re-run the
checks on the changed slides, then one second look at the changed slides only.

## What to tell the person

> Structure passed. 2 formula errors fixed. Figures: 412 checked, 409 from the ledger, 3 not in
> any source (slides 17, 41, 77). 0 leftover placeholders. 40 of 104 slides looked at, 6 defects
> fixed, 1 left (slide 63: a crowded table). Renders in LibreOffice.

## Changing one part later

Change the ledger (a figure) or the one module (a slide), never the built file. Reassemble, run
`check_file` on the whole file (it is quick), render only the changed slides' range, and look at
those. A change to `look.json` or the helper changes every slide: say so and review the overview.
