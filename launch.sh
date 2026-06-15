#!/usr/bin/env bash
# Double-click launcher for the Teamup Dispatch Map.
# Invoked by "Launch Teamup Dispatch.desktop" (Terminal=true) from Dolphin.
#
# First run: creates the virtualenv + installs deps.
# Picks live mode if .env has a TEAMUP_API_KEY, otherwise demo mode.
# Opens the browser once the server is up; stops the server when you close
# this window (or press Ctrl+C).

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PORT="${PORT:-8000}"
URL="http://127.0.0.1:${PORT}"

echo "================================================"
echo "  Teamup Dispatch Map"
echo "  $DIR"
echo "================================================"

# --- 1. first-run bootstrap: venv + dependencies ---
if [ ! -d .venv ]; then
  echo "[setup] first run: creating virtualenv + installing dependencies..."
  if ! python3 -m venv .venv; then
    echo
    echo "ERROR: could not create the virtualenv."
    echo "You may need:  sudo apt install python3-venv"
    read -rp "Press Enter to close..."
    exit 1
  fi
  # shellcheck disable=SC1091
  . .venv/bin/activate
  pip install --upgrade pip >/dev/null 2>&1
  if ! pip install -r requirements.txt; then
    echo
    echo "ERROR: installing dependencies failed (see messages above)."
    read -rp "Press Enter to close..."
    exit 1
  fi
else
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

# --- 2. demo vs live ---
MODE_ENV=""
if [ -f .env ] && grep -qE '^[[:space:]]*TEAMUP_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' .env; then
  echo "[mode] LIVE - using credentials from .env"
else
  echo "[mode] DEMO - no Teamup key in .env, showing sample data"
  MODE_ENV="DEMO=1 DB_PATH=demo.db"   # keep demo data out of the live DB
fi

# --- 3. run server, open browser, clean up on exit ---
echo "[run]  starting server at ${URL}"
echo
env $MODE_ENV PORT="$PORT" uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

cleanup() {
  echo
  echo "[stop] shutting down..."
  kill "$SERVER_PID" 2>/dev/null
  wait "$SERVER_PID" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

# wait for the port to come up (up to ~20s), then open the browser
for _ in $(seq 1 40); do
  if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then break; fi
  sleep 0.5
done
xdg-open "$URL" >/dev/null 2>&1 || echo "Open this in your browser: ${URL}"

echo
echo ">>> Map is live at ${URL}"
echo ">>> Close this window (or press Ctrl+C) to stop the server."
wait "$SERVER_PID"
