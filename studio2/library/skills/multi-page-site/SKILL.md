---
name: multi-page-site
description: Lay out several linked pages (a small website, docs, chapters) with one stylesheet, the same menu on every page and a whole-site check. Use for a site, several pages, docs or chapters, or adding a page to one.
---

# Multi-page site

This makes a set of pages that reads as one site: the same look, the same menu, links that work
wherever the folder is put. It is for any request with more than one page.

## What you need first

- The pages and what each is for. When the person gives only a subject ("a site for the garden
  club"), choose the pages yourself (four to six), say the list in one line, and carry on.
- Whether the site has facts or figures. If it does, read `sourced-report` as well: its marks and
  source list apply to every page that states them.
- The project notes (`notes/project.md`), if the project has them: an existing site's layout, menu
  order and naming are written there. Follow them.

## One page or several

A rule of thumb, not a law:
- **One long page** when a reader goes through it once from top to bottom (a report under about
  eight sections, a single event, a landing page). Give it a jump menu (`studio-page-basics`).
- **Several pages** when readers come for one part and leave (contact, a schedule, a catalogue), when
  parts are updated at different times, or when one page would be more than about ten screens.
- A report in chapters of many pages, or one built from many sources: read `large-documents` too.

## Steps

1. **Plan with `mcp__studio__set_plan`**: one item per page, plus the stylesheet and the checks.
   Mark each as it starts and ends. The plan is the progress; the project notes hold the conventions.
2. **Lay out the files.**
   - `index.html` is the start page. Other pages sit beside it (`events.html`), or in a folder with
     its own `index.html` when a part has several pages (`plants/index.html`, `plants/roses.html`).
   - Names are short, lower case, with `-` between words. They become the addresses: never rename a
     page later without changing every link to it.
   - One shared stylesheet, `styles/site.css`, holds the colours, type and spacing every page uses:
     the `theme` skill's tokens (`--bg`, `--fg`, `--accent`, `--gap` and the rest) and their dark
     values, the layout of the header, menu, main column and footer. A page's own `<style>` holds
     only what that page alone needs.
   - At most one shared script, `scripts/site.js`, and only if pages need the same behaviour.
   - Pictures in `media/`, data in `data/`.
3. **Write the start page first, completely**, with the shared stylesheet and the menu. It is the
   model every other page is copied from. Get it on screen early.
4. **The menu.** The same `<nav>` markup on every page, the links in the same order, the current
   page marked:
   ```html
   <nav class="site-nav" aria-label="Site">
     <a href="index.html">Home</a>
     <a href="events.html" aria-current="page">Events</a>
     <a href="plants/index.html">Plants</a>
   </nav>
   ```
   Style `[aria-current="page"]` in the stylesheet so a reader sees where they are. Every page links
   back to the start page (the menu does it; the site's name in the header can too). A jump menu
   for one long page is a second `<nav aria-label="On this page">`, not part of the site menu.
5. **Relative links only**: `events.html`, `../index.html`, `media/rose.jpg`. Never a leading `/`,
   never the project's address or `localhost`: the site must work at the project's address, as an
   exported folder opened from a disk, and hosted in a subfolder. A page in a folder writes `../`
   before every shared file (`../styles/site.css`) and every link out of its folder.
6. **Every page complete by itself.** Each has: `<title>` naming the page and the site ("Events ·
   Garden club", never the same title twice), `<meta name="viewport" content="width=device-width,
   initial-scale=1">`, the shared stylesheet, the menu, an `<h1>`, a one-paragraph summary of the
   page at the top, light and dark (`prefers-color-scheme`, through the tokens), and a layout that
   works at 1440, 820 and 390 px. A reader who lands on any page from a search must not need another.
7. **The other pages.** Copy the `<head>` and the menu from the start page, change the title and the
   `aria-current`, then write the content. Never retype the menu from memory: that is how it drifts.
8. **Keep the conventions in the project notes.** Write them in `notes/project.md`, short: the file
   layout, the menu order, the naming rule, the stylesheet's path, what the shared script does. A
   later request that adds a page reads them first and copies the head and menu from an existing
   page, then adds the new page to the menu of every page.
9. **Check the whole site** (below), fix, and check again.

## What the result contains

- `index.html` and the other pages, each complete by itself.
- `styles/site.css` with the tokens and the shared layout; `scripts/site.js` only if needed.
- The same menu on every page, with the current page marked.
- `notes/project.md` with the site's conventions.
- On a page that states facts: the marks and the source list `sourced-report` describes.

## Check before finishing

1. Run `mcp__studio__check_site` (start page `index.html`). Fix every finding: a broken link or
   anchor, a missing or empty file, an orphan (add it to the menu or link it), a dead end, a missing
   or repeated title, no viewport, a menu that differs, a page without the shared stylesheet. Run it
   again until it reports no findings. Read its `covers`: links a script builds are not seen.
2. Run `mcp__studio__check_page` on each page that states facts or figures, and fix what fails.
3. Run `mcp__studio__look_at_page` on the start page and on at least one inner page (one in a folder
   if there is one), at 1440 and at 390, and once with `dark: true`. Open the tiles; check that the
   menu looks the same and shows the current page.

## Exporting a site

The person exports from Export in the page's menu. Tell them truthfully what each way does:
- **One HTML file** holds the start page (`index.html`) only, with its own styles and pictures
  inside. The other pages are not in it, and its notes say how many it links to. Use it only for a
  one-page project.
- **A folder to host or run** (`.zip`) keeps every page, the stylesheet, pictures and data, leaves out
  working folders (`research/`, `notes/`, `attachments/` and the like) and adds a short read-me. It is
  the way to share or host a site: any static host takes the folder as it is.
- **Everything** is the working folder as it is, notes and all: for keeping, not for sharing.

## Never

- Never use a leading `/` or a full address for a link to a page of the project.
- Never give each page its own copy of the colours or the menu's styles: they drift.
- Never link to a file in `research/`, `notes/` or `attachments/` from a page: an export leaves them out.
- Never leave a page out of the menu by accident; a page meant to be hidden still links back home.
