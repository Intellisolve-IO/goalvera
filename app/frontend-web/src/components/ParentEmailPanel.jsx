import React, { useEffect, useState } from 'react';
import { loadParentEmail, sendParentEmail } from '../dataStore.js';

// Weekly parent email (). After the teacher approves the weekly report, this shows exactly
// what will be sent and to whom: only the student's guardians with "receives reports" on (the
// others are listed as excluded), the exact text, and, in demo mode, that every email goes to the
// teacher's own inbox instead. Nothing is sent until the teacher clicks Send; nothing sends by itself.

const PROBLEMS = {
  NOT_APPROVED: 'Approve the weekly report (with its parent email) first.',
  NO_RECIPIENTS: 'No guardian of this student is set to receive reports.',
  NO_STAFF_EMAIL: 'There is no teacher email on file to send the demo copy to.',
  EMAIL_OFF: 'Email is switched off here.',
  EMAIL_HAS_LAST_NAME: 'The parent email contains a last name. Edit it to use first names only.',
  EMAIL_HAS_ADDRESS: 'The parent email contains an email address. Remove it first.',
  EMAIL_HAS_ID: 'The parent email contains an internal id. Remove it first.',
  EMAIL_HAS_IEP: 'The parent email quotes IEP goal text. Rephrase it first.',
  EMAIL_HAS_NUMBER: 'The parent email contains a long number. Remove it first.',
};
const SENT = {
  sent: (r) => (r.redirected ? `Sent ${r.sent} demo email${r.sent === 1 ? '' : 's'} to ${r.to} (not to parents).` : `Sent to ${r.sent} guardian${r.sent === 1 ? '' : 's'}.`),
};
const when = (iso) => (iso ? new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' }) + ' ET' : '');

export function ParentEmailView({ p, busy = false, result = null, error = null, onSend = null }) {
  if (!p) return null;
  const problem = p.problem ? (PROBLEMS[p.problem] || `Can’t send (${p.problem}).`) : null;
  return (
    <div className="parent-send" data-testid="parent-send">
      <div className="pl-sub">Send weekly email</div>
      {p.redirect ? (
        <p className="demo-redirect" data-testid="redirect-banner" role="note">
          <strong>Demo mode:</strong> parent emails go to {p.redirect_to || 'your own address'}, not to parents.
        </p>
      ) : null}
      <table className="guardians" data-testid="guardians">
        <thead><tr><th>Parent / guardian</th><th>Relationship</th><th>Email</th><th>Weekly report</th></tr></thead>
        <tbody>
          {p.contacts.map((c) => (
            <tr key={c.contact_id} className={c.receives_reports ? '' : 'excluded'} data-testid="guardian-row">
              <td>{c.first_name}</td>
              <td>{c.relationship}</td>
              <td>{c.email || <span className="muted">none on file</span>}</td>
              <td>{c.receives_reports ? <span className="badge status-approved">receives reports</span>
                : <span className="badge muted-badge">excluded: {c.email ? 'not set to receive reports' : 'no email'}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {p.emails.length && !p.problem ? (
        <details className="send-preview" open data-testid="send-preview">
          <summary>Exactly what will be sent ({p.emails.length} email{p.emails.length === 1 ? '' : 's'})</summary>
          {p.emails.map((e) => (
            <div key={e.contact_id} className="email-preview">
              <div><span className="muted">From:</span> {p.from}</div>
              <div><span className="muted">To:</span> {e.to}{p.redirect ? <span className="muted"> (instead of {e.guardian_first_name})</span> : null}</div>
              <div><span className="muted">Subject:</span> {e.subject}</div>
              <pre className="email-body">{e.body}</pre>
            </div>
          ))}
        </details>
      ) : null}
      {problem ? <p className="muted" data-testid="send-problem">{problem}</p> : null}
      <div className="card-row">
        <button type="button" className="btn primary" disabled={!p.can_send || busy || !onSend} onClick={onSend}
          data-testid="send-weekly-email">
          {busy ? 'Sending…' : p.redirect ? `Send weekly email (demo: to ${p.redirect_to || 'you'})` : `Send weekly email to ${p.emails.length} guardian${p.emails.length === 1 ? '' : 's'}`}
        </button>
        <span className="hint">Sent only when you click. Every send is logged.</span>
      </div>
      {p.last_sent ? <p className="muted" data-testid="last-sent">Last sent {when(p.last_sent.at)}{p.last_sent.redirected ? ' (demo copy to the teacher)' : ''}.</p> : null}
      {result ? <p className={result.status === 'sent' ? 'pl-ok' : 'pl-error'} role="status">
        {result.status === 'sent' ? SENT.sent(result) : `Not sent (${result.status}).`}</p> : null}
      {error ? <p className="pl-error" role="alert">{PROBLEMS[error] || `Not sent (${error}).`}</p> : null}
    </div>
  );
}

export default function ParentEmailPanel({ studentId, week, reviewerId = null, version = '' }) {
  const [p, setP] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    setP(null); setResult(null); setError(null);
    loadParentEmail(studentId, week, reviewerId).then((r) => { if (live) setP(r); }).catch(() => { if (live) setP(null); });
    return () => { live = false; };
  }, [studentId, week, reviewerId, version]);
  async function send() {
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await sendParentEmail(studentId, week, p.preview_sha256, reviewerId);
      setResult(r);
      setP(await loadParentEmail(studentId, week, reviewerId));
    } catch (e) {
      setError(e.message);
      if (e.message === 'PREVIEW_CHANGED') setP(await loadParentEmail(studentId, week, reviewerId));
    }
    setBusy(false);
  }
  return <ParentEmailView p={p} busy={busy} result={result} error={error} onSend={send} />;
}
