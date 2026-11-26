# Contract: Apps

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

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
| `approval_request` | `approval_id`, `tool`, `title`, `detail`, `reason`, `skill?`, `guarded?`, `owner_only?` | The agent wants to do something risky and is waiting. `title` is a short line ("Run a shell command"), `detail` the command or path, `reason` why it needs approval |
| `approval_resolved` | `approval_id`, `decision`, `by` | `decision`: `allow`, `deny`. `by`: `user`, `turn_end` (the turn ended first: always `deny`) |
| `question_request` | `question_id`, `kind`, `title`, `summary`, `references`, `questions`, `why` | The agent asks the person something, shows its plan or checks in on a part, and is waiting ("How much to involve the person") |
| `question_resolved` | `question_id`, `by`, `skipped`, `answers` | The person answered or skipped, or the turn ended first |
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

`TurnRequest` gains `attachments: list[str]`, `context: str` (system-prompt text after
`library/system-prompt.md`: what this studio's configuration allows, the same for every request;
the project facts — project URL, app state, log file, how to restart the app — go in the message
as a `<studio-project>` block; see "What the agent is told once, and what with each message"),
`fresh_prompt: str | None` (the message with those facts in full, sent instead when a resume fails
and a new conversation starts) and
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

## Bridge addition: files dragged onto the page (withdrawn 24 November 2026)

The bridge used to send `file-drag` when a drag carrying files entered the page, and the shell
covered the whole screen with a drop target. Both are gone: see "Files are dropped on the chat,
nowhere else" at the end of this file.

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

