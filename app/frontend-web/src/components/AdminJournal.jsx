import React, { useCallback, useEffect, useState } from 'react';
import {
  JOURNAL_MAX,
  JOURNAL_PAGE,
  actionLabel,
  errorText,
  formatEt,
  lastDays,
  loadJournal,
  rangeProblem,
} from '../adminApi.js';
import ReportDownload from './ReportDownload.jsx';

// Same list as the server's journal (review_api/journal.py ACTIONS).
const ACTIONS = [
  'sign_in', 'sessions_listed', 'session_viewed', 'audit_viewed', 'notes_edited', 'session_approved',
  'pdf_downloaded', 'incident_pdf_downloaded', 'classroom_changed', 'bug_reported', 'bug_report_filed',
  'request_refused', 'journal_viewed', 'report_downloaded',
  'iep_viewed', 'iep_edited', 'roster_imported', 'settings_changed',
  'planner_viewed', 'planner_plan_saved', 'planner_summary_saved', 'planner_note_saved',
  'planner_summary_drafted', 'planner_plan_drafted',
  'materials_suggested', 'materials_requested', 'materials_made', 'materials_downloaded',
  'materials_emailed', 'materials_resent', 'materials_send_failed',
  'domain_added', 'domain_edited', 'domain_removed', 'class_added', 'class_edited', 'class_removed',
  'session_class_assigned',
  'session_students_set',
  'parent_email_sent',
  'parent_email_send_failed',
  'profile_updated', 'access_changed',
];

/**
 * Admin activity journal (tasks#77): newest first, Eastern time. Shows what the
 * server returns: its display name for the person and student, else the internal id.
 * Opening it is journaled on the server (journal_viewed).
 */
export default function AdminJournal({ onClose = null, load = loadJournal, initial = null, inline = false }) {
  const [range, setRange] = useState(() => lastDays(7));
  const [draft, setDraft] = useState(range);
  const [action, setAction] = useState('');
  const [limit, setLimit] = useState(JOURNAL_PAGE);
  const [state, setState] = useState(initial || { loading: true, entries: [], more: false, error: null });
  const [report, setReport] = useState(false);

  const fetchRows = useCallback(async (q) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const r = await load(q);
      setState({ loading: false, entries: r.entries, more: r.more, error: null });
    } catch (err) {
      setState({ loading: false, entries: [], more: false, error: err });
    }
  }, [load]);

  useEffect(() => {
    if (!initial) fetchRows({ ...range, action, limit });
  }, [range, action, limit, fetchRows, initial]);

  const problem = rangeProblem(draft.from, draft.to);

  function apply(e) {
    e.preventDefault();
    if (problem) return;
    setLimit(JOURNAL_PAGE);
    setRange({ ...draft });
  }

  const { loading, entries, more, error } = state;
  const body = (
        <div className={inline ? 'activity-body' : 'modal-body'}>
          <form className="admin-filters" onSubmit={apply}>
            <label>
              From
              <input type="date" value={draft.from} max={draft.to}
                onChange={(e) => setDraft({ ...draft, from: e.target.value })} data-testid="journal-from" />
            </label>
            <label>
              To
              <input type="date" value={draft.to} min={draft.from}
                onChange={(e) => setDraft({ ...draft, to: e.target.value })} data-testid="journal-to" />
            </label>
            <label>
              Action
              <select value={action} onChange={(e) => { setLimit(JOURNAL_PAGE); setAction(e.target.value); }}>
                <option value="">All actions</option>
                {ACTIONS.map((a) => <option key={a} value={a}>{actionLabel(a)}</option>)}
              </select>
            </label>
            <button type="submit" className="btn primary small" disabled={!!problem || loading}>Show</button>
            {problem && <p className="auth-error" role="alert">{problem}</p>}
          </form>

          {error ? (
            <div className="admin-msg error" role="alert" data-testid="journal-error">{errorText(error, 'the journal')}</div>
          ) : !loading && entries.length === 0 ? (
            <div className="admin-msg" data-testid="journal-empty">
              No journal entries from {range.from} to {range.to}
              {action ? ` for “${actionLabel(action)}”` : ''}.
            </div>
          ) : (
            <table className="pipeline-table journal-table" data-testid="journal-table">
              <thead>
                <tr><th>Time (ET)</th><th>Person</th><th>Action</th><th>Student</th></tr>
              </thead>
              <tbody>
                {entries.map((r) => (
                  <tr key={r.event_id}>
                    <td className="mono">{formatEt(r.occurred_at)}</td>
                    <td>
                      {r.actor || r.actor_id}
                      {r.actor_role && <span className="muted"> · {r.actor_role}</span>}
                    </td>
                    <td title={r.action}>
                      {actionLabel(r.action)}
                      {r.record_id && r.record_type === 'session' && (
                        <span className="muted mono journal-record"> {r.record_id}</span>
                      )}
                    </td>
                    <td>{r.student || r.student_id || <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="admin-panel-foot">
            <span className="muted" data-testid="journal-count">
              {loading ? 'Loading…' : error ? '' : `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'}`}
            </span>
            {more && !error && (
              <button type="button" className="btn ghost small" disabled={loading}
                onClick={() => setLimit((l) => Math.min(l + JOURNAL_PAGE, JOURNAL_MAX))}
                data-testid="journal-more">
                Load more
              </button>
            )}
          </div>
        </div>
  );
  // Activity Log tab (David, Oct 8): the same journal, on its own tab instead of a pop-up
  if (inline) {
    return (
      <section className="activity-log" data-testid="journal-panel">
        {/* The activity report CSV (by person / by student, date range) lives here now (David, Oct 10):
            the Download button at the top right of every page is gone; this is its only way in. */}
        <div className="activity-head">
          <p className="muted">Newest first. Times are Eastern (ET). Opening this page is recorded in the journal.</p>
          <button type="button" className="btn small" data-testid="open-report" onClick={() => setReport(true)}>
            Download report (CSV)
          </button>
        </div>
        {body}
        {report && <ReportDownload onClose={() => setReport(false)} />}
      </section>
    );
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal admin-panel" role="dialog" aria-modal="true" aria-labelledby="journal-title"
        data-testid="journal-panel" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">Admin · activity journal</p>
            <h2 id="journal-title">Who did what</h2>
            <p className="muted">Newest first. Times are Eastern (ET). Opening this page is recorded in the journal.</p>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </header>
        {body}
      </div>
    </div>
  );
}
