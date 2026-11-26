# Contract: Access and safety

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

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

# The owner of a shared studio

A shared studio is one other people are let into. Some things there belong to the person who
runs it and not to a guest: what reaches every project (skills in the library, instructions for
every project). Until now those were refused to everyone who came through the door, the owner
included, because the studio could not tell them apart.

- **Who the owner is.** `owners` in the config file, or `STUDIO2_OWNERS` (a comma-separated
  list), holds the owner's email addresses, compared without regard to letter case. A request
  is the owner's when the gate in front signed it in ("Access from other devices") and the
  email in that signed token is on the list. Nothing else makes an owner: not the address a
  request comes from (through a tunnel every request arrives from this machine itself, a
  guest's too), not the studio's own access key, not a request made on the machine without the
  gate. Without a gate, or with an empty list, a shared studio has no owner through its door.
- A studio that is not shared has one person, and everything is theirs, as before.
- **`GET /api/health` gains `owner`** (boolean): whether this request is the owner's. Always
  true on a studio that is not shared.
- **What the owner may do on a shared studio that a guest may not:** read and write the
  instructions for every project; add, import, copy, change and delete skills in the library,
  from the Library page and through `save_skill` in a request the owner started (a request
  keeps who started it, also while it waits in the queue); **and run requests on the Claude
  plan** (below). Everything else stays as it is for everyone on a shared studio: the agent
  never sees an app signed in, setup and model installs belong to the person at the machine,
  the library's places are set in the config file.
- **The Claude plan on a shared studio is the owner's alone.** A plan may not be offered to the
  people a tool is shared with, so on a shared studio it stays off for every request that is
  not the owner's: a guest's, one made on the machine without the gate, any request when no
  owner is named. For the owner's own requests it is a connection like any other.
  - `GET /api/health` lists connections as this request may use them: to a guest the plan is
    there, not available, with the reason "The Claude plan is this studio's owner's. Use
    another model account."
  - A project's remembered connection and model are the project's, shared by everyone in it.
    When a request cannot use the remembered or asked-for connection (a guest in a project the
    owner left on the plan), it runs on the first connection that requester may use, with that
    connection's default model, for that request only: the project's remembered choice is not
    changed, and a `notice` (`info`) on the request says which model it ran on and why. A
    studio with no other connection fails the request with the reason instead.
  - A request keeps who started it, so a guest's message queued behind the owner's request
    runs as a guest's.
  - A project the owner creates on a shared studio starts on the plan when the plan is listed
    (theirs to use, and not a charge for each request); anyone else's starts on the first
    connection listed, as on any studio.
- **UI.** Where the interface hid these because the studio is shared, it now hides them only
  from a guest (`shared` and not `owner`).

# A page with its own address signs in by itself

Behind a gate, a project's page can have an address of its own (`STUDIO2_PAGE_HOSTS`), and the
gate keeps a separate sign-in for each address. A sign-in page does not show inside a frame, so
the studio's frame stayed empty until the person opened the page once in its own tab. The
studio now does that trip itself.

- **Every page port answers two paths before anything else**, whatever serves the project:
  - `GET /__studio/here.gif`: a one-pixel picture, never kept. It reaches the browser only when
    the browser may reach this address, so it tells the studio's own page whether the gate has
    signed the person in here.
  - `GET /__studio/back?to=<address>`: 302 to `to`, when `to` is an http(s) address whose name
    is one this studio answers to ("Names the studio answers to"). Anything else → 400. It is
    how a trip to this address returns to the studio.
- **The interface.** For a page on another host than the studio's, it asks for the picture. If
  it does not arrive, and this is within a few seconds of opening the project, and no such trip
  was made from this tab in the last two minutes, the whole tab goes to
  `<page address>/__studio/back?to=<where it was>`: the gate signs the person in at that address
  (without asking again when they are signed in at the gate already) and the studio comes back
  with the page in its frame. Otherwise the notice is shown as before, and its button makes the
  same trip instead of opening a second tab.
- Not through a gate (a port on the same host), nothing here applies.

# What an agent's shell may not reach

Two studios can run on one computer as the same user (the owner's own, and a shared copy). The
agent's shell runs as that user too, and the studio trusts requests that come from this computer
itself, so without these rules an agent could read the computer's keys, call a studio's own API on
loopback (answer its own approval card, set a project to allow everything, read another project), or
read another project's files. The threat is an agent that is misled by something it read (a web
page, a file) or simply mistaken, in a household where every person is trusted; not a skilled
attacker. **These rules read the command's text. A determined program can get past them** (a script
file it wrote first, a path it builds from pieces, a name decoded at run time). Real isolation is a
separate user or a container per project (Step 4), or the operating system's sandbox described at
the end of this section.

## What is caught

A shell command (also inside `bash -c`, `eval`, a pipe into a shell, `python -c`, `node -e`, a
here-document handed to another language, and after a `cd` on the same line) is caught when it:

1. **reads, lists, copies, archives or uploads from a place that holds keys or sign-ins:**
   `~/.secrets`, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.netrc`, `~/.git-credentials`,
   `~/.npmrc`, `~/.pypirc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`,
   `~/.claude.json`, `~/.claude/` and `~/.codex/` (except their `skills/`, `plugins/`, `agents/` and
   `commands/` folders, and the agent program's own folder for this project's sessions under
   `~/.claude/projects/`, where it keeps long tool output for the agent to read back),
   `~/Library/Keychains`, `~/Library/Cookies`, and the profile folders of Chrome, Chromium, Brave,
   Edge, Arc, Opera, Vivaldi, Firefox and Safari. `STUDIO2_AGENT_PRIVATE` adds more places (paths
   joined by `:`). Any path counts, however it is written: `~`, `$HOME`, `${HOME}`, the full path, a
   relative path that climbs out with `..`, a pattern (`~/.ss*`), a path after `=` or `@`
   (`--file=…`, `-F f=@…`), a path inside code (`open('/Users/…/.ssh/id_rsa')`, `open('../../<other id>/events.jsonl')`; another
   person's home folder counts like one's own), and code that builds
   one from the home folder and one of these names (`Path.home() / '.ssh'`). A command that reads a
   whole folder (`tar`, `zip`, `rsync`, `scp`, `ditto`, `find`, `rg`, `ag`, `ack`, `fd`, `cp` with
   `-r`/`-R`/`-a`, `grep` with `-r`/`-R`) is also caught when the folder holds one of these places (`tar czf
   home.tgz ~`), and so is serving such a folder (`python3 -m http.server -d DIR`, `--directory`, the
   current folder without one; `serve DIR`, also through `npx`; `php -S … -t DIR`). A word written
   `[user@]host:path` is a path on another machine (`scp x me@vps:~`), not read as one here. Listing names only (`ls ~`, `ls -la ~`, `du`, `tree`) is not. The `security` command's subcommands that read or change passwords, keys and
   keychains (`find-generic-password`, `find-internet-password`, `dump-keychain`, `export`,
   `unlock-keychain`, …) are caught.
2. **talks to a studio or to another project on this computer:** any address on `localhost` (and
   `*.localhost`), `127.x.x.x`, `0.0.0.0`, `::1` or this computer's own names and the addresses a
   studio listens on, whose port is a studio's own port, one of its project page ports or one of its
   back-end ports, in a URL (`http://127.0.0.1:<port>/…`), as `host:port` (always with the `:`; the
   host `0` only after a scheme, `http://0:<port>`, so `s[0:8400]` is not one), in
   `/dev/tcp/<host>/<port>`, or as a quoted host followed by a port in code (`('127.0.0.1', <port>)`). **The project's
   own page port and its own back end (page port + 100) are not caught**: agents test their pages and
   apps with `curl`. Other ports (a dev server the agent starts on 5173, a database) are not caught.
   The ports come from each studio's settings (`port`, `port_range`, back ends at +100), not from fixed
   numbers: every studio records its port, port range, data folder, config folder and listening
   addresses in a file of its own under `~/.local/state/studio2/studios/` (`STUDIO2_STUDIOS_DIR` moves
   it) when it starts, so each knows the others. A studio that has not started since this rule
   existed is not known to the others until it does.
   **The project's own routes on its studio stay callable**, because the studio itself tells the
   agent to use them: `/api/projects/<its own id>/app` and below (the app's state, restart, how it
   runs, its output), `/data/…` (the databases attached to it) and `/model/…` (its model route).
   Everything else on a studio's port is caught, in its own project too: the project's settings, its
   approval cards, attaching a database, another project, the other studio.
3. **reads or writes inside a studio's data folder or config folder outside the project's own
   folder**, for any studio recorded there: another project's workspace, `events.jsonl`,
   `project.json`, anything under another project id, the studio's run folder (connector settings),
   and the records under `~/.local/state/studio2/studios/`. The project's own folder is its workspace
   and `projects/<its id>/` (its library, its events). A write anywhere in the data folder outside the
   workspace (its own `project.json` included) is caught too. A studio's own `skills/` and
   `templates/` folders in its data folder are open to every project. A command that only searches a
   folder that holds a data folder (`find ~/dev …`, `rg x ~/dev`, `Grep` over `~/dev`) is not caught;
   one that copies all of it out (`tar`, `zip`, `cp -r`, `rsync`, `ditto`, `cpio`, `pax`, `rclone`,
   `scp`) is. A studio's config file is recorded as a file; its folder only when that folder is not the
   home folder or one above it. A record with a field of the wrong type is skipped, and the record of a
   studio that stopped (no process, nothing answering on its port) is removed.

Writing, moving or deleting in the studio's own settings (Approvals: `~/.config/studio2/`,
`~/.claude/`, the data folder, the studio's command line when it changes something) is now caught
the same way.

## What "caught" means

- In a studio that is not shared, and for the owner of a shared one: the approval card, **also in a
  project whose approvals are set to allow everything** (`always`, as for spending money). The reason
  names the place or the address. If the person does not allow it, the agent is told not to reach the
  same thing another way.
- A line wrapped in commands more deeply than the rules read counts as one of these (it asks, also
  with "allow everything").
- The card carries `guarded: true` (`approval_request`). On a shared studio only the owner may answer
  such a card with "allow": anyone else gets 403 `owner_only`, "Only this studio's owner can allow
  this." ("deny" is open to everyone).
- In a shared studio, for a request that is not the owner's: refused, with no card. The agent is told
  `<reason> This studio is shared, so only its owner can allow this. Do not try another way to reach
  it; tell the person what you needed it for.`

## The agent's own tools

- `Read`, `Glob`, `Grep` and `NotebookRead`: a `file_path` or `path` (and a `pattern` that starts with
  `/` or `~`) into the places in 1 or 3 is refused before it runs, and so is a `Glob` or `Grep` over a
  folder that holds one. Same hook and same kind of refusal as a write outside the project; the
  reason is the one above. `Write`, `Edit`, `MultiEdit` and `NotebookEdit` already cannot leave the
  project folder.
- Any tool (other than `Bash`, which is judged as above) whose input has a `url`, `uri`, `href` or
  `endpoint` naming an address in 2 is refused before it runs: `WebFetch`, a browser connector's
  `navigate`, the studio's `look_at_page`.

## Not caught (honestly)

A program the agent wrote to a file and then runs (`python steal.py`); a path or address assembled
at run time (`cat $(echo fi5zc2g= | base64 -d)`, `chr()` sums, string joins without a home accessor);
a variable the line does not set (`cat "$D/id_rsa"`); a link out of the project that is not at its top
(`src/k` → `~/.ssh`; a link named at the top is followed, and making one names the place, so asks);
another address of this computer that is not one
a studio listens on or names, and other spellings of loopback (decimal or octal numbers such as
`017700000001`, names that resolve to it through wildcard DNS); connector (MCP) tools that take a file
path (only their `url`-like fields are read); a host and port written apart without a `:`
(`nc localhost 8200` asks as `nc` always does, but not as one of these); a model gateway's own port (the agent already holds its key); and an
agent that set out to hide what it does. These rules make an honest mistake, or an agent following
an instruction it should not, stop and ask; they do not contain an agent.

## The operating system's sandbox: tried, not switched on

The agent program's own sandbox for its shell (`ClaudeAgentOptions.sandbox`, macOS Seatbelt; SDK
0.2.180, agent program 2.1.321) was tried on this Mac with a stand-in model endpoint on loopback, which
is how gateway models are reached, so it applies to them as much as to the Claude plan. It confines
files well: reads of a denied folder (and of another project's `events.jsonl` in a denied data folder)
fail with "Operation not permitted" from `cat`, `bash -c`, Python and Node, also when the agent asks to
run outside the sandbox, while a narrower `allowRead` keeps the project's own folder readable; writes
outside the project and `$TMPDIR` fail. It is not switched on because it does not work cleanly here:
- **Loopback cannot be limited per port for ordinary connections.** Direct connections to any loopback
  port fail, the project's own included (`curl http://127.0.0.1:<own port>/` fails); only connections
  through the sandbox's proxy can be allowed per port. `allowLocalBinding`, which a back end started
  from the shell needs to listen at all, opens every loopback port again, the studios' included.
- `git init` and `git config` fail (writes to `.git/hooks` and `.git/config` are refused); `uv` needs
  a system service and its cache allowed; `npm` needs `~/.npm` allowed.
- `autoAllowBashIfSandboxed` must be false, or shell commands no longer reach the approval rules above.
- The `Read` tool is not covered by the sandbox; it needs its own deny rules in the settings.

So the rules above stay the guard, and the real fix stays a separate user or a container per project.

# What a guest may not do on a shared studio

On a shared studio everyone the gate lets in who is not on the owner list is a guest (see "The
owner of a shared studio"). On a studio that is not shared everyone is the owner and nothing here
applies.

**What these limits are, and what they are not.** They stop a guest from changing the studio for
everyone, and from using the owner's accounts, keys, data sources and folders, through the studio's
own controls: its routes, its cards and the agent's studio tools. They do not contain a guest who
sets out to get around them. The studio runs as the owner's own user on the owner's computer, and so
does everything it starts for a project: a project's app, and every script an agent writes and runs.
Such a program can read what the owner can read and do what the owner can do, and the rules on the
agent's shell read a command's text, not what a program does ("What an agent's shell may not
reach"). A guest who can get a program run (a request that writes a script and runs it, for
example) can get past every limit below. Real separation needs a separate user or a separate
machine for guests' work; the studio does not have that yet. Let in only people you trust with your
computer.

- **A guest may:** create projects, send requests, stop them, answer questions, deny any approval
  card, allow a card of their own request that is not guarded and asks for nothing that is the
  owner's, read, edit, upload, restore and export a project's files (key files left out, below),
  start and stop a project's own app when its run line is one the owner has started (below), draw
  on the page, use a template, choose a project's skills and connectors from the library, detach a
  database from a project, set a project's approvals back to asking, read the library and the
  examples, refresh the library's list, and ask the agent about a skill.
- **Only the owner may**, and a guest gets 403 `not_for_shared` with a sentence saying so, before
  anything is changed or run:
  - connectors: add, change, remove and test one (`POST`, `PATCH`, `DELETE /api/mcp-servers…`,
    `POST /api/mcp-servers/{id}/test`), and ask a model to suggest one (`POST /api/mcp-servers/suggest`).
    A connector can be a command, and its test starts it.
  - a folder of this computer: look at one or bring it in (`POST /api/apps/inspect`,
    `POST /api/projects/attach`), and change how an attached app runs (`PUT …/app/config`).
  - context from this computer: look at a path or address, and add it to a project
    (`POST /api/context/inspect`, `POST /api/projects/{id}/context`). Changing an item's label or
    note, and removing it, stay open.
  - templates: save a project as one, change, delete or import one (`POST …/template`,
    `PATCH` and `DELETE /api/templates/{id}`, `POST /api/templates/import`).
  - always and never for every project (`PUT /api/library/prefs`).
  - data sources: add, change, remove and test one, test one not saved, and attach one to a project
    (`POST`, `PATCH`, `DELETE /api/data-sources…`, `POST …/test`, `PUT /api/projects/{id}/data-sources/{source}`).
  - keys for other services: save or remove one (`PUT` and `DELETE /api/services/{name}`).
  - making an app live: set the recipe, start a ship, answer its card, update the copy
    (`PUT …/ship/recipe`, `POST …/ships`, `POST …/ships/{id}/decision`, `POST …/ship/update-copy`).
  - letting paid media generation start again after its daily stop (`DELETE /api/generation/pause`).
  - checking a model and stopping a check (`POST /api/models/check`, `POST /api/models/check/stop`):
    a check runs a request on one of the owner's model accounts.
- **Already so before:** skills in the library and the instructions for every project are the
  owner's (403 `not_for_shared`); "allow" on a guarded approval card is the owner's (403
  `owner_only`); setup, model lists with a key and installing LiteLLM answer only a plainly local
  request (403 `setup_is_local`); the places the library looks and the signed-in look are refused
  on a shared studio for everyone.
- **`GET` routes stay open**: what they show holds no key (keys, passwords and connector values are
  never shown, only that they are set).
- **The interface** hides or disables these for a guest (`shared` and not `owner` in
  `/api/health`) and says that the studio's owner does this.
- **New routes:** a test walks the server's route table and fails on a route that changes
  something (`POST`, `PUT`, `PATCH`, `DELETE`) and is neither owner-only nor on its list of what a
  guest may do.
- **The agent in a guest's request** holds the same line. A request keeps who started it (`turns.by_owner`);
  on a shared studio one that is not the owner's (or a tool called with no request running) gets a plain
  refusal from these studio tools, and nothing is saved or started:
  - `save_template`: "Only the studio's owner can save a template: it is offered to every project. …"
  - `propose_ship_recipe` and `make_live`: "Only the studio's owner can set how this app is made live …" /
    "… can make this app live …". Their cards would otherwise be the guest's to answer.
  - `save_skill` was already refused for a guest before its card is shown (`not_for_shared`).
  - The other tools stay as they are: `activate_skill` and `request_connector` change only this project's
    request; `query_database` and a connector with saved keys are open (left for the owner to decide);
    `generate_media` keeps its card and its daily cap.
  - A shell command that writes the studio's own skills or templates folders, `~/.claude/`, the studio's
    config, runs the `studio` command's changing subcommands or calls a studio's API is already a guarded
    reason ("What an agent's shell may not reach"), so a guest's request is refused it with no card.
- **Cards that need the owner.** On a shared studio "allow" on a card needs the owner when the card
  is guarded, when it belongs to a request the owner started (any card of the owner's own request),
  or when it asks for something only the owner may do (`save_skill`, `save_template`, a ship recipe,
  making an app live), whoever started the request. Anyone else gets 403 `owner_only`, "Only this
  studio's owner can allow this." "Deny" stays open to everyone.
  The server says which cards these are: on a shared studio such an `approval_request` carries
  `owner_only: true` (decided when the card is shown, by the same rule the answer route uses). A
  studio that is not shared never sends it. The interface shows a guest no "Allow" on such a card,
  only "Deny" and the line "Only the studio's owner can allow this."; the owner sees the card as
  before. An event from before this field has none: the interface then goes by `guarded: true`, and a
  refusal that still arrives is shown on the card as its sentence.
- **Allow everything.** Only the owner may set a project's approvals to `allow`
  (`PATCH /api/projects/{id}` with `approvals: "allow"`); a guest gets 403 `not_for_shared`. A guest
  may set them back to `ask`. In the interface a guest sees "Allow everything" in the approvals menu
  greyed out with "Owner only", ticked when the owner set it, and may still choose
  "Ask before risky commands".
- **Key files** (the names in "Key files": `.env`, `.env.*`, `.envrc`, `.dev.vars`, `.netrc`,
  `.pypirc`, SSH private keys, and anything under a folder so named) are never handed to a guest:
  `GET …/file` answers 403 `not_for_shared`; the project download and a version's diff leave them
  out; a project's page port does not serve them (404) to anyone but the owner. They stay listed by
  name in the Files list. This is not containment: a guest may still ask the agent to read such a
  file, and the agent can, as the owner's user. Who may see which project is the owner's decision
  and is not changed here.
- **A project's app runs only a run line the owner has started.** On a shared studio the project
  record keeps `run_seen`, the `run` line of `studio.json` when the owner last started the app (the
  owner's start, restart, or the end of a request the owner started). The studio does not start or
  restart the app while `studio.json` says anything else: a guest's `POST …/app/start` and
  `…/app/restart` answer 403 `run_not_seen`, and the start after a guest's request, after a file
  change and at the studio's own start leave the app stopped, with the reason on the app and a
  `notice`: "On a shared studio a project's app runs only with a run line the studio's owner has
  started. This one is new or changed, so it was not started. Ask the owner to start the app once."
  A guest may not write or upload `studio.json` through the file routes (403 `not_for_shared`). A
  project made from a template or an example takes that template's run line as seen, and so does a
  copy the owner brought in; a project that existed before this rule takes its current one, once.
  An app the owner set up (in place, from its record) is not checked: only the owner sets that. This
  stops a guest from getting a command started by the studio itself; it does not stop a script the
  agent runs in a guest's request (above).
- **The route table test** fails on any route kind it does not know (a plain Starlette route, a
  websocket, a mounted app), on a route that changes something and was not judged, on a `GET` that
  acts and was not judged, and on an owner-only mark that is not the studio's own check.

# Where a shell command is judged from

The agent program's shell keeps its folder from one call to the next: `cd data` in one call, and
the next call starts in `data/`. The rules in "What an agent's shell may not reach" and
"Approvals" used to read every command as if it started at the project's top, so `cd .. && mkdir
-p data/agg` after a `cd data` was read as leaving the project and asked as "the studio's own
settings" (a card for the owner, a refusal for a guest), though it wrote the project's own
`data/agg`.

- **What the studio is told.** The PreToolUse input of every tool call has `cwd`, the folder the
  agent's shell is in at that moment; it follows `cd` (tried on CLI 2.1.327: after `cd data` the next
  call reports `<project>/data`, and `cd ..` there brings the next one back to the top). A helper agent's
  shell is another shell and its calls carry their own folder.
- **How it is used.** A hook kept for Bash (`matcher: "Bash"`) notes `cwd` under the call's
  `tool_use_id`; the permission callback, which gets the same id, judges `cd <that folder> && <command>`
  (`risk.judged_from`), so every rule reads relative paths from where the shell really is. The card and
  the refusal show the command as the agent wrote it. Nothing outlives the request.
- **Never weaker.** A command that leaves the project from that folder is caught as before (`cd ../..`
  from `data/` is the studio's records). A folder outside the project is read as itself, never as the
  project: from another folder `touch notes.txt` writes outside the project and is caught, where read
  from the top it had looked like a project file; a scratch folder such as `/tmp` is scratch, as when a
  line says `cd /tmp` itself. With no folder reported (a call the hook said nothing about) the command
  is read from the top, as before.
- **From a scratch folder** (`/tmp`, `/private/tmp`, `/var/folders`) the rules keep the folder itself,
  as written. A relative path that stays inside the scratch folders is scratch, as before; one that
  climbs out is judged as the place it reaches, read both ways (`/tmp/..` is `/` to the shell's `cd`
  and `/private` to a file read), on one line or across calls: `cd /tmp && cat ../Users/<user>/.ssh/id_rsa`
  is a private place (until 25 November 2026 it was read as scratch and let through). A read that is
  merely outside (`cd /tmp && cat ../etc/hosts`, `cd /private/tmp && ls ../../etc`) is not asked, like any
  read outside the project.

# Text a command only rewrites or prints

The rule for paths inside text (code given to a program on the command line, such as
`python3 -c "open('../../x')"`) read every `../` in a line from the project's top. So a page being
rewritten for a folder (`sed 's#href="styles/#href="../../styles/#' a.html > build/b.html`, which the
long-document method makes agents write) was read as reaching another project, a guarded card for
the owner and a refusal for a guest.

- **A relative climb is read from where its part of the line runs**, after an earlier `cd` on the line
  or in an earlier call ("Where a shell command is judged from"), like every other word, and named as
  the place it really reaches. A folder that cannot be known: from the project's top, as before.
- **Arguments that are only text are not read for relative paths:**
  - a `sed` script (the first word that is not an option, or every `-e`) made of nothing but
    `s<d>pattern<d>replacement<d>flags` commands, joined by `;` or new lines, with flags among
    `g p i I m` and digits. A script with anything else (an address, `r`, `R`, `w`, `W`, `e`, a `w` or
    `e` flag, a block, `y`) and a script from a file (`-f`) are read as before;
  - a `perl` `-e` (`-pe`, `-ne`, …) script of the same kind, without the `e` flag (which runs the
    replacement as code);
  - what `echo` or `printf` prints, up to the first redirection, when the output is not piped on
    (into `xargs` or a shell the text can become a path or a command).
- **Still read in those arguments:** an absolute or home path (`~/.ssh`, `/etc/…`, a studio's data
  folder), an address, the keychain. **Always read:** the file a redirection writes, the files the
  command is given, and every other program's text. `awk` is not covered (its `getline <` and
  `print >` open files from inside its text).

