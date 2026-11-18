# The same idea for a long Word document and a large workbook

The plan files, the ledger, the workers, the checks and the review are the same as for a deck.
What changes is the frame and the unit a worker writes.

## A long Word document: Markdown per chapter against one reference file

- **The frame** is one reference document, `build/reference.docx`, whose named styles (Title,
  Heading 1 to 3, Body Text, Table, Caption, the list styles) carry the whole look from
  `build/look.json`. Make it once: `pandoc -o build/reference.docx --print-default-data-file
  reference.docx`, then set those styles' fonts, sizes, colours and spacing with a short
  python-docx script in `tools/` (run it with the docx skill's `uv run` line plus
  `--with python-docx==1.2.0`). Never style text inside a chapter.
- **A worker writes one chapter** as Markdown: `build/chapters/<nn>-<id>.md`, a `#` heading, then
  paragraphs, `##` sub-headings, pipe tables and `![caption](files/img/<name>.png)` pictures. Figures
  come from the ledger: the worker writes `{{fact:revenue_2025|million}}` and the assembler replaces
  each mark from `data/facts.json` with the helper's `format()`; an unknown key stops the build with
  its name. A mark left unreplaced is a template mark, which `check_file` reports as a leftover.
- **Assembly**, one command (`tools/assemble_document.py` or `.js`): replace the marks, then
  `pandoc build/chapters/*.md --reference-doc=build/reference.docx --toc -o files/<name>.docx`.
  Headings come out as Heading 1 to 3, which `check_file` reads, so its places read
  `Paragraph 12 under "Market"` and `by_part` names the chapter.
- **Without pandoc** (`command -v pandoc` prints nothing): the docx skill's docx-js route, with the
  styles defined once in `tools/doc_helper.js` (`paragraphStyles` by id) and one module per chapter
  that calls `h.heading()`, `h.paragraph()`, `h.table()` and `h.fact()`, exactly as for a deck.
- **Pages**: `render_document` draws 60 pages a call; a 120-page report is three calls with `pages`.

## A large workbook: one assumptions sheet and one builder per sheet type

- **The ledger is the Assumptions sheet.** `tools/build_<name>.py` writes it first from
  `data/facts.json`: one row per fact (key, label, value, unit, period, source) and a workbook name
  for each key, so every formula reads `=revenue_2025*(1+growth_2026)` instead of `=Assumptions!C7`.
  Its typed numbers are the ledger's, so `check_file` reports them by key.
- **One builder per sheet type**, written and tested once in `tools/workbook_helper.py`:
  `region_sheet(book, region)`, `summary_sheet(book, regions)`, `checks_sheet(book)`. A builder
  sets every style, width and number format; workers write the list of sheets and their inputs
  (`build/sheets/<id>.json`), not cells.
- **Formulas only for derived cells**; inputs are typed once, on Assumptions. A total, a share or a
  growth rate is a formula, never a typed number.
- **Then** `render_document` (it recalculates every formula with LibreOffice, stores the results in
  the file and lists formula errors), the xlsx skill's `recalc.py` when it says so, and `check_file`.
  A Checks sheet compares three or four totals with the ledger (`=ABS(B2-revenue_2025)<0.5`).

```python
from openpyxl.workbook.defined_name import DefinedName

def assumptions_sheet(book, facts, look):
    sheet = book.create_sheet("Assumptions", 0)
    sheet.append(["Key", "Label", "Value", "Unit", "Period", "Source"])
    for row, fact in enumerate(facts, start=2):
        sheet.append([fact["key"], fact.get("label", ""), fact["value"], fact.get("unit", ""),
                      fact.get("period", ""), fact.get("source", fact.get("formula", ""))])
        book.defined_names[fact["key"]] = DefinedName(fact["key"], attr_text=f"Assumptions!$C${row}")
    style_header(sheet, look)          # the helper's own: fonts, fills and widths from look.json
    return sheet
```

Workbook cells are not compared for conflicts by `check_file` (a model repeats its row and column
names on every sheet); the formulas are what keep the sheets consistent.
