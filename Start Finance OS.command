#!/bin/bash
# Double-click this in Finder to run everything.
#
# Starts Postgres if it is not already up, then the API and the web app, waits
# until both actually answer, and opens the browser. Closing this window (or
# Ctrl-C) stops both servers -- the trap matters, because a stale uvicorn
# squatting on port 8000 has caused confusing 404s in this project before.

set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

say() { printf "\033[1m%s\033[0m\n" "$1"; }
fail() { printf "\033[31m%s\033[0m\n" "$1"; }

cleanup() {
  echo
  say "Stopping…"
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null
  wait 2>/dev/null
  exit 0
}
trap cleanup INT TERM EXIT

# --- Postgres -------------------------------------------------------------
if ! pg_isready -q 2>/dev/null; then
  say "Starting Postgres…"
  brew services start postgresql@17 >/dev/null 2>&1 \
    || brew services start postgresql >/dev/null 2>&1
  for _ in $(seq 1 30); do pg_isready -q 2>/dev/null && break; sleep 1; done
fi
if ! pg_isready -q 2>/dev/null; then
  fail "Postgres will not start. Open Terminal and run: brew services start postgresql@17"
  read -r -p "Press return to close."
  exit 1
fi

mkdir -p "$ROOT/logs"

# Anything already holding the ports is from a previous run, not another app.
lsof -ti:8000 | xargs -r kill -9 2>/dev/null
lsof -ti:3000 | xargs -r kill -9 2>/dev/null

say "Starting the API…"
( cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 ) \
  > "$ROOT/logs/api.log" 2>&1 &
API_PID=$!

say "Starting the app…"
# -H 0.0.0.0 is what makes it reachable from a phone on the same network.
( cd "$ROOT/frontend" && npx next dev -H 0.0.0.0 -p 3000 ) \
  > "$ROOT/logs/web.log" 2>&1 &
WEB_PID=$!

# Wait for a real answer rather than sleeping a guessed number of seconds.
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://localhost:3000/ && break
  sleep 1
done

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
