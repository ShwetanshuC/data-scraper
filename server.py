from __future__ import annotations

import threading
import time
import uuid
import re
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify, render_template
from pathlib import Path

from t import attach
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from app.sheets import ensure_sheets_tab, enter_sheets_iframe_if_needed, goto_cell, read_cell, set_cell_value
from windowsautomation import monitor_loop


# Resolve absolute paths so it works regardless of current working directory
_BASE = Path(__file__).resolve().parent
_TPL_DIR = (_BASE / "web" / "templates").as_posix()
_STATIC_DIR = (_BASE / "web" / "static").as_posix()

app = Flask(
    __name__,
    template_folder=_TPL_DIR,
    static_folder=_STATIC_DIR,
    static_url_path="/static",
)


class JobControl:
    def __init__(self, batch_limit: int = 80):
        self.pause = False
        self.stop = False
        self.batch_limit = batch_limit
        self.batch_completed = 0
        self.total_completed = 0
        self.total_errors = 0
        self.cooldown_until: float = 0.0
        self._lock = threading.Lock()

    def set_pause(self, value: bool):
        with self._lock:
            self.pause = value

    def set_stop(self):
        with self._lock:
            self.stop = True

    def mark_success(self):
        with self._lock:
            self.total_completed += 1

    def mark_attempt(self):
        with self._lock:
            self.batch_completed += 1

    def mark_error(self):
        with self._lock:
            self.total_errors += 1

    def reset_batch_if_needed(self):
        with self._lock:
            if time.time() >= self.cooldown_until and self.cooldown_until != 0.0:
                # cooldown finished; reset batch
                self.cooldown_until = 0.0
                self.batch_completed = 0

    def need_cooldown(self) -> bool:
        with self._lock:
            return self.batch_completed >= self.batch_limit

    def begin_cooldown(self, seconds: int):
        with self._lock:
            self.cooldown_until = time.time() + seconds

    def cooldown_remaining(self) -> int:
        with self._lock:
            if self.cooldown_until <= 0:
                return 0
            rem = int(self.cooldown_until - time.time())
            return rem if rem > 0 else 0


class Job:
    def __init__(self, sheet_url: str):
        self.id = str(uuid.uuid4())
        self.sheet_url = sheet_url
        self.status = "created"  # created, checking_access, running, paused, cooldown, error, completed
        self.progress = 0
        self.error: str | None = None
        self.log: list[str] = []
        self._lock = threading.Lock()
        self.control = JobControl(batch_limit=80)

    def add_log(self, msg: str):
        with self._lock:
            self.log.append(msg)

    def set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)


jobs: Dict[str, Job] = {}
jobs_lock = threading.Lock()

# Shared WebDriver instance (attach to existing Chrome with remote debugging)
_driver = None
_driver_lock = threading.Lock()


def get_driver():
    global _driver
    with _driver_lock:
        try:
            # If an existing driver is healthy, reuse it
            if _driver is not None:
                _ = _driver.window_handles  # simple liveness check
                return _driver
        except Exception:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None
        # Create a new attachment
        _driver = attach()
        return _driver


def is_valid_sheet_url(u: str) -> bool:
    if not u:
        return False
    return bool(re.match(r"^https://docs\.google\.com/spreadsheets/d/[a-zA-Z0-9-_]+/edit", u))


def _open_share_dialog(d) -> bool:
    try:
        d.switch_to.default_content()
    except Exception:
        pass
    selectors = [
        (By.CSS_SELECTOR, "button[aria-label*='Share']"),
        (By.CSS_SELECTOR, "div[aria-label*='Share'][role='button']"),
        (By.XPATH, "//button[normalize-space()='Share']"),
        (By.XPATH, "//*[@role='button' and normalize-space()='Share']"),
    ]
    for by, sel in selectors:
        try:
            els = d.find_elements(by, sel)
            for el in els:
                if not el.is_displayed():
                    continue
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                el.click()
                return True
        except Exception:
            continue
    return False


def _email_has_editor_access(d, email: str, timeout: float = 8.0) -> bool:
    try:
        d.switch_to.default_content()
    except Exception:
        pass
    if not _open_share_dialog(d):
        return False
    try:
        WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//*[@role='dialog']"))
        )
    except Exception:
        pass
    # Try to read dialog text
    try:
        dlg = d.find_elements(By.XPATH, "//*[@role='dialog']")
        text = "\n".join([x.text for x in dlg]) if dlg else d.find_element(By.TAG_NAME, 'body').text
    except Exception:
        try:
            text = d.find_element(By.TAG_NAME, 'body').text
        except Exception:
            text = ""
    txt = (text or "").lower()
    has_email = (email or "").lower() in txt if email else False
    # Editor wording variants (cover different UIs/locales)
    editor_tokens = (
        "editor", "can edit", "editing", "editing access", "you can edit", "has edit access",
        # drive share panels
        "anyone with the link can edit", "restricted can edit",
        # owner also implies full control
        "owner", "is owner", "you are the owner",
    )
    has_editor = any(tok in txt for tok in editor_tokens)
    # Close dialog politely
    try:
        close = d.find_elements(By.XPATH, "//*[@role='dialog']//*[@aria-label='Close' or @aria-label='Cancel' or normalize-space()='Done']")
        if close:
            close[0].click()
    except Exception:
        pass
    # If email is visible with any role, or any indication of edit capability exists, consider it OK.
    return bool(has_editor or has_email)


def _sheet_is_view_only(d, timeout: float = 3.0) -> bool:
    """Heuristic: detect common view-only banners or request-access UI.

    We deliberately avoid typing into the grid. This just scans top-level UI
    for obvious signals that the sheet cannot be edited by the current user.
    """
    try:
        d.switch_to.default_content()
    except Exception:
        pass
    end = time.time() + timeout
    checks = [
        (By.XPATH, "//*[contains(., 'View only') or contains(., 'view only')]"),
        (By.XPATH, "//*[contains(., 'Request edit access') or contains(., 'request edit access')]")
    ]
    while time.time() < end:
        try:
            for by, sel in checks:
                els = d.find_elements(by, sel)
                for el in els:
                    if el.is_displayed():
                        return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def check_edit_access(job: Job) -> bool:
    job.add_log("Attaching to browser…")
    try:
        d = get_driver()
    except Exception as e:
        job.set(status="error", error=f"Could not attach to browser: {e}")
        return False

    job.add_log("Opening sheet…")
    try:
        h = ensure_sheets_tab(d, job.sheet_url)
        d.switch_to.window(h)
        enter_sheets_iframe_if_needed(d, timeout=8)
    except Exception as e:
        msg = str(e)
        if 'invalid session id' in msg.lower():
            job.set(status="error", error="Browser session lost. Please restart Chrome with remote debugging and try again.")
        else:
            job.set(status="error", error=f"Could not open sheet: {e}")
        return False

    # Quick heuristic: if we can spot view-only UI, fail fast
    try:
        if _sheet_is_view_only(d):
            job.set(status="error", error="This sheet appears to be view-only. Please grant Edit access and try again.")
            return False
    except Exception:
        pass

    # Non-destructive access check via Share dialog
    job.add_log("Checking edit access (Share dialog)…")
    try:
        ok = _email_has_editor_access(d, "integrusautomation@gmail.com", timeout=10)
        if ok:
            job.add_log("Edit access confirmed for integrusautomation@gmail.com.")
            return True
        # Could not verify, but not definitively view-only. Proceed optimistically.
        job.add_log("Could not verify editor via Share dialog; proceeding anyway.")
        return True
    except Exception as e:
        job.add_log(f"Warning: access check error: {e}; proceeding anyway.")
        return True


def start_monitor_thread(job: Job) -> None:
    def progress_cb(msg: str):
        job.add_log(msg)

    def run():
        try:
            job.add_log("Starting automation…")
            job.set(status="running", progress=0)
            # Run monitor loop (long-running)
            d = get_driver()
            def control_hooks():
                ctl = job.control
                # expose a simple dict of callables/values to the orchestrator
                return {
                    'should_stop': lambda: ctl.stop,
                    'should_pause': lambda: ctl.pause,
                    'on_attempt': ctl.mark_attempt,
                    'on_success': ctl.mark_success,
                    'on_error': ctl.mark_error,
                    'batch_limit': ctl.batch_limit,
                    'cooldown_remaining': ctl.cooldown_remaining,
                    'begin_cooldown': lambda secs: ctl.begin_cooldown(secs),
                    'reset_batch_if_needed': ctl.reset_batch_if_needed,
                    'need_cooldown': ctl.need_cooldown,
                }

            monitor_loop(sheet_url=job.sheet_url, progress_cb=progress_cb, driver=d, control=control_hooks())
        except Exception as e:
            job.set(status="error", error=str(e))

    t = threading.Thread(target=run, name=f"monitor-{job.id}", daemon=True)
    t.start()


@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        # Surface template errors instead of a blank page
        return (
            f"<pre style='padding:16px'>Template error: {e}\n\n"
            f"Expected template at web/templates/index.html</pre>",
            500,
            {"Content-Type": "text/html; charset=utf-8"},
        )

@app.get("/_healthz")
def healthz():
    return {"ok": True}


@app.post("/start")
def start():
    sheet_url = (request.form.get("sheet_url") or request.json.get("sheet_url") if request.is_json else request.form.get("sheet_url"))
    if not is_valid_sheet_url(sheet_url or ""):
        return jsonify({"error": "Please enter a valid Google Sheets link."}), 400

    job = Job(sheet_url)
    with jobs_lock:
        jobs[job.id] = job

    job.set(status="checking_access", progress=10)

    def bg():
        if check_edit_access(job):
            job.add_log("Access OK. Launching scraper…")
            start_monitor_thread(job)
        # else: status is already set to error by check_edit_access

    threading.Thread(target=bg, name=f"access-{job.id}", daemon=True).start()

    return jsonify({"job_id": job.id})


@app.get("/status/<job_id>")
def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    with job._lock:
        # Determine high-level state
        state = job.status
        ctl = job.control
        cooldown_rem = ctl.cooldown_remaining()
        if ctl.stop:
            state = 'stopped'
        elif cooldown_rem > 0:
            state = 'cooldown'
        elif ctl.pause:
            state = 'paused'
        else:
            # keep whatever job.status says (likely 'running')
            pass

        # Progress bar reflects batch progress 0..100
        pct = 0
        try:
            pct = int((ctl.batch_completed / max(1, ctl.batch_limit)) * 100)
        except Exception:
            pct = 0

        return jsonify({
            "job_id": job.id,
            "status": state,
            "progress": pct,
            "error": job.error,
            "log": job.log[-200:],
            "stats": {
                "batch_completed": ctl.batch_completed,
                "batch_limit": ctl.batch_limit,
                "total_completed": ctl.total_completed,
                "total_errors": ctl.total_errors,
                "cooldown_remaining": cooldown_rem,
            }
        })


@app.post("/pause/<job_id>")
def pause(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    job.control.set_pause(True)
    job.set(status="paused")
    job.add_log("Paused by user.")
    return jsonify({"ok": True})


@app.post("/resume/<job_id>")
def resume(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    job.control.set_pause(False)
    job.set(status="running")
    job.add_log("Resumed by user.")
    return jsonify({"ok": True})


@app.post("/stop/<job_id>")
def stop(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    job.control.set_stop()
    job.set(status="stopped")
    job.add_log("Stopped by user.")
    return jsonify({"ok": True})


def main():
    # Run on localhost by default
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
