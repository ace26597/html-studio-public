"""The app's own private address in what it sends is replaced with the one the request came in on.

CONTRACT.md: "`studio.json` — a project with its own back end", "The app's own address in what it
sends". A real app (standard library, in a subprocess) behind the real preview listener. Zero model calls.
"""

import asyncio
import gzip
import socket
import sys

import httpx
from uvicorn.server import ServerState

from studio2_server import preview as preview_module
from studio2_server.preview import PreviewApp, bind, listen

APP = r'''
import gzip, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
ME = f"127.0.0.1:{PORT}"
PAGE = (f'<!doctype html><html><head><link rel="stylesheet" href="http://{ME}/style.css">'
        f'<script src="//localhost:{PORT}/app.js"></script></head><body>'
        f'<a id="api" href="http://{ME}/api/data">data</a> <span id="ws">ws://{ME}/ws</span>'
        f'<a id="other-port" href="http://127.0.0.1:{PORT + 1}/x">x</a>'
        f'<a id="longer" href="http://127.0.0.1:{PORT}0/x">x</a>'
        f'<a id="query" href="https://elsewhere.example/?next=127.0.0.1:{PORT}">y</a>'
        f'<a id="ftp" href="HTTPS://LOCALHOST:{PORT}/up">z</a></body></html>').encode()
FILES = {
    "/page": ("text/html; charset=utf-8", PAGE),
    "/app.js": ("text/javascript", f'fetch("http://[::1]:{PORT}/api/data"); new WebSocket("wss://{ME}/ws");'.encode()),
    "/data.json": ("application/json", f'{{"a": "http:\\/\\/{ME}\\/api", "b": "https://localhost:{PORT}/x"}}'.encode()),
    "/style.css": ("text/css", f"body{{background:url(http://{ME}/bg.png)}}".encode()),
    "/manifest": ("application/manifest+json", f'{{"start_url": "http://{ME}/"}}'.encode()),
    "/notes.txt": ("text/plain", f"see http://{ME}/notes".encode()),
    "/img.png": ("image/png", b"\x89PNG\r\n" + f"http://{ME}/x".encode()),
    "/font.woff2": ("font/woff2", f"http://{ME}/x".encode()),
    "/svg": ("image/svg+xml", f'<svg><image href="http://{ME}/i.png"/></svg>'.encode()),
    "/sse": ("text/event-stream", f"data: http://{ME}/x\n\n".encode()),
    "/big": ("text/plain", b"x" * (9 * 1024 * 1024) + f" http://{ME}/end".encode()),
    # No address of the app's own in these: they must come out exactly as sent, by any name.
    "/clean.html": ("text/html; charset=utf-8", f'<p><a href="http://127.0.0.1:{PORT + 1}/">x</a> https://studio.example/ w h /</p>'.encode()),
    "/clean.js": ("text/javascript", f'const a = "http://localhost:{PORT}0/"; const b = "//127.0.0.1"; // which path\\'.encode()),
    "/clean.json": ("application/json", f'{{"host": "127.0.0.1:{PORT}", "u": "https://elsewhere.example/"}}'.encode()),
}
CLEAN_PIECES = [b"which", b" path", b" /", b"tmp/", b" with", b" h", b"\\"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, kind, body, status=200, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"abc"')
        self.send_header("Content-MD5", "x")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def chunk(self, data):
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.flush()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path
        if path in FILES:
            kind, body = FILES[path]
            extra = [("Link", f"<http://{ME}/app.js>; rel=preload; as=script"),
                     ("Refresh", f"30; url=http://{ME}/page")] if path == "/page" else []
            return self.send_body(kind, body, extra=extra)
        if path == "/gzip.json":
            return self.send_body("application/json", gzip.compress(f'{{"u": "http://{ME}/z"}}'.encode(), mtime=0),
                                  extra=[("Content-Encoding", "gzip")])
        if path == "/clean-gzip.json":
            return self.send_body("application/json", gzip.compress(b'{"u": "https://elsewhere.example/"}', mtime=0),
                                  extra=[("Content-Encoding", "gzip")])
        if path == "/clean-chunked.js":
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in CLEAN_PIECES:
                self.chunk(piece)
            self.chunk(b"")
            return
        if path == "/br.json":     # an encoding the studio does not decode: passed on as it is
            return self.send_body("application/json", b"\x1b\x00not-really-brotli http://" + ME.encode(),
                                  extra=[("Content-Encoding", "br")])
        if path == "/range.txt":
            return self.send_body("text/plain", f"http://{ME}/r".encode(), status=206,
                                  extra=[("Content-Range", "bytes 0-10/100")])
        if path == "/bomb.json":        # small on the wire, 20 MB once decoded
            return self.send_body("application/json", gzip.compress(b" " * (20 * 1024 * 1024) + f'"http://{ME}/z"'.encode(), mtime=0),
                                  extra=[("Content-Encoding", "gzip")])
        if path == "/large.js":
            return self.send_body("text/javascript", (f'fetch("http://{ME}/x");\n' * 40000).encode())
        if path in ("/liar.json", "/liar.html"):     # says gzip, is not
            kind = "application/json" if path.endswith(".json") else "text/html"
            return self.send_body(kind, f'{{"u": "http://{ME}/z"}}'.encode(), extra=[("Content-Encoding", "gzip")])
        if path in ("/short.json", "/short.html"):   # promises 500 bytes, sends fewer, closes
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if path.endswith(".json") else "text/html")
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(f'{{"u": "http://{ME}/z"}}'.encode())
            self.wfile.flush()
            self.close_connection = True
            return
        if path in ("/words.txt", "/words.json"):       # a chat app streaming tokens as plain text or JSON
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8" if path.endswith(".txt") else "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in CLEAN_PIECES + [f" http://{ME}/".encode()]:
                self.chunk(piece)
                time.sleep(0.3)
            self.chunk(b"")
            return
        if path in ("/chunked.txt", "/forever.txt", "/chunked.js", "/forever.js"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if path.endswith(".txt") else "text/javascript")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            text = f"one http://{ME}/a\ntwo ws://localhost:{PORT}/b\nend http://127.0.0.1:{PORT}".encode()
            if path.startswith("/forever"):
                self.chunk(f"first http://{ME}/a\n".encode())
                time.sleep(30)          # never ends while the test reads
                return
            for at in range(0, len(text), 7):     # the address is cut across pieces
                self.chunk(text[at:at + 7])
                time.sleep(0.002)
            self.chunk(b"")
            return
        self.send_body("text/plain", b"missing", status=404)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def started(tmp_path):
    port = free_port()
    script = tmp_path / "app.py"
    script.write_text(APP)
    process = await asyncio.create_subprocess_exec(sys.executable, str(script), str(port))
    async with httpx.AsyncClient() as http:
        for _ in range(300):
            try:
                await http.get(f"http://127.0.0.1:{port}/notes.txt")
                break
            except httpx.TransportError:
                await asyncio.sleep(0.05)
    preview = PreviewApp(tmp_path, target=lambda: ("proxy", port, None))
    page_port = free_port()
    servers = await listen(preview, bind(("127.0.0.1",), page_port), None, ServerState())
    return process, preview, servers, port, page_port


async def stopped(process, preview, servers):
    process.kill()
    await process.wait()
    for server in servers:
        server.close()
    await preview.client.aclose()


PATHS = ("/page", "/app.js", "/data.json", "/style.css", "/manifest", "/notes.txt", "/img.png", "/font.woff2",
         "/svg", "/sse", "/gzip.json", "/br.json", "/range.txt", "/chunked.txt", "/big")


CLEAN = ("/clean.html", "/clean.js", "/clean.json", "/clean-gzip.json", "/clean-chunked.js", "/img.png", "/br.json")


async def raw(http, url, host=None):
    """The bytes as they came over the wire (not decoded), and the headers that describe them."""
    async with http.stream("GET", url, headers={"host": host} if host else {}) as answer:
        body = b"".join([piece async for piece in answer.aiter_raw()])
        return body, answer.headers.get("content-length"), answer.headers.get("content-encoding")


async def test_an_answer_without_the_address_is_byte_for_byte_what_the_app_sent_by_any_name(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for path in CLEAN:
                direct = await raw(http, f"http://127.0.0.1:{port}{path}")
                for host in (f"127.0.0.1:{page_port}", f"localhost:{page_port}", f"studio.example:{page_port}"):
                    assert await raw(http, f"http://127.0.0.1:{page_port}{path}", host) == direct, (path, host)
    finally:
        await stopped(process, preview, servers)


async def test_by_loopback_too_the_apps_own_address_becomes_the_page_address(tmp_path):
    """On this computer the app's port is another origin than the page: a fetch there is refused."""
    process, preview, servers, port, page_port = await started(tmp_path)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for here in (f"127.0.0.1:{page_port}", f"localhost:{page_port}"):
                text = (await http.get(f"http://127.0.0.1:{page_port}/page", headers={"host": here})).text
                assert f'href="http://{here}/style.css"' in text and f'src="//{here}/app.js"' in text
                js = (await http.get(f"http://127.0.0.1:{page_port}/app.js", headers={"host": here})).text
                assert js == f'fetch("http://{here}/api/data"); new WebSocket("ws://{here}/ws");'
                gz = await http.get(f"http://127.0.0.1:{page_port}/gzip.json", headers={"host": here})
                assert gz.json() == {"u": f"http://{here}/z"}
    finally:
        await stopped(process, preview, servers)


async def test_by_another_name_the_apps_own_address_becomes_the_one_the_request_came_in_on(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    here = f"studio.example:{page_port}"
    me = f"127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=30, base_url=f"http://127.0.0.1:{page_port}",
                                     headers={"host": here}) as http:
            page = await http.get("/page")
            text = page.text
            assert f'href="http://{here}/style.css"' in text and f'src="//{here}/app.js"' in text
            assert f'href="http://{here}/api/data"' in text and f"ws://{here}/ws" in text
            assert f'href="http://{here}/up"' in text                               # any case
            assert f"http://127.0.0.1:{port + 1}/x" in text                          # another port
            assert f"http://127.0.0.1:{port}0/x" in text                             # a longer port
            assert f"next=127.0.0.1:{port}" in text                                  # no // in front
            assert me not in text.replace(f"{me}0", "").replace(f"next={me}", "")
            assert int(page.headers["content-length"]) == len(page.content)
            assert "etag" not in page.headers and "content-md5" not in page.headers
            assert page.headers["link"] == f"<http://{here}/app.js>; rel=preload; as=script"
            assert page.headers["refresh"] == f"30; url=http://{here}/page"

            assert (await http.get("/app.js")).text == \
                f'fetch("http://{here}/api/data"); new WebSocket("ws://{here}/ws");'
            data = await http.get("/data.json")
            assert data.json() == {"a": f"http://{here}/api", "b": f"http://{here}/x"}
            assert '"http:\\/\\/' + here + '\\/api"' in data.text                    # the escaping is kept
            assert (await http.get("/style.css")).text == f"body{{background:url(http://{here}/bg.png)}}"
            assert (await http.get("/manifest")).json() == {"start_url": f"http://{here}/"}
            assert (await http.get("/notes.txt")).text == f"see http://{here}/notes"
            gz = await http.get("/gzip.json")
            assert gz.json() == {"u": f"http://{here}/z"} and "content-encoding" not in gz.headers
            assert int(gz.headers["content-length"]) == len(gz.content)

            # Left alone: other kinds, streams of events, partial answers, too large, unknown encodings.
            for path in ("/img.png", "/font.woff2", "/svg", "/sse", "/range.txt"):
                assert me.encode() in (await http.get(path)).content, path
            big = await http.get("/big")
            assert big.content.endswith(f" http://{me}/end".encode())
            assert int(big.headers["content-length"]) == len(big.content)
            br = await http.get("/br.json")
            assert br.headers["content-encoding"] == "br" and br.content.endswith(me.encode())
            head = await http.head("/page")
            assert head.status_code == 200 and head.content == b""

            chunked = await http.get("/chunked.js")
            assert chunked.text == (f"one http://{here}/a\ntwo ws://{here}/b\nend http://{here}")
            # Plain text that streams is left alone (only plain text with a length is rewritten).
            assert (await http.get("/chunked.txt")).text == (
                f"one http://{me}/a\ntwo ws://localhost:{port}/b\nend http://127.0.0.1:{port}")
    finally:
        await stopped(process, preview, servers)


async def test_a_stream_that_never_ends_is_rewritten_as_it_comes_and_not_held(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    here = f"studio.example:{page_port}"
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            async with http.stream("GET", f"http://127.0.0.1:{page_port}/forever.js", headers={"host": here}) as answer:
                first = await asyncio.wait_for(answer.aiter_raw().__anext__(), 5)
        assert first.startswith(b"first http://" + here.encode() + b"/a")
    finally:
        await stopped(process, preview, servers)


async def test_plain_text_that_streams_comes_piece_by_piece_untouched_and_on_time(tmp_path):
    """A piece ending in h, w, / or \\ could start the address; holding it would make a stream of
    tokens lag a character behind. Streamed plain text and streamed JSON are therefore not
    rewritten at all."""
    process, preview, servers, port, page_port = await started(tmp_path)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for host in (f"127.0.0.1:{page_port}", f"studio.example:{page_port}"):
                pieces = []
                async with http.stream("GET", f"http://127.0.0.1:{page_port}/words.txt", headers={"host": host}) as answer:
                    async for piece in answer.aiter_raw():
                        pieces.append(piece)
                assert pieces[:7] == [b"which", b" path", b" /", b"tmp/", b" with", b" h", b"\\"], host
                assert b"".join(pieces[7:]) == f" http://127.0.0.1:{port}/".encode()
                # The same for JSON that streams (tokens as lines of JSON): people watch it arrive.
                pieces = []
                async with http.stream("GET", f"http://127.0.0.1:{page_port}/words.json", headers={"host": host}) as answer:
                    async for piece in answer.aiter_raw():
                        pieces.append(piece)
                assert pieces[:7] == [b"which", b" path", b" /", b"tmp/", b" with", b" h", b"\\"], host
                assert b"".join(pieces[7:]) == f" http://127.0.0.1:{port}/".encode()
    finally:
        await stopped(process, preview, servers)


async def test_over_https_the_scheme_follows_the_request(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=preview),
                                     base_url="https://studio.example:8201") as web:
            assert (await web.get("/app.js")).text == \
                'fetch("https://studio.example:8201/api/data"); new WebSocket("wss://studio.example:8201/ws");'
            assert "ws://" not in (await web.get("/page")).text.replace("wss://", "")
    finally:
        await stopped(process, preview, servers)


def test_cut_anywhere_a_stream_comes_out_as_the_whole_answer_does():
    port = 8312
    rewriter = getattr(preview_module, "AddressRewriter")
    text = (f"a http://127.0.0.1:{port}/x b //localhost:{port} c ws://[::1]:{port}/w d http:\\/\\/127.0.0.1:{port}\\/e "
            f"f http://127.0.0.1:{port}0 g HTTP://LOCALHOST:{port} h http://127.0.0.1:{port}").encode()
    whole = rewriter(port, "127.0.0.1:8312", "http", "phone.example:8201")
    expected = whole.feed(text) + whole.end()
    assert expected.count(b"phone.example:8201") == 6 and f"{port}0".encode() in expected
    for first in range(len(text) + 1):
        for second in range(first, len(text) + 1, 5):
            cut = rewriter(port, "127.0.0.1:8312", "http", "phone.example:8201")
            out = cut.feed(text[:first]) + cut.feed(text[first:second]) + cut.feed(text[second:]) + cut.end()
            assert out == expected, (first, second)
    # What is sent at once: everything but a tail that could still become the address.
    held = rewriter(port, "127.0.0.1:8312", "http", "phone.example:8201")
    assert held.feed(b"line one\n") == b"line one\n"
    assert held.feed(b"next http://127.0") == b"next "
    assert held.feed(b".0.1:8312/z\n") == b"http://phone.example:8201/z\n"


def exchange(port: int, path: str, host: str, accept: str = "*/*") -> bytes:
    """One request over a plain socket; everything the server sent until it closed."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept: {accept}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            try:
                piece = sock.recv(65536)
            except OSError:
                break
            if not piece:
                break
            data += piece
    return data


async def test_an_app_that_lies_about_gzip_or_closes_early_is_passed_on_as_it_sent_never_a_500(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    me = f"127.0.0.1:{port}"
    try:
        for host in (f"127.0.0.1:{page_port}", f"studio.example:{page_port}"):
            for path in ("/liar.json", "/liar.html"):
                for accept in ("*/*", "text/html"):
                    answer = await asyncio.to_thread(exchange, page_port, path, host, accept)
                    head, _, body = answer.partition(b"\r\n\r\n")
                    assert head.startswith(b"HTTP/1.1 200"), (path, host, head[:40])
                    assert b"content-encoding: gzip" in head.lower() and body == f'{{"u": "http://{me}/z"}}'.encode()
            for path in ("/short.json", "/short.html"):
                for accept in ("*/*", "text/html"):
                    answer = await asyncio.to_thread(exchange, page_port, path, host, accept)
                    head, _, body = answer.partition(b"\r\n\r\n")
                    assert head.startswith(b"HTTP/1.1 200"), (path, host, head[:40])
                    assert b"content-length: 500" in head.lower() and body == f'{{"u": "http://{me}/z"}}'.encode()
    finally:
        await stopped(process, preview, servers)


async def test_what_is_decoded_is_capped_and_past_the_cap_the_answer_goes_on_untouched(tmp_path):
    process, preview, servers, port, page_port = await started(tmp_path)
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            direct = await raw(http, f"http://127.0.0.1:{port}/bomb.json")
            assert len(direct[0]) < 100_000 and direct[2] == "gzip"
            for host in (f"127.0.0.1:{page_port}", f"studio.example:{page_port}"):
                assert await raw(http, f"http://127.0.0.1:{page_port}/bomb.json", host) == direct, host
    finally:
        await stopped(process, preview, servers)


async def test_a_large_body_is_rewritten_off_the_event_loop(tmp_path, monkeypatch):
    """Twenty large scripts at once must not make a small request wait behind them."""
    import threading
    seen = []
    whole = preview_module.AddressRewriter.whole

    def spy(self, body):
        seen.append((len(body), threading.current_thread() is threading.main_thread()))
        return whole(self, body)
    monkeypatch.setattr(preview_module.AddressRewriter, "whole", spy)
    process, preview, servers, port, page_port = await started(tmp_path)
    here = f"studio.example:{page_port}"
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            large = await http.get(f"http://127.0.0.1:{page_port}/large.js", headers={"host": here})
            assert large.text.count(f"http://{here}/x") == 40000
            small = await http.get(f"http://127.0.0.1:{page_port}/app.js", headers={"host": here})
            assert f"http://{here}/api/data" in small.text
        bodies = [(size, on_loop) for size, on_loop in seen if size > 1000]
        assert any(size > 1_000_000 and not on_loop for size, on_loop in bodies), seen
        assert all(on_loop for size, on_loop in bodies if size < 100_000), seen    # small ones stay cheap
    finally:
        await stopped(process, preview, servers)
