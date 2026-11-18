---
name: use-template
description: Follow a template the person saved - make new work that looks like it and is built the same way, with new content. Use whenever the project has a `template/` folder, or the person says "like the template", "same style as", "use my template", or asks to save something as a template.
---

# Using a saved template

A template is in `template/`: a copy of earlier work plus `template/TEMPLATE.md`. It is a
pattern for how things look and how they are built. It is never a source of content.

## First
1. Read `template/TEMPLATE.md` whole. Then read the template's main file and its styles (and
   its build script for a deck, workbook or document). Look at the pictures in any
   `.preview` folder: they show the finished result you are matching.
2. Write down, for yourself, what makes it look the way it does: colours and theme variables,
   fonts and sizes, spacing and width, the order of sections or slides or sheets, the
   components it uses (cards, callouts, tables, chart styling), how dense it is, its tone.

## Then build with new content
- **Page:** start the project's `index.html` from the template's structure and styles. Keep
  the theme variables, layout and components; write every heading, sentence, figure and
  chart from the person's request and this project's own sources.
- **Deck, workbook, document:** copy the build script from `template/tools/` into this
  project's `tools/` and change it there. Keep the grid, colours, type, slide or sheet
  functions, number formats and checks. Replace the outline, the text and the data. Keep the
  library the script uses; if it has a `package.json`, copy that too and run
  `npm install --prefix tools`. Then the usual loop from `office-files`: build,
  `render_document`, look, fix.
- **App:** the project already is a copy of the template's files. Change it to what the
  person asks and keep its conventions.
- If the request does not fit the template (a dashboard asked for from a report template),
  keep what carries over (colours, type, spacing, components) and say what you adapted.

## Never carry content over
The facts, figures, names, dates, quotes and sources in `template/` belong to other work.
None of it may appear in what you make unless the person gives it to you again or it comes
from this project's own sources. Check before you finish: search your output for names and
numbers that appear in the template and remove any that came from there. Do not edit files
under `template/`.

## Saving a template
When the person asks to save their work as a template, call `save_template`:
- `name`: short, what it is for ("Board deck, dark title slides").
- `file`: for a deck, workbook or document, its path under `files/`. Leave it out to save the
  whole page or app.
- `notes`: how it was made, so the next person or agent can repeat it: the palette with
  values, fonts and sizes, the grid, the section or slide order and why, chart choices, and
  for a build script what each function does and which parts are content to replace.
Tidy first if it helps (remove dead files), but do not strip the content: the example is
what shows how it looks.
