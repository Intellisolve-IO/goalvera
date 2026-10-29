import React, { useEffect, useState } from 'react';
import { loadSessionClass, saveSessionClass } from '../dataStore.js';
import { setupError } from '../schoolSetup.js';

// The session's class (tasks#103): picked from the Classes of its classroom. Until someone picks, the
// app suggests the class whose schedule matches the recording's start time and length. Picking is
// logged (audit trail + Activity Log). Classroom materials use the class's domain and methodology.
export default function SessionClass({ recordingId, classCode, reviewerId = null, readOnly = false, onChange = null }) {
  const [v, setV] = useState(null);
  const [pick, setPick] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    setV(null);
    loadSessionClass(recordingId, reviewerId).then((r) => {
      if (!live) return;
      setV(r);
      setPick(r.class ? r.class.id : '');
    }).catch(() => { if (live) setV({ error: true }); });
    return () => { live = false; };
  }, [recordingId, classCode, reviewerId]);
  if (!v || v.error || (!v.choices.length && !v.class)) return null;
  const can = v.can_change && !readOnly;
  const cur = v.class;

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const r = await saveSessionClass(recordingId, pick || null, reviewerId);
      setV(r);
      setPick(r.class ? r.class.id : '');
      if (onChange) onChange(r);
    } catch (e) {
      setErr(setupError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section data-testid="session-class">
      <h3>Class</h3>
      <p className="muted" data-testid="session-class-current">
        {cur ? (
          <>
            <strong>{cur.name}</strong>
            {cur.domain_name ? ` · ${cur.domain_name}${cur.methodology ? ` (${cur.methodology})` : ''}` : ''}
            {cur.schedule_text ? ` · ${cur.schedule_text}` : ''}
            {v.source === 'suggested' ? <span className="badge suggested-badge" data-testid="class-suggested"> suggested from the schedule</span> : null}
          </>
        ) : v.source === 'cleared' ? 'No class' : 'No class matches this recording’s time.'}
      </p>
      {can ? (
        <div className="card-row classroom-move">
          <label>
            {v.source === 'teacher' ? 'Change class' : 'Class'}
            <select value={pick} onChange={(e) => setPick(e.target.value)} disabled={busy} data-testid="class-select">
              <option value="">— no class —</option>
              {v.choices.map((c) => (
                <option key={c.id} value={c.id}>{c.name}{c.id === v.suggested_id ? ' (suggested)' : ''}</option>
              ))}
            </select>
          </label>
          <button type="button" className="btn ghost" onClick={save} data-testid="save-class"
            disabled={busy || (v.source === 'teacher' ? pick === (cur ? cur.id : '') : false)}>
            {v.source === 'teacher' ? 'Save' : 'Confirm'}
          </button>
        </div>
      ) : null}
      {err ? <p className="pl-error" role="alert">{err}</p> : null}
    </section>
  );
}
