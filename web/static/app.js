window.ScraperUI = (function () {
  const sel = (q) => document.querySelector(q);
  let jobId = null;
  let pollTimer = null;

  function setProgress(pct, statusText) {
    const fill = sel('#progress-fill');
    const status = sel('#status-text');
    fill && (fill.style.width = `${Math.max(0, Math.min(100, pct))}%`);
    status && (status.textContent = statusText || '');
  }

  function setError(msg) {
    sel('#error').style.display = 'block';
    sel('#error').textContent = msg || 'An error occurred';
    sel('#success').style.display = 'none';
  }

  function setSuccess(msg) {
    sel('#success').style.display = 'block';
    sel('#success').textContent = msg || 'Automation started successfully';
    sel('#error').style.display = 'none';
  }

  function renderLogs(lines) {
    const log = sel('#log');
    if (!log) return;
    log.textContent = (lines || []).map((l) => `• ${l}`).join('\n');
    log.scrollTop = log.scrollHeight;
  }

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch(`/status/${jobId}`);
      if (!res.ok) throw new Error('status error');
      const data = await res.json();
      renderLogs(data.log || []);
      const st = data.status;
      const stats = data.stats || {};
      // Update progress and title
      setProgress(data.progress || 0, st === 'cooldown' ? 'Cooling down…' : st.charAt(0).toUpperCase() + st.slice(1));
      if (st === 'running') setSuccess('Automation running. You can now add websites to your sheet.');
      if (st === 'completed') { setSuccess('All tabs processed successfully.'); clearInterval(pollTimer); pollTimer = null; }
      if (st === 'error') { setError(data.error || 'Error'); }
      // Update stats
      sel('#stat-batch').textContent = `${stats.batch_completed || 0} / ${stats.batch_limit || 80}`;
      sel('#stat-total').textContent = `${stats.total_completed || 0}`;
      sel('#stat-errors').textContent = `${stats.total_errors || 0}`;
      // Cooldown timer
      const rem = stats.cooldown_remaining || 0;
      const cd = sel('#cooldown');
      const cdt = sel('#cooldown-remaining');
      if (rem > 0) {
        cd.style.display = 'block';
        const h = Math.floor(rem / 3600);
        const m = Math.floor((rem % 3600) / 60);
        const s = rem % 60;
        cdt.textContent = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
      } else {
        cd.style.display = 'none';
      }
      // Toggle pause/resume buttons
      const paused = st === 'paused';
      sel('#btn-pause').style.display = paused ? 'none' : 'inline-block';
      sel('#btn-resume').style.display = paused ? 'inline-block' : 'none';
      if (st === 'stopped' || st === 'error') { clearInterval(pollTimer); pollTimer = null; }
    } catch (e) {
      // network errors: keep trying
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    const url = sel('#sheet_url').value.trim();
    sel('#form-error').style.display = 'none';
    sel('#error').style.display = 'none';
    sel('#success').style.display = 'none';

    try {
      const formData = new FormData();
      formData.append('sheet_url', url);
      const res = await fetch('/start', { method: 'POST', body: formData });
      const data = await res.json();
      if (!res.ok) {
        sel('#form-error').textContent = data.error || 'Invalid input';
        sel('#form-error').style.display = 'block';
        return;
      }
      jobId = data.job_id;
      sel('#progress-area').style.display = 'block';
      setProgress(10, 'Starting…');
      renderLogs([ 'Submitting sheet link…', 'Waiting for access check…' ]);
      pollTimer = setInterval(poll, 1000);
      poll();
    } catch (e) {
      sel('#form-error').textContent = 'Network error. Please try again.';
      sel('#form-error').style.display = 'block';
    }
  }

  async function post(path) {
    const res = await fetch(path, { method: 'POST' });
    if (!res.ok) throw new Error('request failed');
    return res.json();
  }

  function bindControls() {
    const btnPause = sel('#btn-pause');
    const btnResume = sel('#btn-resume');
    const btnStop = sel('#btn-stop');
    if (btnPause) btnPause.addEventListener('click', async () => {
      if (!jobId) return; try { await post(`/pause/${jobId}`); poll(); } catch {}
    });
    if (btnResume) btnResume.addEventListener('click', async () => {
      if (!jobId) return; try { await post(`/resume/${jobId}`); poll(); } catch {}
    });
    if (btnStop) btnStop.addEventListener('click', async () => {
      if (!jobId) return; try { await post(`/stop/${jobId}`); poll(); } catch {}
    });
  }

  function init() {
    const form = sel('#sheet-form');
    form && form.addEventListener('submit', onSubmit);
    bindControls();
  }

  return { init };
})();
