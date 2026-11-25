# Scripts and looks in a skill

Two parts of making a skill that only some runs need. Both are rules of `make-a-skill`.

## Scripts

Write a script only for a step that must come out the same every time and that a program does
better than a description: a calculation over many rows, turning one file format into another, a
check with a pass or fail answer, pulling fixed fields out of a file. Never for something the
agent's own tools already do (reading a page, querying a database, making a chart).

1. Write it in this project first, as a file (`skill/scripts/<name>.py`). Prefer Python with only
   its standard library, or a short shell script; say at the top in a comment what it does, what
   it takes and what it prints.
2. It takes its inputs as arguments and writes where it is told to: no path of this project, no
   name, date or figure of today's work inside it. It holds no key or password and reads none from
   a file; it uses the network only when the job is to fetch something, and the body then says
   from where.
3. It prints what it did, and ends with a non-zero status and a plain message when it cannot do
   the job, so the agent running it later knows.
4. **Run it here on a real example before saving**, and on one input that should fail. A script
   nobody has run is not saved.
5. Say in the body, at the step that uses it, exactly how to run it and what comes back: "Run
   `python <this skill's folder>/scripts/totals.py <the csv> <the output json>`; it prints the row
   count and writes the totals." The agent that loads the skill is told the skill's folder; never
   write a path of this computer.
6. Keep it short enough to read: the person is shown every line before it is saved, and all scripts
   of one save may be 40,000 characters together at most.

The same holds for a script you change or add in a skill the person already has ("Changing a
skill"): run it here first, on a real example and on one input that should fail, and say what you
ran and what it printed.

## Keeping the look

A skill made from work in this project keeps its look, unless the person says the look does not
matter.

1. Add `## How it looks` to the body, in words another agent can follow without seeing the page:
   the layout from top to bottom at desktop width (what sits beside what, and how it folds on a
   narrow screen), the palette with its colour values and what each colour is for, the typefaces
   and the sizes of the title, headings and body, how cards, tables and charts are drawn, and the
   two or three details that give the page its character.
2. Save the page itself as a reference file (`files: [{"path": "index.html", "as":
   "example.html"}]`, with its stylesheet when that is a separate file) and say in the body:
   "`reference/example.html` is the layout and look to follow. Copy its structure and styles; never
   its content."
3. The example still holds this project's content. Say so in your answer after saving, and offer
   to save a copy with the content taken out if they will share the skill.

For one part of a page, describe and keep that part only.
