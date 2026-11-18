# The GLP-1 race

A live dashboard app the studio built from one request on 17 November 2026: clinical trials of
nine obesity and diabetes drugs from the ClinicalTrials.gov API (version 2), and adverse-event
reports and drug labels from openFDA. Both are free public APIs and need no key. The run, the
request and what was checked are in [the examples page](../../examples.md); the video is
[demo-glp1.mp4](../../demo/demo-glp1.mp4).

These are the files as the agent left them, without the saved text of the sources (other
people's writing; `sources/index.json` lists them), the agent's working notes and the app's
cache. Nothing was edited by hand afterwards.

## Run it

It needs [uv](https://docs.astral.sh/uv/) and a network connection.

```sh
cd studio2/docs/examples/glp1-race
uv run --with-requirements requirements.lock uvicorn server:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/`. The first start fetches everything (about 6.9 MB, under a
minute here) and keeps it in `state/cache.json`; after that the trials are fetched again every
12 hours and the FDA data every 24. This was run once from an empty folder outside the studio on
the Mac it was built on: the page answered and the back end returned 1,763 trials. It has not
been run on another machine.

## What to know before reading its numbers

- The numbers are live, so they will differ from the ones in the video and on the examples page.
- FDA figures are counts of reports, not rates. They cannot be compared between drugs as risk;
  the page says so above that section.
- A trial counts for a drug whenever ClinicalTrials.gov's intervention search matches it, which
  includes trials where the drug is only the comparator.
- It is not medical advice.

## What is not right

Left as built: on a phone two chart labels overlap (the axis title against the "2026 incomplete"
note, and one long drug name on the phase 3 timeline), the sources section leaves an empty gap
under the shorter of its two cards on a wide screen, and the phone page is very long.

It is not among the examples on the studio's home page: those are pages only, and this has a
back end of its own.
