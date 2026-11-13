# Model accounts

Moved from the [front page](../../README.md).

## Model accounts

Claude models work best with the studio; other models are experimental. The assistant is
Anthropic's agent program (the one inside Claude Code), driven as a library.

| Account | `studio setup --account` | What has actually been run |
|---|---|---|
| Your Claude plan, signed in on this computer | `plan` | Almost every run recorded in the status table, on Opus, Sonnet and Haiku |
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
