"""Per-project preview servers: one loopback port each, serving the workspace with the bridge injected."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import mimetypes
import re
import socket
from pathlib import Path

import httpx
import uvicorn
import websockets
from uvicorn.server import ServerState


log = logging.getLogger("studio2")
BRIDGE_PATH = Path(__file__).parent / "static" / "bridge.js"
BRIDGE_TAG = b'<script src="/__studio/bridge.js"></script>'
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".csv": "text/csv", ".md": "text/markdown", ".svg": "image/svg+xml", ".wasm": "application/wasm",
         ".html": "text/html", ".htm": "text/html", ".txt": "text/plain", ".py": "text/plain",
         ".pdf": "application/pdf",
         ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
         ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".aiff": "audio/aiff",
         ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
         ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".hdr": "image/vnd.radiance",
         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
font:15px/1.5 system-ui,sans-serif;background:Canvas;color:GrayText}}p{{max-width:28rem;text-align:center}}</style>
</head><body><p>{message}</p></body></html>"""
EMPTY = PAGE.format(title="Empty project", message="This project has no index.html yet. "
                    "Ask the assistant to build something and the page will appear here.")
MISSING = PAGE.format(title="Not found", message="There is no file at this address in the project.")


STATUS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
font:15px/1.5 system-ui,sans-serif;background:Canvas;color:CanvasText}}main{{width:min(46rem,90vw)}}
h1{{font-size:1.1rem;margin:0 0 .35rem}}p{{color:GrayText;margin:0 0 1rem}}
pre{{font:12px/1.5 ui-monospace,Menlo,monospace;background:color-mix(in srgb,CanvasText 6%,Canvas);
border-radius:10px;padding:.9rem 1rem;max-height:50vh;overflow:auto;white-space:pre-wrap;word-break:break-word}}</style>
</head><body><main><h1>{title}</h1><p>{message}</p>{log}</main>
<script>setTimeout(function(){{location.reload()}},{reload});</script></body></html>"""
# What the app may name itself in an absolute address it builds from its Host, besides the address
# the studio sends it to.
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0")
# Not passed on from the browser and not added: the app is shown its own origin and host
# (_own_origin), and these would contradict them. Next's Server Actions compare Origin with
# X-Forwarded-Host before Host, and Rails compares Origin with an address that follows
# X-Forwarded-Proto, so either would make them refuse the page's own forms.
NOT_FORWARDED = ("x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "forwarded")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length", "content-encoding"}
INJECT_LIMIT = 8 * 1024 * 1024


def status_page(app) -> bytes:
    """Shown at the project URL while its app is not answering."""
    state = app.state if app else "none"
    if state == "starting" and getattr(app, "slow", False):
        state = "quiet" if getattr(app, "not_answering", False) else "slow"
    title, message, reload = {
        "starting": ("Starting the app…", "This page loads by itself as soon as the app answers.", 1000),
        "slow": ("Still starting the app…", "It is running but has not answered on its port yet. A first "
                 "start that downloads packages or compiles can take minutes. This page loads by itself as "
                 "soon as it answers. If the output below shows no install or compile going on, check "
                 "that the app listens on the port in $PORT.", 3000),
        "quiet": ("The app is running but not answering", "It has been running for minutes and has never "
                  "answered on its port. It has not crashed; most likely it listens on another port than the "
                  "one in $PORT, or does not serve at all. Ask the assistant to look at its output below. This "
                  "page loads by itself if it answers.", 5000),
        "stopped": ("The app is stopped", "Start it from the app menu in the studio.", 3000),
        "crashed": ("The app stopped by itself", f"Exit code {app.exit_code}. The last output is below; "
                    "ask the assistant to fix it, or restart the app from the studio.", 3000),
        "external": ("Nothing answers at " + str(getattr(app, "address", "") or "its address"),
                     "This project shows a server the studio did not start. Start it the way you "
                     "usually do; this page loads by itself once it answers.", 2000),
    }.get(state, ("This project's app is not set up correctly", (app.error if app else "") or "", 3000))
    tail = "\n".join(text for _n, text in list(app.lines)[-40:]) if app else ""
    log_html = f"<pre>{html.escape(tail)}</pre>" if tail else ""
    return STATUS_PAGE.format(title=html.escape(title), message=html.escape(message), log=log_html,
                              reload=reload).encode()


NO_WORKER = (b"// Studio preview: service workers are switched off here (see CONTRACT.md).\n"
             b"self.addEventListener('install', () => self.skipWaiting());\n"
             b"self.addEventListener('activate', event => event.waitUntil(\n"
             b"  self.registration.unregister()\n"
             b"    .then(() => self.clients.matchAll({type: 'window'}))\n"
             b"    .then(clients => clients.forEach(client => client.navigate(client.url)))));\n")
SELF_RELOADING = b'<meta name="studio-reload" content="off">'


def inject_bridge(body: bytes, self_reloading: bool = False) -> bytes:
    """Add the bridge. For an app whose development server updates the page by itself, also say
    so, so the studio does not reload the page on top of it when a file changes."""
    tag = (SELF_RELOADING if self_reloading else b"") + BRIDGE_TAG
    at = body.lower().rfind(b"</body>")
    return body + tag if at < 0 else body[:at] + tag + body[at:]


def as_download(scope, path: Path) -> dict:
    """`?download=1` makes the browser save the file. A link's `download` attribute is ignored
    when the link points at another origin, which is every link from the studio to a project."""
    if b"download=1" not in scope.get("query_string", b"").split(b"&"):
        return {}
    name = "".join(c if c.isalnum() or c in "._- ()" else "_" for c in path.name) or "file"
    return {"content-disposition": f'attachment; filename="{name}"'}


RANGE_CHUNK = 8 * 1024 * 1024


def byte_range(header: str, size: int):
    """`Range: bytes=a-b` → (first, last), None when there is no usable range header, or
    "unsatisfiable". An open-ended range is answered in chunks of 8 MB, which is what players expect."""
    header = (header or "").strip().lower()
    if not header.startswith("bytes=") or "," in header or size <= 0:
        return None
    first, _, last = header[6:].partition("-")
    try:
        if first == "":                      # the last n bytes
            count = int(last)
            return (max(0, size - count), size - 1) if count > 0 else None
        start = int(first)
        end = min(int(last), size - 1) if last else min(size - 1, start + RANGE_CHUNK - 1)
    except ValueError:
        return None
    if start >= size or end < start:
        return "unsatisfiable"
    return start, end


def content_type(path: Path) -> str:
    kind = TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    text = kind.startswith("text/") or kind in ("application/json", "image/svg+xml")
    return f"{kind}; charset=utf-8" if text else kind


class PreviewApp:
    """A minimal ASGI static server for one workspace."""

    def __init__(self, workspace: Path, target=None):
        self.workspace = workspace
        self.target = target or (lambda: ("static", None, None))  # → (mode, app port, app)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0), follow_redirects=False,
                                        trust_env=False)

    def resolve(self, url_path: str) -> tuple[int, Path | None, str | None]:
        """→ (status, file, redirect). Never leaves the workspace and never serves .git."""
        parts = [part for part in url_path.split("/") if part]
        if any(part in (".", "..") or part.lower() == ".git" or "\\" in part or "\x00" in part
               for part in parts):
            return 404, None, None
        root = self.workspace.resolve()

        def allowed(path: Path) -> bool:
            # Checked where a link ends, not by the names in the address: a link inside the
            # workspace can point outside it or into .git.
            if path != root and root not in path.parents:
                return False
            return not any(part.lower() == ".git" for part in path.relative_to(root).parts)

        target = root.joinpath(*parts).resolve()
        if not allowed(target):
            return 404, None, None
        if target.is_dir():
            if not url_path.endswith("/"):
                return 307, None, url_path + "/"
            # index.html is added after the folder was checked, and can itself be a link.
            target = (target / "index.html").resolve()
            if not allowed(target):
                return 404, None, None
        if not target.is_file():
            return 404, (target if target == root / "index.html" else None), None
        return 200, target, None

    async def __call__(self, scope, receive, send) -> None:
        mode, port, app = self.target()
        if scope["type"] == "websocket":
            return await (self._proxy_ws(scope, receive, send, port, app) if mode == "proxy"
                          else send({"type": "websocket.close", "code": 1013}))
        if scope["type"] != "http":
            return
        headers = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
        path = scope["path"]
        if path == "/__studio/bridge.js":
            body = await asyncio.to_thread(BRIDGE_PATH.read_bytes)
            return await self._send(send, scope, 200, body, "text/javascript; charset=utf-8", headers)
        if dict(scope["headers"]).get(b"service-worker") == b"script":
            # A service worker would fetch the project's pages itself (so they arrive without the
            # bridge) and serve old copies of them. In the studio a project never gets one: the
            # browser is handed a worker that removes itself, which also retires one registered earlier.
            return await self._send(send, scope, 200, NO_WORKER, "text/javascript; charset=utf-8", headers)
        if path.startswith("/__studio/files/"):
            # The project's files as they are on disk, whatever is serving the project URL.
            status, target, _redirect = self.resolve(path[len("/__studio/files"):])
            if status != 200 or scope["method"] not in ("GET", "HEAD"):
                return await self._send(send, scope, 404, b"Not found", "text/plain; charset=utf-8", headers)
            return await self._send_file(send, scope, target, {**headers, **as_download(scope, target)})
        if mode == "proxy":
            return await self._proxy(scope, receive, send, port, app)
        if mode == "status":
            wants_page = b"text/html" in dict(scope["headers"]).get(b"accept", b"")
            body = inject_bridge(status_page(app)) if wants_page else b"The project's app is not running."
            return await self._send(send, scope, 503, body,
                                    "text/html; charset=utf-8" if wants_page else "text/plain; charset=utf-8", headers)
        if scope["method"] not in ("GET", "HEAD"):
            return await self._send(send, scope, 405, b"Method not allowed", "text/plain; charset=utf-8", headers)
        status, target, redirect = self.resolve(path)
        if redirect:
            query = scope.get("query_string", b"").decode("latin-1")
            headers["location"] = redirect + (f"?{query}" if query else "")
            return await self._send(send, scope, 307, b"", "text/plain; charset=utf-8", headers)
        if status == 404:
            # A missing root index.html is a normal state for a new project, not an error.
            page, code = (EMPTY, 200) if target else (MISSING, 404)
            return await self._send(send, scope, code, inject_bridge(page.encode()), "text/html; charset=utf-8", headers)
        kind = content_type(target)
        if not kind.startswith("text/html"):
            return await self._send_file(send, scope, target, {**headers, **as_download(scope, target)})
        body = inject_bridge(await asyncio.to_thread(target.read_bytes))
        await self._send(send, scope, 200, body, kind, {**headers, **as_download(scope, target)})

    @staticmethod
    def _host(app, port: int) -> str:
        """host:port of the upstream: the studio's own app on 127.0.0.1, or an attached address."""
        host = getattr(app, "host", None) or "127.0.0.1"
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    async def _proxy(self, scope, receive, send, port: int, app) -> None:
        """Forward one request to the project's app; inject the bridge into HTML answers."""
        query = scope.get("query_string", b"")
        upstream_host = self._host(app, port)
        url = f"http://{upstream_host}{scope['raw_path'].decode('latin-1') if scope.get('raw_path') else scope['path']}"
        if query and "?" not in url:
            url += "?" + query.decode("latin-1")
        request_headers = [(k, v) for k, v in scope["headers"]
                           if k.decode("latin-1").lower() not in (
                               "host", "accept-encoding", "connection", "if-none-match", "if-modified-since",
                               *NOT_FORWARDED)]
        request_headers.append((b"host", upstream_host.encode()))
        request_headers = self._own_origin(scope, request_headers, upstream_host)
        request_headers.append((b"accept-encoding", b"identity"))
        request_headers = self._forwarded_for(scope, request_headers)

        async def body():
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    return
                if message.get("body"):
                    yield message["body"]
                if not message.get("more_body"):
                    return

        has_body = scope["method"] not in ("GET", "HEAD", "OPTIONS", "DELETE") or any(
            k.lower() in (b"content-length", b"transfer-encoding") for k, _ in scope["headers"])
        try:
            upstream = await self.client.send(
                self.client.build_request(scope["method"], url, headers=request_headers,
                                          content=body() if has_body else None), stream=True)
        except httpx.HTTPError:
            page = inject_bridge(status_page(app))
            return await self._send(send, scope, 502, page, "text/html; charset=utf-8", {"cache-control": "no-store"})
        try:
            kind = upstream.headers.get("content-type", "")
            out = [(k.lower(), v) for k, v in upstream.headers.raw if k.decode("latin-1").lower() not in HOP_BY_HOP]
            length = upstream.headers.get("content-length")
            # This is a live preview: the browser must never show an older copy of the app's
            # files (frameworks' static handlers invite heuristic caching with ETag/Last-Modified).
            out = [(k, v) for k, v in out if k not in (b"cache-control", b"etag", b"last-modified", b"expires")]
            out = self._frameable(out)
            out = self._back_to_page(scope, out, port, upstream_host)
            out.append((b"cache-control", b"no-store"))
            if kind.startswith("text/html") and self._is_page_load(scope) and scope["method"] != "HEAD" and (
                    length is None or int(length) <= INJECT_LIMIT):
                page = inject_bridge(await upstream.aread(), app.self_reloading() if app else False)
                out.append((b"content-length", str(len(page)).encode()))
                await send({"type": "http.response.start", "status": upstream.status_code, "headers": out})
                return await send({"type": "http.response.body", "body": page})
            if length is not None:
                out.append((b"content-length", length.encode()))
            await send({"type": "http.response.start", "status": upstream.status_code, "headers": out})
            if scope["method"] != "HEAD":
                async for chunk in upstream.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        except (httpx.HTTPError, OSError):
            pass  # the app went away mid-response; the client sees a cut connection
        finally:
            await upstream.aclose()

    @staticmethod
    def _is_page_load(scope) -> bool:
        """Only a navigation gets the bridge. An app's fetch() answers can be labelled text/html
        too (some APIs do that for JSON), and must reach the page's code untouched."""
        headers = dict(scope["headers"])
        dest = headers.get(b"sec-fetch-dest")
        if dest is not None:
            return dest in (b"document", b"iframe", b"frame")
        return b"text/html" in headers.get(b"accept", b"")

    @staticmethod
    def _own_origin(scope, headers: list, upstream_host: str) -> list:
        """A request from the project's own page reaches the app as a request from the app's own
        origin. Development servers refuse the rest (Next's allowedDevOrigins, CSRF checks), and the
        person may be on a phone where the page's address is the Mac's tailnet name. An Origin
        from anywhere else is passed on as it came, so the app's own checks still see it."""
        given = dict(scope["headers"])
        host, origin = given.get(b"host", b""), given.get(b"origin", b"")
        if not origin or origin.split(b"://", 1)[-1] != host:
            return headers
        return [(k, v) for k, v in headers if k.lower() != b"origin"] + [(b"origin", f"http://{upstream_host}".encode())]

    @staticmethod
    def _forwarded_for(scope, headers: list) -> list:
        """The app learns which address asked (uvicorn and most servers trust this from loopback);
        without it every request through the studio would look like one from the Mac itself."""
        client = (scope.get("client") or ("", 0))[0]
        if not client:
            return headers
        given = [v.decode("latin-1").strip() for k, v in headers if k.lower() == b"x-forwarded-for"]
        chain = ", ".join([*filter(None, given), client])
        return [(k, v) for k, v in headers if k.lower() != b"x-forwarded-for"] + [(b"x-forwarded-for", chain.encode("latin-1"))]

    @staticmethod
    def _back_to_page(scope, headers: list, port: int, upstream_host: str) -> list:
        """The app builds absolute addresses from the Host it was sent, which is its own private one
        (127.0.0.1 and its port). Followed by the browser, a redirect there leaves the project's
        address: on a phone 127.0.0.1 is the phone, and on the Mac the app's own port has no bridge.
        Such an origin in Location or Content-Location becomes the one the request came in on; a
        cookie scoped to the app's loopback host by name becomes one for whatever host the browser used."""
        host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
        own = {upstream_host.lower(), *(f"{name}:{port}" for name in LOOPBACK_NAMES)}
        out = []
        for name, value in headers:
            if host and name in (b"location", b"content-location"):
                text = value.decode("latin-1")
                match = re.match(r"(?:https?:)?//([^/?#]*)", text, re.I)
                if match and match.group(1).lower() in own:
                    value = f"{scope.get('scheme', 'http')}://{host}{text[match.end():]}".encode("latin-1")
            elif name == b"set-cookie":
                parts = value.decode("latin-1").split(";")
                kept = [part for part in parts[1:] if not (
                    part.strip().lower().startswith("domain=")
                    and part.split("=", 1)[1].strip().lstrip(".").lower() in LOOPBACK_NAMES)]
                value = ";".join([parts[0], *kept]).encode("latin-1")
            out.append((name, value))
        return out

    @staticmethod
    def _frameable(headers: list) -> list:
        """The project's page is shown inside the studio, so an app's refusal to be framed
        (X-Frame-Options, or frame-ancestors in its Content-Security-Policy; Django sends DENY
        by default) is dropped here. The rest of its policy is kept."""
        out = []
        for name, value in headers:
            if name == b"x-frame-options":
                continue
            if name in (b"content-security-policy", b"content-security-policy-report-only"):
                kept = [part.strip() for part in value.decode("latin-1").split(";")
                        if part.strip() and not part.strip().lower().startswith("frame-ancestors")]
                if not kept:
                    continue
                value = "; ".join(kept).encode("latin-1")
            out.append((name, value))
        return out

    async def _proxy_ws(self, scope, receive, send, port: int, app=None) -> None:
        query = scope.get("query_string", b"").decode("latin-1")
        upstream_host = self._host(app, port)
        url = f"ws://{upstream_host}{scope['path']}" + (f"?{query}" if query else "")
        given = dict(scope["headers"])
        origin = given.get(b"origin", b"")
        if origin and origin.split(b"://", 1)[-1].lower() != given.get(b"host", b"").lower():
            # The socket carries the person's cookies and reaches the app as one from its own
            # origin, so a page on another site must not open it. Closed before accept, the
            # browser gets a refused handshake and the app is never contacted. No Origin means
            # no browser (a script or test), which is let through as before.
            return await send({"type": "websocket.close", "code": 4403})
        passed = {name.decode("latin-1"): given[name].decode("latin-1") for name in (b"cookie", b"authorization")
                  if name in given}
        try:
            # The app sees a socket from its own origin, with the page's cookies (a signed-in app
            # checks them on its sockets too).
            upstream = await websockets.connect(url, subprotocols=scope.get("subprotocols") or None,
                                                origin=f"http://{upstream_host}", additional_headers=passed,
                                                open_timeout=5, max_size=None)
        except Exception:
            return await send({"type": "websocket.close", "code": 1013})
        message = await receive()
        if message["type"] != "websocket.connect":
            return await upstream.close()
        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

        async def down():
            async for data in upstream:
                await send({"type": "websocket.send", **({"text": data} if isinstance(data, str) else {"bytes": data})})

        async def up():
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])

        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await send({"type": "websocket.close", "code": 1000})

    async def _send_file(self, send, scope, target: Path, headers: dict) -> None:
        """A file as it is on disk, whole or the byte range asked for. A video player asks for
        ranges (to seek, and Safari will not play a video from a server that cannot give them)."""
        size = target.stat().st_size
        headers = {**headers, "accept-ranges": "bytes"}
        span = byte_range(dict(scope["headers"]).get(b"range", b"").decode("latin-1"), size)
        if span == "unsatisfiable":
            return await self._send(send, scope, 416, b"", content_type(target), {**headers, "content-range": f"bytes */{size}"})
        first, last = span or (0, size - 1)

        def read() -> bytes:
            with target.open("rb") as handle:
                handle.seek(first)
                return handle.read(last - first + 1)
        body = b"" if scope["method"] == "HEAD" or size == 0 else await asyncio.to_thread(read)
        if span:
            headers["content-range"] = f"bytes {first}-{last}/{size}"
        raw = [(b"content-type", content_type(target).encode()), (b"content-length", str(max(0, last - first + 1)).encode())]
        raw += [(key.encode(), value.encode("latin-1", "replace")) for key, value in headers.items()]
        await send({"type": "http.response.start", "status": 206 if span else 200, "headers": raw})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _send(send, scope, status: int, body: bytes, kind: str, headers: dict) -> None:
        raw = [(b"content-type", kind.encode()), (b"content-length", str(len(body)).encode())]
        raw += [(key.encode(), value.encode("latin-1", "replace")) for key, value in headers.items()]
        await send({"type": "http.response.start", "status": status, "headers": raw})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body})


async def listen(app, sockets: list[tuple[socket.socket, bool]], tls: tuple[str, str] | None,
                 state: ServerState) -> list[asyncio.AbstractServer]:
    """Serve an ASGI app on already-bound sockets in the running loop; (socket, True) speaks TLS.

    Uses uvicorn's HTTP protocol directly (asyncio `create_server`) rather than a
    `uvicorn.Server` per port: a second Server would re-install signal handlers, and a
    Config with its own log level would reconfigure the studio server's loggers.
    """
    loop, servers, configs = asyncio.get_running_loop(), [], {}
    for sock, secure in sockets:
        secure = bool(secure and tls)
        if secure not in configs:
            configs[secure] = uvicorn.Config(
                app, log_config=None, lifespan="off", ws="websockets-sansio",
                ssl_certfile=tls[0] if secure else None, ssl_keyfile=tls[1] if secure else None)
            configs[secure].load()
        config = configs[secure]

        def protocol(config=config) -> asyncio.Protocol:
            connection = config.http_protocol_class(config=config, server_state=state, app_state={})
            # (uvicorn's HTTP protocol hands Upgrade requests to config.ws_protocol_class itself.)
            connection.access_log = False
            return connection

        servers.append(await loop.create_server(protocol, sock=sock, backlog=128, ssl=config.ssl if secure else None))
    return servers


def bind(hosts: tuple[str, ...], port: int) -> list[tuple[socket.socket, bool]]:
    """→ [(socket, is it an extra address)]. Raises OSError when any address cannot be bound."""
    sockets = []
    try:
        for host in hosts:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append((sock, host != "127.0.0.1"))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError:
        for sock, _extra in sockets:
            sock.close()
        raise
    return sockets


class Previews:
    """One listening socket per project and address, all in the studio's event loop."""

    def __init__(self, hosts: tuple[str, ...] = (), guard=None, tls: tuple[str, str] | None = None):
        self.tls = tls
        self.running: dict[str, tuple[list[asyncio.AbstractServer], ServerState, int]] = {}
        self.hosts = ("127.0.0.1", *hosts)   # loopback first: it decides whether the port is free
        self.guard = guard or (lambda app: app)

    def port(self, project_id: str) -> int | None:
        entry = self.running.get(project_id)
        return entry[2] if entry else None

    async def start(self, project_id: str, workspace: Path, port: int, target=None) -> None:
        """Serve `workspace` on 127.0.0.1:port. Raises OSError when the port cannot be bound."""
        if project_id in self.running:
            return
        sockets = bind(self.hosts, port)
        state = ServerState()
        servers = await listen(self.guard(PreviewApp(workspace, target)), sockets, self.tls, state)
        self.running[project_id] = (servers, state, port)

    async def stop(self, project_id: str) -> None:
        entry = self.running.pop(project_id, None)
        if not entry:
            return
        servers, state, _port = entry
        for server in servers:
            server.close()
        for connection in list(state.connections):
            connection.shutdown()
        try:
            await asyncio.wait_for(asyncio.gather(*(server.wait_closed() for server in servers)), 1)
        except asyncio.TimeoutError:  # a connection that will not finish: drop it
            for connection in list(state.connections):
                with contextlib.suppress(Exception):
                    connection.transport.close()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(project_id) for project_id in list(self.running)))
