---
name: data-analysis
description: Explore and analyse CSV, Excel, Parquet or JSON files in the project with SQL - profile the data, answer questions, and report findings with the queries that produced them. Use when the person uploads data and asks what is in it, asks a question about it, or wants an analysis rather than a dashboard.
---

# Data analysis

Use `mcp__studio__query_files` (DuckDB). Paths are relative to the project folder.

## Profile first
`query_files` with `profile: "data/file.csv"` (instead of `sql`) gives the size, the row count and,
per column, its type, missing values, about how many distinct values, min and max, with five
sample rows, in one call. In SQL:
```sql
describe select * from 'data/file.csv';
summarize select * from 'data/file.csv';   -- min, max, nulls, distinct per column
```
- Excel: convert once with a short Python script (`uv run --with openpyxl --with pandas …`)
  to CSV or Parquet under `data/`, then query that.
- Several files: `select * from read_csv('data/*.csv', union_by_name = true, filename = true)`.
- Check for duplicates on the key, impossible values, mixed units, and gaps in dates before
  trusting any total. Report what you found in a "Data notes" section.

## Big files
Each call has 60 s (`timeout` up to 300) and 2 GB of memory; only files in the project can be read.
- Profile first. If the answer says `sampled`, say on the page that figures about the whole file
  come from the queries, not from the profile.
- Over about 100 MB (CSV or JSON): convert once to Parquet and query that from then on, often ten
  times faster: `sql: "select * from 'data/big.csv'"`, `save_as: "data/big.parquet"`.
- The page never loads the raw file. Save small aggregated tables for it with `save_as` (totals by
  month, by category, the top 100 rows): keep each under about 1 MB and all of them under about
  5 MB, e.g. `data/summary/by_month.json`.
- State on the page how many rows the figures come from ("from 35,400,000 orders"), and say so
  plainly when a figure comes from a sample or a capped extract.
- A limit reached is not a dead end: filter (`where`), select fewer columns, aggregate
  (`group by`) instead of returning rows, or use the Parquet copy.

## Answer with queries, not impressions
- Each finding is one query. Keep the queries in `analysis/queries.sql` with a comment per
  finding, so the person can rerun them.
- Totals and averages need the right denominator: say what was excluded (nulls, cancelled rows).
- Compare like with like: the same period length, the same population.
- A difference is worth reporting when it is large relative to the variation in the data;
  with small counts, show the counts.
- Do not claim a cause from a correlation. Say what the data shows and what it cannot show.

## Report
- Lead with the three to five findings that answer the person's question, each with its figure.
- Mark figures with `data-src` and `data-sql` (see `sourced-report`) and run `check_page`:
  the studio re-runs each query and compares it with the number on the page.
- Large results go to a file with `save_as`, and the page loads that file.
