# Contract: Core

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

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
resume. `connection`/`model` are the project's last used choice. `instructions` (a string, `""`
when there are none) is what the person wants done in every request of this project (see
"Instructions for a project"). The studio never renames a project.
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
| `POST /api/projects/{id}/restore` | `{sha}` | `{checkpoint}`. New commit whose tree equals `sha`, except files too large for versions, which stay on disk as they are ("Files too large for versions"). 409 `turn_running` during a turn |
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
| `checkpoint` | `sha`, `kind`, `message`, `files:[{path, status}]`, `left_out?:[{path, size}]`, `left_out_count?` | A git commit was created. `left_out` (at most 50, largest first) and `left_out_count` (all of them) are there only when files too large for versions were left out of it (see "Files too large for versions") |
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
is shown with its reason. The system prompt tells the agent that the person exports from there.

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
reaches the agent with the next message: the project's standing facts are sent again whenever they
change.

## What the agent is told

When the project has context items, its standing facts name each one: kind, label, full
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

# Which code a studio is running

A studio's interface is files on disk, served as they are: a new build shows at once, on every
studio that runs from that folder. Its server is a process: it keeps the code it started with
until it is restarted. So one folder can serve two studios with the same interface and
different servers, and a studio can show an interface newer than its server. This makes that
visible instead of hiding the newer features quietly.

- **The stamp.** At start the server notes the code it runs: in a git checkout, the short hash
  and date of the last commit that changed `studio2/server`; anywhere else, a fingerprint of the
  server's own source files. It notes when it started.
- **`GET /api/health` gains `build`:** `{commit (string or null), committed_at (ISO or null),
  started_at (ISO), stale (boolean), newer (string or null)}`. `stale` is true when the code on
  disk is no longer the code this process started with: in a git checkout, a later commit has
  changed `studio2/server` (uncommitted work in progress does not count); elsewhere, the source
  fingerprint differs. `newer` is that later commit's short hash. The comparison is made on
  request and kept for ten seconds.
- **UI.** When `build.stale` is true, or the server sends no `build` at all (it was started
  before this existed, so it is older than the interface), every page shows one notice: that
  this studio's server is older than its interface, since when it has been running, that newer
  features stay hidden until it is restarted, and (on a shared studio) that its owner does that.
  It can be put away for the browser session; it comes back when the server's start time or the
  newer commit changes. The Home page's foot says which code the server runs and since when.
- A restart is still a person's act: nothing here restarts a studio.

# Big projects: a plan the person sees, notes that last, and room for a few requests at once

A big project runs over many requests and conversations. Three things keep it on track.

## The plan: the tool `set_plan`

```
set_plan {items: [{text, status}]}      status: "pending" | "doing" | "done"
```

- 0 to 30 items, each `text` 1 to 200 characters (longer is refused, not cut). The list replaces
  the whole plan; an empty list clears it. The answer says how many items are done and left.
- It belongs to the project, not to one conversation. It is kept in the project record as
  `plan: {items, turn_id, updated_at}` (`items` as given, `turn_id` the request that set it), never
  in the project's files: setting it does not reload the preview, make a version or show as a
  change the person made.
- It writes one `plan` event of the running request, the event the chat already draws, with each
  status in the chat's words: `{"type": "plan", "entries": [{content, status}], "source": "set_plan"}`
  where `pending` stays `pending`, `doing` is `in_progress` and `done` is `completed`. Events are
  kept, so the plan is drawn again after a reload or a restart.
- While the plan has an item that is not done, it is part of the current facts sent with every
  message, one line per item with its state (at most 30 lines):
  `- The plan for this project (set_plan; set in an earlier request or this one). Keep it current:`
  then `  - [done] …`, `  - [doing] …`, `  - [ ] …`. A plan whose items are all done is not sent.
- Only the main agent has it: the researcher's `disallowedTools` line names
  `mcp__studio__set_plan`; the other helpers' `tools:` lists do not.
- The harness draws it as the plan, not as a tool row (as it does for `TodoWrite`). The plan the
  agent program's own task tools make (`TodoWrite`, `TaskCreate`) still makes `plan` events, but
  it is not kept in the project record or sent in the facts.
- The chat shows the newest plan only. At the request that last changed it: pinned while that
  request runs, folded afterwards ("Plan · 2 of 5 done"). When a newer request is the last one
  and the plan is unfinished, it is shown folded there instead, with the start of the request
  that last changed it. Done items are struck through. An empty plan shows nothing.
- The chat keeps the event's `source`. The project's plan is the newest `set_plan` one: a task list
  of the agent program in a later request never hides it. A task list is drawn only in a thread
  that has no `set_plan` plan.

## Project notes: `notes/project.md`

- A file the agent keeps for later requests: what the project is, the decisions the person made,
  conventions (file layout, where data comes from, style), and what is still open. Short dated
  lines; a stale line is replaced, not added to. Never a secret, key or password.
- When it exists, its text is part of the standing facts (sent once per conversation, and again
  when it changes), at most 6,000 characters (the studio reads at most the first 64 KB of the file,
  so a huge file costs nothing); a longer file is cut and a line says so:
  `- The project's notes (notes/project.md; keep them current):` then the text, indented.
  No file, or an empty one: nothing is added.
- The agent edits it with its own file tools; the person reads and edits it in the Files panel
  under "Working files". It is versioned with the project, so restoring a version restores it,
  and an edit the person makes there is in `<studio-user-changes>` like any file.
- A change to a file under `notes/` does not reload the preview: `notes/` holds no page content.
  The `files_changed` event is still written (the Files panel lists it).
- It is left out of exports (as before) and out of a saved template (`notes/` at the top of the
  project, like `state/`).
- Not in an attached app: its lines say not to add the file there (an app edited in place is the
  person's own folder, and a safe copy's work can go back into it). A notes file already there is
  still read.

## Room for a few requests at once

- A working agent's processes peak near 0.8 GB. `STUDIO2_MAX_TURNS` (default `4`; `0` means no
  limit) caps how many requests run at once in one studio, over all its projects. A model check's
  requests count too.
- A request that has waited for the person (an approval card or an `ask_person` card) for more
  than 5 minutes no longer takes room: a card left open for days must not block the studio. When
  the 5 minutes pass, a queued message may start in its place. When the person answers and it
  carries on, it takes room again, so for a while one more request than the limit may run.
- A new request that would go over it is refused: HTTP 429, code `studio_busy`, message
  "The studio is already running 4 requests, its limit. Try again when one finishes.", with
  `details: {running, limit}` (`running` here counts only the requests that take room). The chat
  shows it above the message box, and the text stays in the box. Starting a request on a new
  project ("ask about this skill") is refused before the project is made.
- A message queued in a project (`queue: true`) is never refused for this: it waits. When its
  turn comes and the studio is full, it stays in the queue and starts as soon as any request in
  the studio ends, oldest queued first. So the limit is never passed.
- A project that already has messages waiting puts a new message behind them, even when no request
  of its own runs and the message did not ask to be queued: it is not refused while older ones
  wait, and it never starts ahead of them. The interface asks for `queue: true` whenever a request
  runs or messages wait.
- A model check waits for room before each of its messages, so a full studio is never recorded as
  the model failing.
- The queue lives in the studio's memory. A restart of the studio drops what was waiting; the chat
  then says which message was not sent and offers to put its text back in the message box.
- **On a shared studio the limit holds guests back, not its owner.** A request the owner sends (the
  gate signed in one of `STUDIO2_OWNERS`) is never refused for the limit and never waits behind a
  guest's: it starts at once, or, when its own project is busy, waits only for that project. The
  owner's requests still count, so guests get the room that is left. In a project's queue the
  owner's messages go before guests' (each keeping its own order), and when room appears the
  owner's waiting messages start first. A model check counts as the owner's.
- **On a shared studio a project holds at most 20 waiting messages.** One more is refused: HTTP
  429, code `queue_full`, "This project already has 20 messages waiting. Send more when some have
  run.", `details: {waiting, cap}`.
- Who queued a message is kept with it (`owner: true` only when the studio was sure it was the
  owner); a message whose sender is not known runs as a guest's.
- A studio that is not shared works as before: everyone there is its owner, and the limit applies
  to all.
- `GET /api/health` has `turns: {running, limit, waiting}`: `running` is every request running now,
  `limit` is `0` for no limit, and `waiting` is how many of them have waited for the person longer
  than 5 minutes and take no room.

# Files too large for versions

A project's versions are git commits of its folder. A 2 GB data file and its Parquet copy went into
that history like any page, and again with every change, so the disk filled and saving got slow.

- **The limit.** A file larger than `STUDIO2_VERSION_MAX_FILE_MB` (default 100; `0` means no limit)
  is left out of every version the studio saves. Checked by its size on disk at each save. Only
  files the version would otherwise keep count: files the project's `.gitignore` leaves out were
  never in versions. A symbolic link is never left out (git keeps the link, not what it points to).
- **Where.** At the save itself (`workspace.checkpoint`), not in the project's `.gitignore`, which
  stays the person's and the agent's. A left out file is never read into git: it is excluded
  before `git add`, so its content is not written to the history's objects either.
- **A file that grows past the limit** leaves the tree from the next version on (the version lists
  it as `deleted` in `files`, and in `left_out`). It stays on disk untouched. **A file that shrinks
  back** under the limit is in the next version again, like a new file.
- **Restore never touches a left out file.** Before it restores, the studio saves the current work
  as usual (left out files stay out), then builds the old version's tree without every file that
  is left out now (and without anything at or under its path, or a file where its folder is), and
  puts that on disk. So a left out file is not deleted, not overwritten by an older copy and not
  reverted, even when the old version still holds it. The restore version carries `left_out` too.
- **History is not rewritten.** A version saved before the limit, or with a larger one, keeps its
  copy of the file; restoring it brings that copy back only when no file is on disk at that path
  now. A project that already has a large file in its history just stops adding new copies.
- **What stays as it was.** Copying a project from a template or an example, saving a template, and
  export read the folder on disk, not its versions, so they carry a left out file as before (a
  template keeps its own 40 MB limit). **An attached app** (safe copy or edit in place) is not
  limited: a safe copy's commits go back into the person's own repository when it ships, and a
  tracked file left out there would be deleted from it. Its versions are saved as before.
- **The person is told** in the `checkpoint` event: `left_out: [{path, size}]` (at most 50, largest
  first; `size` in bytes) and `left_out_count`, present only when files were left out. The chat's
  marker adds "1 large file not in versions" (or "N large files"), with the names and sizes in its
  tooltip. Nothing is shown when there are none.
- **The agent is told,** in the facts of each request and only when such files exist, one line:
  "These files are larger than N MB, so they are not saved in versions and undo cannot bring them
  back: <paths, at most 10, then "and N more">. Do not commit them with git yourself."
