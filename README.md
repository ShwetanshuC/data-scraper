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

- Windows: `bin\\run.ps1` (PowerShell)
  - Launches Chrome with remote debugging on port 9222
  - Creates `.venv`, installs deps, runs `windowsautomation.py`
  - Env knobs:
    - `CHROME_REMOTE_DEBUG_PORT` (default 9222)
    - `CHROME_REMOTE_PROFILE_DIR` (persist a separate Chrome profile)
  - Example: `powershell -ExecutionPolicy Bypass -File bin\\run.ps1`

- macOS/Linux: `bin/run.sh`
  - Same behavior as above for Unix-like systems

Option B — Manual

1. Start Chrome with remote debugging (Windows example):
   `chrome.exe --remote-debugging-port=9222`
2. Open your Google Sheet and ChatGPT in the same Chrome profile.
3. Put clinic websites in column `Z` of the Sheet.
4. `python windowsautomation.py`

Option C — Web UI

1. Start Chrome with remote debugging (same as above for Windows).
2. `pip install -r requirements.txt`
3. `python server.py`
4. Open `http://127.0.0.1:5000` in your browser.
5. Paste your Google Sheets link and click Start. Make sure `integrusautomation@gmail.com` has edit access.
   - The UI performs a reversible edit-access check on cell `ZZ1000` (writes a token and immediately clears it).
   - On success, the automation starts and logs progress. Add websites to the sheet to begin scraping.

## Notes
- The orchestrator keeps the Sheet tab focused for reads/writes, opens each site in its own tab, and uses ChatGPT in a separate tab.
- Dropdown menus are handled via targeted expansion or direct‑href when available.
- Prompts and parsing live in `app/prompts.py` to keep logic easy to tweak.
