# Model accounts

Moved from the [front page](../../README.md).

## Model accounts

Claude models work best with the studio; other models are experimental. The assistant is
Anthropic's agent program (the one inside Claude Code), driven as a library.

| Account | `studio setup --account` | What has actually been run |
|---|---|---|
| Your Claude plan, signed in on this computer | `plan` | Almost every run of the studio so far, on Opus, Sonnet and Haiku |
| An Anthropic API key | `api-key` | The environment the agent gets is tested. No paid request has been made |
| Claude on Amazon Bedrock, Google Vertex AI or Microsoft Foundry | `bedrock`, `vertex`, `foundry` | Same: environment tested, no paid request made |
| A gateway you already run that speaks Anthropic's message format, such as LiteLLM | `gateway` | Whole requests on a GPT model through a LiteLLM gateway, including a sourced page built in 7 minutes |
| A key from OpenAI, OpenRouter, Azure OpenAI or Gemini | `openai`, `openrouter`, `azure-openai`, `gemini` | One GPT model, with another gateway standing in for the provider: it passed four of the five parts of the model check. No real key of these four providers has been used. No Gemini model and no open-weight model has been run |

**The Claude plan is for single-owner use only.** Anthropic does not allow a tool to offer
plan sign-in to its users. So the plan is offered when you alone use the studio, on this
computer or on your own private network. A studio set up as shared refuses the plan and needs
a key or a cloud account. If setup finds no Claude sign-in on the computer, `studio login`
starts one.

**Where a key goes.** A key you type in setup is stored in the system keychain, or in
`~/.config/studio2/secrets.json` (readable by you only) on a machine without a keychain. It is
never written to the config file and never shown again.

```sh
studio setup --account plan --local --single                    # your Claude plan, this computer, just you
studio setup --account api-key --key-file ~/anthropic.key       # an Anthropic key, read from a file
studio setup --account gateway --base-url http://127.0.0.1:4000 --key-file ~/gateway.key --models <names>
studio setup --show                                             # what is set up now, without any key
```

A key is never given as a flag: it is read from a file, from standard input (`--key-stdin`),
or typed at the prompt. Add `--yes` when a config file already exists.

**Keys from other providers.** The agent program only speaks Anthropic's message format, so
for these keys the studio runs LiteLLM beside itself to translate.

```sh
studio litellm install
studio setup --account openrouter --key-file ~/openrouter.key --models <vendor/model>,<a cheaper model>
studio setup --account azure-openai --key-file ~/azure.key --base-url https://<resource>.openai.azure.com \
             --api-version <version> --models <deployment>
```

- LiteLLM is installed only on a yes: version 1.102.1 and nothing newer, every package
  checked against a hash kept in [`studio2/litellm-requirements.txt`](../litellm-requirements.txt).
  Two LiteLLM releases on PyPI carried credential-stealing code in March 2026, so the studio
  never installs "the latest".
- It listens on this computer only, starts when a request needs it and stops with the studio.
- Before you rely on a model, run **Library → Models → Check**. It sends a fixed two-message
  request and keeps the result beside the model: passed or what failed, the time, the steps
  and the cost. It asks first when the model costs money.
- What to expect on this route: Anthropic does not support its agent program on other models,
  so an update on either side can break it. The built-in web search does not work (the
  studio's own search does). Prompt caching is not passed on, so the same work can cost more.
  How well edits land depends on the model.

## Models tried through OpenRouter

One request each on 19 November 2026, through a LiteLLM gateway with an OpenRouter key (the
`gateway` account), on a scratch studio: "make this project's page a pricing page with three
plans, then say what you built". The agent had to read the page, write it, look at it in the
browser and answer. Cost is what the gateway reported for the whole request. One request says
whether a model works here, not how good it is: the pages were checked for the three prices,
not judged.

| Model (`openrouter/…`) | Result | Time | Cost |
|---|---|---|---|
| `deepseek/deepseek-v4.1-flash` | Worked | 45 s | $0.011 |
| `z-ai/glm-5.3-flash` | Worked | 53 s | $0.010 |
| `openai/gpt-6-luna` | Worked | 130 s | $0.014 |
| `minimax/minimax-m3.1-flash` | Worked (25 model calls, the most of any) | 112 s | $0.032 |
| `z-ai/glm-5.3-flashx` | Worked | 94 s | $0.045 |
| `google/gemini-4-hydrogen` | Worked | 59 s | $0.116 |
| `moonshotai/kimi-k3.1-pro` | Worked | 103 s | $0.118 |
| `openai/gpt-6.1-terra` | Worked; one edit call was malformed and it tried again | 113 s | $0.147 |
| `x-ai/grok-4.7` | Worked | 98 s | $0.162 |
| `openai/gpt-6.2-sol` | Worked on the third try; OpenAI rate-limited the first two (HTTP 429) | 69 s | $0.176 |
| `moonshotai/kimi-k2.7-code` | 1 of 2: the first request read the page, then ended with no edit and no answer; the second worked but printed a stray `<antThinking>` tag | 46 s | $0.044 |
| `z-ai/glm-5.3` | Failed: a text-only model (see below) | | |
| `z-ai/glm-5.3-prime`, `qwen/qwen4-max` | Refused by OpenRouter under a "zero data retention" account setting: no provider of these models offers it | | |

## A harder job: the same dashboard from 28 models

On 19 November 2026 each model was asked once, in a new project, to build an interactive sales
dashboard from the same 453-row data file: four headline numbers, three charts, a leaderboard, a
sortable table of the 15 largest orders, filters for city and channel that recompute everything,
three findings, light and dark, and a phone layout. The data was generated with a fixed seed, so
the right numbers were known, and every page was then checked in a real browser against them:
11 checks a page either passes or does not (the four numbers on the page and in the reply,
both filters recomputing correctly, the table sorting and holding the true 15 orders, the
findings section, dark mode, no sideways scroll on a phone, no console errors). Looks were not
scored. All but the three plan runs went through a LiteLLM gateway with an OpenRouter key; cost
is what the gateway recorded. A run was stopped at 15 minutes.

| Model | Checks | Time | Cost |
|---|---|---|---|
| `anthropic/claude-sonnet-5.5` on the Claude plan | 11 of 11 | 1.7 min | none (about $0.26 at API prices) |
| `anthropic/claude-opus-5.5` on the Claude plan | 11 of 11 | 3.7 min | none (about $1.02 at API prices) |
| `anthropic/claude-fable-5.2` on the Claude plan | 11 of 11 | 4.8 min | none (about $2.74 at API prices) |
| `anthropic/claude-haiku-5.5` | 11 of 11 | 2.7 min | $0.04 |
| `z-ai/glm-5.3-flash` | 11 of 11 | 2.0 min | $0.05 |
| `deepseek/deepseek-v4.1-flash` | 11 of 11 | 4.6 min | $0.07 |
| `deepseek/deepseek-v4.1-flash-1017` | 11 of 11 | 5.0 min | $0.17 |
| `z-ai/glm-5.3` (text only) | 11 of 11 | 5.2 min | $0.21 |
| `z-ai/glm-5.3-flashx` | 11 of 11 | 8.1 min | $0.38 |
| `openai/gpt-6.1-terra` | 11 of 11 | 8.8 min | $0.38 |
| `minimax/minimax-m3` | 11 of 11 | 5.8 min | $0.39 |
| `deepseek/deepseek-v4-pro-0813` (text only) | 11 of 11 | 11.3 min | $0.52 |
| `x-ai/grok-build-0.1` | 11 of 11 | 2.6 min | $0.69 |
| `google/gemini-4-hydrogen` | 11 of 11 | 5.0 min | $0.75 |
| `openai/gpt-6.2-sol` | 11 of 11 | 10.6 min | $1.00 |
| `google/gemini-4-neon` | 11 of 11 | 5.6 min | $1.03 |
| `moonshotai/kimi-k3` | 11 of 11 | 3.4 min | $1.54 |
| `stepfun/step-5-preview` | 11 of 11 | 8.7 min | $1.79 |
| `moonshotai/kimi-k3.1-pro` | 11 of 11 | 9.2 min | $2.00 |
| `anthropic/claude-sonnet-5.5` | 10 of 11 | 75 s | $0.27 |
| `openai/gpt-6.2-astra` | 10 of 11 | 4.8 min | $1.96 |
| `minimax/minimax-m3.1-flash` | stopped at 15 min; the page as it stood passes 9 of 11 | 15.2 min | $0.20 |
| `google/gemini-4-carbon` | stopped at 15 min; the page as it stood passes 9 of 11 | 15.3 min | $0.72 |
| `x-ai/grok-4.7` | stopped at 15 min; the page as it stood passes 9 of 11 | 15.3 min | $1.96 |
| `moonshotai/kimi-k2.7-code` | ended with nothing built | 21 s | $0.02 |
| `xiaomi/mimo-v2.6-pro` | stopped at 15 min, nothing built | 15.3 min | $0.02 |
| `mistralai/mistral-large-4-0` | stopped at 15 min, nothing built | 15.2 min | $0.08 |
| `tencent/hy4` | stopped at 15 min, nothing built | 15.4 min | $0.17 |

What it showed:

- **Correctness did not follow price.** 19 of 28 passed every check, from a
  4-cent run to a 2-dollar one. The studio's own instructions and tools carry most of the result;
  what differs between models is time, cost and how far they push the design.
- **Claude Sonnet 5.5 through OpenRouter was the fastest and among the cheapest of the strong
  ones**, which makes it the natural default for a shared studio, where the Claude plan is off.
- **The cheap models were enough for this kind of job:** Claude Haiku 5.5, GLM 5.3 Flash and
  DeepSeek V4.1 Flash each built a correct page for under a dime.
- **Text-only models worked** (DeepSeek V4 Pro 0813, GLM 5.3) once the studio kept pictures from
  them (below); they check a page by the browser's measurements and cannot see it.
- **Four built nothing:** Kimi K2.7 Code ended as completed after loading two skills, and Mistral
  Large 4.0, Hunyuan HY4 and MiMo V2.6 Pro had made a handful of calls in 15 minutes.
- **One run is one run.** A model that failed here may pass on a second try, and the reverse.

What the simpler request showed about the route, beyond single models:

- **A model that cannot take pictures is kept from them.** The agent sees its page by opening a
  screenshot, and a text-only model's provider refuses the request that carries one (404, "No
  endpoints found that support image input"), which used to end the request after the work was
  done. The studio now knows which models take text only (from the provider's listing, kept in
  the price list; a connection can name more in `text_only_models`), refuses the agent's Read of
  a picture for them with what to use instead, and says so in the chat. Such a model checks its
  page by the browser's measurements and cannot see how it looks. Run once for real: DeepSeek V4
  Pro 0813 was refused two screenshots, carried on and finished a dashboard that passed every
  check. Not covered: a picture a connector's own tool answers with, and PDFs.
- **A request can end as completed with nothing done** (the first Kimi K2.7 Code request). The
  studio does not catch that yet.
- **Cached input was reported for every model that worked**, so the "prompt caching is not
  passed on" line above does not hold for this route; it was written about the LiteLLM the
  studio runs itself and has not been measured again there.
- **Your OpenRouter privacy settings decide which models exist for you.** A refused model
  answers 404 with the reason in the message.
