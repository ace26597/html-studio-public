"""`query_files` on big files, and what its SQL may read (CONTRACT.md, the section of that name).

Every test is small and fast: the limits are tested with tiny limits, never with big files."""

import json
import os
import subprocess
import time

import pytest

from studio2_server import filesql, tools
from studio2_server.check import check_page, run_sql
from studio2_server.sources import SourceError


async def call(studio, pid, **args):
    result = await tools.call(studio, pid, "query_files", args)
    text = result["content"][0]["text"]
    return result.get("is_error", False), (text if result.get("is_error") else json.loads(text))


@pytest.fixture
def workspace(studio, project):
    folder = studio.store.workspace(project["id"])
    (folder / "data").mkdir(exist_ok=True)
    (folder / "data" / "orders.csv").write_text("id,region,amount\n1,north,10\n2,south,20\n3,north,\n4,east,5\n")
    return folder


# -- what the SQL may read --------------------------------------------------------------------
async def test_the_projects_own_files_are_read_as_before(studio, project, workspace):
    pid = project["id"]
    error, answer = await call(studio, pid, sql="select sum(amount) as total from 'data/orders.csv'")
    assert not error and answer["rows"] == [[35]]
    error, answer = await call(studio, pid, sql="select * from 'data/orders.csv'", save_as="data/orders.parquet")
    assert not error and answer["saved"] == "data/orders.parquet" and answer["row_count"] == 4
    for sql in ("select count(*) from 'data/orders.parquet'", "select count(*) from read_parquet('data/*.parquet')",
                f"select count(*) from '{workspace.resolve()}/data/orders.csv'", "select count(*) from './data/orders.csv'",
                "select count(*) from read_csv('data/*.csv')"):
        error, answer = await call(studio, pid, sql=sql)
        assert not error and answer["rows"] == [[4]], (sql, answer)
    error, answer = await call(studio, pid, sql="summarize select * from 'data/orders.csv'")
    assert not error and answer["row_count"] == 3          # summarize is a read, as the data-analysis skill says


async def test_nothing_outside_the_project_can_be_read(studio, project, workspace, tmp_path):
    pid = project["id"]
    other = workspace.parent.parent / "someone-else"
    other.mkdir(parents=True, exist_ok=True)
    (other / "private.csv").write_text("secret\nkept\n")
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\nkept\n")
    (workspace / "data" / "link.csv").symlink_to(outside)
    (workspace / "data" / "folder").symlink_to(other, target_is_directory=True)
    for sql in ("select * from read_csv('/etc/hosts')", "select content from read_text('/etc/hosts')",
                "select * from read_blob('/etc/passwd')", f"select * from '{outside}'",
                "select * from '../../someone-else/private.csv'", "select * from 'data/../../../someone-else/private.csv'",
                "select * from 'data/link.csv'", "select * from 'data/folder/private.csv'",
                "select * from read_csv('https://example.com/x.csv')", "select * from read_csv('s3://bucket/x.csv')",
                "select * from glob('/*')", "select * from sniff_csv('/etc/hosts')",
                "select * from read_csv(['data/orders.csv', '/etc/hosts'])"):
        error, text = await call(studio, pid, sql=sql)
        assert error, sql
        assert "secret" not in text and "localhost" not in text, sql
    error, text = await call(studio, pid, sql="select * from read_csv('/etc/hosts')")
    assert "Only files inside the project folder" in text


async def test_statements_that_change_things_are_refused_before_duckdb(studio, project, workspace):
    pid = project["id"]
    for sql in ("copy (select 1) to 'data/x.csv'", "attach 'other.db'", "install httpfs", "load httpfs",
                "set enable_external_access = true", "pragma version", "select 1; select 2"):
        error, text = await call(studio, pid, sql=sql)
        assert error, sql
    assert not (workspace / "data" / "x.csv").exists()
    error, text = await call(studio, pid, sql="select getenv('HOME')")
    assert error
    # Inside the worker the configuration is locked and access to other places is off.
    error, answer = await call(studio, pid, sql="select current_setting('enable_external_access'), "
                                                "current_setting('lock_configuration')")
    assert not error and answer["rows"] == [[False, True]]


def test_the_worker_gets_no_keys_from_the_studio(monkeypatch):
    monkeypatch.setenv("STUDIO2_ACCESS_KEY", "k-1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-2")
    kept = filesql._environment()
    assert "STUDIO2_ACCESS_KEY" not in kept and "OPENROUTER_API_KEY" not in kept and "PATH" in kept


# -- save_as .parquet -------------------------------------------------------------------------
async def test_parquet_is_written_whole_inside_the_project_only(studio, project, workspace):
    pid = project["id"]
    error, answer = await call(studio, pid, sql="select region, sum(amount) total from 'data/orders.csv' group by 1",
                               save_as="data/summary/by_region.parquet")
    assert not error and answer["row_count"] == 3 and answer["bytes"] > 0
    assert (workspace / "data" / "summary" / "by_region.parquet").is_file()
    for target in ("../out.parquet", ".git/out.parquet", "data/out.xlsx"):
        error, text = await call(studio, pid, sql="select 1", save_as=target)
        assert error, target
    # A leading / is read inside the project, as safe_path always has.
    error, answer = await call(studio, pid, sql="select 1 as one", save_as="/elsewhere/out.parquet")
    assert not error and answer["saved"] == "elsewhere/out.parquet" and (workspace / "elsewhere/out.parquet").is_file()
    # A statement that fails leaves no file behind.
    error, _text = await call(studio, pid, sql="select * from 'data/missing.csv'", save_as="data/broken.parquet")
    assert error and not (workspace / "data" / "broken.parquet").exists()
    error, answer = await call(studio, pid, sql="select * from 'data/orders.csv'", save_as="data/two.parquet", limit=2)
    assert not error and answer["row_count"] == 2


# -- profile ----------------------------------------------------------------------------------
async def test_profile_describes_a_file(studio, project, workspace):
    error, answer = await call(studio, project["id"], profile="data/orders.csv")
    assert not error, answer
    assert answer["rows"] == 4 and answer["format"] == "csv" and answer["bytes"] > 0 and answer["sampled"] is False
    amount = next(column for column in answer["columns"] if column["name"] == "amount")
    assert amount["missing"] == 1 and amount["min"] == "5" and amount["max"] == "20"
    region = next(column for column in answer["columns"] if column["name"] == "region")
    assert region["distinct_about"] in (2, 3, 4) and region["type"] == "VARCHAR"
    assert answer["sample"]["columns"] == ["id", "region", "amount"] and len(answer["sample"]["rows"]) == 4
    assert "aggregated" in answer["next"]
    await call(studio, project["id"], sql="select * from 'data/orders.csv'", save_as="data/orders.parquet")
    error, answer = await call(studio, project["id"], profile="data/orders.parquet")
    assert not error and answer["rows"] == 4 and answer["format"] == "parquet"
    for bad in ("data/missing.csv", "../orders.csv", "data/book.xlsx"):
        (workspace / "data" / "book.xlsx").write_bytes(b"PK")
        error, text = await call(studio, project["id"], profile=bad)
        assert error, bad
    error, text = await call(studio, project["id"], profile="data/orders.csv", sql="select 1")
    assert error and "not both" in text


async def test_a_profile_that_does_not_fit_in_time_says_it_was_sampled(studio, project, workspace, monkeypatch):
    rows = "\n".join(f"{n},r{n % 3}" for n in range(500))
    (workspace / "data" / "many.csv").write_text("n,r\n" + rows + "\n")
    monkeypatch.setattr(filesql, "FULL_SHARE", 0)         # no time for a full pass
    monkeypatch.setattr(filesql, "SAMPLE_ROWS", 100)
    error, answer = await call(studio, project["id"], profile="data/many.csv")
    assert not error, answer
    assert answer["sampled"] == {"how": "the first 100 rows", "rows_read": 100}
    assert answer["rows"] == 500                           # counted on its own, since that fitted
    assert "say so" in answer["next"]


def test_a_big_file_is_told_to_become_parquet(monkeypatch):
    monkeypatch.setattr(filesql, "BIG_FILE", 10)
    out = tools._profiled({"rows": 1, "columns": [], "sample": {}, "sampled": False},
                          {"profile": "data/big.csv", "bytes": 2048}, 60)
    assert "convert it to Parquet once" in out["next"] and "data/big.parquet" in out["next"]


# -- limits -----------------------------------------------------------------------------------
async def test_the_time_limit_ends_the_worker(studio, project, workspace):
    started = time.monotonic()
    error, text = await call(studio, project["id"], timeout=1,
                             sql="select count(*) from range(100000000000) t(i) where i % 7 = 3")
    assert error and "1-second limit" in text and "group by" in text
    assert time.monotonic() - started < 6
    left = subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "filesql_worker.py"],
                          capture_output=True, text=True).stdout.split()
    assert left == []                     # the worker this test started is gone
    error, text = await call(studio, project["id"], timeout="soon", sql="select 1")
    assert error and "timeout" in text


async def test_the_memory_limit_says_what_to_do(studio, project, workspace, monkeypatch):
    monkeypatch.setenv("STUDIO2_QUERY_MEMORY", "40MB")
    monkeypatch.setenv("STUDIO2_QUERY_SPILL", "10MB")
    error, text = await call(studio, project["id"],
                             sql="select length(string_agg(repeat('x', 100) || i, ',')) from range(3000000) t(i)")
    assert error and "40MB memory limit" in text and "aggregate" in text


def test_limits_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO2_QUERY_MEMORY", "512MB")
    monkeypatch.setenv("STUDIO2_QUERY_THREADS", "2")
    monkeypatch.setenv("STUDIO2_QUERY_SPILL", "1GB")
    got = filesql.limits(tmp_path)
    assert got["memory"] == "512MB" and got["threads"] == 2 and got["spill_bytes"] <= 1024 ** 3
    monkeypatch.setenv("STUDIO2_QUERY_MEMORY", "plenty")
    monkeypatch.delenv("STUDIO2_QUERY_SPILL")
    got = filesql.limits(tmp_path)
    import shutil
    assert got["memory"] == "2GB" and got["spill_bytes"] <= shutil.disk_usage(tmp_path).free // 2


async def test_the_temporary_folder_is_outside_the_project_and_removed(studio, project, workspace):
    root = filesql.scratch_root()
    assert root == studio.settings.data_dir / "run" / "query"
    assert workspace.resolve() not in root.resolve().parents
    await call(studio, project["id"], sql="select * from 'data/orders.csv'", save_as="data/o.parquet")
    await call(studio, project["id"], timeout=1, sql="select count(*) from range(100000000000) t(i) where i % 7 = 3")
    assert list(root.iterdir()) == []


# -- the page check uses the same worker ------------------------------------------------------
def test_check_page_runs_data_marks_in_the_worker(studio, project, workspace):
    (workspace / "index.html").write_text(
        '<p><span data-src="data/orders.csv" data-sql="select count(*) from \'data/orders.csv\'">4</span> orders, '
        '<span data-src="data/orders.csv" data-sql="select sum(amount) from \'data/orders.csv\'">35</span> in all, '
        '<span data-src="data/orders.csv" data-sql="select count(*) from read_csv(\'/etc/hosts\')">9</span> hosts, '
        '<span data-src="data/orders.csv" data-sql="drop table x">1</span>.</p>')
    result = check_page(workspace, save=False)
    statuses = [item["status"] for item in result["items"]]
    assert statuses == ["verified", "verified", "failed", "failed"]
    assert "inside the project" in result["items"][2]["reason"] and "Only reading" in result["items"][3]["reason"]
    assert run_sql(workspace, "select count(*) from 'data/orders.csv'") == (["count_star()"], [(4,)])
    with pytest.raises(SourceError):
        run_sql(workspace, "select * from '/etc/hosts'")


async def test_a_wide_file_keeps_the_profile_under_the_answer_cap(studio, project, workspace):
    names = [f"column_{n}" for n in range(400)]
    (workspace / "data" / "wide.csv").write_text(",".join(names) + "\n" + ",".join("v" * 150 for _ in names) + "\n")
    result = await tools.call(studio, project["id"], "query_files", {"profile": "data/wide.csv"})
    text = result["content"][0]["text"]
    assert not result.get("is_error") and len(text) < tools.RESULT_CAP
    answer = json.loads(text)
    assert answer["columns_cut"] == 150 and answer["sample"]["columns_cut"] == 370


async def test_missing_values_are_counted_exactly(studio, project, workspace):
    # 7 of 30,000 is 0.023%: worked out from a rounded percentage it would come out as 6.
    lines = ["n,v"] + [f"{n},{'' if n < 7 else n}" for n in range(30_000)]
    (workspace / "data" / "gaps.csv").write_text("\n".join(lines) + "\n")
    error, answer = await call(studio, project["id"], profile="data/gaps.csv")
    assert not error and next(c for c in answer["columns"] if c["name"] == "v")["missing"] == 7
