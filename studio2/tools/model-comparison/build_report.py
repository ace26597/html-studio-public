"""Build the two pages of a model comparison from a run folder.

Usage: build_report.py <run folder> [--shots <folder of pictures>] [--date "19 November 2026"]
Reads results.jsonl, grades.jsonl, verdict.json and models.json from the run folder (see README.md)
and writes:

  studio2/docs/model-comparison.md               the page GitHub shows: findings, the table, a
                                                 picture of every page
  docs/benchmarks/model-comparison/index.html    the same as one interactive page of the site
                                                 (sortable table, a cost chart, every page in
                                                 light, dark and at phone width)
  docs/benchmarks/model-comparison/shots/        the pictures both pages show
  docs/benchmarks/model-comparison/banner.jpg    eight of the pages side by side

Every number on the pages comes from the run's files. The only hand-written parts are in verdict.json
(what the run suggests, and a reading of how each page looks), and the pages say so. Set STUDIO_LINK
to the address of the studio's project screen (ending in /p/) to add a link to each project.
"""
import argparse
import html
import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE.parents[1] / "docs"                                  # studio2/docs: the page GitHub shows
SITE = HERE.parents[2] / "docs" / "benchmarks" / "model-comparison"    # the site: the interactive page
PICTURES = "../../docs/benchmarks/model-comparison"              # the site's folder, as the page in studio2/docs names it
LIVE = "https://ace26597.github.io/html-studio-public/benchmarks/model-comparison/"
FAMILIES = [("anthropic/", "Claude"), ("openai/", "GPT"), ("google/", "Gemini"), ("deepseek/", "DeepSeek"),
            ("moonshotai/", "Kimi"), ("z-ai/", "GLM"), ("x-ai/", "Grok"), ("minimax/", "MiniMax"),
            ("mistralai/", "Mistral"), ("tencent/", "Hunyuan"), ("xiaomi/", "MiMo"), ("stepfun/", "Step")]
PLAN = {"fable": ("Claude Fable 5.2", "anthropic/claude-fable-5.2"), "opus": ("Claude Opus 5.5", "anthropic/claude-opus-5.5"),
        "sonnet": ("Claude Sonnet 5.5", "anthropic/claude-sonnet-5.5")}
CHECKS = [("done", "Finished the request"), ("answer", "Reply gives the four headline numbers correctly"),
          ("headline", "Page shows the four headline numbers correctly"), ("city", "City filter recomputes correctly (Portland)"),
          ("channel", "Channel filter recomputes correctly (Referral)"), ("sort", "Top-orders table sorts on a heading click"),
          ("top15", "Table holds the true 15 largest won orders"), ("stands", "Has a “What stands out” section"),
          ("dark", "Dark mode has a dark background"), ("phone", "No sideways scroll at phone width"),
          ("clean", "No errors in the browser console")]
BANNER = ["fable", "openai_gpt-6.2-sol", "moonshotai_kimi-k3.1-pro", "google_gemini-4-hydrogen",
          "anthropic_claude-haiku-5.5", "deepseek_deepseek-v4.1-flash-1017", "z-ai_glm-5.3-flash", "minimax_minimax-m3"]


def load(path):
    rows = {}
    for line in open(path) if path.exists() else []:
        row = json.loads(line)
        rows[row["model"]] = row
    return rows


def size(value):
    return len(value) if isinstance(value, list) else int(value or 0)


def gather(run, shots_from, out):
    results, grades = load(run / "results.jsonl"), load(run / "grades.jsonl")
    listing = (json.load(open(run / "models.json")).get("models") or {}) if (run / "models.json").exists() else {}
    verdict = json.load(open(run / "verdict.json")) if (run / "verdict.json").exists() else {}
    (out / "shots").mkdir(parents=True, exist_ok=True)
    models = []
    for model, row in results.items():
        grade = grades.get(model) or {}
        on_plan = row["connection"] == "subscription"
        listed = PLAN[model][1] if on_plan else model.replace("openrouter/", "")
        label = PLAN[model][0] if on_plan else listed.split("/", 1)[1]
        key = f"plan/{model}" if on_plan else listed
        family = next((name for prefix, name in FAMILIES if listed.startswith(prefix)), listed.split("/")[0])
        price = listing.get(listed) or {}
        usage, spent = row.get("usage") or {}, row.get("turn_cost") or {}
        tokens = usage.get("tokens") or {}
        head, answer = grade.get("headline") or {}, grade.get("answer") or {}
        built = any(head.values())          # a page that still is the untouched starter passes nothing
        sort = grade.get("sort") or {}
        done = row.get("status") == "completed"
        checks = {"done": done, "answer": done and bool(answer) and all(answer.values()),
                  "headline": bool(head) and all(head.values()),
                  "city": (grade.get("filter_city") or {}).get("result") == "correct",
                  "channel": (grade.get("filter_channel") or {}).get("result") == "correct",
                  "sort": bool(sort.get("sortable")), "top15": sort.get("top15") == 15,
                  "stands": bool(grade.get("stands_out")), "dark": built and (grade.get("dark_background") or 1) < 0.35,
                  "phone": built and (grade.get("phone_overflow_px") or 0) <= 2,
                  "clean": built and not grade.get("errors")}
        checks = {name: bool(value) for name, value in checks.items()}
        name = grade.get("name") or model.replace("openrouter/", "").replace("/", "_")
        shots = {}
        for kind in ("desktop", "full", "dark", "phone") if built else ():
            source, kept = shots_from / f"{name}-{kind}.jpg", out / "shots" / f"{name}-{kind}.jpg"
            if source.exists() and source.resolve() != kept.resolve():
                shutil.copyfile(source, kept)
            if kept.exists():
                shots[kind] = f"shots/{kept.name}"
        models.append({
            "id": listed, "key": key, "name": name, "built": built, "label": label, "family": family, "plan": on_plan,
            "pid": row.get("pid"), "status": row.get("status"), "error": (row.get("error") or "")[:240],
            "tries": row.get("tries"), "seconds": row.get("seconds"),
            "cost": spent.get("cost_usd") if not on_plan else None,
            "plan_equivalent": usage.get("cost_usd") if on_plan else None,
            "requests": spent.get("requests"), "tools": size(row.get("tools")), "tool_errors": size(row.get("tool_errors")),
            "out_tokens": tokens.get("output"),
            "price_in": float(price["prompt"]) * 1e6 if price.get("prompt") else None,
            "price_out": float(price["completion"]) * 1e6 if price.get("completion") else None,
            "text_only": "image" not in (price.get("input") or ["image"]),
            "checks": checks, "passed": sum(checks.values()), "outside": grade.get("outside_hosts") or [],
            "city_detail": (grade.get("filter_city") or {}).get("result"),
            "channel_detail": (grade.get("filter_channel") or {}).get("result"),
            "shots": shots, "note": (verdict.get("notes") or {}).get(key, "")})
    models.sort(key=lambda m: (-m["passed"], m["cost"] if m["cost"] is not None else 0, m["seconds"] or 0))
    return models, verdict


def minutes(seconds):
    return "" if seconds is None else f"{seconds} s" if seconds < 90 else f"{seconds / 60:.1f} min"


def money(value):
    return "" if value is None else f"${value:.3f}" if value < 0.1 else f"${value:.2f}"


def outcome(model, total):
    cut = str(model["status"]).startswith("stopped")
    if model["status"] == "completed" and model["built"]:
        return f"{model['passed']} of {total}"
    if model["status"] == "completed":
        return "said it was done with nothing built"
    if cut and model["built"]:
        return f"stopped at 15 minutes; the page as it stood passes {model['passed']} of {total}"
    return "stopped at 15 minutes with nothing built" if cut else "failed"


def cost_of(model):
    if model["plan"]:
        return f"no credits (about {money(model['plan_equivalent'])} at API prices)"
    return money(model["cost"]) or "not recorded"


def banner(models, out):
    """Eight first screens side by side, for the front page."""
    try:
        from PIL import Image
    except ImportError:
        return False
    by = {m["name"]: m for m in models}
    chosen = [by[name] for name in BANNER if name in by and by[name]["shots"].get("desktop")][:8]
    if len(chosen) < 8:
        chosen = [m for m in models if m["shots"].get("desktop")][:8]
    width, height, gap = 480, 300, 10
    sheet = Image.new("RGB", (4 * width + 5 * gap, 2 * height + 3 * gap), "#e6e9ee")
    for index, model in enumerate(chosen):
        first = Image.open(out / model["shots"]["desktop"]).convert("RGB").resize((width, height))
        sheet.paste(first, (gap + (index % 4) * (width + gap), gap + (index // 4) * (height + gap)))
    sheet.save(out / "banner.jpg", quality=84)
    return [m["label"] for m in chosen]


def markdown(models, verdict, truth, date, spent, shown):
    total = len(CHECKS)
    built = [m for m in models if m["built"]]
    perfect = [m for m in models if m["passed"] == total]
    strip = lambda text: text.replace("<strong>", "**").replace("</strong>", "**")
    lines = [
        "# One job, the same for every model", "",
        f"On {date}, {len(models)} models were each asked once, in a new project, to build the same interactive sales "
        f"dashboard from the same data file. {len([m for m in built if m['status'] == 'completed'])} finished with a "
        f"working page, and {len(perfect)} of those passed every one of {total} checks made in a real browser against "
        f"the data's true numbers. It cost ${spent:.2f} of OpenRouter credits.", "",
        f"[![Eight of the dashboards, each built by a different model from the same request]({PICTURES}/banner.jpg)](#the-dashboards)", "",
        f"*Left to right, top then bottom: {', '.join(shown)}.*" if shown else "", "",
        "The same results as one interactive page (a sortable table, cost against time, and every dashboard in light, "
        f"dark and at phone width): [{LIVE.removeprefix('https://')}]({LIVE}). Its source is "
        f"[`docs/benchmarks/model-comparison`]({PICTURES}).", "",
        "## What the run suggests", ""]
    lines += [f"- {strip(line)}" for line in verdict.get("summary") or []]
    lines += ["", "One run per model shows what a model did this once. It does not show how often it would do it again: "
              "a model that failed here may pass on a second try, and the reverse.", "",
              "## Every model", "",
              "| Model | Family | Checks | Time | Cost | Model calls |", "|---|---|---|---|---|---|"]
    for m in models:
        tags = (" (on a Claude plan)" if m["plan"] else "") + (" (text only)" if m["text_only"] else "")
        lines.append(f"| `{m['id']}`{tags} | {m['family']} | {outcome(m, total)} | {minutes(m['seconds'])} | "
                     f"{cost_of(m)} | {m['requests'] or ''} |")
    lines += ["", "## The dashboards", "",
              "The first screen of each page at desktop width; select one for the whole page. The line under a picture "
              "is one reading of how it looks, not a score.", ""]
    cells = []
    for m in built:
        cell = (f"**{m['label']}**{' (Claude plan)' if m['plan'] else ''}<br>{outcome(m, total)} · {minutes(m['seconds'])} · {cost_of(m)}<br>"
                f"<a href=\"{PICTURES}/{m['shots'].get('full') or m['shots']['desktop']}\">"
                f"<img src=\"{PICTURES}/{m['shots']['desktop']}\" alt=\"The dashboard {html.escape(m['label'])} built\" width=\"420\"></a>"
                + (f"<br>{m['note']}" if m["note"] else ""))
        cells.append(cell)
    lines += ["<table>"]
    for start in range(0, len(cells), 2):
        pair = cells[start:start + 2] + [""] * (2 - len(cells[start:start + 2]))
        lines.append("<tr>" + "".join(f"<td width=\"50%\" valign=\"top\">{cell}</td>" for cell in pair) + "</tr>")
    lines += ["</table>", "", "## What was checked", "",
              "Each check is something the page either does or does not do. How good a page looks is not one of them.", ""]
    lines += [f"{number}. {label}" for number, (_key, label) in enumerate(CHECKS, 1)]
    lines += ["", "The data is generated sample data for a made-up company, with a fixed seed, so the right answers are "
              f"known: ${truth['revenue']:,} of won revenue, {truth['won']} won orders, a {truth['win_rate']}% win rate and "
              f"{truth['avg_kw']} kW on average. For each filter the grader picks one value in the page's own control and "
              "compares the page with the data for that value.", "",
              "## How it was run", "",
              "- Every model got the same brief and data, in a new project in one studio, through one route: Claude Code as "
              "the agent, a LiteLLM gateway, OpenRouter. Claude Fable, Opus and Sonnet ran on a Claude plan; Sonnet and "
              "Haiku also ran through OpenRouter.",
              "- Cost is what the gateway recorded for the request. A run was stopped at 15 minutes.",
              "- Two models take text only. The studio keeps pictures from such a model, so it checks its page by the "
              "browser's measurements and cannot see it.",
              "- The brief, the data, the runner, the grader and this page's builder are in "
              "[`studio2/tools/model-comparison`](../tools/model-comparison), with the raw results of this run.", "",
              "<details><summary>The brief every model received</summary>", "", "```text",
              (HERE / "brief.txt").read_text().strip(), "```", "", "</details>", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--shots", type=Path)
    parser.add_argument("--date", default="19 November 2026")
    args = parser.parse_args()
    out = SITE
    models, verdict = gather(args.run, args.shots or args.run / "shots", out)
    truth = json.load(open(HERE / "truth.json"))["all"]
    spent = round(sum(m["cost"] or 0 for m in models), 2)
    data = {"models": models, "checks": CHECKS, "studio": os.environ.get("STUDIO_LINK", ""), "date": args.date,
            "picks": verdict.get("picks") or [], "summary": verdict.get("summary") or [], "truth": truth, "spent": spent}
    page = (HERE / "report_template.html").read_text()
    page = page.replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    page = page.replace("<!--BRIEF-->", html.escape((HERE / "brief.txt").read_text()))
    (out / "index.html").write_text('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
                                    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                                    "<style>body { margin: 0; } img { max-width: 100%; }</style>\n" + page + "\n</html>\n")
    shown = banner(models, out)
    (DOCS / "model-comparison.md").write_text(markdown(models, verdict, truth, args.date, spent, shown or []))
    pictures = list((out / "shots").iterdir())
    print(f"{len(models)} models; {len(pictures)} pictures, {sum(f.stat().st_size for f in pictures) / 1e6:.1f} MB; "
          f"${spent} spent; banner: {'made' if shown else 'not made (Pillow is not installed)'}")


if __name__ == "__main__":
    main()
