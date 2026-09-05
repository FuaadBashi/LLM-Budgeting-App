#!/bin/bash
# Double-click this in Finder to run everything.
#
# Starts Postgres if it is not already up, then the API and the web app, waits
# until both actually answer, and opens the browser. Closing this window (or
# Ctrl-C) stops both servers -- the trap matters, because a stale uvicorn
# squatting on port 8000 has caused confusing 404s in this project before.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# The launcher is normally copied to the Desktop, so its own directory is not
# necessarily the repository. Prefer a colocated checkout, then the standard
# Desktop/Projects location used by this installation.
if [[ -d "$SCRIPT_DIR/backend" && -d "$SCRIPT_DIR/frontend" ]]; then
  ROOT="$SCRIPT_DIR"
elif [[ -d "$SCRIPT_DIR/Projects/BudgetApp/backend" && -d "$SCRIPT_DIR/Projects/BudgetApp/frontend" ]]; then
  ROOT="$SCRIPT_DIR/Projects/BudgetApp"
else
  printf "\033[31m%s\033[0m\n" "Cannot find BudgetApp from $SCRIPT_DIR."
  printf "%s\n" "Expected backend/ and frontend/ beside this launcher, or in Projects/BudgetApp/."
  read -r -p "Press return to close."
  exit 1
fi
cd "$ROOT"

say() { printf "\033[1m%s\033[0m\n" "$1"; }
fail() { printf "\033[31m%s\033[0m\n" "$1"; }

cleanup() {
  STATUS=$?
  trap - INT TERM EXIT
  echo
  say "Stopping…"
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null
  wait 2>/dev/null
  exit "$STATUS"
}
trap cleanup INT TERM EXIT

# --- Postgres -------------------------------------------------------------
if ! pg_isready -q 2>/dev/null; then
  say "Starting Postgres…"
  # Unset, brew checks for updates on every invocation -- a slow or blocked
  # connection turns that into a silent, indefinite hang right here, which
  # reads as "stuck at starting" with no error and no clue why.
  export HOMEBREW_NO_AUTO_UPDATE=1
  ( brew services start postgresql@17 >/dev/null 2>&1 \
      || brew services start postgresql >/dev/null 2>&1 ) &
  BREW_PID=$!
  # Backgrounded and bounded, not awaited: if brew itself hangs on something
  # other than the update check, this loop still exits on schedule instead of
  # blocking forever on a command this script does not control.
  for _ in $(seq 1 30); do pg_isready -q 2>/dev/null && break; sleep 1; done
  kill "$BREW_PID" 2>/dev/null
fi
if ! pg_isready -q 2>/dev/null; then
  fail "Postgres will not start. Open Terminal and run: brew services start postgresql@17"
  read -r -p "Press return to close."
  exit 1
fi

mkdir -p "$ROOT/logs"

# Anything already listening on these app-owned ports is from a previous run.
for PORT in 8000 3000; do
  OLD_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$OLD_PIDS" ]] || kill $OLD_PIDS 2>/dev/null || true
done

say "Starting the API…"
( cd "$ROOT/backend" && \
  .venv/bin/alembic upgrade head && \
  exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 ) \
  > "$ROOT/logs/api.log" 2>&1 &
API_PID=$!

say "Starting the app…"
# -H 0.0.0.0 is what makes it reachable from a phone on the same network.
( cd "$ROOT/frontend" && exec ./node_modules/.bin/next dev -H 0.0.0.0 -p 3000 ) \
  > "$ROOT/logs/web.log" 2>&1 &
WEB_PID=$!

# Wait for both real answers rather than sleeping a guessed number of seconds.
API_READY=0
WEB_READY=0
for _ in $(seq 1 60); do
  kill -0 "$API_PID" 2>/dev/null || break
  kill -0 "$WEB_PID" 2>/dev/null || break
  curl -sf -o /dev/null http://localhost:8000/api/auth/session && API_READY=1
  curl -sf -o /dev/null http://localhost:3000/ && WEB_READY=1
  [[ "$API_READY" -eq 1 && "$WEB_READY" -eq 1 ]] && break
  sleep 1
done

if [[ "$API_READY" -ne 1 || "$WEB_READY" -ne 1 ]]; then
  echo
  fail "Finance OS did not start."
  echo "API log: $ROOT/logs/api.log"
  tail -n 12 "$ROOT/logs/api.log" 2>/dev/null || true
  echo
  echo "App log: $ROOT/logs/web.log"
  tail -n 12 "$ROOT/logs/web.log" 2>/dev/null || true
  echo
  read -r -p "Press return to close."
  exit 1
fi

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
echo
say "Running."
echo "  This Mac:  http://localhost:3000"
[[ -n "$LAN_IP" ]] && echo "  Your phone (same Wi-Fi):  http://$LAN_IP:3000"
echo
echo "  Logs: logs/api.log, logs/web.log"
echo "  Close this window to stop."
echo

open "http://localhost:3000"
wait
