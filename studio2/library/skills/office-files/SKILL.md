---
name: office-files
description: Make a PowerPoint deck, a Word document or an Excel workbook as a real file, check it, and show it inside the page with a download button. Use whenever the person asks for slides, a presentation, a document to send, a spreadsheet, a model or "an export", alone or next to an HTML report.
---

# Office files

Real `.pptx`, `.docx` and `.xlsx` files, built by a script, rendered, looked at, then shown.
Read the skill for the kind you are making as well: `powerpoint-deck`, `excel-workbook`,
`word-document`.

## Where things go
- The file: `files/<short-name>.pptx` (or `.docx`, `.xlsx`).
- The script that builds it: `tools/build_<short-name>.py`, run with
  `uv run --with python-pptx python tools/build_<short-name>.py` (`python-docx`, `openpyxl`,
  `matplotlib` the same way). Change the script and run it again; never patch the binary.
- Charts that go into a deck or document as pictures: `files/img/`.

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

## Build, render, look, fix
1. Write and run the build script.
2. Call `render_document` with the file's path.
3. Decks, documents: open **every** page image it returns with the Read tool. Workbooks: read
   the `errors` list and the first rows it returns, then use `look_at_page` on the preview.
4. Fix what is wrong in the script (text cut off or overflowing its box, uneven margins, a
   chart with unreadable labels, an empty page, a formula error, a column too narrow) and go
   back to 1. Two or three rounds is normal. Stop when you would hand it to a client.
5. In your answer say what you rendered and looked at, and anything you could not check.

Fonts: use Arial, Georgia or Courier New. They exist on every Mac and Windows machine, so the
file looks the same for the person as it did in your render.

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

Paths are relative to the page. Use about 720px of height for decks and documents, 620px for
workbooks, and 560px on narrow screens. Render again after the last change to a file, or the
viewer shows the old version. Look at the page afterwards like any other change.
