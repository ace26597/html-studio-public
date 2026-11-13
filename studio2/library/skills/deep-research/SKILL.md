---
name: deep-research
description: Research a broad question properly with parallel sub-agents - plan, research in parallel, synthesise, then independently check. Use when a question needs more than about six sources, covers several companies, products or sub-topics, or the person asks for a detailed, thorough or deep report, for as much as possible, or for everything about a subject.
---

# Deep research

Four stages. Keep the person informed: put a skeleton page up first and fill it as findings arrive.

## 1. Plan (you, a few minutes)
- Restate the question, the decision behind it, and the scope (period, geography, what is out).
- Do two or three quick searches to learn the vocabulary and who the primary sources are.
- Split the work into 3–6 independent threads that each answer a distinct question, for
  example: financials from filings; products and pricing; each main competitor; market size
  and regulation; customer and installer sentiment.
- Write the plan to `research/plan.md` and save `index.html` with the title, the planned
  sections and "researching…" placeholders.

## 2. Research in parallel (sub-agents)
Start one `researcher` sub-agent per thread, all in a single message so they run together.
Give each one a complete brief, because it knows nothing of this conversation:
- the exact question and scope for its thread, and what is explicitly not its job;
- the primary sources to try first, if you know them;
- where to write: `research/notes/<thread>.md`;
- the rule: every finding has a source id from `fetch_source` and an exact quote.

Do not research the same threads yourself while they run. When they return, read their
notes files (not only their summaries). A helper's final message comes back to you as the
result of starting it; helpers cannot be messaged and do not write to the person.

## 3. Synthesise (you)
- Read every notes file. Build the page with the `sourced-report` skill: marks on every
  figure, using the source ids and quotes from the notes. Work from the notes: do not read a
  whole source again that a researcher already read; use `read_source` with `find` only to
  confirm the words you are about to quote.
- Reconcile conflicts openly: when two sources disagree, show both, say which you rely on and why.
- Fill gaps with one more targeted `researcher` if a section is thin; otherwise say what is unknown.
- Charts come from the figures you gathered; each has its data table.

## 4. Check (independent)
- Run `mcp__studio__check_page` and fix every failure.
- Start one `checker` sub-agent with the page path. It re-reads the claims against the saved
  sources with fresh eyes and writes `research/review.md`. Fix what it finds, run
  `check_page` again, and look at the page with `look_at_page`.
- Finish only when the check has zero failures, or the remaining ones are listed on the page.

## Budget
Typical run: 4 researchers, 20–40 saved sources, one checker. When the person asked for as
much as possible, use five or six threads and send one more round for any section that came
back thin. Do not start more than six sub-agents at once. If the person asked a narrow
question, skip this skill and just answer it.

Say how deep it went, on the page (a short "How this was researched" note) and in your
reply: the number of sources read, the threads covered, and what was not covered or could
not be reached. A search tool that refused, or pages that would not load, are part of that.
