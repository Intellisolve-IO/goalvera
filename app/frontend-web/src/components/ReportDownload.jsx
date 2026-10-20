import React, { useState } from 'react';
import { downloadActivityReport, errorText, lastDays, rangeProblem } from '../adminApi.js';

/**
 * Activity report CSV (): by person or by student, for a date range.
 * Fetched with the signed-in token and saved as a file; the server records the
 * download in the journal (report_downloaded).
 */
export default function ReportDownload({ onClose, download = downloadActivityReport }) {
  const [by, setBy] = useState('person');
  const [range, setRange] = useState(() => lastDays(7));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const problem = rangeProblem(range.from, range.to);

  async function submit(e) {
    e.preventDefault();
    if (problem) return;
    setBusy(true);
    setMsg(null);
    try {
      const name = await download({ by, ...range });
      setMsg({ ok: true, text: `Saved ${name} to this computer. The download is recorded in the journal.` });
    } catch (err) {
      setMsg({ ok: false, text: errorText(err, 'the activity report') });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal admin-panel narrow" role="dialog" aria-modal="true" aria-labelledby="report-title"
        data-testid="report-panel" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">Admin · activity report</p>
            <h2 id="report-title">Download report (CSV)</h2>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </header>
        <form className="modal-body" onSubmit={submit}>
          <fieldset className="admin-choice">
            <legend>Count activity</legend>
            <label><input type="radio" name="by" value="person" checked={by === 'person'}
              onChange={() => setBy('person')} /> By person</label>
            <label><input type="radio" name="by" value="student" checked={by === 'student'}
              onChange={() => setBy('student')} /> By student</label>
          </fieldset>
          <div className="admin-filters">
            <label>
              From
              <input type="date" value={range.from} max={range.to} data-testid="report-from"
                onChange={(e) => setRange({ ...range, from: e.target.value })} />
            </label>
            <label>
              To
              <input type="date" value={range.to} min={range.from} data-testid="report-to"
                onChange={(e) => setRange({ ...range, to: e.target.value })} />
            </label>
          </div>
          <p className="hint">Report days are UTC days: each starts at 8 PM ET the evening before (7 PM in winter).</p>
          {problem && <p className="auth-error" role="alert">{problem}</p>}
          {msg && (
            <div className={`admin-msg ${msg.ok ? 'ok' : 'error'}`} role={msg.ok ? 'status' : 'alert'}
              data-testid="report-msg">{msg.text}</div>
          )}
          <div>
            <button type="submit" className="btn primary" disabled={busy || !!problem} data-testid="report-download">
              {busy ? 'Preparing…' : 'Download CSV'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
