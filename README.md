# Data Scraper

Minimal, modular automation that reads clinic sites from a Google Sheet, asks ChatGPT where to find the staff/providers page, navigates there, and writes back Phone, Owner First/Last, and Doctor count.

## Structure
- `windowsautomation.py`: Orchestrator. Ties everything together end‑to‑end.
- `app/config.py`: Configuration (e.g., `SHEET_URL`).
- `app/sheets.py`: Google Sheets helpers (ensure tab, iframe, read/write, paste).
- `app/chat.py`: ChatGPT helpers (find chat tab, open new chat, send text).
- `app/chat_attach.py`: Image attach and send with screenshot.
- `app/screenshot.py`: Viewport and full‑page screenshots (CDP).
- `app/nav.py`: Navigation helpers (menus, dropdowns, direct‑href, heuristics).
- `app/utils.py`: Small shared utilities (host/tab switching, visible links, debug log).
- `app/prompts.py`: Prompt builders and reply parsers.
- `t.py`: Attach to running Chrome with remote debugging and find the editor.
- `chatgpt_response_checker.py`: Detects when ChatGPT finished responding.

## Prereqs
- Chrome running with remote debugging enabled (e.g., `--remote-debugging-port=9222`).
- Python deps: `pip install -r requirements.txt`.

## Run
Option A — Quick Start

- `bin/run.sh`
  - Launches Chrome with remote debugging on port 9222
  - Creates `.venv`, installs deps, runs `windowsautomation.py`
  - Env knobs:
    - `CHROME_REMOTE_DEBUG_PORT` (default 9222)
    - `CHROME_REMOTE_PROFILE_DIR` (persist a separate Chrome profile)

Option B — Manual

1. Start Chrome with remote debugging (macOS example):
   `open -na "Google Chrome" --args --remote-debugging-port=9222`
2. Open your Google Sheet and ChatGPT in the same Chrome profile.
3. Put clinic websites in column `Z` of the Sheet.
4. `python3 windowsautomation.py`

## Notes
- The orchestrator keeps the Sheet tab focused for reads/writes, opens each site in its own tab, and uses ChatGPT in a separate tab.
- Dropdown menus are handled via targeted expansion or direct‑href when available.
- Prompts and parsing live in `app/prompts.py` to keep logic easy to tweak.
