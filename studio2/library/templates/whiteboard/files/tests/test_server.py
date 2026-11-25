"""Tests for the whiteboard back end. The studio runs them with run_tests (pytest).

The drawing is the project's own file, so these tests never save a drawing: they check the
answers that change nothing. A standard-library server has no test client, so each test starts
it on a free port of its own (port 0) and stops it after.
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def start():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_the_stamp_answers():
    httpd, base = start()
    try:
        with urllib.request.urlopen(base + "/api/board/stamp") as answer:
            assert answer.status == 200 and "stamp" in json.loads(answer.read())
    finally:
        httpd.shutdown()


def test_a_scene_that_is_not_one_is_refused_and_nothing_is_saved():
    before = server.BOARD.read_bytes() if server.BOARD.exists() else None
    httpd, base = start()
    try:
        request = urllib.request.Request(base + "/api/board", data=b'{"no": "elements"}', method="PUT")
        try:
            urllib.request.urlopen(request)
            raise AssertionError("the bad scene was accepted")
        except urllib.error.HTTPError as refused:
            assert refused.code == 400
    finally:
        httpd.shutdown()
    assert (server.BOARD.read_bytes() if server.BOARD.exists() else None) == before
