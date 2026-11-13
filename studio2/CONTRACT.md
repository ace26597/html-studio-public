# Studio 2 — API and event contract (Step 1)

Server and web UI are built against this file. Change it first, then both sides.
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
| `DELETE /api/projects/{id}` | | 204. Moves the folder to `projects/.trash/` |
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
to another: whatever names a different endpoint is removed from what the agent program inherits.
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
the name the agent program reads. `STUDIO2_AGENT_ENV=NAME,NAME` passes named variables through.

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

A comment line (`: ping`) is sent every 15 seconds. The browser reconnects with
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
- Events are written to `events.jsonl` before they are sent.
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
`.git` is never served. Responses are `Cache-Control: no-store`. For `text/html`
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
  `STUDIO_PROJECT_URL` and `PYTHONUNBUFFERED=1` set. It must listen on `127.0.0.1:$PORT`.
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
  page's cookies. `X-Frame-Options` and the `frame-ancestors` part of a
  `Content-Security-Policy` are dropped from the app's answers, because the page is shown
  inside the studio.
- stdout and stderr go to `projects/<id>/app.log` (truncated at each start, capped at
  5 MB) and to an in-memory tail of 2,000 lines.

### App object

```json
{"configured": true, "run": "…", "state": "running", "pid": 123, "port": 8301,
 "exit_code": null, "started_at": "…", "error": null, "last_line": 57}
```

`state`: `none` (no valid `studio.json`), `starting` (process alive, port not answering
yet), `running`, `stopped` (stopped on request), `crashed` (exited by itself, or never
answered within 90 s; see `exit_code`). `error` explains an invalid `studio.json`.
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
| `turn_unqueued` | `queued_id`, `reason` | It was removed without running. `reason`: `cancelled`, `restart` (the studio restarted) |
| `approval_request` | `approval_id`, `tool`, `title`, `detail`, `reason` | The agent wants to do something risky and is waiting. `title` is a short line ("Run a shell command"), `detail` the command or path, `reason` why it needs approval |
| `approval_resolved` | `approval_id`, `decision`, `by` | `decision`: `allow`, `deny`. `by`: `user`, `turn_end` (the turn ended first: always `deny`) |
| `app_status` | `state`, `exit_code`, `run` | The project's app changed state. `turn_id` is the running turn or null |

- Queued messages run in order, each as its own turn, as soon as the turn before ends —
  also after a failed turn, but not after Stop: Stop also clears the queue
  (`turn_unqueued`, `cancelled`).
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
- nothing else. Reads, edits inside the project, ordinary commands and web access do not ask.

The rules read the command, not a file's text: the body of a here-document that `cat` or `tee`
writes to a file (`cat > notes.py <<'EOF' … EOF`) is skipped, while where it writes and
everything after it are still checked. A here-document handed to another language (`python3 - <<'EOF'`, `node`, …)
is a program, not shell: only the rules that name programs apply to it, not the checks on
redirections and file commands. That holds when the interpreter is started through a runner
(`uv run python - <<'EOF'`, `poetry run`, `pipenv run`, `env`, `npx`, …) and when the
here-document is piped into one (`cat <<'EOF' | python3 -`). Only a body fed to a shell (`sh`,
`bash`, `zsh`, …) is read as commands; any other reader's body gets only the rules that name
programs. Text inside quotes (`'…'`, `"…"`) is never read as a redirection or a command
separator: `python -c "print('<b>>~$30,000</b>')"` writes nothing.

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

`GET /api/projects/{id}/look?path=/&width=1440&height=900&dark=0&full_page=1&wait_ms=1200`
loads the project URL + `path` in a headless Chromium that is already cached on the machine
(never downloaded) and returns:

```json
{"image": "/abs/…/projects/<id>/looks/look-<ms>.jpg", "url": "…", "status": 200, "title": "…",
 "viewport": {"width": 1440, "height": 900}, "dark": false, "page_height": 2410,
 "captured_height": 2410, "cut_off": false, "wider_than_viewport": false,
 "console": [{"level": "error|warning|exception", "text": "…"}],
 "failed_requests": [{"url": "…", "status": 404, "error": null}], "text_start": "…"}
```

The screenshot is capped at 6,000 px tall (`cut_off`), the last 12 are kept, and they live
outside the workspace so they are not versioned. 503 `look_unavailable` when no cached browser
or no `playwright` package exists; the agent is then told it cannot see the page.

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
 "port": 5432, "database": "analytics", "user": "reader", "ssl": "require",
 "has_password": true, "created_at": "…", "last_test": {"ok": true, "at": "…", "message": "…"} | null}
```

`kind`: `postgres`, `redshift`, `mysql`. `ssl`: `disable`, `prefer`, `require`, `verify-full`
(default `prefer`; `redshift` defaults to `require`). `name` is unique, 1–60 characters.

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/data-sources` | | `[DataSource]` |
| `POST /api/data-sources` | `{name, kind, host, port?, database, user, password?, ssl?}` | `DataSource` (201). 400 `invalid_request` with a message naming the field; 409 `name_taken` |
| `PATCH /api/data-sources/{id}` | any of the same fields; `password: ""` removes the stored password | `DataSource` |
| `DELETE /api/data-sources/{id}` | | 204. Also detaches it from every project and deletes the stored password |
| `POST /api/data-sources/{id}/test` | | `{ok, message, server_version?, tables?}`; always 200 for a known id. Connects, runs a trivial query, counts visible tables. Updates `last_test` |
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

## Other changes

- A gateway connection is checked with one tiny request before a turn starts, so a wrong
  key, an unknown model or an unreachable gateway fails in seconds with the gateway's own
  message instead of after the CLI's retries.
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
| `render_document` | `path`, `store_results?` (default true) | Renders an Office or PDF file of the project into a preview folder next to it (see "Office documents") |
| `render_video` | `path`, `out?`, `format?`, `width?`, `height?`, `fps?`, `duration?`, `start?`, `scale?`, `audio?`, `volume?`, `check?`, `times?`, `frames?`, `poster_at?` | Renders an animated page of the project to a video file, frame by frame (see "Video and images from a page") |
| `render_image` | `path`, `out`, `width?`, `height?`, `scale?`, `selector?`, `transparent?`, `dark?`, `at?`, `wait_ms?` | Saves a picture of a page, or of one element of it, as a `.png`, `.jpg` or `.webp` file in the project |
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
- `name` is what tools are called by: `mcp__<name>__<tool>`; lower-case letters, digits, `-`
  and `_`, 1–40 characters, unique, not `studio`.

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/mcp-servers` | `{name, label?, transport, url? , command?, args?, headers?: {name: value}, env?: {name: value}, description?}` | `McpServer` (201). 400 `invalid_request`, 409 `name_taken` |
| `PATCH /api/mcp-servers/{id}` | any of the same fields | `McpServer`; only `yours` |
| `DELETE /api/mcp-servers/{id}` | | 204; only `yours`. Removes it from every project and deletes its stored secrets |
| `POST /api/mcp-servers/{id}/test` | | `{ok, message, tools: [{name, description}]}`; connects, lists tools (at most 200), 20 s limit. Works for every source; updates `last_test` for `yours` |
| `POST /api/skills` | `{name, description, body}` | the skill (201); writes `data/skills/<name>/SKILL.md`. `name` as for servers, ≤ 60 |
| `DELETE /api/skills/{name}` | | 204; only `yours` |
| `GET /api/projects/{id}/library` | | `{"skills": [{…skill, "enabled": bool}], "mcp_servers": [{…McpServer, "enabled": bool}]}` (`built-in` skills are not listed) |
| `PUT /api/projects/{id}/library` | `{"skills"?: {id: bool}, "mcp_servers"?: {id: bool}}` | the same object as the GET. Unknown ids → 400. Takes effect at the project's next turn |

`Project` gains `library: {"skills": {id: bool}, "mcp_servers": {id: bool}}` holding only
the person's deviations from the defaults. At most 12 MCP servers enabled per project.

A turn that could not connect to an enabled MCP server still runs; it carries a `notice`
(`warning`) naming the server.

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
  cookie: a page request is redirected to `http://<host>:<studio port>/login?next=…`
  (also from a project port), anything else gets 401 `access_denied`, a WebSocket is closed
  with 4401. Eight wrong keys from one address within five minutes → 429.
- `Project.preview_url` uses the host the request was addressed to, so a project opened
  from `http://100.x.y.z:8200/` gets `http://100.x.y.z:<port>/`. Clients must use it as
  sent and never build project addresses themselves.
- Mutating API requests are accepted from the studio's own origin: the configured loopback
  origins, or `http://<Host header>`.

- `STUDIO2_TLS_CERT` and `STUDIO2_TLS_KEY` (both, files) make the extra addresses speak HTTPS,
  on the studio port and every project port; loopback stays plain HTTP. Redirects, the
  `Origin` check and `Project.preview_url` then use `https://`, and the cookie is `Secure`.
  The address to use is the name on the certificate, not the IP.
- The sign-in page may be framed only by a page on the same host name (any port):
  `Content-Security-Policy: frame-ancestors 'self' https://<name> https://<name>:* …`.

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
own browser (a chosen filter, an open tab, something typed) is not in them. The mark list is
taken from the person's live page and is the more reliable of the two.


---

# Models for a project's back end

A project's app does not inherit the studio's environment: it gets `PATH`, `HOME`, `USER`,
`LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `LC_*`, `TERM`, `TZ` and a few cache locations, plus what
the studio sets for it (`PORT`, `HOST`, `STUDIO_PROJECT_ID`, `STUDIO_PROJECT_URL`,
`STUDIO_TOKEN`, `STUDIO_DATA_URL`, `STUDIO_MODEL_URL`). No key or token of the person's or
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
| `.pptx` `.ppt` `.odp`, `.docx` `.doc` `.rtf` `.odt` | `document.pdf`, `page-<n>.png` (1600 px wide for slides, 1240 px for pages, at most 60), `index.html` | `{file, kind, pdf, pages: [path], page_count, preview, embed, next}` |
| `.pdf` | `page-<n>.png`, `index.html` | the same, with `pdf` the file itself |
| `.xlsx` `.xlsm` `.xls` `.ods` | `index.html` | `{file, kind, sheets: [{name, rows, columns, formulas, errors: [{cell, formula, value}], start: [text rows]}], formula_results_stored, preview, embed, next}` |

`kind` is `presentation`, `document`, `workbook` or `pdf`. `index.html` is a self-contained
viewer (slides with a film strip, pages in a scroll, or sheets as tables with a formula bar),
with Download and, where there is one, Open PDF. It needs nothing from the studio, so it works
in a downloaded project. `embed` is an `<iframe>` for it.

For a workbook every formula is calculated (LibreOffice, set to recalculate on load) and the
results are compared cell by cell: a formula whose result is an error value or empty is listed
under `errors`. With `store_results` (the default) the results are also written into the
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
- `has_code`: the template contains `studio.json` or scripts. `imported`: it came from a file.
- Built-in templates have `source: "studio"`, `mode: "copy"`, `kind` `app` or `page`,
  `preview: null`.

## What is saved

A copy of the project's files as they are, without `.git`, `sources/`, `checks/`,
`attachments/`, `research/`, dependency folders and caches, and:

| Kind | Also left out | Kept |
|---|---|---|
| `page` | `data/`, `files/`, `tools/` | pages, styles, scripts, images |
| `app` | `data/` | everything else, including `studio.json` |
| `deck`, `workbook`, `document` | everything except → | the chosen file, its `.preview/` folder, `tools/`, `files/img/` |

Plus `TEMPLATE.md` (what it is, its files, how to use it, the person's description and any
notes on how it was made) and a preview image (a screenshot of the page, or the first page of
the Office file). At most 2,000 files and 40 MB; otherwise 413 `template_too_large`.

## REST

| Route | Body | Answer |
|---|---|---|
| `POST /api/projects/{id}/template` | `{name, description?, kind?, file?, notes?}` | 201 Template. `kind` defaults to `app` when the project has a back end, else `page`; `file` (a project path to a .pptx, .xlsx or .docx) sets the kind from the file. 400 `invalid_template` for an empty name, a wrong kind or a missing file |
| `PATCH /api/templates/{id}` | `{label?, description?}` | Template. 400 `builtin_template` for a built-in one |
| `DELETE /api/templates/{id}` | | 204. Projects that use it keep their copy |
| `GET /api/templates/{id}/preview` | | the preview image, or 404 |
| `GET /api/templates/{id}/export` | | `application/zip`, named `<label>.studio-template.zip` |
| `POST /api/templates/import` | multipart `file`, optional form field `confirm=1` | 201 Template. A template with `has_code` needs `confirm=1`: without it 409 `needs_confirmation`, because an app template runs on this machine when a project is started from it. 400 `invalid_template` for a file that is not a template; paths that leave the template, links and oversize files are refused |
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
`<out>.jpg` beside the file; `embed` is a `<video>` (or `<img>` for a GIF) with a download
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
4. One job runs at a time. The result is downloaded into the project at `out` (the extension
   follows what the service returned) and the spend (the fall in the balance) is appended to
   `data/studio2/generation-usage.jsonl` with the project, model, prompt and outcome.
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
 "package_manager": "npm", "install": "npm ci", "env_files": [".env", ".env.local"],
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
- `env_files`: names only (`.env`, `.env.*`), never contents.
- `size.files`/`size.bytes`: what a copy would take (tracked files for git; for a plain folder,
  the files its `.gitignore` keeps, without `node_modules`, `.venv`, build output and caches).
  `history_bytes` is the size of `.git`. `partial` is true when the count stopped at 50,000 files.
- `refused.copy`/`refused.in_place`: null, or the sentence that says why that way in is not
  possible. A missing or unreadable folder refuses both.

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
`composer.json` → `composer install`. `<python>` is `.venv/bin/python` when the install makes
one, `uv run python` with a `uv.lock`, else `python3`.

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

### Safe copy

- A git folder becomes `git clone --no-hardlinks <path> workspace` (the app's whole history
  comes along), checked out on a new branch `studio/<name>-<id>` at the original's current
  commit. The clone's `origin` remote is removed, so nothing in the copy can push to the
  original; step 3l moves work back by fetching from the copy. A plain folder becomes a copy
  of the files its `.gitignore` keeps (without `node_modules`, `.venv`, build output and
  caches) in a new git repository.
- Refused above 2 GB (history plus files) or 50,000 files for a git folder, and above 500 MB or
  20,000 files for a plain folder.
- `studio.json` (`{"run": …, "restart": "never"}`, when there is a command), the copied env files,
  `node_modules/`, `.venv/` and `__pycache__/` are listed in the copy's `.git/info/exclude`:
  they never enter a version, so the studio branch holds only the app's own files.
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
  `__pycache__/`, `.env` and `.env.*`; the app's own `.gitignore` files apply too.
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
           "included_uncommitted": false, "env_files": [".env.local"], "install": "npm ci",
           "address": null, "attached_at": "…"},
"app_config": null
```

`attach` is null for other projects. `kind` is `git` or `folder`; `branch`, `started_from` and
`studio_branch` are null for a plain folder; `studio_branch` is null in place.
`uncommitted` is what the original had at that moment (at most 200 entries).

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
  commands again.
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
   is nothing new (`nothing_to_ship`), or the original is not in a state the method can take
   (`ship_refused`, message with the reason and "Next: …").
2. The workspace is saved as a version ("Changes before making it live"); the ship works from
   that saved version (`head`), never from files on disk.
3. Checks run in the copy.
4. The card (`ship_ready`): the files that will change, the check results, exactly what will be
   written where (`writes`), the commands and the folder they run in, the address, protected
   paths touched, how going back works, and the limits. The person allows or denies, every time.
5. On Allow the plan is made again; if the original, the branch or the copy changed while the
   card was open the ship ends `refused` and nothing is written.
6. Apply; then the after-commands in the original; then the address.
7. When a step after apply fails, the studio goes back by itself: `merge` resets the original's
   branch to where it was (`git reset --hard`; there were no uncommitted changes) and puts the
   studio branch back (or removes it); `files` puts back every file it wrote or removed. Then it
   runs `back` (or the after-commands again). Objects fetched into the original's `.git` stay
   there, unreferenced (git removes them in its own time). `none` has nothing to put back.
8. A record is written: `projects/<id>/ships/<ship_id>/record.json` (with `logs/` holding each
   command's full output and `backup/` the files a `files` ship replaced).

While a ship runs, turns are refused with 409 `ship_running` (a message sent with `queue: true`
waits and runs when the ship ends), and so are file writes, uploads, inline edits and restore.
"Make it live" asked for by the agent during a turn starts when that turn ends (not after Stop
or a failed turn). A studio restart during a ship ends it `interrupted`; a change that was
already being applied is put back at start-up, without running the after-commands again
(the record says so). Limits, stated on every card: data and database changes are not undone
by going back; where the change ends up is whatever the recipe's commands do.

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
studio could not put everything back: the message says what to check), `failed`, `interrupted`.

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
  `api_key_secret`) or `api_key_env`. The connection gets the kind's usual id (`subscription`,
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
`studio2/.venv`; Node 20 or newer (the one on PATH, else `studio2/.tools/node` from the
nodejs.org tarball, sha256 checked against values written in the script); the interface build;
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
- No paid key is involved. A third service was looked for and not added: Bing answered but with
  unrelated results for some queries, and Brave, Mojeek, Startpage, Ecosia and Qwant refused or
  sent a challenge from this machine.

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
without one, the key already saved for the account is used.

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
   time it was tried; an attached app's own connectors unless pinned (its `.mcp.json` came with
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
   one-word request), and up to three connectors whose name appears in the request
   (`mode: "words"`, with a `note` saying why). Shared words do not show relevance, so a long
   request usually gets no skill this way and the `note` says none was added; the agent still
   has `search_library` and `activate_skill`.
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
| `request_connector` | `{id, reason?}` | `{connector, connected: false, joins: "the next request", next, sign_in?}`. A running agent program cannot be given a connector, so the id is added to the project's `library.requested` and the connector joins the next request (`how: "requested"`), for the rest of the project or until the person says never. `connected: true` when it is already there. Refused for a switched-off connector and for one that needs a setting |

## What the person says, and what is counted

- Project: `PUT /api/projects/{id}/library` with `{"skills"?: {id: true | false | null}, "mcp_servers"?: {…}}`.
  `true` = always in this project, `false` = never in this project, `null` = leave it to the
  studio. Choices made by hand before this step keep their meaning: they are pins (`true`) and
  switch-offs (`false`). At most 12 pinned connectors.
- Whole machine: `PUT /api/library/prefs` with `{"skills"?: {id: "always" | "never" | null}, "mcp_servers"?: {…}}`
  → the preferences (`data/library-prefs.json`). The project's word wins over the machine's.
- `data/library-usage.json`, per skill id: `picked` (requests it was picked for by the model
  or the word search, counted when the turn completes), `used` (requests in which the agent
  called it: the agent program's `Skill` tool call, or `activate_skill`), `streak` (picked
  without use, in a row; any use, or an "always" or back-to-automatic from the person, resets
  it). At `streak` ≥ 5 the skill is `benched`: no longer picked, still found by search.
- What the agent program reported for each connector at the start of a turn (`connected`,
  `needs-auth`, `failed`) is kept in `data/library-status.json` and shown as `state`; for
  `needs-auth` the `fix` is the command `claude mcp login <name>`.

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

The studio never works a cost out from token counts itself, and never shows a request nobody priced
as free. Every money figure it shows says where it came from (`basis`):

- `reported`: the provider sent a cost with the answer and the route recorded it. Today only
  OpenRouter does this (LiteLLM keeps its `usage.cost`).
- `calculated`: token counts times a price list, worked out by LiteLLM (OpenAI, Azure OpenAI,
  Gemini and anything else that returns only tokens) or by the agent program (an Anthropic key,
  Bedrock, Vertex, Foundry). Shown with the word "calculated".
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
Turns written before 13 November 2026 have the running total in `cost_usd` and no
`session_cost_usd`; the project total and the chat work their part out the same way when they read
them. Until that day the chat showed the running total as each turn's cost.

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
(`basis`: `reported`, `calculated`, or null when nothing was priced). `turn_finished.usage` carries
`cost_basis` the same way, with `plan` for the subscription.
`source` is `gateway` (read back by tag), `recovered` (see below) or `unavailable`. When it is
`unavailable` `cost_usd` is null and `reason` says why in a sentence for the person: the gateway
cannot be reached; it answered 401 or 403 (the key cannot read the spend log); it answered 404 (it
is not LiteLLM, or it keeps no database); it answered something else; or it had no request tagged
for this turn. `cost_usd` is null whenever no request was priced, even if `requests` is above zero.
The latest `turn_cost` of a turn replaces the `cost_usd` of its `turn_finished`.

**Turns from before tagging** have no tag. `POST /api/projects/{id}/cost/recover` looks each
finished gateway turn that has no price up in the gateway's spend log by what the log does record:
the agent program's session id (`turn_finished.session_id`) and the turn's time window (from
`turn_started` to five seconds after `turn_finished`). It writes `turn_cost` with `source:
"recovered"`. The interface says "found by session and time". It adds nothing for a turn it cannot
match, and never writes a figure for a window in which the log has no row of that session.

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
  check_only?: boolean}`. Unknown keys or values → 400 `invalid_options`. Left out: `standard`, no verify.
  `check_only: true` marks a request that only checks the page (the **Check this page** button):
  it implies `verify: true`, and the studio picks no skills or connectors by itself for it (see
  "Verification" below). `check_only: false` is the same as leaving it out.
- `PATCH /api/projects/{id}` takes `turn_options` (`{depth?, verify?}`, same 400; `check_only` is a
  property of one request and is refused here): the project's remembered choice, kept in the
  project record as `turn_options` (`{depth, verify}`; absent = standard, off).
  A request's own `options` are not saved by sending it; the UI saves the choice when it changes.
- `turn_started` carries `options` (`{depth, verify}`, always both keys, plus `check_only: true`
  only for a check-only request). `turn_queued` carries the options of the queued message, and they
  are the ones it runs with.

## What the agent receives

The options become one `<studio-effort>` block in the composed message, after the text and the
project facts. The wording lives in one place, `server/studio2_server/effort.py`. `standard` with
verify off adds nothing: the message is byte for byte what it was before this step. The wording
describes intent and never promises a length in words or pages.

With `depth: "deep"` the picker also adds the studio's `deep-research` skill to the request
(`how: "depth"`), unless the person switched it off in the project.

## Verification

With verify on, the agent checks everything the person can see on the project's page(s) after its
work, through the `checker` sub-agent (an independent context), fixes what is certainly wrong and
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

When a request wrote at least one `verification` event, the studio itself runs the page check
once more after the agent has finished (before the version is saved), on `index.html` when it
exists and on every other page `check_page` was run on during the request. It does not write
`checks/<page>.json` (the agent's last check stays the saved one) and it never sends the agent
back. It writes one event:

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
read. A page the check could not read has `error` set and adds no problems. No event when there is no page to check. What the
re-check covers is only what the page check can see: marked figures against saved sources and
project data, and cited sources against the source list. It knows nothing of charts, totals that
are not marked, or an app's behaviour; the card says so.

`check_only` requests: the picker adds only what the person chose (skills and connectors they
said "always" for, here or for the machine). Nothing is added by itself: no `all`, `earlier` or
`picked` skills and no `requested` or `picked` connectors. `library_picked` then has `mode: "none"`
and a `note` saying so.

## UI

The composer has a three-step **Quick / Standard / Deep** control and a **Check the result** switch,
with one plain line saying what the step means (Deep takes longer and uses more of the model
allowance or money). **Check this page** sends a request that only verifies (verify on, no new
content; it sends `options: {depth: "standard", verify: true, check_only: true}`). The chat shows
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

| Step `id` | Offered when | Chip | Text |
|---|---|---|---|
| `fix` | `still_wrong > 0` | Fix what the check still finds | Fix the N items the studio's own check still finds wrong on this page (they are listed on the check card), then run the check again. |
| `failed` | `reported`, `failed > 0`, no `fix` | Work through the failed items | Work through the N items the check marked as failed: fix those a better source settles, and tell me which ones need my decision. |
| `check` | `has_page`, not `reported`, and `changed` or `verify` | Check this page | The check-only text, with `options: {depth: "standard", verify: true, check_only: true}` |

At most one studio step is offered (the first that applies, in this order).

## The event

When a request ends `completed` and there is at least one step, the studio writes one event after
the version is saved and before `turn_finished`:

```json
{"type": "next_steps", "steps": [
  {"id": "check", "from": "studio", "label": "Check this page",
   "text": "Check this page: verify everything on it, and change nothing except certain fixes.",
   "options": {"depth": "standard", "verify": true, "check_only": true}},
  {"id": "a1", "from": "agent", "label": "Add a section on battery life", "text": "Add a section on …"}]}
```

The studio's step first, then the agent's in the order given (`a1`, `a2`, `a3`); at most 4 in all.
An agent step whose label equals the studio step's label (letter case ignored) is dropped.
`options` is present only on a step that needs them. A request that was stopped or failed writes
no `next_steps`. Never more than one per request.

## UI

Chips are shown under the last request only, when it completed and nothing is running or queued;
an older request's chips are not shown again. A tap puts `text` in the chat box (after what is
already typed, on a new line, when the box is not empty), puts the caret there and sends nothing.
When the person sends a message whose text is exactly a shown step's `text` and that step has
`options`, the message goes with those options (so "Check this page" from a chip is the same
check-only request as the button); any other text goes with the project's remembered options.
On a phone the chips wrap and each is at least 36 px tall.
