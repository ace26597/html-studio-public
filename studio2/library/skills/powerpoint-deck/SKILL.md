---
name: powerpoint-deck
description: Build a professional PowerPoint deck with python-pptx - a clear argument, one message per slide, native charts, speaker notes - and check every slide by rendering it. Use for any request for slides, a deck or a presentation.
---

# PowerPoint deck

Read `office-files` first (where files go, the build-render-look loop, how to show the file).

## The argument
- Write the outline before any code: the audience, the one thing they should decide or
  believe, and one sentence per slide. If the sentences read in order do not make the case,
  fix the outline.
- Usual shape, 8 to 15 slides: title; the answer in three points; one section per point with
  its evidence; what it means / next steps; sources. Put detail in an appendix, not in the way.
- Each slide's title is a full sentence that states the finding ("Margins fell four points as
  storage grew"), not a label ("Margins"). Two lines at most.
- One message per slide: either a few words (40 at most), or one chart, or one table, or one
  number. If it does not fit, it is two slides. Never shrink text to make it fit.

## Layout: place everything yourself
- 16:9: `prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)`. Use the blank
  layout (`prs.slide_layouts[6]`) and draw your own boxes; the default placeholders look dated.
- Fix a grid once at the top of the script and reuse it on every slide: margins 0.7 in, title
  box at the top (about 1.3 in high), content area below, a footer line 0.4 in from the
  bottom with the source note on the left and the slide number on the right.
- Write small functions and use them everywhere: `title_slide`, `section_slide`,
  `bullets_slide`, `chart_slide`, `table_slide`, `number_slide`, `sources_slide`, each taking
  `notes`. Consistency is most of what "professional" means.
- Type: titles 28-32 pt bold, body 18-20 pt (never under 16), footer 10-11 pt in a muted
  colour. Left-aligned. Line spacing 1.1-1.2, space between bullets.
- Colour: one accent, one dark text colour, one muted grey, white or near-white background;
  a dark variant for the title and section slides is fine. No gradients, shadows, clip-art or
  stock decoration. Leave white space.
- In every text box: `tf.word_wrap = True`, `tf.auto_size = MSO_AUTO_SIZE.NONE`, set margins,
  and set the font on every run (name, size, colour); python-pptx inherits nothing useful.

## Charts and tables
- Native charts, so the person can edit them: `slide.shapes.add_chart(XL_CHART_TYPE…,
  x, y, cx, cy, chart_data)`. Bars for comparisons (sorted), lines for time, a single stacked
  bar for parts of a whole. Follow the `charts` skill for the choice.
- Clean them: no chart title inside the chart (the slide title is the finding), data labels
  on, gridlines light or off, legend only for two or more series and placed at the bottom,
  the accent colour for the series that matters and grey for the rest, number format set
  (`'#,##0'`, `'0%'`), font sizes 12-14 pt.
- Tables: at most 6 rows by 5 columns, header row filled, numbers right-aligned, 12-14 pt.
  A bigger table belongs in the workbook; show the three rows that matter.
- A picture only when it is real (a chart from the report, a screenshot from `look_at_page`).

## Notes and sources
- Speaker notes on every content slide: two to four sentences to say, then the source ids.
- Footer source note on any slide with a figure ("Source: s4, s12"). Last slide: the sources
  with titles and dates.

## Check
Render with `render_document` and open every page image. Look for: text running out of its
box or off the slide, titles on three lines, a chart whose labels overlap or are too small to
read, boxes that are not aligned with the grid, a slide that is mostly empty, different font
sizes for the same role. Fix in the script and render again.
