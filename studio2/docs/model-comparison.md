# One job, the same for every model

On 19 November 2026, 28 models were each asked once, in a new project, to build the same interactive sales dashboard from the same data file. 21 finished with a working page, and 19 of those passed every one of 11 checks made in a real browser against the data's true numbers. It cost $16.41 of OpenRouter credits.

[![Eight of the dashboards, each built by a different model from the same request](../../docs/benchmarks/model-comparison/banner.jpg)](#the-dashboards)

*Left to right, top then bottom: Claude Fable 5.2, gpt-6.2-sol, kimi-k3.1-pro, gemini-4-hydrogen, claude-haiku-5.5, deepseek-v4.1-flash-1017, glm-5.3-flash, minimax-m3.*

The same results as one interactive page (a sortable table, cost against time, and every dashboard in light, dark and at phone width): [ace26597.github.io/html-studio-public/benchmarks/model-comparison/](https://ace26597.github.io/html-studio-public/benchmarks/model-comparison/). Its source is [`docs/benchmarks/model-comparison`](../../docs/benchmarks/model-comparison).

## What the run suggests

- **With a Claude plan on your own computer, stay on Claude.** Fable, Opus and Sonnet each passed all eleven checks in under five minutes and spent no credits.
- **For a shared studio, where the plan is not allowed, Claude Sonnet 5.5 through OpenRouter is the default to pick.** All eleven checks in 75 seconds for $0.27: the fastest run of all, and a quarter of what GPT 6.2 Sol cost for the same result in 10.6 minutes.
- **Cheap models are good enough for this kind of job.** Claude Haiku 5.5 ($0.04), GLM 5.3 Flash ($0.05) and DeepSeek V4.1 Flash ($0.07) each built a correct, working dashboard.
- **Paying more did not buy more correctness.** The three dearest runs (Kimi K3.1 Pro $2.00, GPT 6.2 Astra $1.96, Grok 4.7 $1.96) were no more correct than the cheapest. Astra's table does not sort, and Grok was still working when the 15-minute limit stopped it.
- **Four built nothing:** Kimi K2.7 Code said it was done after loading two skills, and Mistral Large 4.0, Hunyuan HY4 and MiMo V2.6 Pro had built nothing after 15 minutes.
- The studio's own instructions and tools carry most of the result: the same checks pass across families and the pages share a house style. What differs between models is speed, cost and how far they push the design.

One run per model shows what a model did this once. It does not show how often it would do it again: a model that failed here may pass on a second try, and the reverse.

## Every model

| Model | Family | Checks | Time | Cost | Model calls |
|---|---|---|---|---|---|
| `anthropic/claude-sonnet-5.5` (on a Claude plan) | Claude | 11 of 11 | 1.7 min | no credits (about $0.26 at API prices) |  |
| `anthropic/claude-opus-5.5` (on a Claude plan) | Claude | 11 of 11 | 3.7 min | no credits (about $1.02 at API prices) |  |
| `anthropic/claude-fable-5.2` (on a Claude plan) | Claude | 11 of 11 | 4.8 min | no credits (about $2.74 at API prices) |  |
| `anthropic/claude-haiku-5.5` | Claude | 11 of 11 | 2.7 min | $0.043 | 16 |
| `z-ai/glm-5.3-flash` | GLM | 11 of 11 | 2.0 min | $0.048 | 17 |
| `deepseek/deepseek-v4.1-flash` | DeepSeek | 11 of 11 | 4.6 min | $0.072 | 57 |
| `deepseek/deepseek-v4.1-flash-1017` | DeepSeek | 11 of 11 | 5.0 min | $0.17 | 59 |
| `z-ai/glm-5.3` (text only) | GLM | 11 of 11 | 5.2 min | $0.21 | 25 |
| `z-ai/glm-5.3-flashx` | GLM | 11 of 11 | 8.1 min | $0.38 | 35 |
| `openai/gpt-6.1-terra` | GPT | 11 of 11 | 8.8 min | $0.38 | 26 |
| `minimax/minimax-m3` | MiniMax | 11 of 11 | 5.8 min | $0.39 | 37 |
| `deepseek/deepseek-v4-pro-0813` (text only) | DeepSeek | 11 of 11 | 11.3 min | $0.52 | 18 |
| `x-ai/grok-build-0.1` | Grok | 11 of 11 | 2.6 min | $0.69 | 56 |
| `google/gemini-4-hydrogen` | Gemini | 11 of 11 | 5.0 min | $0.75 | 17 |
| `openai/gpt-6.2-sol` | GPT | 11 of 11 | 10.6 min | $1.00 | 46 |
| `google/gemini-4-neon` | Gemini | 11 of 11 | 5.6 min | $1.03 | 16 |
| `moonshotai/kimi-k3` | Kimi | 11 of 11 | 3.4 min | $1.54 | 19 |
| `stepfun/step-5-preview` | Step | 11 of 11 | 8.7 min | $1.79 | 86 |
| `moonshotai/kimi-k3.1-pro` | Kimi | 11 of 11 | 9.2 min | $2.00 | 48 |
| `anthropic/claude-sonnet-5.5` | Claude | 10 of 11 | 75 s | $0.27 | 7 |
| `openai/gpt-6.2-astra` | GPT | 10 of 11 | 4.8 min | $1.96 | 16 |
| `minimax/minimax-m3.1-flash` | MiniMax | stopped at 15 minutes; the page as it stood passes 9 of 11 | 15.2 min | $0.20 | 42 |
| `google/gemini-4-carbon` | Gemini | stopped at 15 minutes; the page as it stood passes 9 of 11 | 15.3 min | $0.72 | 12 |
| `x-ai/grok-4.7` | Grok | stopped at 15 minutes; the page as it stood passes 9 of 11 | 15.3 min | $1.96 | 30 |
| `moonshotai/kimi-k2.7-code` | Kimi | said it was done with nothing built | 21 s | $0.017 | 3 |
| `xiaomi/mimo-v2.6-pro` | MiMo | stopped at 15 minutes with nothing built | 15.3 min | $0.022 | 6 |
| `mistralai/mistral-large-4-0` | Mistral | stopped at 15 minutes with nothing built | 15.2 min | $0.084 | 5 |
| `tencent/hy4` | Hunyuan | stopped at 15 minutes with nothing built | 15.4 min | $0.17 | 14 |

## The dashboards

The first screen of each page at desktop width; select one for the whole page. The line under a picture is one reading of how it looks, not a score.

<table>
<tr><td width="50%" valign="top">**Claude Sonnet 5.5** (Claude plan)<br>11 of 11 · 1.7 min · no credits (about $0.26 at API prices)<br><a href="../../docs/benchmarks/model-comparison/shots/sonnet-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/sonnet-desktop.jpg" alt="The dashboard Claude Sonnet 5.5 built" width="420"></a><br>The same warm palette as Opus with a larger, labelled monthly chart. Plain and clear.</td><td width="50%" valign="top">**Claude Opus 5.5** (Claude plan)<br>11 of 11 · 3.7 min · no credits (about $1.02 at API prices)<br><a href="../../docs/benchmarks/model-comparison/shots/opus-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/opus-desktop.jpg" alt="The dashboard Claude Opus 5.5 built" width="420"></a><br>Warm cream page with orange bars; dense and tidy, cities and channels side by side with paired revenue and win-rate bars.</td></tr>
<tr><td width="50%" valign="top">**Claude Fable 5.2** (Claude plan)<br>11 of 11 · 4.8 min · no credits (about $2.74 at API prices)<br><a href="../../docs/benchmarks/model-comparison/shots/fable-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/fable-desktop.jpg" alt="The dashboard Claude Fable 5.2 built" width="420"></a><br>Navy header band; the three findings sit right under the headline numbers as large figures, and the leaderboard is beside the monthly chart. The clearest layout of the lot.</td><td width="50%" valign="top">**claude-haiku-5.5**<br>11 of 11 · 2.7 min · $0.043<br><a href="../../docs/benchmarks/model-comparison/shots/anthropic_claude-haiku-5.5-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/anthropic_claude-haiku-5.5-desktop.jpg" alt="The dashboard claude-haiku-5.5 built" width="420"></a><br>Teal bars with a value on every month, section links under the title. Simple and readable; nothing looks cheap about it.</td></tr>
<tr><td width="50%" valign="top">**glm-5.3-flash**<br>11 of 11 · 2.0 min · $0.048<br><a href="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-flash-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-flash-desktop.jpg" alt="The dashboard glm-5.3-flash built" width="420"></a><br>Orange accent and the findings above the chart. A lot of page for five cents.</td><td width="50%" valign="top">**deepseek-v4.1-flash**<br>11 of 11 · 4.6 min · $0.072<br><a href="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4.1-flash-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4.1-flash-desktop.jpg" alt="The dashboard deepseek-v4.1-flash built" width="420"></a><br>The studio's default look, with section links and a long, complete page. Competent rather than designed.</td></tr>
<tr><td width="50%" valign="top">**deepseek-v4.1-flash-1017**<br>11 of 11 · 5.0 min · $0.17<br><a href="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4.1-flash-1017-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4.1-flash-1017-desktop.jpg" alt="The dashboard deepseek-v4.1-flash-1017 built" width="420"></a><br>A warm gradient header and a sentence that frames the year before the numbers. A step up in looks from the other Flash.</td><td width="50%" valign="top">**glm-5.3**<br>11 of 11 · 5.2 min · $0.21<br><a href="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-desktop.jpg" alt="The dashboard glm-5.3 built" width="420"></a><br>Filter chips instead of drop-downs, clean blue charts. Also text only, also fine.</td></tr>
<tr><td width="50%" valign="top">**glm-5.3-flashx**<br>11 of 11 · 8.1 min · $0.38<br><a href="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-flashx-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/z-ai_glm-5.3-flashx-desktop.jpg" alt="The dashboard glm-5.3-flashx built" width="420"></a><br>Plain and text-heavy near the top, with an explanation of each formula. Correct, not pretty.</td><td width="50%" valign="top">**gpt-6.1-terra**<br>11 of 11 · 8.8 min · $0.38<br><a href="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.1-terra-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.1-terra-desktop.jpg" alt="The dashboard gpt-6.1-terra built" width="420"></a><br>Calm green page with its own navigation and a 'Sales overview' title. Restrained and professional.</td></tr>
<tr><td width="50%" valign="top">**minimax-m3**<br>11 of 11 · 5.8 min · $0.39<br><a href="../../docs/benchmarks/model-comparison/shots/minimax_minimax-m3-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/minimax_minimax-m3-desktop.jpg" alt="The dashboard minimax-m3 built" width="420"></a><br>Peach background with orange bars; simple and warm.</td><td width="50%" valign="top">**deepseek-v4-pro-0813**<br>11 of 11 · 11.3 min · $0.52<br><a href="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4-pro-0813-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/deepseek_deepseek-v4-pro-0813-desktop.jpg" alt="The dashboard deepseek-v4-pro-0813 built" width="420"></a><br>Clean default blue. It could not see its own page (text only), and it shows no sign of that.</td></tr>
<tr><td width="50%" valign="top">**grok-build-0.1**<br>11 of 11 · 2.6 min · $0.69<br><a href="../../docs/benchmarks/model-comparison/shots/x-ai_grok-build-0.1-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/x-ai_grok-build-0.1-desktop.jpg" alt="The dashboard grok-build-0.1 built" width="420"></a><br>The plainest: stock chart styling in cyan, and its city and channel charts put revenue and win rate on two different scales, which is hard to read.</td><td width="50%" valign="top">**gemini-4-hydrogen**<br>11 of 11 · 5.0 min · $0.75<br><a href="../../docs/benchmarks/model-comparison/shots/google_gemini-4-hydrogen-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/google_gemini-4-hydrogen-desktop.jpg" alt="The dashboard gemini-4-hydrogen built" width="420"></a><br>Findings up top, and each chart's title says what it shows ('Won revenue peaked in April…'). Good at telling you what to look at.</td></tr>
<tr><td width="50%" valign="top">**gpt-6.2-sol**<br>11 of 11 · 10.6 min · $1.00<br><a href="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.2-sol-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.2-sol-desktop.jpg" alt="The dashboard gpt-6.2-sol built" width="420"></a><br>The most ambitious design: a green hero banner with an illustration, tabs, icons on each tile, findings beside the chart.</td><td width="50%" valign="top">**gemini-4-neon**<br>11 of 11 · 5.6 min · $1.03<br><a href="../../docs/benchmarks/model-comparison/shots/google_gemini-4-neon-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/google_gemini-4-neon-desktop.jpg" alt="The dashboard gemini-4-neon built" width="420"></a><br>Findings first, each as a labelled card with its number; standard blue charts below. Compact.</td></tr>
<tr><td width="50%" valign="top">**kimi-k3**<br>11 of 11 · 3.4 min · $1.54<br><a href="../../docs/benchmarks/model-comparison/shots/moonshotai_kimi-k3-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/moonshotai_kimi-k3-desktop.jpg" alt="The dashboard kimi-k3 built" width="420"></a><br>Compact and plain; abbreviates the revenue tile to $4.11M with the full figure beneath.</td><td width="50%" valign="top">**step-5-preview**<br>11 of 11 · 8.7 min · $1.79<br><a href="../../docs/benchmarks/model-comparison/shots/stepfun_step-5-preview-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/stepfun_step-5-preview-desktop.jpg" alt="The dashboard step-5-preview built" width="420"></a><br>Clean, with a light and dark switch of its own. It needed 86 model calls and $1.79 to get there.</td></tr>
<tr><td width="50%" valign="top">**kimi-k3.1-pro**<br>11 of 11 · 9.2 min · $2.00<br><a href="../../docs/benchmarks/model-comparison/shots/moonshotai_kimi-k3.1-pro-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/moonshotai_kimi-k3.1-pro-desktop.jpg" alt="The dashboard kimi-k3.1-pro built" width="420"></a><br>An editorial headline ('The year in solar sales.'), its own navigation and an insight panel beside the chart. Distinctive, and the dearest run here.</td><td width="50%" valign="top">**claude-sonnet-5.5**<br>10 of 11 · 75 s · $0.27<br><a href="../../docs/benchmarks/model-comparison/shots/anthropic_claude-sonnet-5.5-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/anthropic_claude-sonnet-5.5-desktop.jpg" alt="The dashboard claude-sonnet-5.5 built" width="420"></a><br>Close to the plan run's page: clean, restrained, nothing wasted.</td></tr>
<tr><td width="50%" valign="top">**gpt-6.2-astra**<br>10 of 11 · 4.8 min · $1.96<br><a href="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.2-astra-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/openai_gpt-6.2-astra-desktop.jpg" alt="The dashboard gpt-6.2-astra built" width="420"></a><br>Polished: dark header, a highlighted revenue tile, findings beside the chart. But clicking a column heading does not reorder the orders table, which shows five rows a page.</td><td width="50%" valign="top">**minimax-m3.1-flash**<br>stopped at 15 minutes; the page as it stood passes 9 of 11 · 15.2 min · $0.20<br><a href="../../docs/benchmarks/model-comparison/shots/minimax_minimax-m3.1-flash-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/minimax_minimax-m3.1-flash-desktop.jpg" alt="The dashboard minimax-m3.1-flash built" width="420"></a><br>A report-style headline ('How Brightline Solar sold solar in 2025') in a narrow column. Complete when it was stopped.</td></tr>
<tr><td width="50%" valign="top">**gemini-4-carbon**<br>stopped at 15 minutes; the page as it stood passes 9 of 11 · 15.3 min · $0.72<br><a href="../../docs/benchmarks/model-comparison/shots/google_gemini-4-carbon-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/google_gemini-4-carbon-desktop.jpg" alt="The dashboard gemini-4-carbon built" width="420"></a><br>Much like Hydrogen's page, findings first. It was still checking its work when the limit stopped it.</td><td width="50%" valign="top">**grok-4.7**<br>stopped at 15 minutes; the page as it stood passes 9 of 11 · 15.3 min · $1.96<br><a href="../../docs/benchmarks/model-comparison/shots/x-ai_grok-4.7-full.jpg"><img src="../../docs/benchmarks/model-comparison/shots/x-ai_grok-4.7-desktop.jpg" alt="The dashboard grok-4.7 built" width="420"></a><br>A plain blue page that opens with a sentence summing up the year. Complete when it was stopped.</td></tr>
</table>

## What was checked

Each check is something the page either does or does not do. How good a page looks is not one of them.

1. Finished the request
2. Reply gives the four headline numbers correctly
3. Page shows the four headline numbers correctly
4. City filter recomputes correctly (Portland)
5. Channel filter recomputes correctly (Referral)
6. Top-orders table sorts on a heading click
7. Table holds the true 15 largest won orders
8. Has a “What stands out” section
9. Dark mode has a dark background
10. No sideways scroll at phone width
11. No errors in the browser console

The data is generated sample data for a made-up company, with a fixed seed, so the right answers are known: $4,107,930 of won revenue, 187 won orders, a 42.5% win rate and 7.86 kW on average. For each filter the grader picks one value in the page's own control and compares the page with the data for that value.

## How it was run

- Every model got the same brief and data, in a new project in one studio, through one route: Claude Code as the agent, a LiteLLM gateway, OpenRouter. Claude Fable, Opus and Sonnet ran on a Claude plan; Sonnet and Haiku also ran through OpenRouter.
- Cost is what the gateway recorded for the request. A run was stopped at 15 minutes.
- Two models take text only. The studio keeps pictures from such a model, so it checks its page by the browser's measurements and cannot see it.
- The brief, the data, the runner, the grader and this page's builder are in [`studio2/tools/model-comparison`](../tools/model-comparison), with the raw results of this run.

<details><summary>The brief every model received</summary>

```text
The attached file data/orders.csv holds sample data for a made-up rooftop-solar installer, Brightline Solar: one row per quote given in 2025.

Build this project's page as an interactive sales dashboard for the owner:

1. A header with the company name and the period, and four headline numbers: revenue from won orders, number of won orders, win rate (won divided by won plus lost; open quotes are left out), and the average system size of won orders in kW.
2. A chart of won revenue by month.
3. A chart comparing the cities and one comparing the sales channels, each showing revenue and win rate.
4. A leaderboard of the salespeople.
5. A table of the 15 largest won orders that can be sorted by clicking a column heading.
6. Filters for city and for channel that update every number, chart and table on the page.
7. A short "What stands out" section with three findings from the data, each with its number.

The page must read its numbers from the CSV file (do not type them in), look good in light and in dark mode, and work on a phone. When you are done, reply with the four headline numbers for the whole year.
```

</details>
