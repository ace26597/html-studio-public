"""The project's back end. The studio starts it with the command in studio.json.

Add routes under /api/. Anything in static/ is served as the site.
Need another package? Add `--with <name>` to the run command in studio.json.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()
STARTED = datetime.now(timezone.utc)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "started_at": STARTED.isoformat(timespec="seconds")}


# Keep this last: it serves static/index.html at / and every other file in static/.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="site")
