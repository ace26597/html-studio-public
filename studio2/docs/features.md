# What you can make

Moved from the [front page](../../README.md). Bringing an app you already have is in [apps.md](apps.md).

You ask in the chat. On the page you can switch between four modes: **Browse** (use the page),
**Select** (point the agent at one part), **Edit** (change text directly) and **Draw** (circle,
box, note or drag things, then send the drawing with a message). Every request and every edit
is saved as a version you can restore, and a running request can be stopped.

- **Sourced reports.** The agent searches, saves every page or PDF it reads into the project,
  and marks figures so a check can compare them with the saved source.
- **Dashboards and data analysis.** From files you drop in (CSV, Parquet, JSON) or from your own
  PostgreSQL, Redshift or MySQL database, read-only.
- **Apps with their own back end.** A project can run its own server; the studio starts it,
  shows its log and serves it on the project's address.
- **PowerPoint, Word and Excel files.** Real files, rendered so the agent looks at every slide
  and page, with spreadsheet formulas calculated and checked. Needs LibreOffice.
- **Other formats.** A finished file can be handed over in another format: Word as RTF, ODT or
  PDF; a deck as PDF; a workbook as ODS or one sheet as CSV; a Markdown report as Word, ODT, RTF,
  EPUB or one HTML file. The answer says what was not carried over. Needs LibreOffice, and pandoc
  for the Markdown ones.
- **Diagrams.** Flow, sequence, state, entity, timeline and Gantt diagrams are drawn by the
  studio from a short description the agent writes, checked before they are shown (the drawing
  parses, contains every label that was asked for, and is readable on a desktop page), and saved
  as pictures in a light and a dark version. Nothing extra to install.
- **Animation, 3D scenes, pictures and video.** A video is built as a page and rendered frame
  by frame to MP4, GIF or WebM. Needs `ffmpeg` and the assistant's browser.
- **Generated media.** Photographs, clips and sound from Higgsfield, through its command-line
  tool signed in on your machine. Every request shows its price and waits for your Allow.

You can also save a page, an app or an Office file as a template and start new work from it.

Two buttons work on a page that is already there, under the last answer and in the menu beside
the model's name. Neither adds content.

- **Verify content.** A separate checker compares every marked figure with its saved source,
  recomputes totals and reports on a card what matched, what it fixed and what it could not check.
  It also reads the decks, Word documents, workbooks and PDFs the page links to or the request
  made, and names the slide, paragraph or cell of a figure that no source, data file or checked
  page figure vouches for.
- **Polish the look.** The agent looks at the page at desktop width first, then tablet and phone, reads it in pieces
  large enough to judge text and spacing, and gets a list of what the studio measured in the
  browser: text cut off by its box, a page that scrolls sideways, overlapping text, text too
  small or too faint, missing padding, controls too small to tap. It fixes what is clearly a
  defect, in the page's own style, and suggests matters of taste instead of changing them.

Files a request makes for you (a deck, a document, a spreadsheet, a picture, a CSV) are listed
under its answer in the chat, each with its kind and size and a button that opens it. In the
Files panel a Markdown file is shown formatted, a CSV as a table and an SVG as a picture, with
the source one click away.

**An app behind a sign-in.** When a project's app has a sign-in screen and you are signed in to
it in your browser, the agent's own browser can see the app as you do, so Polish the look and
the checks work on the pages behind the sign-in and not on the sign-in screen. The project's
menu has the switch, "Let the agent use my sign-in": on for apps made in the studio, off for an
app you attached, and not offered on a shared studio. The studio keeps your session in memory
only, never in a file or in what the agent is told, and only the cookies that app itself set.
While it is on, anything the agent clicks in the app it clicks as you.

## Your own skills and connectors

The studio finds what is already on the machine, read-only: your skills in `~/.claude/skills`,
the skills of your installed Claude Code plugins, and connectors (MCP servers) from
`~/.claude.json`, from your plugins and from an attached app. It takes only skill folders and
connector settings. A plugin's hooks, commands and permission rules are never read.

- **Picked per request, with a reason.** A word search narrows the library, then one call to
  a cheap model picks up to 25 skills and up to 8 connectors for the request. The activity
  feed shows what was picked and why. With 40 or fewer skills to choose from, all are loaded.
- **Always and Never.** Say either for a skill or a connector, for one project or the whole
  computer. A skill picked five times in a row without being used stops being picked.
- **The agent can switch more on.** During a request it can search the whole library, switch
  a skill on at once, and ask for a connector, which joins from your next message.
- A connector the studio picked by itself asks you before its first use. One that needs
  signing in is shown as such, with the command, and is not picked.

## Making a skill of your own

A skill is a page of instructions the agent follows for one kind of job. You do not need to
know how one is written.

- **Describe it.** Library → New skill: say what should happen in your own words ("every week
  get the last seven days of … from …, then …, then check it like this …") and add examples to
  follow if you have them. The agent asks for what is missing, shows the draft in plain words
  and writes the skill.
- **From work you like.** A project's menu → Make a skill from this…: the whole page or app, or
  the part you selected, with a note on what changes each time. The skill keeps the look as well
  as the steps: the layout, colours and type are written into it and the page is kept as an
  example to follow, so the next result on another subject looks like the one you liked.
- **You approve every skill.** Before one is saved you see all of it on a card and choose Allow
  or Deny, whatever the project's approval setting, because a saved skill is given to the agent
  in every project from then on. Replacing a skill keeps its last five versions.
- **Share it as a file.** Every skill in the Library has Export, and Import shows what a file
  holds before anything is added. A file that brings scripts says so and waits for a tick.
  Skills made through the chat hold instructions and reference files only, never scripts.

Add your own skills, connectors and databases on the **Library** page.
