"""The model comparison: one new project per model in a running studio, the same brief and data for all.

Usage: run.py <run folder> <how many at once> <connection>=<model> ...
  for example: run.py runs/today 3 gateway=openrouter/z-ai/glm-5.3-flash subscription=sonnet
`connection` is the id of a model account in the studio's config. The studio is the one at
STUDIO_URL (http://127.0.0.1:8200 when not set). Appends one JSON line per finished model to
<run folder>/results.jsonl and prints it, so progress can be read. A request that the provider
rate-limits before doing anything is tried again, up to three times. Every request costs what
its model costs: nothing here caps that, except the time limit per model.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8200").rstrip("/")
HERE = Path(__file__).parent
LIMIT = 15 * 60          # seconds per model
BRIEF = (HERE / "brief.txt").read_text()


def label(model: str) -> str:
    return model.replace("openrouter/", "").split("/")[-1]


async def one_try(client, pid, connection, model, row):
    """One request in the project. Fills `row`; returns True when the provider refused before any work."""
    started = time.monotonic()
    sent = await client.post(f"{BASE}/api/projects/{pid}/turns",
                             json={"text": BRIEF, "connection": connection, "model": model,
                                   "attachments": ["data/orders.csv"]})
    if sent.status_code != 202:
        row["status"] = f"not started: {sent.status_code} {sent.text[:200]}"
        return False
    turn, finished_at = sent.json()["turn_id"], None
    async with client.stream("GET", f"{BASE}/api/projects/{pid}/events", timeout=None) as stream:
        async for line in stream.aiter_lines():
            now = time.monotonic()
            if now - started > LIMIT and finished_at is None:
                row["status"] = "stopped at the 15 minute limit"
                await client.post(f"{BASE}/api/projects/{pid}/turns/{turn}/stop")
                finished_at = now
            if finished_at is not None and now - finished_at > 30:
                break                      # no cost line came (the Claude plan sends none)
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event.get("turn_id") != turn:
                continue
            kind = event.get("type")
            if kind == "tool_call":
                row["tools"].append(event.get("name") or "?")
            elif kind == "tool_result" and event.get("status") == "error":
                row["tool_errors"].append(str(event.get("summary"))[:200])
            elif kind == "approval_request":
                row["asked"].append(str(event.get("detail"))[:160])
                await client.post(f"{BASE}/api/projects/{pid}/turns/{turn}/approvals/{event['approval_id']}",
                                  json={"decision": "deny"})
            elif kind == "notice":
                row["notices"].append(f"{event.get('level')}: {str(event.get('message'))[:260]}")
            elif kind == "message_chunk":
                row["text"] += event.get("text") or ""
            elif kind == "turn_finished":
                if not str(row["status"]).startswith("stopped"):
                    row["status"] = event.get("status")
                row["usage"], row["error"] = event.get("usage"), event.get("error")
                finished_at = now
            elif kind == "turn_cost":
                row["turn_cost"] = {k: event.get(k) for k in ("cost_usd", "requests", "unpriced", "source", "reason")}
                break
    row["seconds"] = round(time.monotonic() - started)
    refused = row["status"] == "failed" and not row["tools"] and "429" in str(row.get("error"))
    return refused


async def one(client, spec, gate, run):
    connection, model = spec.split("=", 1)
    async with gate:
        row = {"model": model, "connection": connection, "status": "?", "tools": [], "tool_errors": [], "asked": [],
               "notices": [], "text": "", "tries": 0, "started": time.strftime("%H:%M:%S")}
        try:
            project = (await client.post(f"{BASE}/api/projects", json={"name": f"Model test · {label(model)}"})).json()
            pid = row["pid"] = project["id"]
            row["page"] = project["preview_url"]
            with open(HERE / "orders.csv", "rb") as data:
                up = await client.post(f"{BASE}/api/projects/{pid}/upload", files={"file": ("orders.csv", data, "text/csv")},
                                       data={"dir": "data"})
            if up.status_code != 200:
                row["status"] = f"upload failed: {up.status_code} {up.text[:200]}"
                return row
            for attempt in range(1, 4):
                row["tries"] = attempt
                row.update(tools=[], tool_errors=[], text="", status="?")
                if not await one_try(client, pid, connection, model, row):
                    break
                await asyncio.sleep(75)        # rate-limited upstream: wait and try again
        except Exception as exc:
            row["status"] = f"script error: {exc.__class__.__name__}: {str(exc)[:200]}"
        row["text"] = row["text"][-1500:]
        with open(run / "results.jsonl", "a") as out:
            out.write(json.dumps(row) + "\n")
        print(json.dumps({k: row.get(k) for k in ("model", "status", "seconds", "tries", "pid")}), flush=True)
        return row


async def main(run, width, specs):
    run.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(width)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
        await asyncio.gather(*(one(client, spec, gate, run) for spec in specs))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]))
