# HTML: a long report or a site of many pages

The parts of the method that are only for pages. Read the `sourced-report` skill (the marks, the
source list, the checked-numbers appendix) and, for a site, `multi-page-site` (the layout, the menu,
relative links): their rules hold here.

## Report or site

- **A long report** is one page made of sections: `index.html`, joined from the shell
  `build/shell.html` and the sections `build/sections/<nn>-<id>.html`, with a jump menu at the top.
- **A site** (a report in chapters, documentation) is several pages: each page is the one shell
  `build/page-shell.html` filled with its own module `build/pages/<id>.html`, with the same menu.
Choose with the rule of thumb in `multi-page-site`, and say the choice in the outline.

## The outline and the look

`build/outline.json` as in `plan-files.md`, with `"kind": "report"` or `"kind": "site"`:

```json
{"kind": "report", "page": "index.html", "shell": "build/shell.html",
 "sections": [{"id": "market", "title": "The market", "module": "build/sections/02-market.html",
               "message": "Demand doubled in five years", "facts": ["units_2025", "units_2020"]}]}

{"kind": "site", "shell": "build/page-shell.html",
 "pages": [{"id": "home", "path": "index.html", "title": "Garden survey", "nav": "Home",
            "module": "build/pages/home.html", "message": "…", "facts": []},
           {"id": "costs", "path": "chapters/costs.html", "title": "Costs · Garden survey", "nav": "Costs",
            "module": "build/pages/costs.html", "message": "…", "facts": ["cost_total"]}]}
```

The look is the stylesheet: `styles/site.css` with the `theme` skill's tokens, written once by you
before any helper starts. It is the only place that sets a colour, a font or a size; a section uses
the classes it defines and never a `style` attribute. There is no `look.json` for pages.

## The ledger on pages

`data/facts.json` has the shape in `plan-files.md`, with two additions for pages:
- `display`: the figure as the page shows it (`"$4.2 million"`). It must be in the fact's `quote`,
  as stated or as a rounding, or `check_page` fails it.
- `sql`, for a fact `obtained` from `data`: one select that returns that one value
  (`select sum(units) from 'data/sales.csv'`), which `check_page` runs again.

A piece never types a figure: it writes `{{fact:<key>}}`. The joiner below turns each into the
figure with its mark (`data-src` and `data-quote`, `data-sql`, or `data-derived` with the sources
of the facts it is computed from), the same mark on every page that shows it. Source ids (`s1`, `s2`)
are those of `sources/index.json` and stay the same on every page. A claim that is not a figure
(a date, a quoted sentence) is a ledger fact too, so it is marked the same way.

## The frame, written once

1. Write `styles/site.css`.
2. Write the shell, a whole page: `<title>`, the viewport, `<link rel="stylesheet"
   href="{{root}}styles/site.css">`, the header with the site's name, and these marks the joiner fills:
   - report (`build/shell.html`): `<!-- join:toc -->` under the summary, `<!-- join:sections -->`,
     then `<h2>Sources</h2><!-- join:sources -->` and the checked-numbers appendix;
   - site (`build/page-shell.html`): `<title><!-- join:title --></title>`, `<!-- join:nav -->`
     where the menu goes, `<main><!-- join:content --></main>`, and `<!-- join:sources -->` with the
     appendix at the end.
   `{{root}}` becomes `""` or `../` for a page in a folder; `{{check}}` is the address of the page's
   saved check, so the appendix of `sourced-report` fetches `'{{check}}'` on every page.
3. Copy `join-pages.py` from this skill's `reference` folder to `tools/join_pages.py`. Write the first
   section or page yourself, join it and check it (below) before handing out the rest.
A file that still holds `<!-- join:` or `{{fact:` is a shell or a piece: `check_site` does not
count it as a page.

## What a section helper is given

Its piece of the outline, the module path to write, the classes `styles/site.css` defines, the
shell and the first module you wrote as the model. A report section is one `<section id="<id>">`
with an `<h2>`; a page module is the inside of `<main>`: an `<h1>`, a one-paragraph summary, then the
content. Neither has a `<head>`, a menu or a source list: the shell has those. Figures only as
`{{fact:<key>}}`; links to the project's files start with `{{root}}`; a link to another page is
relative to the page's own `path` in the outline.

## Join, then check, in this order

1. **Join with one command**: `uv run python tools/join_pages.py .` It replaces every mark, builds the menu
   and each page's source list, and writes the pages. It stops, writing nothing, when a key is not in
   the ledger or is in it twice, a module is missing, or a section lacks its id: fix that and join again. `--only <id>`
   joins one piece into a temporary folder.
2. **`check_site`** on the project: links, anchors, missing files, orphans, menus, titles. Fix the
   modules or the shell, join again, until it reports no findings.
3. **`check_page`** on every page that states facts: fix every `failed` item in the ledger or the
   module (never on the built page, which the next join overwrites), and every `not_listed` source.
4. **`look_at_page`** on the start page and at least one inner page (one per kind of page), at 1440
   and 390, light and dark.

## The review

Start a `checker` helper with the pages: it reruns `check_page` on each, judges what the automatic
check cannot (the right period, unit, company), redoes the arithmetic of derived figures and writes
`research/review.md`. Then you look at the pages it names. Fix in the modules or the ledger, join,
and run the checks again on the changed pages.

## What to tell the person

> 9 pages, 0 site findings. Figures: 86 marked, 84 verified in their sources, 2 derived and
> recomputed. 0 unmarked numbers. Looked at the start page and 3 inner pages at desktop and phone width.

## Changing one part later

Change the ledger (a figure, on every page that shows it) or the one module, never the built pages.
Join, run `check_site` and `check_page` on the changed pages, and look at them. A change to the
stylesheet or the shell changes every page: say so and look at several.
