"""Whiteboard back end: serves the page and saves the drawing to board.excalidraw.

Standard library only. The drawing lives in the project folder, so it is versioned with the
project and the assistant can read and edit it like any other file.
"""
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
BOARD = ROOT / "board.excalidraw"
LIMIT = 20 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/api/board/stamp":  # lets the page notice edits made on disk
            body = json.dumps({"stamp": BOARD.stat().st_mtime_ns if BOARD.exists() else 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        return super().do_GET()

    def log_message(self, format, *args):
        if "/api/board/stamp" not in (args[0] if args else ""):
            super().log_message(format, *args)

    def do_PUT(self):
        if self.path != "/api/board":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= LIMIT:
            return self.send_error(413)
        raw = self.rfile.read(length)
        try:
            scene = json.loads(raw)
            assert isinstance(scene.get("elements"), list)
        except Exception:
            return self.send_error(400, "Not an Excalidraw scene")
        tmp = BOARD.with_suffix(".tmp")
        tmp.write_text(json.dumps(scene, indent=1), "utf-8")
        tmp.replace(BOARD)
        body = json.dumps({"stamp": BOARD.stat().st_mtime_ns}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"whiteboard on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
