"""One `query_files` call, in a process of its own (CONTRACT.md, "`query_files` on big files").

Run by filesql.py as `python -I filesql_worker.py` with the project folder as working folder. It
reads one JSON job on standard input and writes one JSON answer on standard output. Only the
standard library and duckdb are imported here: `-I` keeps the project folder off the import path,
and this file must stay importable on its own.

DuckDB is shut in before the agent's statement runs: only the project folder and the call's own
temporary folder can be opened, no extension is installed or loaded, and the configuration is
locked so no later statement can open it again.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import os
import sys
import threading
import time
import uuid

SAMPLE_ROWS = 1_000_000
FULL_SHARE = 0.6        # a full profile must end within this share of the time limit
TEXT_CAP = 200          # characters of a min, max or sample value


def to_json(value):
    """A DuckDB value → something JSON can carry, the same way for every studio query."""
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


def _short(value):
    value = to_json(value)
    return value[:TEXT_CAP] + "…" if isinstance(value, str) and len(value) > TEXT_CAP else value


def connect(job: dict):
    """→ a DuckDB connection that can open only the project folder (the working folder) and the
    call's temporary folder."""
    import duckdb
    here, scratch = os.path.realpath("."), os.path.realpath(job["scratch"])
    spill = os.path.join(scratch, "spill")
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect(":memory:", config={
        "memory_limit": job["memory"], "threads": int(job["threads"]), "temp_directory": spill,
        "max_temp_directory_size": job["spill"], "autoinstall_known_extensions": False,
        "autoload_known_extensions": False})
    con.execute("SET enable_progress_bar = false")
    # `./` is the project: relative names in the agent's SQL are checked against it as written.
    con.execute("SET allowed_directories = ?", [["./", here + "/", scratch + "/"]])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


class _Timer:
    """Interrupts the connection after `seconds` (a profile that would not finish in time)."""

    def __init__(self, con, seconds: float):
        self.fired = False
        self._timer = threading.Timer(max(0.1, seconds), self._fire, [con])
        self._timer.daemon = True

    def _fire(self, con):
        self.fired = True
        con.interrupt()

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *_exc):
        self._timer.cancel()


def run_sql(con, job: dict) -> dict:
    limit = job.get("limit")
    relation = con.sql(job["sql"])
    if relation is None:
        raise ValueError("That statement returned no rows to read.")
    columns = list(relation.columns)
    if job.get("parquet_to"):
        if limit:
            relation = relation.limit(int(limit))
        relation.write_parquet(job["parquet_to"])
        rows = con.execute("SELECT count(*) FROM read_parquet(?)", [job["parquet_to"]]).fetchone()[0]
        return {"columns": columns, "row_count": rows, "truncated": False}
    rows = relation.limit(int(limit) + 1).fetchall() if limit else relation.fetchall()
    truncated = bool(limit) and len(rows) > int(limit)
    rows = rows[:int(limit)] if limit else rows
    return {"columns": columns, "rows": [[to_json(v) for v in row] for row in rows], "truncated": truncated}


READERS = {".csv": "read_csv", ".tsv": "read_csv", ".txt": "read_csv", ".parquet": "read_parquet",
           ".json": "read_json_auto", ".jsonl": "read_json_auto", ".ndjson": "read_json_auto"}


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _stats(con, source: str, columns: list[tuple[str, str]]) -> tuple[int, list[dict]]:
    """One pass over `source`: the row count, and per column the values present (so the missing
    ones exactly), about how many distinct values, min and max."""
    parts = ["count(*)"]
    for name, _kind in columns:
        column = _ident(name)
        parts += [f"count({column})", f"approx_count_distinct({column})",
                  f"min({column})::VARCHAR", f"max({column})::VARCHAR"]
    line = con.execute(f"SELECT {', '.join(parts)} FROM {source}").fetchone()
    total, out = int(line[0]), []
    for index, (name, kind) in enumerate(columns):
        present, distinct, low, high = line[1 + 4 * index: 5 + 4 * index]
        out.append({"name": name, "type": kind, "missing": total - int(present), "distinct_about": to_json(distinct),
                    "min": _short(low), "max": _short(high)})
    return total, out


def profile(con, job: dict) -> dict:
    """Size, rows, and per column its type, missing values, about how many distinct values, min and
    max, plus five sample rows. Every row when that fits in the time; else the first rows."""
    import duckdb
    path = job["profile"]
    extension = os.path.splitext(path)[1].lower()
    source = f"{READERS[extension]}('{path.replace(chr(39), chr(39) * 2)}')"
    started, budget = time.monotonic(), float(job["timeout"])
    columns = [(line[0], line[1]) for line in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
    sampled, rows, rows_note = False, None, None
    share, first = float(job.get("full_share", FULL_SHARE)), int(job.get("sample_rows", SAMPLE_ROWS))
    try:
        if share <= 0:          # straight to the sample (the studio's tests)
            raise duckdb.InterruptException("no full pass")
        with _Timer(con, budget * share):
            rows, described = _stats(con, source, columns)
    except duckdb.InterruptException:
        sampled = {"how": f"the first {first:,} rows", "rows_read": None}
        sampled["rows_read"], described = _stats(con, f"(SELECT * FROM {source} LIMIT {first})", columns)
        left = budget - (time.monotonic() - started) - 2
        try:
            if extension == ".parquet":
                rows = con.execute("SELECT sum(num_rows) FROM parquet_file_metadata(?)", [path]).fetchone()[0]
            elif left > 1:
                with _Timer(con, left):
                    rows = con.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
        except duckdb.InterruptException:
            rows = None
        if rows is None:
            rows_note = "Not counted: reading the whole file does not fit in the time limit."
    sample = con.execute(f"SELECT * FROM {source} LIMIT 5")
    answer = {"rows": int(rows) if rows is not None else None, "columns": described,
              "sample": {"columns": [d[0] for d in sample.description],
                         "rows": [[_short(v) for v in row] for row in sample.fetchall()]},
              "sampled": sampled}
    if rows_note:
        answer["rows_note"] = rows_note
    return answer


def classify(exc: Exception) -> str:
    text = str(exc)
    if "max_temp_directory_size" in text or "temp_directory" in text and "space" in text.lower():
        return "spill"
    if "Out of Memory" in text or "could not allocate" in text.lower() or "memory limit" in text.lower():
        return "memory"
    if "Permission Error" in text or "disabled by configuration" in text:
        return "outside"
    return "sql"


def main() -> None:
    job = json.loads(sys.stdin.read())
    answers = []
    try:
        con = connect(job)
    except Exception as exc:  # the studio says what went wrong; nothing ran
        json.dump({"fatal": " ".join(str(exc).split())[:600]}, sys.stdout)
        return
    for part in job["parts"]:
        part = {**job, **part}
        started = time.monotonic()
        try:
            answer = profile(con, part) if part.get("profile") else run_sql(con, part)
        except Exception as exc:  # each part gets its own verdict
            answer = {"error": " ".join(str(exc).split())[:600], "kind": classify(exc)}
        answer["seconds"] = round(time.monotonic() - started, 2)
        answers.append(answer)
    json.dump({"answers": answers}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
