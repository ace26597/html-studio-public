---
name: studio-page-basics
description: How to structure a readable single-page report or dashboard in a Studio project - layout, typography, chart sizing and the source list. Use when starting a new page or when a page looks cramped or hard to read.
---

# Studio page basics

Use this as the default shape for a report or dashboard in `index.html`.

## Layout
- One centered column for reports: `max-width: 72rem`, generous side padding, and prose
  blocks limited to about 70 characters per line (`max-width: 68ch`).
- Dashboards: a short header, a row of 3-5 headline figures, then a 12-column CSS grid of
  cards. Charts take 6 or 12 columns; never squeeze three charts into one row.
- Order: title → one-paragraph summary or key figures → detail sections → method and sources.
- Add a small table of contents when there are more than four sections. On a long page make
  it a jump menu that stays reachable, and fold reference material (long tables, appendices,
  the source list) in `<details>`: a page many screens long on a phone is read from its menu.

## Typography and colour
- System font stack, 16px base, line-height 1.5; headings 1.25-2rem, sentence case.
- Define colours as CSS variables on `:root` and override them inside
  `@media (prefers-color-scheme: dark)`. Check text contrast in both.
- Tables: right-align numbers, use `font-variant-numeric: tabular-nums`, keep units in the header.

## Charts
- Minimum height 280px; label both axes with units; start bar-chart axes at zero.
- Put the takeaway in the chart title ("Revenue grew in every quarter"), the description below it.
- Inline SVG or a canvas library from a CDN are both fine. Keep the data in a file under
  `data/` or in one clearly marked `<script type="application/json">` block, not scattered in markup.
- If a series is incomplete, show the gap and say why; do not interpolate silently.

## Sources
- Read what you cite with the `fetch_source` tool and mark each figure with `data-src` and
  `data-quote`; the `sourced-report` skill has the details and the checked-numbers appendix.
- End the page with a "Sources" section: a numbered list with title, publisher, the date it
  was saved and a link to the source's address that can be clicked. Cite in the text with a linked number.
- State clearly what could not be found.

## Build order
1. Save a skeleton with the title, section headings and placeholders that say "loading".
2. Fill sections one at a time, saving after each, so the preview shows progress.
3. Add charts, then the source list, then check light and dark.
