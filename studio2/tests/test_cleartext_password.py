"""A server that asks for the password in clear text does not get it, unless the source checks who
it is talking to (`verify-full`). "Same host and port" is no identity on loopback: anything on this
computer can listen on a free database port (CONTRACT.md, "Data sources")."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from studio2_server.datasources import run_test

PASSWORD = "pw-cleartext-0123456789"


class Listener:
    """A one-connection stand-in database on 127.0.0.1 that records every byte it is sent."""

    def __init__(self, talk):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self._serve, args=(talk,), daemon=True)
        self.thread.start()

    def _serve(self, talk):
        self.sock.settimeout(10)
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        conn.settimeout(5)
        try:
            talk(conn, self)
        except OSError:
            pass
        finally:
            conn.close()
            self.sock.close()

    def read(self, conn, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = conn.recv(n - len(out))
            if not chunk:
                raise OSError("closed")
            out += chunk
        self.received += out
        return out

    def drain(self, conn) -> None:
        try:
            while chunk := conn.recv(4096):
                self.received += chunk
        except OSError:
            pass


def postgres_asking_for_cleartext(conn, me: Listener) -> None:
    while True:
        length, code = struct.unpack("!ii", me.read(conn, 8))
        if code in (80877103, 80877104):        # SSLRequest, GSSENCRequest: not offered
            conn.sendall(b"N")
            continue
        me.read(conn, length - 8)               # the rest of the startup message
        break
    conn.sendall(b"R" + struct.pack("!ii", 8, 3))   # AuthenticationCleartextPassword
    me.drain(conn)


def mysql_switching_to_cleartext(conn, me: Listener) -> None:
    caps = 1 | 8 | 512 | 32768 | (1 << 19)      # long password, with db, 4.1, secure connection, plugin auth
    salt = b"abcdefgh" + b"ijklmnopqrst"
    body = (b"\x0a" + b"8.0.0-standin\0" + struct.pack("<I", 7) + salt[:8] + b"\0"
            + struct.pack("<H", caps & 0xFFFF) + b"\x21" + struct.pack("<H", 2) + struct.pack("<H", caps >> 16)
            + bytes([21]) + b"\0" * 10 + salt[8:] + b"\0" + b"mysql_native_password\0")
    conn.sendall(struct.pack("<I", len(body))[:3] + b"\x00" + body)
    header = me.read(conn, 4)
    me.read(conn, int.from_bytes(header[:3], "little"))             # the client's handshake response
    switch = b"\xfe" + b"mysql_clear_password\0"
    conn.sendall(struct.pack("<I", len(switch))[:3] + bytes([header[3] + 1]) + switch)
    me.drain(conn)


def source(kind: str, port: int, ssl: str) -> dict:
    return {"kind": kind, "host": "127.0.0.1", "port": port, "database": "d", "user": "u", "ssl": ssl}


@pytest.mark.parametrize("kind", ["postgres", "redshift"])
def test_a_postgres_listener_asking_for_the_password_in_clear_does_not_get_it(kind):
    pytest.importorskip("psycopg")
    me = Listener(postgres_asking_for_cleartext)
    result = run_test(source(kind, me.port, "prefer"), PASSWORD)
    me.thread.join(10)
    assert result["ok"] is False
    assert PASSWORD.encode() not in me.received
    assert "clear text" in result["message"] and "verify-full" in result["message"]


def test_verify_full_and_an_old_libpq_leave_the_option_out(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from studio2_server import datasources
    seen: list = []

    def connect(**kwargs):
        seen.append(kwargs)
        raise psycopg.OperationalError("stop here")
    monkeypatch.setattr(psycopg, "connect", connect)
    for ssl in ("verify-full", "require"):
        with pytest.raises(psycopg.OperationalError):
            datasources._connect(source("postgres", 5432, ssl), "pw")
    assert "require_auth" not in seen[0] and seen[1]["require_auth"] == "!password"
    monkeypatch.setattr(psycopg.pq, "version", lambda: 150004)    # libpq 15 has no require_auth
    with pytest.raises(psycopg.OperationalError):
        datasources._connect(source("postgres", 5432, "require"), "pw")
    assert "require_auth" not in seen[2]


def test_a_mysql_listener_switching_to_clear_text_does_not_get_the_password():
    pytest.importorskip("pymysql")
    me = Listener(mysql_switching_to_cleartext)
    result = run_test(source("mysql", me.port, "disable"), PASSWORD)
    me.thread.join(10)
    assert result["ok"] is False
    assert PASSWORD.encode() not in me.received
    assert "clear text" in result["message"]


# -- MySQL: nothing in clear text unless the connection is encrypted and checked, before sending --
def test_the_pymysql_floor_is_the_version_that_stops_before_logging_in_without_tls():
    import re
    import tomllib
    from pathlib import Path
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    spec = next(d for d in project["project"]["dependencies"] if d.lower().startswith("pymysql"))
    floor = tuple(int(n) for n in re.search(r">=\s*([\d.]+)", spec).group(1).split("."))
    assert floor >= (1, 2), spec       # 1.1.x went ahead in plain when given a context and offered no TLS


@pytest.mark.parametrize("mode", ["require", "verify-full"])
def test_an_older_pymysql_is_refused_before_it_connects(monkeypatch, mode):
    pymysql = pytest.importorskip("pymysql")
    from studio2_server import datasources
    called: list = []
    monkeypatch.setattr(pymysql, "VERSION", (1, 1, 2, "final"))
    monkeypatch.setattr(pymysql, "connect", lambda **kw: called.append(kw))
    with pytest.raises(datasources.DataError) as caught:
        datasources._connect(source("mysql", 3306, mode), PASSWORD)
    assert caught.value.code == "tls_unavailable" and "1.2" in caught.value.message and not called
    datasources._connect(source("mysql", 3306, "prefer"), PASSWORD)     # prefer never promised TLS
    assert len(called) == 1


def test_under_verify_full_a_clear_text_method_is_answered_only_on_a_checked_connection(monkeypatch):
    pymysql = pytest.importorskip("pymysql")
    from studio2_server import datasources
    seen: list = []
    def connect(**kwargs):
        seen.append(kwargs)
        raise pymysql.err.OperationalError(2003, "stop here")
    monkeypatch.setattr(pymysql, "connect", connect)
    with pytest.raises(pymysql.err.OperationalError):
        datasources._connect(source("mysql", 3306, "verify-full"), PASSWORD)
    handlers = seen[0].get("auth_plugin_map") or {}
    assert set(handlers) >= {"mysql_clear_password", "dialog"}

    class Plain:                     # what PyMySQL looks like when the TLS did not happen
        _secure = False
        password = PASSWORD.encode()

        def __init__(self):
            self.written: list = []
            self._sock = socket.socket()

        def write_packet(self, data):
            self.written.append(data)

        def _process_auth(self, *args, **kwargs):
            self.written.append(b"default answer")
    conn = Plain()
    try:
        for name in ("mysql_clear_password", "dialog"):
            with pytest.raises(datasources.DataError) as caught:
                handlers[name](conn).authenticate(None)
            assert caught.value.code == "cleartext_refused" and not conn.written
    finally:
        conn._sock.close()
