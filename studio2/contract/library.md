# Contract: Library

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

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

**Dialogs.** A page may open a JavaScript dialog (`alert`, `confirm`, `prompt`, or `beforeunload`
when it is left). `dialogs` (`"dismiss"` by default, or `"accept"`) says how each one is answered
during this look: dismissed is Cancel, accepted is OK, and an accepted `prompt` gets its own
default text. So a button behind `confirm("Remove?")` can be tried with `dialogs: "accept"`. The
answer lists every dialog that appeared, in order (the first 30):
`dialogs: [{type, message, answer, text?, error?}]`, the message cut at 300 characters, `answer` is
`accepted` or `dismissed` (or `unanswered`, with `error`, when the page went away first), and `text`
is what an accepted `prompt` was given. Any other value of `dialogs` is refused. The list is empty when
none appeared. The GET and the studio's own looks take the default; the GET's answer lists them too.
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
| `look_at_page` | `path?` (`/`, `/index.html` or `index.html`), `width?`, `dark?`, `full_page?`, `wait_ms?`, `actions?`, `dialogs?` (`dismiss` or `accept`) | Same as `POST …/look`; returns the JSON (the screenshot is a file the agent then reads) |
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
holds and which were picked for this request; the system prompt says once that the pick can be
incomplete and that the agent searches the library once when the task needs a format, a method or
a service no loaded skill covers.

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
end with `save_skill`. A skill made here is instructions, reference files and, where a step is
better done by a program than described (a calculation, a conversion, a check), scripts: see "The
tool `save_skill`" for what the person is shown.

A skill made from work in a project keeps that work's look unless the person says the look does
not matter: the body gets a part `How it looks` (the layout at desktop width and how it folds, the
palette with its colour values, the type, how cards, tables and charts are drawn) and the page
itself is saved as `reference/example.html`, to copy the structure and styles from and never the
content. The agent tells the person that the example still holds the project's content and offers
a copy with the content taken out. (The first version carried instructions only: a real run then
gave the right parts and checks in a rearranged layout, so the look was added on 18 November.)

## The tool `save_skill`

```
{name, description, body, files?: [{path, as?, to?}], remove?: [path], replace?: boolean}
```

- `name`: lower-case letters, digits, `-` and `_`, at most 60 characters, not the name of one of
  the studio's own skills. `description`: when to use it, at most 600 characters. `body`: the
  instructions in Markdown, at most 12,000 characters, without a front-matter block (the studio
  writes it).
- `files`: files copied from the project into the skill: at most 12, each at most 5 MB, 10 MB
  together. Two kinds are taken and anything else is refused by name; the file's own name and the
  name it is saved under are both checked, so one kind cannot be saved under the other's ending.
  - **Material to read or follow**: `.md .txt .html .css .json .csv .tsv .svg .png .jpg .jpeg
    .webp .gif .pdf .docx .pptx .xlsx`. Saved at `reference/<as or the file's name>`.
  - **Scripts** (since 19 November 2026; until then the tool refused them): `.py .js .mjs .cjs .ts
    .sh .bash .zsh .rb .pl .php .ps1 .bat .cmd`. Saved at `scripts/<as or the file's name>`. A
    script must be text (UTF-8, no zero byte), and the scripts of one call may hold at most 40,000
    characters together, because the card shows every one of them whole; more is refused with the
    advice to save in two calls.
  - `as` is at most 80 characters and no part of it starts with a dot. `to` is given instead of
    `as` to name the whole path inside the skill (`to: "ooxml/scripts/pack.py"`), which is how a
    file of a copied skill is replaced where it is: at most 120 characters, `/` between parts, no
    part that is `..` or starts with a dot, not `SKILL.md` or `studio-skill.json`, and not the
    path of a folder the skill already has.
- `remove`: paths of files the skill has now, to take out of it. Only with `replace: true` on a
  skill that exists; a path the skill does not have is refused by name, as is `SKILL.md`, and a
  path that is also written by `files`. At most 50.
- **It always asks**, whatever the project's approval setting: one approval card (`tool:
  "save_skill"`) titled `Save the skill “<name>” to your library` (or `Replace your skill
  “<name>”`), with the reason "A skill in your library is given to the agent in every project from
  now on." and, when the skill will hold a script after this save, " It holds scripts the agent
  may run through its shell." With no request running there is nobody to ask and nothing is
  saved.
- **What the card carries.** The `approval_request` event has, for this tool only, a field
  `skill`:

  ```
  {description, body,
   files:   [{path, size, script, replaces}],    // every file this call writes; size in bytes
   scripts: [{path, text, lines, replaces}],     // each script this call writes, whole
   removed: [path], kept: [path], kept_scripts: [path]}
  ```

  `replaces` is true when the skill already has a file at that path. `kept` are the files of the
  version being replaced that stay as they are; `kept_scripts` those of them that are scripts.
  The event's `detail` says the same in plain text, in this order: `When it is used: …`,
  `Instructions:` and the body, `Files:` with one `path (size)` a line, `Files removed:` with one
  path a line, `Files kept from the version it replaces: …` (the first twelve, then a count), and
  for each script a line `Script <path> (new | replaces the one in the skill, <n> lines):`
  followed by its text. For this tool the detail is kept whole instead of being cut at 4,000
  characters (up to 14,000, and up to 60,000 when it shows scripts).
- What is read for the card is what is saved: the files are read from the project when the tool
  is called, and a change to the project's copy while the card waits changes nothing.
- A skill of the person's with that name is replaced only with `replace: true`; without it the
  tool refuses and says so. The version it replaces is kept in `data/skills/.history/<name>-<UTC
  time>/` (the last five per name). Replacing changes the description and the instructions and
  keeps the skill's other files: `files` adds to them or replaces one at the same path, `remove`
  takes one out, and the card lists what is kept. A replaced file keeps its executable mark. A
  file is never written through a link: a link at the path is replaced by the file, and a path
  whose folder is a link is refused.
- On Allow it writes `data/skills/<name>/SKILL.md` and the files, rebuilds the index and answers
  `{saved, id: "yours:<name>", replaced, files: [path], scripts: [path], removed: [path], next}`.
  On Deny nothing is written and the answer tells the agent not to ask again for the same text in
  this request.
- **What a script in a skill can do.** The agent runs it through its shell in whatever project
  the skill is used, as the person, under that project's approval rules (which read the command's
  text and are not a sandbox). That is the same as for a skill imported with its scripts; the
  difference is who wrote it, which is why the card shows the text and always asks. The form
  (`POST /api/skills`, `PUT /api/skills/{name}`) still writes instructions only.

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
- Every skill in the Library has **Export** (downloads the file) and **Open** (see "Reading any
  skill, explaining it, and starting from one"). The person's own skills also have **Edit** for the
  description and instructions, in the opened skill, and **Delete**.
- **A project's menu** has **Make a skill from this…** beside Save as template: the whole page or
  app, or the selected part (offered when something is selected), and the optional note.
- **The approval card** for `save_skill` shows the name, when it is used, the whole instructions
  (scrolling inside the card) and the files, with Allow and Deny. The instructions are shown as
  the text they are, not as formatted Markdown, so that formatting can hide nothing from the
  person approving. The card's `detail` is plain text in this shape, which the card reads:
  `When it is used: …`, `Instructions:` and the body, `Files:` with one `path (size)` a line,
  and `Files kept from the version it replaces: …`, each part after a blank line.

# Reading any skill, explaining it, and starting from one

The library lists every skill it found (the studio's own, the person's, this machine's and the
plugins'), but until this section only the person's own could be opened, and only their
instructions. A skill is given to the agent as it is, so the person can read all of it first.

## Reading: `GET /api/library/skills/detail?id=<skill id>`

Any skill in the index. 404 `unknown_skill` otherwise. Nothing is run and nothing is written.

```
{…the library's skill object (id, name, label, description, source, origin, enabled, …),
 body, body_cut,            // the instructions without their front matter; at most 200,000
                            // characters, body_cut true when there were more
 front: {key: text},        // what else the front matter says (allowed-tools, license, …), as written
 files: [{path, size, kind, script}],     // every file, SKILL.md first, then by path
 scripts: [path], size,     // the files a program could run; the folder's size in bytes
 yours,                     // true for a skill the person made or copied: it can be changed
 picked, used, last_used}   // how often it was picked for a request and then really used
```

- `kind` is how the file can be shown: `picture` by its ending (`.png .jpg .jpeg .gif .webp`),
  `text` for any other file whose start reads as text (valid UTF-8 with no zero byte: Markdown,
  a page, a script, a licence without an ending), `other` for the rest (a PDF, an Office file,
  a font). `script` is the rule of "Skills a person makes": a script type, or a file marked
  executable.
- The files are the ones an export would hold: `.git`, `node_modules`, `__pycache__`, `.venv`,
  `.history`, `.env` and `.env.*` are never listed and never served.
- `picked`, `used`, `last_used` come from the library's own count (step 3o); 0, 0 and null for a
  skill that was never picked.
- It works on a shared studio, as export does. The folder's place on the machine is not in the
  answer.

## One file: `GET /api/library/skills/file?id=<skill id>&path=<path>`

`path` must be one of `files`: anything else is 404 `unknown_file`, so a path cannot leave the
skill. For `text`: `{path, size, kind, script, text, cut}`, the first 200,000 characters, `cut`
true when there are more. A page is shown as its source, never drawn. For `picture`: the same
without `text`; `raw=1` answers with the picture itself (`Cache-Control: no-store`). For `other`:
415 `not_shown` ("export the skill to open this file"). `raw=1` on a file that is not a picture
is 415 too: a skill's page or script is never handed to the browser as something to run.

## A copy of one's own: `POST /api/skills/copy`

```
{from: <skill id>, name, allow_scripts?: boolean, replace?: boolean}  →  201, the new skill
```

Any skill in the index becomes one of the person's own under `name`, with every file an export
would hold. It is an export followed by an import under that name, with the import's rules and
answers: 409 `name_taken_by_studio`, `name_taken` (unless `replace`), `scripts_need_confirmation`
(unless `allow_scripts`; the preview with the scripts' names is in `error.details`), 413
`too_large`, 403 `not_for_shared`, 404 `unknown_skill`. The skill it was copied from is left as
it was and stays in the library.

## Asking the agent about a skill: `POST /api/library/skills/ask`

```
{id: <skill id>, about: "explain" | "change", text?}  →  202 {project_id, turn_id}
```

The studio makes an ordinary project and sends one request in it, which can be stopped, queued
and continued in the chat like any other. The request names the skill's folder on this machine,
which the agent may read and the interface never sees.

- `explain`: the project is named `About the skill: <name>` and the request is
  `Explain the skill “<name>” from my library (use the make-a-skill skill, "Explaining a skill").
  Its folder is <folder>. Read SKILL.md and every other file in it before you write.` followed,
  when `text` is given (at most 2,000 characters), by a blank line and `What I want to know:
  <text>`. The agent writes the explanation on the project's page and saves nothing.
- `change`: only for one of the person's own (`yours`); 409 `not_yours` for any other, with a
  message to make a copy first. `text` is required and at most 2,000 characters (400
  `invalid_request` otherwise). The project
  is named `Skill: <name>` and the request is `Change my skill “<name>” (use the make-a-skill
  skill, "Changing a skill"). Its folder is <folder>.`, a blank line, then `text`. The agent
  saves through `save_skill` with `replace: true`, which asks on its card as always.
- 404 `unknown_skill`; 403 `not_for_shared` for `change` on a shared studio. `explain` is allowed
  there.

The skill `make-a-skill` gains the two parts the requests name. **Explaining a skill:** read
every file; say in plain words what it is for and what kind of request starts it; what it has
the agent do, step by step; what each file is and when it is read; for each script what it does,
what it needs (programs, keys, the network) and what it changes on the machine; what the skill
never says and a reader would want to know; and what could be better. Quote the line a claim
rests on. Run nothing. **Changing a skill:** read the whole skill first; change only what was
asked; keep its files; show what will change before saving. A file of the skill is changed by
writing the new version in the project and saving it with `to` set to its path in the skill, and
taken out with `remove`; a script is run on a real example in the project before it is saved.
(Until 19 November 2026 the tool wrote no scripts and removed no file.)

## UI

- **Every skill in the Library has Open**, in the list of the studio's and the person's skills and
  in the index of all of them. The dialog shows when it is used, where it comes from, how many
  files and scripts it holds and how often it was picked and used; a list of its files with the
  instructions first and scripts marked; and the chosen file as text (a picture as a picture; a
  file of another kind names itself and points at Export).
- Its actions: **Explain this skill** (opens the new project), **Export**, and for one that is not
  the person's **Make my own copy**: a name (the skill's own name with `-mine`, changeable), and
  when it holds scripts their names with the same box to tick as an import. For the person's own:
  **Edit** (as before), **Ask for a change** (a text box; opens the project) and **Delete**.
- **The New page dialog** gains a third choice beside a template and a folder: **A skill**, which
  opens Library → New skill at "Describe it".


# A connector from a description

Until this section a connector (an MCP server) was added by someone who already knew its address
or its command. A person who knows only what they want ("read my Notion pages", "search our
Sentry errors") now says that, and a model suggests which connector to add. The suggestion fills
in the form; the person reads it, types the keys and presses Add. Nothing is added, started or
connected by the suggestion itself.

## `POST /api/mcp-servers/suggest`

```
{text, connection?}  →  200
{have:        [{id, name, label, source, origin, description, state, fix}],
 suggestions: [{name, label, description, transport, url, command, args,
                secrets: [{name, where}], why, sign_in, check}],
 left_out:    [{label, reason}],
 asked:       {connection, model} | null,
 note:        text | null}
```

- `text`: what the person wants to reach or do, 1 to 2,000 characters (400 `invalid_request`).
  `connection`: the model account to ask; the studio's default when left out (400
  `unknown_connection`).
- `have`: up to five connectors already in the library that the description's words match (the
  library's own word search over names, labels and descriptions; every source, the studio's
  shipped list included). These are real: each was found on this computer or shipped with the
  studio. A connector the model suggests that the library already has (the same address, or the
  same package) is listed here and not under `suggestions`.
- `suggestions`: at most three, from one question to the connection's small model (the one that
  picks skills; `asked` names it). The model answers from what it remembers; it has no tools and
  does not search the web. So a suggestion is a claim, and the studio checks what it can before
  showing it:
  - **Only two shapes are accepted.** A web address beginning `https://`, or a package started by
    its runner: `npx -y <package> …` (npm) or `uvx <package> …` (PyPI; also `uvx --from <package>
    <program> …`). Any other command is left out: a program named from memory cannot be checked.
    `-y` is added to an `npx` suggestion that lacks it, since `npx` would otherwise wait for a
    key press nobody can give.
  - **A package is looked up in its registry** (`registry.npmjs.org`, `pypi.org`; nothing is
    downloaded or run). `check` is `{kind: "npm" | "pypi", package, found, version, description,
    published, first_published, downloads_week, repository}`: `published` is when that version
    was published, `first_published` when the package's first version was, `downloads_week`
    npm's own count for the last seven days (null for PyPI, which gives none). Any of them is
    null when the registry did not say.
    A package the registry does not have is left out (`left_out`, with the reason). When the
    registry cannot be reached, `found` is null and the suggestion stays, said to be unchecked.
  - **An address is asked once, with no key**: one `POST` of an MCP `initialize` to it, no
    redirect followed, six seconds at most, and only when the host is a public one (every address
    it resolves to; a loopback, private or link-local address is never asked). `check` is `{kind:
    "address", found, status}`: `found` is false for a name that does not exist, no answer at all, 404, 410 or a server error,
    and such a suggestion is left out; true for any other answer (401 and 403 mean it is there
    and wants a key or a sign-in); null when it was not asked.
  - An address or a command that holds a key (`?api_key=`, `--token=`) is left out: a key goes
    in `secrets`, where it is stored in the keychain and never shown again.
  - A package being in the registry shows that the name exists, not that it is safe or the one
    the service's maker publishes: a name a model made up can be registered by someone else. The
    interface shows the version, its date and the weekly downloads so the person can judge, and
    says so in words when a package has fewer than 1,000 downloads a week or its first version
    is under 30 days old.
- `secrets`: the names of the headers (a web address) or environment values (a package) the
  connector needs, each with `where`: one sentence on where the person gets the value (at most 300
  characters; a longer one is cut at a word and ends with …). Never a value. At most six; a name is letters, digits, `-` and `_`.
- `sign_in`: the model says this connector signs in through a browser (OAuth) and takes no key.
  A connector added in the studio cannot do that. The interface then shows the command that adds
  it to the agent program instead (`claude mcp add --scope user --transport http <name> <url>`,
  then `claude mcp login <name>`), after which it is in the library as one of this computer's.
- `name` is made free: when the library already has a connector of that name, `-2`, `-3` … is
  added.
- `asked` is null and `suggestions` empty, with `note` saying why in a sentence, when no model
  was asked: the connection has no small model, the studio runs its scripted stand-in
  (`STUDIO2_FAKE=1`), or the model did not answer within 40 seconds or answered with something
  that could not be read. `have` is given all the same.
- The question goes to the provider of the connection named, like a request's text. It is not
  part of any request, so what it used is on no page's total: one call to the small model, a
  fraction of a cent where it is charged at all.
- A shared studio answers as any other: whoever may add a connector there may ask for a
  suggestion.

## UI

- **Library, MCP servers, Add.** The dialog opens on **Describe what you need**: a text box with
  an example, **Suggest**, and a link to fill in the form oneself. The answer shows *Already in
  your library* (name, where it is from, what it does) and *Suggested by <model>, from memory*:
  for each one what it is, why it fits, its address or command, the line the check gave (found
  on npm with version, date and downloads; the address answered; could not be checked), the keys
  it needs and where each comes from, and **Use this**. What was left out is said in one line.
- **Use this** fills the form: name, label, address or command and arguments, description, and
  one empty row per key with its `where` under it. The form says above it that the suggestion
  came from a model's memory and what was checked. The person types the values and presses Add;
  Test works as before, on the saved connector.
- **New page** has a fourth choice, **A connector**, which says what a connector is and opens
  the Library's Add dialog at *Describe what you need* (`/library#new-connector`). It is not
  offered on a shared studio, like *A skill*.

