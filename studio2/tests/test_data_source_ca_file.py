"""`verify-full` trusts the data source's own CA file when it has one, and the studio's public list when
not; the file is checked when saved and again at each connection; changing it needs the password
(CONTRACT.md, "Data sources")."""

from __future__ import annotations

import datetime as dt
import os
import socket
import ssl
import struct
import threading
from pathlib import Path

import pytest

from studio2_server import datasources
from studio2_server.datasources import DataError, DataSources, MemorySecrets, run_test

PASSWORD = "pw-ca-file-0123456789"


# -- certificates made here, for 127.0.0.1 and "localhost" --------------------------------------
def make_pki(folder: Path) -> dict[str, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.timezone.utc)

    def name(text):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    def pem(cert):
        return cert.public_bytes(serialization.Encoding.PEM)

    def authority(label):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (x509.CertificateBuilder().subject_name(name(label)).issuer_name(name(label))
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                             content_commitment=False, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False,
                                             encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(key, hashes.SHA256()))
        return key, cert

    ca_key, ca = authority("Studio Test CA")
    _, other = authority("Some Other CA")
    key = ec.generate_private_key(ec.SECP256R1())
    server = (x509.CertificateBuilder().subject_name(name("localhost")).issuer_name(ca.subject)
              .public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .sign(ca_key, hashes.SHA256()))
    paths = {name: folder / f"{name}.pem" for name in ("ca", "other", "server", "key")}
    paths["ca"].write_bytes(pem(ca))
    paths["other"].write_bytes(pem(other))
    paths["server"].write_bytes(pem(server))
    paths["key"].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    return paths


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    pytest.importorskip("cryptography")
    return make_pki(tmp_path_factory.mktemp("pki"))


def store(tmp_path) -> DataSources:
    return DataSources(tmp_path / "data-sources.json", MemorySecrets())


def body(**extra) -> dict:
    return {"name": "Shop", "kind": "postgres", "host": "localhost", "database": "shop", "user": "sam",
            "ssl": "verify-full", **extra}


# -- the setting --------------------------------------------------------------------------------
def test_a_ca_file_is_checked_when_saved_and_kept_as_a_full_path(tmp_path, pki, monkeypatch):
    data = store(tmp_path)
    saved = data.create(body(ca_file=str(pki["ca"])))
    assert saved["ca_file"] == str(pki["ca"])
    assert data.create(body(name="None"))["ca_file"] is None

    home = tmp_path / "home"
    (home / "certs").mkdir(parents=True)
    (home / "certs" / "bundle.pem").write_bytes(pki["ca"].read_bytes() + pki["other"].read_bytes())
    monkeypatch.setenv("HOME", str(home))
    assert data.create(body(name="Home", ca_file="~/certs/bundle.pem"))["ca_file"] == str(home / "certs" / "bundle.pem")

    (tmp_path / "notes.txt").write_text("not a certificate")
    (tmp_path / "big.pem").write_bytes(b"x" * (1024 * 1024 + 1))
    cases = {
        str(tmp_path / "missing.pem"): "no file",
        str(tmp_path): "not a file",
        str(tmp_path / "notes.txt"): "no certificate",
        str(tmp_path / "big.pem"): "1 MB",
        "certs/ca.pem": "full path",
    }
    for path, words in cases.items():
        with pytest.raises(DataError) as caught:
            data.create(body(name="Bad", ca_file=path))
        assert caught.value.code == "invalid_request" and words in caught.value.message, (path, caught.value.message)
    if os.geteuid() != 0:
        locked = tmp_path / "locked.pem"
        locked.write_bytes(pki["ca"].read_bytes())
        locked.chmod(0)
        with pytest.raises(DataError, match="cannot be read"):
            data.create(body(name="Bad", ca_file=str(locked)))
        locked.chmod(0o600)


def test_changing_the_ca_file_needs_the_password_again(tmp_path, pki):
    data = store(tmp_path)
    source = data.create(body(ca_file=str(pki["ca"]), password="pw"))
    for change in (str(pki["other"]), ""):
        with pytest.raises(DataError) as caught:
            data.update(source["id"], {"ca_file": change})
        assert caught.value.code == "password_needed"
    assert data.raw(source["id"])["ca_file"] == str(pki["ca"])
    assert data.update(source["id"], {"ca_file": "", "password": "pw"})["ca_file"] is None
    assert data.update(source["id"], {"ca_file": str(pki["ca"]), "password": "pw"})["ca_file"] == str(pki["ca"])
    assert data.update(source["id"], {"name": "Shop 2"})["ca_file"] == str(pki["ca"])   # untouched: no password
    assert data.update(source["id"], {"ca_file": str(pki["ca"])})["name"] == "Shop 2"   # the same file: no change


async def test_the_api_takes_and_returns_the_ca_file(client, pki, tmp_path):
    made = await client.post("/api/data-sources", json=body(ca_file=str(pki["ca"])))
    assert made.status_code == 201 and made.json()["ca_file"] == str(pki["ca"])
    bad = await client.post("/api/data-sources/test", json=body(ca_file=str(tmp_path / "gone.pem")))
    assert bad.status_code == 400 and "no file" in bad.json()["error"]["message"]
    cleared = await client.patch(f"/api/data-sources/{made.json()['id']}", json={"ca_file": ""})
    assert cleared.status_code == 200 and cleared.json()["ca_file"] is None


# -- what the drivers are given ----------------------------------------------------------------
def postgres_kwargs(monkeypatch, source: dict) -> dict:
    psycopg = pytest.importorskip("psycopg")
    seen: list = []

    def connect(**kwargs):
        root = kwargs.get("sslrootcert")
        seen.append({**kwargs, "root_bytes": Path(root).read_bytes() if root and Path(root).is_file() else None})
        raise psycopg.OperationalError("stop here")
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(psycopg.OperationalError):
        datasources._connect(source, "pw")
    return seen[0]


def test_postgres_verify_full_trusts_the_ca_file_or_the_public_list_never_the_home_folder(monkeypatch, pki):
    with_file = postgres_kwargs(monkeypatch, {**body(), "port": 5432, "ca_file": str(pki["ca"])})
    assert with_file["root_bytes"] == pki["ca"].read_bytes()          # a private copy of what was checked
    without = postgres_kwargs(monkeypatch, {**body(), "port": 5432})
    roots = without["sslrootcert"]
    assert roots == datasources.public_roots() and Path(roots).is_file() and ".postgresql" not in roots
    for mode in ("disable", "prefer", "require"):          # the other modes mean what they say: no CA check
        assert "sslrootcert" not in postgres_kwargs(monkeypatch, {**body(ssl=mode), "port": 5432,
                                                                 "ca_file": str(pki["ca"])})


def test_mysql_verify_full_loads_only_the_ca_file(monkeypatch, pki):
    pymysql = pytest.importorskip("pymysql")
    seen: list = []

    def connect(**kwargs):
        seen.append(kwargs)
        raise pymysql.err.OperationalError(2003, "stop here")
    monkeypatch.setattr(pymysql, "connect", connect)
    for ca_file in (str(pki["ca"]), None):
        with pytest.raises(pymysql.err.OperationalError):
            datasources._connect({**body(kind="mysql", ca_file=ca_file), "port": 3306}, "pw")
    with_file, without = (call["ssl"] for call in seen)
    names = [dict(item[0] for item in cert["subject"]).get("commonName") for cert in with_file.get_ca_certs()]
    assert names == ["Studio Test CA"] and with_file.verify_mode == ssl.CERT_REQUIRED and with_file.check_hostname
    assert "Studio Test CA" not in str(without.get_ca_certs())


def test_a_ca_file_that_went_away_fails_the_connection_in_plain_words(tmp_path, pki):
    gone = tmp_path / "ca.pem"
    gone.write_bytes(pki["ca"].read_bytes())
    data = store(tmp_path)
    source = data.raw(data.create(body(ca_file=str(gone)))["id"])
    gone.unlink()
    result = run_test({**source, "port": 1}, PASSWORD)
    assert result["ok"] is False and "no file" in result["message"] and str(gone) in result["message"]


# -- a real TLS handshake with libpq ------------------------------------------------------------
class TlsPostgres:
    """A one-connection stand-in on 127.0.0.1 that agrees to TLS with the given certificate, then asks
    for the password in clear text and records what it is sent inside the encrypted channel."""

    def __init__(self, pki):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(pki["server"], pki["key"])
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read(self, conn, n):
        out = b""
        while len(out) < n:
            chunk = conn.recv(n - len(out))
            if not chunk:
                raise OSError("closed")
            out += chunk
        return out

    def _serve(self):
        self.sock.settimeout(10)
        try:
            raw, _ = self.sock.accept()
        except OSError:
            return
        raw.settimeout(5)
        try:
            length, code = struct.unpack("!ii", self._read(raw, 8))
            while code == 80877104:                       # GSSENCRequest: not offered
                raw.sendall(b"N")
                length, code = struct.unpack("!ii", self._read(raw, 8))
            assert code == 80877103                       # SSLRequest
            raw.sendall(b"S")
            conn = self.context.wrap_socket(raw, server_side=True)
            length = struct.unpack("!i", self._read(conn, 4))[0]
            self._read(conn, length - 4)                  # the startup message
            conn.sendall(b"R" + struct.pack("!ii", 8, 3))  # AuthenticationCleartextPassword
            while chunk := conn.recv(4096):
                self.received += chunk
        except (OSError, AssertionError, ssl.SSLError):
            pass
        finally:
            raw.close()
            self.sock.close()


@pytest.mark.parametrize("trusted", ["ca", "public", "other"])
def test_libpq_checks_the_server_against_the_ca_file(pki, trusted):
    pytest.importorskip("psycopg")
    server = TlsPostgres(pki)
    ca_file = {"ca": str(pki["ca"]), "public": None, "other": str(pki["other"])}[trusted]
    source = {**body(ca_file=ca_file), "host": "localhost", "port": server.port}
    result = run_test(source, PASSWORD)
    server.thread.join(10)
    if trusted == "ca":
        # The certificate was checked against the file, so the server was trusted with the password.
        assert PASSWORD.encode() in server.received
    else:
        assert result["ok"] is False and PASSWORD.encode() not in server.received
        assert "certificate" in result["message"].lower()


# -- the file is trusted for what it holds -----------------------------------------------------
def capture_postgres(monkeypatch):
    """psycopg.connect replaced: records the CA bytes libpq would read at the moment of connecting."""
    psycopg = pytest.importorskip("psycopg")
    seen: list = []

    def connect(**kwargs):
        root = kwargs.get("sslrootcert")
        seen.append({"password": kwargs.get("password"), "root": root,
                     "ca": Path(root).read_bytes() if root and Path(root).is_file() else None})
        raise psycopg.OperationalError("stop here")
    monkeypatch.setattr(psycopg, "connect", connect)
    return seen


def test_a_changed_ca_file_does_not_get_the_saved_password(tmp_path, pki, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(pki["ca"].read_bytes())
    data = store(tmp_path)
    made = data.create(body(ca_file=str(ca), password=PASSWORD))
    assert "ca_sha256" not in made                         # kept with the source, not in the answers
    seen = capture_postgres(monkeypatch)
    run_test(data.raw(made["id"]), data.password(made["id"]))
    assert seen[-1]["password"] == PASSWORD and seen[-1]["ca"] == pki["ca"].read_bytes()

    ca.write_bytes(pki["other"].read_bytes())             # another authority, same path
    before = len(seen)
    result = run_test(data.raw(made["id"]), data.password(made["id"]))
    assert len(seen) == before                             # the driver was never called
    assert result["ok"] is False and result["code"] == "ca_changed"
    assert "has changed since the password was saved" in result["message"] and str(ca) in result["message"]
    with pytest.raises(DataError) as caught:              # the agent's and the app's path too
        datasources.run_query(data.raw(made["id"]), data.password(made["id"]), "select 1", 10, None)
    assert caught.value.code == "ca_changed"

    # Renaming, or naming the same path again, without the password does not trust the new file.
    data.update(made["id"], {"name": "Shop 2"})
    data.update(made["id"], {"ca_file": str(ca)})
    assert run_test(data.raw(made["id"]), data.password(made["id"]))["code"] == "ca_changed"
    # Typing the password again trusts the file as it is now.
    data.update(made["id"], {"password": PASSWORD})
    run_test(data.raw(made["id"]), data.password(made["id"]))
    assert len(seen) == before + 1 and seen[-1]["ca"] == pki["other"].read_bytes()
    # The copy libpq read is private and gone after connecting.
    assert seen[-1]["root"] != str(ca) and not Path(seen[-1]["root"]).exists()


def test_a_source_saved_before_the_fingerprint_needs_the_password_typed_again(tmp_path, pki, monkeypatch):
    data = store(tmp_path)
    made = data.create(body(ca_file=str(pki["ca"]), password=PASSWORD))
    items = data._read()
    for item in items:
        item.pop("ca_sha256", None)                        # as written by the code before this check
    data._write(items)
    seen = capture_postgres(monkeypatch)
    result = run_test(data.raw(made["id"]), data.password(made["id"]))
    assert result["code"] == "ca_changed" and "before the studio" in result["message"] and not seen
    data.update(made["id"], {"name": "Renamed"})          # a save without the password vouches for nothing
    assert run_test(data.raw(made["id"]), data.password(made["id"]))["code"] == "ca_changed"
    data.update(made["id"], {"password": PASSWORD})
    run_test(data.raw(made["id"]), data.password(made["id"]))
    assert seen and seen[-1]["password"] == PASSWORD


def test_no_saved_password_and_a_test_before_saving_are_not_held_to_a_fingerprint(tmp_path, pki, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(pki["ca"].read_bytes())
    data = store(tmp_path)
    made = data.create(body(ca_file=str(ca)))
    ca.write_bytes(pki["other"].read_bytes())
    seen = capture_postgres(monkeypatch)
    run_test(data.raw(made["id"]), data.password(made["id"]))          # nothing saved to protect
    run_test({**DataSources.normalise(body(ca_file=str(ca)))}, "typed")  # the unsaved test: its own password
    assert [call["ca"] for call in seen] == [pki["other"].read_bytes()] * 2


def test_mysql_gets_the_checked_bytes_and_a_changed_file_stops_it(tmp_path, pki, monkeypatch):
    pymysql = pytest.importorskip("pymysql")
    ca = tmp_path / "ca.pem"
    ca.write_bytes(pki["ca"].read_bytes())
    data = store(tmp_path)
    made = data.create(body(kind="mysql", ca_file=str(ca), password=PASSWORD))
    seen: list = []

    def connect(**kwargs):
        seen.append(kwargs)
        raise pymysql.err.OperationalError(2003, "stop here")
    monkeypatch.setattr(pymysql, "connect", connect)
    run_test(data.raw(made["id"]), data.password(made["id"]))
    assert len(seen) == 1
    ca.write_bytes(pki["other"].read_bytes())
    assert run_test(data.raw(made["id"]), data.password(made["id"]))["code"] == "ca_changed" and len(seen) == 1


async def test_the_test_route_says_ca_changed_and_keeps_it_in_last_test(client, pki, tmp_path):
    ca = tmp_path / "route-ca.pem"
    ca.write_bytes(pki["ca"].read_bytes())
    made = (await client.post("/api/data-sources", json={**body(ca_file=str(ca)), "port": 1,
                                                         "password": PASSWORD})).json()
    ca.write_bytes(pki["other"].read_bytes())
    tested = (await client.post(f"/api/data-sources/{made['id']}/test")).json()
    assert tested["ok"] is False and tested["code"] == "ca_changed"
    listed = [s for s in (await client.get("/api/data-sources")).json() if s["id"] == made["id"]][0]
    assert listed["last_test"]["code"] == "ca_changed" and "ca_sha256" not in listed
