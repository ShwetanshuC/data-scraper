#!/usr/bin/env bash
set -euo pipefail

# Quick Start launcher for the data scraper.
# - Starts Chrome with remote debugging
# - Creates a local venv and installs deps
# - Runs windowsautomation.py

PORT="${CHROME_REMOTE_DEBUG_PORT:-9222}"
PROFILE_DIR="${CHROME_REMOTE_PROFILE_DIR:-"$PWD/.chrome-remote-$PORT"}"
PY="${PYTHON:-python3}"

echo "Launching Chrome with remote debugging on port $PORT..."
if [[ "${OSTYPE:-}" == darwin* ]]; then
  # macOS
  open -na "Google Chrome" --args --remote-debugging-port="$PORT" --user-data-dir="$PROFILE_DIR" || true
elif [[ "${OSTYPE:-}" == linux* ]]; then
  # Linux (try Chrome, then Chromium)
  (google-chrome --remote-debugging-port="$PORT" --user-data-dir="$PROFILE_DIR" >/dev/null 2>&1 & ) || true
  (chromium --remote-debugging-port="$PORT" --user-data-dir="$PROFILE_DIR" >/dev/null 2>&1 & ) || true
else
  echo "On Windows, start Chrome manually: chrome.exe --remote-debugging-port=$PORT"
fi

echo "Waiting for Chrome DevTools on http://127.0.0.1:$PORT ..."
for i in {1..30}; do
  if "$PY" - <<PY
import urllib.request, sys
try:
    urllib.request.urlopen("http://127.0.0.1:$PORT/json/version", timeout=0.5)
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  then
    break
  fi
  sleep 0.5
done

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv)"
  "$PY" -m venv .venv
fi
if [ -f ".venv/bin/activate" ]; then
  # Unix-like
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "Activate your virtualenv manually and re-run: .venv\\Scripts\\activate"
fi

pip install -r requirements.txt

exec python windowsautomation.py
