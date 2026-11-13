---
name: sourced-report
description: Write a report whose every figure and factual claim can be traced to a saved source, with a checked-numbers appendix. Use for any research report, briefing, market or company analysis, literature summary or fact-based page.
---

# Sourced report

The reader must be able to trust every number without trusting you. That is done with
three things the studio gives you: saved sources, marks on the page, and the page check.

## 1. Read through `fetch_source`
- Read anything you may cite with `mcp__studio__fetch_source` (pages, PDFs, filings). It saves
  the text in the project as `s1`, `s2`, … and lists it in `sources/index.json`.
- Find the exact words with `read_source` (`find: "revenue"`). Quote what the source says,
  not what a search snippet said. A snippet is a lead, never a source.
- Prefer primary sources: the company's filing or press release over an article about it,
  the statistics office over a blog. Note the period and unit of every figure.
- A file the person uploaded is registered with `add_source`.

## 2. Mark every figure where it appears
```html
Revenue was <span data-src="s3" data-quote="total net revenues of $1,330.4 million">$1.33 billion</span> in 2024.
```
- `data-src` is the source id; `data-quote` is a short exact passage (8–40 words is typical)
  that contains the figure. The number shown must be in the quote, as stated or rounded.
- Computed from a project data file: `data-src="data/sales.csv" data-sql="select sum(amount) from 'data/sales.csv'"`.
- Your own arithmetic on sourced figures (growth, share, ratio):
  `data-src="s3 s4" data-derived="(1330.4 − 2290.8) ÷ 2290.8 from s3 and s4"`.
- Important non-numeric claims (a launch date, a quoted statement, "largest in the US") get
  the same quote mark.
- Give marks a quiet style (a dotted underline) and a `title` with the source title, and link
  a numbered reference next to the sentence: `<a href="#src-s3">[3]</a>`.
- Every chart has its data in a table under it (a `<details>` block is fine) with each value
  marked. A chart drawn from numbers nobody can inspect does not count as sourced.

## 3. Run the check, fix, repeat
- Run `mcp__studio__check_page` before you finish. Fix every `failed` item: correct the
  figure, quote the right passage, or remove the claim. Never loosen a quote to make it pass.
- Look at `unmarked`: numbers in the text with no mark. Mark them, or wrap things that are
  truly not facts (an example, a section count) in `<span data-nocheck>`.
- A figure you could not source is removed or stated as unknown. Say so in a
  "What could not be confirmed" paragraph. Do not fill gaps with estimates of your own.

## 4. The checked-numbers appendix
End the page with this section; it shows the result of the last real check instead of your
own description of it:
```html
<section id="checked-numbers">
  <h2>Checked numbers</h2>
  <p class="note">Each figure was compared with the saved text of its source. A match means
  the words are in the source as saved on the date shown; it does not prove the source is right.</p>
  <div id="checked-list">Loading the check…</div>
</section>
<script>
fetch('checks/index.html.json').then(r => r.json()).then(c => {
  const s = c.summary, esc = t => String(t ?? '').replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
  document.getElementById('checked-list').innerHTML =
    `<p><strong>${s.verified}</strong> verified · <strong>${s.derived}</strong> derived · <strong>${s.failed}</strong> failed · checked ${new Date(c.checked_at).toLocaleString()}</p>` +
    '<details><summary>Show every checked figure</summary><table><thead><tr><th>#</th><th>Figure</th><th>Status</th><th>Source</th><th>Evidence</th></tr></thead><tbody>' +
    c.items.map(i => `<tr class="st-${i.status}"><td>${i.n}</td><td>${esc(i.text)}</td><td>${i.status}</td><td>${i.sources.map(esc).join(', ')}</td><td>${esc(i.status === 'derived' ? i.reason : i.quote)}${i.status === 'failed' ? '<br><em>' + esc(i.reason) + '</em>' : ''}</td></tr>`).join('') +
    '</tbody></table></details>';
}).catch(() => { document.getElementById('checked-list').textContent = 'The check has not been run for this page.'; });
</script>
```
Before the appendix, a "Sources" list: for each cited source its number, title, publisher,
its address as a link the reader can click (`url` in `sources/index.json`) and the date it was saved (`fetched_at` in `sources/index.json`), with `id="src-s3"` anchors.

## Shape of a good report
1. Title, date, and a three-to-five sentence answer to the question the reader came with.
2. Key figures (four to six), each marked.
3. Sections that each answer one question; lead with the finding, then the evidence.
4. Comparisons in tables with the same period, unit and definition per column; say so when
   they differ instead of presenting them as comparable.
5. Risks, open questions and what could not be confirmed.
6. Sources, then checked numbers.

Write plainly. Separate what a source states from what you conclude from it ("This suggests…").
