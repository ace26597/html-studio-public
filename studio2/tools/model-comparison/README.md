# The model comparison

One job, given to many models in the same way, and checked the same way: build an interactive
sales dashboard from one data file. The results of the run of 19 November 2026 are in
[`studio2/docs/model-comparison.md`](../../docs/model-comparison.md).

It measures what a model does inside the studio, through the studio's own agent, tools and
instructions. It is not a test of a model on its own.

## What is here

| File | What it is |
|---|---|
| `brief.txt` | The request every model gets, word for word |
| `make_data.py`, `orders.csv`, `truth.json` | The data: 453 quotes of a made-up solar installer, generated with a fixed seed, and the right answers worked out from it |
| `run.py` | Makes one new project per model in a running studio, uploads the data, sends the brief and records what happened (status, time, tokens, cost) |
| `grade.py` | Opens each page in a real browser and checks it against `truth.json`; takes the pictures |
| `build_report.py`, `report_template.html` | Write the two pages from a run folder: the one GitHub shows (`studio2/docs/model-comparison.md`) and the interactive one of the site (`docs/benchmarks/model-comparison/`) |
| `runs/2026-11-19/` | That run's raw results, grades, the price list of the day, and `verdict.json` |

## The eleven checks

A page passes a check or it does not; none of them is about looks.

1. The request finished.
2. The reply gives the four headline numbers correctly.
3. The page shows the four headline numbers correctly (revenue within 0.6 %, the count exactly,
   the win rate within 0.6 points, the average size within 0.06 kW).
4. Choosing one city in the page's own filter recomputes the numbers correctly.
5. The same for one sales channel.
6. The table of the largest orders reorders when a column heading is clicked.
7. That table holds the true 15 largest won orders (a table shown a few rows at a time is paged through).
8. The page has a "What stands out" section.
9. In dark mode the page has a dark background.
10. At phone width the page does not scroll sideways.
11. The browser console shows no errors.

## Run it

It needs a running studio with the model accounts you want to compare, and it spends what those
models cost. Nothing here caps that except the 15 minutes each model gets: the run of 19 November
cost $16.41 for 25 paid models, from 2 cents to 2 dollars a model.

```sh
cd studio2
# one new project per model, three at a time; <connection> is a model account's id in the studio
.venv/bin/python tools/model-comparison/run.py tools/model-comparison/runs/mine 3 \
    gateway=openrouter/anthropic/claude-haiku-5.5 gateway=openrouter/z-ai/glm-5.3-flash subscription=sonnet

# check every page in a browser and take the pictures (the studio must still be running)
PYTHONPATH=server .venv/bin/python tools/model-comparison/grade.py tools/model-comparison/runs/mine

# write studio2/docs/model-comparison.md and docs/benchmarks/model-comparison/
.venv/bin/python tools/model-comparison/build_report.py tools/model-comparison/runs/mine --date "<the day>"
```

`STUDIO_URL` names another studio than `http://127.0.0.1:8200`. `build_report.py` reads prices
and which models take text only from `models.json` in the run folder (the fields it uses from
OpenRouter's public model list; without the file those columns are empty), and the hand-written
part from `verdict.json`: `summary` and `picks` for the top of the page, `notes` for a line under
each picture. Without `verdict.json` the pages hold the measurements only.

## What it does not tell you

- **One request per model.** It shows what a model did once, not how often it would do it again.
  In the run of 19 November one model ended as completed with nothing built; it had done the same
  in one of two earlier tries that day.
- **The route is part of the result.** Every paid model went through a LiteLLM gateway and
  OpenRouter, which chooses the provider. Another route can be faster, slower or dearer.
- **Passing every check is not the same as a good page.** Nineteen pages passed everything and
  they do not look alike. The pictures are there for that.
- **The pictures in `docs/benchmarks/model-comparison/shots/` are the run's**, not copied into
  `runs/`: `grade.py` writes new ones into a new run folder.
