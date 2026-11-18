---
name: office-files
description: Make a PowerPoint deck, a Word document, an Excel workbook or a PDF as a real file, check it, and show it inside the page with a download button. Use whenever the person asks for slides, a presentation, a document to send, a spreadsheet, a model, a PDF or "an export", alone or next to an HTML report.
---

# Office files

Real files, built by a script, rendered, looked at, then shown. This skill says what is the same
for every kind. How to build each kind is in its own skill; read that one too:

| The person wants | Skill | Built with |
|---|---|---|
| Slides, a deck, a presentation | `pptx` | pptxgenjs |
| A Word document, memo, letter, report to send | `docx` | docx-js |
| A spreadsheet, a model, a table export | `xlsx` | openpyxl |
| A PDF, or work on an existing PDF | `pdf` | reportlab, pypdf |

Each of those skills starts with a section "In this studio" that gives the exact commands here.

## Where things go
- The file: `files/<short-name>.pptx` (or `.docx`, `.xlsx`, `.pdf`).
- The script that builds it: `tools/build_<short-name>.js` or `.py`. Change the script and run it
  again; never patch the finished file by hand.
- Pictures that go into a file: `files/img/`.
- Everything temporary (PDFs for checking, page images, unpacked folders): `/tmp/<short-name>/`.
- A project that already has a build script keeps it and its library. Do not rewrite a working
  python-pptx or python-docx script unless the person asks.

## Content comes first, and it is the same content
- A file made from a report says what the report says. Take every figure from the project's
  data files and saved sources (`sources/index.json`, `sources/s<n>.txt`, `data/`), read by
  the script where possible, not retyped from memory. If the page has checked numbers
  (`checks/index.html.json`), use those values.
- Name the sources in the file itself: a last "Sources" slide, a references section, a
  "Sources" sheet. Use the same source ids as the page.
- Say less per unit than the page does. A deck is the argument in ten steps; a document is
  the page in prose a person can send; a workbook is the numbers someone can recalculate.
- No invented figures, no filler slides, no placeholder text left in.

## How it looks is the person's choice
Follow what they asked for, their template or their brand. When they said nothing, choose for this
topic and this audience. The studio has no house style.

## What each kind needs beyond its own skill
- **Deck:** write the outline first, one sentence per slide; read in order, the sentences must make
  the case. Each slide's title states the finding ("Margins fell four points as storage grew"),
  not a label ("Margins").
- **Document:** the summary comes first and stands alone: the answer, the reasons, what to do. Refer
  to every table and figure in the text. Mark sources as [1], [2] and end with a numbered list.
- **Workbook:** keep this order, and leave out the sheets a small export does not need:
  **Read me** (what it is, the date, how to change an assumption, the sources), **Inputs** (one
  assumption per row: label, value, unit, source), **Data**, **Calculations**, **Summary**, **Checks**
  (formulas returning TRUE or FALSE, with one cell at the top:
  `=IF(AND(B2:B20),"All checks pass","CHECK FAILED")`). Compare three or four totals with the
  figures on the page; if they differ, find out which is wrong before going on.

## Build, render, look, fix
1. Write and run the build script.
2. Run the checks of the kind's own skill: `validate.py` for a deck or document, `recalc.py` for a
   workbook with formulas.
3. Call `render_document` with the file's path.
4. Decks, documents and PDFs: open **every** page image it returns with the Read tool. Workbooks:
   read the `errors` list and the first rows it returns, use `look_at_page` on the preview, and open
   the `chart_pages` images when there are charts.
5. Fix what is wrong in the script (text cut off or overflowing its box, uneven margins, a
   chart with unreadable labels, an empty page, a formula error, a column too narrow) and go
   back to 1. Two or three rounds is normal. Stop when you would hand it to a client.
6. In your answer say what you rendered and looked at, and anything you could not check.

## Show it in the page
`render_document` writes a viewer page next to the file (`files/<name>.<ext>.preview/index.html`)
and returns an `embed` snippet. Put a "Files" section on the page (or next to the part of the
report the file belongs to) with, for each file: a heading saying what it is and what it is
for in one sentence, the framed viewer, and a plain link as a fallback.

```html
<section id="files">
  <h2>Files</h2>
  <article class="file">
    <h3>Board deck: the case for the battery line</h3>
    <p>12 slides. The argument of this report for a 20-minute meeting.</p>
    <iframe src="files/board-deck.pptx.preview/index.html" title="Board deck" loading="lazy"
            style="width:100%;height:720px;border:1px solid var(--line, #d9dce3);border-radius:10px"></iframe>
    <p><a href="files/board-deck.pptx" download>Download the PowerPoint file</a></p>
  </article>
</section>
```

Paths are relative to the page. Use about 720px of height for decks, documents and PDFs, 620px for
workbooks, and 560px on narrow screens. Render again after the last change to a file, or the
viewer shows the old version. Look at the page afterwards like any other change.
