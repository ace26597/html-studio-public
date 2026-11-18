"""The project's back end. The studio starts it with the command in studio.json.

Add routes under /api/. Anything in static/ is served as the site.
Need another package? Add `--with <name>` to the run command in studio.json.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI()
# Answer only to the names the studio gives (a page elsewhere could point its own name at this
# machine). Outside the studio the variable is not set and nothing is limited.
if os.environ.get("STUDIO_ALLOWED_HOSTS"):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.environ["STUDIO_ALLOWED_HOSTS"].split(","))
STARTED = datetime.now(timezone.utc)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "started_at": STARTED.isoformat(timespec="seconds")}


# Keep this last: it serves static/index.html at / and every other file in static/.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="site")
