#!/bin/bash
# Put a new build of the interface live, safely.
#
#   studio2/tools/release-ui.sh             check, build aside, try it in a browser, then switch
#   studio2/tools/release-ui.sh --rollback  put the build before this one back
#   studio2/tools/release-ui.sh --check     everything but the switch
#
# Every studio running from this folder serves web/dist as it is, so whatever is in web/dist is
# live at once, with no restart. `npm run build` writes straight into it: on a machine with a
# studio running, use this instead. It
#   1. type-checks and runs the interface's tests;
#   2. builds into web/dist-staging, which nothing serves;
#   3. starts a throwaway studio on that build (no model calls) and drives it in a real browser:
#      open a project, send a message, close and reopen the chat, reload, and the same at phone
#      size (tools/release/ui_smoke.py). Any page error stops the release;
#   4. switches: web/dist becomes web/dist-prev and the staged build becomes web/dist. Two renames,
#      so a browser never sees half a build;
#   5. says which server code each studio on this machine runs, since a change to studio2/server
#      still needs that studio restarted.
set -euo pipefail

STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$STUDIO/web"
PY="${STUDIO2_PYTHON:-$STUDIO/.venv/bin/python}"
[ -x "$PY" ] || PY="$(cd "$STUDIO" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's#/\.git$##')/studio2/.venv/bin/python"
PORT="${STUDIO2_RELEASE_PORT:-8795}"
MODE="${1:-release}"

studios() {   # which server code each studio here runs
  for port in ${STUDIO2_LIVE_PORTS:-8200 8400}; do
    curl -s -m 5 "http://127.0.0.1:$port/api/health" 2>/dev/null | "$PY" -c '
import json, sys
port = sys.argv[1]
try:
    build = json.load(sys.stdin).get("build") or {}
except Exception:
    print("  port %s: not answering" % port); raise SystemExit
late = "  <- OLDER than the code on disk: restart it" if build.get("stale") else ""
print("  port %s: server code %s, started %s%s" % (port, build.get("commit") or "unknown", (build.get("started_at") or "?")[:16], late))' "$port"
  done
}

if [ "$MODE" = "--rollback" ]; then
  [ -d "$WEB/dist-prev" ] || { echo "There is no earlier build to put back."; exit 1; }
  mv "$WEB/dist" "$WEB/dist-undone" && mv "$WEB/dist-prev" "$WEB/dist" && mv "$WEB/dist-undone" "$WEB/dist-prev"
  echo "The earlier build is live again (and this one is kept as dist-prev)."; studios; exit 0
fi

cd "$WEB"
echo "1/4  Checking types and running the interface's tests"
npx tsc --noEmit
npx vitest run 2>&1 | grep -E "Test Files|Tests |FAIL|✗|×" || true
npx vitest run >/dev/null 2>&1 || { echo "The interface's tests failed. Nothing was released."; exit 1; }

echo "2/4  Building into web/dist-staging"
npx vite build --outDir dist-staging --emptyOutDir >/dev/null 2>&1
[ -f dist-staging/index.html ] || { echo "The build made no index.html. Nothing was released."; exit 1; }

echo "3/4  Trying the build in a browser on a throwaway studio (port $PORT, no model calls)"
DATA="$(mktemp -d)"
env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" PYTHONPATH="$STUDIO/server" \
  STUDIO2_FAKE=1 STUDIO2_PORT_RANGE="$((PORT+1))-$((PORT+4))" STUDIO_NO_OPEN=1 \
  "$PY" "$STUDIO/tools/release/serve_staged.py" "$DATA" "$WEB/dist-staging" "$PORT" > "$DATA/server.log" 2>&1 &
SERVER=$!
cleanup() { kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; rm -rf "$DATA"; }
trap cleanup EXIT
for _ in $(seq 1 40); do curl -s -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
if ! PYTHONPATH="$STUDIO/server" "$PY" "$STUDIO/tools/release/ui_smoke.py" "http://127.0.0.1:$PORT"; then
  echo "Nothing was released. The build is in web/dist-staging; the throwaway studio's log follows."; tail -5 "$DATA/server.log"; exit 1
fi

if [ "$MODE" = "--check" ]; then echo "4/4  Checked only: web/dist was not touched."; exit 0; fi

echo "4/4  Switching"
rm -rf dist-prev
[ -d dist ] && mv dist dist-prev
mv dist-staging dist
echo "Live: $(grep -o 'assets/index-[^"]*\.js' dist/index.html | head -1). The build before it is web/dist-prev (--rollback puts it back)."
studios
