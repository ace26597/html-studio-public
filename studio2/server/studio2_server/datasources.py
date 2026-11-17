"""The person's own databases: saved connections, read-only queries (CONTRACT.md, "Data sources").

Passwords live in the operating system's keychain (through `keyring`), or in the studio's
owner-only secrets file on a machine without a keychain (keystore.py); never in the list this
module writes and never in anything it returns. Queries run inside a read-only transaction,
so the database itself refuses writes; the statement check here only gives a clearer error.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import decimal
import json
import re
import secrets
import time
import uuid
from pathlib import Path

from .config import now_iso
from .store import write_json_atomic

KINDS = {
    "postgres": {"label": "PostgreSQL", "default_port": 5432, "driver": "psycopg", "ssl": "prefer"},
    "redshift": {"label": "Amazon Redshift", "default_port": 5439, "driver": "psycopg", "ssl": "require"},
    "mysql": {"label": "MySQL / MariaDB", "default_port": 3306, "driver": "pymysql", "ssl": "prefer"},
}
SSL_MODES = ("disable", "prefer", "require", "verify-full")
FIELDS = ("name", "kind", "host", "port", "database", "user", "ssl")
READ_WORDS = {"select", "with", "show", "explain", "values", "table", "describe", "desc"}
WRAPPABLE = {"select", "with", "values", "table"}
INLINE_DEFAULT, INLINE_MAX, FILE_MAX_ROWS, FILE_MAX_BYTES = 1_000, 10_000, 500_000, 200 * 1024 * 1024
STATEMENT_TIMEOUT_MS, CONNECT_TIMEOUT = 60_000, 10
SERVICE = "studio2-data-source"


class DataError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# -- secrets ----------------------------------------------------------------------------------
class KeychainSecrets:
    """Passwords in the studio's secret store (keystore.py): the system keychain, or the
    owner-only file on a machine that has no keychain."""

    def __init__(self, store=None):
        if store is None:
            from .keystore import KeyringStore
            store = KeyringStore()
        self.store = store

    def get(self, source_id: str) -> str | None:
        return self.store.get(SERVICE, source_id)

    def set(self, source_id: str, password: str) -> None:
        self.store.set(SERVICE, source_id, password)

    def delete(self, source_id: str) -> None:
        self.store.delete(SERVICE, source_id)


class MemorySecrets:
    """For tests and the fake studio: nothing touches the real keychain."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, source_id: str) -> str | None:
        return self.values.get(source_id)

    def set(self, source_id: str, password: str) -> None:
        self.values[source_id] = password

    def delete(self, source_id: str) -> None:
        self.values.pop(source_id, None)


# -- statements -------------------------------------------------------------------------------
def split_statement(sql: str) -> tuple[str, str]:
    """→ (the single statement without trailing semicolon or comments at the edges, its first word).
    Raises DataError when it is empty, more than one statement, or not a read."""
    out, i, n, depth_quote = [], 0, len(sql), None
    while i < n:
        ch, pair = sql[i], sql[i:i + 2]
        if depth_quote:
            out.append(ch)
            if ch == depth_quote:
                if sql[i + 1:i + 2] == depth_quote:  # doubled quote inside a literal
                    out.append(depth_quote)
                    i += 1
                else:
                    depth_quote = None
        elif pair == "--":
            end = sql.find("\n", i)
            i = n if end < 0 else end
            continue
        elif pair == "/*":
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        elif ch in ("'", '"', "`"):
            depth_quote = ch
            out.append(ch)
        elif ch == ";":
            if sql[i + 1:].strip() and split_or_empty(sql[i + 1:]):
                raise DataError(400, "query_failed", "Send one statement at a time.")
            break
        else:
            out.append(ch)
        i += 1
    statement = "".join(out).strip()
    if not statement:
        raise DataError(400, "query_failed", "The query is empty.")
    word = re.match(r"\(*\s*([A-Za-z]+)", statement)
    first = word.group(1).lower() if word else ""
    if first not in READ_WORDS:
        raise DataError(400, "query_failed",
                        "Only reading is allowed here (SELECT, WITH, SHOW, EXPLAIN). "
                        "The connection is read-only.")
    return statement, first


def split_or_empty(rest: str) -> bool:
    """True when `rest` contains anything besides comments and semicolons."""
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", rest, flags=re.S)
    return bool(cleaned.replace(";", "").strip())


def to_json(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        number = float(value)
        return number if decimal.Decimal(repr(number)) == value else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    return str(value)


# -- drivers ----------------------------------------------------------------------------------
def driver_available(kind: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(KINDS[kind]["driver"]) is not None


def _connect(source: dict, password: str | None):
    kind = source["kind"]
    if not driver_available(kind):
        raise DataError(503, "driver_missing", f"The {KINDS[kind]['label']} driver is not installed in the studio.")
    if kind in ("postgres", "redshift"):
        import psycopg
        extra = {"client_encoding": "utf8"} if kind == "redshift" else {}
        if source["ssl"] != "verify-full" and psycopg.pq.version() >= 160000:
            # A server nobody checked may be anything listening on that port: it must not get the
            # password in clear text (libpq refuses AuthenticationCleartextPassword before sending).
            extra["require_auth"] = "!password"
        try:
            conn = psycopg.connect(
                host=source["host"], port=source["port"], dbname=source["database"],
                user=source["user"], password=password or None, sslmode=source["ssl"],
                connect_timeout=CONNECT_TIMEOUT, application_name="studio", **extra)
        except psycopg.OperationalError as exc:
            # libpq: 'authentication method requirement "!password" failed: server requested a cleartext password'
            if "require_auth" in extra and '"!password"' in str(exc):
                raise DataError(400, "cleartext_refused", CLEARTEXT_REFUSED) from None
            raise
        conn.prepare_threshold = None
        conn.read_only = True  # every transaction starts as BEGIN READ ONLY
        return conn
    return _connect_mysql(source, password)


CLEARTEXT_REFUSED = ("The server asked for the password in clear text, and nothing checked that it is the "
                     "database you saved (ssl is not verify-full), so the password was not sent. Use ssl "
                     "verify-full, or a server that does not ask for the password in clear text.")


class _RefuseCleartext:
    """PyMySQL authentication methods that send the password itself (mysql_clear_password, and the
    dialog plugin's "Password:" prompt): refused unless the server was checked (verify-full).
    PyMySQL asks a mapped handler's `authenticate` first, before it would write anything."""

    def __init__(self, _conn):
        pass

    def authenticate(self, _packet):
        raise DataError(400, "cleartext_refused", CLEARTEXT_REFUSED)


def _connect_mysql(source: dict, password: str | None):
    """The `ssl` modes mean what they mean to PostgreSQL (CONTRACT.md, "Data sources"). PyMySQL has
    no such modes, so they are built here: always an explicit context or an explicit "no TLS"."""
    import ssl as ssl_module

    import pymysql
    mode = source["ssl"]
    common = dict(host=source["host"], port=source["port"], database=source["database"], user=source["user"],
                  password=password or "", connect_timeout=CONNECT_TIMEOUT,
                  read_timeout=STATEMENT_TIMEOUT_MS // 1000 + 5, autocommit=False)
    if mode != "verify-full":
        common["auth_plugin_map"] = {"mysql_clear_password": _RefuseCleartext, "dialog": _RefuseCleartext}
    if mode == "disable":
        return pymysql.connect(**common, ssl_disabled=True)
    # verify-full: the trusted authorities and the host name, both checked. A dict such as
    # {"check_hostname": True} is not enough: with no CA file PyMySQL then checks neither.
    context = ssl_module.create_default_context()
    if mode != "verify-full":
        context.check_hostname = False          # prefer and require encrypt but do not check who answers
        context.verify_mode = ssl_module.CERT_NONE
    try:
        conn = pymysql.connect(**common, ssl=context)
    except pymysql.err.OperationalError as exc:
        # Plain is acceptable for prefer only when the server offers no TLS at all; any other
        # failure (wrong password, host unreachable, a failed handshake) is passed on as it is.
        if mode != "prefer" or not _no_tls_offered(exc):
            raise
        return pymysql.connect(**common, ssl_disabled=True)
    # Older PyMySQL goes ahead in plain when the server has no TLS, even when given a context.
    if mode != "prefer" and not isinstance(getattr(conn, "_sock", None), ssl_module.SSLSocket):
        _close(conn)
        raise DataError(400, "tls_unavailable", f"The server did not agree to an encrypted connection, and this "
                                                f"source requires one (ssl: {mode}). The connection was closed unused.")
    return conn


def _no_tls_offered(exc: Exception) -> bool:
    """PyMySQL's answer when a context was given and the server does not support TLS."""
    code = exc.args[0] if exc.args else None
    return code == 2026 and "support" in str(exc).lower()   # CR_SSL_CONNECTION_ERROR


def _begin(conn, kind: str) -> None:
    with conn.cursor() as cur:
        if kind == "mysql":
            try:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME={STATEMENT_TIMEOUT_MS}")
            except Exception:
                pass  # MariaDB names it differently; the read timeout still applies
            cur.execute("START TRANSACTION READ ONLY")
        else:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")


def _clean_error(exc: Exception, password: str | None) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    if password:
        text = text.replace(password, "•••")
    return text[:600]


def run_test(source: dict, password: str | None) -> dict:
    try:
        conn = _connect(source, password)
    except DataError as exc:
        return {"ok": False, "message": exc.message}
    except Exception as exc:
        return {"ok": False, "message": _clean_error(exc, password)}
    try:
        _begin(conn, source["kind"])
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = str(cur.fetchone()[0])[:200]
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'mysql', "
                        "'performance_schema', 'sys', 'pg_internal')")
            tables = int(cur.fetchone()[0])
        return {"ok": True, "message": f"Connected. {tables} table{'s' if tables != 1 else ''} visible.",
                "server_version": version, "tables": tables}
    except Exception as exc:
        return {"ok": False, "message": _clean_error(exc, password)}
    finally:
        _close(conn)


def _close(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def _type_name(kind: str, conn, description) -> str:
    try:
        if kind == "mysql":
            import pymysql.constants.FIELD_TYPE as field_type
            names = {value: name.lower() for name, value in vars(field_type).items() if name.isupper()}
            return names.get(description[1], str(description[1]))
        info = conn.adapters.types.get(description.type_code)
        return info.name if info else str(description.type_code)
    except Exception:
        return "unknown"


def run_query(source: dict, password: str | None, sql: str, limit: int, save_to: Path | None) -> dict:
    statement, first = split_statement(sql)
    cap = max(1, min(limit, FILE_MAX_ROWS if save_to else INLINE_MAX))
    text = f"SELECT * FROM (\n{statement}\n) AS studio_query LIMIT {cap + 1}" if first in WRAPPABLE else statement
    started = time.monotonic()
    try:
        conn = _connect(source, password)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(400, "query_failed", f"Could not connect: {_clean_error(exc, password)}") from None
    try:
        _begin(conn, source["kind"])
        with conn.cursor() as cur:
            cur.execute(text)
            if cur.description is None:
                raise DataError(400, "query_failed", "That statement returned no rows to read.")
            columns = [{"name": d[0], "type": _type_name(source["kind"], conn, d)} for d in cur.description]
            rows = cur.fetchmany(cap + 1)
        truncated = len(rows) > cap
        rows = [[to_json(value) for value in row] for row in rows[:cap]]
    except DataError:
        raise
    except Exception as exc:
        raise DataError(400, "query_failed", _clean_error(exc, password)) from None
    finally:
        _close(conn)
    answer = {"columns": columns, "row_count": len(rows), "truncated": truncated,
              "elapsed_ms": int((time.monotonic() - started) * 1000)}
    if save_to is None:
        return {**answer, "rows": rows}
    save_to.parent.mkdir(parents=True, exist_ok=True)
    names = [column["name"] for column in columns]
    if save_to.suffix.lower() == ".json":
        payload = json.dumps([dict(zip(names, row)) for row in rows], ensure_ascii=False)
        if len(payload.encode()) > FILE_MAX_BYTES:
            raise DataError(400, "query_failed", "The result is larger than 200 MB; select fewer rows or columns.")
        save_to.write_text(payload, "utf-8")
    else:
        with save_to.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            for row in rows:
                writer.writerow(["" if v is None else json.dumps(v) if isinstance(v, (list, dict)) else v for v in row])
                if handle.tell() > FILE_MAX_BYTES:
                    answer["truncated"] = True
                    break
    return answer


def run_schema(source: dict, password: str | None, table: str | None) -> dict:
    skip = "('information_schema', 'pg_catalog', 'mysql', 'performance_schema', 'sys', 'pg_internal')"
    try:
        conn = _connect(source, password)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(400, "query_failed", f"Could not connect: {_clean_error(exc, password)}") from None
    try:
        _begin(conn, source["kind"])
        with conn.cursor() as cur:
            if not table:
                cur.execute(
                    "SELECT t.table_schema, t.table_name, t.table_type, count(c.column_name) "
                    "FROM information_schema.tables t LEFT JOIN information_schema.columns c "
                    "ON c.table_schema = t.table_schema AND c.table_name = t.table_name "
                    f"WHERE t.table_schema NOT IN {skip} "
                    "GROUP BY t.table_schema, t.table_name, t.table_type "
                    "ORDER BY t.table_schema, t.table_name LIMIT 2000")
                return {"tables": [{"schema": s, "name": n, "type": "view" if "VIEW" in str(t).upper() else "table",
                                    "columns": int(c)} for s, n, t, c in cur.fetchall()]}
            schema, _, name = table.rpartition(".")
            if not re.fullmatch(r"[\w$ -]+", name) or (schema and not re.fullmatch(r"[\w$ -]+", schema)):
                raise DataError(400, "query_failed", "That is not a valid table name.")
            where = "table_name = %s" + (" AND table_schema = %s" if schema else f" AND table_schema NOT IN {skip}")
            cur.execute("SELECT column_name, data_type, is_nullable, table_schema FROM information_schema.columns "
                        f"WHERE {where} ORDER BY table_schema, ordinal_position", (name, schema) if schema else (name,))
            found = cur.fetchall()
            if not found:
                raise DataError(400, "query_failed", f"There is no table named {table} that this user can see.")
            schema = schema or found[0][3]
            estimate = None
            if source["kind"] == "postgres":
                cur.execute("SELECT reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = %s AND c.relname = %s", (schema, name))
                row = cur.fetchone()
                estimate = int(row[0]) if row and row[0] is not None and row[0] >= 0 else None
            elif source["kind"] == "mysql":
                cur.execute("SELECT table_rows FROM information_schema.tables WHERE table_schema = %s "
                            "AND table_name = %s", (schema, name))
                row = cur.fetchone()
                estimate = int(row[0]) if row and row[0] is not None else None
            return {"table": f"{schema}.{name}", "row_estimate": estimate,
                    "columns": [{"name": c, "type": t, "nullable": str(nullable).upper() == "YES"}
                                for c, t, nullable, s in found if s == schema]}
    except DataError:
        raise
    except Exception as exc:
        raise DataError(400, "query_failed", _clean_error(exc, password)) from None
    finally:
        _close(conn)


# -- the saved list ---------------------------------------------------------------------------
class DataSources:
    def __init__(self, path: Path, secrets_store):
        self.path, self.secrets = path, secrets_store

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("id")]

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, items)  # type: ignore[arg-type]

    def view(self, item: dict) -> dict:
        try:
            has_password = self.secrets.get(item["id"]) is not None
        except Exception:
            has_password = False
        return {**{key: item.get(key) for key in ("id", *FIELDS, "created_at", "last_test")},
                "has_password": has_password}

    def list(self) -> list[dict]:
        return [self.view(item) for item in sorted(self._read(), key=lambda i: i["name"].lower())]

    def raw(self, source_id: str) -> dict:
        for item in self._read():
            if item["id"] == source_id:
                return item
        raise DataError(404, "unknown_data_source", "That data source does not exist.")

    def by_name(self, name: str, allowed: list[str]) -> dict:
        for item in self._read():
            if item["id"] in allowed and (item["name"].lower() == name.lower() or item["id"] == name):
                return item
        raise DataError(404, "unknown_data_source", f"No data source named “{name}” is attached to this project.")

    def password(self, source_id: str) -> str | None:
        return self.secrets.get(source_id)

    @staticmethod
    def normalise(fields: dict, base: dict | None = None) -> dict:
        """Validated connection fields (no password). Raises DataError naming the bad field."""
        merged = {**(base or {}), **{k: v for k, v in fields.items() if k in FIELDS and v is not None}}
        bad = lambda message: DataError(400, "invalid_request", message)  # noqa: E731
        kind = merged.get("kind")
        if kind not in KINDS:
            raise bad("Choose a database type: " + ", ".join(KINDS) + ".")
        name = " ".join(str(merged.get("name") or "").split())
        if not 1 <= len(name) <= 60:
            raise bad("Give the data source a name of 1 to 60 characters.")
        host = str(merged.get("host") or "").strip()
        if not host or re.search(r"[\s/@]", host):
            raise bad("Host must be a host name or address, without a scheme or path.")
        database, user = str(merged.get("database") or "").strip(), str(merged.get("user") or "").strip()
        if not database:
            raise bad("Enter the database name.")
        if not user:
            raise bad("Enter the user name.")
        try:
            port = int(merged.get("port") or KINDS[kind]["default_port"])
        except (TypeError, ValueError):
            raise bad("Port must be a number.") from None
        if not 1 <= port <= 65535:
            raise bad("Port must be between 1 and 65535.")
        ssl = merged.get("ssl") or KINDS[kind]["ssl"]
        if ssl not in SSL_MODES:
            raise bad("ssl must be one of: " + ", ".join(SSL_MODES) + ".")
        return {"name": name, "kind": kind, "host": host, "port": port, "database": database, "user": user, "ssl": ssl}

    def create(self, fields: dict) -> dict:
        clean = self.normalise(fields)
        items = self._read()
        if any(item["name"].lower() == clean["name"].lower() for item in items):
            raise DataError(409, "name_taken", "Another data source already has that name.")
        item = {"id": "ds_" + secrets.token_hex(4), **clean, "created_at": now_iso(), "last_test": None}
        if fields.get("password"):
            self.secrets.set(item["id"], str(fields["password"]))
        self._write([*items, item])
        return self.view(item)

    def update(self, source_id: str, fields: dict) -> dict:
        items = self._read()
        current = self.raw(source_id)
        clean = self.normalise(fields, current)
        if any(i["id"] != source_id and i["name"].lower() == clean["name"].lower() for i in items):
            raise DataError(409, "name_taken", "Another data source already has that name.")
        typed = "password" in fields and fields["password"] is not None
        moved = any(clean[k] != current.get(k) for k in ("kind", "host", "port", "ssl"))
        if moved and not typed and self.secrets.get(source_id) is not None:
            # The stored password goes only to the server it was saved for: anything on this
            # computer can call this route, and a database server picks how a password is sent.
            raise DataError(400, "password_needed", "This data source now points somewhere else: type its "
                                                    "password again. A saved one is only sent where it was saved for.")
        if typed:
            if fields["password"] == "":
                self.secrets.delete(source_id)
            else:
                self.secrets.set(source_id, str(fields["password"]))
        connection_changed = any(clean[k] != current.get(k) for k in FIELDS if k != "name") or "password" in fields
        updated = {**current, **clean, "last_test": None if connection_changed else current.get("last_test")}
        self._write([updated if i["id"] == source_id else i for i in items])
        return self.view(updated)

    def record_test(self, source_id: str, result: dict) -> None:
        items = self._read()
        for item in items:
            if item["id"] == source_id:
                item["last_test"] = {"ok": bool(result["ok"]), "at": now_iso(), "message": result["message"]}
        self._write(items)

    def delete(self, source_id: str) -> None:
        self.raw(source_id)
        self._write([item for item in self._read() if item["id"] != source_id])
        try:
            self.secrets.delete(source_id)
        except Exception:
            pass
