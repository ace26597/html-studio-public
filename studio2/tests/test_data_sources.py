"""Data sources: saved connections, secrets, attach, read-only queries.

The query tests need a real PostgreSQL. Point STUDIO2_TEST_PG at one
(host:port:database:user:password, a user that is allowed to write, so the
read-only guarantee is really exercised); without it they are skipped, never faked.
"""

import os

import pytest

from studio2_server.datasources import DataError, split_statement, to_json

PG = os.environ.get("STUDIO2_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set STUDIO2_TEST_PG=host:port:database:user:password to run")


def pg_body(**extra):
    host, port, database, user, password = PG.split(":", 4)
    return {"name": "Research", "kind": "postgres", "host": host, "port": int(port), "database": database,
            "user": user, "password": password, "ssl": "disable", **extra}


@pytest.mark.parametrize("sql", ["select 1", "  WITH a AS (select 1) select * from a;", "select ';' as x; -- done",
                                 "/* note */ select 1", "explain select 1", "(select 1) union (select 2)"])
def test_reads_are_accepted(sql):
    assert split_statement(sql)[1] in ("select", "with", "explain")


@pytest.mark.parametrize("sql", ["", "delete from t", "select 1; drop table t", "update t set a=1",
                                 "create table x(a int)", "select 1; select 2"])
def test_writes_and_batches_are_refused(sql):
    with pytest.raises(DataError):
        split_statement(sql)


def test_values_become_plain_json():
    import datetime, decimal
    assert to_json(decimal.Decimal("1.5")) == 1.5 and to_json(decimal.Decimal("0.1234567890123456789")) == "0.1234567890123456789"
    assert to_json(datetime.date(2026, 11, 10)) == "2026-11-10" and to_json(b"\x00\x01") == "AAE=" and to_json(float("nan")) is None


async def test_sources_are_saved_without_their_password(client, studio, settings):
    made = await client.post("/api/data-sources", json={"name": " Warehouse ", "kind": "redshift", "host": "example.invalid",
                                                        "database": "analytics", "user": "reader", "password": "s3cret-value"})
    assert made.status_code == 201
    source = made.json()
    assert source["name"] == "Warehouse" and source["port"] == 5439 and source["ssl"] == "require"
    assert source["has_password"] is True and "password" not in source
    assert "s3cret-value" not in (settings.data_dir / "data-sources.json").read_text()
    assert "s3cret-value" not in (await client.get("/api/data-sources")).text
    assert studio.data.password(source["id"]) == "s3cret-value"

    assert (await client.post("/api/data-sources", json={"name": "warehouse", "kind": "postgres", "host": "h",
                                                         "database": "d", "user": "u"})).json()["error"]["code"] == "name_taken"
    bad = await client.post("/api/data-sources", json={"name": "x", "kind": "oracle", "host": "h", "database": "d", "user": "u"})
    assert bad.status_code == 400 and "database type" in bad.json()["error"]["message"]
    bad = await client.post("/api/data-sources", json={"name": "x", "kind": "postgres", "host": "http://h/x", "database": "d", "user": "u"})
    assert bad.status_code == 400 and "Host" in bad.json()["error"]["message"]

    patched = (await client.patch(f"/api/data-sources/{source['id']}", json={"password": ""})).json()
    assert patched["has_password"] is False and patched["host"] == "example.invalid"

    failed = (await client.post(f"/api/data-sources/{source['id']}/test")).json()
    assert failed["ok"] is False and failed["message"]
    assert (await client.get("/api/data-sources")).json()[0]["last_test"]["ok"] is False


async def test_attach_token_and_delete(client, studio, project):
    pid = project["id"]
    source = (await client.post("/api/data-sources", json={"name": "Warehouse", "kind": "postgres", "host": "example.invalid",
                                                           "database": "d", "user": "u", "password": "pw"})).json()
    assert "token" not in project and project["data_sources"] == []
    assert (await client.get(f"/api/projects/{pid}/data")).json()["error"]["code"] == "bad_token"
    token = {"X-Studio-Token": studio.token(pid)}
    assert (await client.get(f"/api/projects/{pid}/data", headers=token)).json() == []
    not_attached = await client.post(f"/api/projects/{pid}/data/Warehouse/query", json={"sql": "select 1"}, headers=token)
    assert not_attached.status_code == 404 and not_attached.json()["error"]["code"] == "unknown_data_source"

    assert (await client.put(f"/api/projects/{pid}/data-sources/{source['id']}")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}")).json()["data_sources"] == [source["id"]]
    assert [s["attached"] for s in (await client.get(f"/api/projects/{pid}/data-sources")).json()] == [True]
    assert (await client.get(f"/api/projects/{pid}/data", headers=token)).json() == [
        {"name": "Warehouse", "kind": "postgres", "database": "d"}]
    context = studio.agent_context(pid)
    assert "Warehouse" in context and "query_database" in context
    assert studio.token(pid) not in context and "pw" not in context.replace("password", "")
    assert "example.invalid" not in context

    assert (await client.delete(f"/api/data-sources/{source['id']}")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}")).json()["data_sources"] == []
    assert studio.data.password(source["id"]) is None


async def test_the_library_lists_what_exists(client, studio):
    library = (await client.get("/api/library")).json()
    assert {t["id"] for t in library["tools"]} >= {"files", "shell", "web_search", "look", "app", "databases"}
    assert {"studio:studio-page-basics", "studio:sourced-report", "studio:deep-research"} <= {
        s["id"] for s in library["skills"] if s["source"] == "studio"}
    assert library["observed_at"] is None and not [s for s in library["skills"] if s["source"] == "built-in"]
    assert {k["kind"] for k in library["data_kinds"]} == {"postgres", "redshift", "mysql"}
    studio.record_session({"skills": ["dataviz", "studio:studio-page-basics"], "mcp_servers": []})
    library = (await client.get("/api/library")).json()
    assert library["observed_at"] and [s["id"] for s in library["skills"] if s["source"] == "built-in"] == ["dataviz"]


@needs_pg
async def test_a_real_database_is_tested_browsed_and_queried_read_only(client, studio, project):
    pid = project["id"]
    assert (await client.post("/api/data-sources/test", json=pg_body(password="wrong"))).json()["ok"] is False
    unsaved = (await client.post("/api/data-sources/test", json=pg_body())).json()
    assert unsaved["ok"] is True and unsaved["tables"] >= 1 and "PostgreSQL" in unsaved["server_version"]
    assert (await client.get("/api/data-sources")).json() == []

    source = (await client.post("/api/data-sources", json=pg_body())).json()
    assert (await client.post(f"/api/data-sources/{source['id']}/test")).json()["ok"] is True
    await client.put(f"/api/projects/{pid}/data-sources/{source['id']}")
    token = {"X-Studio-Token": studio.token(pid)}
    base = f"/api/projects/{pid}/data/Research"

    tables = (await client.get(f"{base}/schema", headers=token)).json()["tables"]
    assert {"schema": "public", "name": "trials", "type": "table", "columns": 24} in tables
    detail = (await client.get(f"{base}/schema", params={"table": "trials"}, headers=token)).json()
    assert detail["table"] == "public.trials" and {"name": "enrollment", "type": "integer", "nullable": True} in detail["columns"]

    total = (await client.post(f"{base}/query", json={"sql": "select count(*) as n, max(start_date) as latest from trials"},
                               headers=token)).json()
    assert total["columns"][0]["name"] == "n" and total["rows"][0][0] > 100 and total["truncated"] is False
    assert isinstance(total["rows"][0][1], str) and len(total["rows"][0][1]) == 10          # ISO date
    capped = (await client.post(f"{base}/query", json={"sql": "select nct_id from trials order by 1", "limit": 5},
                                headers=token)).json()
    assert capped["row_count"] == 5 and capped["truncated"] is True

    saved = (await client.post(f"{base}/query", json={"sql": "select phase, count(*) as trials from trials group by 1 order by 2 desc",
                                                      "save_as": "data/by_phase.csv"}, headers=token)).json()
    assert saved["saved"] == "data/by_phase.csv" and "rows" not in saved
    assert (studio.store.workspace(pid) / "data/by_phase.csv").read_text().splitlines()[0] == "phase,trials"

    for sql in ("delete from trials", "select 1; drop table trials"):
        refused = await client.post(f"{base}/query", json={"sql": sql}, headers=token)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "query_failed"
    # Even a write smuggled into a read statement is refused by the database itself.
    smuggled = await client.post(f"{base}/query", headers=token, json={
        "sql": "with gone as (delete from trials returning 1) select count(*) from gone"})
    assert smuggled.status_code == 400
    # EXPLAIN ANALYZE really executes its statement. Run this test with a user that MAY write:
    # the refusal then comes from the read-only transaction, not from missing privileges.
    executed = await client.post(f"{base}/query", headers=token, json={"sql": "explain analyze delete from trials"})
    assert executed.status_code == 400 and "read-only" in executed.json()["error"]["message"]
    still = (await client.post(f"{base}/query", json={"sql": "select count(*) from trials"}, headers=token)).json()
    assert still["rows"][0][0] == total["rows"][0][0]
    broken = await client.post(f"{base}/query", json={"sql": "select nope from trials"}, headers=token)
    assert broken.status_code == 400 and "nope" in broken.json()["error"]["message"]
