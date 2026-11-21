"""A page with its own address signs in by itself (CONTRACT.md): the two paths every page port answers."""
from urllib.parse import quote

import pytest

from studio2_server.access import REQUEST_NAMES
from studio2_server.preview import BACK_PATH, HERE_PATH, ONE_PIXEL, PreviewApp, back_target

NAMES = frozenset({"studio.example.org", "shop-studio.example.org"})


async def call(app, path, query=b""):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": "GET", "path": path, "query_string": query, "headers": []}, receive, send)
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    return sent[0]["status"], headers, b"".join(m.get("body", b"") for m in sent[1:])


@pytest.fixture
def names():
    marker = REQUEST_NAMES.set(NAMES)
    yield
    REQUEST_NAMES.reset(marker)


async def test_the_picture_is_served_by_the_page_port_itself_even_with_no_app(tmp_path):
    for target in (None, lambda: ("proxy", 1, None)):       # files, or an app that is not there
        status, headers, body = await call(PreviewApp(tmp_path, target=target), HERE_PATH)
        assert (status, headers["content-type"], body) == (200, "image/gif", ONE_PIXEL)
        assert headers["cache-control"] == "no-store"


async def test_back_returns_to_an_address_of_this_studio_only(tmp_path, names):
    app = PreviewApp(tmp_path)
    home = "https://studio.example.org/p/p_1?tab=chat"
    status, headers, _body = await call(app, BACK_PATH, f"to={quote(home, safe='')}".encode())
    assert (status, headers["location"]) == (302, home)
    for elsewhere in ("https://evil.example.com/", "https://studio.example.org.evil.com/", "//studio.example.org/",
                      "javascript:alert(1)", "ftp://studio.example.org/", ""):
        status, headers, _body = await call(app, BACK_PATH, f"to={quote(elsewhere, safe='')}".encode())
        assert status == 400 and "location" not in headers, elsewhere


async def test_back_goes_nowhere_when_the_studio_has_no_names(tmp_path):
    status, _headers, _body = await call(PreviewApp(tmp_path), BACK_PATH, b"to=https%3A%2F%2Fstudio.example.org%2F")
    assert status == 400


def test_an_address_is_checked_by_its_name_not_by_how_it_looks():
    assert back_target(b"to=https://STUDIO.example.org/p/x", NAMES) == "https://STUDIO.example.org/p/x"
    assert back_target(b"to=https://studio.example.org@evil.example.com/", NAMES) is None
    assert back_target(b"to=https://studio.example.org/%0d%0aSet-Cookie:a=b", NAMES) is None
    assert back_target(b"to=https://studio.example.org/" + b"a" * 2100, NAMES) is None
    assert back_target(b"to=http://[bad", NAMES) is None
