---
name: charts
description: Choose the right chart for a question and build it so it is readable, honest and themed. Use whenever a page needs a chart, graph or plot, or an existing chart is hard to read.
---

# Charts

## Pick by the question
| Question | Chart |
|---|---|
| How did it change over time? | line (many points) or columns (few periods) |
| How do items compare? | horizontal bars, sorted, zero-based |
| What is it made of? | stacked bar, or a single 100% bar; a pie only for 2–3 parts |
| How are two measures related? | scatter with labelled axes |
| How is it distributed? | histogram |
| One number against a target or last period | a figure with a small delta, not a chart |

More than six series: show the top five and "other", or use small multiples.

## Build
- Apache ECharts from a CDN is the default for anything interactive:
  `<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>`.
  A simple static chart can be inline SVG.
- Give the container an explicit height (320–420px) and call `chart.resize()` on window resize.
- Title states the finding ("Gross margin fell four points in two years"); a subtitle gives
  the measure, unit and period. Label both axes with units. Format numbers ($1.3B, 47%).
- Bars start at zero. A line chart may start above zero only if the axis says so clearly.
  Never use two different y-axes to imply a relationship.
- Missing periods stay as gaps. Estimates and forecasts are drawn differently (dashed) and labelled.
- Colours and text colour come from the theme variables; redraw when the colour scheme changes.
- Tooltips show the exact value with its unit and period.

## Keep it checkable
Put the chart's data in a table under it (inside `<details><summary>Data</summary>…</details>`),
with every value marked as the `sourced-report` skill describes, and have the script read the
values from that table or from the same JSON. One set of numbers, shown twice, checked once.

## Look at it
Use `look_at_page` after adding charts. Fix overlapping labels, clipped legends, unreadable
axis text and empty canvases before finishing. Check the console list for chart errors.
