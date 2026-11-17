# Attaching the studio to an existing web app

Instructions for a coding agent (Claude Code, Codex or similar) that has been asked to set the
studio up on a machine and point it at an app that already exists, so that a person can open
the app in the studio, click or draw on it, and ask for changes in plain words.

Read `CONTRACT.md` for exact behaviour, the repository's front page (`../README.md`) for
install and model accounts, and `README.md` here for running from source and every setting. This file
says what to do, what has actually been run, and where the limits are. Nothing here is a
promise beyond the "checked" column.

## 0. Rules before you start

1. **Work on a copy, never on the live app.** The agent in the studio edits files and runs
   commands as the user who started the studio. Attach a copy of the code, with made-up or
   sandbox data.
2. **Check where the copy sends its requests.** A front end usually has the address of its
   real back end written in its config. An attached copy of a site was seen calling its
   production API on first load. Point it at a sandbox back end, or accept that those calls
   fail, before anyone uses it.
3. **No live secrets in the project folder.** A `.env` with production keys does not belong in
   an attached copy. Everything in the project folder that git does not ignore is saved in the
   project's versions.
4. **Keep the studio private.** Whoever can open the studio can run commands on that machine
   through the agent. It listens on loopback only unless told otherwise; over Tailscale is the
   only other arrangement that has been used. Do not put it on a public address.
5. **A machine for this should hold nothing else that matters.** There are no user accounts and
   no per-project isolation yet. On a box that only holds the studio and copies of apps, the
   damage an agent can do is limited to that box.
6. **Keep the repository private** unless its owner says otherwise.

## 1. Install the studio

Needs: Python 3.12, `uv`, Node 20.19 or later in 20.x or 22.12 or later (to build the interface
and to run JavaScript apps), `git`. Optional: a Chromium that Playwright can drive (lets the agent look at pages and render
video), `ffmpeg` (video), LibreOffice (Office files).

```sh
./studio2/install.sh --yes      # fetches uv, Python 3.12, Node if needed; builds; adds the `studio` command
studio doctor                   # what is there, what is missing, which model accounts work
studio start --no-open          # background; prints the address; `studio stop` ends it
```

`install.sh --yes` asks nothing: no sudo, no browser download, no setup. Without `--yes` it
asks before each of those. If the installer cannot be used, the hand steps are:

```sh
cd studio2
uv venv --python 3.12
uv pip install --require-hashes -r server-dev-requirements.txt   # the tested versions, checked by hash
uv pip install --no-deps -e .
(cd web && npm ci && npm run build)
.venv/bin/python -m studio2_server          # http://127.0.0.1:8200
```

- **macOS:** the only system the studio and the installer have run on (12 November 2026: from
  a fresh copy, a user with nothing installed, to a running studio in about 12 seconds).
  `start-tailnet.sh` starts the owner's own copy for their Tailscale devices.
- **Linux:** installed and running on Red Hat Enterprise Linux (the owner's report, 13 November 2026); Debian and Ubuntu not run. The installer covers x86-64 and ARM64 with glibc, prints
  `apt-get`, `dnf`, `yum`, `zypper` or `pacman` commands for what it will not install itself,
  and stops on musl (no Node build from nodejs.org). Keys fall back to the owner-only file
  `~/.config/studio2/secrets.json` when `keyring` finds no keychain. A browser for looking at
  pages is `studio2/.venv/bin/python -m playwright install chromium` (the installer asks;
  never do this on a machine whose owner forbids it) and may need
  `sudo … -m playwright install-deps chromium`. Voice input needs `STUDIO2_TRANSCRIBE`. Expect
  the first run there to find things: run `studio doctor` and the tests first.
- Run the tests before trusting an install: `STUDIO2_FAKE=1 .venv/bin/pytest -q` (no model calls).

## 2. Give it a model

`studio setup` writes this for you. Without a terminal, give it the answers:

```sh
studio setup --account plan --local --single                       # the Claude sign-in on this machine
studio setup --account api-key --key-file ~/anthropic.key --yes    # a key, read from a file, kept in the keychain
studio setup --answers answers.json --yes                          # everything at once (CONTRACT.md, "Answers")
studio setup --show                                                # what is set up now, without any key
```

Connections live in `~/.config/studio2/config.json` (or the file named by `STUDIO2_CONFIG`).
With no file, the studio uses the Claude login already on the machine. Keys are never written
in the file: each connection names where its key lives, either the secret store
(`api_key_secret`, what setup writes: the system keychain, or `~/.config/studio2/secrets.json`
readable by its owner only where there is no keychain) or an environment variable
(`api_key_env`, as below; the studio must then be started with that variable set).

```json
{"connections": [
  {"id": "subscription", "kind": "claude-subscription", "models": ["opus", "sonnet", "haiku"]},
  {"id": "api", "label": "Claude API key", "kind": "anthropic-api",
   "api_key_env": "ANTHROPIC_API_KEY", "models": ["sonnet", "opus", "haiku"]},
  {"id": "aws", "label": "Claude on Bedrock", "kind": "bedrock", "region": "ap-south-1",
   "models": ["sonnet", "haiku"], "model_ids": {"sonnet": "<Bedrock model id>", "haiku": "<Bedrock model id>"}},
  {"id": "gcp", "label": "Claude on Google Cloud", "kind": "vertex", "region": "asia-south1",
   "project": "<project id>", "models": ["sonnet", "haiku"]},
  {"id": "azure", "label": "Claude on Azure", "kind": "foundry", "resource": "<resource name>",
   "api_key_env": "AZURE_FOUNDRY_KEY", "models": ["sonnet"], "model_ids": {"sonnet": "<deployment name>"}},
  {"id": "gateway", "label": "LiteLLM", "kind": "anthropic-gateway",
   "base_url": "http://127.0.0.1:4000", "api_key_env": "LITELLM_API_KEY", "models": ["claude-opus", "gpt-sol"]},
  {"id": "openrouter", "label": "OpenRouter key", "kind": "litellm", "provider": "openrouter",
   "api_key_env": "OPENROUTER_API_KEY", "models": ["<model>", "<a cheaper model>"], "small_model": "<the cheaper model>"}
]}
```

| Kind | Use it when | Checked |
|---|---|---|
| `claude-subscription` | The person running the studio has a Claude plan and is signed in on this machine | Yes, daily |
| `anthropic-api` | You have an Anthropic API key | Environment only; no paid call made |
| `bedrock` / `vertex` / `foundry` | Claude is bought through AWS, Google Cloud or Azure | Environment only; no paid call made |
| `anthropic-gateway` | A gateway that speaks Anthropic's message format (LiteLLM and similar), for Claude or other models | Yes, with LiteLLM (GPT models) |
| `litellm` | You have a key from OpenAI, OpenRouter, Azure OpenAI or Gemini (`provider`: `openai`, `openrouter`, `azure-openai`, `gemini`); the studio runs LiteLLM for it | Yes, with another gateway as the upstream of an `openai` account; no real key of the four |

- **Whose login.** A Claude plan is for the person who holds it, on their own machine. If the
  studio is offered to other people, give it an API key or a cloud account, not someone's
  plan: Anthropic does not allow a tool to offer plan sign-in to its users without approval.
  Say so in setup (`studio setup --shared`, or "Shared with other people" on the setup screen):
  the plan is then switched off and every device must sign in with the access key.
- **OpenAI, OpenRouter, Azure OpenAI or Gemini keys.** Claude models work best with the studio;
  other models are experimental. Enter the key in setup and the studio runs the gateway that
  translates for it (LiteLLM), on this machine only:

  ```sh
  studio litellm install --yes     # once: LiteLLM 1.102.1, about 430 MB, every package checked by hash
  studio setup --account openai --key-file ~/openai.key --models <model>,<cheaper model> --small-model <cheaper model> --yes
  studio setup --account openrouter --key-file ~/openrouter.key --models <vendor/model> --yes
  studio setup --account azure-openai --key-file ~/azure.key --base-url https://<resource>.openai.azure.com \
               --api-version <version> --models <deployment> --yes
  studio setup --account gemini --key-file ~/gemini.key --models <model> --yes
  ```

  An `openai` account takes `--base-url` for any other service that speaks OpenAI's chat
  format (a local model server, another gateway). `--small-model` is the model for background
  work; left out, the studio takes the cheapest-looking one and says which. Until LiteLLM is
  installed such an account is listed as not available, with the command. Then check the
  model: Library → Models → Check, or
  `curl -s -X POST localhost:8200/api/models/check -H 'content-type: application/json' -d '{"connection":"openai","model":"<model>","confirm_cost":true}'`
  and read `GET /api/models` until `running` is null. It costs what two short messages cost
  (roughly 150,000 tokens in), so ask the person first. Four things to tell them: Anthropic
  does not support this route for its agent program, so a release of either side can break
  it; the built-in web search does not work on it (the studio's own `search_web` does);
  prompt caching is not passed on, so it can cost more; and how well a request goes depends
  on the model. Run on 12 November 2026: GPT 6.1 Sol through the studio's own LiteLLM passed
  four of the check's five parts in 48 seconds (it called tools one at a time).
- **Cloud credentials are visible to the agent.** On `bedrock`, `vertex` and `foundry` the
  agent program needs the cloud's credentials, so its shell can read them. Use a role or key
  limited to model calls.
- Every other secret-looking variable in the studio's environment is blank for the agent.
  `STUDIO2_AGENT_ENV=NAME,NAME` passes named ones through.
- A paid connection has no spending limit in the studio. Set one at the provider.

## 3. Attach the app

### From the dialog

New page → **An app on this computer**. Type the folder's full path and press Check: the studio
reads the folder (it writes nothing there) and shows the kind of app it found, its git branch
and last commit, its size, files changed but not committed, env files (names only), the
package install command and the start command. Then choose:

- **Safe copy** (default): a `git clone` of the folder on a branch `studio/<name>-<id>` at the
  original's current commit (or a copy of the files, for a folder without git). The original is
  never written to. Uncommitted changes are listed and left behind unless you tick "Take these
  too"; env files are copied only if ticked and are never kept in versions. The first start
  installs packages (`npm ci` and so on) in the app log, then runs the start command.
- **Edit in place**: the project is the folder itself, for a development machine. The studio
  keeps its versions in its own history beside the project record, so the app's `.git` is not
  touched. Optionally give the address where the app's dev server already runs
  (`http://localhost:5173`): the studio shows it and never starts or stops it. Refused for the
  home folder, a disk root, the studio's own folders, and a folder already edited in place.

Run with the dialog on 12 November 2026: a company website (Next.js 16) as a safe copy,
installed and running in the studio in about ten seconds with the original unchanged (every
file in its `.git` hashed before and after); a Vite app edited in place with its own dev server
running, where an edit through the studio hot-reloaded the page, was saved as a version, and
the app's own repository had no new commit and an unchanged index. Details and limits:
`CONTRACT.md`, "Bring an app by its folder".

What the dialog cannot express yet, found with the CRM's sandbox: an app whose second process
needs its own packages (a Python API in `api/` with its own `requirements.txt`), and data a
sandbox needs before first use. The start command field takes a one-line shell command, so two
processes can be written there, but only the top folder's packages are installed; in that run
the API's `requirements.txt` did not even resolve on a fresh install, so the API did not start.
Such an app still needs the hand steps below (or a copy plus an agent turn that sets it up).

### By hand (the fallback)

These steps were run on 11 and 12 November 2026 with a Next.js 16 site, a Vue 3 starter, and a
sandbox copy of a Next.js + FastAPI app (two processes, sign-in, a service worker).

```sh
STUDIO=http://127.0.0.1:8200
# 1. A project. The answer has "id" and "preview_url".
curl -s -X POST -H 'content-type: application/json' -d '{"name":"My site (copy)"}' $STUDIO/api/projects
W=<data folder>/projects/<id>/workspace        # default data folder: <repo>/data/studio2

# 2. The code: tracked files only, without the original repository's history.
rm -f "$W/index.html"
git -C /path/to/the/app archive HEAD | tar -x -C "$W"

# 3. Its packages, with the runtime the app expects.
(cd "$W" && npm ci)

# 4. Tell the studio how to run it (section 4), then record the import as its own version.
echo '{"run": "exec npx next dev -p $PORT -H 127.0.0.1", "restart": "never"}' > "$W/studio.json"
git -C "$W" add -A && git -C "$W" commit -q -m "[user] Attach My site"

# 5. Start it and wait for "running".
curl -s -X POST $STUDIO/api/projects/<id>/app/restart
curl -s $STUDIO/api/projects/<id>/app          # state, port, error; the log is projects/<id>/app.log
```

Then open `$STUDIO/p/<id>`. The app's own pages fill the window; the chat floats over them.

- The project folder is its own git repository; that is where the studio's versions live. Do
  not copy the original `.git` into it.
- `node_modules/`, `.venv/` and `__pycache__/` are kept out of versions automatically. Make
  sure the app's own `.gitignore` came along, so build output and local env files stay out too.
- The studio only notices a new `studio.json` while the project is open in a browser or at the
  end of a turn; step 5 starts it without either.
- The dialog above does all of this for apps it recognises; these steps remain for the rest.

## 4. `studio.json` for each kind of app

One command, started with `sh -c` in the project folder. It must listen on `127.0.0.1:$PORT`.
Add `"restart": "never"` for a development server that reloads by itself; without it the
studio restarts the app after every turn that changed files.

| App | `run` | Checked |
|---|---|---|
| Next.js | `exec npx next dev -p $PORT -H 127.0.0.1` | Yes (16.1, Turbopack, React 19) |
| Vite (Vue, React, Svelte, Solid) | `exec npx vite --port $PORT --host 127.0.0.1 --strictPort` | Yes (Vue 3; React from the built-in template) |
| FastAPI / Starlette | `uv run --with fastapi --with uvicorn uvicorn server:app --host 127.0.0.1 --port $PORT --reload` | Yes, without `--reload` |
| Plain HTML files | no `studio.json`; the studio serves the folder and reloads on change | Yes |
| Nuxt | `exec npx nuxt dev --port $PORT --host 127.0.0.1` | No |
| SvelteKit | `exec npx vite dev --port $PORT --host 127.0.0.1` | No |
| Astro | `exec npx astro dev --port $PORT --host 127.0.0.1` | No |
| Angular | `exec npx ng serve --port $PORT --host 127.0.0.1` | No |
| Django | `python manage.py runserver 127.0.0.1:$PORT` | No |
| Flask | `flask run --host 127.0.0.1 --port $PORT --debug` | No |
| Rails | `bin/rails server -b 127.0.0.1 -p $PORT` | No |
| Laravel | `php artisan serve --host=127.0.0.1 --port=$PORT` | No |

- **Naming a runtime.** The app does not inherit the studio's full environment. If it needs a
  particular Node or Python, put it in the command: `PATH=$HOME/.local/opt/node24/bin:$PATH exec npx next dev …`.
- **Two processes** (a front end and its own API): make `run` a small script that starts the
  API in the background on a private port and then `exec`s the front end on `$PORT`. Everything
  it starts is stopped together. Run with a Next.js front end and a FastAPI back end:

  ```sh
  #!/bin/sh
  API_PORT=$((PORT + 1000))
  ( cd api && APP_ENV=sandbox DATABASE_URL="sqlite:///$(pwd)/../.sandbox-data/app.db" \
      exec /path/to/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$API_PORT" --reload ) &
  API_PORT="$API_PORT" exec npx next dev -p "$PORT" -H 127.0.0.1
  ```

  This works cleanly when the browser only ever talks to the front end, which passes API calls
  on itself (a rewrite or proxy rule such as `/api/* → http://127.0.0.1:$API_PORT`). If the
  page calls the API at its own address instead, that address must be reachable from the
  person's device and the API must accept the project's address as a caller; prefer adding a
  pass-through rule to the copy. Keep the copy's database and uploads in a folder its
  `.gitignore` ignores, so they are neither versioned nor reported as changes.
- **What the studio does for you on the way through.** Requests reach the app with the app's
  own host and origin, so development servers' host and origin checks pass from a phone too.
  Live-reload sockets are passed on, with the page's cookies. An app's refusal to be shown in a
  frame is dropped. Nothing is cached.

## 5. What the person can do, and how well

| Action | Plain HTML | App with a dev server |
|---|---|---|
| Ask in the chat | Yes | Yes |
| Select an element, then ask | Yes; the agent gets the file | Yes; the agent gets what the framework reports (below) and searches for the rest |
| Draw on the page, then ask | Yes | Yes |
| Edit text in place (Edit mode) | Yes, written straight to the file | No: the text is produced by code. Select it and ask instead |
| Versions and restore | Yes | Yes (code only; a database the app keeps elsewhere is not rolled back) |

What a selection tells the agent about where the element comes from (development builds only):

| Framework | What is reported | Checked |
|---|---|---|
| React 16 to 18 | Component names, and file and line when the build keeps them | No |
| React 19 (including Next.js) | Component names, innermost first; React 19 no longer keeps file and line on elements | Yes: "React component DueReminders < TodayAgendaView < TodayAgenda < TodayScreen" |
| Vue 3 (Vue 2 in part) | The component's file, and its parents' | Yes: `src/components/HelloWorld.vue < src/App.vue` |
| Svelte | File and line | No |
| Astro up to 6 | File and line, when its dev toolbar is on. Astro 7 stopped emitting them | No |
| Angular | Component class name | No |
| A build plugin that stamps elements (`code-inspector-plugin`, TanStack devtools, LocatorJS) | The stamped file and line | No |
| Server templates (Django, Rails, Laravel, Jinja), WordPress | Nothing; the agent searches by the text and markup | No |

For exact file and line in React 19 or Next.js, add `code-inspector-plugin` (MIT) to the copy's
development build; it supports Vite, webpack and Turbopack. Without it the agent found the
right file by component name and text in the one real test.

## 6. What does not fit

- **Content that lives in a database or a CMS** (WordPress posts, a headless CMS): the studio
  changes code. Only theme, template and plugin files are code. Say so and point to the CMS.
- **A production build with no source:** nothing to edit. Attach the source.
- **Sign-in through another site** (Google and similar): the redirect address registered for
  the real app does not match the project's address, so that sign-in fails in the copy. Use a
  local test login.
- **Cookies are shared by every project on the same host:** two attached apps that use the same
  cookie name will sign each other out.
- **Installable-app features** (offline use, push notifications): the studio switches service
  workers off in every project, because a worker fetches pages itself and they would arrive
  without the studio's layer. Those features cannot be tried inside the studio.
- **Calls from the page to another address** (a separate API) are subject to that API's own
  rules about who may call it; the project's address is new to it.
- **Heavy development servers:** each attached JavaScript app is a Node process of several
  hundred megabytes while it runs. Stop the ones not in use (`…/app/stop`).

## 7. Getting changes back into the real app: make it live

The contract is in CONTRACT.md, "Ship: make it live". A project brought in by its folder has a
"Make it live" button in its top bar, and the agent can be asked in the chat ("make it live",
"deploy this"). Either way the studio does the shipping, not the agent:

1. **A recipe first**, kept in the project (not in the app's files): checks to run in the copy,
   how the change reaches the original, commands to run in the original afterwards, an address
   to check, paths that need the owner, and what to run after going back. Ask the agent to "set
   up making this live": it reads the app (scripts, README, deploy files) and proposes one with
   `propose_ship_recipe`; the person confirms it on a card, and again whenever it changes. Or
   write it in the panel's Recipe tab.
2. **How the change reaches the original:**
   - *Leave a branch* (the default for a git folder): the original's repository receives the
     studio branch (`attach.studio_branch`) by a fetch from the copy. Its files and its
     checked-out branch do not change; nothing is restarted. The result says how to merge it.
   - *Merge* (a per-project choice): the same, then a fast-forward of the branch the copy
     started from. Refused, with the reason, when the original has uncommitted changes, is on
     another branch, or has moved on; "Bring the copy up to date" merges the original's new
     commits into the copy and stops on a conflict.
   - *Copy files* (a plain folder): the files changed since the last ship are written into the
     folder, and what they replace is kept so the studio can put it back.
   - *In place*: nothing moves; the checks and after-commands run in the folder.
3. **Every ship:** the studio saves the version, runs the checks in the copy, then shows a card
   with the files, the check results, exactly what will be written where and which commands
   will run. Nothing reaches the original before Allow, whatever the project's approval setting;
   a protected path also needs the project name typed. After Allow: apply, after-commands (in
   the original, with no studio secrets, under a time limit), address check. If a step after
   apply fails the studio goes back by itself and says what state things are in. Every ship is
   recorded (History tab; `projects/<id>/ships/`).
4. **Limits:** data and database changes are not undone by going back; where the change ends up
   is whatever the recipe's commands do; the studio never pushes to GitHub or any remote itself.
   A Next.js app's `next build` writes the same `.next` folder its `next dev` uses, so do not use
   it as a check in a copy whose dev server is running.

For a copy made by hand (no `attach` record), the way back is still a patch made by a person who
can read the change:

```sh
git -C "$W" diff <import commit> HEAD -- . ':!studio.json' > changes.patch
git -C /path/to/the/app apply --3way changes.patch      # then test and deploy as that app is normally deployed
```

## 8. A new section inside an existing site

Attach the site as above, then ask for the new page or tool in the chat and say where it
should live ("a savings calculator at /tools/savings"). The agent adds it in the site's own
framework and style, and it travels back the same way (section 7). For something that should stay
separate from the site, make it its own project instead.

## 9. Not built yet

- Attaching an app whose second process needs its own packages or seeded data, from the dialog.
- Shipping to another machine directly: the studio only runs the recipe's commands on this one
  (a command that copies or pushes elsewhere is the person's own, and is shown on the card).
- User accounts, and a container per project. Until then: one trusted person per machine.
- Any agent program other than Claude Code.
- A run on Linux.
