# Studio 2 — API and event contract (Step 1)

Server and web UI are built against this file. Change it first, then both sides.

The studio was built in numbered steps, and some sections are headed with the step that added
them (Step 1 is this first part, then Step 2, 2b, 3 and 3k to 3o, in that order). A step name only
says when a section was added; each section is complete in itself. Step 4, user accounts and a
container per project, is not built yet.

All JSON. All timestamps are ISO-8601 UTC strings. Errors are
`{"error": {"code": "<snake_case>", "message": "<plain sentence>"}}` with a 4xx/5xx status.

## Processes and ports

- Studio server: `127.0.0.1:8200` (another port when setup or `studio start` chose one; see "Install and first run"). Serves `/api/*` and the built web UI (`studio2/web/dist`) at `/`.
- Project previews: one port per project from `8201–8299`, served by the same process.
- Web dev server (Vite): `127.0.0.1:5200`, proxies `/api` to 8200.
- Mutating `/api` requests with an `Origin` header other than the studio's (8200 or 5200) get 403. Requests without an `Origin` (curl, tests) are allowed.
- Any non-API path that is not a built asset returns the UI's `index.html`, so `/p/<id>` deep links work.

## Data layout

`STUDIO2_DATA` (default `<repo>/data/studio2`):

```
projects/<id>/
  project.json    project record (below)
  events.jsonl    append-only event log, one JSON object per line
  workspace/      the project: a git repo and the agent's working directory
```

`<id>` is `p_` + 8 lowercase hex characters.

The studio's own git commands on a project (versions, restore, diffs, bringing a copy up to
date) are held against a project's `.git/config` and `.gitattributes`, which the agent can
write. What is held, and how:

- Settings that name a program are fixed on the command line, where they outrank the
  repository: `core.hooksPath=/dev/null` (no hook of the project runs), `core.fsmonitor`,
  `core.sshCommand`, `core.pager`, `core.editor`, `sequence.editor`, `core.askPass`,
  `credential.helper`, `core.alternateRefsCommand`, `gpg.program` (with signature checks off),
  `merge.default`, submodule recursion off.
- Programs a project names under a name of its own (`filter.<x>.clean/smudge/process`,
  `diff.<x>.textconv/command`, `merge.<x>.driver`) run only when an attribute selects them.
  Before every command the studio writes the git directory's `info/attributes` (which outranks
  every `.gitattributes`) as `* !filter !diff !merge`, replacing whatever is there and never
  writing through a link. Every git version honours that file. Diffs also pass
  `--no-ext-diff --no-textconv`, so `diff.external` is not used. A side effect: Git LFS is a
  filter, so a safe copy holds each LFS file as the small pointer file it is in the repository
  (the large file is not fetched or stored, and the copy's app does not have it; inspection says
  so before the copy is made, see Inspect `lfs`). Filling a copy with the real files is not
  built. An attribute that names a custom diff or merge program has no effect there. `text`
  and `eol` attributes still apply.
- Only local paths are read: every transport but `file` is refused, so a `url.<x>.insteadOf`
  cannot turn the original's path into `ext::` (a command) or a network address. The fetch from
  the original names its reading program (`--upload-pack=git-upload-pack`).
- Git gets a short environment (`PATH`, `HOME`, the locale, `TMPDIR`, `TZ`) and no global or
  system config, never the studio's keys.

Not held: an alias the project defines is never used (the studio calls git's own commands,
which aliases cannot replace); a program on `PATH` that the agent replaces is the agent acting
as this user, which only containers stop (Step 4). Git commands on the person's original folder
(ship, inspect) run with his own configuration on purpose, as "Ship: make it live" says.

Nothing the studio writes into a project from a template or an example ever lands in its
`.git`, however the name is spelt (`.GIT`, `.Git`, or with code points a Mac disk ignores).

## Project record

```json
{
  "id": "p_1a2b3c4d",
  "name": "Untitled",
  "created_at": "...", "updated_at": "...",
  "port": 8201,
  "preview_url": "http://127.0.0.1:8201/",
  "connection": "subscription",
  "model": null,
  "session_id": null,
  "running_turn": null,
  "head": "<git sha>",
  "name_suggestion": null
}
```

`running_turn` is the active turn id or null. `session_id` is the harness session to
resume. `connection`/`model` are the project's last used choice. The studio never renames a project.
A project still named `Untitled` when a turn completes is **offered** a name once, with no model
call: `name_suggestion` holds the `<title>` of `index.html` when the agent gave it one, else the
first eight words of the request (60 characters at most, `…` when cut); it is null when nothing
is on offer. The person takes it with the ordinary rename (`PATCH {name}` with that text) or
drops it (`PATCH {"name_suggestion": null}`; any other value is a 400). Either one, and a name
typed by hand, ends the offer, and a project is offered a name only once: a dropped offer does
not come back after later turns. The interface shows the offer as a small bar under the top bar
("Name this page …?", Use it, a cross) while the name is `Untitled` and the page is in Browse.

## REST

| Method and path | Body | Result |
|---|---|---|
| `GET /api/health` | | `{ok, version, fake, connections:[Connection]}` |
| `GET /api/projects` | | `[Project]`, newest first |
| `POST /api/projects` | `{name?}` | `Project` (201). Workspace gets a blank `index.html` and an initial commit |
| `GET /api/projects/{id}` | | `Project` |
| `PATCH /api/projects/{id}` | `{name?, connection?, model?, name_suggestion?: null}` | `Project`. `name_suggestion: null` drops the offered name (above) |
| `DELETE /api/projects/{id}` | | 204. Moves the folder to `projects/.trash/`. From the moment it is called the project's queue is emptied (`turn_unqueued`, `cancelled`) and a new turn, queued message, ship or app start for it is refused (404 `project_not_found`); a running turn is stopped and waited for first |
| `GET /api/projects/{id}/files` | | `[{path, size, modified_at}]`, excludes `.git`, `node_modules`, `__pycache__`, `.venv` |
| `GET /api/projects/{id}/file?path=` | | `{path, content, hash}` for UTF-8 text up to 1 MB; 415 otherwise |
| `PUT /api/projects/{id}/file` | `{path, content, base_hash?}` | `{path, hash, checkpoint}`. 409 `stale_file` if `base_hash` no longer matches. Creates a `user` checkpoint |
| `POST /api/projects/{id}/upload` | multipart `file`, optional `dir` | `{path, size, checkpoint}`; 25 MB cap |
| `POST /api/projects/{id}/dom-edits` | `{path, tag?, before_text, after_text}` | `{path, checkpoint}`. Request `path` is the page's URL path (`/` → `index.html`); response `path` is the workspace file. With `tag`, the server first looks for the one `<tag>` element whose whole content is `before_text`; otherwise the text must occur exactly once in the file (raw, HTML-escaped, or with different whitespace). Else 409 `ambiguous_edit` or `edit_not_found`. The new text is HTML-escaped |
| `GET /api/projects/{id}/versions` | | `[{sha, kind, message, created_at, files:[{path, status}]}]`, newest first. `kind` is `init`, `agent`, `user` or `restore` |
| `GET /api/projects/{id}/versions/{sha}/diff` | | `{sha, diff, truncated}`; unified diff capped at 200 KB |
| `POST /api/projects/{id}/restore` | `{sha}` | `{checkpoint}`. New commit whose tree equals `sha`. 409 `turn_running` during a turn |
| `POST /api/projects/{id}/turns` | `{text, selection?, connection?, model?}` | 202 `{turn_id}`. 409 `turn_running` if one is active |
| `POST /api/projects/{id}/turns/{turn_id}/stop` | | 202. Idempotent |
| `GET /api/projects/{id}/events?after=<seq>` | | Server-sent events (below) |

`path` values are workspace-relative, forward slashes, no `..`, never inside `.git`.

### Connection

```json
{"id": "subscription", "label": "Claude subscription", "kind": "claude-subscription",
 "available": true, "reason": null, "models": [{"id": "opus", "label": "Opus"}], "default_model": "opus"}
```

Kinds: `claude-subscription`, `anthropic-api`, `anthropic-gateway` (LiteLLM or any
Anthropic-compatible gateway the person runs), `litellm` (another provider's key, through a
LiteLLM the studio runs itself; see "Any model"), `bedrock`, `vertex`, `foundry`, `fake`. Connections come from `~/.config/studio2/config.json` (or
`STUDIO2_CONFIG`); with no file there is one `subscription` connection. Under
`STUDIO2_FAKE=1` the only connection is `fake`.

```json
{"connections": [
  {"id": "subscription", "kind": "claude-subscription", "models": ["opus", "sonnet"]},
  {"id": "litellm", "label": "LiteLLM", "kind": "anthropic-gateway",
   "base_url": "http://127.0.0.1:4000", "api_key_env": "LITELLM_API_KEY",
   "models": ["claude-opus", "gpt-sol", "kimi-k3"]}
]}
```

Keys are read from the named environment variable at run time, or from the secret store when
the connection names one (`api_key_secret`, written by setup; see "Install and first run"),
and are never returned by the API or written to events. In a shared studio a
`claude-subscription` connection is listed as unavailable, with the reason.

What each kind needs (all run the same agent program; only its environment differs):

| Kind | Fields | The agent program is started with |
|---|---|---|
| `claude-subscription` | none | the Claude login on this machine; every key, gateway and cloud setting removed |
| `anthropic-api` | `api_key_env` | `ANTHROPIC_API_KEY` (sent as `X-Api-Key`) |
| `anthropic-gateway` | `base_url`, `api_key_env`; optional `small_model`, `thinking` | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` (bearer) |
| `litellm` | `provider`, a key, `models`; optional `small_model`, `base_url`, `api_version` | the address and own key of the studio's LiteLLM, and every model slot mapped (see "Any model") |
| `bedrock` | `region` | `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION`, and AWS's own credentials from the studio's environment |
| `vertex` | `region`, `project` | `CLAUDE_CODE_USE_VERTEX=1`, `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` |
| `foundry` | `resource` or `base_url`; `api_key_env` (else the machine's Entra sign-in) | `CLAUDE_CODE_USE_FOUNDRY=1`, `ANTHROPIC_FOUNDRY_RESOURCE` or `_BASE_URL`, `ANTHROPIC_FOUNDRY_API_KEY` |

Any kind may carry `model_ids`, which pins the short names to the provider's own ids:
`{"model_ids": {"sonnet": "apac.anthropic.…", "haiku": "…"}}`. A connection never falls through
to another: whatever names a different endpoint is removed from what the agent program inherits
(only from that: while it starts, another project's turn, health, setup and the plan's sign-in
check still read the studio's own environment whole). That includes each cloud's own names (`ANTHROPIC_FOUNDRY_API_KEY`, `_AUTH_TOKEN`, `_BASE_URL`,
`_RESOURCE`, `ANTHROPIC_VERTEX_PROJECT_ID`, `ANTHROPIC_VERTEX_BASE_URL`, `ANTHROPIC_BEDROCK_BASE_URL`,
`AWS_BEARER_TOKEN_BEDROCK`): a turn sees them only when its connection is that cloud, and then
only the ones the connection sets or that cloud's sign-in reads. A private address for Bedrock
or Vertex (`ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_VERTEX_BASE_URL`) has no field in a
connection: set in the studio's own environment it reaches that cloud's turns and no other, and
so does `ANTHROPIC_FOUNDRY_API_KEY` for a Foundry connection that names no key of its own.
Any other inherited variable whose name starts with `ANTHROPIC_` or `CLAUDE_` and looks like a
secret (a key, a token, a password) is blanked for the agent like every other secret; one that
only counts tokens (`CLAUDE_CODE_MAX_OUTPUT_TOKENS`) is left.
On `anthropic-api` and the three clouds the turn's `cost_usd` is a charge (`cost_estimated`
false); on the subscription it is the API-price equivalent. Only `claude-subscription` and
`anthropic-gateway` have been run against a real model; the others are checked by tests of the
environment they build, not by a paid call.

Models other than Claude (OpenAI, OpenRouter, Azure OpenAI, Gemini) are reached through
something that translates for them: an `anthropic-gateway` the person runs, or a `litellm`
connection, for which the studio runs LiteLLM itself. It works: GPT models through LiteLLM have
run whole requests here, tool calls and edits included (last checked 12 November 2026).
Anthropic does not support that route for its agent program, and the built-in web search is
not available on it; the studio's `search_web` is. A model that is not Claude's on either kind
carries `experimental: true` in the Connection's `models`, and the Connection carries it too.

The agent's own process does not inherit them. Every variable in the studio's environment
whose name looks like a secret (`key`, `token`, `secret`, `passw`, `credential`) is blank in the
agent's shell, the access key and gateway keys included; a gateway turn gets its key only under
the name the agent program reads. `STUDIO2_AGENT_ENV=NAME,NAME` passes named variables through;
it does not pass a name that points the agent program at a model endpoint (those above).

### Selection (optional on a turn)

```json
{"path": "/", "selector": "main > section:nth-of-type(2) h2", "tag": "h2",
 "text": "Market share", "html": "<h2>Market share</h2>"}
```

`text` ≤ 500 characters, `html` ≤ 4,000 characters (the bridge truncates).

Optional `source` (≤ 600 characters): where the app's framework says the element comes from,
when the page is a development build that can tell. The bridge reads it from the element and
its ancestors: React component names (and file and line where React still keeps them), a Vue
component's file, Svelte's file and line, Astro's source attributes, an Angular component's
class, or a file and line stamped by a build plugin. Paths inside the project are given
relative to it. Absent for a plain page or a production build; the agent is told to prefer it
to a text search. In the bridge's `select` message the field is called `hint`, because
`source` there names the sender. For a page the project's own app renders, the agent is not
told a file name (there is none); for a page that is a file, it is.

## Events

`GET /api/projects/{id}/events?after=<seq>` replays every stored event with
`seq > after` (default 0), then streams live. Each SSE message is:

```
id: <seq>
data: <one event as JSON>
```

After 15 seconds with nothing to send, the server sends a keep-alive as a named event, which
pages can see (a comment line would never reach them):

```
event: ping
data: {}
```

It has no `id:` and no `seq`, and is not an event of the project. A page that has heard
nothing at all (no event, no ping) for 45 seconds takes the connection for dead (a half-open
socket after a laptop sleeps or a phone changes networks still reports "open"), closes it and
opens a new one with `after=<the last seq it has>`. The browser reconnects by itself with
`Last-Event-ID`; the server honours it the same as `after`.

Every event has `seq` (integer, starts at 1, per project), `ts`, `type`, and
`turn_id` (string or null).

| `type` | Extra fields | Meaning |
|---|---|---|
| `turn_started` | `text`, `selection`, `connection`, `model` | A user message was accepted |
| `message_chunk` | `message_id`, `text` | Assistant text; append chunks with the same `message_id`. Only the main agent's words: text and thinking of a helper agent are never sent, streamed or not (its tool calls are, with `parent_tool_id`) |
| `thought_chunk` | `message_id`, `text` | Model thinking, when the harness exposes it |
| `tool_call` | `tool_id`, `name`, `kind`, `title`, `input`, `parent_tool_id` | A tool started. `kind`: `read`, `edit`, `execute`, `search`, `fetch`, `agent`, `think`, `other`. `title` is a short human line ("Edit index.html", "Run python build.py"). `input` is the raw tool input, capped at 8 KB. `parent_tool_id` is set for calls made inside a sub-agent |
| `tool_result` | `tool_id`, `status`, `summary` | `status`: `ok` or `error`. `summary` capped at 4 KB |
| `plan` | `entries:[{content, status}]` | The agent's current to-do list; replaces the previous one. `status`: `pending`, `in_progress`, `completed` |
| `files_changed` | `paths` | Workspace files changed on disk (debounced ~300 ms). `turn_id` may be null |
| `checkpoint` | `sha`, `kind`, `message`, `files:[{path, status}]` | A git commit was created |
| `turn_finished` | `status`, `error`, `usage`, `session_id` | `status`: `completed`, `stopped`, `failed`. `usage`: `{input_tokens, output_tokens, cost_usd, duration_ms}` with nulls where unknown |
| `notice` | `level`, `message` | Out-of-band information for the user (`info`, `warning`, `error`) |
| `turn_cost` | `cost_usd`, `requests`, `unpriced`, `source`, `reason` | Written after `turn_finished` once what a gateway turn cost has been read back from the gateway (see "Cost per request and per project"). `turn_id` is the turn it prices |

Rules:
- Events are written to `events.jsonl` before they are sent, one whole line each.
- A last line cut short by a crash is cut off when the log is next opened (the cut bytes are kept
  in `events.jsonl.torn` beside it, replaced each time), so the next event starts on its own line
  and takes the number after the last whole event. A last event that is whole but lost only its
  newline is kept.
- A turn always ends with exactly one `turn_finished`, including after Stop, a crash
  or a server restart (startup closes orphaned turns as `failed` with
  `error: "The studio restarted during this turn."`).
- After a turn that changed files there is one `checkpoint` (kind `agent`) before
  `turn_finished`.
- A `tool_call` can be sent twice with the same `tool_id`: once as soon as the tool and
  its target are known (so a long file write is visible while the model is still
  producing it), and again with the complete `input`. The later event replaces the
  earlier one's `title`, `kind` and `input`; it does not add a second tool.
- A stopped or failed turn can leave a `tool_call` without a `tool_result`; clients
  close open tools at `turn_finished`.
- `files[].status` is `added`, `modified` or `deleted`. Checkpoint objects (in events
  and in write responses) are `{sha, kind, message, created_at, files}`; a write that
  changes nothing returns `checkpoint: null` and emits no event.
- File writes, uploads, inline edits and restore return 409 `turn_running` during a turn.
- The bridge remembers scroll position per page in `sessionStorage` and restores it
  after each reload.

## Preview and bridge

`http://127.0.0.1:<port>/…` serves files from the project's `workspace/`
(`/` → `index.html`; a missing `index.html` gets a small "empty project" page).
`.git` is never served, and neither is a file outside `workspace/`: a symbolic link is
followed and the file it ends at is checked again (inside the workspace, not inside `.git`),
whether the address names the file or its folder (`/docs/` → `docs/index.html`); otherwise 404.
The same holds for `/__studio/files/…`. Responses are `Cache-Control: no-store`. For `text/html`
responses the server injects `<script src="/__studio/bridge.js"></script>` before
`</body>` (or at the end). `/__studio/bridge.js` is served by the studio, not the workspace.

The bridge talks to the parent window with `postMessage`. Every message is
`{source: "studio-bridge", type, ...}` from the frame and
`{source: "studio-shell", type, ...}` from the shell. The shell accepts messages only
from its own iframe's `contentWindow` and the project's preview origin.

| Direction | `type` | Fields |
|---|---|---|
| frame → shell | `ready` | `path` (pathname + search + hash), `title` |
| shell → frame | `set-mode` | `mode`: `browse`, `select`, `edit` |
| frame → shell | `select` | a Selection object (above) |
| frame → shell | `text-edit` | `path`, `selector`, `tag`, `before_text`, `after_text` |
| frame → shell | `navigate` | `path` (pathname + search + hash; in-page navigation only, full loads send `ready`) |

Select mode: hover outlines the element under the pointer, click selects it and does
not trigger the page's own handlers. Edit mode: clicking a text element makes it
`contenteditable`; on blur, if the text changed, send `text-edit`. Browse mode: the
bridge does nothing. The shell reloads the iframe when `files_changed` arrives, and
re-sends the current mode after `ready`.

## Harness interface (server-internal)

```python
@dataclass
class TurnRequest:
    project_id: str
    turn_id: str
    workspace: Path
    prompt: str                 # user text plus selection / user-change context, already composed
    session_id: str | None      # resume when set
    connection: dict            # resolved connection config
    model: str | None

@dataclass
class TurnResult:
    status: str                 # completed | failed
    session_id: str | None
    error: str | None
    usage: dict

class Harness(Protocol):
    kind: str
    def describe(self, connection: dict) -> dict: ...          # → Connection
    async def run(self, request: TurnRequest, emit: Callable[[dict], None]) -> TurnResult: ...
```

`emit` takes an event without `seq`/`ts`/`turn_id`; the turn runner adds them. Stop is
`asyncio` task cancellation; the harness must terminate its child process and re-raise.
The turn runner, not the harness, emits `turn_started`, `checkpoint` and `turn_finished`.

---

# Step 2 additions

Everything above still holds unless a line here says otherwise.

## Ports

| Port | What |
|---|---|
| 8201–8299 | The project's URL (unchanged). Fixed for the life of the project: at start-up the studio waits up to 10 s for a busy port instead of moving the project |
| 8301–8399 | The project's own app, when it has one: project port + 100. Loopback only; people use the project URL, which proxies to it |

## `studio.json` — a project with its own back end

A file `studio.json` in the workspace root turns the project into an app:

```json
{"run": "uv run --with fastapi --with uvicorn uvicorn server:app --host 127.0.0.1 --port $PORT"}
```

- `run` (string, required) is started with `sh -c` in the workspace, in its own process
  group, with `PORT` (project port + 100), `HOST=127.0.0.1`, `STUDIO_PROJECT_ID`,
  `STUDIO_PROJECT_URL`, `STUDIO_ALLOWED_HOSTS=127.0.0.1,localhost` and `PYTHONUNBUFFERED=1`
  set. It must listen on `127.0.0.1:$PORT`.
- **The app answers only to the names in `STUDIO_ALLOWED_HOSTS`.** Its port is on `127.0.0.1`,
  so other devices never reach it, but any program on this machine can, including a web page
  open in a browser here: the page's site points its own name at `127.0.0.1` (DNS rebinding),
  and the browser then sends the app `Host: that-site:<port>` and lets the page read the
  answers. The studio cannot check names on a port it does not own, so the app does. The studio
  always reaches it as `Host: 127.0.0.1:$PORT` (see below), so a back end that refuses any other
  name loses nothing. The check applies only while `STUDIO_ALLOWED_HOSTS` is set, so the same
  code run outside the studio (an export, a ship) is not limited by it.
  - The studio's own templates do this: `fastapi-app` with Starlette's `TrustedHostMiddleware`
    (400), `whiteboard` in its request handler (421), and `vite-app` by leaving Vite's own
    `server.allowedHosts` at its default, which answers only to `localhost`, `*.localhost` and
    addresses (403). The template used to switch that check off.
  - The agent is told the same rule for every back end it writes, with the way to do it in the
    common frameworks, and that the name is compared without its port: the studio's `Host` is
    `127.0.0.1:$PORT`, so a check against the whole header would refuse the studio itself and every
    page would be an error. All three templates compare the name alone.
  - An app the person attached is their own code and the studio does not change it. It is
    protected only if its server already checks names. By default Vite (6.0.9 and 5.4.12 or
    later), Django (an empty `ALLOWED_HOSTS` with `DEBUG`) and Rails in development do; Next,
    Express, Flask, FastAPI/uvicorn and Python's `http.server` answer any name. A second process the app starts itself (a back
    end on its own port beside the front end) is not the studio's either: it listens wherever its
    own settings say.
- While the app is `running`, every request to the project URL (any method, plus
  WebSockets) is proxied to it. HTML responses get the bridge script injected, so Select
  and Edit keep working. `/__studio/*` is always answered by the studio.
- While the app is not running, the project URL shows a status page (state, last log
  lines) that reloads itself when the app comes up. It does not fall back to static files.
- The app is started: when the studio starts, when `studio.json` appears or its `run`
  changes (noticed while the project is open in a browser, at the end of a turn, or on
  request), after an agent turn that changed files, and on request. It is not restarted
  automatically after a crash.
- A project never gets a service worker, whether its pages are files or come from its app: a
  request for a worker script (the browser marks it `Service-Worker: script`) is answered with
  one that unregisters itself. A worker would fetch pages itself, so they would arrive without
  the bridge, and would serve old copies. Installable-app features (offline, push) therefore
  cannot be tried inside the studio.
- While a development server runs, what it writes into the project (`.next`, `.nuxt`,
  `.svelte-kit`, `.astro`, `.turbo`, `.vite`, `.angular`, caches) and anything the project's
  `.gitignore` ignores produce no `files_changed` events. A page from a `"restart": "never"`
  app carries `<meta name="studio-reload" content="off">`, so the studio does not reload it
  on top of the development server's own reload.
- `"restart": "never"` (optional) is for a development server that picks up file changes by
  itself (Vite, Next, Nuxt, `uvicorn --reload`): a running app is then left alone after a turn.
  It is still started when it is not running, and restarted when `run` changes or on request.
- A request from the project's own page reaches the app with the app's own origin
  (`http://127.0.0.1:$PORT`) and host, so a development server's origin and host checks pass
  from any device; an `Origin` from anywhere else is passed on unchanged. WebSockets carry the
  page's cookies, so a WebSocket whose `Origin` names another host or port than its `Host` (a
  page on another site) is refused before it is accepted and never reaches the app; one with no
  `Origin` (not a browser) is passed on. `X-Frame-Options` and the `frame-ancestors` part of a
  `Content-Security-Policy` are dropped from the app's answers, because the page is shown
  inside the studio.
- An app that builds absolute addresses from its `Host` (Starlette and FastAPI's trailing-slash
  redirect, `url_for`) answers with its own private address (`http://127.0.0.1:$PORT/…`, or
  `localhost`, `[::1]`, `0.0.0.0` at that port). In a `Location` or `Content-Location` header that
  origin is replaced with the scheme and `Host` the request came in on, so the browser stays on the
  project's address (on a phone 127.0.0.1 is the phone; on the Mac the app's own port has no
  bridge). Every other address is passed on as it is. A `Set-Cookie` whose `Domain` names the
  app's loopback host loses that attribute, so the cookie belongs to whatever host the browser used;
  `Secure` is left alone (the browser treats 127.0.0.1 and localhost as secure, and the tailnet
  address is HTTPS).
- The app gets `X-Forwarded-For` (the browser's address appended to any it sent). It does not get
  `X-Forwarded-Host`, `X-Forwarded-Proto` or `Forwarded`, and those sent by the browser are removed:
  the app sees its own origin and host (above), and these would contradict them. Next's Server
  Actions compare `Origin` with `X-Forwarded-Host` first, and Rails compares `Origin` with a base
  address that follows `X-Forwarded-Proto`, so either header would make them refuse the page's
  forms. Addresses an app writes into what it sends from its `Host` therefore name 127.0.0.1;
  the next bullet puts them right.
- **The app's own address in what it sends.** An app often writes its own address into a page,
  a script or JSON (`http://127.0.0.1:$PORT/api/…`, `ws://localhost:$PORT/…`). On a phone that
  address is the phone, and on this computer it is the app's own port: another origin than the
  page (a `fetch` there is refused unless the app allows it) and without the bridge. So on every
  request, by `127.0.0.1` on this computer as by the tailnet name or a name in `STUDIO2_NAMES`, the
  studio replaces that address in the answer with the one the request came in on:
  - What is replaced: exactly the app's own origin, `127.0.0.1`, `localhost` or `[::1]` (any
    case) at the app's port and not followed by another digit, after `http://`, `https://`,
    `ws://`, `wss://` or `//`, also written with escaped slashes (`http:\/\/…`, as some JSON
    writers do). It becomes the request's scheme and `Host` (`https://name:<page port>` over
    the certificate); `ws`/`wss` follow the request's scheme; `//` stays `//`. Nothing else: no
    other host, no other port, no address without `//` in front.
  - Where: bodies whose `Content-Type` is HTML, JavaScript, CSS, JSON (also `…+json`) or plain
    text, and the `Link` and `Refresh` headers. Never images, fonts, media, downloads,
    server-sent events (`text/event-stream`) or anything else; never a partial answer (206), a
    `HEAD`, or a WebSocket message.
  - How much: an answer with a `Content-Length` up to 8 MB is read whole, rewritten, and sent
    with its new `Content-Length`. A larger one is passed on untouched. An answer without a
    length (chunked, or one that never ends) is rewritten as it streams: each piece is sent at
    once, except the last few bytes when they could be the start of the address, which wait for
    the next piece (never more than the address is long, about 25 bytes), so a stream is never
    held. Plain text (`text/plain`) and JSON (`application/json`) without a length are the
    exception: they are passed on untouched, piece by piece, because a piece ending in `h`, `w`,
    `/` or `\` would otherwise wait for the next one, and a chat app streaming words would lag a
    character behind. Plain text and JSON with a length are rewritten. An answer over 256 KB is decoded and rewritten off the studio's
    main loop, so large scripts do not make other requests wait. Rewriting costs about 0.5 ms per megabyte, more where the address appears often (1.6 ms
    for a 140 KB page that names it 1,500 times).
  - Compression: the studio asks the app for an uncompressed answer (`Accept-Encoding:
    identity`). An app that compresses anyway with `gzip` or `deflate` and gives a length is
    decoded to look for the address, up to 8 MB decoded: past that the decoding stops and the
    answer is passed on untouched (a small compressed answer can hold a huge one). When the address
    is there the answer is sent rewritten and uncompressed. A compressed answer without a length, and any other encoding, is passed on
    untouched, with its `Content-Encoding`. The
    app's `ETag`, `Last-Modified`, `Expires` and `Cache-Control` are never passed on (the answer
    is `no-store`), so no cached copy can disagree with a rewritten one; a `Content-MD5` or
    `Digest` header is dropped from a rewritten answer.
  - A rewrite never makes an answer fail. An answer that says `gzip` or `deflate` and is not
    is passed on as the app sent it. An answer that ends before its `Content-Length` (the app
    closed early) is passed on as far as it came, with the app's headers, and the connection is
    then cut, as the app's was.
  - An answer that does not hold the address is passed on byte for byte as the app sent it, by
    any name and on every path (whole, streamed, compressed): same bytes, same `Content-Length`,
    same `Content-Encoding`. Only the headers above (`Cache-Control`, `ETag`, …) differ, as for
    every answer.
  - A value the app keeps as data and also sends (a callback address, a saved link) is shown with
    the address the page was opened by. If the person saves it back through a form, the app
    stores that address, not its private one.
  - It is always on; there is no setting. The address it replaces does not work as the page's
    own from any device, so there is nothing to keep, and the match is exact, so text that only looks
    like it is left alone. A page that deliberately shows its private address as text (the
    app's own help page, say) shows the project's address instead.
- stdout and stderr go to `projects/<id>/app.log` (truncated at each start, capped at
  5 MB) and to an in-memory tail of 2,000 lines.

### App object

```json
{"configured": true, "run": "…", "state": "running", "pid": 123, "port": 8301,
 "exit_code": null, "started_at": "…", "error": null, "last_line": 57, "slow": false,
 "not_answering": false}
```

`state`: `none` (no valid `studio.json`), `starting` (process alive, port not answering
yet), `running`, `stopped` (stopped on request), `crashed` (the process exited; see
`exit_code`). Only an exited process is `crashed`. An app that has not answered within 90 s
(a first start that downloads packages, a slow first compile) stays `starting` with
`slow: true`: the studio keeps checking every 2 s and it becomes `running` when its port
answers. A line in its output says so, and again each minute with how long it has waited and
to check that the app listens on `$PORT`; the page at the project address says the same. Five
minutes after the start, an app that is still alive and has never answered gets `not_answering:
true` and one plain line in its output: it is running but not answering on its port, most likely
listening on another port or not serving at all. It is still `starting` (it has not exited) and is
never stopped by the studio, since it may still be compiling; the studio keeps checking and it
becomes `running` if it answers. The project address then says it is running but not answering.
`slow` and `not_answering` are false in every other state. The agent is told to wait on `slow` for a
minute or two at most, then read the app's output and check the port, not to keep waiting. An
attached app's install that runs past its own limit is stopped like a Stop: SIGTERM to the app's
whole process group, then SIGKILL after 5 s; it is then `crashed` with its exit code.
`error` explains an invalid `studio.json`.

**An app that stops by itself is started again.** When an app that had been `running` for at
least 20 s exits without being asked to (a development server whose worker died, a native
library that aborted), the studio records `crashed` with its exit code and then starts it again
by itself: `starting`, then `running`. Its output keeps what the run that died said last,
followed by one line from the studio with the exit code, how long it had run and which restart
this is. At most 3 such restarts in 10 minutes. Past that, and for an app that exits before it
has been `running` for 20 s (a start that fails, a command that ends at once), it stays `crashed`
as before, and the last line says no further restart is tried. A Stop by the person, a restart by
the studio or the agent, and a server the studio did not start (`address`) are never restarted
this way. An exit code of 0 is no sign of a clean stop: `next dev` exits with 0 when the process
that serves the pages has died.
`Project` gains `app: {configured, state}`.

## REST additions

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/templates` | | `[{id, label, description, app}]`; `app` is true when the template has a back end |
| `POST /api/projects` | `{name?, template?}` | as before; unknown template → 400 `unknown_template`. Default template is `blank` |
| `PATCH /api/projects/{id}` | also `approvals?: "ask" \| "allow"` | `Project` now includes `approvals` (default `ask`) |
| `GET /api/projects/{id}/app` | | `App` |
| `POST /api/projects/{id}/app/start` \| `stop` \| `restart` | | `App` after the action was issued (state usually `starting` or `stopped`). 409 `no_app` when not configured |
| `GET /api/projects/{id}/app/logs?after=<n>` | | `{lines: [{n, text}], last}`; lines with `n > after`, at most 2,000, each capped at 2,000 characters. `n` restarts at 1 with each start; `last` smaller than the caller's `after` means the app restarted — ask again with `after=0` |
| `GET /api/projects/{id}/download` | | `application/zip` of the workspace (no `.git`, `node_modules`, `.venv`, `__pycache__`), attachment named after the project |
| `POST /api/projects/{id}/turns` | also `attachments?: [path]`, `queue?: bool` | `attachments` are workspace paths (upload them first, normally with `dir=attachments`); missing ones → 400 `missing_attachment`. During a running turn: without `queue` 409 `turn_running` as before; with `queue: true` 202 `{queued_id}` |
| `DELETE /api/projects/{id}/queue/{queued_id}` | | 204. Idempotent |
| `POST /api/projects/{id}/turns/{turn_id}/approvals/{approval_id}` | `{decision: "allow" \| "deny"}` | 204. 404 `unknown_approval` if it is not waiting |

Uploads are now allowed during a running turn (so files can be attached to a queued
message); they are committed with the turn's checkpoint instead of their own.

## Event additions

| `type` | Extra fields | Meaning |
|---|---|---|
| `turn_started` | also `attachments: [path]`, `queued_id` (string or null) | |
| `turn_queued` | `queued_id`, `text`, `selection`, `attachments`, `connection`, `model` | A message is waiting for the running turn to end. `turn_id` is null |
| `turn_unqueued` | `queued_id`, `reason`, `text` | It was removed without running. `reason`: `cancelled` (the person removed it, or the project is being deleted), `stopped` (Stop cleared it), `restart` (the studio restarted), `not_started` (its turn came but it could not start, for example because no connection is left to send it with). `text` is the message's text, so the thread can say what was not sent and offer it again. The thread says so for every reason except `cancelled`, and offers the text back for `stopped` and `not_started` |
| `approval_request` | `approval_id`, `tool`, `title`, `detail`, `reason` | The agent wants to do something risky and is waiting. `title` is a short line ("Run a shell command"), `detail` the command or path, `reason` why it needs approval |
| `approval_resolved` | `approval_id`, `decision`, `by` | `decision`: `allow`, `deny`. `by`: `user`, `turn_end` (the turn ended first: always `deny`) |
| `app_status` | `state`, `exit_code`, `run`, `slow`, `not_answering?` | The project's app changed state, or became `slow` or `not_answering` while `starting`. `turn_id` is the running turn or null |

- Queued messages run in order, each as its own turn, as soon as the turn before ends —
  also after a failed turn. Stop clears the messages that were waiting when it was pressed
  (`turn_unqueued`, `stopped`, with the text: the thread says each was not sent and offers to put
  it back in the message box). Putting it back gives the text only: the note names what does
  not come back with it (the selected element, each attached file). A message whose turn comes
  but cannot start is `not_started`, with the same note and the same offer; a message sent after the Stop ("stop, do this instead") is the
  person's to keep and runs when the stopped turn has ended. This holds for a Stop at any point.
- Stop counts for as long as the turn is running, including after the agent's part has ended
  while the studio is still finishing (saving the version, restarting the app, opening the page,
  the re-check). A Stop that arrives then no longer cancels anything: what the agent changed is
  still saved as a version (`checkpoint` before `turn_finished`) and the app is still restarted
  on it, while the queue is cleared as above, a ship the agent asked for is not begun, and the
  steps not yet started are skipped (the look at the page, the re-check, `next_steps`). A turn
  that had completed ends with `status: "stopped"` and a `notice` (`info`) saying the agent had
  already finished and what became of its changes (saved as a version; could not be saved; or it
  changed no files). A turn that had failed stays `failed` with its error.
- When the studio's own ending of a turn fails (a disk that is full, say) before `turn_finished`
  was written, it still writes one, `status: "failed"` with what went wrong, and the queue goes
  on as after any failed turn (not for a project being deleted).
- Every `approval_request` is followed by exactly one `approval_resolved` before the
  turn's `turn_finished`.
- `usage` gains `cost_estimated` (bool). On a subscription connection `cost_usd` is what
  the tokens would have cost at API prices, not a charge: `cost_estimated: true`. On a
  gateway connection `cost_usd` is null in `turn_finished` (the harness cannot know the
  gateway's price); a later `turn_cost` event carries what the gateway itself recorded
  (see "Cost per request and per project").

## Approvals

With `approvals: "ask"` the agent pauses before:
- a shell command that looks risky: `sudo`; installing software outside the project
  (`brew`, `apt`, `npm -g`, `pip install` outside a virtual environment, any browser
  download such as `playwright install`); deleting, moving or redirecting to paths outside
  the project; `git push`, `gh`; piping a download into a shell; process and service
  control (`kill`, `pkill`, `pm2`, `launchctl`, `crontab`); `ssh`/`scp`/`rsync` to another host; `docker`.
- a setting that makes git run a program or read other settings, in any scope (`git config`
  with `--local`, `--global`, `--file`/`-f`, or none): `core.fsmonitor`, `core.hooksPath`,
  `core.sshCommand`, `core.pager`, `core.editor`, `core.askPass`, `core.gitProxy`, `filter.*`,
  `diff.external`, `diff.*.textconv`, `diff.*.command`, `merge.*.driver`, `alias.*`,
  `credential.*`, `include.*`, `includeIf.*`, `gpg.*`, `url.*`, `protocol.*`; and anything that
  writes or redirects into the project's own `.git/` folder (`.git/config`, `.git/hooks/…`,
  `.git/info/attributes`). The reason says that this would let git run a program. Reading a
  setting, removing one, and ordinary settings (`user.name`, `pull.rebase`, …) do not ask.
- anything that would change the studio's own settings or state: the studio's command line when it
  changes something (`studio setup`, `keys`, `library add`/`remove`, `litellm …`, `access-key`,
  `login`, `stop`, `start`; also as `python -m studio2_server.cli …` or the virtual environment's
  `studio` by its path; `studio status`, `doctor`, `library` and `--help` only read and do not ask),
  and writing, moving, deleting or redirecting into `~/.config/studio2/`, `~/.claude/` (plugins,
  settings, skills) or the studio's data folder (outside the project's own folder; this also asks
  when the project's folder holds the data folder, as the studio's own repository brought in place
  does). The reason says that this would change the studio's own settings. Reading them does not ask.
- in a project edited in place (its folder is the person's own app, with their own repository),
  a `git` command that can discard or rewrite work there:
  - `checkout` of paths (`checkout -- <path>`, `checkout .`, `checkout <commit> <path>`, a single
    argument that is a file in the folder), `checkout -f`, `checkout -B`;
  - `switch -C`, `switch -f`/`--discard-changes`;
  - `restore` of the working tree (no `--staged`, or with `--worktree`/`-W`);
  - `reset --hard`/`--merge`/`--keep`, and a `reset` that moves the branch to another commit
    (`reset HEAD~1`, `reset --soft HEAD~2`);
  - `clean` (not `-n`); `stash drop`, `stash clear`, `stash pop`;
  - `branch -D`, `-f`/`--force`, `-M`, `-C`;
  - `rebase`, `commit --amend`, `push`, `pull` (not `--ff-only`), `merge`, `cherry-pick`, `revert`;
  - what removes the means of recovery: `reflog expire`/`delete`, `gc --prune`, `prune`, `tag -d`;
    and `filter-branch`, `filter-repo`, `update-ref`.

  These do not ask, because git itself refuses to lose anything in them, or they only read:
  `checkout -b`, `switch -c`, a plain `switch <branch>`/`checkout <branch>`, `restore --staged`,
  `stash`/`stash push`/`save`/`list`/`show`, `branch -d`/`-m`, `pull --ff-only`, a plain
  `reset` of the index (`reset`, `reset HEAD`, `reset -- <path>`: it only unstages; no commit
  moves and no file changes), `status`, `log`, `diff`, `show`, `ls-files`, `rev-parse`,
  `blame`, `branch`, `add`, `commit`, `tag`, `fetch`, `gc`. In a project of the studio's own all
  of these touch only the project's own history and do not ask (`push` always asks).
- nothing else. Reads, edits inside the project, ordinary commands and web access do not ask.

How paths are judged:
- Against the project folder, from where the command runs: a `cd` or `pushd` (and `popd`) earlier on
  the same line moves that, so `cd .. && rm -rf *` asks while `cd src && rm -rf build` does not.
  `cd` alone, `cd ~` and `cd $HOME` go to the home folder. A `cd` to a scratch folder (`/tmp`, …)
  makes relative paths there fine. A `cd` to a place that cannot be read (a variable the agent's shell
  does not have, one the line set to something unreadable, a trimmed path such as `cd ${PWD%/*}` or
  zsh's `cd $PWD:h`) leaves the command in an unknown folder: nothing relative counts as inside from
  there, so `cd $OUT && rm -rf *` asks. `cd -` goes back to the folder before the line's last `cd`;
  with no `cd` before it on the line, that folder is unknown.
- `git -C <dir>` (and `--git-dir`/`--work-tree`) out of the project, or `git` after a `cd` out of
  it, asks for anything but reading. git's own options before the subcommand (`-C`, `-c k=v`,
  `--git-dir=…`, `--work-tree=…`) are skipped when looking for `push` and the other subcommands.
- `find … -delete`, `find … -exec rm …` (also `rmdir`, `unlink`, `shred`) and `find … | xargs rm`
  delete what `find` finds: judged by `find`'s starting points.
- Every redirection that writes is read: `>`, `>>`, `>|`, a numbered one (`2>`, `2>>`, `3>`), both
  streams (`&>`, `&>>`), `>&` to a file, and `<>`/`1<>` (opens a file to read and write). `2>&1` and
  `>&2` join streams and write nothing. `ln` changes only its last argument (the link), not what it points at.
- A path that starts with `~`, `$HOME` or `${HOME}` is expanded to the home folder and judged by
  where it leads, so a path into an app that lives under the home folder is inside it.
- A download's target is a write: `curl -o`/`--output`/`--output-dir` (and `-O` into the current
  folder), `wget -O`/`--output-document`/`-P` (and its default, the current folder).
- git's own path arguments are writes: the folder of `clone` (and `--separate-git-dir`,
  `--template`), `worktree add <dir>`, `init <dir>`, `archive -o <file>`. `GIT_DIR=` and
  `GIT_WORK_TREE=` (for the command, or set earlier on the line) name where git works, like `-C`.
- A scratch folder is judged after `..` is resolved: `/tmp/..` and `$(mktemp -d)/..` are not
  scratch, and neither is a folder that holds the project.
- A variable the same line sets is followed into the words where it is used, and the line is judged
  as if that text had been typed there: `OUT=~/Desktop/site; rm -rf $OUT` asks like
  `rm -rf ~/Desktop/site`, and `x="rm -rf"; $x ~/x` is `rm -rf ~/x` (an unquoted value is split into
  words, as bash does). A `for` loop's words are its values, each judged. `${X:-dir}` and `${X:=dir}`
  have `dir` as a value, `${X%.md}`, `${X#…}`, `${X/a/b}`, `${X,,}` and `${X^^}` are worked out, and
  numbers (`$((i+1))`, `$(seq 1 5)`, `$RANDOM`, `$$`) are numbers. `eval X=…` sets `X` too. The
  studio's own names (`$PORT`, `$HOST`, `$STUDIO_…`) are not known any more when the line sets them.
- A `for` loop over a pattern (`for f in *.md`), or over a listing of the project (`$(git ls-files)`,
  `$(find . …)`, `$(ls src)`, `find . … | while read f`), stands for file names inside the project.
  A listing rooted elsewhere (`find ~`, `find ..`, `find -L`, `ls /`) or a file's contents
  (`$(cat files.txt)`) cannot be read. Because a file's name can hold `;`, these are never read as
  text to run (`sh -c "wc $f"` asks).
- A few substitutions are read for what they print: `$(pwd)`, `$(dirname X)`, `$(cd X && pwd)`,
  `$(realpath X)`, `$(mktemp …)` (a scratch folder), `$(which X)` and `$(command -v X)` (the program
  `X`), `$(git rev-parse --show-toplevel)`, the listings above, `$(npm root)`, `$(npm bin)` and
  `$(npm prefix)` without other words (with `-g` they are the machine's own), `$(echo … | tr …)`, and
  plain text from `date`, `basename`, `echo` and the like. `printf` with a format and `find -printf`
  print what their format says, so they cannot be read. `$0` is the agent's shell itself (`/bin/zsh`).
  So `rm -rf $(dirname $PWD)` asks. The rules that name programs are read on the line as typed too,
  so a substitution read for its output never hides one.
- A target of a writing or deleting command, or of a redirection, that the line sets to something
  that cannot be read (`OUT=$(cat t.txt)`, `read d`, `VERSION=$(jq -r .version package.json)`), that
  holds any other substitution, or that trims a path (`${PWD%/*}`, `$PWD:h`) asks. One in which a
  substitution or variable is followed by `..` asks.
- So does one that starts with a variable the line does not set, unless the agent's shell really has
  it: `$HOME`, `$PWD`, `$TMPDIR`, `$USER`, `$LOGNAME` and the numbers above. Each of the agent's
  commands runs in a fresh shell (the folder carries over, variables do not), so `rm -rf "$OUT"/*` is
  `rm -rf /*` there. `$PORT`, `$HOST` and `$STUDIO_…` belong to a project's back end, not to the agent's
  shell. A variable the line does not set further along a path (`dist/$NAME`) is not judged.
- The text of `trap '…' EXIT` and the command after `find … -exec` (with `{}` as find's starting
  points) are judged as commands.

The rules read the command, not a file's text: the body of a here-document that `cat` or `tee`
writes to a file (`cat > notes.py <<'EOF' … EOF`), or that `git` reads as a message
(`git commit -F - <<'EOF'`, `git tag -F -`, `git notes add -F -`), is skipped, while where it writes and
everything after it are still checked. A here-document handed to another language (`python3 - <<'EOF'`, `node`, …)
is a program, not shell: only the rules that name programs apply to it, not the checks on
redirections and file commands. That holds when the interpreter is started through a runner
(`uv run python - <<'EOF'`, `poetry run`, `pipenv run`, `env`, `npx`, …) and when the
here-document is piped into one (`cat <<'EOF' | python3 -`). Only a body fed to a shell (`sh`,
`bash`, `zsh`, …) is read as commands; any other reader's body gets only the rules that name
programs. Text inside quotes (`'…'`, `"…"`) is never read as a redirection or a command
separator: `python -c "print('<b>>~$30,000</b>')"` writes nothing.

Commands inside commands are judged by the same rules, as if they had been typed on their own:
- the text given to a shell with `-c` (`bash -c '…'`, `sh -c`, `zsh -lc`, also after `xargs`,
  `find -exec`, `timeout` and the like), to `eval`, or to a shell as a here-string
  (`bash <<< '…'`);
- the command after `env NAME=value`, `env -S` (also `-S'…'`, `-iS'…'`, `--split-string=…`), `sudo`, `nohup`, `time`, `nice`, `timeout`,
  `command`, `exec` and `setsid`;
- the command that `watch`, `script`, `tmux` (`new`, `new-window`, `split-window`, `respawn-pane`,
  `run-shell`, `send-keys`), `screen`, `parallel` and `xargs` run, and what `at` or `batch` read
  from a here-string or a pipe. Words that `echo` or `printf` pipe into `xargs` are read as its
  arguments (`echo ~/x | xargs rm -rf` is `rm -rf ~/x`);
- the commands inside `$( … )` and backticks, wherever they stand (also inside double quotes);
- text that `echo` or `printf` pipes into a shell (`echo 'rm -rf ~' | sh`).

The line is read the way the shell reads it first: a backslash at the end of a line joins it to the
next (`r\` + new line + `m` is `rm`), `$'…'` text is decoded (`$'\x72m'` is `rm`), and a command
after `(`, `{`, `!`, `if`, `then`, `else`, `do`, `while`, `until`, `coproc` or a `case` pattern is the
command itself (`(rm -rf ~/x)` and `for f in a; do rm -rf ~/x; done` ask like `rm -rf ~/x`), and so
is one after zsh's `repeat N`, `noglob`, `nocorrect`, `-`, `for i (…)`, `if … {`, `while … {` and an
anonymous function `() { … }`. `((…) )` is a subshell, not arithmetic. zsh's `=rm` is `rm`, `$"rm"` is
`"rm"`, and a program name in quotes (`"ssh" host`) is that program. For `bash -c -- '…'` the text
after `--` is the script.

The inner command is read from where the outer one runs, so `cd .. && bash -c 'rm -rf *'` asks
just as `cd .. && rm -rf *` does. This goes four levels deep; a command wrapped deeper than that asks.

When the inner text cannot be read before it runs, it asks:
- the shell's text, or `eval`'s, gets a command from a variable or a substitution that the outer
  shell fills in (`bash -c "$CMD"`, `bash -c "npm test && $NEXT"`, `eval "$(tool init)"`);
- the shell's text holds a variable that the same line sets (`X='; rm -rf ~'; bash -c "echo $X"`,
  also through `export`, `read` or `for X in`), or a substitution other than `$(pwd)`;
- the program a line runs is itself a variable or a substitution (`$CMD ~/x`, `$(tool) …`). When
  the line sets that variable only to plain text, the text is read in its place:
  `C=/usr/bin/chromium; "$C" --headless` is read as `/usr/bin/chromium --headless`, and
  `x=rm; $x -rf ~/x` as `rm -rf ~/x`. A program whose name is written out after a folder that comes
  from a variable (`"$VIRTUAL_ENV/bin/python"`, `$BIN/tsc`) is that program, read by its name, and so
  are `$(which node)`, `$(command -v python3)` and the conventional names `$PYTHON`, `$PYTHON3`,
  `$NODE`, `$NPM`, `$PIP` and `$UV` when the line does not set them;
- any other program's output is piped into a shell (`base64 -d … | sh`, `cat script | bash`);
- a shell reads a process substitution (`bash <(…)`, `source <(…)`, `. <(…)`); `source /dev/stdin`
  and `. /dev/stdin` read a here-string or a pipe like a shell does;
- code given to another language on the command line (`python -c`, `perl -e`, `ruby -e`,
  `node -e`, `php -r`, `awk '…'` and the like) or as a here-document (`python3 - <<'EOF'`)
  mentions running a program: `system(`, `subprocess`, `popen`, `exec`, `spawn(`,
  `child_process`, and on the command line backticks in Perl, Ruby or PHP and pipes in awk.
  `python -c "print(1)"` does not ask.

These are read as usual and do not ask: `$HOME`, `$PWD`, `$USER`, `$LOGNAME`, `$TMPDIR`, the
variables the studio gives a project's back end (`$PORT`, `$HOST`, `$STUDIO_…`), `${NAME:-default}`
forms of these, `$(pwd)`, and anything inside single quotes, which the outer shell does not fill in.
A variable that the line does not set and that stands where an argument goes
(`bash -c "npm test -- $ARGS"`) is read as an argument: it comes from the environment the agent was
started with, which its commands cannot change from one to the next.
Running a script file (`bash build.sh`, `python3 make.py`) is not looked inside.

These rules are text matching and a last check, not a wall. They catch the ordinary ways an agent
reaches outside its project, including commands wrapped in other commands, but a determined
attempt to hide a command (a script file written first and run later, a program in another
language that starts a shell) can get past them. What they still cannot read: a script file's
contents, code in another language that builds its call to a program without naming one of the
words above (`getattr(os, 'sys' + 'tem')`), shell functions and aliases defined and then called,
and what a variable from the agent's starting environment holds. A variable the line sets is read
only from its plain text, the few substitutions and `${…}` forms listed above; anything else it holds
is unknown, and asks only where it names a path that is changed or deleted, or the program itself.
Array elements, `${!X}`, `declare -n` and zsh's `${(…)X}` flags are not followed; where one names a
path to change, it asks. A value set by `source`/`.` of a file or by quoted `eval '…'` is not seen at all:
`X=dist; source vars.sh; rm -rf $X` is judged as `rm -rf dist`.

File writes outside the project stay refused outright. `approvals: "allow"` skips the
question (same behaviour as Step 1).

## Templates

`library/templates/<id>/` holds `template.json` (`{label, description}`) and `files/`,
copied into the new workspace before the first commit. Step 2 ships `blank`, `report`,
`dashboard`, `whiteboard`, `vite-app` and `fastapi-app`. Template files contain layout and
instructions only, never sample figures presented as data.

## Harness interface additions

`TurnRequest` gains `attachments: list[str]`, `context: str` (extra system-prompt text; unused
today, because the project facts — project URL, app state, log file, how to restart the app —
are sent inside every message as a `<studio-project>` block, so a resumed conversation never
holds stale ones) and
`approve: async (tool, title, detail, reason) -> bool`, which emits the two approval
events and waits for the answer. The fake harness asks for approval when the message
contains "risky", and writes a small `studio.json` app when it contains "backend".

## Bridge addition: pages that refresh themselves

`ready` gains `auto_reload` (bool). It is false when the page contains
`<meta name="studio-reload" content="off">`. The shell then does not reload the frame when
project files change (the page does its own updating: a dev server with hot reload, or an
editor such as the whiteboard that saves into the project). The shell still reloads it when
the project's app becomes `running`, after an inline edit, and on restore.

## Look — the agent can see the page

`GET /api/projects/{id}/look?path=/&width=1440&height=900&dark=0&full_page=1&wait_ms=1200&top=0`
loads the project URL + `path` in a headless Chromium that is already cached on the machine
(never downloaded) and returns:

```json
{"image": "/abs/…/projects/<id>/looks/look-<ms>.jpg", "url": "…", "status": 200, "title": "…",
 "viewport": {"width": 1440, "height": 900}, "dark": false, "page_height": 2410,
 "captured_from": 0, "captured_height": 2410, "cut_off": false, "wider_than_viewport": false,
 "console": [{"level": "error|warning|exception", "text": "…"}],
 "failed_requests": [{"url": "…", "status": 404, "error": null}], "text_start": "…",
 "tiles": [{"image": "/abs/…/looks/look-<ms>-1.jpg", "top": 0, "height": 1300}],
 "layout": {"measured": 412, "viewport_meta": true, "dark_styles": false,
            "findings": [{"kind": "clipped_text", "count": 2, "means": "…",
                          "examples": [{"where": "section.kpis > div.card > p", "y": 640,
                                        "text": "Total participants est…", "detail": "cut on the right by 46 px"}]}],
            "note": "Measured in the browser, not judged: …"}}
```

**A page longer than one look.** One look covers at most 6,000 px of height. `top` (page pixels,
default 0) starts it further down: the picture, the tiles and `captured_from` then begin there, and
`cut_off` is true whenever the page goes on below what was captured. The agent's tool says which
`top` shows the next part. The measurements always cover the whole page.

**Readable pieces (`tiles`).** `image` is the whole page in one picture, which a model sees scaled
down: a phone-width page 6,000 px tall arrives at a third of its size, too small to judge text
or spacing (measured on 23 of the looks the agents had taken). So the same load is also saved as
pieces a model can read, top to bottom, each overlapping the next a little: 1,300 px tall at
widths of 700 and more, and 1,100 px tall at twice the pixel density below 700 (a phone's
screen has at least that density). `top` and `height` are in page pixels. `tiles` is empty when
the page fits one piece at widths of 700 and more, since `image` is then readable as it is. The agent is told to read the tiles when it
judges how a page looks.

**What was measured (`layout`).** After the pictures are taken, the studio measures the page in
the same browser, with no model call, and lists what it found. Each finding has a `kind`, how
many there are (`count`), what the kind means, and up to five examples (`where`: a short CSS
path; `y`: the element's top in page pixels, so the tile can be found; the start of its text; a
detail). Kinds:

| `kind` | Reported when |
|---|---|
| `no_viewport_meta` | The page has no `<meta name="viewport">`: a phone lays it out 980 px wide and shrinks it, whatever this look shows |
| `wider_than_screen` | The page scrolls sideways; the elements that stick out past the right edge |
| `clipped_text` | Text cut by a box that hides its overflow (by more than 4 px). The detail says when it ends in an ellipsis or a line clamp, which may be meant |
| `overlapping_text` | Two lines of text from different elements drawn over each other (a quarter of the smaller one or more), neither behind an opaque box |
| `text_at_edge` | Text closer than 8 px (below 700 wide) or 12 px to the edge of the screen |
| `tight_padding` | Text closer than 6 px to the left edge of the box that frames it (a box with its own background or border) |
| `small_text` | Text of six characters or more smaller than 11 px. Reference marks and anything in `sup` or `sub` are left out: they are small on purpose |
| `low_contrast` | Text whose contrast with the colour behind it is under 4.5 to 1 (3 to 1 for large text). Skipped over pictures and gradients, where it cannot be measured |
| `small_tap_target` | Below 700 wide: a button, field or stand-alone link smaller than 24 px in either direction (the least WCAG 2.2 allows). Links in running text are left out |
| `broken_image` | An image that failed to load |
| `long_lines` | At 700 wide and more: a paragraph running to about 110 characters a line or more |

`viewport_meta` and `dark_styles` (the page's CSS has rules for `prefers-color-scheme: dark`, so
it is worth a look with `dark`) are always given. At most 4,000 elements are measured
(`measured`). The list is a set of hints from measurements, not a judgement: an item can be
intended, and a page with none can still look wrong. `layout` is `null` when measuring failed;
the look itself still succeeds.

`tiles=0` and `layout=0` (query) or `"tiles": false`, `"layout": false` (body) leave either out.
The studio's own looks (the page's errors after a request, a template's picture) ask for neither.

The screenshot is capped at 6,000 px tall, the last 12 looks are kept (each with
its tiles), and they live
outside the workspace so they are not versioned. 503 `look_unavailable` when no cached browser
or no `playwright` package exists; the agent is then told it cannot see the page.

Looking needs two things, and the reason says which is missing and how to get it:
- the optional `look` extra, `playwright==1.62.0` (`uv pip install -e '.[look]'` in `studio2/`);
- a Chromium already in the Playwright cache: `$PLAYWRIGHT_BROWSERS_PATH` when set, else
  `~/Library/Caches/ms-playwright`, else `~/.cache/ms-playwright` (the first that exists). The
  studio never downloads one; the person runs `<studio python> -m playwright install chromium`
  (about 170 MB) or points `PLAYWRIGHT_BROWSERS_PATH` at a cache that already has one.

A "can look" answer is kept for the life of the studio. A "cannot" answer is checked again
when asked after 5 seconds, so a browser or package installed while the studio runs is found
without a restart; the reason for a missing package also says to restart the studio if it is
still reported after installing.

## Project files under an app

`GET <project URL>/__studio/files/<path>` returns the workspace file at `<path>` as it is on
disk, also while the project's app owns the URL (same path rules as the static preview, never
`.git`). The shell uses it for thumbnails, file links and the image viewer.

## Bridge addition: files dragged onto the page

frame → shell `file-drag` (no fields) when a drag carrying files enters the page. The shell
shows its drop target over the frame; a drop there uploads to the workspace root.

---

# Step 2b additions: what the studio can do, and the person's own databases

## Library — what is available

`GET /api/library` → everything the agent can use, as the studio actually has it:

```json
{"connections": [Connection],
 "tools": [{"id", "label", "description", "available": true, "note": null}],
 "skills": [{"id", "label", "description", "source": "studio" | "built-in"}],
 "mcp_servers": [{"name", "status"}],
 "templates": [Template],
 "data_kinds": [{"kind": "postgres", "label": "PostgreSQL", "default_port": 5432, "available": true, "note": null}],
 "observed_at": "…" | null}
```

- `tools` is the studio's own list (files, shell, web search, web fetch, sub-agents, look at
  the page, project back end, databases). `available: false` with a `note` when something is
  missing on this machine (for example no cached browser), and a `note` on web search that it
  only works on the Claude subscription connection.
- `skills` with `source: "studio"` come from `library/skills/`. `built-in` skills and
  `mcp_servers` are what the most recent agent session reported when it started;
  `observed_at` is when (null: no session has run since the studio was installed, and those
  two lists are then empty rather than guessed).
- `data_kinds` lists the database types the studio can connect to; `available: false` when
  its driver is not installed.

## Data sources — the person's own databases

A data source is a saved database connection that belongs to the studio, not to a project.
The password is kept in the operating system's keychain (macOS Keychain), never in a file the
studio writes, never in an event, a prompt or a response. A project can use a data source only
after the person attaches it to that project.

### DataSource object

```json
{"id": "ds_1a2b3c4d", "name": "Warehouse", "kind": "postgres", "host": "db.example.com",
 "port": 5432, "database": "analytics", "user": "reader", "ssl": "require", "ca_file": null,
 "has_password": true, "created_at": "…", "last_test": {"ok": true, "at": "…", "message": "…"} | null}
```

`kind`: `postgres`, `redshift`, `mysql`. `ssl`: `disable`, `prefer`, `require`, `verify-full`
(default `prefer`; `redshift` defaults to `require`). `name` is unique, 1–60 characters.
The modes mean the same for every kind (PostgreSQL's own meanings; for MySQL the studio does
the same itself):

| `ssl` | Encrypted | Checks who the server is |
|---|---|---|
| `disable` | never | no |
| `prefer` | when the server offers TLS; plain only when it offers none (a wrong password or an unreachable host is not retried in plain) | no |
| `require` | always; the connection fails without TLS | no |
| `verify-full` | always | yes: the certificate must be signed by a trusted authority and name the host |

`prefer` and `require` protect against someone reading the traffic, not against someone
pretending to be the server. For a database on another machine use `verify-full`.

**Who is trusted under `verify-full`.** With `ca_file` set, only the authorities in that file.
Without it, the public authorities in the list the studio's Python uses (Python's own default
file, such as Homebrew's OpenSSL list on a Mac; when there is none, the `certifi` list). The
same list for every kind. Not the macOS Keychain, and not libpq's `~/.postgresql/root.crt`
(PostgreSQL's driver would otherwise read that file, and trust nothing when it is missing).

**`ca_file`**: the full path of a PEM certificate file on the studio's computer, for a server
whose certificate is not signed by a public authority (most private databases; cloud databases
that publish their own bundle). A leading `~/` is expanded before it is saved. It is a setting,
not a secret. On create, on edit and on a test before saving it must be a readable file of at
most 1 MB holding at least one certificate; otherwise 400 `invalid_request` saying which of
those is wrong. `""` (or `null` on create) means none. It is used only with `verify-full`: it
is kept, and unused, while `ssl` is another mode. It is read again at every connection, so a
file that has gone or changed into something else fails the connection with the same words.

**The CA file is trusted for what it holds, not where it is.** When a source is saved with a CA
file, and whenever its password is typed again, the studio keeps the file's SHA-256 with the
source (not in the API's answers). At every connection that would send the saved password under
`verify-full`, the file is read once, its SHA-256 compared, and the driver given exactly the
bytes that were checked (PostgreSQL through a private copy that is deleted after connecting).
When the file has changed, nothing is sent: the connection is refused with the code
`ca_changed` and the words "The CA file … has changed since the password was saved, so the
password was not sent. If the new file is right, type the password again to trust it." Typing
the password again (with or without other changes) trusts the file as it is then. A cloud
provider's updated bundle therefore needs the password once more. A source saved before the
studio kept this fingerprint is treated the same way: until its password is typed again, a
connection with the saved password is refused with `ca_changed`, saying the file was saved
before the studio checked what it holds. A test before saving, and a source with no saved
password, send no saved password and are not checked this way.

**No password in clear text to a server that was not checked.** The same host and port is no
proof of who answers: on this computer anything can listen on a free database port (a saved
`localhost:5432` whose database is not running), and the database server chooses how the
password is sent. Unless `ssl` is `verify-full`:

- PostgreSQL and Redshift connect with libpq's `require_auth=!password`, so a server that asks
  for the password in clear text (`AuthenticationCleartextPassword`) is refused before anything
  is sent; MD5 and SCRAM go on as before. This needs libpq 16 or later (the studio's psycopg
  carries 18); with an older libpq the option is left out and the limit stands.
- MySQL refuses the `mysql_clear_password` and `dialog` methods (PyMySQL otherwise answers both
  with the password itself).

Under `verify-full`, MySQL answers those two methods only after checking, at that moment, that
the connection really is encrypted with a checked certificate and host name; otherwise it
refuses before the password is written. `require` and `verify-full` need PyMySQL 1.2 or later
(the studio's floor), which stops before logging in when the server offers no TLS; with an older
PyMySQL found at run time, those two modes refuse to connect at all rather than log in first and
check afterwards.

The error says so, and how to proceed: use `verify-full`, or a server that does not ask for
the password in clear text. **Still open:** MySQL's `caching_sha2_password` and `sha256_password`,
when the server asks for a full authentication, send the password over the unchecked TLS of
`prefer`/`require` or encrypt it with a public key the server itself supplies; a server that
pretends can read it either way. For MySQL, only `verify-full` protects the password from a
listener that is not the database. The form says so under the modes when the kind is MySQL
and the mode is not `verify-full`; nothing is refused.

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/data-sources` | | `[DataSource]` |
| `POST /api/data-sources` | `{name, kind, host, port?, database, user, password?, ssl?, ca_file?}` | `DataSource` (201). 400 `invalid_request` with a message naming the field; 409 `name_taken` |
| `PATCH /api/data-sources/{id}` | any of the same fields; `password: ""` removes the stored password; `ca_file: ""` removes the file | `DataSource`. A new `kind`, `host`, `port`, `ssl` or `ca_file` on one with a stored password needs `password` with it (400 `password_needed`): the CA file decides which server is trusted with the password, as the host does |
| `DELETE /api/data-sources/{id}` | | 204. Also detaches it from every project and deletes the stored password |
| `POST /api/data-sources/{id}/test` | | `{ok, message, code?, server_version?, tables?}`; always 200 for a known id. Connects, runs a trivial query, counts visible tables. Updates `last_test` (which keeps `code` too). `code: "ca_changed"` when the CA file no longer matches the saved password |
| `POST /api/data-sources/test` | the same body as create | the same answer, without saving anything |
| `GET /api/projects/{id}/data-sources` | | `[{...DataSource, "attached": bool}]` |
| `PUT /api/projects/{id}/data-sources/{ds_id}` | | 204. Attach |
| `DELETE /api/projects/{id}/data-sources/{ds_id}` | | 204. Detach |

`Project` gains `data_sources: [id]`.

### Using an attached data source (the agent, and the project's own app)

Both need the project's token: header `X-Studio-Token: <token>`. The agent is given it with
every message; the project's app gets it as the environment variable `STUDIO_TOKEN`, with
`STUDIO_DATA_URL` = `http://127.0.0.1:8200/api/projects/<id>/data`. The token is not part
of the `Project` object. Wrong or missing token → 403 `bad_token`.

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/projects/{id}/data` | | `[{name, kind, database}]` for attached sources |
| `GET /api/projects/{id}/data/{name}/schema?table=` | | Without `table`: `{tables: [{schema, name, type, columns: n}]}` (at most 2,000). With `table` (`schema.name` or `name`): `{table, columns: [{name, type, nullable}], row_estimate}` |
| `POST /api/projects/{id}/data/{name}/query` | `{sql, limit?, save_as?}` | `{columns: [{name, type}], rows: [[…]], row_count, truncated, elapsed_ms}`. With `save_as` (a workspace path ending `.csv` or `.json`) the rows are written there instead and the answer has `saved: path` and no `rows` |

Rules for `query`:
- One statement, and it must be a read: the studio opens a read-only transaction (so the
  database itself refuses writes), applies a 60 s statement timeout, and rolls back.
- `limit` defaults to 1,000 rows, at most 10,000 inline; with `save_as` at most 500,000 rows
  or 200 MB. `truncated: true` when more rows existed.
- Values are JSON: numbers, strings, booleans, null; dates and times as ISO 8601 strings;
  decimals as numbers when exact in a double, else strings; bytes as base64.
- Errors are 400 `query_failed` with the database's own message, 404 `unknown_data_source`
  when the name is not attached to this project, 503 `driver_missing`.

## Look: acting before the screenshot

`POST /api/projects/{id}/look` takes the same options as the GET in a JSON body plus
`actions`, run in order after the page has loaded and before the screenshot:
`{"fill": selector, "value": text}`, `{"click": selector}`, `{"hover": selector}`,
`{"press": key}`, `{"select": selector, "value": option}`, `{"wait_for": selector}`,
`{"wait_ms": n}`. At most 30 actions. The answer gains
`actions: [{index, ok, error}]`; a failed action does not stop the rest.
The page is loaded with the person's sign-in or the agent's own kept one, and the answer says
which (`signed_in`, `kept_sign_in`, `sign_in_note`; "Seeing an app signed in as the person").

## Other changes

- A gateway connection is checked with one tiny request before a turn starts, so a wrong
  key, an unknown model or an unreachable gateway fails in seconds with the gateway's own
  message instead of after the CLI's retries. It has the shape of the agent program's own
  request where a gateway is known to tell them apart: streamed, with the output limit the
  agent program will use (`CLAUDE_CODE_MAX_OUTPUT_TOKENS` when the connection's
  `max_output_tokens` or the studio's environment sets it, else the program's default of
  32000). Only the status and the first event are read, then the connection is closed. A
  status of 400 or above, or an `error` event first, is reported with the gateway's words.
  One refusal is asked about again: an output limit larger than the model takes. The agent
  program lowers its limit by itself for the models it knows, so the check is repeated once
  with a limit of 1024 and that answer counts.
  It still sends a plain-string system prompt and no thinking or tools, so a route that
  refuses those passes the check and fails in the turn; the model check covers that.
- Checkpoint messages for uploads are `Upload <path>`; clients label those "Uploaded", not
  "Saved your edit".
- `tool_call.title` for shell commands drops a leading `cd <project folder> &&`/`;`.

---

# Step 3 additions: the capability library

## Studio tools (an MCP server inside the studio)

Every turn of the `claude` harness gets an in-process MCP server named `studio`. Its tools
appear in events as `tool_call` with `name: "mcp__studio__<tool>"`.

| Tool | Arguments | What it does |
|---|---|---|
| `search_web` | `query`, `max_results?` (≤ 20) | Keyless web search. Works on every connection, including gateways. Returns `{results: [{title, url, snippet}], via: "duckduckgo" \| "yahoo", cached}`. How it is sent is under "Web search" below. When no service answers, the error says search is unavailable right now, names what each service answered, and tells the agent to use the built-in `WebSearch` where the connection has it or to fetch an address it knows exists, and not to make addresses up |
| `fetch_source` | `url`, `render?`, `refresh?`, `max_chars?` | Downloads a public page, PDF or text file, extracts the readable text and saves it in the project as a **source** (below). `render: true` loads the page in the browser first (for pages built by scripts). Returns the source id, title and the start of the text. An address already saved in the project (as it was asked for or as it ended up after redirects; fragment, trailing slash and the host's case ignored) is not downloaded again: the answer is `{id, title, url, kind, chars, saved, already_saved: true, note}` without the text. `refresh: true` downloads it again |
| `read_source` | `id`, `find?`, `offset?`, `end?`, `max_chars?` | Reads part of a saved source: the passages around each match of `find` (12 at most, about 1,000 characters each), or the range from `offset` to `end` (or `max_chars` from `offset`, 12,000 by default); `next_offset` says where the text goes on |
| `add_source` | `path`, `title?` | Registers a project file (PDF, text, Markdown, HTML) as a source |
| `query_files` | `sql`, `limit?`, `save_as?` | Runs one read-only DuckDB statement with the project folder as working directory (`select … from 'data/x.csv'`, Parquet, JSON) |
| `database_schema` | `source`, `table?` | Same as `GET …/data/{name}/schema` for an attached data source |
| `query_database` | `source`, `sql`, `limit?`, `save_as?` | Same as `POST …/data/{name}/query` |
| `look_at_page` | `path?` (`/`, `/index.html` or `index.html`), `width?`, `dark?`, `full_page?`, `wait_ms?`, `actions?` | Same as `POST …/look`; returns the JSON (the screenshot is a file the agent then reads) |
| `check_page` | `path?` (default `index.html`) | The citation and number check (below) |
| `check_file` | `path` | The same for a `.pptx`, `.docx`, `.xlsx` or `.pdf` file: every figure in it against checked page figures, data files and saved sources (see "Verify content reaches files") |
| `render_document` | `path`, `store_results?` (default true), `pages?` | Renders an Office or PDF file of the project into a preview folder next to it (see "Office documents"; `pages` in "Very large documents") |
| `render_video` | `path`, `out?`, `format?`, `width?`, `height?`, `fps?`, `duration?`, `start?`, `scale?`, `audio?`, `volume?`, `check?`, `times?`, `frames?`, `poster_at?` | Renders an animated page of the project to a video file, frame by frame (see "Video and images from a page") |
| `render_image` | `path`, `out`, `width?`, `height?`, `scale?`, `selector?`, `transparent?`, `dark?`, `at?`, `wait_ms?` | Saves a picture of a page, or of one element of it, as a `.png`, `.jpg` or `.webp` file in the project |
| `render_diagram` | `name`, `source?` or `path?`, `must_show?`, `png_width?`, `colors?`, `alt?` | Draws a Mermaid diagram with the studio's own pinned copy, checks that it parses, holds the asked-for labels and reads at desktop width, and saves its source, a light and a dark SVG (and a PNG) under `files/diagrams/` (see "Diagrams and conversions") |
| `convert_document` | `path`, `to`, `sheet?`, `reference?`, `replace?` | Converts a finished file of the project to another format beside it, through LibreOffice or pandoc, and says what did not come across (see "Diagrams and conversions") |
| `generation_models` | `kind?`, `model?` | Lists what the generation service can make, or one model's settings, with the credits left. Costs nothing (see "Generated media") |
| `generate_media` | `kind`, `out`, `prompt?`, `model?`, `aspect_ratio?`, `resolution?`, `quality?`, `duration?`, `params?`, `references?`, `start_image?`, `end_image?` | Asks the generation service for a picture, a clip or sound, after the person approves the price |

### Sources

`sources/index.json` in the workspace: `{"sources": [Source]}`.

```json
{"id": "s3", "url": "https://…" | null, "path": "sources/s3.txt", "original": "sources/s3.pdf" | null,
 "title": "…", "kind": "html" | "pdf" | "text", "fetched_at": "…", "chars": 48211, "sha256": "…",
 "pages": 12 | null}
```

`sources/<id>.txt` is the extracted text exactly as it was read (PDF pages separated by a
line `[page N]`). A source is never rewritten: fetching the same URL again makes a new id
only when the text changed. Limits: 25 MB download, 2,000,000 characters of text.

Saving is one step per project: helpers that fetch in parallel each get their own id, and an id is
always paired with the text that was read for it. Two fetches of the same address at the same time
(without `refresh`) end with one source; the second answers `already_saved: true` with the first's id.
The index is written to a temporary file and renamed over the old one, so it is never half written.

### The page check

Marking in the page (any HTML file in the project):

| Mark | Meaning | How it is checked |
|---|---|---|
| `<span data-src="s3" data-quote="exact words from the source">$4.2 billion</span>` | a figure or statement taken from a source | `verified` when the quote (white space and typographic quotes normalised, case-insensitive) is in the saved text of `s3` **and** every number shown in the element also appears in the quote; else `failed` with the reason |
| `<span data-src="data/sales.csv" data-sql="select sum(amount) from 'data/sales.csv'">1,284</span>` | a figure computed from project data | the statement is run; `verified` when its single value equals the number shown at the precision shown; else `failed` with the value found |
| `<span data-src="s3 s4" data-derived="s3 revenue ÷ s4 units">$412</span>` | a figure the author calculated from sourced figures | `derived`: the sources must exist; the arithmetic is not checked |

`check_page` answer, also written to `checks/<page>.json`:

```json
{"page": "index.html", "checked_at": "…",
 "summary": {"verified": 31, "derived": 4, "failed": 2, "unmarked_numbers": 7, "sources_cited": 9, "sources_unused": 1,
             "cited_not_listed": 2},
 "source_list": true, "not_listed": ["s27", "s2"],
 "items": [{"n": 1, "text": "$4.2 billion", "status": "verified" | "derived" | "failed", "kind": "quote" | "data" | "derived",
            "sources": ["s3"], "quote": "…", "reason": null, "found": null, "where": "Revenue › paragraph 2"}],
 "unmarked": [{"text": "…sentence with the number…", "numbers": ["38%"], "where": "…"}],
 "sources": [{"id", "title", "url", "fetched_at", "cited": 4}]}
```

`unmarked` lists numbers in visible text that sit outside every mark (years, dates, list and
section numbering, and anything inside `[data-nocheck]`, `<code>`, `<pre>`, `<svg>`, `<script>`
and the element with `id="checked-numbers"` are skipped). It is advice, not a failure.
A quote match says the words are in the saved source; it does not say the source is right.

**Cited sources missing from the source list.** A page's source list is its elements with
`id="src-<id>"` (the sourced-report skill's "Sources" list). When the page has at least one such
element, every source id cited by a `data-src` mark (a saved source's id, or any `s<number>`;
project files such as `data/sales.csv` are not sources) must have one. The answer then carries
`summary.cited_not_listed` (how many ids are missing; 0 when the page has no source list at all)
and `not_listed: ["s27", "s2"]` (those ids, in the order they are first cited), and
`source_list: true|false` (whether the page has one). A missing entry is reported, not counted as
a failed item; the tool's `next` names the ids to add.

## Skills, agents and MCP servers per project

`GET /api/library` changes:

```json
{"skills": [{"id": "studio:deep-research", "name": "deep-research", "label": "…", "description": "…",
             "source": "studio" | "yours" | "machine" | "built-in", "default": true}],
 "agents": [{"name": "researcher", "description": "…"}],
 "studio_tools": [{"name": "fetch_source", "description": "…", "available": true, "note": null}],
 "mcp_servers": [McpServer],
 …the Step 2b fields, unchanged}
```

- `source: "studio"`: shipped in `library/skills/`, on by default in every project.
  `"yours"`: added in the studio (`data/skills/`), on by default. `"machine"`: found in
  `~/.claude/skills` on this machine, off by default, copied into a project's session only
  when switched on there. `"built-in"`: reported by the agent CLI itself; cannot be switched.
- `agents` are the sub-agent types shipped in `library/agents/`.

### McpServer object

```json
{"id": "mcp_1a2b3c4d", "name": "docs", "label": "Cloudflare docs", "transport": "http" | "stdio",
 "url": "https://…" | null, "command": "npx" | null, "args": ["…"],
 "secret_names": ["Authorization"], "source": "yours" | "machine" | "suggested",
 "description": "…", "last_test": {"ok", "at", "message", "tools": [{"name", "description"}]} | null}
```

- `yours`: added in the studio. Header and environment **values** go to the keychain; the
  object only lists their names. `machine`: read from `~/.claude.json` at the moment of use
  (the studio stores nothing about it but the name). `suggested`: a short list shipped in
  `library/mcp-suggestions.json` of public servers that need no account.
- `description` is the one the person or the file gave (`description` in a connector's
  settings in `~/.claude.json`, a connector file or a plugin's `.mcp.json`). When there is none
  and the studio has seen the connector's tools (a test, or the agent program's start-up report of
  a turn it was connected in), it is made from them: `Tools: <names> (<n> tools). <first sentence
  of each tool's description>; …`, at most 400 characters. Otherwise it is empty; the studio does
  not invent one.
- `name` is what tools are called by: `mcp__<name>__<tool>`; lower-case letters, digits, `-`
  and `_`, 1–40 characters, unique, not `studio`.

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/mcp-servers` | `{name, label?, transport, url? , command?, args?, headers?: {name: value}, env?: {name: value}, description?}` | `McpServer` (201). 400 `invalid_request`, 409 `name_taken` |
| `PATCH /api/mcp-servers/{id}` | any of the same fields | `McpServer`; only `yours`. A new `url`, `transport`, `command` or `args` on one with stored secrets needs its `headers` or `env` given again, each with a value (400 `secret_needed`, "Where a key is kept"). `headers` and `env` are sent whole: an empty value is refused, never "keep the stored one" |
| `DELETE /api/mcp-servers/{id}` | | 204; only `yours`. Removes it from every project and deletes its stored secrets |
| `POST /api/mcp-servers/{id}/test` | | `{ok, message, tools: [{name, description}]}`; connects, lists tools (at most 200), 20 s limit. Works for every source; updates `last_test` for `yours`. A stdio connector from the library starts through the launcher, as in a request (below) |
| `POST /api/skills` | `{name, description, body}` | the skill (201); writes `data/skills/<name>/SKILL.md`. `name` as for servers, ≤ 60 |
| `DELETE /api/skills/{name}` | | 204; only `yours` |
| `GET /api/projects/{id}/library` | | `{"skills": [{…skill, "enabled": bool}], "mcp_servers": [{…McpServer, "enabled": bool}]}` (`built-in` skills are not listed) |
| `PUT /api/projects/{id}/library` | `{"skills"?: {id: bool}, "mcp_servers"?: {id: bool}}` | the same object as the GET. Unknown ids → 400. Takes effect at the project's next turn |

`Project` gains `library: {"skills": {id: bool}, "mcp_servers": {id: bool}}` holding only
the person's deviations from the defaults. At most 12 MCP servers enabled per project.

A turn that could not connect to an enabled MCP server still runs; it carries a `notice`
(`warning`) naming the server.

A turn's connectors reach the agent program in a file, never on its command line (where `ps`
shows every argument to every process and user on the machine): `<data>/run/mcp/mcp-<random>.json`,
mode 600, in a folder of mode 700, outside every project. Only the studio's own tools, which run
inside the studio and hold no secret, stay on the command line. The file is kept while the
program runs (it may read it again to reconnect) and removed when the turn ends, however it ends;
files a killed studio left behind are removed when the studio starts. The agent runs as the same
user as the studio, so while its turn runs it can still read that file: the gain is that a
connector's headers and environment values are no longer one `ps` away for every process and
every user.

**A library connector starts outside the project folder.** The agent program starts each stdio
connector with the project as its working folder, and its connector settings have no field to
change that (the SDK's stdio entry has `command`, `args` and `env` only; the bundled program
spawns with its own project folder as `cwd`). A command that looks in its working folder first
would then run what the agent wrote there, with the connector's stored `env`: `npx -y some-mcp`
finds `node_modules/.bin/some-mcp`, `python -m pkg` finds `pkg/`. So every stdio connector from
the library (sources `yours`, `machine` and `plugin`) is started through a small launcher the
studio owns:

`<the studio's python> -I -S <studio2_server>/connector_launch.py <data>/run/connectors <command> <args…>`

- The launcher makes a new, empty folder of mode 700 under `<data>/run/connectors` (itself mode
  700, a real folder owned by the studio's user, never a link), changes to it, and replaces itself
  with the real command (`exec`). The connector's own process then has that folder as its working
  folder. A start folder (`c-` and 16 hexadecimal characters) whose last change is more than a
  day old is removed when the studio starts and each time a connector starts: only by its exact
  path inside `<data>/run/connectors`, after that folder is checked as above, and without
  following a link inside it. Younger folders are left, since a connector may still be using
  one.
- It imports nothing but Python's own modules (`-I -S`: no working folder, script folder, user
  site or `.pth` files on the import path), so a file planted in the project, or in the folder it
  goes to, is never loaded by it.
- The connector's secrets stay in its `env`, in the owner-only settings file above: the
  launcher's command line holds only the folder, the command and its arguments, and it never
  prints a variable.
- Variables that point into the project folder are dropped before the command starts: a
  variable whose value is the project or a path inside it (`CLAUDE_PROJECT_DIR`, `OLDPWD`, `INIT_CWD`), and any
  entry inside the project in a list of paths (`PATH`, `NODE_PATH`, `PYTHONPATH`, …). A variable
  that still mentions the project folder anywhere in its value after that
  (`NODE_OPTIONS=--require <project>/x.js`) is dropped whole. `PWD`
  becomes the new folder. When the project is the home folder or above it, nothing is dropped.
- A command that is a relative path (`./serve.sh`, `bin/serve`) used to be found in the project
  folder. It is now refused when a connector is added or changed in the studio (400
  `invalid_request`, "give the full path"), and one from `~/.claude.json` or a plugin fails to
  start with a sentence that says so. A relative path among the arguments (`node dist/index.js`)
  is now read from the new, empty folder and fails too: give full paths. A connector that used
  to work on the folder it started in (`.` as an argument, such as a file-system or git
  connector) now sees an empty folder, without an error, and should be given the project's full
  path instead.
- The connector's Test starts it the same way, through the launcher (from the
  `<data>/run/connectors` folder rather than the project).
- An attached app's own connectors (source `app`, from the project's `.mcp.json`) belong to the
  project: they still start in the project folder, without the launcher, and get no studio
  variable.
- Still open: the agent's shell runs as the same person, so it can write outside the project
  (`<data>/run/connectors`, `~/.npmrc`, the studio's own Python), as for every other check here.
  The launcher closes the project folder, not the machine.

## Sub-agents in the background

The agent CLI starts sub-agents in the background and ends the assistant's turn while they
work; the session continues by itself when they report back. A studio turn therefore ends
only when the person's message has been answered **and** no sub-agent is still working
(`turn_finished` comes once, after the last of them). `usage.duration_ms` is the wall time
of the whole turn. Background shell commands do not hold a turn open.

---

# Access from other devices (phone, iPad, another computer)

Off by default: the studio and every project port listen on loopback only, and a request
that is not plainly local is refused with 403.

- `STUDIO2_HOSTS=<address>[,<address>]` adds addresses to listen on (for example the Mac's
  Tailscale address). It applies to the studio port and to every project port.
- `STUDIO2_ACCESS_KEY` (at least 24 characters) is then required; the server does not start
  without it.
- `STUDIO2_OPEN_TAILNET=1` lets devices on the tailnet in **without** the key: a request whose
  own connection comes from a Tailscale address (100.64.0.0/10) and carries no forwarding
  header is treated like a local one. The server refuses to start in this mode unless every
  address in `STUDIO2_HOSTS` is a Tailscale address. A proxy or tunnel on the same machine is
  still refused (it connects from loopback with a forwarding header), and so is any other
  address. In this mode the tailnet is the only boundary: every device on it can use the
  studio, including the agent's shell.
- A request is **local** only when the client address is loopback, the `Host` is
  `127.0.0.1`, `localhost` or `[::1]`, and it carries no forwarding header
  (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP`, …). So a tunnel or
  reverse proxy on the same machine is never treated as local.
- Every other request needs the cookie `studio2_access` (HttpOnly, SameSite=Lax, 30 days),
  set by `POST /login` with form field `key`. `GET /login` is the sign-in page. Without the
  cookie: a page request on the studio's own port is redirected to `/login?next=…` on the host it
  was addressed to, exactly as given (`http://<host>:<studio port>/login?next=…` when opened
  directly); a project port sends it to `http://<host>:<studio port>/login?next=…`, anything else gets 401 `access_denied`, a WebSocket is closed
  with 4401. Eight wrong keys from one address within five minutes → 429. The cookie is the
  studio's: a project port takes it out of the `Cookie` header before passing a request or a
  WebSocket on to the project's app.
- An open studio tab whose cookie has expired, or was made with a key that has since changed,
  gets that 401 from any API call (the health check, an event stream reconnect, or any other
  request); the UI then sends the browser to `/login?next=<the current address>`, once, and
  never from the sign-in page itself. Not while the file viewer holds unsaved edits: then the
  tab stays where it is and says, in the page, that the studio needs signing in again and that
  the edits are still here, with a link that opens the sign-in page in a new tab. While it
  waits it checks the studio again every few seconds and whenever the tab comes back into
  view; once a check is answered, the notice goes, the event stream reconnects, and Save works
  without a reload. If the edits are saved or dropped while signing in is still needed, the
  next 401 sends the browser to sign in as above. It shows "the studio is not running" only
  when nothing answered (a network error, or 502/503/504 from a proxy in front); any other
  answer is shown with its message.
- `Project.preview_url` uses the host the request was addressed to, so a project opened
  from `http://100.x.y.z:8200/` gets `http://100.x.y.z:<port>/`. Clients must use it as
  sent and never build project addresses themselves.
- **Behind a tunnel or proxy that carries one port.** When the address a request was sent to has
  another port than the studio listens on (no port in `Host` means 443 for `https`, 80 for `http`),
  something in between maps the port, and `<host>:<project port>` leads nowhere. The name still
  has to be one the studio answers to (`STUDIO2_NAMES`), such a request is never local, and it
  needs the access key like any other. The studio then works at that address as given: the
  sign-in redirect, the cookie and the `Origin` check use it without adding a port. Project pages
  are **not** reachable there: `Project.preview_url` is `<scheme>://<host>/no-page/<port>/`, and
  every `GET /no-page/…` answers 404 with a short page that says so. Chat, files and history work.
  Loopback names are never treated this way. The scheme is the connection's own; behind a proxy on
  this machine it is what the proxy reports in `X-Forwarded-Proto` (the server takes that header
  from loopback connections only).
- **A name for a project's page behind such an address.** `STUDIO2_PAGE_HOSTS` gives project ports
  names of their own there: `8403=site.example.com,8404=shop.example.com`, and `*=p{port}.example.com`
  for every other port (`{port}` is replaced by the number). For a request that arrived through a
  mapped address, `Project.preview_url` is then `<scheme>://<that name>/`; a project whose port has
  no name keeps the `/no-page/<port>/` address. Whatever is in front must send each name to its
  project's port on this machine. These names are names the studio answers to (a `{port}` name
  counts as any one part in front of the rest). They change nothing for a studio opened directly:
  loopback and private-network addresses keep their ports. The access cookie belongs to one host,
  so the key sign-in on the studio's name does not carry over to a page's name: use a gate (next
  item), whose token arrives with every request. An entry that is not `<port>=<name>` or
  `*=<name with {port}>` stops the server from starting.
- **A page with its own name that shows nothing in the studio.** A gate keeps its sign-in per
  name, and its sign-in page refuses to be shown inside another page, so a project page whose
  name the person has not opened yet is an empty frame in the studio and no request reaches the
  project. The interface notices it by the bridge: every page the studio serves carries it and
  says `ready`. When the project's page is on another host than the studio and its frame has
  said nothing 8 seconds after it was asked for, a notice over the frame says the page has its
  own address and has not loaded here, and offers that address in a new tab ("Open the page")
  and "Try again". Coming back to the studio's tab tries again by itself, and the notice goes
  with the first `ready`. A page on the studio's own host (loopback, a tailnet name with a
  port) never gets this notice.
- **A gate in front that signs people in.** A studio behind a service that checks who a person is
  before passing a request on (an identity-aware proxy) can take that service's signed token instead
  of the access key. Set `STUDIO2_GATE_KEYS` (the `https://` address of the gate's public signing
  keys, a JWKS document) and `STUDIO2_GATE_AUDIENCE` (the audience the token must name; several
  separated by commas). A request, HTTP or WebSocket, on the studio port or a project port, whose
  header `Cf-Access-Jwt-Assertion` (another name with `STUDIO2_GATE_HEADER`) holds a token that
  verifies is signed in: it needs no cookie and never sees the sign-in page. A token verifies when
  it is signed with RS256 by one of the gate's keys (no other algorithm is accepted), names one of
  the audiences, comes from the issuer (`STUDIO2_GATE_ISSUER`; default: the scheme and host of
  `STUDIO2_GATE_KEYS`), and has not expired (60 seconds of leeway). Anything else is treated as if
  the header were absent, so the key sign-in still applies. Such a request is still not *local*:
  what only the studio's own computer may do stays that way. The keys are fetched when first
  needed and kept for an hour; a token signed with a key the studio has not seen makes it fetch
  again, at most once a minute; when the keys cannot be fetched no token is accepted. The server
  does not start with only one of the two settings, or with a keys address that is not `https://`.
  The studio logs the token's `email`, when it has one, the first time it accepts that token.
- Mutating API requests are accepted from the studio's own origin: the configured loopback
  origins, or `http://<Host header>`.

- `STUDIO2_TLS_CERT` and `STUDIO2_TLS_KEY` (both, files) make the extra addresses speak HTTPS,
  on the studio port and every project port; loopback stays plain HTTP. Redirects, the
  `Origin` check and `Project.preview_url` then use `https://`, and the cookie is `Secure`.
  The address to use is the name on the certificate, not the IP.
- The sign-in page may be framed only by a page on the same host name (any port):
  `Content-Security-Policy: frame-ancestors 'self' https://<name> https://<name>:* …`.

## Names the studio answers to

A site can point its own name at this machine's address (DNS rebinding); the browser then
sends the studio `Host: that-site:8200` and treats the answers as the site's own. So every
request, HTTP and WebSocket, on the studio port and on every project page port, is checked
**before anything else** by the name in its `Host` (port, case and a final dot ignored):

- An IP address (`100.101.102.103`, `127.0.0.1`, `[::1]`) always passes: an address cannot be
  pointed somewhere else. A bracketed address must be the whole name (`[::1]` or `[::1]:8200`);
  `[::1].evil.example` is a name, not an address.
- `localhost` and any name ending in `.localhost` pass.
- This machine's own names pass, taken only from its own settings, never from a DNS answer: its
  host name (`socket.gethostname()`); the full names that `/etc/hosts` lists on the same line as
  that host name (such as `box.lan` on a Linux server installed with that name); the first part
  of each alone, and `<first part>.local`; on a Mac also the Bonjour name
  (`scutil --get LocalHostName`) and `<that>.local`. A name only the network's DNS gives this
  machine (a LAN router's `box.lan` that the machine itself does not list) is not taken, because
  whoever runs that DNS chooses it: add it with `STUDIO2_NAMES`.
- The names the studio was started with pass: every name (not address) in `STUDIO2_HOSTS`, and
  with `STUDIO2_TLS_CERT` every DNS name in the certificate (its subject alternative names; its
  common name when it has none). A wildcard `*.example.net` covers exactly one more part in front.
  So the tailnet name passes with `start-tailnet.sh` and no other setting: it is the name on the
  certificate.
- `STUDIO2_NAMES=<name>[,<name>]` adds names, for a studio reached through a proxy or a DNS name
  of its own.
- Any other name is refused with **421**: a short page (or, when the request does not ask for
  HTML, `{"error": {"code": "unknown_name", …}}`) that says which name was used and how to add it.
  A WebSocket is closed with 4421 before it is accepted. The project's app is never contacted.
- A request with **no** `Host` is refused with 400 (`no_name`). Browsers always send one; HTTP/1.1
  requires it.

The name check only refuses; a name that passes still goes through the rules above (local,
tailnet, access key). The project's own back end (page port + 100) listens on `127.0.0.1` and is
the project's program, not the studio's, so the studio cannot check names there. Other
devices reach it only through the page port, which is checked. A page open in a browser on this
same machine can reach `127.0.0.1:<port + 100>` by rebinding, so the app checks its own `Host`
against `STUDIO_ALLOWED_HOSTS`: the studio's templates and the back ends the agent writes do;
an attached app does only if its own server does (see "`studio.json` — a project with its own
back end"). Closing this for every program would need the app in a container or on a socket
only the studio can open, which is not done.

This is access for the owner on a private network, over plain HTTP unless a certificate is set. It is not a hosted,
multi-user mode: there are no accounts, every signed-in device has the owner's full rights
(including the shell), and project ports are separate origins on the same host.


---

# Draw on the page

The person draws on the live page (any project page: report, dashboard, app) and sends the
drawing with a message. The agent gets a numbered list of the marks, each tied to the page
element under it, plus screenshots of the marked regions with the marks drawn in.

## Bridge

`set-mode` gains `draw`. In draw mode the bridge puts a transparent layer over the viewport.
Marks are tied to the element under them, so they stay with their content when the page (or
a scrolling panel inside it) scrolls or reflows. Marks are numbered on the page from 1.

| Direction | `type` | Fields |
|---|---|---|
| shell → frame | `draw` | `tool`: `pen`, `box`, `note`, `move`, `pan`; `color` (CSS colour). Default `pen`, `#ef4444` |
| shell → frame | `draw-undo` | removes the last mark |
| shell → frame | `draw-clear` | removes every mark |
| frame → shell | `drawing` | `count`, `drawing` (a Drawing, or null when there are no marks). Sent after every change |

`pen`: drag to draw. `note`: tap, type, Enter (Shift+Enter for a new line, Escape to drop
it). `box`: drag a rectangle, then type what belongs there ("sidebar", "heading here");
Enter keeps it (an empty label is allowed), Escape drops it. Boxes are how the person
sketches a layout, on an existing page or an empty one. `move`: press on an element and drag
it to where it should go; the bridge outlines the element, draws an arrow and a ghost of it
at the new place. Nothing on the page is actually moved: it is an instruction for the agent.
`pan`: the layer lets everything through, so the person can scroll with one finger and use
the page; marks stay visible. Leaving draw mode hides the marks and keeps them;
a page load drops them (the frame sends `ready`, and the shell must forget its copy).

### Drawing

```json
{"path": "/", "viewport": {"width": 390, "height": 760}, "dark": false,
 "page": {"width": 390, "height": 5120},
 "marks": [
  {"n": 1, "kind": "stroke", "shape": "loop", "color": "#ef4444",
   "points": [[120, 2310], [124, 2318]], "box": [96, 2290, 210, 140],
   "rel": [[0.12, 0.3], [0.14, 0.34]],
   "anchor": {"selector": "#revenue > figure", "tag": "figure", "text": "Revenue by quarter…",
              "rect": [16, 2270, 358, 260]},
   "section": "Revenue", "from": null, "to": null},
  {"n": 2, "kind": "note", "text": "make this a bar chart", "color": "#ef4444",
   "points": [[140, 2450]], "box": [140, 2450, 0, 0], "rel": [[0.35, 0.69]],
   "anchor": {…}, "section": "Revenue"}]}
```

A `box` mark has `kind: "box"`, `text` (the label, may be empty), two `points` (opposite
corners) and `covers`. A `move` mark has `kind: "move"`, two `points` (where the element was
grabbed and where it was dropped), `anchor` (the element being moved, with its `rect`),
`ghost: [x, y, w, h]` (where the element would sit) and
`to: {selector, tag, text, side}` — the element at the drop point and which side of it the
drop was on (`above`, `below`, `left`, `right` or `inside`).

Coordinates are CSS pixels in the document (not the viewport). A mark whose anchor is the
page itself (`body`) has an empty `rel` and is redrawn from `points`. `rel` is each point as a
fraction of the anchor's rectangle; it is what lets the marks be redrawn on a fresh render.
`shape` is `dot`, `line`, `loop` or `scribble`; a `line` also carries `from` and `to`
(`{selector, tag, text}`) when its ends sit on different elements. A loop or scribble carries
`covers`: up to six `{selector, tag, text}` for the elements actually under it, which matters
when the anchor is a large container. `anchor.text` ≤ 200
characters, `text` of a note ≤ 500, at most 60 marks and 600 points per stroke.

## REST

| Route | Body | Answer |
|---|---|---|
| `POST /api/projects/{id}/drawings` | a Drawing | 200 `{path, images: [path], marks: n, rendered: bool, note: string or null}` |

The server loads the page in the headless browser at the drawing's viewport width, redraws
the marks on their anchors and saves one screenshot per marked region (at most 5) as
`attachments/drawing-<ms>-<n>.jpg`, and the drawing itself as `attachments/drawing-<ms>.json`
(`path`). The saved file gains `images: [{path, marks: [n]}]` and `note`. When no browser is available `rendered` is false,
`images` is empty and `note` says why; the drawing is still saved and usable.

To send it, put `path` in the turn's `attachments`. An attachment that is a drawing file is
not listed as a plain file: the agent is given the page, the width, every mark with its
element, section and note text, and the images to open. 400 `invalid_drawing` for a drawing
with no marks.

The screenshots come from a fresh load of the page, so state the person had set up in their
own browser (a chosen filter, an open tab, something typed) is not in them; their sign-in is,
when they allow it ("Seeing an app signed in as the person"). The mark list is
taken from the person's live page and is the more reliable of the two.


---

# Models for a project's back end

A project's app does not inherit the studio's environment: it gets `PATH`, `HOME`, `USER`,
`LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `LC_*`, `TERM`, `TZ` and a few cache locations, plus what
the studio sets for it (`PORT`, `HOST`, `STUDIO_PROJECT_ID`, `STUDIO_PROJECT_URL`,
`STUDIO_ALLOWED_HOSTS`, `STUDIO_TOKEN`, `STUDIO_DATA_URL`, `STUDIO_MODEL_URL`). No key or token of the person's or
the studio's reaches it. When the studio's config has an
`anthropic-gateway` connection (LiteLLM), the app instead gets `STUDIO_MODEL_URL` in its
environment and calls the gateway through the studio, with `STUDIO_TOKEN` as the API key:

| Route | Notes |
|---|---|
| `GET {STUDIO_MODEL_URL}/models` | The gateway's model list (OpenAI shape); each entry gains `paid: bool` |
| `POST {STUDIO_MODEL_URL}/chat/completions` | OpenAI chat body, forwarded as is. No streaming: `stream: true` → 400 `no_streaming` |

`STUDIO_MODEL_URL` is `http://127.0.0.1:<studio port>/api/projects/<id>/model/v1`, so an
OpenAI client works with `base_url=$STUDIO_MODEL_URL`, `api_key=$STUDIO_TOKEN`. The token is
accepted as `Authorization: Bearer …` or `X-Studio-Token`; 401 `bad_token` otherwise. 503
`no_gateway` when no gateway is configured, 502 `gateway_unreachable` when it does not answer.

A model is **paid** when its name starts with `openrouter/` and does not end in `:free` or
`/free`. Each successful call is written to `projects/<id>/model-usage.jsonl` with the cost
the gateway reported (`x-litellm-response-cost`). When a project's paid spend for the day has
reached `STUDIO2_APP_MODEL_DAILY_USD` (default 2.00), further paid calls get 429 `daily_limit`;
calls to models that are not paid are never limited. The answer carries
`x-studio-model-cost` and `x-studio-model-spent-today`.


---

# Office documents

The agent makes PowerPoint, Word and Excel files with ordinary libraries, as project files
(by convention under `files/`, built by a script under `tools/`). The studio's part is
`render_document`, which makes such a file visible: to the agent, so it can check its own
work, and to the person, inside the page.

`render_document(path)` writes `<path>.preview/` next to the file:

| File type | What is written | What the tool returns |
|---|---|---|
| `.pptx` `.ppt` `.odp`, `.docx` `.doc` `.rtf` `.odt` | `document.pdf`, `page-<n>.png` (1600 px wide for slides, 1240 px for pages, at most 60 a call: `pages` and the rest in "Very large documents"), `index.html` | `{file, kind, pdf, pages: [path], page_count, preview, embed, next}` |
| `.pdf` | `page-<n>.png`, `index.html` | the same, with `pdf` the file itself |
| `.xlsx` `.xlsm` `.xls` `.ods` | `index.html` | `{file, kind, sheets: [{name, rows, columns, cut, formulas, unchecked, errors: [{cell, formula, value}], error_count, start: [text rows]}], formula_results_stored, preview, embed, next}` |

`kind` is `presentation`, `document`, `workbook` or `pdf`. `index.html` is a self-contained
viewer (slides with a film strip, pages in a scroll, or sheets as tables with a formula bar),
with Download and, where there is one, Open PDF. It needs nothing from the studio, so it works
in a downloaded project. `embed` is an `<iframe>` for it.

For a workbook every formula is calculated (LibreOffice, set to recalculate on load) and the
results are compared cell by cell: a formula whose result is an error value or empty is listed
under `errors` (at most 30 in the tool's answer; `error_count` is how many there are). The
viewer and `start` show at most the first 400 rows and 50 columns of a sheet (`cut` is true
when the sheet is bigger), but the error check covers every formula in the workbook (array
formulas and data tables included), up to 200,000 formulas; `unchecked` counts a sheet's formulas past that bound, and `next` then says
how many were checked and how many were not instead of saying there are no errors. `next` also
says which sheets' previews are cut. With `store_results` (the default) the results are also written into the
`.xlsx` beside their formulas, because a file written by a library holds formulas without
results and shows empty cells in anything that does not calculate (a phone's file preview).
Nothing else in the file is touched.

Rendering uses a LibreOffice that is already installed (`STUDIO2_SOFFICE`, the macOS
application, or `soffice` on the path), with a profile of its own. Without one the tool is
listed as unavailable with the reason, and the agent is told it cannot render Office files.


---

# Voice input

The person can dictate a message. The browser records, the studio transcribes on this machine,
and the text is put in the message box for the person to check and send. Nothing is sent to a
speech service.

- `GET /api/health` gains `transcription: {available: bool, reason: string or null}`.
- `POST /api/transcribe`: multipart `file` (the recording: `audio/webm`, `audio/mp4`,
  `audio/wav`, `audio/ogg`, …; at most 25 MB) and optional form field `language` (`auto` by
  default, else a code such as `en`, `hi`, `mr`). Answer 200
  `{text, seconds}`: `text` is the transcript with noise markers such as `[BLANK_AUDIO]`
  removed (it may be empty when nothing was said), `seconds` is how long transcription took.
  503 `transcription_unavailable` with the reason when there is no transcriber, 413
  `file_too_large`, 422 `transcription_failed` when the transcriber could not read the audio.

The transcriber is a command that takes an audio file path and a language and prints the text:
`STUDIO2_TRANSCRIBE` if set, else `~/scripts/transcribe.sh` when it exists (whisper.cpp on
this Mac). It gets two minutes. Recording needs a secure page (HTTPS or localhost) and the
person's permission for the microphone; inside another app's frame, that frame must allow
the microphone.


---

# Templates the person saves

A project, or one Office file in it, can be saved as a **template** and used to start or
guide other work, so that the new thing looks like it and is built the same way. Templates
can be exported as a file and imported on another studio.

## Template object

`GET /api/templates` now returns the studio's built-in templates and the person's own:

```json
{"id": "tpl_3fa9c1d2", "label": "Competitor analysis", "description": "…", "app": false,
 "source": "yours", "kind": "page", "mode": "pattern", "created_at": "…",
 "from_project": "Enphase vs SolarEdge vs Tesla", "files": 14, "bytes": 412004,
 "preview": "/api/templates/tpl_3fa9c1d2/preview", "main": "index.html", "imported": false,
 "has_code": false}
```

- `source`: `studio` (built in; cannot be changed or deleted) or `yours`.
- `kind`: `page` (a report, dashboard or any page), `app` (a project with its own back
  end), `deck` (.pptx), `workbook` (.xlsx), `document` (.docx).
- `mode`: `copy` or `pattern`. An `app` template is **copied**: a project started from it
  begins as those files and runs. Every other kind is a **pattern**: the template's files are
  put in the project's `template/` folder for the agent to follow, and the page itself starts
  empty. A pattern carries the look and the way it was built, never the facts: the agent is
  told not to reuse the template's figures, names, dates or sources.
- `has_code`: the template contains `studio.json` or scripts under `tools/` (names compared
  without case: on a Mac disk `Studio.JSON` is the file the studio runs). `imported`: it came from a file.
- Built-in templates have `source: "studio"`, `mode: "copy"`, `kind` `app` or `page`,
  `preview: null`.

## What is saved

A copy of the project's files as they are, without `.git`, `sources/`, `checks/`,
`attachments/`, `research/`, dependency folders and caches, never with `state/` (what an app
writes while it runs: its database, uploads) or key files (see "Key files" under "Bring an app
by its folder"; the one rule the whole studio uses), and:

| Kind | Also left out | Kept |
|---|---|---|
| `page` | `data/`, `files/`, `tools/` | pages, styles, scripts, images |
| `app` | `data/` | everything else, including `studio.json` |
| `deck`, `workbook`, `document` | everything except → | the chosen file, its `.preview/` folder, `tools/`, `files/img/` |

Plus `TEMPLATE.md` (what it is, its files, how to use it, the person's description and any
notes on how it was made) and a preview image (a screenshot of the page, or the first page of
the Office file). At most 2,000 files and 40 MB; otherwise 413 `template_too_large`.
`state/` and key files are also dropped from a template's files when it is exported, imported
or used to start a project, so a template saved before this rule does not pass them on.

## REST

| Route | Body | Answer |
|---|---|---|
| `POST /api/projects/{id}/template` | `{name, description?, kind?, file?, notes?}` | 201 Template. `kind` defaults to `app` when the project has a back end, else `page`; `file` (a project path to a .pptx, .xlsx or .docx) sets the kind from the file. 400 `invalid_template` for an empty name, a wrong kind or a missing file |
| `PATCH /api/templates/{id}` | `{label?, description?}` | Template. 400 `builtin_template` for a built-in one |
| `DELETE /api/templates/{id}` | | 204. Projects that use it keep their copy |
| `GET /api/templates/{id}/preview` | | the preview image, or 404 |
| `GET /api/templates/{id}/export` | | `application/zip`, named `<label>.studio-template.zip` |
| `POST /api/templates/import` | multipart `file`, optional form field `confirm=1` | 201 Template. A template with `has_code` needs `confirm=1`: without it 409 `needs_confirmation`, because an app template runs on this machine when a project is started from it. 400 `invalid_template` for a file that is not a template; paths that leave the template, links, any `.git` (in any letter case) and oversize files are refused |
| `POST /api/projects` | `{name?, template?}` | `template` may be a saved template's id |
| `PUT /api/projects/{id}/uses-template` | `{template}` | Project. Puts the template's files in `template/` (replacing an earlier one) and records it; an `app` template used this way is a pattern too. Idle projects only |
| `DELETE /api/projects/{id}/uses-template` | | Project. Removes `template/` and the record |

`Project` gains `uses_template: {id, label, kind} | null`. While it is set, every turn tells
the agent that the template's files are in `template/`, to read `template/TEMPLATE.md` first,
and to follow the template without carrying over its content.

The agent can save a template itself: studio tool `save_template(name, description?, kind?,
file?, notes?)`, the same as the POST. `notes` is where it records how the thing was made
(palette, type, grid, section order, chart choices, the build script's parts).


---


# Video and images from a page

Motion, 3D and navigable graphics are ordinary pages: the agent writes them with CSS, the Web
Animations API, GSAP, three.js, D3 and the like, and the person uses them in the preview. The
studio's part is to turn such a page into files that can leave the studio: a video, a GIF, a
picture.

## `render_video`

A video is a page whose picture depends only on time. `render_video(path)` loads that page in
the studio's headless browser with the page's clock replaced by one the studio steps: for
each frame it moves the clock forward by `1/fps`, runs the timers and animation frames that
fall due, sets every CSS animation, transition, Web Animation, SVG animation and `<video>` to
that moment, takes a lossless picture, and passes it to ffmpeg. Nothing is recorded in real
time, so a frame that takes a second to draw costs render time and never a dropped frame.
`Date`, `performance.now`, `setTimeout`, `setInterval` and `requestAnimationFrame` all follow
the stepped clock; `Math.random` is left alone, so a page that wants the same picture on every
render seeds its own.

The page may declare itself, and arguments override what it declares:

```js
window.studioVideo = { width: 1920, height: 1080, fps: 30, duration: 24,   // seconds
                       seek(t) { /* draw second t */ },   // optional; may return a promise
                       ready,                              // optional promise: models, images loaded
                       audio: "media/track.mp3",           // optional soundtrack, a project file
                       poster: 9.5 };                      // optional: the second used as the poster
```

While a page loads for rendering its timers do not run (time stands at zero), so `ready` must
settle from load events, not from a timeout; a page that is not ready after 20 seconds fails
with that explanation. `window.__studioRender` is `true` in the renderer, for a page that
wants to hide its player controls.

| Argument | Default | Meaning |
|---|---|---|
| `path` | | The page, a project file (`video/launch.html`) |
| `out` | `media/<page name>.<format>` | Where the file goes |
| `format` | `mp4` | `mp4` (H.264, yuv420p, BT.709, faststart: plays on phones, in browsers and in editors), `gif` (at most 800 px wide and 15 frames a second) or `webm` (VP9) |
| `width`, `height` | 1920 × 1080 | The picture in CSS pixels; even numbers. `scale: 2` doubles the output (3840 × 2160). At most 3840 × 2160 in all |
| `fps` | 30 | 1 to 60 |
| `duration`, `start` | declared; 0 | Seconds. At most 180. Required when the page declares none |
| `audio`, `volume` | declared; 1 | A sound file of the project, cut to the video's length with a one-second fade out |
| `check` | false | Take only the contact sheet, encode nothing: the quick way to see the motion |
| `times`, `frames` | 12 frames, evenly spaced | Which moments the contact sheet shows: `times: [3.2, 7]` (up to 24; four or fewer are drawn large), or a count |
| `poster_at` | declared, or 40% in | The second saved as the poster picture |

It answers `{page, width, height, fps, duration, frames, declared_by_page, driven_by_seek,
file, bytes, sound, poster, embed, sheet, sheet_times, seconds_taken, console,
failed_requests, next}`. `sheet` is a contact sheet (a grid of frames, each labelled with its
time) saved outside the project, which the agent opens to see its own motion; `poster` is
`<name>.poster.jpg` beside the file (`media/intro.mp4` → `media/intro.poster.jpg`), a name that
cannot replace a picture of the project called `intro.jpg`; a video rendered before this has its
poster at `<name>.jpg`, which is left as it is and still works wherever a page names it; `embed` is a `<video>` (or `<img>` for a GIF) with a download
link. With `check` there is no `file`, `poster` or `embed`. A render runs for at most 15
minutes and one runs at a time. On this Mac a 12-second 1920 × 1080 video takes about 18
seconds.

`<video>` elements inside the page are stepped to the frame (the picture is taken only after
the new frame is on screen). Which clips the studio's browser can decode depends on the
machine: on a Mac it plays MP4 (H.264) and WebM; a Chromium built without H.264 (most Linux
builds) plays only WebM (VP9, AV1), and there a clip is converted to WebM first.

The `motion-video` skill carries `stage.js`, which the agent copies into the project: a
fixed-size stage with scenes (`data-start`, `data-end`) whose CSS animations run on the
scene's clock, `studioStage.timeline(gsapTimeline)`, `studioStage.onFrame(t => …)`, and a
player (play, scrub, loop, `?t=3.5`) for the preview. It sets `window.studioVideo`.

## `render_image`

`render_image(path, out)` saves a picture of a project page into the project: the whole page
at `width` (default 1200), exactly `width` × `height` when both are given, or one element
(`selector`). `scale` 1 to 3 multiplies the pixels; `transparent` leaves out the page
background (PNG and WebP); `dark` asks for the dark colour scheme; `at` steps an animated
page's clock to that second first. Answers `{file, width, height, bytes, console, next}`.

## Serving

A project's static files are served with byte ranges (`Accept-Ranges: bytes`, 206 answers),
on the project URL and under `/__studio/files/`, because a video player seeks with them and
Safari does not play a video without them. An open-ended range is answered 8 MB at a time.

Both tools use the cached Chromium that `look` uses; `render_video` also needs an ffmpeg that
is already installed (`STUDIO2_FFMPEG`, or `ffmpeg` on the path). Without them the tool is
listed as unavailable with the reason. On a Mac the browser draws WebGL with the graphics
chip (`--use-angle=metal`); elsewhere with the software renderer, which is correct but slow.



---


# Generated media

Photographs, footage, music and speech cannot be drawn by code. For those the studio can ask a
generation service. There is one provider: the Higgsfield command-line tool (`higgsfield`, or
`STUDIO2_HIGGSFIELD`), signed in on this machine as the person. Without it both tools are
listed as unavailable.

Every generation spends the person's credits, so:

1. `generate_media` first asks the service for the price of exactly this request (free).
2. It refuses when the price is more than the credits left, or would take the studio past
   `STUDIO2_GENERATE_DAILY_CREDITS` (default 100) for the day, across all projects.
3. It asks the person, through the running turn, **every time and whatever the project's
   `approvals` setting**: an `approval_request` with `tool: "generate_media"`, a title that
   carries the price ("Generate a picture for 6.5 credits"), the prompt, model, settings and
   target file as `detail`, and the reason "It spends 6.5 of your 372.3 Higgsfield credits."
   Denied, or no turn running: nothing is spent and the tool says so.
4. One job runs at a time. Just before buying, with no other job running, the studio checks
   again that generation is not paused, that the credits left still cover the price, and that
   the price still fits under the daily limit given what was spent since the quote; two quotes
   approved together cannot pass the limit between them, and a pause that began after the quote
   is honoured. A refusal here spends nothing and says so. The result is downloaded into the
   project at `out` (the extension follows what the service returned) and the spend (the fall
   in the balance) is appended to `data/studio2/generation-usage.jsonl` with the project, model,
   prompt and outcome. A job the service made but the studio could not download or save still
   counts: it is recorded with outcome `download_failed` or `save_failed` and the credits it
   cost (the fall in the balance, or the quoted price when the balance cannot be read), and the
   tool says the credits were spent. A job the studio stopped waiting for is recorded as
   `timed_out` at its quoted price (`credits_basis: "quoted"`), because the service may still
   finish it and charge. A balance that rose while a job ran (a refund, the monthly credits) is
   no measure of the job: the quoted price is recorded, never a negative amount. A job that did
   not complete is recorded with whatever the balance fell by, usually nothing.
5. **A server fault stops everything.** If the service fails with a 5xx error, generation is
   paused for six hours (recorded in `data/studio2/generation.json`) and the tool tells the
   agent not to try another model: such a failure can charge credits and deliver nothing, and
   a retry usually repeats it. Any other failure (a refused prompt, bad settings) is returned
   as it is, with the instruction not to switch models.

`kind` is `image` (default model `gpt_image_2`), `video` (`seedance_2_0`) or `audio` (no
default: the agent picks one with `generation_models`). `aspect_ratio`, `resolution`,
`quality` and `duration` are passed when given; `params` carries anything else the model
takes. `references`, `start_image` and `end_image` are project files the service is given.
It answers `{file, kind, model, credits_spent, credits_left, width, height, next}`.

`generation_models(kind?)` lists `{model, name, kind}` with the defaults, the credits left,
what the studio spent today and the daily limit; `generation_models(model)` returns that
model's parameters.

| Route | Answer |
|---|---|
| `GET /api/generation` | `{available, reason, paused, spent_today, daily_limit}`; `paused` is the explanation while a server fault holds generation off, else null |
| `DELETE /api/generation/pause` | 204. Lifts the pause |

The agent is told that a generated picture is an illustration: it is labelled as generated
where it is shown and is never presented as a photograph of the person's real product, place,
team or customers.


# Bring an app by its folder (step 3k)

A project can start from a folder on the studio's own computer instead of a blank page or a
template. The person types the folder's path (a browser cannot browse the disk), the studio
inspects it, and the person picks one of two ways in. Nothing is created before the person
presses Create; nothing is ever written to the original folder by a safe copy.

## Inspect

`POST /api/apps/inspect {path}` → `Inspection`. Reads only (git commands on the original run
with `GIT_OPTIONAL_LOCKS=0`, so not even git's index is refreshed). `~` is expanded.

```json
{"path": "/Users/me/dev/site", "name": "site", "exists": true, "readable": true, "writable": true,
 "git": {"branch": "main", "head": "<sha>", "subject": "…", "uncommitted": [{"path": "a.txt", "status": "modified"}],
         "uncommitted_more": 0},
 "framework": {"id": "next", "label": "Next.js"}, "run": "exec npx next dev -p $PORT -H 127.0.0.1",
 "package_manager": "npm", "install": "npm ci", "env_files": [".env", ".env.local"], "env_links": [],
 "size": {"files": 225, "bytes": 9100000, "history_bytes": 217000000, "partial": false},
 "refused": {"copy": null, "in_place": "The studio's own data is inside this folder."},
 "notes": ["…"]}
```

- `git` is null for a folder that is not the top of a git work tree. `uncommitted` lists at
  most 200 entries (`status`: `added`, `modified`, `deleted`, `untracked`); `uncommitted_more`
  counts the rest.
- `framework`/`run` come from a table of known kinds (below); both null when nothing matched.
  `run` names the Node or Python found for the app (`PATH=<dir>:$PATH …`) when the studio's
  own `PATH` would not find it. `install` is null when there is nothing to install.
- `env_files`: names only of the key files at the top of the folder (see "Key files"), never contents.
  A key file that is a link is not in this list (the studio never copies links); it is in
  `env_links`, and a note says the copy starts without it.
- `size.files`/`size.bytes`: what a copy would take (tracked files for git; for a plain folder,
  the files its `.gitignore` keeps, without `node_modules`, `.venv`, build output and caches).
  `history_bytes` is the size of `.git`. `partial` is true when the count stopped at 50,000 files.
- `refused.copy`/`refused.in_place`: null, or the sentence that says why that way in is not
  possible. A missing or unreadable folder refuses both.
- `lfs`: null, or `{"files": 12, "names": ["img/a.png", …], "not_here": ["data/b.bin", …],
  "not_here_count": 2}` for the files kept in Git LFS. A file counts when what is committed at
  `HEAD` is an LFS pointer, recognised by its content (the lines
  `version https://git-lfs.github.com/spec/v1`, `oid sha256:<hex>`, `size <n>`, under 1024
  bytes): that is exactly what a safe copy will hold, whatever `.gitattributes` says. The commit
  is read with `git ls-tree` and `git cat-file --batch` (object names on standard input, never
  paths on a command line), the read-only way inspection reads the folder; no filter runs. For a
  plain folder, its files that are pointers count. `not_here` (at most five) and
  `not_here_count`: the ones whose file in the folder is the pointer too (never downloaded there;
  every one, for a plain folder). `names` lists at most five. A note says how many there are,
  names a few, that a safe copy holds only the pointer files so images, models or data kept that
  way will be missing or broken there, and that to work with them the folder is brought in to be
  edited in place; when some are not downloaded in the folder either, it says to run git lfs pull
  there first. A copy can still be made.

Errors: 400 `invalid_request` when `path` is empty or not absolute after `~` expansion.

Known kinds (first match wins; every one is started with `"restart": "never"`, because each
reloads by itself):

| Kind | Detected by | `run` |
|---|---|---|
| Next.js | `next` in package.json dependencies | `exec npx next dev -p $PORT -H 127.0.0.1` |
| Nuxt | `nuxt` | `exec npx nuxt dev --port $PORT --host 127.0.0.1` |
| SvelteKit | `@sveltejs/kit` | `exec npx vite dev --port $PORT --host 127.0.0.1 --strictPort` |
| Astro | `astro` | `exec npx astro dev --port $PORT --host 127.0.0.1` |
| Angular | `@angular/core` | `exec npx ng serve --port $PORT --host 127.0.0.1` |
| Vite | `vite` | `exec npx vite --port $PORT --host 127.0.0.1 --strictPort` |
| Django | `manage.py` | `exec <python> manage.py runserver 127.0.0.1:$PORT` |
| FastAPI | `main.py`, `app.py`, `server.py` or `api.py` that makes `<name> = FastAPI(` | `exec <python> -m uvicorn <module>:<name> --host 127.0.0.1 --port $PORT --reload` |
| Flask | `app.py` or `wsgi.py` that makes `Flask(` | `exec <python> -m flask --app <module> run --host 127.0.0.1 --port $PORT --debug` |
| Rails | `Gemfile` naming `rails`, and `bin/rails` | `exec bin/rails server -b 127.0.0.1 -p $PORT` |
| Laravel | `artisan` | `exec php artisan serve --host=127.0.0.1 --port=$PORT` |
| Plain HTML | `index.html` at the top and no `package.json` | none: the studio serves the files |

Install commands: `package-lock.json` → `npm ci`, `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`,
`yarn.lock` → `yarn install --frozen-lockfile`, `bun.lock`/`bun.lockb` → `bun install`, a
`package.json` without a lock → `npm install`; `uv.lock` → `uv sync`, `requirements.txt` →
`uv venv -q && uv pip install -q -r requirements.txt`; `Gemfile` → `bundle install`;
`composer.json` → `composer install`. `<python>` is the interpreter the proposed install
leaves the packages in: `uv run python` after `uv sync`, `.venv/bin/python` after the
`requirements.txt` install (it creates `.venv`; the folder need not have one yet), whether or
not a Node install comes first. With no Python
install proposed it is `uv run python` with a `uv.lock`, `.venv/bin/python` when the folder
already has a `.venv`, else `python3`. A `pyproject.toml` with a `[project]` table (and no
`uv.lock` or `requirements.txt`) → `uv sync`, with `uv run python`, but only when there is
evidence of a Python app (below): a `pyproject.toml` that only configures tools (`[tool.*]`,
`[build-system]`, or a `[project]` with no app beside it) is never installed.

**Evidence of a Python app** is one of: a `manage.py`; a top-level `main.py`, `app.py`,
`server.py`, `api.py` or `wsgi.py` that makes a FastAPI or Flask app; or a start command, as
typed in the attach form, that is Python (it begins with `python`, `python3`, `uv run`,
`.venv/bin/…`, `uvicorn`, `gunicorn` or `flask`). The install is chosen again from the typed
start command when there is one. A folder with no `package.json` keeps its `uv.lock` or
`requirements.txt` install whatever it holds.

A folder with both a Node part (`package.json`) and a Python part (`uv.lock`, `requirements.txt`
or a `pyproject.toml` as above):

- **without evidence of a Python app**, only the Node install runs (the Python files are taken
  as docs, tooling or scripts), and a note names the file and says its Python packages are not
  installed;
- **with it**, both installs run, Node first; `package_manager` names the Node one. When the
  Python app is what starts, they are joined by `&&` (it cannot start without its packages).
  When a Node app is what starts, the Python install may fail without stopping it:
  `<node> && ( <python> || echo "[studio] …" )`, and the `[studio]` line in the app log says
  the Python packages could not be installed (the lines above it say why) and that the app
  starts without them.

One command starts the app, so one part is started:

- a known Node kind (the table above) wins: its dev server is started on `$PORT`. When a
  Django, FastAPI or Flask app is also found, a note says the Python back end's packages are
  installed but it is not started, that a failed Python install does not stop the Node app, and
  that a start command that runs both (or the agent) is how to have both;
- otherwise (a `package.json` that is only build tools or scripts) the Python app is started,
  from the environment its install makes.

A monorepo (a `package.json` with `workspaces`, or a `pnpm-workspace.yaml`): the studio looks
only at the top of the folder. It installs there, starts what the top-level files show (often
nothing), and a note says it holds several packages and that the person types the start
command or asks the agent.

A safe copy never takes `.venv`. When the detected Python app would start from the folder's
own `.venv` and nothing in the folder can make it again (no `uv.lock`, `requirements.txt` or
`pyproject.toml` with `[project]`), a note says, before anything is created, that a safe copy
of this app will not start and why, and what to do: edit it in place, or add a
`requirements.txt` and check again. The studio does not guess a package list. The copy can still
be made; when its app is started anyway (any app whose command starts with `.venv/bin/`, has no
install step and has no `.venv`), the app log says first, in a `[studio]` line, that the folder
has no `.venv` and what to do.

## Attach

`POST /api/projects/attach` → `Project` (201).

```json
{"path": "/Users/me/dev/site", "mode": "copy", "name": "Site (copy)",
 "run": "exec npx next dev -p $PORT -H 127.0.0.1", "address": null,
 "env_files": [".env.local"], "include_uncommitted": false}
```

- `mode`: `copy` (default) or `in_place`. A refused way in → 400 `folder_refused` with the
  reason from the inspection; a missing folder → 400 `folder_not_found`.
- `run`: the start command; absent → the detected one; `""` → none.
- `address`: only with `in_place`. `http://` on `127.0.0.1`, `localhost` or `[::1]` with a port,
  nothing else (400 `address_refused`: the studio proxies what it shows, and a proxy that
  forwarded to other machines would let a page reach them). With an address, `run` is ignored.
- `env_files`: names from the inspection to copy into a safe copy (ignored in place, where the
  files are already there). Never versioned.
- `include_uncommitted` (copy of a git folder only): also take the original's uncommitted
  changes and untracked files into the first version. Default false: they are reported, not taken.
- `name`: default "<folder name> (copy)" or "<folder name> (in place)".

### Key files

One rule says which files hold keys, for attach, ship, templates and export alike
(`keyfiles.py`). A path is a key file when any part of it is one of:

- `.env`, `.env.<anything>`, `.envrc` (direnv);
- `.dev.vars`, `.dev.vars.<anything>` (Cloudflare wrangler's local secrets);
- `.netrc`, `.pypirc` (passwords for servers and package indexes);
- `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` (SSH private keys).

A folder with such a name counts too (a virtualenv called `.env`). Not on the list, on purpose:
`.npmrc` (usually a registry address; a token in it is normally read from the environment),
`*.pem` and `*.key` (public certificates and Keynote files share those endings) and `*.pub`.
Refusing those would refuse ordinary work; keep a key in one of them out of an attached copy
yourself.

### Safe copy

- A git folder becomes `git clone --no-hardlinks <path> workspace` (the app's whole history
  comes along), checked out on a new branch `studio/<name>-<id>` at the original's current
  commit. The clone's `origin` remote is removed, so nothing in the copy can push to the
  original; step 3l moves work back by fetching from the copy. A plain folder becomes a copy
  of the files its `.gitignore` keeps (without `node_modules`, `.venv`, build output and
  caches) in a new git repository.
- Refused above 2 GB (history plus files) or 50,000 files for a git folder, and above 500 MB or
  20,000 files for a plain folder.
- Key files (at any depth) reach the copy only when ticked
  in `env_files`; unticked ones are left out of the copied files, of the uncommitted changes and
  of the untracked files that `include_uncommitted` takes, even when the folder's `.gitignore`
  does not list them. (A key file the original's git already tracks comes with its history, as
  committed; its uncommitted changes are not taken.)
- `studio.json` (`{"run": …, "restart": "never"}`, when there is a command), the key-file
  names above as patterns, `node_modules/`, `.venv/` and `__pycache__/` are listed in the copy's `.git/info/exclude`:
  they never enter a version, so the studio branch holds only the app's own files.
- Files kept in Git LFS are the small pointer files in the copy, as committed; the studio runs
  no LFS program and does not fill them (not built). Inspection's note says so beforehand.
- The import is the first version (`kind` `init`, message "Import from <path> at <sha7>"),
  made even when nothing changed. Versions list only that commit and what came after it;
  restore and version diffs refuse anything earlier (404 `version_not_found`).
- When there is an install command, the app's first start runs it before `run`, in the same
  log (`app.log`); the app stays `starting` for up to 15 minutes while it installs. A failed
  install leaves the app `crashed` with the install's output; restart tries again.

### Edit in place

- The project's working folder is the given folder. The studio's versions are a separate git
  directory, `projects/<id>/history.git`, with the folder as its work tree: the app's own
  `.git`, index and branches are never read for writing or changed by the studio. Its first
  version is the folder as found. Its `info/exclude` lists `node_modules/`, `.venv/`,
  `__pycache__/` and the key-file names; the app's own `.gitignore` files apply too.
- Refused for: the home folder, a disk root or mount point, a folder that contains or is inside
  the studio's data folder or the studio's own repository, a folder another project already
  edits in place, and a folder that is not both readable and writable.
- Restore rewrites the person's files in that folder (that is what in place means); it never
  goes back past the first version.
- No `studio.json` is written into the folder. The app is declared in the project record
  (`app_config`, below). Deleting the project moves only `projects/<id>` to the trash; the
  folder is never deleted or moved.
- The zip download and the file list take the files the studio's history would keep (tracked
  plus untracked, minus ignored), not everything in the folder.
- What the studio itself saves for a turn (chat attachments, drawings, sources the agent
  fetches) is written into the folder like any other file, under `attachments/` and `sources/`.
  The app's own `git status` shows them as untracked.
- Templates: an attached project (either way in) cannot be saved as a page or app template
  (400 `attached_app`: a template would copy the person's app and its env files into the
  studio), and cannot follow a template pattern (the pattern would be written into the app's
  folder as `template/`).

### The app of an attached project

`Project` gains:

```json
"attach": {"mode": "copy", "path": "/Users/me/dev/site", "kind": "git", "framework": "next",
           "branch": "main", "started_from": "<sha>", "studio_branch": "studio/site-1a2b3c4d",
           "import_commit": "<sha>", "uncommitted": [{"path": "a.txt", "status": "modified"}],
           "included_uncommitted": false, "env_files": [".env.local"],
           "env_skipped": [{"name": ".env", "reason": "it is a link, and the studio never copies links"}],
           "install": "npm ci",
           "address": null, "attached_at": "…"},
"app_config": null
```

`attach` is null for other projects. `kind` is `git` or `folder`; `branch`, `started_from` and
`studio_branch` are null for a plain folder; `studio_branch` is null in place.
`uncommitted` is what the original had at that moment (at most 200 entries). `env_files` is
what was copied; `env_skipped` names every ticked file that was not, with why (always empty in
place).

`app_config` is `{run?, restart?, address?}` or null. When it is not null it is the app's
declaration and `studio.json` is not read. In place it is always set (`{}` when there is
neither a command nor an address). `PUT /api/projects/{id}/app/config {run?, restart?, address?}`
replaces it (same address rule; 409 `not_in_place` for other projects, which use `studio.json`)
and syncs the app; the agent uses this route to set an in-place app up.

A project whose `app_config.address` is set shows that server: the project URL proxies every
request and WebSocket to it, with the bridge injected, exactly as for an app the studio
started. The studio never starts, restarts or stops it. Its App object has
`state: "external"`, `address`, `run: null`, `configured: true`; start, stop and restart answer
409 `external_app`. While that server does not answer, the project URL shows a page saying so
(502). Pages from it carry `<meta name="studio-reload" content="off">` (its own dev server reloads them).

App object additions: `address` (string or null) and `installing` (true while the first-start
install runs).

The agent's project facts say that the project is an attached app, where it came from, which
way in, that nothing outside the project folder may be changed, not to run git commands that
move branches or rewrite history, and (when no start command was found) that the app needs
setting up.


# Ship: make it live (step 3l)

A project brought in by its folder (`attach` not null) can send its change back to the original.
The studio does this, never the agent, and only after the person allows a card that shows
exactly what will be written where. Nothing in this section writes to the original except the
apply and go-back steps of a ship, which run only after Allow.

## The recipe

Kept in the project record as `ship.recipe` (not in the app's files, so a ship cannot be
changed by editing the app):

```json
{"checks": ["npm test"], "method": "branch", "after": ["npm run build", "pm2 restart site"],
 "address": "http://127.0.0.1:3020/", "protected": [".env", ".env.*", "migrations/"],
 "back": null, "timeout": 600, "notes": "read package.json and ecosystem.config.js"}
```

- `checks`: commands run in the project folder (the copy) before every ship. Any non-zero exit
  ends the ship before the card; nothing is written.
- `method`, by how the app came in:
  - safe copy of a git folder: `branch` (default) or `merge`;
  - safe copy of a plain folder: `files`;
  - in place: `none`.
- `after`: commands run in the original folder after the change is applied (not for `branch`).
- `address`: an `http://` or `https://` address fetched with GET after the after-commands
  (following redirects); it passes on a status below 400. Six tries five seconds apart. Not for `branch`.
- `protected`: path patterns. `*` matches across folders; a pattern without `/` also matches a
  file name anywhere; one ending in `/` matches everything under that folder. A ship that
  touches one needs the project name typed on the card.
- `back`: commands run after the studio has put the original back; null means the `after`
  commands again. Not for `branch`.
- `timeout`: seconds per command, 10 to 3600 (default 600). At most 10 commands per list.
- Commands run with `/bin/sh -c` in their own process group, with only the environment an app
  gets (`PATH`, `HOME`, `USER`, `LANG`, …; no studio secrets, no keys), stdin closed, output to a
  file (so a server a command leaves running in the background does not hold the ship), killed
  at the time limit.

The agent drafts the recipe by reading the app and calls `propose_ship_recipe`; the person
confirms it on an approval card (`tool: "ship_recipe"`, the whole recipe as `detail`) every
time it changes, whatever the project's approval setting. Proposing the same recipe again asks
nothing. The person can also edit it in the interface; saving there is their confirmation.

## How each method reaches the original

- `branch`: in the original, `git fetch <copy> refs/heads/<studio_branch>:refs/heads/<studio_branch>`
  (refused when the branch there has commits the copy lacks, or when it is checked out there).
  The original's files and checked-out branch do not change. After-commands and the address
  are not run (nothing in the running app changed). The result says how to merge it
  (`merge_help`). Nothing new = the branch there already points at the copy's version.
- `merge`: the same fetch, then `git merge --ff-only` in the original. Refused, with the reason
  and a next step, when the original is not on the branch the copy started from, has
  uncommitted changes to tracked files, or has moved on (its head is not an ancestor of the
  copy's version). Untracked files in the original are left alone; git refuses a merge that
  would overwrite one.
- `files`: every file changed since the last ship (or since the import) is written into the
  original folder; deleted ones are removed. What each write replaces is kept first in the
  ship's record. Refused when any of those files changed in the original since the copy last
  matched it. Never writes through a symbolic link or into `.git`.
- `none` (in place): nothing moves; the checks, after-commands and address run in the folder.

Git commands that write to the original run with the person's own git configuration but
without their hooks (`core.hooksPath=/dev/null`); after-commands are the place for that.
The studio never pushes to GitHub or any remote. If an after-command does, it is the person's
recipe, shown on the card.

## A ship, step by step

1. Refused (409) when the project is not attached (`not_attached`, 400), has no recipe
   (`no_recipe`), a turn is running (`turn_running`), a ship is running (`ship_running`), there
   is nothing new (`nothing_to_ship`), the original is not in a state the method can take, or
   the ship would carry a key file (`ship_refused`, message with the reason and "Next: …").
   Key files (see "Key files") are never shipped: a copy keeps them out of its versions, so one
   in a ship was forced in or predates that rule.
   - `branch` and `merge` carry every version between the original's commit and the copy's
     into the original's repository, so every one of those versions is looked at, not only the
     end result: a key added in one version and removed in a later one still refuses the ship.
     Removing it in a new version does not help; the way out is a fresh copy, or the change made
     in the original by hand.
   - `files` carries no history, so only the files it would write are looked at.
   - Not refused: a key file the original's repository already tracks at the ship's base (his
     own file, changed like any other), and the usual key-free names `.env.example`, `.sample`,
     `.template`, `.dist` and `.defaults` (committed on purpose, edited with every new setting).
     The copy side stays cautious: those names still reach a copy only when ticked.
   - `none` (in place) moves nothing, so nothing is looked at.
2. The workspace is saved as a version ("Changes before making it live"); the ship works from
   that saved version (`head`), never from files on disk.
3. Checks run in the copy.
4. The card (`ship_ready`): the files that will change, the check results, exactly what will be
   written where (`writes`), the commands and the folder they run in, the address, protected
   paths touched, how going back works, and the limits that apply to its method. The person
   allows or denies, every time. `method_label` is the same name the recipe editor uses for the
   method. Counts are written in words that fit the number ("1 file", "2 files").
5. On Allow the plan is made again; if the original, the branch or the copy changed while the
   card was open the ship ends `refused` and nothing is written.
6. Apply; then the after-commands in the original; then the address.
7. When a step after apply fails, the studio goes back by itself: `merge` resets the original's
   branch to where it was and puts the studio branch back (or removes it); `files` puts back
   every file it wrote or removed. Then it runs `back` (or the after-commands). This holds when
   applying itself fails too: the after-commands then run for the first time, so that what is
   live matches the folder, and the message says so; it says "ran again" only when they had
   already run in this ship. A card whose recipe has `back` but nothing after the change says that
   a failed apply runs those commands. A `branch`
   ship that fails while the branch is fetched only puts the studio branch back (or removes it):
   no command of its recipe runs, then or ever, and the message does not say one ran. Going back
   never discards what the studio did not put there, because the person may work in his folder
   while the after-commands run:
   - `merge` resets only when the original is still on its branch at the version the ship left
     there, and with `git reset --keep`, which keeps every edit and untracked file and refuses
     when a file the ship changed has been edited since (by the person or by an after-command).
     What was staged is kept but no longer staged, and the result's message says which files
     (not only the record's `back` lines). When the person has committed or
     switched branches since, or the reset refuses, nothing is reset: the ship ends `back_failed`
     with the head before the ship and now, or the files in the way, for him to settle by hand.
   - The studio branch is put back only while it still points at what the ship fetched and is
     not checked out (otherwise it is left, and the message says so).
   - `files` puts back a file only when it still holds what the ship wrote (or already holds
     what was there before); a file edited since, or that has become a link, is left as it is
     and named in the message, and the other files are still put back.
   The same rules hold when a ship cut off by a restart is put back at start-up. Objects fetched into the original's `.git` stay
   there, unreferenced (git removes them in its own time). `none` has nothing to put back.
8. A record is written: `projects/<id>/ships/<ship_id>/record.json` (with `logs/` holding each
   command's full output and `backup/` the files a `files` ship replaced).

While a ship runs, turns are refused with 409 `ship_running` (a message sent with `queue: true`
waits and runs when the ship ends), and so are file writes, uploads, inline edits and restore.
"Make it live" asked for by the agent during a turn starts when that turn ends (not after Stop,
also a Stop pressed while the turn was finishing, or a failed turn). A studio restart during a ship ends it `interrupted`; a change that was
already being applied is put back at start-up, without running the after-commands again
(the record says so). Limits, stated on every card, only those that fit its method:
- `merge` and `files`: going back restores the branch (`merge`) or the files (`files`) only;
  data and database changes are not undone;
- `merge`, `files` and `none`: where the change ends up is whatever the after-commands do, and with
  none in the recipe, nothing is restarted or sent anywhere;
- every method: the studio itself never pushes to GitHub or any remote (for `branch`: the branch
  stays in the original's repository until he merges or deletes it).

The `message` of a finished ship says only what happened:
- `shipped`: where the change is, and whether after-commands ran and the address answered
  (nothing about them when the recipe has none);
- `rolled_back`: what failed, that the original was put back, files that are kept but no longer
  staged, and whether the after-commands ran again;
- `back_failed`: what failed, what going back did put back, what it left and why, and what to do
  by hand. A command to run is written between backticks; the interface shows it as code.

Known limit until containers (Step 4): the agent's shell can reach the studio's API on
loopback, so it could answer a card by calling the route itself. The card stops a page or an
ordinary mistake, not an agent set on getting round it.

## Ship record

```json
{"id": "s_1a2b3c4d", "project": "p_…", "by": "person", "turn_id": null,
 "started_at": "…", "finished_at": "…", "phase": "done",
 "result": "shipped", "message": "…",
 "recipe": {…}, "attach": {"mode", "path", "kind", "branch", "studio_branch"},
 "plan": {"method", "base", "head", "before": {"head", "branch", "ref"}, "where", "writes"},
 "files": [{"path", "status"}], "files_more": 0,
 "checks": [Command], "decision": {"decision": "allow", "at", "by": "person", "protected": []},
 "applied": ["…"], "after": [Command], "address": {"ok", "address", "status", "attempts": ["…"]},
 "back": ["…"], "back_after": [Command]}
```

`by` is `person` (the button) or `agent` (asked for in chat). Command:
`{command, exit_code, output, duration_ms, timed_out}`; `output` keeps the last 8 KB.
`result`: `shipped`, `denied`, `checks_failed`, `refused`, `rolled_back`, `back_failed` (the
studio could not put everything back: the message says what was put back and what to check),
`failed`, `interrupted`. The interface labels `shipped` "Live" only when after-commands ran or the
address answered; otherwise by what happened ("Branch left", "Merged", "Files copied", "Done").
The top-bar button uses the card's words while a ship waits ("Allow and leave the branch", "Allow
and merge", "Allow and make it live", …) and while it runs ("Leaving the branch…", "Merging…",
"Copying the files…", "Making it live…" only when after-commands or an address follow).

## REST

| Method and path | Body | Result |
|---|---|---|
| `GET /api/projects/{id}/ship` | | `ShipView` (404 `not_attached`) |
| `PUT /api/projects/{id}/ship/recipe` | `{recipe}` | `ShipView`. 400 `invalid_recipe`; 409 `ship_running` |
| `POST /api/projects/{id}/ships` | | 202 `{ship_id}`; refusals above |
| `GET /api/projects/{id}/ships` | | `[ShipRecord]`, newest first |
| `GET /api/projects/{id}/ships/{ship_id}` | | `ShipRecord` |
| `POST /api/projects/{id}/ships/{ship_id}/decision` | `{decision: "allow"\|"deny", confirm?}` | 204. 404 `not_waiting`; 400 `confirm_needed` when a protected path is touched and `confirm` is not the project name |
| `POST /api/projects/{id}/ship/update-copy` | | `{updated, message}`. Safe copy of a git folder only: fetches the original's branch into the copy (reading the original only) and merges it as a `user` version. 409 `update_conflict` (nothing changed; the message names the files) |

`ShipView`: `{available, methods, recipe, confirmed, confirmed_by, confirmed_at, last, running,
card}`. `recipe` is a default (not confirmed) until one is saved; `running` is the ship id or
null; `card` is the card while one waits for an answer. `Project.ship` is the same object for
an attached project, null otherwise. `last`: `{ship_id, result, at, head, method}`.

## Events

All with `turn_id: null`.

| `type` | Extra fields | Meaning |
|---|---|---|
| `ship_recipe` | `recipe`, `confirmed_by` | The recipe was saved |
| `ship_started` | `ship_id`, `by`, `method`, `base`, `head`, `files`, `files_more` | A ship began (after the refusal checks) |
| `ship_step` | `ship_id`, `step`, `state`, and per step: `index`, `command`, `exit_code`, `output`, `duration_ms` (commands); `lines` (apply, back); `address`, `attempts` (address) | Progress. `step`: `check`, `apply`, `after`, `address`, `back`, `back_after`. `state`: `running`, `ok`, `failed` |
| `ship_ready` | `ship_id`, `card` | The card; waits for the decision |
| `ship_decided` | `ship_id`, `decision` | |
| `ship_finished` | `ship_id`, `result`, `message`, `merge_help` | Always exactly one per `ship_started` |

## Studio tools

- `propose_ship_recipe {checks, method, after, address, protected, back, timeout, notes}`: asks
  the person as above; returns the saved recipe or an error when it was not accepted.
- `make_live {}`: during a turn, asks the studio to ship when the turn ends (error when there is
  no confirmed recipe or the project is not attached). The agent is told never to run deploy,
  restart, push or copy commands against the original itself.

`TurnRequest` gains `studio_tool(name, args)`, used by the fake harness to call these tools
(`"propose ship recipe {json}"` and `"make it live"` in its scripted requests).


---

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

# Step 3o: a library that picks for itself

Supersedes, in "Skills, agents and MCP servers per project": skills found on this machine are
no longer "off until switched on"; `GET /api/library` no longer lists them (`skills` there is
the studio's own, the ones added in the studio, and the agent program's built-in ones); the
project library object and its `PUT` are as below.

## What is found (the index)

Read-only, from the person's home folder (`Settings.home`); nothing is written there.

| Found | Where | Kept |
|---|---|---|
| The studio's skills, and skills added in the studio | `library/skills/`, `data/skills/` | always loaded unless switched off in a project |
| Personal skills | `~/.claude/skills/<name>/SKILL.md` | source `machine` |
| Plugin skills | every `SKILL.md` up to four folders down in each plugin's `installPath` from `~/.claude/plugins/installed_plugins.json` (folders named `deprecated`, `template(s)`, `node_modules`, `.git` skipped). Without that file: the newest version folder of each plugin under `~/.claude/plugins/cache/<marketplace>/<plugin>/` | source `plugin`, `origin: "Plugin <name>@<marketplace>"`, `enabled` from `enabledPlugins` in `~/.claude/settings.json` |
| Helper agents | `library/agents/*.md`, `~/.claude/agents/*.md`, each plugin's `agents/*.md` | listed only. The studio's own two are the only ones loaded (a choice: see below) |
| Connectors | added in the studio; `mcpServers` in `~/.claude.json`; each plugin's `.mcp.json` (or the file or object its manifest's `mcpServers` names); an attached app's own `.mcp.json` in its project folder | sources `yours`, `machine`, `plugin`, `app` (and `suggested`, as before) |

- Only a skill's folder and a connector's settings are taken. A plugin's hooks, commands,
  permission rules, settings and scripts outside skill folders are never read or run, and
  `setting_sources=[]` and `strict_mcp_config` stay.
- A skill with the same folder name and description in several plugins is offered once
  (`duplicate_of` on the others; they are not listed).
- In a plugin connector's settings `${CLAUDE_PLUGIN_ROOT}` becomes the plugin's folder and
  `${VAR}` / `${VAR:-default}` are read from the studio's environment. A variable that is not
  set makes the connector `needs-setting`; it is then never picked.
- The index (name, label, description up to 600 characters, source, origin, enabled, size in
  bytes, file count) is cached in `data/library-index.json` and rebuilt when
  `installed_plugins.json`, `settings.json`, `~/.claude.json`, a skills folder or one of the
  studio's or personal `SKILL.md` files changes (checked at most twice a second).
  `POST /api/library/refresh` rebuilds it now.

Skill ids: `studio:<name>`, `yours:<name>`, `machine:<name>`, `plugin:<plugin>@<marketplace>:<name>`.
Connector ids: `mcp_…`, `machine:<name>`, `plugin:<plugin>@<marketplace>:<name>`, `app:<name>`, `suggested:<name>`.

## Per request: what is picked

Before every turn (`turn_started`, then one `library_picked`, then the agent starts):

1. Always in: the studio's own skills and the ones added in the studio (unless switched off in
   the project); every skill and connector the person said "always" for, in this project or
   for the whole machine; up to eight skills the agent used earlier in this project; connectors
   the agent asked for in an earlier request of this project.
2. Never in: anything the person said "never" for (project first, then machine); skills of
   plugins that are switched off in Claude Code (they can still be found, pinned or switched
   on by the agent); a connector that needs a setting, needed signing in or failed the last
   time it was tried (see "Signing in" below); an attached app's own connectors unless pinned (its `.mcp.json` came with
   the code, and a stdio connector runs a command the moment it is connected); a skill that
   was picked `STUDIO2_LIBRARY_UNUSED_AFTER` times in a row (5) without being used; a skill
   whose folder name is already taken by one of the studio's own skills or by a benched one
   (skills load by folder name and the first of a name wins, so one skill per name is offered).
3. When 40 or fewer skills are left to pick from, all of them are loaded and nothing is chosen
   (`mode: "all"`).
4. Otherwise a word search over names and descriptions (stemmed words, rarer words count more,
   a name match counts three times a description match, a small table of everyday synonyms)
   keeps the best 60. One call to a cheap model (the `haiku` alias through the agent program;
   no tools, settings or connectors) is given the request, those 60 and up to 40 connectors,
   and answers with at most 25 skills and as many connectors as there is room for under eight,
   one line of reason each (`mode: "model"`, `route: "connection"`).
   **One provider per request** (the owner, 12 November 2026): this call, like every call the
   studio makes for a request, goes through the connection the request's turn runs on and
   nowhere else. No request text is sent to another provider; in particular a request on a
   gateway or a key never uses the Claude plan, and a request on the plan uses the plan. The
   studio makes no other side call with a model for a request: names are offered without one,
   and "Check this model" runs on the connection being checked.
   Which model answers (`picking_model`): Haiku on the Claude plan, an Anthropic key and the
   cloud accounts (or `model_ids.haiku`); the small model of the studio's own LiteLLM; on an
   `anthropic-gateway` the model its settings name (`small_model`, or `model_ids.haiku`),
   because `haiku` is not a name a gateway routes by itself. A connection with none is not
   asked: the word search decides (5) and the `note` says so, the first time with the fix
   ("This connection has no cheap model for picking; add `small_model` to it …"). A cheap
   model that failed or ran out of time is not asked again for 10 minutes; a success clears
   that. `studio doctor` lists, per model account, which model picks skills or that none is
   set (a warning with the fix); the Library's Models block shows the same line
   (`picking_model` on each connection of `GET /api/models`, a model name or null); setup asks
   for the cheap model when a gateway is added and, left empty, takes the cheapest-looking of
   the models listed and says which.
5. When that call fails or takes longer than `STUDIO2_PICK_TIMEOUT` seconds (6), when the
   connection has no cheap model, or under the
   fake agent, the word search alone decides (`route: "words"`): up to 5 skills scoring at least 45% of the best
   match that also match at least a third of the request's own words (two at least; one for a
   one-word request), and up to three connectors whose own name appears in the request, or
   whose description matches the request's words by the same share rule as skills
   (`mode: "words"`, with a `note` saying why). A connector's own name is the one in its
   settings: the plugin name the studio appends when the name is taken (`slack-legal`) is not
   matched, so a word of the plugin's ("legal") never picks a plugin's connectors. A skill that
   is picked never brings its plugin's connectors with it. Shared words do not show relevance, so a long
   request usually gets no skill this way and the `note` says none was added; the agent still
   has `search_library` and `activate_skill`.

   **Signing in.** A connector is `needs-auth` when the agent program reported it so (it could
   not be used in a turn because it needs signing in), or when its settings carry `oauth` and it
   has never been seen to connect. Such a connector is never offered to the picking model and
   never picked for words of its description. A never-tried one whose own name is in the request
   is picked (its `reason` says it may need signing in), so the first try shows whether it does.
   One that was reported `needs-auth` is left out even when named, until it is seen `connected`
   (pinned or switched on in a project, or a successful test); the
   pick's `note` then says once, in one sentence, which named connectors were left out and the
   command that signs them in. A connector the agent asked for (`request_connector`) that is
   `needs-auth`, reported so or never seen connected, is not added either: it stays in the
   project's `library.requested` and joins the first request after it is seen `connected`, and
   until then the same sentence of the `note` names it.
6. The skill list is kept inside the agent program's budget for it: 8,000 characters (1% of a
   200,000-token context window at about four characters a token). The studio measures names
   plus descriptions plus eight characters a skill; when it would overflow, the descriptions in
   the per-project copies are shortened evenly (never below 60 characters), and only then are
   the last-picked skills left out. The originals are never changed.
7. A connector the studio picked by itself (`how: "picked"`) asks the person before its first
   tool call in that request (an ordinary approval card, "Use the connector <name>"), unless
   the project's approvals are set to allow. Pinned and asked-for connectors do not ask.

`library_picked` (a turn event; also kept as the project's `last_pick`, with `at`):

```json
{"type": "library_picked", "mode": "model" | "words" | "all" | "none",
 "skills": [{"id", "name", "label", "source", "origin", "how": "pinned" | "earlier" | "all" | "picked", "reason"}],
 "connectors": [{"id", "name", "label", "source", "origin", "how": "pinned" | "requested" | "picked", "reason",
                 "state": "ok" | "needs-auth" | "failed" | "needs-setting" | null, "fix": "…" | null}],
 "library_skills": 490, "considered": 60, "loaded_skills": 31, "left_out": 0,
 "list_chars": 7412, "budget_chars": 8000, "description_cap": 180,
 "ms": 2140, "total_ms": 2390, "note": "…" | null,
 "route": "connection" | "words" | null}
```

`route` is where the choice was made: the request's own connection (4 above) or the word
search; null when nothing had to be chosen.

`skills` lists only what was added on top of the studio's own; `loaded_skills` counts both.
`ms` is the picking itself, `total_ms` includes copying the skill folders.

The project facts sent with every message say how many skills and connectors the library
holds, which were picked for this request, that the pick can be wrong, and that the agent
should search and switch on what is missing.

## During a request: three studio tools

| Tool | Arguments | Result |
|---|---|---|
| `search_library` | `{query, kind?: "skill" \| "connector" \| "all", limit?}` | `{skills: [{id, name, description, from, loaded}], connectors: [{id, name, description, from, connected, state?, fix?}], next}`. Whole index, switched-off things left out |
| `activate_skill` | `{id}` (or a bare name) | `{skill, id, folder, instructions, files, more_files, next}`. The skill folder is copied into the project's plugin folder (`data/projects/<id>/library/skills/<name>/`, readable by the agent, outside the project folder) and the body of its `SKILL.md` (40,000 characters at most) comes back in the answer. It counts as a use and the skill stays loaded for later requests of the project. Refused for a switched-off skill and for one above 20 MB |
| `request_connector` | `{id, reason?}` | `{connector, connected: false, joins: "the next request", next, sign_in?}`. A running agent program cannot be given a connector, so the id is added to the project's `library.requested` and the connector joins the next request (`how: "requested"`), for the rest of the project or until the person says never. `connected: true` when it is already there. Refused for a switched-off connector and for one that needs a setting. For one that is `needs-auth`: `joins: "after signing in"`, `sign_in` holds the command, `next` tells the agent it is not connected until the person signs in, and the request gets a `notice` (`warning`) that says so to the person |

## What the person says, and what is counted

- Project: `PUT /api/projects/{id}/library` with `{"skills"?: {id: true | false | null}, "mcp_servers"?: {…}}`.
  `true` = always in this project, `false` = never in this project, `null` = leave it to the
  studio. Choices made by hand before this step keep their meaning: they are pins (`true`) and
  switch-offs (`false`). At most 12 pinned connectors, and every one of them is connected for a request (the
  smaller limit of 8 is for what the studio adds by itself). A connector pinned for the whole computer
  that does not fit is named in the pick's `note`, never dropped silently.
- Whole machine: `PUT /api/library/prefs` with `{"skills"?: {id: "always" | "never" | null}, "mcp_servers"?: {…}}`
  → the preferences (`data/library-prefs.json`). The project's word wins over the machine's.
- `data/library-usage.json`, per skill id: `picked` (requests it was picked for by the model
  or the word search, counted when the turn completes), `used` (requests in which the agent
  called it: the agent program's `Skill` tool call, or `activate_skill`), `streak` (picked
  without use, in a row; any use, or an "always" or back-to-automatic from the person, resets
  it). At `streak` ≥ 5 the skill is `benched`: no longer picked, still found by search.
- What the agent program reported for each connector at the start of a turn (`connected`,
  `needs-auth`, `failed`) is kept in `data/library-status.json` and shown as `state`; for
  `needs-auth` the `fix` is the command `claude mcp login <name>`. A later report that says
  neither (`pending`, for one still connecting) keeps what was known, so `needs-auth` and
  `failed` last until the connector is reported `connected`. A successful test
  (`POST /api/mcp-servers/{id}/test`, any source) counts as `connected`. The same file keeps
  each connector's tools: names from the start-up report (`mcp__<name>__<tool>` among the
  agent program's tools), names and descriptions from a test; a report of the same names keeps
  the descriptions already known. They make the `description` of a connector that has none.

## REST

| Method and path | Returns |
|---|---|
| `GET /api/library` | as before, plus `index` (below) and `prefs`; `skills` without the machine's and plugins' |
| `GET /api/library/items?kind=skills\|agents\|connectors&q=&source=&state=&offset=&limit=&project=` | `{items, total, offset, limit, counts: {source: n}, index}`. `counts` are per source before the `source` filter is applied. `limit` ≤ 200 (default 50). `q` is the same word search (plus a plain match on part of a name); without it the order is source, then name. `state`: `always`, `never`, `benched`, `signin`. Skill items carry `pref`, `choice` (when `project` is given), `picked`, `used`, `benched`; connector items are `McpServer` plus `origin`, `state`, `fix`, `missing`, `plugin_enabled`, `pref`, `choice` |
| `PUT /api/library/prefs` | the preferences; 400 for an unknown id or word |
| `POST /api/library/refresh` | `index` after a rebuild |
| `GET /api/projects/{id}/library` | `{skills: [{…skill, enabled, choice, pref}], mcp_servers: [{…McpServer, enabled, choice, pref}], requested: [ids], last_pick}`. `skills`: the studio's own and added ones, plus every other skill the person decided about. `enabled`: loaded without being picked |

`index`: `{skills, agents, connectors, by_source: {skills: {studio, yours, machine, plugin}, agents: {…}, connectors: {…}}, plugins, plugins_enabled, duplicates, built_at, build_ms}`.

## Choices made where the plan left them open

- Helper agents from the person's settings and plugins are indexed and shown, not loaded: an
  agent file can name tools, a model and hooks of its own, which is more than "skill folders
  and connector settings".
- Skills of plugins that are switched off in Claude Code are indexed and can be pinned, found
  and switched on, but are not picked automatically.
- One budget for every model: 8,000 characters. A model with a larger window has more room
  than the studio uses.
- A connector asked for mid-request stays for the rest of the project, and a skill switched on
  mid-request stays among the eight "used earlier" ones, so a conversation does not lose what
  it was working with when the next message is short.
- The studio's own picks of connectors ask before first use; nothing else about approvals changed.

## Cost per request and per project

The studio shows the cost a route reported whenever there is one. When a LiteLLM route sends no cost
for a request, the studio works one out itself as a **backup** (token counts times the model's rate,
below) and says so. It never shows a request nobody could price as free. Every money figure says
where it came from (`basis`):

- `reported`: the provider sent a cost with the answer and the route recorded it. Today only
  OpenRouter does this (LiteLLM keeps its `usage.cost`).
- `calculated`: token counts times a price list, worked out by LiteLLM (OpenAI, Azure OpenAI,
  Gemini and anything else that returns only tokens) or by the agent program (an Anthropic key,
  Bedrock, Vertex, Foundry). Shown with the word "calculated".
- `backup`: at least one request of the turn was priced by the studio itself, because the route sent
  no cost for it. Shown as "calculated by the studio", with how many requests and whose rates.
- the plan-equivalent below, which is the same arithmetic for a route that is not billed per request.

Response headers are not a source: the agent program makes the model calls, so the studio never sees
them, and LiteLLM's `/v1/messages` route sends no cost header for the streamed answers the agent
program asks for (checked on LiteLLM 1.102.1 on 13 November 2026).

Three kinds of figure exist and are never added together:

| Kind | Where it comes from | Shown as |
|---|---|---|
| **Charged** | The money figure of a route that bills: the studio's own LiteLLM (`litellm`, OpenRouter and the other accounts), a gateway the person runs (`anthropic-gateway`, read back from its spend log), `anthropic-api` and the three clouds (the agent program's figure) | `$4.45` |
| **Plan-equivalent** | The subscription: what the tokens would have cost at API prices. `cost_estimated: true` | `≈ $1.02 at API prices`, never a bare dollar figure, and not in the charged total |
| **Unpriced** | A request the route reported no price for (a model LiteLLM has no price for, or a gateway that does not report spend). It is counted, never shown as `$0` | `3 requests unpriced` |

**One turn's part of a running total.** The agent program reports what the whole conversation has
cost so far (`total_cost_usd` is a running total for the session), not what one turn cost. So on
every connection whose figure comes from the agent program (the subscription, `anthropic-api` and the
three clouds) `usage.cost_usd` is that total less what the same conversation had reached at the end of
the turn before, and `usage.session_cost_usd` carries the total itself. The project record keeps
`session_cost: {session_id, total_usd}` for the next turn. A new conversation starts from nothing.
A change of connection or model resumes the same conversation, and the agent program's total keeps
running through it. So on the two LiteLLM kinds too `usage.session_cost_usd` carries the program's
total and `session_cost` is updated, although the turn's own figure is the gateway's and the
program's total is never used as its price: otherwise the turn after a gateway turn would be counted
the gateway turn's growth of the total as well.
Turns written before 13 November 2026 have the running total in `cost_usd` and no
`session_cost_usd`; the project total and the chat work their part out the same way when they read
them. Until that day the chat showed the running total as each turn's cost.
Gateway turns (`litellm`, `anthropic-gateway`) written before 17 November 2026 come in two shapes:
- written before Step 2 (10 November 2026), with no `cost_estimated` at all: `cost_usd` is the agent
  program's running total. When it is above 0 the project total and the chat take it as the
  conversation's mark, so the turn after is not counted that turn's growth (a 0 says nothing: it is
  not taken as a mark). The turn itself stays unpriced, as every turn of that time does.
- written from Step 2 until 17 November 2026: the studio dropped the program's total, so the log
  holds nothing to work the gateway turn's growth out from (its token counts are not enough: the
  growth is the agent program's own arithmetic, at prices the studio does not have for every model
  it reaches through a gateway). A turn after one of these in the same conversation still includes
  that growth in its own part. The studio does not guess it.

**Tagging.** On an `anthropic-gateway` connection the agent program sends one extra request
header, `x-litellm-tags: studio-turn:<turn id>` (through `ANTHROPIC_CUSTOM_HEADERS`). The turn id is
random; no project name, path, prompt or key goes in it. LiteLLM records the tag in the request's
`request_tags`. The library's choosing call (a request of the same turn on the same connection)
carries the same tag, so it is counted in the turn. On the studio's own LiteLLM the header is
`x-studio-turn: <turn id>` as before. A gateway that does not understand the tag ignores it.

**Reading it back (`anthropic-gateway`).** After `turn_finished` the studio reads the gateway's
spend log, read-only, with the key the connection already holds: `GET <base_url>/spend/logs/v2`
(`start_date`, `end_date` in UTC, `page_size` 1000, paged), and keeps the rows whose `request_tags`
contain `studio-turn:<turn id>`. Nothing else of a row is kept: the cost (`spend`), the status and the
token counts. The gateway writes its log every ten seconds (LiteLLM's default), so the studio waits
twelve seconds, then reads again every three seconds until two reads in a row agree, for at most a
minute. A row is `reported` when its `metadata.usage_object.cost` is above zero, otherwise
`calculated`; a turn is `reported` only when every priced row is. Only rows with `status: success` count. A row with a `spend` above zero is priced; any other
successful row is **unpriced**. The turn's `turn_finished.usage` carries `cost_pending: true` until
the read-back ends, and the interface says "cost pending". A studio that restarts before the read-back
ended picks it up again at start-up (the tag is in the gateway's log whenever it is read).

`turn_cost` event: `{turn_id, cost_usd (number or null), requests, unpriced, source, reason, basis}`
(`basis`: `reported`, `calculated`, `backup`, or null when nothing was priced). `turn_finished.usage` carries
`cost_basis` the same way, with `plan` for the subscription.
`source` is `gateway` (read back by tag), `recovered` (see below), `tokens` (the backup, from the turn's own token counts) or `unavailable`. When it is
`unavailable` `cost_usd` is null and `reason` says why in a sentence for the person: the gateway
cannot be reached; it answered 401 or 403 (the key cannot read the spend log); it answered 404 (it
is not LiteLLM, or it keeps no database); it answered something else; or it had no request tagged
for this turn. `cost_usd` is null whenever no request was priced, even if `requests` is above zero.
The latest `turn_cost` of a turn replaces the `cost_usd` of its `turn_finished`.

**Turns from before tagging** have no tag. `POST /api/projects/{id}/cost/recover` looks each
finished gateway turn that has no price up in the gateway's spend log by what the log does record:
the agent program's session id (`turn_finished.session_id`) and the turn's time window (from
five seconds before `turn_started` to ten seconds after `turn_finished`). A row belongs to one turn
only. A row tagged `studio-turn:<id>` is that turn's whatever its time (in the same session; a
gateway's clock can run ahead of the studio's), and never another turn's. The window decides only
for untagged rows: it never reaches into a neighbouring turn of the same session (priced or not),
the gap between two turns going to the earlier one up to the later one's start. It writes `turn_cost` with `source:
"recovered"`. The interface says "found by session and time". It adds nothing for a turn it cannot
match, and never writes a figure for a window in which the log has no row of that session.

**The backup calculation.** It applies to the two LiteLLM kinds (`anthropic-gateway`, `litellm`) and
never to the plan or to a route whose figure the agent program works out.

- *Rates*, per million tokens, for input, output, cache read and cache write, looked up by the
  model's name in two places, in this order: the gateway's own price for the model
  (`GET <base_url>/model/info`, read-only, the same key; an entry whose input and output prices are
  both 0 is not a price, unless the gateway's own settings give it 0 and 0: see free models below), then the list that ships with the studio, `library/model-prices.json`
  (`{as_of, source, models: {"<provider>/<model>": {input, output, cache_read, cache_write, above?}}}`,
  written by `tools/prices/update.py` from OpenRouter's public model list, which carries the
  providers' list prices; `above: {prompt_tokens, …}` holds a model's higher rate for long prompts).
  A name is matched whole, then without a leading `openrouter/`, then by its last part with dots as
  hyphens and without a trailing date (`claude-opus-5-5` is `anthropic/claude-opus-5.5`); a last
  part that fits two listed models matches neither. A `:free` ending is never taken off:
  `qwen/qwen3-coder:free` is not the paid `qwen/qwen3-coder`.
- *A model the provider serves for free* is priced at zero, as `reported`, never calculated: when the
  route reported a cost of 0 for a request (LiteLLM's cost, a spend-log row's `spend`) and the model's
  name ends in `:free` or `/free`, the shipped list gives it an input and an output rate of 0, or the
  gateway's own settings for that model do (`GET /model/info`: `litellm_params.input_cost_per_token`
  and `litellm_params.output_cost_per_token` both exactly 0, a price only the gateway's own
  configuration sets). Both LiteLLM paths (the studio's own and a spend log) use this one rule. A 0
  and 0 that is only in the entry's `model_info` does not make a model free: LiteLLM fills in 0 there
  for a model its price list has no rate for, and its list gives 0 and 0 to some models that are not
  free (in LiteLLM 1.102.1's list, `anthropic.claude-mythos-preview` on Bedrock), so it can mean "no
  price known". Any other reported 0 is LiteLLM having no price: the backup below when the rates are
  known, else unpriced. A request with no cost at all is never taken as free by its name alone; where a
  turn is priced from its own token counts (below) and the gateway's settings or the list give the
  model 0 and 0, that is tokens times 0, shown as calculated by the studio, never the paid rate of
  another model of that name.
- *Complete or not at all.* A source is used for a request only when it has a rate for every kind of
  token the request used (a request that read from the cache needs a cache-read rate). When neither
  source is complete the request stays **unpriced**. Nothing is ever priced at a guessed rate.
- *Cache writes have two prices on Anthropic's models:* an entry written with the 1-hour lifetime
  costs twice the input rate, a 5-minute one 1.25 times (`cache_write_1h` beside `cache_write` in
  the list; the agent program the studio runs writes 1-hour entries). Where a model has both and
  they differ, the request must say how many written tokens were of which lifetime
  (`cache_creation.ephemeral_1h_input_tokens`, kept as `tokens.cache_write_1h`); a request that
  wrote to the cache without saying so stays unpriced.
- *One request of a readable spend log* (a successful row with no `spend`, that the provider did not
  report as free): `prompt_tokens` includes the cached ones, so the cost is
  (prompt − cache read − cache write) × input + cache read × its rate + cache write × its rate +
  completion × output, with the cache counts from the row's `usage_object.prompt_tokens_details`.
- *A turn whose spend log cannot be read* (`source: "unavailable"` before this): the turn's own token
  counts, each model's at that model's own rate, added up (`turn_cost` then has `source: "tokens"`).
  The counts are `turn_finished.usage.models` (next item); a turn from before 19 November 2026 has
  only `usage.tokens`, which is priced at the rate of the turn's model as it was then. A model
  neither source has a complete rate for leaves its part out: the figure then covers the models
  that could be priced, `backup.unpriced_models` names the rest, and the reason says the figure is
  not the whole turn. These counts are summed over the turn's requests, so the size of each prompt
  is not known: on a model with a higher rate for long prompts (`above`) a model's summed prompt
  above that size stays **unpriced**, and one at or below it takes the ordinary rate (no single
  request can have been above). A single request (a spend-log row, the studio's own LiteLLM) is
  checked against `above` by its own size. A figure worked out this way is the provider's list
  price times the tokens: a route that sends a request to a dearer provider of the same model
  charges more, and only the spend log knows.
- *What a turn used, by model* (`turn_finished.usage.models`:
  `{"<model>": {input, cache_read, cache_write, output}}`, input without the cached ones). The agent
  program reports two things with each result, and they are not the same. `usage` is what the main
  agent's own requests used in this one turn. `modelUsage` is a running total for the whole
  conversation, by model, and also holds what helper agents used and the program's own small
  calls; it carries on when the conversation is resumed in a new process. Measured on 19 November
  2026 with one helper agent: `usage` held 17,104 written tokens where `modelUsage` held 28,197.
  The studio therefore keeps the running totals in the project record
  (`session_usage: {session_id, models}`) and writes each turn's part: the totals at the end of the
  turn less the totals the same conversation had reached before it, model by model. A new
  conversation, or a count that went down, starts from nothing. `usage.tokens` is the sum over the
  models, and `input_tokens` and `output_tokens` with it, so a turn's token count includes its
  helpers. The studio's own picking call before the turn (`cheap.py`) is added under the model
  that answered it. Where the agent program reports no `modelUsage`, `usage` is used as before.
- *A turn that ended without a result* (stopped by the person, the process died, a time limit):
  the agent program never said what it used. The studio then keeps what it heard on the way: what
  each of the assistant's messages reported, main agent and helpers, each message once and at
  the largest count it reported (the message itself, and the stream's `message_start` and
  `message_delta`: a translating gateway such as LiteLLM starts every message with zeros and says
  what it used when the message ends). A message that was cut off before its end is missing, and
  the program's own small calls are missing, so `usage.tokens_partial` is `true`, a figure priced
  from them says it is at least that much. The running totals of the conversation still hold
  the cut-short turn when it is resumed (measured: all of it but the answer that was on its
  way), so the project record keeps what was already counted (`session_usage.pending`) and the
  next turn's part is the growth of the totals less that, and never less than what the next
  turn itself was heard using: counted once, not twice.
- *A long-prompt tier holds whichever source has the price.* The gateway's own price carries its
  tier from LiteLLM's fields `input_cost_per_token_above_<N>k_tokens`,
  `output_cost_per_token_above_<N>k_tokens`, `cache_read_input_token_cost_above_<N>k_tokens`,
  `cache_creation_input_token_cost_above_<N>k_tokens` and
  `cache_creation_input_token_cost_above_1hr_above_<N>k_tokens` (`N` is 128, 200, 256, 272 or 512
  in LiteLLM 1.102.1's list; `_priority` and `_flex` variants are service tiers, not sizes, and are
  not read). A kind the tier gives no rate for is unpriced above it. When the source that would
  price a request has no tier but the other source knows one for that model, the size is still the
  other's: a request above it is not priced at the first source's ordinary rate (the next source
  prices it with its tier, or it stays unpriced), and a summed turn above it is unpriced.
- *The studio's own LiteLLM* writes each request's token counts beside its cost; a request it could
  not price is priced the same way from the shipped list.

`turn_cost` and `turn_finished.usage` carry `backup: {requests, usd, rates, as_of} | null`: how many
requests the studio priced, their part of the figure, whose rates (`gateway`, `list` or `both`) and
the list's date when it was used. `unpriced` counts only what neither source could price.
`GET …/cost` adds `backup_usd`, the part of `charged_usd` the studio worked out, and each turn's `backup`.

**Per project.**

| | | |
|---|---|---|
| `GET /api/projects/{id}/cost` | | `{charged_usd, calculated_usd, plan_equivalent_usd, unpriced_turns, unpriced_requests, pending_turns, turns:[{turn_id, at, connection, model, kind, cost_usd, basis, requests, unpriced_requests, source, reason}]}`, summed from `events.jsonl`. `calculated_usd` is the part of `charged_usd` whose basis is `calculated` |
| `POST /api/projects/{id}/cost/recover` | | `{recovered: n, unmatched: n, turns: [...]}`; 409 while a turn runs |

`kind` is `charged`, `plan`, `unpriced` or `pending`. A turn that made no model request (it failed
to start, or the studio restarted during it) is not in the totals. A `turn_finished` without
`cost_estimated` (written before Step 2) is `unpriced`: there is no way to tell an estimate from a
charge. `charged_usd` and `plan_equivalent_usd` are separate sums and are shown separately.

## An app the person stopped stays stopped

`POST …/app/stop` writes `app_stopped: true` in the project record; `start` and `restart` clear it.
When the studio starts, an app with the flag is not started: it is configured and shown as
`stopped`. A turn that changes the project's files still starts it (the person is working on it)
and clears the flag; a turn that changes nothing leaves it stopped. Until 13 November 2026 a Stop
was kept in memory only, so every restart of the studio started every app.

## What an app writes while it runs: `state/`

An app keeps what it writes at run time (a SQLite file, saved settings, uploads it stores) in
`state/` at the project root. New projects ignore `/state/` in their `.gitignore`, and the line is
added to a studio-made project that lacks it when its next turn starts (never to an attached app,
whose ignore file is the person's). What is ignored is not watched and not versioned, so an app
saving its data does not reload the page under the person (no `files_changed`), is not reported to
the agent as a file the person changed, and is not rolled back by restoring a version. `data/` is
for files the agent prepares for the page (a CSV to chart) and stays versioned. A project
downloaded or saved as a template does not carry `state/`. Apps built before this keep their data
where they put it and behave as before.

## Python and packages for apps and for the agent's scripts

- The studio runs on its own environment (`studio2/.venv`); the LiteLLM it runs has another of its
  own. Neither is ever the agent's or an app's.
- Apps and scripts run with `uv run --with <package> …` or `uv run python …`: uv builds one
  environment per set of packages in its cache, shared by every project with the same set, and
  nothing is installed into the project or the machine. A bare `python` or `python3` is whatever
  the machine has (on a Mac, often 3.9): the agent is told not to use it, and an app whose `run`
  starts with one gets a line saying so at the top of its log.
- The agent's shell and a project's app never inherit the studio's own Python: `VIRTUAL_ENV`,
  `PYTHONPATH`, `PYTHONHOME`, `CONDA_PREFIX` and `CONDA_DEFAULT_ENV` are blanked, and the folder of
  the studio's interpreter (and of an activated environment) is taken off `PATH`.
- `studio start` puts the uv the installer fetched (`studio2/.tools/uv`) on `PATH` when the machine
  has no uv of its own, as it already did for Node.
- **Pinned packages.** A Python app lists its packages in `requirements.txt` (names; a version only
  where one is needed) and starts with `uv run --with-requirements requirements.lock …`. Before the
  studio starts such an app it writes `requirements.lock` from `requirements.txt` with
  `uv pip compile` (exact versions of every package, direct and indirect) when the lock is missing
  or older than `requirements.txt`, and says so in the app's log. The lock is a project file, so it
  is versioned and the app runs on the same versions after uv's cache is cleaned or on another
  machine. When locking fails (no network, a package that does not exist) the lines uv printed go
  in the app's log and a lock that was already there is kept. The agent edits `requirements.txt`,
  never the lock, and locks in its own turn after changing it. Apps started with
  `uv run --with <package>` (every app built before 13 November 2026) are not pinned and run as
  before: `--with fastapi` is whatever was current when uv first resolved it.
- **A cost the provider reports as zero is a price.** A request whose OpenRouter answer carries
  `usage.cost: 0` is priced at $0 (`reported`), not unpriced. Checked on 13 November 2026 for the
  two such requests in project `p_288d381d`: OpenRouter's own usage for the key ($5.889394) and the
  gateway's log for the same days ($5.886041) differ by a third of a cent, so they were not billed.


# How much to do, and checking what is on the page (depth and verify)

The person says how much a request should do (**depth**) and whether the result is checked
before the request ends (**verify**). Both are remembered per project and sent with each request.

## REST

- `POST /api/projects/{id}/turns` takes `options: {depth?: "quick"|"standard"|"deep", verify?: boolean,
  check_only?: boolean, polish?: boolean}`. Unknown keys or values → 400 `invalid_options`. Left out:
  `standard`, no verify.
  `check_only: true` marks a request that only verifies the page's content (the **Verify content**
  button): it implies `verify: true`, and the studio picks no skills or connectors by itself for it (see
  "Verification" below). `check_only: false` is the same as leaving it out.
  `polish: true` marks a request about how the page looks (the **Polish the look** button, see
  "Polishing the look" below). `polish: false` is the same as leaving it out; `check_only` and
  `polish` together → 400 `invalid_options`.
- `PATCH /api/projects/{id}` takes `turn_options` (`{depth?, verify?}`, same 400; `check_only` and
  `polish` are properties of one request and are refused here): the project's remembered choice, kept in the
  project record as `turn_options` (`{depth, verify}`; absent = standard, off).
  A request's own `options` are not saved by sending it; the UI saves the choice when it changes.
- `turn_started` carries `options` (`{depth, verify}`, always both keys, plus `check_only: true`
  only for a check-only request and `polish: true` only for a polish request). `turn_queued` carries the options of the queued message, and they
  are the ones it runs with.

## What the agent receives

The options become one `<studio-effort>` block in the composed message, after the text and the
project facts. The wording lives in one place, `server/studio2_server/effort.py`. `standard` with
verify off adds nothing: the message is byte for byte what it was before this step. The wording
describes intent and never promises a length in words or pages.

With `depth: "deep"` the picker also adds the studio's `deep-research` skill to the request
(`how: "depth"`), unless the person switched it off in the project.

## Verification

With verify on, the agent checks everything the person can see on the project's page(s), and the
figures in the project's Office and PDF files ("Verify content reaches files"), after its work, through the `checker` sub-agent (an independent context), fixes what is certainly wrong and
lists the rest, then calls the studio tool **`report_verification`** once:

```
{checked, passed, fixed, failed, unverifiable: integers ≥ 0,
 summary?: string (≤ 400 chars),
 findings: [{what (≤ 200), where (≤ 200), status: "passed"|"fixed"|"failed"|"unverifiable", note (≤ 400)}]}
```

At most 60 findings are kept (failed first, then unverifiable, fixed, passed); counts are kept as
given. It writes one event:

```json
{"type": "verification", "checked": 12, "passed": 9, "fixed": 1, "failed": 1, "unverifiable": 1,
 "summary": "…", "findings": [{"what": "…", "where": "…", "status": "failed", "note": "…"}],
 "findings_total": 4, "findings_cut": 0}
```

A second call in the same request replaces nothing: it writes a second event (the UI shows the
last). If verify was on and the request ends `completed` without a `verification` event, the studio
writes `notice {level: "warning", message: "Verification was asked for and was not reported."}`
before `turn_finished`. Never a silent pass.

What a pass means, said wherever the result is shown: the figure was found in the saved source or
recomputed from the page's own data. It does not prove the source is right. Unverifiable items are
counted, not hidden.

The wording tells the agent to run `check_page` again after its fixes and before reporting, to
report an item as fixed only when that re-check passes, and not to restate the counts in its
closing message (the card has them; the message points to it).

### The studio's own re-check

When a request wrote at least one `verification` event and was not stopped, the studio itself runs the page check
once more after the agent has finished (before the version is saved), on `index.html` when it
exists and on every other page `check_page` was run on during the request. It does not write
`checks/<page>.json` (the agent's last check stays the saved one) and it never sends the agent
back. It runs `check_file` on files too, and the event's entries say which are pages and which are
files: "Verify content reaches files". It writes one event:

```json
{"type": "verification_recheck",
 "pages": [{"page": "index.html", "checked": 37, "verified": 31, "derived": 4, "failed": 2,
            "cited_not_listed": 2, "source_list": true, "error": null}],
 "problems": [{"page": "index.html", "what": "$4.2 billion", "where": "Revenue",
               "why": "The quote is not in the saved text of s3."},
              {"page": "index.html", "what": "Source s27", "where": "",
               "why": "Cited on the page but has no entry in the page's source list."}],
 "problems_total": 4, "problems_cut": 0, "agrees": false}
```

`problems` are the check's failed items and its cited-but-not-listed sources (at most 40 kept,
`problems_cut` counts the rest); `agrees` is true when there are none and at least one page was
read, and no page or file was left out past the caps (`not_rechecked`, "Very large documents"). A page (or file) the check could not read has `error` set and adds no problems. No event when there is no page
or file to check. What the re-check covers is only what the page check and the file check can see:
marked figures against saved sources and project data, cited sources against the source list, and
the figures in the project's Office and PDF files against checked page figures, data and saved
sources. It knows nothing of charts, totals that are not marked, or an app's behaviour; the card
says so.

`check_only` requests: the picker adds only what the person chose (skills and connectors they
said "always" for, here or for the machine). Nothing is added by itself: no `all`, `earlier` or
`picked` skills and no `requested` or `picked` connectors. `library_picked` then has `mode: "none"`
and a `note` saying so.

## Polishing the look

Verification is about what the page says. **Polish the look** is about how it looks and reads: a
request with `polish: true` adds one more part to the `<studio-effort>` block (the wording is in
`effort.py`, like the rest). The agent is told to:

1. look at every page with `look_at_page` at 1440 px wide first (a desktop, where the page is
   mostly read and has to look best), then 820 (a tablet), then 390 (a phone), once more at 1440
   with `dark` when `layout.dark_styles` is true, and at an app's main states;
2. open every tile of each look and read its `layout` list, confirming a measured item in the
   tiles before acting on it;
3. fix what is clearly a defect (text cut off, overlapping or off the screen, a page that scrolls
   sideways, broken or missing spacing, unreadable type, a layout that wastes or overfills a
   desktop window) with the smallest change and in the page's own style, never by hiding content.
   The desktop view comes first and the tablet second; on a phone it fixes what is broken and
   never trades the desktop view for it (the owner, 18 November 2026: the studio is used on a
   desktop, sometimes an iPad, rarely a phone);
4. leave matters of taste alone (another layout, palette or typeface) and offer them as next steps
   instead (`suggest_next_steps`);
5. look again after its fixes, and end with what it fixed, what it looked at and what it could not check.

It adds and removes no content. There is no card and no new event: the answer and the chips carry
the result. The picker works as for any request. When the studio has no browser the agent says it
cannot see the page and changes nothing. A polish request is an ordinary request in every other
way (it can be queued, stopped and undone as a version).

## UI

The composer's bar has one small control beside the model's: it names the depth ("Standard"),
with a tick when the content will be verified. It opens a menu with the three steps **Quick /
Standard / Deep**, each with a few words on what it means (Deep is slower and costs more of the
model allowance or money), **Verify after each request** to switch the content check on or off,
**Verify content now**, which sends a request that only verifies (verify on, no new content; it sends
`options: {depth: "standard", verify: true, check_only: true}`), and **Polish the look now**, which
sends the polish request (`options: {depth: "standard", verify: false, polish: true}`). The chat shows
a `verification` event as a card: counts first, findings expandable, failed ones first. A
`verification_recheck` belongs to the turn's last card and is shown at the top of it: when it
disagrees, "N items the studio's own check still finds wrong" with each one listed, before the
agent's own counts; when it agrees, one short line. Either way one line says what the re-check
covers. The summary is cut (≤ 400 characters) at a sentence or word, never mid-word, and ends
with "…".

## Ports and processes, cleanly

Every app the studio runs gets its ports back cleanly when it stops, and gets the same ports
again when it starts.

- **`studio stop`** ends the studio and returns only when it has gone: the process has exited and
  the studio's port and its first page port can be bound again (up to 10 s after the stop signal).
  If the process will not go, or something still holds the port, it says so plainly with the pid
  (and exits 1). It says "a request that was still running was ended" only when a project had a
  running request.
- **The free-port test** binds with `SO_REUSEADDR`, so a socket left in TIME_WAIT by a studio that
  just closed does not count as "in use". `studio start` takes a block only when the studio port and
  the first page port are both free, and prints both ranges: pages P+1–P+99, back ends P+101–P+199.
- **Pages follow the studio's port.** At start, a project whose page port is outside the current
  page range gets a new port from the range (its `preview_url` follows) and the studio logs that once
  for the project. A project inside the range keeps its port.
- **An app's stop.** The process group gets SIGTERM, then SIGKILL after 5 s. The studio then checks
  (up to about 2 s) that nothing answers on the app's port. If something still does, the app's log
  gets one line naming the holder (pid and command, found with `lsof` where the computer has it, otherwise with `ss` on Linux).
- **An app's start.** If the app's port is held by something the studio did not just start: when
  that process's working folder is inside the project's folder, it is a leftover of the same app and
  is stopped first. Otherwise the app is not started, its state is `crashed`, and its log says
  "Port N is in use by pid P (command), which the studio did not start." A process outside the
  project's folder is never stopped.
- **Restart keeps the port.** A stop and start, or a restart, comes back on the same port, with the
  earlier process group (children included) gone.
- **Deleting a project frees its ports:** the page listener is closed and the app's group is ended,
  so the next new project can take them.
- **The Claude plan connection** is available only when this computer is signed in to a Claude plan
  (`claude auth status`, the same check as `studio doctor`). Otherwise it is unavailable with
  "No Claude sign-in on this computer. Sign in with `studio login`, or use a key." The answer is
  checked in the background and kept for about a minute; until the first answer arrives the
  connection is unavailable with "Checking the Claude sign-in on this computer." Under
  `STUDIO2_FAKE=1` nothing changes.

# Suggested next steps

After a request that completed, the chat can show a few chips under the answer: things the person
might ask for next. A tap puts the chip's text in the chat box. Nothing is sent until the person
sends it, and they can change the text first. No model call is made to produce them: the agent
proposes its own in the request it is already running, and the studio adds its own from what the
request did.

## The agent's part: `suggest_next_steps`

A studio tool the agent may call once, last, when there is an obvious next thing:

```
{steps: [{label (≤ 40 chars), text (≤ 400 chars)}]}
```

`label` is what the chip says ("Add a section on battery life"). `text` is the whole request as
the person would write it; it is what lands in the chat box. White space is folded and both are
cut at a word. A step with an empty label or text is dropped, as is one whose label repeats an
earlier one (letter case ignored). At most 3 are kept. A second call in the same request replaces
the first. `steps` that is not a list → the tool answers with an error. The tool writes no event;
it answers `{kept, cut}`.

The wording for the agent is one bullet in the project facts sent with every message
(`nextsteps.AGENT_RULE`; the agent program loads studio tools on demand, so a rule kept only in the
tool's description is never read): requests it could carry out itself in this project, nothing already done, no questions, no check of the page (the studio offers that),
none at all when nothing sensible follows, and no repeat of them in the closing message.

## The studio's part

Rules in one function, `nextsteps.studio_steps(facts)`, with no model call. `facts`:

| Key | Meaning |
|---|---|
| `changed` | The request changed project files (it made a version) |
| `has_page` | The project has `index.html` or a `studio.json` |
| `verify` | A check was asked for (`options.verify`) |
| `reported` | The agent reported a check (`verification` event) |
| `failed` | The `failed` count of the last report, or 0 |
| `still_wrong` | `problems_total` of the studio's own re-check, or 0 |
| `polish` | The request was a polish request (`options.polish`) |
| `can_look` | The studio has a browser to look with |

| Step `id` | Offered when | Chip | Text |
|---|---|---|---|
| `fix` | `still_wrong > 0` | Fix what the check still finds | Fix the N items the studio's own check still finds wrong on this page (they are listed on the check card), then run the check again. |
| `failed` | `reported`, `failed > 0`, no `fix` | Work through the failed items | Work through the N items the check marked as failed: fix those a better source settles, and tell me which ones need my decision. |
| `check` | `has_page`, not `reported`, and `changed` or `verify` | Verify content | The check-only text, with `options: {depth: "standard", verify: true, check_only: true}` |
| `look` | `has_page`, `changed`, `can_look`, not `polish`, and none of `errors`, `fix`, `failed` applies | Polish the look | The polish text, with `options: {depth: "standard", verify: false, polish: true}` |

Of the first three at most one is offered (the first that applies, in this order). Before them
comes `errors` ("Fix the page's errors"), offered when the studio's own look at the page found any
(`page_errors`, below). `look` is offered beside `check` or by itself, never beside a step that
fixes something: a page with errors or wrong figures is put right first.

**The studio's own look at the page.** After a request that completed and changed the project, the
studio opens the project's page once in its own browser (an app once it is running again, up to 8
seconds) and collects what `look_at_page` reports: script errors and failed requests. When there
are any it writes one `notice` (`warning`: "The studio opened the page after this request and it
shows N errors when it loads: …", the first three named) before `next_steps`. No model call. It is
skipped when there is no browser, on the scripted agent, and when nothing changed. This is the
independent check an app gets: the page check (`check_page`) sees marked figures only, and an app
usually has none.

## The event

When a request ends `completed` and there is at least one step, the studio writes one event after
the version is saved and before `turn_finished`:

```json
{"type": "next_steps", "steps": [
  {"id": "check", "from": "studio", "label": "Verify content",
   "text": "Verify content: check every figure and statement on this page against its sources, and change nothing except certain fixes.",
   "options": {"depth": "standard", "verify": true, "check_only": true}},
  {"id": "look", "from": "studio", "label": "Polish the look",
   "text": "Polish the look: see how this page looks on a desktop first, then on a tablet and a phone, fix what is clearly off, and suggest the rest.",
   "options": {"depth": "standard", "verify": false, "polish": true}},
  {"id": "a1", "from": "agent", "label": "Add a section on battery life", "text": "Add a section on …"}]}
```

The studio's steps first (two at most), then the agent's in the order given (`a1`, `a2`, `a3`); at most 5 in all.
An agent step whose label equals the studio step's label (letter case ignored) is dropped.
`options` is present only on a step that needs them. A request that was stopped or failed writes
no `next_steps`. Never more than one per request.

## UI

Chips are shown under the last request only, when it completed and nothing is running or queued;
an older request's chips are not shown again. A tap puts `text` in the chat box (after what is
already typed, on a new line, when the box is not empty), puts the caret there and sends nothing.
When the person sends a message whose text is exactly a shown step's `text` and that step has
`options`, the message goes with those options (so "Verify content" from a chip is the same
check-only request as the button, and "Polish the look" the same polish request); any other text goes with the project's remembered options.
On a phone the chips wrap and each is at least 36 px tall.

# Examples that come with the studio

Finished work made with the studio, shipped with it, so a new install has something to open:
reports, dashboards and other pages (never an app with its own back end). The home page shows
them; **Open a copy** makes an ordinary project from one.

## Layout

`library/examples/<id>/` (`id`: lower-case letters, digits and hyphens) holds `example.json`,
`files/` (the project's files) and, when there is one, `preview.jpg`. `example.json`:

```json
{"title": "Electric or petrol car: five-year cost", "summary": "One sentence.",
 "kind": "report", "order": 10,
 "made": {"model": "opus", "date": "2026-11-13", "requests": 2, "duration_s": 1888,
          "depth": "deep", "request": "The person's first request, as they typed it."},
 "sources": 127, "check": {"checked": 381, "passed": 372, "fixed": 9, "failed": 0, "unverifiable": 0}}
```

`kind`: `report`, `dashboard`, `explainer` or `interactive`. `made`, `sources` and `check` are
taken from the project's own record by `tools/examples/add.py` and never typed by hand, from its
completed requests only: the model of the longest one, the day of the last one, how many there were
and how long they ran together, the first one's depth and text, the number of saved sources, and the counts of the
last reported check (`null` when none was reported). A missing fact is `null`, never a guess.

**What is left out of `files/`:** `.git`, `research/`, `attachments/`, `template/`, `notes/`,
caches and dependency folders, and the saved text of every source (`sources/*` except
`sources/index.json`): that text is other people's writing. The source list, with each address,
stays.

## REST

| Route | Answer |
|---|---|
| `GET /api/examples` | `[Example]`, by `order` then title. An example whose `example.json` cannot be read or has no title, or that has no `files/index.html`, is left out |
| `GET /api/examples/{id}/preview` | the picture (`image/jpeg`), or 404 `no_preview` |
| `POST /api/projects` with `{example: id, name?}` | 201 Project: a copy of the example's files with one first version ("From the example …"). `name` defaults to the example's title. 400 `unknown_example`; 400 `invalid_request` when `template` is given too |

`Example` is `example.json` plus `id`, `files` (count), `bytes` and `preview`
(`/api/examples/<id>/preview` or `null`). `Project` gains `from_example: id | null`.

## A copy and its sources

The copy has the page's source list and no source text, so the page check cannot confirm a quote
until its source is fetched again. Two things make that work:

- `fetch_source` reads a page in the studio's browser by itself when the plain download held nothing
  readable (a page built by scripts), instead of sending the agent back to ask with `render: true`;
  the answer then has `rendered: true`. Without a browser the old error stands.
- `fetch_source` for an address that is saved but whose text file is missing downloads it again
  and writes it **under the same id** (the page's marks keep pointing at it). The answer has
  `restored: true`. This holds in any project, not only a copy of an example.
- While `from_example` is set and a source's text is missing, the project facts tell the agent so:
  the texts were not shipped, and a check must fetch each cited source again first.

## UI

The home page has an **Examples** section under the person's own projects (under the empty state
when there are none): a card per example with its picture, title, summary and one
line of facts ("Opus · Deep · 31 min · 2 requests · 115 sources · 381 figures checked", each part only
when the record holds it), and **Open a copy**. Nothing is
shown when the studio has no examples. At 390 px the cards are one column.

# Export: take a page or an app out of the studio

Three ways out, from **Export…** in the page's menu. None of them needs the studio afterwards,
and none publishes anything: the person gets a file and decides where it goes.

| Way | For | What it is |
|---|---|---|
| **One HTML file** | A page without a back end | The page with its own styles, scripts, pictures and data inside. Opens from a double click, an email or a chat message |
| **A folder to host or run** (`.zip`) | Any project the studio made | The project's files without working notes, with `EXPORT-README.md` saying how to open, host or run it; for an app also `run.sh` |
| **Everything** (`.zip`) | Any project | The working folder as it is (`GET …/download`, unchanged): notes, saved sources and all. For keeping, not for sharing |

## One HTML file

Built from one page of the project (`index.html` unless `page` says otherwise):

- a local stylesheet (`<link rel="stylesheet">`) becomes a `<style>`, with the local files its
  `url(…)` names turned into `data:` addresses;
- a local script (`<script src>`) becomes an inline script;
- a local picture, icon, poster, audio or video source becomes a `data:` address: `src` (on `<img>`,
  `<source>`, `<video>`, `<audio>`, `<track>`, `<embed>`, `<input>`), every candidate of a `srcset`
  (on `<img>` and on `<source>` inside `<picture>`; each keeps its `1x`, `2x` or `640w`, because a
  screen at 2x loads that candidate, not `src`), `poster`, `href` of an icon (`icon`,
  `apple-touch-icon`, `mask-icon`), `data` of an `<object>`, `href` of an SVG `<image>`, and the
  `url(…)` in a `style` attribute. Only the address changes: the rest of the tag (an `alt` that
  repeats the file name, its quotes) is kept as written;
- an SVG `<use>` that names another file is left as it is and said in `notes`: a page opened from a
  file cannot load it, and browsers refuse it from a `data:` address too;
- only real tags of the page are changed, never text: the inside of a `<script>` (an HTML string a
  script builds, a JSON block), of a `<style>` other than its CSS addresses, of a `<textarea>`,
  `<title>` or `<xmp>`, and comments are kept exactly as written. The pictures a script builds markup
  with cannot go inside without editing the script, so they are said in `notes`;
- addresses start from the page's folder, or from its `<base href>` when that is a folder of the
  project (requests answered from inside the file start there too); a `<base>` anywhere else is said
  in `notes`;
- every other data-like file of the project (`.json .csv .tsv .txt .geojson .topojson .ndjson
  .jsonl .xml .yaml .yml .md .svg .png .jpg .jpeg .gif .webp .avif`) is put inside the file, and a
  small script at the top answers `fetch()` and `XMLHttpRequest` for it from there, so a page that
  loads its data keeps working with no server (chart libraries often load a CSV the second way). A
  request for anything else goes to the network as before.

Left out, here and in the folder: `.git`, `research/`, `attachments/`, `template/`, `notes/`,
`state/`, `looks/`, dependency folders and caches, key files (see "Key files"), and the saved text of sources
(`sources/*` except `sources/index.json`).

What is not inside is said, never hidden. `external`: the other sites the page still loads from
(a chart library from a CDN, a font): the file needs the internet for those. `notes`: other pages
of the project it links to (they are not in the file), a file an SVG `<use>` names, the pictures a script
builds markup with, a `<base>` outside the project, and a local file it names that does not exist.

It is **not available**, with the reason, when: the project has a back end (`studio.json`: the
page needs its server; use the folder); a module script, in a file or in the page, imports other
files of the project (a browser will not load those for a page opened from a file); or the file
would be over 30 MB.

The size is worked out from the files' sizes before anything is read, to the byte: opening the
dialog builds nothing, and a page that would be over the limit is refused without reading the
clips and pictures it names. `embedded.bytes` is the data files' own size.

## A folder to host or run

The project's files, less what is left out above, plus:

- `EXPORT-README.md`: what this is and when it was exported; for a page, how to open it (a page
  that loads data needs a web server: the one-line command is given) and how to host it (any static
  host takes the folder as it is); for an app, what it needs installed (read from the first word of
  its run command), how to start it, and what it used from the studio that will not be there
  (`needs`: the studio's model address, an attached data source, the project's own address), found
  by looking for the `STUDIO_*` names in the app's files.
- for an app, `run.sh`: sets `PORT` (8000 unless given) and runs the `run` command of
  `studio.json`, unchanged.

Not available for a project brought in from a folder on this computer (it has its own
repository; **Everything** still works).

## REST

| Route | Answer |
|---|---|
| `GET /api/projects/{id}/export?page=index.html` | `{name, app, pages, page, html, folder}`. `html`: `{available, reason, bytes, inlined, embedded: {files, bytes}, external: [host], notes: [string]}`. `folder`: `{available, reason, files, bytes, run, tools: [string], needs: [string]}` |
| `GET /api/projects/{id}/export/page?page=index.html` | the file, `text/html`, as an attachment named after the project. 409 `export_unavailable` with the reason; 404 `unknown_page` |
| `GET /api/projects/{id}/export/folder` | `application/zip`, as an attachment `<name>-export.zip`, written to a temporary file and sent from it (removed afterwards), like `GET …/download`, so a large project never sits in memory. 409 `export_unavailable` |

## UI

**Export…** in the page's menu (in place of "Download as zip") opens a dialog with the three ways,
each with its size, what is and is not inside, and a download button; a way that is not available
is shown with its reason. The project facts tell the agent that the person exports from there.

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

# More places to look for skills and connectors

The studio always reads this machine's `~/.claude/skills`, the plugins installed for Claude Code and
`~/.claude.json`. A person who keeps skills in other folders, or connectors (MCP servers) in another
file, names those places; nothing is copied.

- Config: `library: {skill_dirs: [path], connector_files: [path]}`. The variables
  `STUDIO2_SKILL_DIRS` and `STUDIO2_CONNECTOR_FILES` (paths joined by the system's path separator)
  win over the config when set. `~` is expanded.
- A skill folder holds one folder per skill with a `SKILL.md` (or is itself one skill). Its skills
  are listed like those of `~/.claude/skills`, with the folder as their origin. A skill whose name
  is already found elsewhere is not listed twice.
- A connector file is JSON with `mcpServers`, or the map of names to settings itself. Its
  connectors join those of `~/.claude.json`; a name found twice keeps its first settings.
- `GET /api/library/places` → `{always, skill_dirs: [{path, found, skills}], connector_files:
  [{path, found, connectors}], from_env: {skill_dirs, connector_files}}`.
  `PUT /api/library/places` with `{skill_dirs?, connector_files?}` replaces the lists it is given,
  keeps them in the config and uses them at once. 400 `invalid_place` (not a full path, no such
  folder or file, a file that is not connectors); 403 `not_for_shared` on a shared studio.
- `studio library` shows them; `studio library add <path>` and `studio library remove <path>`.
- The Library page has a **Where the studio looks** block with the same.

# Skills a person makes

A skill is a set of instructions the agent follows for one kind of job. A person makes one
without knowing how a skill is written, in three ways, and shares it as a file.

## Three ways in

1. **Describe it** (Library → New skill → Describe it). The person says in their own words what
   should happen ("first get the data for the last 30 days from …, then …, then check it like
   this …") and may attach examples to follow (a page, a document, a picture). The interface
   makes an ordinary project named `Skill: <the first words>` and sends one request in it:
   `Make a new skill for my library from this description (use the make-a-skill skill):`,
   a blank line, then the description; the attached files go as the request's `attachments`.
2. **From work in a project** (the project's menu → Make a skill from this…). The person chooses
   the whole page or app, or the part they selected, and may add a note on what changes each
   time. The interface sends, in that project:
   `Make a skill from how this was made (use the make-a-skill skill), so I can get the same kind
   of result again for a different subject.` followed, when there is a note, by a blank line and
   `What changes each time: <note>`. With "the part I selected" the request carries the
   selection like any other.
3. **Write it yourself** (the form that was there: name, description, instructions).

Ways 1 and 2 are ordinary requests: they can be queued, stopped and continued in the chat. The
agent asks what it needs to know there, shows the draft on the project's page in plain words
(way 1) or in its answer (way 2), and saves only through the tool below.

## The studio's skill `make-a-skill`

Shipped in `library/skills/make-a-skill/`, loaded like the other studio skills. It tells the agent
how to get from a description, or from work already done, to a skill: what to ask (when it is
used, what it starts from, the steps in order, what a good result looks like, how the result is
checked, what must never happen), how to write the `description` so the skill is picked when it
is needed, how to write steps another agent can follow without this conversation, what to leave
out (anything true only of this one project: its subject, its figures, its file names), and to
end with `save_skill`. A skill made here is instructions and reference files only: no scripts.

A skill made from work in a project keeps that work's look unless the person says the look does
not matter: the body gets a part `How it looks` (the layout at desktop width and how it folds, the
palette with its colour values, the type, how cards, tables and charts are drawn) and the page
itself is saved as `reference/example.html`, to copy the structure and styles from and never the
content. The agent tells the person that the example still holds the project's content and offers
a copy with the content taken out. (The first version carried instructions only: a real run then
gave the right parts and checks in a rearranged layout, so the look was added on 18 November.)

## The tool `save_skill`

```
{name, description, body, files?: [{path, as?}], replace?: boolean}
```

- `name`: lower-case letters, digits, `-` and `_`, at most 60 characters, not the name of one of
  the studio's own skills. `description`: when to use it, at most 600 characters. `body`: the
  instructions in Markdown, at most 12,000 characters, without a front-matter block (the studio
  writes it).
- `files`: reference files copied from the project into the skill, at `reference/<as or the
  file's name>`: at most 12, each at most 5 MB, 10 MB together. Only material to read or follow:
  `.md .txt .html .css .json .csv .tsv .svg .png .jpg .jpeg .webp .gif .pdf .docx .pptx .xlsx`.
  Anything else is refused by name (no scripts); the file's own name and `as` are both checked,
  so a script cannot be saved under another ending. `as` is at most 80 characters and does not
  start with a dot.
- **It always asks**, whatever the project's approval setting: one approval card (`tool:
  "save_skill"`) titled `Save the skill “<name>” to your library` (or `Replace your skill
  “<name>”`), whose detail is the description, the whole body and the list of files, with the
  reason "A skill in your library is given to the agent in every project from now on." For this
  tool the card's detail is kept whole (up to 14,000 characters) instead of being cut at 4,000.
  With no request running there is nobody to ask and nothing is saved.
- A skill of the person's with that name is replaced only with `replace: true`; without it the
  tool refuses and says so. The version it replaces is kept in `data/skills/.history/<name>-<UTC
  time>/` (the last five per name). Replacing changes the description and the instructions and
  keeps the skill's other files: `files` adds to them or replaces one at the same path, and the
  card lists what is kept. A single file is not removed this way (delete the skill and save it
  again).
- On Allow it writes `data/skills/<name>/SKILL.md` and the files, rebuilds the index and answers
  `{saved, id: "yours:<name>", replaced, files: [path], next}`. On Deny nothing is written and
  the answer tells the agent not to ask again for the same text in this request.

A saved skill is the person's own (`source: "yours"`): on by default in every project, loaded
from the next request, switched off per project like any other.

## REST

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/skills/{name}` | | `{…skill, body, files: [{path, size}]}` for a skill of the person's own; 404 `unknown_skill` |
| `PUT /api/skills/{name}` | `{description?, body?}` | the same object; same limits as the tool; the replaced version goes to `.history` (a change that changes nothing adds none). `{}` → 400 `invalid_request` |
| `GET /api/library/skills/export?id=<skill id>` | | `application/zip`, `Content-Disposition: attachment; filename="<name>.skill.zip"`. Any skill in the index (`studio:`, `yours:`, `machine:`, `plugin:`) |
| `POST /api/skills/import` | multipart `file`; query `dry_run=1`, `replace=1`, `allow_scripts=1`, `name=<new name>` | with `dry_run`: the preview below, nothing written. Otherwise the skill (201) |

`files` in the two rows above is a list of `{path, size}` without `SKILL.md`; in the library's
lists and in the answer to an import it is the count the index keeps. `POST /api/skills` takes
the tool's limits and answers 409 `name_taken_by_studio` for the name of one of the studio's own.

**A skill file** (`<name>.skill.zip`) holds the skill's folder as the library has it (`SKILL.md`
at the top and every other file; `.git`, `node_modules`, `__pycache__`, `.venv`, `.history`,
`__MACOSX`, `.DS_Store`, `.env` and `.env.*` left out, and links not followed, since a shared
studio exports to everyone; 40 MB unpacked at most, else 413 `too_large`) and `studio-skill.json`: `{format: 1, name, description,
source, exported_at, files: [path], scripts: [path]}`. The licence of a skill the person did not
write is theirs to check; the file carries whatever licence file the folder holds.

**Importing** reads a `.skill.zip`, or any zip with one `SKILL.md` at its top or inside its one
top folder. Refused with 400 `invalid_skill_file` and the reason: not a zip; no `SKILL.md` or
more than one candidate; front matter without `name` or `description`; a path that is absolute,
holds `..` or is a link; an entry that is not a file; the same path twice (letter case ignored) or
a path that is both a file and a folder; a password; more than 600 files; more than 25 MB packed
or 60 MB unpacked (counted while reading, whatever the zip's own figures say). A refused file
leaves nothing behind. So a few skills that export cannot be imported again: one above those
sizes, or one with a second `SKILL.md` inside. The name is, in order: `name=`, the name in
`studio-skill.json`, the front matter's.
The preview (200): `{name, description, body_start (≤ 1,200 characters), files: [{path, size}]
(with `SKILL.md`), scripts: [path], size (unpacked bytes), exists, taken_by_studio}`.

- `scripts` are files a program could run: `.py .js .mjs .cjs .ts .sh .bash .zsh .rb .pl .php
  .ps1 .bat .cmd`, and any file marked executable in the zip. A file with scripts is saved only
  with `allow_scripts=1`; without it 409 `scripts_need_confirmation` (the preview is in
  `error.details`). The agent may run a skill's scripts through its shell, under the project's
  approval rules, so the person is told what they are bringing in.
- `exists` (a skill of the person's has the name) needs `replace=1`, else 409 `name_taken`; the
  replaced version goes to `.history`. `taken_by_studio` (one of the studio's own skills has the
  name) is 409 `name_taken_by_studio`: import it under another name with `name=`. When several
  apply they are reported in this order: `name_taken_by_studio`, `name_taken`,
  `scripts_need_confirmation`, each with the preview in `error.details`. Flags take `1` or `true`.
- On a shared studio, export works for everyone; adding, importing, changing and deleting a
  skill, and the tool, are refused (403 `not_for_shared`, on a `dry_run` too), like the other
  library settings.

## UI

- **Library, Skills.** A **New skill** button opens a dialog with three choices: **Describe it**
  (a large text box with an example of the kind of description that works, and a place to add
  example files), **Write it yourself** (the form) and **Import a file**. Import shows the
  preview first: the name, when it is used, how the instructions begin, its files, and, when it
  holds scripts, their names with a box to tick ("This skill brings N scripts the agent may
  run") before **Add to my library** works; a name already there asks whether to replace it or
  to add this one under another name.
- Every skill in the Library has **Export** (downloads the file). The person's own skills also
  have **Open** (the instructions and files, with **Edit** for the description and instructions)
  and **Delete**.
- **A project's menu** has **Make a skill from this…** beside Save as template: the whole page or
  app, or the selected part (offered when something is selected), and the optional note.
- **The approval card** for `save_skill` shows the name, when it is used, the whole instructions
  (scrolling inside the card) and the files, with Allow and Deny. The instructions are shown as
  the text they are, not as formatted Markdown, so that formatting can hide nothing from the
  person approving. The card's `detail` is plain text in this shape, which the card reads:
  `When it is used: …`, `Instructions:` and the body, `Files:` with one `path (size)` a line,
  and `Files kept from the version it replaces: …`, each part after a blank line.

# Verify content reaches files

Lane `verify-files`. A wrong number in a slide, a spreadsheet cell, a Word paragraph or a PDF is
found the way a wrong number on the page is: the studio tool `check_file` compares every figure in
the file with what the project can vouch for, the checker runs it, and the studio's own re-check
runs it again. The code is `server/studio2_server/filecheck.py`.

## The tool `check_file {path}`

`path`: a `.pptx`, `.docx`, `.xlsx` or `.pdf` file inside the project (anything else, a missing
file or a path outside the project → a tool error). It reads the file itself; it needs no
LibreOffice and calls no model.

**What is read, and where each figure is said to be (`where`).**

| File | Read with | Read | `where` |
|---|---|---|---|
| `.pptx` | the standard library (the file is a zip of XML) | every slide in the deck's order: each shape's text and each table cell. Not read: speaker notes, charts' own data, slide-number, date and footer placeholders | `Slide 3, title`, `Slide 3, Content 2`, `Slide 3, table row 2, column 3` |
| `.docx` | the standard library | the body: paragraphs and table cells (nested tables too), in order, with the nearest heading (a style named `Title` or `Heading n`, or based on one, or with an outline level). Not read: headers, footers, footnotes, comments | `Paragraph 12 under "Results"`, `Table 2, row 3, column 2 under "Results"` |
| `.xlsx` | openpyxl (in the studio's environment), twice: formulas, and the stored results | every sheet, every cell that is not empty | `Summary!B4 (Revenue, 2025)`: the cell, then the row's label (the nearest text to its left) and the column's heading (the top text above it) when there are any |
| `.pdf` | pypdf (in the studio's environment, as for saved sources) | the text layer, page by page | `Page 3` |

A PDF without a text layer (scanned pictures) and a file that cannot be opened end with `error`
set and nothing checked: never a pass. A workbook cell that holds a **formula** is `derived`: its
value is the result LibreOffice stored (`render_document` stores it, and reports formula errors);
a formula with no stored result is still `derived` and its `note` says to run `render_document`.
A typed-in number, and every figure in a text cell, is compared like any other. A cell's number
is read as the sheet shows it (its number format: `0.123` under `0.0%` is the figure `12.3%`).

**What a figure is.** A number in the text, with its sign, currency (`$ € £ ₹ ¥`, `US$`, `Rs`,
`USD`, `EUR`, `GBP`, `INR`), percent sign and scale (`thousand`, `k`, `million`, `m`, `mn`,
`billion`, `b`, `bn`, `trillion`, `tn`, `lakh`, `crore`, `cr`; single letters only when written
against the number: `4.2m`, `300k`). Thousands separators are dropped (`1,234,567` and the Indian
`12,34,567`); a negative may be written `-1,234`, `−1,234` or `(1,234)`. Its value is the number
times its scale: `$4.2 billion` is 4,200,000,000 shown to a tenth of a billion. Only the comma is
read as a thousands separator: `4.218.950` (read as a version) and `4 218 950` are not one figure.

**What is not a figure to check** (it is skipped; `summary.skipped` counts the numbers skipped in
the text that was read, so superscript text and the placeholders that are not read are not in it):

- years standing alone (four digits from 1900 to 2100 with no separator, sign, currency, percent
  or scale: `in 2025`; `2,025 units` and `$2025` are figures), year ranges (`2024–25`) and
  fiscal years (`FY24`);
- dates and times (`3 March 2025`, `11–18 November 2026`, `Mar 2025`, `2025-03-03`, `3/3/25`, `10:30`); in a workbook a
  cell holding a date;
- slide, page and list numbers: the number after `slide`, `page`, `p.`, `pp.`, `figure`, `fig.`,
  `table`, `chart`, `exhibit`, `appendix`, `section`, `chapter`, `step`, `item`, `no.`, `#`;
  numbering at the start of a paragraph or line (`1.`, `2)`, `(3)`); a PDF line that is only a
  page number (`3`, `Page 3`, `3 of 12`, `3 / 12`);
- reference markers: `[1]`, `[12]`, and superscript text in a slide or a Word document;
- ordinals (`3rd`, `21st`);
- phone-like strings (`+1 415 555 0100`, `(415) 555-0100`, `415-555-0100`), id-like strings (digits
  joined by hyphens, `#4411`, a number with letters against it that are not a unit or scale such
  as `4411A`, a run of nine or more digits with no separator, sign, currency or decimal point in
  text), versions (`v2`, `version 3.1`, `1.2.3`), web addresses and e-mail addresses;
- whole numbers from 0 to 12 with no decimals, sign, currency, percent or scale ("3 regions"): too
  small to tell one source from another;
- in a workbook, `TRUE`/`FALSE` and errors.

**What vouches for a figure**, in this order:

1. `page`: the figures the page check found `verified` on the project's pages, as last saved in
   `checks/<page>.json` by `check_page` (the shown text of each item, and a data item's value);
   a page figure marked `derived` there makes the same figure in the file `derived`;
2. `data`: the project's data files, `data/**/*.csv`, `data/**/*.json` and `data/**/*.geojson`
   (each at most 20 MB, 200 files): every number in them, including the numbers written inside a
   cell or value of text ("148 km WSW of Tual"), and the total of each numeric CSV column;
3. `source`: every number in the saved text of every source (`sources/`).

**Matching.** Values are compared as absolute values (a sign written differently is not a
difference: sources often state a loss without one). A figure is `found` when a vouched value is
equal (`match: "exact"`), or when a vouched value rounded to the figure's precision is the figure
(`match: "rounded"`, and `rounded_from` names the value: `4.2 million` for 4,218,950; `12%` for
12.4). A percentage also matches the same value written as a fraction (`12.3%` for 0.123).
Truncation is not rounding (4.21 million is not found from 4,218,950). A figure written at another
scale than its source states it without the unit word (a table "in millions") is not found.
Otherwise the figure is `not_found`: no page, data file or source has it.

A `found` figure is in a vouched place; it is not proof that it is the right figure for that
sentence (the same number may stand for something else), and the source can be wrong.

**The saved result**, `checks/<file with / and other characters as ->.json` (for
`files/deck.pptx`: `checks/files-deck.pptx.json`):

```json
{"file": "files/deck.pptx", "kind": "presentation", "checked_at": "…", "error": null, "not_read": null,
 "summary": {"figures": 14, "found": 9, "rounded": 2, "derived": 3, "not_found": 2, "skipped": 21},
 "vouched": {"pages": ["index.html"], "sources": 4, "data_files": ["data/sales.csv"]},
 "items": [{"n": 1, "text": "$4.3 billion", "value": "4300000000", "status": "not_found",
            "where": "Slide 3, title", "context": "Revenue reached $4.3 billion in 2025",
            "match": null, "via": null, "ref": null, "rounded_from": null, "note": null},
           {"n": 2, "text": "4.2 million", "value": "4200000", "status": "found",
            "where": "Slide 4, Content 2", "context": "…", "match": "rounded", "via": "data",
            "ref": "data/sales.csv, column units (total)", "rounded_from": "4218950", "note": null}]}
```

`kind`: `presentation`, `document`, `workbook` or `pdf`. `items` are in the file's order (at most
10,000, every `not_found` kept; `summary` counts all: "Very large documents"). `via`: `page`, `data` or `source`; `ref`: the page and where on it,
the data file and column (and row, or `(total)`), or the source id; `rounded` counts the `found`
items that matched as a rounding; `derived` items carry `note` (the formula, or the page's
derivation). `context` is the text around the figure (at most 160 characters). `not_read` says
when part of a workbook was not read (more than 200,000 cells), so a clean result never hides it.

**The tool's answer** is short: `{file, kind, summary, error, not_read, not_found: [items, at most 40],
derived: [items, at most 15], rounded: [items, at most 15], saved, next}`, with the additions in
"Very large documents" (items without
`value`, `n` and the fields that are null). `next`: when something is not found, "correct each figure under not_found in
the file's build script, from its source or data (or fetch the source it came from), build and
render again, and run check_file again; a figure you calculated stays not_found here: recompute
it from vouched figures and say so in your report"; when nothing is, that every figure is found
or derived and that the derived ones (formulas, page derivations) are not recomputed by the
check; with `error`, the error.

## The agent's instructions

- The `VERIFY` wording (`effort.py`) sends the checker to the project's files as well as its
  pages: every Office or PDF file the page shows or the request made, with `check_file`, and
  derived figures in files (formulas, totals, changes) recomputed as for pages. After fixes, the
  agent runs `check_file` again on each file it changed.
- `library/agents/checker.md` has the same step: run `check_file` on each file, judge `found`
  items as it judges `verified` ones (is it the right figure for that sentence), recompute
  `derived` and `not_found` figures from vouched inputs, and give each one a status.
- The `office-files` skill: run `check_file` on the file before handing it over, after the last
  render.
- The project facts name `check_file` in one sentence beside `check_page`.

## The studio's own re-check, on files

After a reported verification the studio's re-check ("The studio's own re-check") also runs
`check_file` (without saving) on every file `check_file` ran on in the request and on every
`.pptx`, `.docx`, `.xlsx` or `.pdf` file of the project that `index.html` links to or frames
(`href`, `src` and `data` attributes; a framed preview, `files/deck.pptx.preview/index.html`,
counts as its file), at most 10 files. In the `verification_recheck` event:

- every entry of `pages` gains `kind: "page"` or `kind: "file"`. A file's entry keeps the page
  keys (`page` is the file's path, `checked` the figures checked, `verified` the found ones,
  `derived`, `failed` the not-found ones, `cited_not_listed: 0`, `source_list: false`, `error`)
  and adds `found`, `not_found` and `rounded`;
- every entry of `problems` gains `kind`; a file's not-found figure is
  `{"page": "files/deck.pptx", "kind": "file", "what": "$4.3 billion", "where": "Slide 3, title",
  "why": "Not in any checked page figure, data file or saved source."}`. Pages' problems come
  first, then files', within the same cap of 40;
- `agrees` is true when there are no problems and at least one page or file was read (and, since
  "Very large documents", nothing was left out past the caps: `not_rechecked`). The event is
  written when there is a page or a file to check.

## UI

- The card's line on what the re-check covers (`RECHECK_COVERS`) says it covers the page's marked
  figures and the figures in the project's Office and PDF files, against saved sources, checked
  page figures and project data.
- A file's problem reads as its place, then the figure: `Slide 3, title: $4.3 billion`, with the
  file's name first when the re-check covered more than one page or file
  (`deck.pptx · Slide 3, title: $4.3 billion`), and the reason under it.

.

.

# Seeing an app signed in as the person

Every project page the studio opens in its own headless browser (`look_at_page` and the look
routes, the pictures of a drawing, the studio's own look after a request, `render_image`,
`render_video`) can carry the sign-in the person has in their own browser for that project, so
an app behind a sign-in shows the agent the signed-in app. One module decides
(`server/studio2_server/session.py`); the browser code asks it for every context it opens.

## The setting

`Project` gains `signed_in_looks` (bool, the effective value). The interface names it
**Let the agent use my sign-in**, a tick in the project's menu (both the top bar's
More menu and the compact menu) under a short section, "This app's sign-in", whose note says what
it means: when ticked, the agent sees this app signed in as you in this browser and can then click
and type in the app as you. `PATCH /api/projects/{id} {signed_in_looks: bool}` sets it and the record keeps it.

- Default when the record has no value: on, except for a project brought in from a folder
  (`attach` set: the person's real code, perhaps talking to real services), which is off until
  the person switches it on.
- On a shared studio (`settings.shared`) it is always false, and `PATCH` with `true` is refused:
  403 `not_for_shared`. Nothing of a person's browser is kept there at all.
- Switching it off forgets what was kept of the person's browser for that project at once.

## What is kept, and where

Values only in the studio's memory (a `Sessions` object), per project, never written anywhere:
not to disk, the event log, the project record, the server's log, or anything the agent
receives. The agent gets pictures and `signed_in`, never a value.

0. **The names of the cookies the project's own app sets** (no values). Every `Set-Cookie` the
   project's app sends through the preview proxy is read for its name and its `Path`, `HttpOnly`,
   `SameSite` and `Secure` (a missing `Path` is the RFC 6265 default path of the request). These
   are written to `app-cookies.json` in the project's folder (beside `project.json`; at most 64
   names; not in the API), so they outlive a restart of the studio: the person has usually signed
   in before it. Only cookies named there are ever kept or carried. So a project with no back end
   keeps no cookies at all (its storage is still carried), a cookie the page's own script sets
   (`document.cookie = …`) is not carried, and neither is any cookie another app on the same host
   name gave the browser. When the person's browser sent cookies but none of them is the app's, the
   look's `sign_in_note` says that this is why it is not signed in.
1. **The person's cookies** of those names, from the `Cookie` header of the person's page loads
   of the project (`Sec-Fetch-Dest: document` or `iframe`, `Sec-Fetch-Site` same-origin,
   same-site or none) and of the bridge's reports (below), at most 16 KB a header. Each such
   request is merged in: a named cookie the request carries is taken with its value; a named
   cookie whose recorded path covers the request's path (RFC 6265 path-match) and that the
   request does not carry is dropped (the app or the browser removed it); any other is left as
   it was. So a cookie limited to `/app` survives the reports to `/__studio/session` and page
   loads of `/`, and goes when a page load under `/app` no longer carries it. A `Set-Cookie` that
   deletes a named cookie (`Max-Age` 0 or less, or an `Expires` in the past) in an answer to the
   person's browser drops it at once. The studio's own access cookie (`studio2_access`) is never
   kept.
2. **The person's `localStorage` and `sessionStorage`** for the page's own origin, reported by
   the bridge (below), at most 64 KB together. A larger report keeps the cookies and drops the
   storage; the look's note then says so.
3. **The agent's own kept sign-in**: after each `look_at_page` (and the look routes) that did not
   carry the person's sign-in, the cookies the studio's browser holds for the loopback host and
   the page origin's `localStorage` and `sessionStorage`, at most 64 KB. So a sign-in the agent
   does once through `actions` (with credentials the person gave it) holds for the next looks.
   Kept separately from the person's: a look that carries the person's sign-in starts from it
   alone (refreshed from their browser each time) and nothing of it is kept as the agent's. A look
   that ends after its project was deleted, or after what was kept for it was forgotten, keeps
   nothing.

While the option is off (or on a shared studio) none of 1 and 2 is kept; only the fact that the
person's browser sent something for the project, so the agent can be told why it was not used.

All values are cleared when the studio stops, when the project is deleted, and (1 and 2) when
the option is switched off. A look already running when the option is switched off finishes as
it began (its browser already holds the sign-in); every later one is without it.

## The report from the page

The bridge script (`/__studio/bridge.js`) reports, in every project page it is injected into,
framed in the studio or open in a tab of its own, but never in the studio's own headless browser
(`navigator.webdriver`): shortly after load, after in-page navigation, two seconds after a click
or a form submit, when the tab is hidden or left, and every five seconds while it is visible and
the storage changed (at least once a minute).

`POST <project URL>/__studio/session`, `Content-Type: application/json`,
`{"local": [[key, value], …], "session": [[key, value], …]}`. The preview server answers it
itself and never passes it to the app. It accepts only what a same-origin page of that project
sends: the method is POST (else 405); `Origin` is present and equals the request's own scheme and
`Host` (else 403); `Sec-Fetch-Site`, when sent, is `same-origin` (else 403); the type is JSON
(else 415; a page on another origin cannot send that without a preflight, which this path does
not answer); entries are strings (else 400). The answer is always an empty 204 (or the error):
nothing kept is ever readable back through it, by any page. A request from the studio's own
browser is recognised by a secret header (generated at start, kept in memory) that the studio
adds only to its browser's requests for a project page port of this studio on a loopback name,
never to anything else, never carried along by a redirect, and removed by the preview server
before anything is passed to an app; such a request is never taken as the person's, neither here
nor as a page load. The studio's browser cannot reach a project page port under any other name
or address the studio answers on (the tailnet name, `STUDIO2_HOSTS`, the machine's own names):
those requests and sockets are refused, so a page in it cannot plant a "person's" session by
reporting through a name that carries no secret header.

## How the studio's browser uses it

For a URL on a project's own loopback address (`http://127.0.0.1:<page port>/…`) only. A
context opened for any other URL (outside pages fetched for the agent, `Looker.rendered`) starts
with nothing, and so does the template list's picture of a page (it can leave the project).

- When the option is on and the person's browser has reported something: the context starts
  with the person's cookies (host-only for `127.0.0.1`, each with the `Path`, `HttpOnly`,
  `SameSite` and `Secure` the app set it with, so a page's script still cannot read an `HttpOnly`
  one) and `localStorage` for the page origin, and the `sessionStorage` entries are set by a
  script that runs before any of the page's own scripts, once per tab, only on that origin.
- Otherwise the context starts from the agent's own kept sign-in for that project, if any.

Every request of the context to this machine is decided by the studio, hop by hop, never by the
browser (which would follow a redirect itself, with the context's cookies and any added header):

- To the project's own origin: made through the context's own request client with the cookies
  the browser would send (including `Secure` ones, which a browser sends to 127.0.0.1) and the
  secret header; the context keeps the answer's `Set-Cookie`.
- To any other loopback address or port (another project, the project's own back end on port +
  100 called directly, another local server): made by the studio without cookies, with the secret
  header only for a project page port, and the answer's `Set-Cookie` dropped. A WebSocket to one is
  refused. Project B's page never receives project A's session.
- A redirect: for a page load, the browser is given a page that moves it on to the new address,
  so that hop is a request of its own and comes back through these rules (an address off this
  machine is then loaded by the browser as any outside page, with nothing of the project's); for
  anything else, the studio follows it here (at most 10 hops), each hop by the same rules, a hop
  off this machine without cookies or the header.
- A `text/event-stream` request to the project's own origin (a stream cannot be held) is passed
  on unchanged, with the browser's cookies and no secret header.

## What the agent is told

A look's result gains `signed_in` (true when the person's sign-in was used), `kept_sign_in`
(true when the agent's own kept one was used) and `sign_in_note` (null, or a sentence). The
note is set when the person's browser has a session for this project that was not used, saying
why (the option is off: its name and where it is, the project's menu; or the studio is shared)
and that the agent must not guess or invent credentials to get past a sign-in; when the person's
storage was too large to keep; and when the person's browser sent only cookies the project's app
never set. The project facts say, next to `look_at_page`, whether
looks carry the person's sign-in for this project, what to tell the person when the page asks to
sign in, never to guess credentials, and that a sign-in done with `actions` lasts across looks.

## Not held

The person allows it per project: with it on, the agent's looks and `actions` act in the app as
the person. The agent writes the project's code, so it could make a page show or send what the
browser holds; this keeps values out of what the studio hands the agent, it does not make the
app's own page keep a secret from the agent. Cookies are copied without their own expiry (a
request header does not carry it). A sign-in held only in IndexedDB or in memory is not carried.
A redirect that starts off this machine (an outside page the project's page navigated to) and
comes back to a loopback address is followed by the browser itself; an event stream's redirect
is too. The studio's own HTTP client in the preview proxy keeps no cookies of its own; but a
request from the person's browser to a project port carries every cookie that browser holds for
the host name (on the tailnet name, other apps' cookies on other ports), and the proxy passes
them all on to the project's app, as it did before this section (only `studio2_access` is
removed): that is the browser's rule for cookies, not one the studio can change by port.

.

.

# Files in the chat, and files shown as what they are

No new event, field or route: everything here is derived in the interface from what the server
already sends (the request's `checkpoint` event, kind `agent`, and `GET /api/projects/{id}/files`).

## Files in the chat

After a request ends (`completed`, `stopped` or `failed`), the chat shows under its answer a small
card for each deliverable file it made or changed: the kind in plain words, the file's name, its
size, and whether it is new or changed. A tap opens the file in the Files panel's own viewer
(the panel opens if it was closed, the folders above the file unfold, and unsaved edits in the
open file are asked about first, as for any other file). There is no download button in the
chat; the viewer has one.

**What counts as a deliverable.** The files in the request's own `checkpoint` (kind `agent`;
an upload's or a restore's version never counts) with status `added` or `modified` that are:
- anything under `files/` (where the agent is told to put Office files), or
- elsewhere in the project, an `added` (not merely changed) PowerPoint, Excel, Word, PDF,
  raster picture (`png jpg jpeg gif webp avif bmp`), video, sound, CSV or TSV file.

and never:
- in the studio's own and working folders at the top of the project: `sources/`, `checks/`,
  `attachments/`, `tools/`, `research/`, `state/`, `template/`, `notes/`; nor `files/img/`
  (the pictures a deck or document is built from) or `files/diagrams/` (the drawings and their
  sources that `render_diagram` saves, which the page already shows);
- inside a folder whose name starts with a dot, `node_modules/`, `__pycache__/`, or a
  `<file>.preview/` folder written by `render_document`;
- the page's own code or a script (`html htm css scss sass less js mjs cjs jsx ts tsx map py sh
  lock`), wherever it is;
- an SVG outside `files/` (nearly always one of the page's own icons);
- a video's poster (`<name>.poster.jpg`, or `<name>.jpg` beside `<name>.mp4` in the same request);
- a file attached to the request's own message.

Cards under `files/` come first, then the rest, each in the checkpoint's order. At most **8** cards
per request; the rest are counted in one line, "and 3 more in Files", which opens the Files panel.

**Kinds.** "PowerPoint deck" (`pptx ppt odp key`), "Excel workbook" (`xlsx xlsm xls ods`),
"Word document" (`docx doc odt rtf`), "PDF", "Picture", "Video", "Sound", "Table (CSV)",
"Table (TSV)", "Text" (`md markdown txt`), else "File". A picture's card shows the picture as its
thumbnail (from `/__studio/files/`); the others an icon.

**Size, and a file that is gone.** Sizes are the files' sizes now, read from the file listing once
the request has ended and again whenever the project's files change while no request runs (not
during one). A file that is not in the listing any more (deleted later, or a version restored
without it) keeps its card, but the card says "no longer in the project" and offers "Show version"
instead, which opens that request's version in Versions, where the file still is. A listing cut
at its 5,000-file cap, or one that could not be read, never makes a card say a file is gone: the
card then shows no size and still opens the file. A file that was changed again since opens as it
is now.

Known limit: a file dropped at the top of the project (not attached to a message) while a request
runs is saved with that request's version, so it can get a card as if the request had made it.

## Files shown as what they are

In the Files panel, a text file of these kinds opens as what it is, with a "Show source" button to
see (and edit and save) its text, and a button back ("Show formatted", "Show table", "Show
picture"). The shown view follows the text as it is being edited.
- **Markdown** (`.md`, `.markdown`): rendered with the chat's own Markdown renderer. Raw HTML in
  the file is shown as text, never turned into elements; links go only to `http`, `https` and
  `mailto` addresses, in a new tab.
- **CSV and TSV** (`.csv`; `.tsv` and `.tab` split on tabs): a table, the first line as its
  header, which stays in view while the table scrolls; a column whose filled cells are all
  numbers (`12`, `-3.5`, `1,234.50`, `$9.99`, `45%`, `(120)`) is right-aligned. Quoted fields
  may hold the delimiter, doubled quotes and line breaks; blank lines are not rows. At most the
  first **500** rows and **50** columns; a line above the table says how many of each the file
  has when some were left out ("Showing the first 500 of 1,204 rows and 50 of 63 columns.").
- **SVG**: the picture, as an `<img>` loaded from the file's text (`data:image/svg+xml`), which
  never runs a script or an event handler in the file. It is never put inline in the studio's
  page. An SVG larger than 1 MB (too large to open as text) is still shown as the picture, as an
  `<img>` of the file's `/__studio/files/` address, without a source view.

Every cell and every piece of Markdown text is a text node; nothing from the file reaches the
studio's page as markup.

.

.

# Diagrams and conversions

Two studio tools: `render_diagram` draws a diagram from Mermaid source and checks it before it is
shown, and `convert_document` turns a finished file into another format. The `diagrams` skill
says how the agent uses the first; the `office-files` skill points at the second.

## `render_diagram`

Arguments: `name` (required; lower-case letters, digits, `-` and `_`, 60 at most), and `source`
(Mermaid text, 50,000 characters at most) **or** `path` (a `.mmd` file of the project), plus
`must_show?` (labels the diagram has to contain), `png_width?` (200 to 4,000 pixels), `colors?`
(`{"light": {…}, "dark": {…}}`: Mermaid theme variables such as `primaryColor`, `lineColor`,
`primaryTextColor`; names are letters and digits, values colours) and `alt?` (the picture's text
for the `embed` snippet).

**One renderer, one pinned version.** Mermaid 11.15.0 (MIT) is kept inside the server at
`server/studio2_server/static/vendor/mermaid/mermaid.min.js` (3.3 MB, byte for byte the file in
the npm package; version and licence in `THIRD_PARTY.md` beside it; the version is written in
code once, `diagram.MERMAID_VERSION`). It runs in the studio's own Chromium (the one `look_at_page`
uses), in a blank page whose every network request is refused: nothing is fetched at render time,
never from a CDN. Each call gets a fresh page and one fixed configuration: `securityLevel:
strict`, `look: classic`, `layout: dagre`, `htmlLabels: false` (labels are SVG text, not HTML),
font family `Helvetica, Arial, sans-serif`, deterministic ids, a Gantt chart 1,200 px wide. Two
more things keep the picture the same between runs: Mermaid's drawing code calls `Math.random`, so
the page gets a seeded one before each drawing, and a Gantt chart gets no "today" line unless its
source asks for one (`todayMarker`), since that line moves every day. The same source then gives
the same bytes (SVG and PNG) on the same computer; another computer's fonts can change the sizes.
A source may still set its own configuration (`%%{init: …}%%` or front matter); the studio does
not forbid it. Without the vendored file or without the Playwright Chromium the tool is listed as
unavailable with the reason (the look tool's reason, which says how to get one).

**Both schemes, every time.** Each call draws the diagram twice, with Mermaid's `default` theme and
its `dark` theme (or the `base` theme with the given `colors`), because a page in this studio
usually has dark styles and a picture drawn for light text on white is unreadable on dark. It
costs one more drawing in a page that is already open (about 20 ms of a call of 0.2 to 0.6 s).

**On a parse error nothing is saved.** The answer is
`{rendered: false, saved: false, name, error, line, line_text, next, ms}`: `error` is the parser's
message, `line` its line number in the source (from the parser, or read from the message; `null`
when it gives none), and `next` tells the agent to fix that line and call again, and after three
failed tries to use a simpler diagram or a table and say so. The studio does not count the tries.

**On success** it writes `files/diagrams/<name>.mmd` (the source), `<name>.svg` (light),
`<name>-dark.svg` and, with `png_width`, `<name>.png` (the light one on white at that width, for
decks and Word files), replacing earlier files of the same name, and answers:

```json
{"rendered": true, "saved": true, "name": "order-flow", "type": "flowchart-v2",
 "source": "files/diagrams/order-flow.mmd", "svg": "files/diagrams/order-flow.svg",
 "svg_dark": "files/diagrams/order-flow-dark.svg", "png": "files/diagrams/order-flow.png" | null,
 "png_width": 1200 | null, "size": {"width": 483, "height": 162}, "nodes": 4, "edges": 4 | null,
 "labels": ["Start here", "Is it ok?", "…"], "must_show": 2, "missing": ["Refund"],
 "layout": {"width": 483, "height": 162, "smallest_text_px": 16, "smallest_text_at_desktop_px": 16,
            "hard_to_read": false,
            "findings": [{"kind": "too_wide" | "small_text" | "clipped_text" | "overlapping_text",
                          "count": 1, "means": "…", "examples": [{"text": "…", "detail": "…"}]}]},
 "safe": {"scripts": 0, "outside_references": 0, "removed": [], "text_as_text": true, "fonts": "…"},
 "embed": "<picture>…</picture>", "renderer": "Mermaid 11.15.0 (the studio's own copy) in the studio's Chromium",
 "blocked_requests": [], "ms": 240, "next": "…"}
```

- **The second check, `must_show`.** Every label must appear in the drawing's text (case,
  white space and typographic quotes ignored; a label may be part of a longer one). Those not
  found are in `missing`, and `next` says the diagram rendered but is not what was asked. Without
  `must_show`, `next` asks the agent to compare `nodes` (shapes: boxes, participants, tasks,
  periods), `edges` (connecting lines and messages; `null` for a Gantt chart or a timeline) and
  `labels` (every piece of text drawn, at most 80) with what it meant.
- **The layout check.** Measured in the page on the light drawing at its own size: `too_wide`
  (wider than 1,400 px, so a desktop page shrinks it), `small_text` (the smallest text under 9 px
  once shrunk to 1,400), `clipped_text` (text past the side of its box, or past the edge of the
  picture, by more than 2 px) and `overlapping_text` (two pieces of text covering a quarter of the
  smaller one). Either of the first two sets `hard_to_read`, and `next` gives the remedy: split it
  or change its direction. The kinds are this tool's own; `look_at_page`'s `layout` measures HTML
  text, not SVG.
- **A safe file.** Mermaid's strict mode leaves out click handlers and links; the studio then
  removes from both SVGs every `script`, `iframe`, `object`, `embed`, `img`, `image`, `link`,
  `meta`, `audio` and `video` element, every `<a>` (its content kept), any `on…` attribute, any `href` or
  `src` that does not point inside the file (`#…`), and any `@import` or `url(…)` in a style that
  does not point inside the file (`removed` lists what was taken out). The saved text is then
  parsed again as XML and refused, with nothing saved, if a script or an outside reference is
  left. No font file is embedded or linked: the text names system fonts, and `safe.fonts` says
  so. `text_as_text` is false only when the source asked for HTML labels (`foreignObject`), which
  show in a browser but not in Office files.
- **Embedding.** `embed` is a `<picture>` that shows the dark SVG under `prefers-color-scheme:
  dark` and the light one otherwise, with `max-width:100%;height:auto`; paths are from the project
  root. The agent puts the saved SVG in the page, inline or as `<img>`, never the Mermaid script.

## `convert_document`

Arguments: `path` (a file of the project), `to` (the target extension, with or without the dot),
`sheet?` (the sheet to write as CSV), `reference?` (a `.docx` or `.odt` of the project whose styles
pandoc follows, for Markdown to that format), `replace?` (default false).

| From | To | Program | Verified (`tests/test_convert.py`, on a Mac with LibreOffice 26.8 and pandoc 3.9) |
|---|---|---|---|
| `.docx` | `.rtf`, `.odt`, `.pdf` | LibreOffice | each opens: RTF header, ODT archive with `content.xml` holding the text and the table, PDF pages |
| `.docx` | `.md` | pandoc (GitHub Markdown) | the text and the table come back as Markdown, a tracked insertion accepted |
| `.odt`, `.rtf` | `.docx` (and `.odt` ↔ `.rtf`, either to `.pdf`) | LibreOffice | `.odt` and `.rtf` to `.docx` open with the text; the others were run once by hand here (each opened), not in the tests |
| `.odt` | `.md` | pandoc | run once by hand here (text and table came back), not in the tests |
| `.pptx` | `.pdf`, `.odp` | LibreOffice | both open; the PDF has one page per slide |
| `.odp` | `.pptx`, `.pdf` | LibreOffice | run once by hand here (each opened; the PDF had a page per slide), not in the tests |
| `.xlsx` | `.ods`, `.pdf`, `.csv` | LibreOffice | ODS opens; a CSV of the named second sheet holds that sheet's values (UTF-8); a CSV of the first holds a formula's calculated result; `.pdf` was run once by hand here, not in the tests |
| `.ods` | `.xlsx`, `.csv` | LibreOffice | run once by hand here (each opened), not in the tests |
| `.md` | `.docx`, `.odt`, `.rtf`, `.epub`, `.html` | pandoc | each opens and holds the text; `reference` styles a `.docx`; a Mermaid block becomes a picture |

- **Where the result goes.** Beside the source under the same name with the new extension
  (`report.md` → `report.docx`); a source under `template/` or `sources/` is never written beside,
  so its result goes under `files/`. A CSV of one sheet of a workbook with several is named
  `<name>-<sheet>.csv`. When that file exists the tool refuses unless `replace: true` (the
  project's history keeps the old one); the source itself is never the target.
- **LibreOffice** is the one `render_document` uses (same profile, same lock: one at a time), with
  its time limit (180 s). A CSV is comma-separated UTF-8 of one sheet, with the values LibreOffice
  calculates; a workbook of several sheets needs `sheet`.
- **pandoc** reads Markdown from standard input (the source's folder and the project are where it
  finds pictures), with `--standalone` for RTF and HTML, `--embed-resources` for HTML (one file),
  a title from the file name when an EPUB has none, and `--reference-doc` for `reference`. A Word
  or OpenDocument file to Markdown is GitHub Markdown, unwrapped, its pictures saved in
  `<name>.media/` beside the result (`media`). Time limit 120 s.
- **Mermaid blocks** (```` ```mermaid ```` or `~~~mermaid`) in Markdown are drawn with
  `render_diagram`'s renderer (as `files/diagrams/<file>-diagram-<n>`, a PNG 1,200 px wide, or the
  SVG for HTML) and put in as pictures; a block that does not parse is left as code and named in
  `not_carried` with its line in the Markdown. Without the renderer they are left as code and
  `not_carried` says why.
- No shell line is built from the person's text: each program gets an argument list; paths are
  confined to the project with the helper every tool uses.

The answer:

```json
{"path": "files/report.docx", "from": "files/report.md", "size": 10897, "tool": "pandoc" | "LibreOffice",
 "version": "pandoc 3.9.0.2", "replaced": false, "opens": true, "pages": 3, "detail": null,
 "media": null, "diagrams": [{"n": 1, "picture": "files/diagrams/report-diagram-1.png"}],
 "not_carried": ["The styles are pandoc's plain defaults: …"], "ms": 410, "next": "…"}
```

`opens` is the result read back (an Office or EPUB archive with its main part, a PDF with its
pages counted in `pages`, RTF by its header, text formats as UTF-8); `detail` says what failed.
`not_carried` lists what the file holds that does not come across or was not checked, found by
reading the source: tracked changes (pandoc accepts them; for LibreOffice, not checked), comments,
a contents list (carried over as saved, not rebuilt: page numbers a script never filled in stay
empty), speaker notes and animations (not in a PDF), charts and conditional formats (not checked
in a workbook conversion), HTML inside Markdown (left out), pandoc's plain styles without
`reference`, fonts named but not included, and, for a PDF from `.docx`, `.pptx` or `.xlsx`, the
fonts the file names that are not among the PDF's fonts ("replaced by others"). pandoc's warnings
are added as they are. `next` tells the agent to call `render_document` on an Office or PDF result
and look at it, and `check_file` when it is a `.pptx`, `.docx`, `.xlsx` or `.pdf` that holds
figures.

When neither program is installed, the tool is listed as unavailable; when only one is, the pairs
of the other answer with what to install (macOS `brew install pandoc` or `brew install --cask
libreoffice`; Debian or Ubuntu `apt-get`; Fedora `dnf`). `studio doctor` and `install.sh` list
pandoc as optional, beside LibreOffice.

.

.

# Very large documents

Lane `big-documents`. A deck of a hundred slides, a long Word document or a workbook of many
sheets is built from a plan, by helpers working one section each, and checked deterministically
before anyone looks at a picture. The procedure is the studio's skill `large-documents`
(`library/skills/large-documents/`, with reference files beside it); the helpers are the library
agents `section-writer` and `slide-reviewer`. No new studio tool: `check_file` and
`render_document` gained what such a file needs, and every cap a large file reaches says so in
the result.

## The plan files

Kept in the project, so a later request can change one slide without redoing the rest. Only the
ledger is read by the studio; the others are the agent's own, and their shapes are what the skill
tells it to write.

| File | What it holds |
|---|---|
| `build/outline.json` | `{title, audience, purpose, kind: "deck"\|"document"\|"workbook", sections: [{id, title, module, slides: [{id, message, layout, facts: [keys], visual}]}]}`: one message per slide (a document: per chapter part, `pages` instead of `slides`; a workbook: `sheets: [{name, type, facts}]`) |
| `build/look.json` | The look of this one document, as named values, written from what the person asked for: `{fonts: {heading, body}, sizes: {…}, colors: {…}, layouts: {<name>: {…}}}`. Never a house theme shared between documents |
| `build/glossary.md` | The terms, names and abbreviations, each written one way |
| `build/slide-map.json` | Written by the assembler: `[{slide, section, id, module, layout}]`, so a defect found at "Slide 41" names the module to redo |
| `data/facts.json` | **The ledger**: every figure the document shows |

**The ledger**, `data/facts.json`:

```json
{"facts": [
  {"key": "revenue_2025", "label": "Revenue", "value": 4218950, "unit": "USD", "period": "FY2025",
   "source": "s3", "obtained": "quoted", "quote": "revenue was $4,218,950"},
  {"key": "units_2025", "label": "Units sold", "value": 2500, "unit": "units", "period": "2025",
   "source": "data/sales.csv", "obtained": "data", "how": "total of the column units"},
  {"key": "revenue_per_unit", "label": "Revenue per unit", "value": 1687.58, "unit": "USD",
   "period": "FY2025", "obtained": "computed", "from": ["revenue_2025", "units_2025"],
   "formula": "revenue_2025 / units_2025"},
  {"key": "target_margin", "label": "Target margin", "value": 18, "unit": "%", "period": "2026",
   "source": "person", "obtained": "given", "note": "Said in the request of 18 November"}]}
```

- `key`: a lower-case name (`[a-z][a-z0-9_]*`), unique. `value`: the figure in full, as a number
  (or a string holding one: `"12.4%"`), in its `unit`; a percentage is written as its number of
  percent (`12.4` with unit `%`). `label`, `unit` and `period` say what it is; `source` and
  `obtained` say where it came from.
- `obtained`: `quoted` (`source` is a saved source id; `quote` the words), `data` (`source` is a
  project data file under `data/`), `computed` (`from`: the keys it is made of, `formula`: an
  expression of those keys with numbers, `+ - * / **`, brackets and `abs`, `min`, `max`, `round`,
  `sum`), or `given` (the person stated it; `source: "person"`).

What `check_file` does with it: see below. The ledger lives under `data/`, so before this lane its
numbers already vouched as data; now it is read as a ledger whenever its top level is an object
with a `facts` list, and a fact vouches only when it passes these checks:

| Fact | Checked | A problem |
|---|---|---|
| any | a valid unique `key`, a number in `value`, an `obtained` of the four | `key` missing or repeated, no number, no `obtained` |
| `quoted` | the value is a number in the saved text of that source, exactly or as a rounding of it at the value's own precision (trailing zeros count as rounding: `4200000` is 4,218,950 rounded) | the source is not saved, or the value is not in it |
| `data` | the value is a number (or a column total) of that data file, the same way | the file is not a CSV or JSON under `data/`, or the value is not in it |
| `computed` | the formula is evaluated from the ledger values of `from` and agrees with `value` to half of the last digit `value` is written to | an unknown key, a name not in `from`, anything else in the formula, a fact that depends on itself, an input with a problem, or a result that disagrees (the recomputed value is named) |
| `given` | nothing: it is the person's own statement | — |

A ledger that is not JSON, or whose top level has no `facts` list, is read as an ordinary data
file and `ledger.error` says why.

## `check_file`: additions

Nothing was renamed; everything below is new in the saved result (`checks/<file>.json`) and the
tool's answer. The rules in "Verify content reaches files" hold unchanged for everything else.

**Ledger agreement.** Ledger facts are added before the pages' figures, so a figure that matches a
ledger value (exactly or as a rounding, as any figure) has `via: "ledger"` and `ref: <key>`; a
computed fact's item has `note: "computed in the ledger: <formula>"` and a given one's `note:
"given by the person; not checked against a source"`. `summary.ledger` counts the items found
through the ledger. A fact with a problem vouches for nothing: a figure that only it would have
matched falls to the data files, the sources, or `not_found`.

```json
"ledger": {"file": "data/facts.json", "facts": 42, "vouching": 40, "computed": 6, "given": 1,
           "problems": [{"key": "revenue_2025", "why": "4300000 is not in the saved text of s3."}],
           "problems_total": 1, "error": null}
```

`ledger` is null when the project has no `data/facts.json`. `problems` keeps at most 200
(`problems_total` counts all).

**Leftover placeholders.** `placeholders: [{text, where, context}]` (at most 500 kept) and
`summary.placeholders` (all). Read in the same text as the figures (slides' shapes and tables,
Word paragraphs and tables, text cells of a workbook, PDF pages), and found by:

| Kind | Found when | Not found (so a real subject is not flagged) |
|---|---|---|
| Office prompt text | `Click to add …`, `Click to edit …`, `Click icon to add …`, `Tap to add …` | |
| Filler text | `Lorem ipsum …`, `dolor sit amet` (any case) | |
| Template marks | `{{anything}}`, `${name}`, `{name}`, `<<name>>`, `«name»` (a name: letters, digits, `_`, `.`, `-`, no spaces) | `{ }` around words with spaces |
| Fill-ins in brackets | `[insert …]`, `[add …]`, `[enter …]`, `[placeholder]`, `[TBD]`, `[TODO]`, `[your …]`, `[source]`, `[citation needed]`, `[XX]` | `[1]` and other reference markers |
| Fill-ins with "here" | `your`/`insert`/`add`/`enter`/`type` + up to two words + `text`, `title`, `name`, `content`, `image`, `picture`, `logo`, `chart`, `data`, `heading`, `caption`, `quote`, `copy`, `number`, `date` or `value` + `here` | "your journey starts here" |
| Markers in capitals | `TBD`, `TBC`, `FIXME`, `XXX` (three or more X, or x), a masked figure (`$X.X million`, `XX%`), and `TODO` at the start of a text or followed by `:` | `TODO` inside a sentence ("the TODO app"), `todo`, `to-do` |
| An empty placeholder on a slide | a slide's placeholder shape (title, body, chart, picture or other; not the slide number, date, footer or header) with no text in it: PowerPoint shows its prompt there while the deck is edited. `text` is `(empty title)` or `(empty <type> placeholder)` | a filled chart or picture placeholder, which is no longer a text shape |

`{name}` in a technical document that shows template syntax on purpose is flagged; the agent says
so instead of removing it.

**Two figures for one label.** `conflicts: [{label, figures: [{text, where}]}]` (at most 200
kept, each with at most 6 figures) and `summary.conflicts` (all). A figure's label is read only
where it is unambiguous: the words written right before it and a colon or `=` (`Revenue 2025:
$4.2m`, back to the start of the line or the last `.`, `;`, `•`, `|`, bracket or comma), or, in a
table of a slide or a Word document, the row's first cell and the column's first cell
(`Revenue` and `2025` → `revenue 2025`). A label counts only with at least two words and a
letter (`Revenue` alone never does). Labels are compared without case, spaces or punctuation.
Two figures with one label conflict when they are both percentages or both not, and neither is
the other shown at its precision (`4.2m` and `4,218,950` agree; `4.2m` and `4.3m` do not), and
they are in different places: several figures in one cell or one line are one statement (a range,
`70% to 158%`), not a disagreement. Workbook
cells are not compared (a model repeats row and column names on every sheet; its consistency is
its formulas). A conflict is a warning to look at, not a not-found figure: the same label may
measure two different things on two slides.

**Where the problems are.** `by_part: [{part, not_found, placeholders, conflicts}]`: every slide,
page, sheet or heading (a Word document's nearest heading; `(before the first heading)`) with at
least one problem, in the file's order, so a large file's problems can be handed back to the
module that wrote them.

**The cap on items.** `items` keeps at most 10,000 figures (was 2,000); every `not_found` item is
kept even past it; `items_cut` counts those left out (0 when none).

**The tool's answer** adds, after `rounded`: `placeholders` (at most 30), `conflicts` (at most 15),
`ledger` (with at most 20 problems), `by_part` (at most 120) and `lists_cut` (true when a list of
problems in the answer, not found, placeholders, conflicts, ledger problems or parts, is shorter
than its count; the full lists are in the saved file).
`next` now names every kind of problem found, in this order: the file's error (then nothing else),
`not_read`, `not_found`, placeholders, conflicts, ledger problems, and where the full lists are
when the answer's are cut.

## `render_document`: additions

- **`pages`** (`"41-60"`, `"41"`, `"61-"`): which pages to draw as images, 1-based and inclusive;
  at most 60 in one call (a longer range is cut to 60 and said). Without it, pages 1 to 60, as
  before. For a workbook it picks the chart pages (default 1 to 20).
- **The PDF is reused.** The preview folder keeps `render.json` (the file's SHA-256). When the file
  is unchanged, a later call draws its pages from the PDF already made (no LibreOffice) and keeps
  the page images drawn before, so the viewer shows every page drawn so far; a changed file is
  converted again and the old images are removed. A workbook is always calculated again.
- **What was drawn, and what was not.** New result keys for decks, documents and PDFs:
  `pages_drawn` (`"41-60"`, this call), `not_drawn` (null, or a sentence naming the page ranges
  that have no image yet and the call that draws them: `call render_document again with pages:
  "61-104"`), `reused_pdf` (true or false). `next` starts with `not_drawn` when it is set (after the note on a cut range, when there is one), so
  "open every page image" never reads as the whole file.
- **An overview.** When the file has more than 20 pages: `overview: [paths]`, contact sheets
  (`<file>.preview/overview-<n>.png`) of every page, 20 to a sheet in five columns, each small
  page numbered, drawn from the PDF whatever `pages` is. They show the whole file's look at once;
  they are too small to read text.
- **A workbook with many sheets.** The answer stays readable: `start` is given for sheets in order
  until 12,000 characters of it are used; later sheets have `start: []` and `start_cut: true`.
  `errors` lists at most 30 per sheet and 100 in all (`error_count` per sheet is complete;
  `errors_listed` and `error_total` say how many were listed of how many). Chart pages: `chart_pages`
  as before plus `chart_page_count`, and `next` says which chart pages were not drawn.
- **Time.** LibreOffice gets 180 seconds plus 60 more for each 10 MB of the file (was 180 in all).

## The studio's own re-check: additions

- The `verification_recheck` event gains `not_rechecked: [paths]`: the pages past the first 10 and
  the files past the first 10 that the re-check did not read. `agrees` is false when it is not
  empty, and the card says how many were not re-checked and names them.
- A file's leftover placeholders are problems like its not-found figures: `{"page": "files/deck.pptx",
  "kind": "file", "what": "Click to add text", "where": "Slide 4, body", "why": "Leftover placeholder
  text."}`, after that file's not-found figures, within the same cap of 40. Conflicts and ledger
  problems are not re-check problems (a conflict may be two real figures; the ledger is not the file).

## The caps a large file reaches

| Cap | Value | What happens at it |
|---|---|---|
| `render_document` page images per call | 60 | `not_drawn` names the pages without an image and the call that draws them; `overview` shows every page small |
| `render_document` workbook chart pages per call | 20 by default, up to 60 with `pages` | `next` names the chart pages not drawn |
| `render_document` workbook answer | `start` up to 12,000 characters; errors 30 per sheet, 100 in all | `start_cut` per sheet; `errors_listed` of `error_total`; full grids in the preview page |
| `render_document` formulas checked | 200,000 per workbook (unchanged) | `unchecked` per sheet and `next` say how many were not checked |
| `render_document` preview grid | 400 rows × 50 columns per sheet (unchanged) | `cut` and `next` |
| LibreOffice time | 180 s + 60 s per 10 MB | the tool's error names the time |
| `check_file` items kept | 10,000 (every `not_found` kept) | `items_cut`; `summary` counts all |
| `check_file` cells read | 200,000 per workbook (unchanged) | `not_read` |
| `check_file` answer lists | not found 40, derived 15, rounded 15, placeholders 30, conflicts 15, ledger problems 20, parts 120 | `lists_cut: true` and `next` points at the saved file |
| A tool's answer | 60,000 characters (unchanged) | the answers above are sized to stay under it; a test builds a 104-slide deck and a 50-sheet workbook and checks they do |
| The re-check | 10 pages and 10 files (unchanged) | `not_rechecked`, `agrees: false`, said on the card |
| Pictures one agent opens | not a studio cap: each slide picture costs the reader up to about 1,600 tokens of its context (a picture is scaled to about 1.15 megapixels) | the skill has a reviewer look at 20 slides at a time and says which slides to look at past 60 |

# Context from this computer: folders, files and addresses

A person can point a project at things that already exist outside it, as context for the agent: a
folder of code (of any size), a single file, or the address where an app is running. Nothing is
copied, uploaded or indexed, and nothing there is changed. On every request the agent is told what
the items are and that the person's questions may be about them; it reads a folder where it is.

This is not "Bring an app by its folder". An attached app is the project: the agent edits it. A
context item is something the project's work refers to: the agent reads it and changes nothing there.

## ContextItem object

```json
{"id": "cx_1a2b3c4d", "kind": "folder", "path": "/Users/me/dev/crm", "address": null,
 "label": "crm", "note": "Our CRM. The API is in api/.", "added_at": "…",
 "found": {"git": {"branch": "main", "head": "<sha>", "subject": "…"}, "framework": {"id": "next", "label": "Next.js"},
           "files": 2310, "bytes": 48000000, "partial": false, "key_files": [".env"]}}
```

- `kind`: `folder`, `file` or `address`. `path` is set for a folder or a file (absolute, with links
  resolved), `address` for an address (as normalised below); the other is null.
- `label`: 1 to 60 characters; by default the folder's or file's name, or the address's host and port.
- `note`: at most 500 characters, what it is in the person's own words; the agent is given it. `""` when none.
- `found`: what the inspection found when the item was added (below), without `top` and `top_more`.
  It is not refreshed afterwards; removing the item and adding it again reads it anew.

A project holds at most **12** items. They live in the project record only: they are not part of
a version, a template, an export or a ship.

## Inspect

`POST /api/context/inspect {value}` → `ContextInspection`. Reads only. `value` is what the person
typed, and what it is decides the kind:

- it starts with `/` or `~` → a path (`~` is expanded): a folder or a file;
- it holds `://` → an address;
- it is `<host>:<port>` with an optional path (`localhost:3000`, `127.0.0.1:8000/admin`,
  `[::1]:5173`) → an address, with `http://` put in front;
- anything else → 400 `invalid_request`, "Type a folder's full path (~/dev/my-app) or an address
  (http://localhost:3000)."

```json
{"kind": "folder", "path": "/Users/me/dev/crm", "address": null, "label": "crm", "refused": null,
 "found": {"git": {"branch": "main", "head": "<sha>", "subject": "…"}, "framework": {"id": "next", "label": "Next.js"},
           "files": 2310, "bytes": 48000000, "partial": false, "key_files": [".env"],
           "top": ["api/", "src/", "README.md"], "top_more": 0},
 "notes": ["…"]}
```

`refused` is null, or the sentence that says why this cannot be context; `found` is then null.
`notes` are sentences for the person, shown under the result.

**A folder.** `found`:
- `git`: `{branch, head, subject}` when the folder is the top of a git work tree, else null. Read
  the way an attach inspection reads it (`GIT_OPTIONAL_LOCKS=0`: not even git's index is refreshed).
- `framework`: `{id, label}` from the table of known kinds in "Bring an app by its folder", or null.
- `files`, `bytes`: the files git tracks; for a folder without git, the files its `.gitignore`
  keeps, without `node_modules`, `.venv`, build output and caches. `partial` is true when the
  count stopped at 50,000 files. Both are null (and `partial` true) when the folder could not be
  counted within 20 seconds; it can still be added.
- `key_files`: the names of the key files at the top of the folder ("Key files"), never their contents.
- `top`: the names at the top of the folder, folders first (each ending in `/`), then files, each
  in name order with the names that start with a dot last, at most 40; `top_more` counts the rest.
  Left out: key files, and what is not the code (`.git`, `node_modules`, `.venv`, build output and
  caches: the names an attach inspection leaves out of its count).

Refused: nothing at the path; a path the studio cannot read; the top of a disk or a mount point;
the home folder; a folder that holds the home folder; a folder that holds the studio's own data
or its own repository (a folder inside them is fine); and, for a folder and a file alike, a hidden
file or folder at the top of the home folder or anything inside one (`~/.ssh`, `~/.aws`,
`~/.config/…`, `~/.zsh_history`: "This is in a hidden part of your home folder (~/.ssh), where keys
and settings live. The studio does not hand those to the agent as context.").

**A file.** `found`: `{"bytes": 1234}`. Refused: a key file by the one rule ("Key files"; "This is
a key file. The studio does not hand key files to the agent as context."), and anything that is
neither a regular file nor a folder.

**An address.** `http` or `https`, a host, an optional port, path and query; a fragment is
dropped. Any host is accepted: the studio does not proxy a context address (unlike the address of
an app edited in place), the agent requests it as it would any page. Refused: another scheme; an
address with a user name or password in it ("Leave the user name and password out of the address:
the studio never keeps one."); more than 2,000 characters. `found`:
`{"reachable": true, "status": 200, "title": "CRM", "this_computer": true}` after one `GET` (5
seconds at most, up to 3 redirects followed, the title read from the first 64 KB of an HTML
answer, null when there is none), or `{"reachable": false, "error": "Nothing answered at …",
"status": null, "title": null, "this_computer": true}`. `this_computer` is true for `localhost`,
`127.0.0.1` and `[::1]`. An address that does not answer is not refused: the app may be started
later, and a note says so.

Notes that are given: a folder of more than 5,000 files (the agent searches it and does not read
it whole); key files at its top (the agent is told not to open them); an address on another
machine (the agent reaches it from the studio's computer, not from the person's device); an
address that did not answer; and, on a shared studio, that a path names a folder on the computer
the studio runs on, not on the device of the person typing.

## REST

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/context/inspect` | `{value}` | `ContextInspection` |
| `GET /api/projects/{id}/context` | | `[ContextItem]`, each with `state` |
| `POST /api/projects/{id}/context` | `{value, label?, note?}` | `ContextItem` (201). 400 `invalid_request`; 400 `context_refused` with the inspection's reason (also for the project's own folder, or a folder or file inside it: "This is already part of the project."); 409 `already_added` when the project has this path or address; 400 `too_many` at 12 |
| `PATCH /api/projects/{id}/context/{item_id}` | `{label?, note?}` | `ContextItem`. 404 `unknown_context`; 400 `invalid_request` for an empty or too long label, or too long a note |
| `DELETE /api/projects/{id}/context/{item_id}` | | 204. 404 `unknown_context` |

`state` (in the list only) is `{"ok": true, "problem": null}`, or `ok` false with the sentence: a
folder or file that is not there or cannot be read now; an address that did not answer within 2
seconds (addresses are asked together, not one after another).

`Project` gains `context: [ContextItem]` (without `state`). A change made while a request runs
reaches the agent with the next message, since the project's facts are sent with every message.

## What the agent is told

When the project has context items, its facts for every message name each one: kind, label, full
path or address, what was found when it was added (the kind of app, the git branch, about how many
files), the person's note in quotes, and, read at that moment, whether it is there now and the
names at the top of a folder (the same list as `top`, at most 40). With them, the rules:

- The person's questions and requests may be about these; look there before answering from memory
  or guessing, and say which files an answer came from.
- A folder or file is read with the ordinary file tools and its full path. A folder may be very
  large: search it first, read only the files the question needs, never read it whole, and hand a
  broad question to sub-agents.
- **Read only.** Create, change or delete nothing there, and run nothing there that writes: no
  install, build, formatter or code generator, and no git command that changes anything. To try a
  change, copy the files into the project and work on the copy. When the person wants the code
  there itself changed, say that it was added as context, and that bringing the folder in as an
  app (New page, "An app on this computer") is how the studio edits one.
- Key files there (`.env` and the like) are not opened, unless the person asks for that file by name.
- An address is requested from the shell, and seen with `look_at_page` and `url` (below). It is
  read, not used: no form is submitted and no data changed there unless the person asks for it.
- An item that is not there now is said to be missing instead of being guessed at.

## `look_at_page`: `url`

`look_at_page` takes `url` in place of `path`: the full address of a page under one of the
project's context addresses (the same scheme, host and port). Any other address is an error that
names the context addresses. Such a page is loaded in a clean browser: it never carries the
person's sign-in or the agent's kept one, whatever the address (a context address may be another
project's page). Everything else about a look is the same.

## Held, and not held

- **Held.** The agent's file tools cannot write outside the project folder (the hook that refuses
  them is the same for every project). A shell command that names a path there to change it,
  redirects into it, or runs git that changes state there asks the person first ("Approvals").
- **Not held.** A program that writes as a side effect when run inside the folder (an install, a
  build, a formatter) is not recognised by the approval rules: the agent is told not to run one
  and nothing stops it. Nothing stops a shell command from reading a key file there either. As
  everywhere in the studio, the agent's shell is not confined.
- **A shared studio.** Context works there as attaching an app does: a path is a folder on the
  studio's computer, and whoever may use a shared studio can already have its agent read that
  computer. The inspection's note says whose computer the path is on.
- **Not built.** Bringing a folder from the person's own device to a studio that runs on another
  machine (a browser cannot hand over a path; files are attached or dropped in the chat);
  an index or search of the folder kept by the studio; choosing per message which items apply.

## UI

- **The chat.** Beside "Attach files" a button "Add context" opens a dialog: one field ("A folder,
  a file or an address", with `~/dev/my-app or http://localhost:3000` as its example), Check, then
  what was found, the notes, a label (filled in, editable), "What is it?" (optional, the note) and
  Add. A refused value shows its reason and cannot be added. While the project has items, a row
  above the message box shows them: "Context", then a chip for each (an icon for the kind, the
  label, the path or address as its tip), the first three and "+N" for the rest; a chip opens the
  panel below.
- **The project's library panel** gains a first tab, "Context" (with the count when there are
  items): each item with its label, its path or address, the note, what was found, what is wrong
  with it now (`state`), Edit (label and note) and Remove; and "Add context". The count on the
  panel's button in the top bar is the attached data sources plus the context items.

.

.
