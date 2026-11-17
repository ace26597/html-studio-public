"""Opening the studio from another device: the access key, the cookie, and host-following links."""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from studio2_server.access import COOKIE, check_settings, token_for
from studio2_server.api import create_app

KEY = "a-long-enough-access-key-for-tests"
REMOTE = "http://100.64.0.7:8200"


def remote_client(app, **headers):
    transport = httpx.ASGITransport(app=app, client=("100.64.0.9", 5555))
    return httpx.AsyncClient(transport=transport, base_url=REMOTE, headers=headers)


@pytest.fixture
async def open_app(settings):
    application = create_app(dataclasses.replace(settings, access_key=KEY, names=("mac.example.ts.net",)))
    await application.state.studio.start()
    yield application
    await application.state.studio.stop()


def test_other_addresses_need_a_real_key():
    check_settings((), None)
    with pytest.raises(SystemExit):
        check_settings(("100.64.0.7",), None)
    with pytest.raises(SystemExit):
        check_settings(("100.64.0.7",), "short")
    check_settings(("100.64.0.7",), KEY)


async def test_without_a_key_only_the_machine_itself_is_answered(app, client):
    assert (await client.get("/api/projects")).status_code == 200
    async with remote_client(app) as far:
        assert (await far.get("/api/projects")).status_code == 403
        assert (await far.get("/login")).status_code == 403
    # A tunnel or proxy on this machine connects from loopback: it must not count as local.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8200",
                                 headers={"X-Forwarded-For": "203.0.113.5"}) as proxied:
        assert (await proxied.get("/api/projects")).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.example.com") as named:
        assert (await named.get("/api/projects")).status_code == 421     # a name the studio does not know


async def test_sign_in_with_the_key_then_links_follow_the_host(open_app):
    async with remote_client(open_app) as far:
        assert (await far.get("/api/projects")).status_code == 401
        page = await far.get("/", headers={"Accept": "text/html"})
        assert page.status_code == 302 and page.headers["location"].startswith("http://100.64.0.7:8200/login?next=")
        assert (await far.get("/login")).status_code == 200
        wrong = await far.post("/login", data={"key": "nope", "next": "/"})
        assert wrong.status_code == 401 and COOKIE not in wrong.headers.get("set-cookie", "")
        right = await far.post("/login", data={"key": KEY, "next": "http://evil.example/steal"})
        assert right.status_code == 303 and right.headers["location"] == "/"
        assert "HttpOnly" in right.headers["set-cookie"] and KEY not in right.headers["set-cookie"]
        far.cookies.set(COOKIE, token_for(KEY))
        made = await far.post("/api/projects", json={"name": "From the iPad"}, headers={"Origin": REMOTE})
        assert made.status_code == 201 and made.json()["preview_url"].startswith("http://100.64.0.7:")
        refused = await far.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://evil.example"})
        assert refused.status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=open_app), base_url="http://127.0.0.1:8200") as near:
        listed = (await near.get("/api/projects")).json()
        assert listed[0]["preview_url"].startswith("http://127.0.0.1:")


async def test_wrong_keys_are_slowed_down(open_app):
    async with remote_client(open_app) as far:
        codes = [(await far.post("/login", data={"key": f"guess-{n}"})).status_code for n in range(10)]
        assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]
        assert (await far.post("/login", data={"key": KEY})).status_code == 429


async def test_over_https_links_cookie_and_origin_follow_the_scheme(open_app):
    """With a certificate the extra address speaks HTTPS (httpx sets the ASGI scheme from the URL)."""
    name = "https://mac.example.ts.net:8200"
    transport = httpx.ASGITransport(app=open_app, client=("100.64.0.9", 5555))
    async with httpx.AsyncClient(transport=transport, base_url=name) as far:
        page = await far.get("/", headers={"Accept": "text/html"})
        assert page.headers["location"].startswith("https://mac.example.ts.net:8200/login?next=https%3A%2F%2F")
        login = await far.get("/login")
        policy = login.headers["content-security-policy"]
        assert "frame-ancestors 'self' https://mac.example.ts.net https://mac.example.ts.net:*" in policy
        assert "x-frame-options" not in login.headers
        right = await far.post("/login", data={"key": KEY, "next": name + "/p/x"})
        assert right.status_code == 303 and right.headers["location"] == name + "/p/x"
        assert "Secure" in right.headers["set-cookie"]
        cookies = {COOKIE: token_for(KEY)}
        made = await far.post("/api/projects", json={"name": "Over HTTPS"}, cookies=cookies, headers={"Origin": name})
        assert made.status_code == 201, made.text
        assert made.json()["preview_url"].startswith("https://mac.example.ts.net:")
        wrong_scheme = await far.post("/api/projects", json={"name": "x"}, cookies=cookies,
                                      headers={"Origin": "http://mac.example.ts.net:8200"})
        assert wrong_scheme.status_code == 403


async def test_an_extra_address_can_speak_tls(tmp_path):
    """The listener used for the studio and every project port, with a throwaway self-signed certificate."""
    import datetime
    import socket

    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from uvicorn.server import ServerState

    from studio2_server.preview import listen

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "studio-test.invalid")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1)).sign(key, hashes.SHA256()))
    (tmp_path / "c.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "c.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": scope["scheme"].encode()})

    secure, plain = socket.socket(), socket.socket()
    for sock in (secure, plain):
        sock.bind(("127.0.0.1", 0))
    servers = await listen(app, [(secure, True), (plain, False)], (str(tmp_path / "c.crt"), str(tmp_path / "c.key")),
                           ServerState())
    try:
        async with httpx.AsyncClient(verify=False) as client:
            assert (await client.get(f"https://127.0.0.1:{secure.getsockname()[1]}/")).text == "https"
            assert (await client.get(f"http://127.0.0.1:{plain.getsockname()[1]}/")).text == "http"
    finally:
        for server in servers:
            server.close()


def test_no_key_is_allowed_only_on_tailscale_addresses():
    check_settings(("100.101.102.103",), None, open_tailnet=True)
    for outside in (("0.0.0.0",), ("192.168.1.20",), ("100.101.102.103", "10.0.0.5")):
        with pytest.raises(SystemExit):
            check_settings(outside, None, open_tailnet=True)


async def test_with_the_tailnet_open_its_devices_need_no_key_and_nothing_else_gets_in(settings):
    application = create_app(dataclasses.replace(settings, open_tailnet=True, names=("mac.example.ts.net",)))
    await application.state.studio.start()
    try:
        def client(address, **headers):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=application, client=(address, 5555)),
                                     base_url="https://mac.example.ts.net:8200", headers=headers)
        async with client("100.101.102.103") as device:                        # a Tailscale device
            assert (await device.get("/api/projects")).status_code == 200
            made = await device.post("/api/projects", json={"name": "From the iPad"},
                                     headers={"Origin": "https://mac.example.ts.net:8200"})
            assert made.status_code == 201 and made.json()["preview_url"].startswith("https://mac.example.ts.net:")
        async with client("203.0.113.9") as stranger:                         # any other address
            assert (await stranger.get("/api/projects")).status_code == 403
        async with client("127.0.0.1", **{"X-Forwarded-For": "100.101.102.103"}) as tunnel:   # a proxy on this machine
            assert (await tunnel.get("/api/projects")).status_code == 403
        async with client("100.101.102.103", **{"X-Forwarded-For": "203.0.113.9"}) as relayed:
            assert (await relayed.get("/api/projects")).status_code == 403
    finally:
        await application.state.studio.stop()
