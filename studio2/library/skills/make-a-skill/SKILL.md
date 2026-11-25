---
name: make-a-skill
description: Turn a person's description of a job, or work already done in this project, into a skill saved in their library, so the same kind of result can be made again. Use when they ask to make, create, save or update a skill, to "remember how to do this", to "do it this way every time", or to turn a report, dashboard, app or part of one into something reusable.
---

# Make a skill

A skill is a page of instructions another agent will follow later, in another project, without
this conversation. The person does not need to know how one is written: they describe the job or
point at work they like, and you write it. A skill made here is instructions, reference files
and, only where a step needs one, a script (see "When a step needs a script").

## 1. Find out what the job is

Starting from **a description**: read it for the steps in order and for what is missing. Starting
from **work in this project**: read what was made and how (the files, and the earlier requests),
then separate what belongs to the job from what belongs to this one subject.

You need to know these six things. Take what the person already said; ask only for what is
missing, all in one short message, in plain words, with your own guess for each so they can
answer "yes" or correct it:

1. **When it is used**: the kind of request that should start it, in the words they would use.
2. **What it starts from**: a topic, a file they attach, a database, a web source, a date range.
   What changes each time (these become the inputs) and what never does.
3. **The steps, in order**, and where each piece of data comes from.
4. **What a good result looks like**: its parts, their order, what it must always contain, and
   any example to follow.
5. **How the result is checked** before it is handed over.
6. **What must never happen** (a source not to use, a figure never to estimate, a tone to avoid).

Do not ask about anything you can decide well yourself, and never ask more than once. If they
say "you decide", decide and say what you chose.

## 2. Write it

- **Name**: two to four words joined with `-`, saying the job (`weekly-sales-review`), not the
  subject of today's work.
- **Description**: one or two sentences, 600 characters at most. It is the only thing an agent
  sees when choosing skills, so say what the skill makes and name the requests that should start
  it ("Use when the person asks for …, mentions …"). Vague descriptions are never picked.
- **Body**, in this order, leaving out a part only when it has nothing to say:
  1. `# <Title>` and two sentences: what this makes and for whom.
  2. `## What you need first`: the inputs, and what to ask the person when one is missing.
  3. `## Steps`: numbered, one action each, in the order to do them. Say where data comes from
     and how much ("the last 30 days", "every row, not a sample"). Name the studio's tools where
     one is needed (`fetch_source`, `query_database`, `check_page`, `look_at_page`,
     `render_document`) and the other skills to read (`sourced-report`, `dashboard`, `xlsx`).
  4. `## What the result contains`: the parts in order, and what each must hold.
  5. `## Check before finishing`: the tests the result must pass, each one something that can be
     done and seen to pass or fail.
  6. `## Never`: the short list of things not to do.
- Write to the agent, as instructions: "Get …", "Compare …". Give the reason for a rule when it
  is not obvious; a rule with its reason is followed better than a rule in capitals.
- **Leave out everything true only of this project**: its subject, its figures, its dates, its
  file names, the mistakes made on the way. Where today's work used "Enphase", the skill says
  "the company the person names".
- Keep it under about 150 lines. Detail that only some runs need goes in a reference file.
- **Reference files** are for things to follow or read: an example page whose layout to copy, a
  document template, a list of approved sources, a picture of the look they want. Say in the body
  what each one is for (`reference/example.html`: the layout to follow, never its content).
  Take out of an example anything private or specific before saving it, or tell the person it is
  in there.
- **When a step needs a script.** Most skills need none: an agent follows written steps well.
  Write one only for a step that must come out the same every time and that a program does better
  (a calculation over many rows, a format conversion, a pass-or-fail check), never for what the
  agent's own tools already do. First read `reference/scripts-and-looks.md` ("Scripts"): the
  rules a script follows. A script nobody has run here is never saved.
- **How it looks is the person's choice.** Write a look into the skill only when they asked for
  one or pointed at an example. But a skill made from work in this project keeps its look unless
  they say it does not matter ("this again" includes the look): describe it in `## How it looks`
  and save the page as `reference/example.html`, as `reference/scripts-and-looks.md` ("Keeping
  the look") says. For one part of a page, keep that part only.

## 3. Show it, then save it

- In a project started to make a skill, keep the draft as `skill/SKILL.md` (the instructions
  only: the name and the description go to `save_skill` as their own fields, and it refuses a
  body that starts with `---`) and make
  `index.html` a plain, readable view of it for the person: what it is for, when it is used, the
  steps, what it checks, in their words, not as code. `skill/SKILL.md` is the source: when the
  person edits the page or asks for a change, change the draft and then the page.
- In a project that has its own page, do not touch that page: put the draft in your answer.
- Read the draft once as the agent who will get it cold. Wherever you would have to guess, fix
  the wording or ask.
- Save with `save_skill` (name, description, body, and `files` for reference files and scripts,
  which must be in this project: a reference file goes to `reference/`, a script to `scripts/`).
  The person sees the whole skill on a card, every script line by line, and allows or denies it.
  Before you call it, tell them in a sentence what each script does, so the card is not the first
  they hear of it. If they deny, ask what to change; do not send the same text again.
- Changing a skill they already have: read it first (`search_library`, then `activate_skill`),
  change only what they asked, and save with `replace: true`. The earlier version is kept, and
  so are the skill's files: `files` adds one or replaces one at the same path, and `remove` takes
  one out.
- After saving, say in two or three sentences what the skill does and how to use it ("in any
  project, ask for a weekly sales review"), that it is in their Library, where it can be opened,
  edited, switched off or exported as a file to share, and offer one next step: trying it on a
  small real example.

## Explaining a skill

When the person asks what a skill of their library does, they are given its folder. They may
never have opened a skill. Read `SKILL.md` and every other file in the folder before you write a
word; run nothing in it. Then make `index.html` a plain page that answers, in this order:

1. **What it is for**, in one or two sentences, and **what kind of request starts it** (the
   description's own words, then yours).
2. **What it has the agent do, step by step**, as a numbered list in the order the instructions
   give. Keep each step to what the skill says; where it leaves a choice to the agent, say so.
3. **Its files**, one line each: what the file is and when the instructions say to read it. A
   file the instructions never mention is said to be unused.
4. **Its scripts**, each on its own: what it does, what it needs (a program, a key, the network),
   what it reads and what it changes on the machine. Read the code to say this; do not guess
   from the name. A skill without scripts says "no scripts: it is instructions only".
5. **What it needs to work**: tools, programs, accounts, files it expects to find.
6. **What it does not say** that a person relying on it would want to know: a missing check, a
   step that can fail without anyone being told, an input it assumes.
7. **What could be better**, at most five points, the most useful first, each one small enough
   to ask for.

Quote the line of the skill a claim rests on, in a short `<blockquote>` or in backticks with the
file's name, so the person can see it is the skill speaking and not you. If they asked something
in particular, answer that first. Save nothing: this is reading, not making. End your answer by
offering the next step that fits: "make your own copy to change it" for a skill that is not
theirs, or "say what to change" for one that is.

## Changing a skill

When the person asks for a change to a skill of their own, they are given its folder. Read the
whole skill first, every file. Change only what they asked for, and keep its wording and order
elsewhere. Show what will change before saving: the lines as they are and as they will be. Then
save with `save_skill`, `replace: true`, under the same name: the earlier version is kept, and
so are the skill's files unless you name them.

- **The instructions** are the `body`: send the whole new text, not only the changed lines.
- **One file of the skill** (a reference file or a script): you cannot write in the skill's
  folder. Copy the file into this project (`skill/<its path>`), change the copy, and give it in
  `files` with `to` set to its path in the skill (`{"path": "skill/scripts/totals.py", "to":
  "scripts/totals.py"}`), so it replaces the one that is there. A new file goes in the same way.
- **A script you changed or wrote is run here first**, on a real example and on one input that
  should fail, as when making a skill. Say what you ran and what it printed.
- **Taking a file out**: `remove: ["reference/old-example.html"]`. Take out only what was asked
  for, or a file the change leaves unused, and say which. If the instructions mention the file,
  change them in the same save.
- The card shows the person each script whole, what is removed and what stays. A file that is
  neither a reference type nor a script (a font, a schema, a program without an ending) cannot
  be written this way: say so, and that it stays as it is.

A skill that is not the person's own cannot be changed: tell them to make their own copy in the
Library first.
