from __future__ import annotations

import threading
import subprocess
import sys
import os
import uuid
from typing import Optional, Dict
from pathlib import Path

from flask import Flask, request, jsonify, render_template


# Base paths
_BASE = Path(__file__).resolve().parent
_TPL_DIR = (_BASE / "web" / "templates").as_posix()
_STATIC_DIR = (_BASE / "web" / "static").as_posix()


app = Flask(
    __name__,
    template_folder=_TPL_DIR,
    static_folder=_STATIC_DIR,
    static_url_path="/static",
)


class ScrapeJob:
    def __init__(self, cmd: list[str]):
        self.id = str(uuid.uuid4())
        self.cmd = cmd
        self.status = "created"  # created, running, completed, error, stopped
        self.returncode: Optional[int] = None
        self.log: list[str] = []
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None

    def add_log(self, line: str):
        with self._lock:
            if line is None:
                return
            self.log.append(line.rstrip())
            if len(self.log) > 1000:
                self.log = self.log[-1000:]

    def set_status(self, status: str, returncode: Optional[int] = None):
        with self._lock:
            self.status = status
            if returncode is not None:
                self.returncode = returncode

    def stop(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            self.status = "stopped"


scrape_jobs: Dict[str, ScrapeJob] = {}
scrape_jobs_lock = threading.Lock()


def _build_scraper_command(sheet_input: str, pipeline_mode: bool, pipeline_name: str | None, selected_worksheets: str | None) -> list[str]:
    py = sys.executable or "python3"
    script = (_BASE / "new_scraper.py").as_posix()
    if pipeline_mode:
        sheet_id = sheet_input.strip()
        if sheet_id.startswith("http") and "/spreadsheets/d/" in sheet_id:
            try:
                sheet_id = sheet_id.split("/spreadsheets/d/")[1].split("/")[0]
            except Exception:
                pass
        cmd = [py, script, "--pipeline-mode", "--pipeline", (pipeline_name or "Default Pipeline"), "--sheet-id", sheet_id]
        if selected_worksheets and selected_worksheets.strip():
            cmd += ["--selected-worksheets", selected_worksheets.strip()]
        return cmd
    sheet_arg = sheet_input.strip()
    if not sheet_arg.startswith("http"):
        sheet_arg = f"https://docs.google.com/spreadsheets/d/{sheet_arg}/edit"
    return [py, script, sheet_arg]


def _run_scraper_in_background(job: ScrapeJob):
    try:
        job.set_status("running")
        proc = subprocess.Popen(
            job.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            cwd=_BASE.as_posix(),
        )
        job._proc = proc
        if proc.stdout is not None:
            for line in proc.stdout:
                job.add_log(line)
        rc = proc.wait()
        if job.status == "stopped":
            job.set_status("stopped", rc)
        elif rc == 0:
            job.set_status("completed", rc)
        else:
            job.set_status("error", rc)
    except Exception as e:
        job.add_log(f"Runner error: {e}")
        job.set_status("error")


@app.get("/")
def root():
    return render_template("scraper.html")


@app.post("/scraper/start")
def scraper_start():
    data = request.json if request.is_json else request.form
    sheet_input = (data.get("sheet_input") or data.get("sheet_url") or "").strip()
    pipeline_mode = str(data.get("pipeline_mode") or "").lower() in ("1", "true", "on", "yes")
    pipeline_name = (data.get("pipeline") or data.get("pipeline_name") or "").strip() or None
    selected_ws = (data.get("selected_worksheets") or "").strip() or None

    if not sheet_input:
        return jsonify({"error": "Please provide a Google Sheet URL or ID."}), 400

    cmd = _build_scraper_command(sheet_input, pipeline_mode, pipeline_name, selected_ws)
    job = ScrapeJob(cmd)
    with scrape_jobs_lock:
        scrape_jobs[job.id] = job
    threading.Thread(target=_run_scraper_in_background, args=(job,), daemon=True).start()
    return jsonify({"job_id": job.id, "cmd": cmd})


@app.get("/scraper/status/<job_id>")
def scraper_status(job_id: str):
    with scrape_jobs_lock:
        job = scrape_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    with job._lock:
        return jsonify({
            "job_id": job.id,
            "status": job.status,
            "returncode": job.returncode,
            "log": job.log[-300:],
            "cmd": job.cmd,
        })


@app.post("/scraper/stop/<job_id>")
def scraper_stop(job_id: str):
    with scrape_jobs_lock:
        job = scrape_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    job.stop()
    return jsonify({"ok": True})


def main():
    debug = bool(int(os.environ.get("SCRAPER_DEBUG", "0")))
    # Resolve port with safe defaults and privileged-port fallback
    port_env = os.environ.get("SCRAPER_PORT") or os.environ.get("PORT")
    try:
        port = int(port_env) if port_env else 500
    except Exception:
        port = 500
    try:
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    except Exception:
        is_root = False
    if port < 1024 and not is_root:
        print(f"Port {port} requires elevated privileges; switching to 5001.")
        port = 500
    app.run(host="127.0.0.1", port=port, debug=debug)


if __name__ == "__main__":
    main()


