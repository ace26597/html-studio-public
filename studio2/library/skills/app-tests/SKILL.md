---
name: app-tests
description: Write and run tests for a project's app (routes via a test client, data functions, one test per bug fixed), kept off live data, run with run_tests before finishing. Use for a back end, an app "with tests" or one that has tests.
---

# App tests

Tests let the next change be made without breaking the last one. The studio runs them with
`mcp__studio__run_tests` and shows the result in the project facts, so the person and the next
request can see whether the app works.

## What you need first
- The app's code and how it starts (`studio.json`, `server.py`, `package.json`).
- What the app must do, from the person's request. Test what they asked for, not the framework.

## Steps
1. **Where tests live**: `tests/` at the top of the project, one file per area
   (`tests/test_api.py`, `tests/test_data.py`). Python uses pytest; a Node app uses the test
   runner it already has (vitest, jest or `node --test`) through the `test` script in
   `package.json`.
2. **Keep tests away from live data.** The studio gives every run `STUDIO_TEST_STATE`, an empty
   folder that is removed afterwards. The app takes its state folder from it:
   ```python
   STATE = Path(os.environ.get("STUDIO_TEST_STATE") or Path(__file__).parent / "state")
   ```
   Read it where the app opens its database or files, not only at import, or set the variable
   in the test before importing the app. Never point a test at `state/`, at the person's real
   database, or at a real outside API: replace the call with a small stand-in function.
3. **Test each route with a test client** (FastAPI or Starlette):
   ```python
   from fastapi.testclient import TestClient
   import server

   client = TestClient(server.app)

   def test_adding_an_item_returns_it():
       made = client.post("/api/items", json={"title": "Buy milk"})
       assert made.status_code == 201 and made.json()["title"] == "Buy milk"
       assert client.get("/api/items").json()[-1]["title"] == "Buy milk"
   ```
   For each route: one test of the normal case, one of bad input (the answer is 400 or 422 and
   nothing is saved), and one of a missing record (404).
   If the app is in a folder of its own, add the project folder to the import path at the top of
   the test file (`sys.path.insert(0, str(Path(__file__).parents[1]))`).
4. **Test the data functions directly**: totals, filters, parsing, date handling, with a few
   rows written in the test. Check the edges: no rows, one row, a missing value.
5. **One test for every bug you fix**, written first so it fails, then the fix makes it pass.
   Name it after what went wrong (`test_total_ignores_cancelled_orders`).
6. **Keep the whole run under about 30 seconds**: small data, no sleeps, no real network. Call
   the app through its test client, in the same process; a standard-library server with no test
   client is started inside the test on port 0 (a free port) and stopped after.
7. **Run them**: call `mcp__studio__run_tests` (it picks pytest for `tests/`, or `npm test`;
   a `"test"` command in `studio.json` overrides that). `path` runs one file, `filter` one test.
   Packages the app needs come from `requirements.txt`; the studio adds pytest and httpx itself.

## Before finishing
- Whenever the app's code changed in this request, call `run_tests` and read the answer.
- A failure: fix the code, or the test when the test itself is wrong, and run again.
- **Never delete, skip or weaken a test to make the run pass.** If something still fails, say
  plainly in your reply what fails and why.
- Say the result in one line: "12 tests pass" or "11 pass, 1 fails: …".

## Never
- Tests that write into `state/` or `data/`, or call a real outside service.
- A test that passes whatever the code does (`assert True`, catching every error).
- Starting the app with its studio.json command or on `$PORT` from a test, or restarting the
  studio's running app for a test.
