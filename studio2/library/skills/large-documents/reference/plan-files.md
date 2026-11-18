# The plan files

Written before any slide, kept in the project. The values below are an example of the shapes:
write every value from what this person asked for and this subject.

## `build/outline.json`

```json
{
  "title": "Regional review 2025",
  "file": "regional-review",
  "kind": "deck",
  "audience": "The board, reading before the meeting",
  "purpose": "Decide whether to open a third region",
  "sections": [
    {"id": "intro", "title": "Where we stand", "module": "build/sections/01-intro.js",
     "slides": [
       {"id": "intro-1", "layout": "cover", "message": "Regional review 2025"},
       {"id": "intro-2", "layout": "statement", "message": "Revenue held at $4.2m on fewer units",
        "facts": ["revenue_2025", "units_total"]}]},
    {"id": "regions", "title": "Regions", "module": "build/sections/02-regions.js",
     "slides": [
       {"id": "regions-1", "layout": "divider", "message": "Two regions carry the year"},
       {"id": "regions-2", "layout": "statement", "message": "North sold most of the units",
        "facts": ["units_north", "units_total"]},
       {"id": "regions-3", "layout": "chart", "message": "Units by region", "visual": "bar: units by region",
        "facts": ["units_north", "units_south", "revenue_per_unit"]},
       {"id": "regions-4", "layout": "table", "message": "The two regions side by side",
        "facts": ["units_north", "units_south"]}]}
  ]
}
```

- One `message` per slide: the sentence the slide proves. Read in order, the messages make the case.
- `layout` is one of the helper's functions. `facts` are the ledger keys the slide shows.
- A document: `"kind": "document"`, sections are chapters with `"parts": [{"id", "message", "facts"}]`
  and `"module": "build/chapters/03-market.md"`. A workbook: `"kind": "workbook"`, `"sheets": [{"name",
  "type", "facts"}]`, one builder per sheet `type`.

## `build/look.json`

```json
{
  "name": "Regional review",
  "size": "LAYOUT_WIDE",
  "fonts": {"heading": "Cambria", "body": "Calibri"},
  "sizes": {"cover": 44, "title": 32, "statement": 26, "body": 16, "table": 13, "caption": 11},
  "margin": 0.6,
  "footer": "Regional review 2025",
  "colors": {"dk1": "1E2430", "lt1": "FFFFFF", "dk2": "123B5C", "lt2": "E9EEF3",
             "accent1": "2F7D6D", "accent2": "C9732B", "accent3": "5B6C8F", "accent4": "9AA9BC",
             "accent5": "6E9E4C", "accent6": "B4475B", "hlink": "2F7D6D", "folHlink": "5B6C8F",
             "muted": "667085"}
}
```

The twelve theme colours (`dk1` … `folHlink`, six hex digits, no `#`) are written into the deck's
theme by `apply_theme.js`; `muted` and any other name are the helper's own. Fonts from the pptx
skill's safe list keep LibreOffice's pictures true to PowerPoint's widths.

## `build/glossary.md`

```
- Revenue: net revenue as reported, in US dollars.
- North, South: the company's two sales regions (never "Northern region").
- FY2025: the year to 31 December 2025.
```

Every worker gets it and writes each term exactly so.

## `data/facts.json` (the ledger)

```json
{"facts": [
  {"key": "revenue_2025", "label": "Revenue", "value": 4218950, "unit": "USD", "period": "FY2025",
   "source": "s1", "obtained": "quoted", "quote": "revenue was $4,218,950"},
  {"key": "units_north", "label": "Units sold, North", "value": 1520, "unit": "units", "period": "2025",
   "source": "data/sales.csv", "obtained": "data", "how": "column units, row North"},
  {"key": "units_south", "label": "Units sold, South", "value": 980, "unit": "units", "period": "2025",
   "source": "data/sales.csv", "obtained": "data", "how": "column units, row South"},
  {"key": "units_total", "label": "Units sold", "value": 2500, "unit": "units", "period": "2025",
   "source": "data/sales.csv", "obtained": "data", "how": "total of column units"},
  {"key": "revenue_per_unit", "label": "Revenue per unit", "value": 1687.58, "unit": "USD", "period": "FY2025",
   "obtained": "computed", "from": ["revenue_2025", "units_total"], "formula": "revenue_2025 / units_total"}
]}
```

- `value` in full (4218950, not 4.2), in its `unit`; a percentage as its number of percent (12.4, unit `%`).
- `obtained`: `quoted` (`source` a saved source id, `quote` its words), `data` (`source` a file under
  `data/`), `computed` (`from` and `formula` of those keys: `+ - * / **`, brackets, `abs min max round
  sum`), `given` (the person said it: `source: "person"`).
- `check_file` checks every fact (in the source, in the data file, the formula recomputed) and reports
  `ledger.problems`; a fact with a problem vouches for nothing. A figure the deck shows at a rounding
  ("$4.2m") is found from the full value.

## `build/slide-map.json` (written by the assembler)

```json
[{"slide": 1, "section": "intro", "id": "intro-1", "module": "build/sections/01-intro.js", "layout": "TITLE"},
 {"slide": 3, "section": "regions", "id": "regions-1", "module": "build/sections/02-regions.js", "layout": "SECTION"}]
```

`check_file`'s `by_part` ("Slide 41") and the reviewer's defects are mapped through it to the module to redo.
