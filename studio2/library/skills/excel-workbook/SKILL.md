---
name: excel-workbook
description: Build an Excel workbook with openpyxl that a person can trust and recalculate - real formulas, inputs separated from calculations, number formats, checks - and verify every formula by rendering it. Use for any request for a spreadsheet, a model, a table export or "the numbers in Excel".
---

# Excel workbook

Read `office-files` first (where files go, the build-render-look loop, how to show the file).

## Sheets, in this order
1. **Read me**: what the workbook is, the date, what each sheet holds, how to change an
   assumption, and the sources.
2. **Inputs**: every assumption and hard-coded parameter, one per row: label, value, unit,
   source. Nothing calculated here.
3. **Data**: the raw tables, one table per sheet or clearly separated blocks, header in row 1,
   no blank rows, no merged cells, one value per cell.
4. **Calculations**: everything derived, by formula.
5. **Summary** (and charts): the few numbers and charts a reader wants first. All formulas.
6. **Checks**: tests that must hold (parts sum to the total, shares sum to 100%, no negative
   where impossible). Each is a formula returning TRUE or FALSE, with one cell at the top:
   `=IF(AND(B2:B20),"All checks pass","CHECK FAILED")`.
A small export needs only Data and Summary. Keep the order.

## Formulas
- Anything that follows from other cells is a formula: totals, differences, growth, shares,
  ratios, averages, lookups, ranks. Never paste a computed number where a formula belongs,
  and never compute in Python what the workbook should compute.
- No numbers inside formulas. `=B4*Inputs!$B$3`, not `=B4*0.21`. Name the important inputs
  (`wb.defined_names`) and use the names.
- Write formulas as English strings starting with `=`, comma separators:
  `ws["D2"] = "=B2-C2"`. Use `SUM`, `SUMIFS`, `AVERAGEIFS`, `COUNTIFS`, `INDEX`/`MATCH`,
  `IFERROR`, `ROUND`, `MIN`, `MAX`. Avoid dynamic-array and newest functions (`FILTER`,
  `UNIQUE`, `LET`, `LAMBDA`) unless asked: they do not work everywhere.
- Guard divisions: `=IF(C2=0,"",B2/C2)`. Same formula down a column; no one-off exceptions.

## Formatting
- A number format on every numeric cell: `#,##0`, `#,##0.0`, `0.0%`, `$#,##0`,
  `$#,##0.0,,"M"`, dates `yyyy-mm-dd`. Units in the header ("Revenue, $M"), not in the cells.
- Header row: bold, filled, wrapped, frozen (`ws.freeze_panes = "A2"`, or `"B2"` with a label
  column). Total rows: bold with a top border. Text left, numbers right.
- Set column widths from the content (longest value plus two, capped near 50).
- Inputs a person may change: blue font (`0000FF`). Formulas: black. Say so on Read me.
- Turn each data block into a table style or add an autofilter; add data validation where a
  cell has a fixed set of choices. Conditional formatting only where it answers a question.
- Charts: native (`openpyxl.chart`), on the Summary sheet, referring to cells, with a title
  that states the finding and axis titles with units.
- Do not style or write empty cells (no fills, borders or `""` on cells without a value): put a
  dash or leave the cell untouched. Set the print layout on wide sheets (landscape, fit to one
  page wide).

## Check
- `render_document` calculates every formula, stores the results in the file and lists any
  that came out as an error or empty. Fix every one and render again until there are none.
- Compare three or four computed totals with the figures on the page. If they differ, find
  out which is wrong before going on.
- Look at the preview with `look_at_page`: readable headers, no `####` or cut-off columns,
  number formats applied, the Checks cell saying all pass.
- A workbook with charts also returns `chart_pages`: one picture per sheet with the charts
  drawn. Open them with the Read tool and check the charts like any other (title, axes, labels).
