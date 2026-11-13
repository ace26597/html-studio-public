---
name: word-document
description: Build a well written, well formatted Word document with python-docx - real heading styles, tables, figures with captions, references, page numbers - and check every page by rendering it. Use for any request for a Word file, a memo, a brief, a proposal, a letter or "a document I can send".
---

# Word document

Read `office-files` first (where files go, the build-render-look loop, how to show the file).

## Writing
- Decide the reader and the purpose, then the outline: title, a summary that stands alone
  (the answer, the three reasons, what to do), the sections, references. Summary first.
- Prose, not slides: full sentences, short paragraphs, one idea each. Lists only for things
  that really are lists. Every number with its unit and date. The same facts as the page.
- A memo is 1-3 pages, a brief 3-8, a report as long as the content needs. Do not pad.

## Styles, set once
- Use the built-in styles and change them at the top of the script, never format run by run:
  `Title`, `Subtitle`, `Heading 1`-`Heading 3`, `Normal`, `List Bullet`, `List Number`,
  `Caption`. Real heading styles give the reader a navigation pane and an automatic contents.
- `Normal`: Arial or Georgia 11 pt, line spacing 1.2, 8 pt after each paragraph, left-aligned.
  Headings: the same family or the other one, bold, dark, with space before (18/14/10 pt).
  Set the East Asian font name too (`rFonts`) so the font really applies.
- Page: Letter with 1-inch margins unless the person works in A4. A footer with the page
  number (a `PAGE` field) and a short title; no header on the first page.
- Title block on page one: title, subtitle, who it is for, date. No cover page for anything
  under ten pages.

## Tables and figures
- Tables: a header row that repeats on each page, header filled, numbers right-aligned,
  column widths set, 9.5-10.5 pt, a caption above ("Table 1. …"). Rows must not split across
  pages. Six columns at most on a portrait page.
- Figures: make charts with matplotlib from the same data as the page, 200 dpi, width about
  6 in, the title stating the finding, labelled axes with units; save under `files/img/`
  and insert with a caption below ("Figure 1. …  Source: s4").
- Refer to every table and figure in the text.

## References
- Mark sources in the text as [1], [2] and end with a numbered "References" list giving the
  title, publisher, date and address of each, plus the page's source id.

## Check
Render with `render_document` and open every page image. Look for: a heading left alone at
the bottom of a page (set keep-with-next), a table broken across pages, a figure pushed to
an almost empty page, wrong fonts, missing page numbers, a nearly empty last page. Fix in the
script and render again.
