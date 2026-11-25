---
name: dashboard
description: Build a dashboard from data files or an attached database - headline figures, filters, charts and a records table, with every figure traceable to a query. Use when the person gives a CSV, spreadsheet, Parquet file or database and asks for a dashboard, overview or explorer.
---

# Dashboard

## Understand the data before drawing anything
- Files: `mcp__studio__query_files` with `profile: "data/x.csv"` first (rows, and per column
  its type, missing values, distinct values, min and max), then small queries for the counts of
  likely categories. Databases: `database_schema`, then small aggregate queries.
- Decide what each column is: an identifier, a category, a date, a measure that can be
  summed, or a measure that cannot (a rate, a price, a percentage). Never sum identifiers,
  postal codes, coordinates or rates.
- State coverage on the page: what the data is, the period it spans, the number of rows, and
  when it was loaded. If it is a sample or a capped extract, say so at the top.

## Choose a small number of useful views
- Four to six headline figures that answer "how much, how many, how is it changing".
- One time series if there is a date. One or two breakdowns by the categories that matter.
  A distribution only when the spread is the point. A searchable, sortable records table.
- Skip a chart when a category has one value, or nearly as many values as rows.
- Filters (date range and one or two categories) apply to every figure, chart and the table
  at once. Show the active filters and a clear way to reset them.

## Build it
- Static data up to a few tens of thousands of rows: load the file in the page and compute
  in the browser. A big file (over about 100 MB, or millions of rows): profile it, convert it to
  Parquet and save small aggregated files for the page (`data-analysis`, "Big files"). Larger, or a database: write a small back end (`app-backend` skill) with
  endpoints that run the aggregate queries, and have the page call them.
- Layout: header with title and coverage; filter bar; headline row; charts in a 12-column
  grid, two per row at most; table last. Use the `theme` and `charts` skills.
- Empty and error states are part of the design: "No rows match these filters", "The data
  source did not answer", never a blank card or a zero that is not a real zero.

## Make the figures checkable
- Mark each static headline figure with the query that produces it:
  `<span data-src="data/orders.csv" data-sql="select count(*) from 'data/orders.csv'">12,480</span>`
  and run `mcp__studio__check_page`. For live figures filled in by script, show the query or
  definition in a "How this is calculated" note under the card instead.
- Verify two or three figures yourself with an independent query before finishing, and look
  at the page with `look_at_page` after applying a filter.
