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
- **Answers and pages about code you already have.** Beside "Attach files" in the chat, **Add
  context** takes the full path of a folder or a file on the studio's computer, or the address
  where an app is running (`http://localhost:3000`). Nothing is copied or uploaded, so the size of
  the code does not matter: the agent is told on every request that it is there and searches it
  where it is. It reads and changes nothing there. More in [apps.md](apps.md).
- **Instructions for one project.** What you always want here, written once in plain words:
  "always use Indian number format", "every report ends with the sources". See
  [Instructions for a project](#instructions-for-a-project) below.
- **How much to involve you.** A slider from *On its own* to *Step by step*: the agent asks first,
  agrees its plan with you, or checks in after each part with its sources, instead of deciding
  quietly. See [Involve me](#involve-me) below.
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
- **A script where a step needs one.** Most skills are written steps. Where a step must come
  out the same every time (a calculation, a conversion, a pass-or-fail check) the agent can
  write a short script, run it in the project first, and save it with the skill. The card then
  shows every line of every script before you allow it, because the agent may later run that
  script in any project the skill is used in.

## Reading a skill, and starting from one

Every skill in the Library opens, whoever wrote it: the ones that come with the studio, the ones
found on your computer, the ones your plugins bring, and your own.

- **Open** shows when the skill is used, its full instructions, every file it holds with its
  contents, and which of those files are scripts the agent could run. Files are shown as plain
  text, a page included, so nothing hides in formatting. It also says how often the studio has
  picked the skill for a request and how often the agent then really used it.
- **Explain this skill** has the agent read the whole folder and write a page about it: what it
  is for, what it has the agent do step by step, what each file and script is for, what it needs
  to work, what it leaves unsaid, and what could be better. It runs nothing in the skill.
- **Make my own copy** turns any skill into one of yours under a new name, with all its files.
  A skill that holds scripts lists them and waits for a tick first, as an import does.
- **Ask for a change** to a skill of your own: say what should be different, and the agent
  rewrites it and shows the result on a card for you to allow. It can change the instructions,
  replace one file or script, add one, or take one out; the card shows each script whole, what
  is removed and what stays, and the version before is kept.
- **New page → A skill** is a second way to the same "describe it" flow.

## A connector from a description

A connector (an MCP server) lets the agent reach something outside the project: your notes, your
tickets, a database, a search engine. You no longer need to know its address.

- **Library → MCP servers → Add** opens on *Describe what you need*. Say it in a sentence
  ("read and search the pages in my Notion workspace") and a small model suggests up to three.
- **It answers from memory, so the studio checks what it can.** A suggestion is only ever a web
  address or a package from npm or PyPI. The package is looked up in its registry (version,
  date, weekly downloads) and the address is asked once with no key; what does not exist is
  left out and said. What your library already has is shown first.
- **Nothing is added until you press Add.** *Use this* fills in the form, with one empty row for
  each key it needs and where to get it. You type the keys and add it; Test works as before.
- A package being in a registry shows the name exists, not that it is safe. The form shows the
  figures so you can judge, and says so when few people use it or it is new.
- A connector that signs in through a browser cannot be added in the studio; you are shown the
  two commands that add it to the agent program instead, after which the library has it.
- **New page → A connector** leads to the same place.

## Instructions for a project

Some things you want every time in one project and never want to type again: a number format, a
closing section, a tone, who the reader is. Each project has a box for them.

- **Project menu → Instructions…**, or the note button beside *Add context* under the message
  box. Write it as you would say it; there is no format to learn. Up to 4,000 characters.
- **The agent is given the text with every request in that project**, and only there. Another
  project has its own or none. A change applies from the next request; a request that is
  running keeps the text it started with.
- **What you ask for in a message comes first.** If a request and an instruction disagree, the
  agent does what the request says and tells you which instruction it set aside. If it could
  not follow one, it says so at the end.
- **It is about how the work is done, not what the agent may do.** Approvals still ask and a
  context folder stays read only whatever the box says.
- The button is marked and the menu row says *On* while a project has instructions. **Clear**
  empties the box; nothing changes until you press Save.
- A template saved from the project and an export do not carry them.

Context tells the agent *where to look*; instructions tell it *how to work*. Something you want
in every project of a kind is better as a [skill of your own](#making-a-skill-of-your-own).

## Involve me

By default the agent decides what it was not told and gets on with it. **Involve me** sets how
much it brings you in instead. It is the last setting in the strip under the message box
(**Involve**, with its percentage); it opens a slider, and the choice is remembered per project.

| Stop | Name | What the agent does |
|---|---|---|
| 0% | On its own | Never asks. |
| 25% | Only if stuck | Asks only when it cannot sensibly go on without you. |
| 50% | Ask first | Asks before it starts when your answer would change the result. |
| 75% | Agree the plan | Asks first, then shows its plan and waits for your go-ahead before it builds. |
| 100% | Step by step | Plan first, then a check-in after each part: what it made, what it found, and the sources and files behind it. |

It is five stops and not a hundred on purpose: an agent does not behave differently at 37% than
at 42%, and you should be able to tell what a setting will do.

- **A stop is the most it may involve you, not a quota.** The agent is told to match the asking
  to the work. "Change the title to …" gets no card at any setting; a one-page piece gets two or
  three check-ins; a long report one per section.
- **Three kinds of card.** *Questions*: up to four, each with choices (the agent's own pick
  first), a box for your own words, or both; what you type counts for more than a choice. *The
  plan*: the sections or features it means to build and what it will rely on. *A check-in*: the
  part it just made, what it found, and its references. A source opens in a new tab; a project
  file opens in the Files panel. The page itself updates as the agent works, so you can look at
  the part while the card waits.
- **You can drop out at any card.** Leave a question empty and the agent decides it. The second
  button (*Skip, decide for me*, *Go ahead, no more questions*, *Finish it without me*) tells it
  to finish the request by itself. Either way its answer ends with what it assumed.
- **The request waits for you**, as it does on an approval card, and the page says so. A stop
  also caps the cards one request may show (1, 2, 4 and 12). A request that only verifies or
  polishes never asks.
- The agent reads your [instructions for the project](#instructions-for-a-project) and its
  context first, and is told not to ask what they already answer.

Your answers belong to that request. Something you find yourself answering every time belongs
in the project's instructions.

## The chat panel

On a desktop the chat floats at the bottom right of the page.

- **Make it the size you want.** Drag its left edge to widen it, its top edge to make it taller,
  or the corner for both. The button in its header jumps to a large size and back; double-click
  an edge for the usual size. The studio remembers the size in your browser.
- **The row inside the message box is for things you do:** attach files, add context, the
  project's instructions, dictate, and *Check this page now* (verify its content, or polish its
  look).
- **The strip under the message box is how the next request runs,** each setting named with
  what it is set to: **Model**, **Depth** (Quick, Standard, Deep), **Verify** (a switch for the
  content check after each request) and **Involve** (the slider above). Click one to change it.
- **It is glass.** The panel is translucent and the page shows through it, blurred. The top bar
  has the same finish. If your system is set to reduce transparency, both are solid.

Add your own skills, connectors and databases on the **Library** page.
