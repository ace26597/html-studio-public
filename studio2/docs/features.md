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
- **Animation, 3D scenes, pictures and video.** A video is built as a page and rendered frame
  by frame to MP4, GIF or WebM. Needs `ffmpeg` and the assistant's browser.
- **Generated media.** Photographs, clips and sound from Higgsfield, through its command-line
  tool signed in on your machine. Every request shows its price and waits for your Allow.

You can also save a page, an app or an Office file as a template and start new work from it.

Two buttons work on a page that is already there, under the last answer and in the menu beside
the model's name. Neither adds content.

- **Verify content.** A separate checker compares every marked figure with its saved source,
  recomputes totals and reports on a card what matched, what it fixed and what it could not check.
- **Polish the look.** The agent looks at the page at desktop width first, then tablet and phone, reads it in pieces
  large enough to judge text and spacing, and gets a list of what the studio measured in the
  browser: text cut off by its box, a page that scrolls sideways, overlapping text, text too
  small or too faint, missing padding, controls too small to tap. It fixes what is clearly a
  defect, in the page's own style, and suggests matters of taste instead of changing them.

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

Add your own skills, connectors and databases on the **Library** page.
