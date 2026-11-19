# Studio 2: the developer's page

This folder is the studio. What it is, how to install it, model accounts, attaching an app and
safety are on the [front page](../README.md). This page is for working on the code: the
layout, running from source, tests, every setting, and the rules.

[API and event contract](CONTRACT.md) · [Attaching an app](ATTACH.md)

## Layout

| Path | What |
|---|---|
| `server/studio2_server/` | The FastAPI server and the `studio` command |
| `web/` | The interface: React 19, Vite, TypeScript. Built into `web/dist`, which the server serves |
| `web/mock/` | A stand-in server for interface work (`npm run mock`); not shipped |
| `library/` | What the agent is given: `system-prompt.md`, 24 skills (four of them Anthropic's, see `library/skills/THIRD_PARTY.md`), 4 sub-agents, 6 starting templates, 8 finished examples (`examples/`, made from real projects with `tools/examples/add.py`), the price list for the backup cost calculation (`model-prices.json`, written by `tools/prices/update.py`), suggested connectors |
| `tests/` | Server tests. They run on the scripted agent and make no model call |
| `install.sh` | The installer described on the front page |
| `server-requirements.txt`, `server-dev-requirements.txt` | The server's packages at exact versions, pinned by hash, for macOS and Linux; the second adds what the tests need. Written by `tools/lock-server.sh` |
| `litellm-requirements.txt` | LiteLLM 1.102.1 and its packages, pinned by hash |
| `start-tailnet.sh` | Starts the server for one machine plus its owner's Tailscale devices |
| `tools/demo-video/` | The scripts that recorded [`docs/demo/`](docs/demo/) ([how](tools/demo-video/README.md)) |
| `tools/model-comparison/` | The same job given to many models and checked in a browser: the brief, the data, the runner, the grader, the page builder and one run's results ([how](tools/model-comparison/README.md)) |
| `tools/prices/` | Writes `library/model-prices.json`, the price list behind a figure the studio works out itself, from OpenRouter's public model list |
| `../docs/` | The site: the [guide](../docs/index.html), the [benchmarks page](../docs/benchmarks/index.html) and the interactive model comparison. Static files, served as they are: by the public copy's host as a web site, and by the studio itself under `/site/`, linked from the projects page |
| `screenshots/` | Five screenshots from the first step, taken with the scripted agent |
| `.venv/`, `.tools/`, `web/node_modules/`, `web/dist/` | Made by the installer; git-ignored |

The server, by concern:

| Modules | Concern |
|---|---|
| `api.py`, `studio.py`, `store.py`, `events.py`, `turns.py` | Routes, the project record, the event log, one agent turn per project at a time |
| `harness/` (`base.py`, `claude.py`, `fake.py`, `cheap.py`) | The agent program behind one interface: Claude Agent SDK, the scripted agent, the cheap-model call |
| `workspace.py`, `watcher.py`, `preview.py`, `apps.py`, `static/bridge.js` | A project's folder and git history, live reload, the page proxy, a project's own back end, the select/edit/draw bridge |
| `tools.py`, `search.py`, `sources.py`, `check.py`, `filecheck.py`, `look.py`, `layout.py`, `session.py`, `datasources.py` | The studio's 26 tools for the agent: search, saved sources, the page check, the check of figures in Office files and PDFs, looking at the page (pieces, measurements, and the person's sign-in when they allow it), databases |
| `office.py`, `convert.py`, `diagram.py`, `media.py`, `generate.py`, `voice.py`, `drawing.py`, `templates.py`, `ownskills.py` | Office files, conversions between document formats, diagrams (Mermaid, kept in `static/vendor/mermaid`), video and pictures, generated media, dictation, drawings, templates, skills a person makes |
| `attach.py`, `ship.py` | Bringing an app by its folder; making it live |
| `context.py` | Folders, files and addresses a project points at as read-only context for the agent |
| `cli.py`, `setup.py`, `setup_api.py`, `doctor.py`, `config.py`, `keystore.py`, `access.py` | The `studio` command, setup, settings, the secret store, access from other devices |
| `gateway.py`, `modelcheck.py`, `models_api.py` | The LiteLLM the studio runs; "Check this model" |
| `costs.py`, `prices.py` | What a request cost: read back from a gateway's own log, or worked out from what the turn used, model by model |
| `catalog.py`, `library.py`, `picker.py` | The library index and what is picked per request |
| `risk.py` | Which shell commands ask first |

Data lives outside this folder, in `<repo>/data/studio2/` (git-ignored) unless `STUDIO2_DATA`
or `--data` says otherwise: `projects/<id>/` with `project.json`, `events.jsonl` and
`workspace/` (the project's folder, a git repository), plus the library index, preferences,
templates and usage records. The layout is in [CONTRACT.md](CONTRACT.md), "Data layout".

## How the pieces fit

The browser shows the studio's shell and, inside a frame, the project's page, which is served
on a port of its own (a separate origin per project). A small script the studio adds to that
page, the bridge, reports selections, text edits and drawings to the shell.

A message starts a turn. The turn runner hands it to a harness; the harness runs the agent
program in the project's folder with the studio's tools, the picked skills and connectors, and
an environment without the studio's secrets. Everything the agent does becomes an event,
appended to `events.jsonl` and streamed to the browser, so a reload replays it. The file
watcher reloads the page as files change. When the turn ends the studio commits the folder:
one version per turn and per edit by the person, and a restore is a new commit.

A project that declares a run command in `studio.json` gets its own back end, started by the
studio on the page port + 100 and reached through the page port. It gets a project token
instead of keys, and calls models and attached databases through the studio.

Change [CONTRACT.md](CONTRACT.md) first, then the server and the interface.

## Run from source

`install.sh --dev` does all of this and adds what the tests need. By hand:

```sh
cd studio2
uv venv --python 3.12
uv pip install --require-hashes -r server-dev-requirements.txt
uv pip install --no-deps -e .
(cd web && npm ci && npm run build)
.venv/bin/python -m studio2_server          # http://127.0.0.1:8200, in the foreground
```

- The packages come from the lock files, not from `pyproject.toml`, which states only minimum
  versions. To move to newer releases on purpose: `tools/lock-server.sh --upgrade`, then install
  from the new files into a fresh environment and run the tests before committing them.

- `studio` is a console entry of the package (`.venv/bin/studio`); `studio start` runs the same
  server in the background with a pid file and a log under the data folder. Run
  `uv pip install --no-deps -e .` again in a checkout made before the command existed.
- `STUDIO2_FAKE=1`, or `studio start --fake`, uses the scripted agent: no model call. It
  branches on words in the message (`fail`, `slow`, `risky`, `dashboard`, `backend`,
  `use skill …`, `find skill …`, `need connector …`).
- The `look` extra is Playwright (`playwright==1.62.0`, pinned to the cached Chromium build).
  It drives a Chromium that is already in the Playwright cache (`$PLAYWRIGHT_BROWSERS_PATH`,
  else `~/Library/Caches/ms-playwright`, else `~/.cache/ms-playwright`); the studio never
  downloads one. Do not run `playwright install` on a machine whose owner has not said yes.
  Details and the order of checks: [docs/install.md](docs/install.md), "Looking at pages".
- Interface work: run the server, then `cd web && npm run dev` (http://127.0.0.1:5200, which
  proxies the API). Or `npm run mock` for a stand-in server with no Python at all.
- The server serves `web/dist`. `npm run build` therefore changes a running studio at once.
  To look at a build first, build elsewhere (`npx vite build --outDir <folder>`).
- A second studio beside a running one needs its own port and data folder:
  `studio start --port 9400 --data <folder>`. To keep it away from your real settings and
  keychain, also set `STUDIO2_CONFIG=<scratch file>` and `STUDIO2_SECRETS=file`.
- Without a terminal, setup takes its answers from flags or from a file:
  `studio setup --answers answers.json`. The file's shape is in [CONTRACT.md](CONTRACT.md),
  "Answers"; the smallest useful one is `{"account": {"account": "plan"}}`.

## Tests

```sh
cd studio2 && STUDIO2_FAKE=1 .venv/bin/pytest -q
cd studio2/web && npm test && npx tsc --noEmit
```

On 19 November 2026: 1660 server tests passed and 3 were skipped, in about 350 seconds; 477
interface tests passed. Neither suite calls a model, and the server suite uses an in-memory
secret store and an empty LiteLLM folder, so it does not touch your settings.

Two of the skipped tests are rows of a table that do not apply to their case (a git setting that
exists only in config). The third needs a real PostgreSQL and a user that may write, to prove that
the studio's queries are read-only:

```sh
STUDIO2_TEST_PG=host:port:database:user:password STUDIO2_FAKE=1 .venv/bin/pytest -q
```

Passing tests are not the whole check: a change is also run for real, in a real browser on the
real server, and what that run did not reach is said plainly.

## Settings

Setup writes `~/.config/studio2/config.json`; its keys are described in
[CONTRACT.md](CONTRACT.md), "The config file". The environment wins over the file. These are
all the variables the code reads.

Where it runs and who may open it:

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO2_DATA` | `<repo>/data/studio2` | The data folder. `--data` wins |
| `STUDIO2_CONFIG` | `~/.config/studio2/config.json` | The config file. Secrets kept in a file sit beside it |
| `STUDIO2_PORT_RANGE` | `8201-8299`, or the 99 ports after a port chosen in setup | Ports for project pages, as `first-last`. Each project's back end is on its page port + 100 |
| `STUDIO2_HOSTS` | none | Extra addresses to listen on, comma separated. Loopback is always served. Needs an access key unless `STUDIO2_OPEN_TAILNET=1` |
| `STUDIO2_NAMES` | none | More DNS names the studio answers to, comma separated. It always answers to addresses, `localhost`, the machine's own names and the names in its TLS certificate; any other name gets a 421 page (this stops a website pointing its own name at your studio). Needed behind a proxy or for a name only your router's DNS gives |
| `STUDIO2_ACCESS_KEY` | the one setup stored | The key other devices sign in with, at least 24 characters |
| `STUDIO2_PAGE_HOSTS` | none | Names for project pages behind a tunnel or proxy that carries one port: `8403=site.example.com`, and `*=p{port}.example.com` for the rest, comma separated. Whatever is in front must send each name to that port here |
| `STUDIO2_GATE_KEYS`, `STUDIO2_GATE_AUDIENCE` | none | A gate in front that signs people in (an identity-aware proxy): the `https://` address of its public signing keys and the audience its tokens name. A request whose token verifies needs no access key. Both or neither |
| `STUDIO2_GATE_HEADER`, `STUDIO2_GATE_ISSUER` | `Cf-Access-Jwt-Assertion`; the scheme and host of the keys address | The header that carries the gate's token, and who must have issued it |
| `STUDIO2_OPEN_TAILNET` | off | `1`: devices on your Tailscale network need no key. Every address in `STUDIO2_HOSTS` must then be a Tailscale address; refused in a shared studio |
| `STUDIO2_SHARED` | off | `1`: shared with other people. The Claude plan is switched off and an access key is compulsory |
| `STUDIO2_TLS_CERT`, `STUDIO2_TLS_KEY` | none | A certificate and its key. The extra addresses then answer over HTTPS only, by the name on the certificate; loopback stays plain HTTP |
| `STUDIO2_SECRETS` | `auto` | Where keys are kept: `keychain`, `file` or `memory`. `auto` is the keychain, or the owner-only file where there is none; the scripted agent uses `memory` |
| `STUDIO_NO_OPEN` | off | `1`: `studio start` does not open the browser |

The agent and its models:

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO2_FAKE` | off | `1`: the scripted agent, no model call |
| `STUDIO2_FAKE_DELAY` | `0.4` | Seconds between the scripted agent's steps |
| `STUDIO2_CLAUDE_CLI` | the one bundled with the Agent SDK | Path to a specific Claude Code program |
| `STUDIO2_AGENT_ENV` | none | Names of variables to pass to the agent's shell although they look like secrets, comma separated |
| `STUDIO2_LITELLM_HOME` | `studio2/.tools/litellm` | Where the studio's own LiteLLM is installed |
| `STUDIO2_CHECK_SECONDS` | `420` | Time limit for each message of "Check this model" (20 at least) |
| `STUDIO2_ANTHROPIC_URL` | `https://api.anthropic.com` | The address `studio doctor --online` asks for an Anthropic key's model list |
| `STUDIO2_PICK_TIMEOUT` | `6` | Seconds the library's picking call may take before the word search decides alone |
| `STUDIO2_LIBRARY_UNUSED_AFTER` | `5` | Picks in a row without a use after which a skill stops being picked |

Limits and helper programs:

| Variable | Default | Meaning |
|---|---|---|
| `STUDIO2_APP_MODEL_DAILY_USD` | `2.00` | What one project's back end may spend on paid models per day |
| `STUDIO2_GENERATE_DAILY_CREDITS` | `100` | What the studio may spend on generated media per day |
| `STUDIO2_FFMPEG` | `ffmpeg` on the PATH | The program that encodes video |
| `STUDIO2_SOFFICE` | LibreOffice in `/Applications`, else `soffice` or `libreoffice` on the PATH | The program that renders Office files |
| `STUDIO2_HIGGSFIELD` | `higgsfield` on the PATH | The generation service's command-line tool |
| `STUDIO2_TRANSCRIBE` | `~/scripts/transcribe.sh` if it exists | The command that turns a recording into text. Without one, the microphone button says so |

A connection in the config file can name an environment variable for its key (`api_key_env`);
the server must then be started with that variable set. The installer reads `STUDIO_BIN_DIR`
and `STUDIO_NODE_VERSION` (`./install.sh --help`). The tests read `STUDIO2_TEST_PG`.

A project's own back end is started with `PORT`, `HOST`, `STUDIO_PROJECT_ID`,
`STUDIO_PROJECT_URL` and, for reaching models and attached databases through the studio,
`STUDIO_TOKEN`, `STUDIO_MODEL_URL` and `STUDIO_DATA_URL`. It does not inherit the studio's
environment.

### Other devices

Setup's second question does this for you. By hand, for your own devices on a private network:

```sh
export STUDIO2_HOSTS=100.x.y.z                         # this machine's private address
export STUDIO2_ACCESS_KEY="$(cat <a file only you can read>)"
.venv/bin/python -m studio2_server
```

Open `http://100.x.y.z:8200/` on the device and enter the key once. For HTTPS on a Tailscale
network, make a certificate for the machine's name and point the two TLS variables at it:

```sh
tailscale cert --cert-file <cert file> --key-file <key file> <name>.ts.net
```

The address then answers only over HTTPS and only by that name. Tailscale certificates last 90
days and are renewed by running the same command. A browser allows the microphone only over
HTTPS or on localhost. Every signed-in device has your full rights, the shell included: never
forward this to the public internet.

## Working rules

- New work goes in `studio2/` only. The earlier engine (`app/`, `hsdoc/`, `packages/`,
  `studio_next/`) is not part of this copy. Nothing here imported from `app/`, `hsdoc/`, `packages/` or
  `studio_next/`; what is ported is copied.
- The contract changes first, then both sides.
- `STUDIO2_FAKE=1` keeps the whole suite at zero model calls. Say what a run on a paid model
  will cost before starting it.
- Screens: phones (360 to 430 px wide), tablets in both orientations, and desktop up to 2560
  px; touch and mouse; light and dark.
- Loopback by default. Never a public address before accounts and a container per project
  exist (Step 4, not built yet): the studio can run a shell.
- No secret in the repository, in a project or in the config file.
- No invented data presented as real. Say plainly what was not checked.
- Test shipping only against folders you made for the purpose, on a throwaway studio. Never
  answer a ship card on a studio that holds someone's real projects.

## Links

- [Front page](../README.md): what the studio is, install, model accounts, safety, limits.
- [CONTRACT.md](CONTRACT.md): every route, event, tool and file format.
- [ATTACH.md](ATTACH.md): attaching an existing app and making it live, with `studio.json` for
  each kind of app.
