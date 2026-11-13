---
name: app-backend
description: Patterns for giving a Studio project its own back end - a scheduled fetcher, a small API over a database or files, saving user input, proxying an external API. Use when a page needs live data, a schedule, stored state, or to call an API that the browser cannot call directly.
---

# Project back end

The `<studio-project>` block in each message says how the studio runs your app. This skill
is about what to build.

## Default stack
One file, `server.py`, FastAPI, started by `studio.json`:
```json
{"run": "uv run --with-requirements requirements.lock uvicorn server:app --host 127.0.0.1 --port $PORT"}
```
List the packages in `requirements.txt`, one name a line (`fastapi`, `uvicorn`, `httpx`; a version
only where you need one), then write the lock, which pins the exact version of everything so the
app runs the same every time: `uv pip compile -q requirements.txt -o requirements.lock`. Do that
again whenever you change `requirements.txt`, and never edit the lock by hand. (The studio writes
a missing or out-of-date lock itself when it starts the app.)
Serve `index.html` and static files from the same app, and put the API under `/api/…`.
Keep what the app writes while it runs in `state/` (SQLite for records, JSON for small settings):
the studio neither watches nor versions that folder, so saving never reloads the page or shows up
as a change to the project. Create the folder at start-up. Files you prepare yourself for the page
(a CSV to chart) go in `data/`, which is versioned. Run Python with `uv run …`, never a bare
`python3`.

## Patterns
- **Scheduled fetch**: an asyncio task started in the app's lifespan that fetches, stores
  rows in SQLite with the fetch time, then sleeps. Fetch once at start-up. Record failures
  in a table and show "last updated" and the last error on the page. Never leave old data
  looking current.
- **API over data**: endpoints run fixed, parameterised queries. Do not accept SQL from the page.
- **Attached database**: call `$STUDIO_DATA_URL/<name>/query` from the server with the header
  `X-Studio-Token: $STUDIO_TOKEN`. Cache results for a short time; show the query time.
- **External API**: call it from the server (browser headers and CORS often get refused),
  set a timeout, pass on a clear error. Keys come from environment variables the person sets;
  never write a key into a project file.
- **Saving input**: validate on the server, write atomically, return the saved record.

## Before finishing
Restart the app as the project block describes, request your own endpoints with curl, read
the log when something fails, and look at the page with `look_at_page`: requests made by the
real page can fail where curl succeeds.
