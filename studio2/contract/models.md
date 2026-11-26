# Contract: Models, setup and cost

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

# Install and first run (step 3m)

Three things are new: an installer, a `studio` command, and setup (in the terminal and as a
first-run screen). `python -m studio2_server` and `start-tailnet.sh` are unchanged; with no
setup keys in the config file every setting comes out as it did before.

## The config file

`~/.config/studio2/config.json` (or `STUDIO2_CONFIG`) keeps its shape and gains optional keys.
It never holds a secret.

```json
{"connections": [
   {"id": "api", "kind": "anthropic-api", "label": "Anthropic API key",
    "models": ["sonnet", "opus", "haiku"], "default_model": "sonnet", "api_key_secret": "api"}],
 "run": {"port": 8200, "hosts": ["100.x.y.z"], "open_tailnet": false},
 "shared": false,
 "access_key_secret": "access-key",
 "setup": {"version": 1, "completed_at": "…"}}
```

- `connections[].api_key_secret` names where the key lives in the secret store (below).
  `api_key_env` works as before and wins when its variable is set. The first connection is the
  default one.
- `run.port`, `run.hosts`, `run.open_tailnet`: where the studio listens. The environment wins:
  `--port`, `STUDIO2_HOSTS` (even when empty), `STUDIO2_OPEN_TAILNET`. A port taken from
  `run.port` brings its own block of project ports (port + 1 … port + 99, back ends + 100);
  `--port` alone keeps 8201–8299 as before; `STUDIO2_PORT_RANGE` overrides both.
- `shared`: the studio is shared with other people (`STUDIO2_SHARED=1` or `0` overrides).
- `access_key_secret`: the access key is in the secret store under this name. Read only when
  `STUDIO2_ACCESS_KEY` is not set and the studio has extra addresses or is shared.
- `setup`: written by setup; its presence means the first-run screen does not open by itself.

## Where a key is kept

One secret store for model keys, the access key, database passwords and connector secrets:
the operating system's keychain through `keyring`; where `keyring` has no backend (a Linux
server without a desktop session) or the keychain refuses, the file `secrets.json` beside the
config file, mode 600, in a folder made with mode 700. `STUDIO2_SECRETS=keychain|file|memory`
forces one. The fake studio uses `memory`. A key is never written to `config.json`, returned
by the API, put in an event or logged.

**A saved secret goes only where it was saved for, through the studio's HTTP API.** The API on
this computer needs no key, so anything running here (the agent's own shell included) can call
it; no route may then carry a saved key, password or connector secret to an address the caller
names. Changing where a model account, a connector or a data source points therefore needs its
secret typed again in the same request; a request that brings none is refused and the saved
secret is left where it was.

What this does not cover: the agent's shell runs as the same person as the studio. Until
projects run in containers it can still do what that person can do without the API: run
`studio setup` (which may name a key by an environment variable), edit
`~/.config/studio2/config.json`, register a plugin in `~/.claude/plugins`, or read the secrets
file. These checks close the API, not the machine.

- `POST /api/setup/models` without `api_key` uses the saved key at the saved address only; a
  different `base_url` is 400 `key_needed`.
- Setup (`POST /api/setup`, `studio setup`): a model account whose `base_url` (or Foundry
  `resource`) changes needs `api_key` (or, in `studio setup`, `api_key_env`) with it.
  `POST /api/setup` refuses `api_key_env`: over the API it would let a caller send any of the
  studio's own variables to an address it chooses. `studio setup` still accepts it; that is no
  protection against the agent, whose shell can run the command.
- A plugin connector whose plugin folder is inside the studio's data folder or inside a project
  folder (including a folder edited in place) has its `${NAME}` filled from the agent's own
  environment (keys and endpoints blanked), for a turn and for the Library's Test alike. A plugin
  installed anywhere else is filled from the studio's environment as before.
- `PATCH /api/mcp-servers/{id}` that changes `url`, `transport`, `command` or `args` of a
  connector with stored headers or environment values needs `headers` (or `env`) in the same
  request, each with a value (400 `secret_needed`); stored ones it does not name are dropped.
- A connector's `headers` or `env`, on `POST` or `PATCH`, never carries an empty value (400
  `secret_needed`). An empty value used to mean "keep the stored one"; that let a caller keep a
  token while adding a variable that changes what the unchanged command runs (`PYTHONPATH`,
  `NODE_OPTIONS`, `DYLD_INSERT_LIBRARIES`). The set is sent whole, every value typed, or not at
  all; `{}` removes them.
- `PATCH /api/data-sources/{id}` that changes `kind`, `host`, `port`, `ssl` or `ca_file` of a
  source with a stored password needs `password` with it (400 `password_needed`). A CA file is
  who may receive the password under `verify-full`: a caller that could swap it for its own
  could answer on the saved address with a certificate it made. What the file holds is fixed
  too: its SHA-256 is kept when it is saved and when the password is typed again, and a
  connection that would send the saved password to a changed file is refused (`ca_changed`)
  until the password is typed again. A `PATCH` that names the same path again, without the
  password, keeps the old fingerprint.

## Shared

Single-owner use is the default and includes the owner's own private network as before
(`STUDIO2_HOSTS` with a key, or `STUDIO2_OPEN_TAILNET=1`). When `shared` is on:

- every `claude-subscription` connection is listed with `available: false` and the reason, and
  a turn on it fails with that reason before any program starts (Anthropic does not allow a
  tool to offer plan sign-in to its users);
- the server does not start without an access key of at least 24 characters, and refuses
  `STUDIO2_OPEN_TAILNET`; requests that are not plainly local sign in as described under
  "Access from other devices";
- `GET` and `POST /api/setup` answer 403 `setup_is_local` to anything but a plainly local request.

Setup refuses to make a studio shared while its only model account is the Claude plan.

## REST

| Method and path | Body | Result |
|---|---|---|
| `GET /api/health` | | gains `setup: {needed}` and `shared` |
| `GET /api/setup` | | `Setup` (below) |
| `POST /api/setup` | `{answers, dry_run?}` | `{applied, changed, changes:[sentence], diff, restart_needed, restart_command, setup: Setup}`; 400 `invalid_setup` with the reason |

`setup.needed` is true when there is no config file, no project exists, `STUDIO2_HOSTS` is not
set and the studio is not the fake one; the interface then shows the setup screen in place of
the home page until it is saved or skipped (`answers: {skip: true}`). `/setup` opens the same
screen at any time.

`Setup`: `{needed, fake, version, config_path, config_exists, secrets: {backend, label},
detected: {claude_login: {found, plan, detail}, tailscale: {found, address, detail}},
accounts: [{account, kind, id, label, summary, key: none|required|optional, fields, models,
default_model, available, note, group: claude|other, provider, lists_models, needs_litellm}],
other_models: {headline, limits}, litellm: {installed, version, size, install_command}, current: {connections: [{id, kind, label, account,
models, default_model, key_from: keychain|file|environment|missing|null, …}], port, hosts,
open_tailnet, shared, access_key_set, done}, running: {port, hosts, open_tailnet, shared,
https}, network_warning, restart_command}`. No key, typed or made, is in any of it.
`detected.claude_login` comes from the agent program's own `auth status`, run with every key
and gateway setting removed; it makes no model call. The fake studio detects nothing.

### Answers

The same object for `POST /api/setup`, `studio setup --answers FILE` and the terminal's
questions. Every part is optional, so a rerun changes one thing.

```json
{"account": {"account": "api-key", "api_key": "…", "models": ["sonnet", "haiku"], "default_model": "sonnet"},
 "where": {"network": true, "host": "tailscale", "port": 8200, "open_tailnet": false},
 "who": {"shared": false, "access_key": "…"},
 "remove": ["old-id"]}
```

- `account.account`: `plan`, `api-key`, `bedrock` (`region`), `vertex` (`region`, `project`),
  `foundry` (`resource` or `base_url`, key optional), `gateway` (`base_url`, key, `models` as
  the gateway names them, optional `small_model`). The key is `api_key` (stored, then named by
  `api_key_secret`) or `api_key_env` (accepted by `studio setup`, refused by `POST /api/setup`; not a protection
  against the agent's shell, see "Where a key is kept"). Changing the
  address of an account that has a key needs the key given again ("Where a key is kept"). The connection gets the kind's usual id (`subscription`,
  `api`, `aws`, `gcp`, `azure`, `gateway`) unless `id` is given, replaces one with that id,
  and goes first; the others stay. `openai`, `openrouter`, `azure-openai` and `gemini` are
  described under "Any model".
- `where.host` is one IPv4 address or `tailscale` (this machine's own). Public, loopback and
  catch-all addresses are refused. `open_tailnet` needs a Tailscale address and a studio that
  is not shared.
- An access key is needed when the studio is shared or has an address without `open_tailnet`.
  `who.access_key` sets it; otherwise the stored one is kept, or one is made and stored (the
  terminal prints it once; the API never returns it; `studio access-key` prints it).
- Writing: the secrets first, then the file; the file it replaces is kept as `config.json.bak`.
  `changes` and `diff` say what changes before anything is written (`dry_run`).
  `restart_needed` is true when the address, port, access rules or shared flag changed to
  something the running studio does not already do; model accounts apply at the next request.

## The `studio` command

`studio2/.venv/bin/studio` (a console entry of the package; the installer links it from
`~/.local/bin`).

| Command | What it does |
|---|---|
| `studio setup` | Asks the three questions in a terminal. With `--answers FILE` or flags (`--account`, `--key-file`, `--key-stdin`, `--key-env`, `--models`, `--default-model`, `--port`, `--local`, `--network [ADDRESS]`, `--open-tailnet`, `--ask-key`, `--shared`, `--single`, `--access-key-file`, `--remove`) it asks nothing. An existing file is shown as a diff and written only on a yes or `--yes`; `--dry-run` writes nothing; `--show` prints what is set up. A key is never a flag. |
| `studio start` | Starts the server in the background (`--foreground` to stay), prints the address and opens the browser (`--no-open`; never from a script, over SSH or without a display). Port: `--port`, else the configured one, else 8200; when that is taken by another program, the next free block 200 ports further on, and it says so. When a studio already answers there it starts nothing and says so. Record in `<data>/studio.pid` (pid, port, address), log in `<data>/logs/studio.log`. Project ports follow the port (port + 1 … + 99). `--fake` uses the scripted agent. |
| `studio stop` | Stops the studio that `studio start` started, after checking that the pid is a studio. One started another way is left alone. |
| `studio status` | Running or not, the address, pid, log, data folder, config file. `--json`. Exit 3 when not running. |
| `studio doctor` | What is installed (Python, uv, Node, git, the built interface, the agent program), each model account and why it does not work, whether there is a browser for looking, ffmpeg, LibreOffice, voice, the port, addresses and access, Tailscale, the data folder, the config file, where keys are kept, version and commit. `--online` asks an Anthropic key's or a gateway's provider for its model list (free). `--json`. Exit 1 when something needed is missing. Changes nothing. |
| `studio access-key` | Prints the access key. |
| `studio login` | Runs the agent program's own sign-in for a Claude plan. Refused in a shared studio. |

## The installer

`studio2/install.sh`, from a clone, on macOS and Linux (x86-64 and ARM64). In order: git (if
missing: the exact command, asked before it is run); uv (official installer, into
`studio2/.tools/uv`, no shell profile touched) and through it Python 3.12 and the packages in
`studio2/.venv` (the exact versions in `server-requirements.txt`, or `server-dev-requirements.txt`
with `--dev`, installed with `--require-hashes`, then the studio itself with `--no-deps`); Node
20.19 or later in 20.x or 22.12 or later, the range the locked Vite declares (the one on PATH,
else `studio2/.tools/node` from the nodejs.org tarball, sha256 checked against values written in the script); the interface build;
`~/.local/bin/studio` (`STUDIO_BIN_DIR`); a report of the optional parts. A browser for looking
at pages is downloaded only on a yes (default no) or `--with-browser`, and never when one is
already cached. `--yes` asks nothing and takes the defaults (no sudo, no browser, no setup).
The private Node is first in `attach.NODE_HOMES`, so attached apps and ship commands find it.


# Any model (step 3n)

Keys of four other providers are model accounts of their own, the studio runs the LiteLLM that
translates for them, every model can be checked with a fixed request, and wherever a model
that is not Claude's is chosen the studio says what that means.

## The four accounts

Setup (`studio setup`, the first-run screen, `/setup`) offers them beside the others; each
becomes a connection of kind `litellm`. The key goes through the key store like every other
(`api_key`, then named by `api_key_secret`; or `api_key_env`).

| `account` | `provider` | Besides the key and `models` | LiteLLM model |
|---|---|---|---|
| `openai` | `openai` | optional `base_url`: an OpenAI-compatible service instead of OpenAI itself | `openai/<model>`; with `base_url`, `custom_openai/<model>` |
| `openrouter` | `openrouter` | | `openrouter/<model>` |
| `azure-openai` | `azure-openai` | `base_url` (the resource's address) and `api_version`, both required; `models` are deployment names | `azure/<deployment>` |
| `gemini` | `gemini` | | `gemini/<model>` |

```json
{"id": "openrouter", "kind": "litellm", "provider": "openrouter", "label": "OpenRouter key",
 "models": ["openai/gpt-x", "openai/gpt-x-mini"], "default_model": "openai/gpt-x",
 "small_model": "openai/gpt-x-mini", "api_key_secret": "openrouter"}
```

- `models` are the provider's own names. Where the key allows, the provider is asked for its
  list (`POST /api/setup/models`, and the terminal does the same): OpenAI and an
  OpenAI-compatible address (`GET …/models`), OpenRouter (public, with prices), Gemini (key in
  a header, never in the address). Azure has no list a key can read, so deployments are typed.
  A typed name the list does not contain is allowed after a confirmation. A gateway the person
  runs (`anthropic-gateway`, usually LiteLLM) is asked too: `GET <base_url>/v1/models` with the
  key as `Authorization: Bearer` (20 s, the key never in a message); when it is unreachable,
  refuses the key or sends no list, the reason is said and names are typed instead.
- A list is never written whole when it is long. Up to 12 models are offered as they are; above
  12, setup offers a suggestion (`gateway.suggest_models`): ids containing, in this order,
  `claude-opus`, `claude-sonnet`, `gpt-`, then `claude-haiku` (a cheap model for the studio's
  background picks); Gemini and other families are not suggested and can be searched for; the two newest of each
  family by natural sort, at most 8, only ids the list holds. The terminal and the screen say
  "412 models found; 8 suggested". The person accepts or edits it: the terminal takes names or
  numbers and `/word` to search; the screen has a filter box with checkboxes and the picked
  models as chips. Only the picked models are written; the first is `default_model` unless
  another is chosen. Names the person gives (`models`, `--models a,b`) win over the suggestion;
  names the fetched list lacks are reported and used only after a confirmation.
- `small_model` is the model for background work (titles, summaries). Setup asks for it; left
  empty it is the cheapest of the offered models (by the provider's price when the list gave
  prices, otherwise by the words providers use for their small models: free, nano, lite, mini,
  flash, haiku, small, fast); when nothing stands out, each request's own model does it.
- Optional, on `litellm` and on `anthropic-gateway`, all off unless set: `disable_betas: true`
  (`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`, for a provider that rejects Anthropic's beta
  request fields; it also turns off the agent program's tool search), `max_context_tokens`,
  `max_output_tokens` (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`).
- `accounts[].group` is `claude` or `other`; `other` are the four here and the gateway.
  `Setup.other_models` is the wording: `headline` ("Claude models work best with the studio;
  other models are experimental.") and four `limits` (Anthropic does not support the route; the
  built-in web search does not work on it and the studio's own does; no prompt caching, so it
  can cost more; how well edits land depends on the model). It is shown in setup, in the
  terminal, in the Library and in the model menu of the chat.

## A model that cannot take pictures

Some models take text only. The agent sees its page by opening the screenshot from
`look_at_page` with its Read tool, and the picture then goes to the model with the next request:
a text-only model's provider refuses that request (OpenRouter: 404, "No endpoints found that
support image input") and the turn fails after the work is done.

- **Which models.** The price list (`library/model-prices.json`) carries `"images": false` for a
  model whose listing says it takes no pictures (OpenRouter's `architecture.input_modalities`
  without `image`); `tools/prices/update.py` writes it. A connection may name more in
  `text_only_models` (a list of model names as the connection spells them). Only the two
  translated kinds (`anthropic-gateway`, `litellm`) are looked at: every Claude model takes
  pictures. A model the list does not know is taken to see pictures, as before.
- **What changes for such a model.** The agent's Read of a picture file (`.png`, `.jpg`, `.jpeg`,
  `.gif`, `.webp`, `.bmp`, `.tif`, `.tiff`, `.heic`, `.avif`, `.ico`) is refused before it
  runs, with the reason and what to use instead: `layout` in the result of `look_at_page` (what
  the studio measured in the browser) and `check_page`. Every other file is read as before, and
  `look_at_page`, `render_image` and the like still run and save their files.
- **What the person is told.** One `notice` (`info`) when the request starts: the model cannot
  take pictures, so the assistant checks the page by measurements and cannot see screenshots or
  pictures attached to the message.
- **Not covered:** a connector's own tool that answers with a picture, and a PDF, which the
  agent program also sends as a document.

## Web search

`search_web` reads two public results pages that have no contract, in this order: DuckDuckGo's
HTML page, then Yahoo's. Rules (`search.py`):

- One search at a time for the whole studio, so helpers that search together wait in line; at
  least 1.5 seconds between two requests to the same service.
- A refusal (any status but 200 below 500, or a challenge page) is asked again once after 2
  seconds; a service that refused twice is left alone for 90 seconds and the other one answers
  meanwhile. A resting service is still asked last, once, before giving up.
- A server error or no connection is asked again straight away, up to three times, 0.7 seconds
  apart, and is no reason to rest (Yahoo answered HTTP 500 to about one request in three on
  12 November 2026 and answered the repeat).
- The same query in the same turn of a project is answered from memory (`cached: true`);
  answers are kept 30 minutes at most.
- No paid key is needed. A third public service was looked for and not added: Bing answered but
  with unrelated results for some queries, and Brave, Mojeek, Startpage, Ecosia and Qwant refused or
  sent a challenge from this machine.
- **A search service with a key comes first when the person has one** (see "Keys for other
  services"). Today that is Tavily: `POST https://api.tavily.com/search` with
  `Authorization: Bearer <key>` and `{query, max_results}`; each of its `results` (`title`, `url`,
  `content`) becomes a result with `content` as the snippet, and `via` is `tavily`. When it cannot
  answer (401: the key is refused; 432 or 433: the plan's limit is used up; anything else), the
  public pages answer instead and the result carries `note` saying why. A refused key or a used-up
  limit rests Tavily for ten minutes, so a bad key costs one request, not one per search.
- **A connector that searches comes before both.** When a connector attached to the request is
  a search service (its name holds `tavily`, `brave`, `exa`, `perplexity`, `serper`, `firecrawl`,
  `kagi` or `searx`), the project facts tell the agent to search with that connector's tools first
  and to keep `search_web` as the fallback. The person makes a connector the default by choosing
  "always" for it, for a project or for the machine, in the Library.

## The LiteLLM the studio runs

One LiteLLM proxy per studio, for all `litellm` connections (`gateway.py`).

- **Installed only on a yes.** `studio litellm install` (asks; `--yes`), the installer's
  question (default no; `--with-litellm`), `studio setup` when one of the four is chosen, and
  `POST /api/litellm/install` (a button; local requests only in a shared studio). Until then
  such a connection is listed `available: false` with the command.
- **One exact release, checked by hash.** `studio2/litellm-requirements.txt` pins LiteLLM
  1.102.1 and all 115 packages it needs, each with its sha256 hashes for macOS and Linux, and
  is installed with `uv pip install --require-hashes` into `studio2/.tools/litellm/venv`
  (`STUDIO2_LITELLM_HOME` moves it). A package whose bytes are not in the list stops the
  install. `installed.json` beside it records the hash of the list it was installed from; a
  changed list asks for a reinstall. About 430 MB.
- **Config without keys.** `<data>/litellm/config.yaml` is written from the connections at
  every start: one entry per model named `<connection id>/<model>`, each key as
  `os.environ/STUDIO_KEY_<ID>`, the master key as `os.environ/LITELLM_MASTER_KEY`,
  `drop_params` and `modify_params` on, telemetry off. Keys are read from the key store when
  the process starts and passed in its environment, which otherwise holds only PATH, HOME, the
  locale, TMPDIR and proxy and certificate settings.
- **This computer only.** `--host 127.0.0.1` on a port the system hands out; its master key is
  random, made once and kept in the key store (`studio2-litellm` / `master-key`). The agent
  program gets that key, never the provider's.
- **Started on need, stopped with the studio.** The first request on a `litellm` connection
  starts it (about 5 seconds) and waits for `GET /health/liveliness`, which needs no key and
  calls no model. It is started again when the connections or a key changed (a request then
  running on it fails), when it has died, or when it stops answering; the request that finds
  it dead gets a `notice` saying so. The studio stops it when it stops; one left behind by a
  studio that was killed (`<data>/litellm/state.json`) is stopped before a new one starts.
- **Its log** is `<data>/litellm/litellm.log`: everything it prints, with every key it was
  given and its master key replaced by `[key]`, rotated at 2 MB (one older file is kept).
- **The agent program** is started as for a gateway, at the proxy's address, asking for
  `<connection id>/<model>`, with every slot mapped: `ANTHROPIC_DEFAULT_OPUS_MODEL`,
  `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_FABLE_MODEL` and
  `CLAUDE_CODE_SUBAGENT_MODEL` to the request's model; `ANTHROPIC_DEFAULT_HAIKU_MODEL` and
  `ANTHROPIC_SMALL_FAST_MODEL` (the older name of the same slot) to `small_model`. A gateway
  the person runs (`anthropic-gateway`) is started exactly as before.
- **Cost.** A small module beside the config (`studio_spend.py`, loaded by LiteLLM as a
  callback) appends what LiteLLM priced each request at to `<data>/litellm/spend.jsonl`: the
  number, the model, token counts and the turn id the agent program sends in an
  `x-studio-turn` header. No key and no text. When the sum for a turn is above zero the turn's
  `usage.cost_usd` is that sum, a charge (`cost_estimated` false). When LiteLLM has no price
  for the model the turn keeps `cost_usd: null`, which the interface shows as priced by the
  gateway.

The interface: setup lists the accounts in two groups (Claude, and "Other models" marked
Experimental), shows the note and, when LiteLLM is not installed, an Install button; the
Library has a Models block (every connection and model, the check's result and button, the
state of the studio's LiteLLM); the chat's model menu marks such models `experimental` and
carries the headline under the connection's name.

`GET /api/litellm` → `{installed, version, pinned, needed, connections:[id], state:
not_installed|idle|running|stopped, detail, port, pid, started_at, last_exit: {code, at,
tail}|null, log, install_command, size, installing, install_error, install_log}`.
`studio litellm` prints the same from outside the studio; `studio doctor` has a section for
it, and with `--online` asks each of these accounts' providers for its model list.

## Check this model

`POST /api/models/check {connection, model, confirm_cost?}` → 202 and the running entry. A
fixed request is run through the real agent program, the turn runner and the studio's tools on
a scratch project, which is hidden from `GET /api/projects` (`hidden: true`) and deleted when
the check ends. One check runs at a time (409 `check_running`). A model that is not known to
be free answers 400 `cost_not_confirmed` with the sentence to show, until `confirm_cost` is
true. `POST /api/models/check/stop` stops it.

The scratch project holds `index.html`, `styles.css` and `data/harvest.csv`. Two messages:

1. Read both files in one step; change two things in `index.html` and one in `styles.css`, as
   edits in place; call the studio's `query_files` and write the number it returns to
   `total.txt`; remember a code word that is in no file.
2. Add the code word as the second line of `total.txt`.

Five parts are verified from the files and the event log, never from what the model says:

| Part | Passes when |
|---|---|
| Both messages finished | both turns ended `completed` (each has 7 minutes, `STUDIO2_CHECK_SECONDS`) |
| Two tool calls in one step | two tool calls were open at the same moment in the first turn |
| Edits in place in two files | both files hold exactly the asked-for changes and each was changed with `Edit` or `MultiEdit`, not written again whole |
| A studio tool call | `mcp__studio__query_files` answered and `total.txt` starts with its number |
| The second message continued the conversation | the code word is the second line of `total.txt` |

A command that would ask the person is answered no and noted. The result is kept in
`<data>/model-checks.json` under `<connection>::<model>` and replaced by the next run:

`{connection, model, status: passed|failed|stopped, reason, parts: [{id, label, ok, detail}],
seconds, steps, messages: [{status, error, seconds, input_tokens, output_tokens, cost_usd,
cost_estimated}], input_tokens, output_tokens, cost_usd, cost_estimated, notes, trace:
[{message, name, title, status}], said, at, check_version, target: {kind, provider, base_url},
studio_version}`. `steps` is the number of tool calls; `reason` is the first part that failed,
in a sentence. A result is shown only while the connection still points where it did (`target`).

`GET /api/models` → `{connections: [Connection + {provider, small_model, models: [{id, label,
experimental?, cost: free|plan|paid|unknown, warning, check: result|null}]}], running:
{connection, model, started_at, phase}|null, parts: [label], check_version, note: {headline,
limits}, litellm: (as `GET /api/litellm`)}`. `cost`: `plan` on the Claude plan; `free` for a
name ending `:free` or `/free`, a name in the connection's `free_models`, and the scripted
agent; `unknown` on a gateway the person runs and on an OpenAI-compatible address (they price
their own models; names starting `openrouter/` count as `paid`); otherwise `paid`.

`POST /api/setup/models {account, api_key?, base_url?, id?}` → `{models: [{id, price?}],
total, suggested: [id], suggested_small_model}` (`account` is a provider key or `gateway`;
`suggested` is the whole list when `total` ≤ 12, else the short list above; `price` in dollars per million input tokens, cheapest first), or 400
`no_model_list` with the reason. The key typed is used for that one request and not kept;
without one, the key already saved for the account is used, and only at the address saved with
it: a `base_url` that differs from it (a trailing `/` aside) is 400 `key_needed` ("type the key
for this address"), and none is sent.

## Choices made in this step

- LiteLLM 1.102.1 and not the newest: it is the release that ran whole requests of the agent
  program on this machine, and it has been public for seven weeks.
- One proxy for all `litellm` connections, with models named after their connection, instead
  of one proxy per connection.
- An `openai` account with its own address is sent as chat completions (`custom_openai/`):
  LiteLLM 1.102 sends `openai/` models to OpenAI's Responses API, which OpenAI-compatible
  services rarely have.
- The slot mapping is applied to `litellm` connections only; a gateway the person runs keeps
  its own routing, with `small_model` as before.
- The check counts an edit as in place only when the edit tool was used; right content written
  again whole fails that part.
- The cheapest model is a suggestion made from prices or names; setup asks.
- The proxy uses LiteLLM's bundled price list (`LITELLM_LOCAL_MODEL_COST_MAP`), so starting it
  fetches nothing from the network.

---

# Keys for other services

One standard for every credential the studio holds, whatever it is for: a model key, the access
key, a database password, a connector's secret, and now the key of a service the studio itself
calls (a search service today).

- **One store.** A secret is kept in the operating system's keychain, or in a file only its owner
  can read (`~/.config/studio2/secrets.json`, mode 600) where there is no keychain (`keystore.py`).
  It is never in `config.json`, the repository, a project, a log or an API answer; the config
  names a secret, it does not hold it.
- **The service's own variable wins.** A service key is read from the environment variable the
  service itself documents (`TAVILY_API_KEY`) when that is set, and from the store otherwise, so a
  machine that already has the variable needs no setup. The answer says which (`source`).
- **Listed, never shown.** `GET /api/services` → `[{name, label, used_for, env, site, configured,
  source: "env" | "store" | null}]`. `PUT /api/services/{name}` with `{key}` saves it in the store
  (400 `invalid_key` for an empty one, 404 `unknown_service`); `DELETE /api/services/{name}`
  removes it from the store (a key in the environment is not the studio's to remove, and the
  answer still says `source: "env"`). No route returns a key.
- **The same from the terminal:** `studio keys` lists them, `studio keys set <name>` asks for the
  key without echoing it (or `--key-file FILE`), `studio keys remove <name>`.
- **Shared studios.** These routes change the machine's settings: like setup, they are for the
  owner on this computer.

The Library page has a **Keys for other services** block: each service with what it is used for,
whether a key is there and where from, a field to save one and a button to remove it.

