"""DNS rebinding: the studio answers only to names it knows (CONTRACT.md, "Names the studio answers to")."""

from __future__ import annotations

import dataclasses
import datetime

import httpx
import pytest

from studio2_server import access
from studio2_server.access import AccessGuard, known_names, name_allowed
from studio2_server.api import create_app
from studio2_server.config import Settings

TAILNET_PEER = ("100.101.102.103", 5555)


def test_addresses_and_localhost_always_pass():
    for host in ("100.101.102.103:8200", "127.0.0.1", "192.168.1.20:8201", "[::1]:8200", "[fd7a:115c::5]",
                 "localhost:8200", "LOCALHOST", "localhost.", "app.localhost:8201"):
        assert name_allowed(host, frozenset()), host
    for host in ("evil.example:8200", "studio.example", "localhost.evil.example", "2130706433", "evil.localhost.com"):
        assert not name_allowed(host, frozenset()), host


def test_known_names_ignore_case_port_and_final_dot_and_wildcards_cover_one_part():
    names = frozenset({"studio.example", "*.tail.example"})
    for host in ("studio.example", "STUDIO.Example:8200", "studio.example.:8201", "mac.tail.example:8200"):
        assert name_allowed(host, names), host
    for host in ("other.example", "tail.example", "a.mac.tail.example", "studio.example.evil.example"):
        assert not name_allowed(host, names), host


def test_the_machine_s_own_names_short_and_full(monkeypatch, settings, tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n# 10.0.0.9 box.commented.example box\n"
                     "127.0.1.1\tBox.lan.example box   # the installer's line\n10.0.0.7 other.example other\n")
    monkeypatch.setattr(access, "HOSTS_FILE", hosts)
    monkeypatch.setattr(access.socket, "gethostname", lambda: "Box")
    monkeypatch.setattr(access, "_bonjour_name", lambda: None)
    names = known_names(settings)
    for host in ("box", "BOX:8200", "box.local", "box.lan.example"):
        assert name_allowed(host, names), host
    for host in ("box.commented.example", "other.example", "other"):
        assert not name_allowed(host, names), host
    # The host name that already is the full name.
    monkeypatch.setattr(access.socket, "gethostname", lambda: "box.corp.example")
    assert name_allowed("box.corp.example", known_names(settings))


def test_a_name_from_dns_is_never_taken(monkeypatch, settings, tmp_path):
    """Whoever answers the lookup picks the whole name, the first part included."""
    monkeypatch.setattr(access, "HOSTS_FILE", tmp_path / "no-hosts-file")
    monkeypatch.setattr(access.socket, "gethostname", lambda: "Mac")
    monkeypatch.setattr(access, "_bonjour_name", lambda: None)
    for answer in ("mac.evil.example", "mac.lan", "154.1.168.192.in-addr.arpa", "someone-else.example"):
        monkeypatch.setattr(access.socket, "getfqdn", lambda *a, answer=answer: answer)
        monkeypatch.setattr(access.socket, "gethostbyaddr", lambda *a, answer=answer: (answer, [], []))
        assert not name_allowed(answer, known_names(settings)), answer
    assert name_allowed("mac.local", known_names(settings))


def test_a_bracketed_address_must_be_the_whole_host():
    for host in ("[::1].evil.example", "[::1].evil.example:8200", "[::1]x", "[::1]:80x", "[::1"):
        assert not name_allowed(host, frozenset()), host
        assert access.host_name(host) not in access.LOCAL_HOSTS, host
    for host in ("[::1]", "[::1]:8200", "[fd7a:115c::5]:8201"):
        assert name_allowed(host, frozenset()), host
    assert access.host_name("[::1]:8200") == "[::1]"


def test_names_from_the_setting_the_listeners_and_the_certificate(monkeypatch, tmp_path, settings):
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "ignored-common.example")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("mac.tail.example"),
                                                        x509.IPAddress(__import__("ipaddress").ip_address("100.64.0.7"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.crt").write_bytes(cert.public_bytes(Encoding.PEM))
    monkeypatch.setenv("STUDIO2_NAMES", " Proxy.Example , ,other.example")
    monkeypatch.setenv("STUDIO2_CONFIG", str(tmp_path / "none.json"))
    from_env = Settings.from_env()
    assert from_env.names == ("Proxy.Example", "other.example")
    names = known_names(dataclasses.replace(settings, names=from_env.names, hosts=("100.64.0.7", "box.lan.example"),
                                            tls=(str(tmp_path / "c.crt"), str(tmp_path / "c.key"))))
    for host in ("proxy.example", "other.example:8200", "box.lan.example", "mac.tail.example:8200"):
        assert name_allowed(host, names), host
    assert not name_allowed("ignored-common.example", names)   # a certificate with alternative names: those only


async def _call(guard, scope):
    sent = []

    async def receive():
        return {"type": "websocket.connect"} if scope["type"] == "websocket" else {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)
    await guard(scope, receive, send)
    return sent


async def test_a_rebound_name_is_refused_on_the_open_tailnet_and_known_forms_pass(settings):
    """The hole: a tailnet device's browser on a page whose name now points at the studio."""
    application = create_app(dataclasses.replace(settings, open_tailnet=True, names=("mac.example.ts.net",)))
    await application.state.studio.start()
    try:
        def client(base, **headers):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=application, client=TAILNET_PEER),
                                     base_url=base, headers=headers)
        async with client("http://attacker.example:8200", Accept="text/html") as rebound:
            page = await rebound.get("/")
            assert page.status_code == 421 and "text/html" in page.headers["content-type"]
            assert "attacker.example" in page.text and "STUDIO2_NAMES" in page.text
            made = await rebound.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://attacker.example:8200",
                                                                                    "Accept": "application/json"})
            assert made.status_code == 421 and made.json()["error"]["code"] == "unknown_name"
        for base in ("https://mac.example.ts.net:8200", "https://MAC.example.ts.net.:8200", "http://100.101.102.103:8200"):
            async with client(base) as known:
                assert (await known.get("/api/projects")).status_code == 200, base
    finally:
        await application.state.studio.stop()
    assert not application.state.studio.store.list()


async def test_the_name_check_comes_before_local_and_covers_project_ports_and_sockets(settings):
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["type"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    names = frozenset({"mac.example.ts.net"})
    page_port = AccessGuard(app, None, 8200, own_login=False, open_tailnet=True, names=names)
    local = {"client": ("127.0.0.1", 1), "scheme": "http", "path": "/", "query_string": b"", "method": "GET"}
    sent = await _call(page_port, {**local, "type": "http", "headers": [(b"host", b"evil.example:8201")]})
    assert sent[0]["status"] == 421 and b"evil.example" in sent[1]["body"]
    sent = await _call(page_port, {**local, "type": "websocket", "headers": [(b"host", b"evil.example:8201"),
                                                                            (b"origin", b"http://evil.example:8201")]})
    assert sent == [{"type": "websocket.close", "code": 4421}]
    sent = await _call(page_port, {**local, "type": "http", "headers": []})
    assert sent[0]["status"] == 400
    assert reached == []
    await _call(page_port, {**local, "type": "http", "headers": [(b"host", b"127.0.0.1:8201")]})
    await _call(page_port, {**local, "type": "websocket", "client": TAILNET_PEER,
                            "headers": [(b"host", b"mac.example.ts.net:8201")]})
    assert reached == ["http", "websocket"]


async def test_the_studio_s_own_project_ports_carry_the_names(settings):
    application = create_app(dataclasses.replace(settings, names=("studio.example",)))
    studio = application.state.studio
    assert "studio.example" in studio.names
    guard = studio.previews.guard(lambda *a: None)
    assert isinstance(guard, AccessGuard) and guard.names == studio.names
