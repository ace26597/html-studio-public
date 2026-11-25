---
name: large-documents
description: Build a very large document that holds together: a long sourced report, a site of many pages, or a big deck, Word file or workbook, with a plan, one ledger of figures, a helper per section, one join and checks. Use above about 12 sections, 8 pages of facts, 25 slides, 30 pages or 8 sheets.
---

# Large documents

One agent writing one file holds together at ten slides or one short page and falls apart at a
hundred: the file outgrows what can be re-read, the look drifts, one figure appears with two
values, and nobody can say which part is wrong. This method fixes each of those with a file or a
check. It is the same for Office files and for pages; what differs is in two reference files.

**When.** A long HTML report (more than about 12 sections or 150 figures), a site or a report in
chapters of more than about 8 pages that state facts, a deck of more than about 25 slides, a
document of more than about 30 pages, a workbook of more than about 8 sheets, or a board pack.
Below that, one agent does it well and faster: a single page (`sourced-report`,
`studio-page-basics`), a small site (`multi-page-site`) or the Office skills. (An 8-section
sourced report of 100 figures came out clean that way in a few minutes.)

**Read the part for your kind now**:
- Office files (deck, Word, Excel): `reference/office.md`, and the `office-files` skill with the
  kind's own (`pptx`, `docx`, `xlsx`).
- Pages (a long report, a site): `reference/html.md`, and the `sourced-report` and, for a site,
  `multi-page-site` skills.

**Cost, said first.** A large build takes many agent turns and many minutes: the plan, several
helpers, the checks and a review. Before starting, tell the person how big the job is (pages,
slides or sheets, sections, how many helpers) and that it will take a while. Never quote a time or
a price you have not measured in this project.

## 1. The plan, before any section

Write these in the project, so a later request can change one part without redoing the rest.
Their shapes, with an example: `reference/plan-files.md`.

- `build/outline.json`: the sections (or pages, or sheets); for each part an `id`, one `message`
  (the sentence it proves), its layout or kind, the ledger `facts` it shows and its `visual`.
- **The look, once**: `build/look.json` for an Office file; the shared stylesheet
  `styles/site.css` with the `theme` tokens for pages. Written from what the person asked for, their
  template or brand, or chosen for this subject and audience. Never reuse another document's look.
- `build/glossary.md`: names, terms and abbreviations, each written one way.
- `data/facts.json`, **the ledger**, one for the whole project: every figure it will show, with
  `key`, `label`, `value` (in full), `unit`, `period`, `source` and `obtained` (`quoted` from a
  saved source, `data` from a file under `data/`, `computed` with `from` and `formula`, or `given`
  by the person). Fetch and save the sources first (`fetch_source`); no figure enters the ledger
  without one. Pages add `display` and, for data, `sql` (`reference/html.md`).
- Write the project's conventions (file layout, naming, the look's file, the join command) in the
  project notes (`notes/project.md`), so a later request follows them. The notes hold decisions,
  not progress.
- **The plan is `mcp__studio__set_plan`**: one item per section (or page, or sheet), plus the
  frame, the join, the checks and the review. Mark each as it starts and ends; it is the only
  record of progress, and it comes back in later requests.

**Show the outline and wait.** Make `index.html` the outline while the job is large: the title,
each section with its parts (number, message, kind, the figures and their sources), the facts still
missing, and the look in a sentence. End the request asking whether to build it. Build only after
a yes (or when the person said to go ahead without asking). That page is for the person, not for
you: while the sections are written it may say which are done, from the plan, and it goes when the
first join or assembly writes the real `index.html` (for an Office file, when the preview of the
built file takes its place). Never keep progress on the page that is not also in the plan.

## 2. The frame, written once

Write what every section shares before any helper starts: the deck helper, the Word reference file
or the workbook builders (`reference/office.md`); the stylesheet, the page shell and the joiner
(`reference/html.md`). It is the only place that sets a colour, a font or a size, and sections
reach figures only by ledger key. Build one section yourself and check it before handing out the
rest: a fault in the frame is cheaper to fix once than in every section. That first check is also
the ledger's: fix every fact it names before the helpers start.

## 3. Section helpers

Start one `section-writer` helper per section (or page), several in one message so they run
together. Each brief holds: one sentence on the goal of the whole request, the section's slice of
the outline (pasted in), the module path to write, what the frame offers (the helper's function
list, or the stylesheet's classes and the shell), the paths of `data/facts.json` and
`build/glossary.md`, and this sentence: "Use numbers only by key; if a number you need is not in
the ledger, do not invent it: write the part without it and list it in your reply." Each returns
only the module's path, what it made and the missing facts; the content stays in the files. Never
paste a helper's content back through yourself.

**At most four at once**, then the next four as they finish. Helpers run inside the agent program
that started them and the request ends only when all have reported, so memory is not the limit;
the account's rate of model requests and your own reading of their replies are.

Missing facts: find their sources, add them to the ledger, and send the module back to its writer
with the keys; or leave the figure out and say so.

## 4. Join, then check without pictures

Join the sections with one command (the deck assembler, pandoc, or `tools/join_pages.py`), then run
the checks in the order your reference file gives. In short:
- Office: the kind's structure check, then `check_file` (figures not found, leftover placeholders,
  conflicts, `ledger.problems`), then `render_document`, then `check_file` again.
- Pages: `check_site` until it reports no findings, then `check_page` on every page that states
  facts, then `look_at_page` on the start page and inner pages.
Fix in the ledger or the module, never in the joined file, which the next join overwrites.

## 5. One review, by someone else

A helper that did not write the parts looks at the result: `slide-reviewer` for a deck or document
(batches of slide pictures, defects to `build/review.json`), `checker` for pages (every page's
marks against their sources, to `research/review.md`). Redo only the modules with defects, join,
run the checks on the changed parts, then one second look at those only. Then stop and report
what remains.

## 6. What to tell the person

Each gate on its own, with numbers, and what was not covered: how many parts, figures checked and
from where, placeholders or site findings left, how many parts were looked at, defects fixed and
left. An example for each kind is in its reference file.

Say "renders in LibreOffice", never "opens in PowerPoint" or "works in Word" or "Excel": nothing here
can test those programs. Name every check you did not run and every cap the results reported.

## 7. Changing one part later

Change the ledger (a figure, everywhere it appears) or the one module (a part), never the joined
file. Join again, run the checks on the whole (they are quick), and look at the changed parts. A
change to the look or the frame changes every part: say so and look at several.
