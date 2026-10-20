import React, { useEffect, useState } from 'react';
import { loadSessionStudents, saveSessionStudents } from '../dataStore.js';
import { setupError } from '../schoolSetup.js';

// Students in this session (). The class's roster is offered ticked as a suggestion, but
// nothing is linked until the teacher (or an admin) clicks Confirm / Save. The choice is saved on the
// server and logged (Activity Log: counts only). Linked sessions show on each student's week in the
// Lesson planner and Students tab and feed the weekly summary, IEP check and parent report.
/** The ticks shown first: the saved students, else the suggestion (not saved until confirmed). */
export function initialPicks(r) {
  if (!r) return new Set();
  if (r.saved) return new Set(r.student_ids);
  const sug = (r.suggestion && r.suggestion.student_ids) || [];
  return new Set(sug.length ? sug : r.student_ids);
}

export default function SessionStudents({ recordingId, reviewerId = null, readOnly = false, onChange = null, initial = null }) {
  const [v, setV] = useState(initial);
  const [picked, setPicked] = useState(initialPicks(initial));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [savedNote, setSavedNote] = useState(null);

  function apply(r) {
    setV(r);
    setPicked(initialPicks(r));
  }
  useEffect(() => {
    if (initial) return undefined;
    let live = true;
    setV(null);
    setSavedNote(null);
    loadSessionStudents(recordingId, reviewerId).then((r) => { if (live) apply(r); })
      .catch(() => { if (live) setV({ error: true }); });
    return () => { live = false; };
  }, [recordingId, reviewerId, initial]);
  if (!v || v.error) return null;
  const can = v.can_change && !readOnly;
  const names = Object.fromEntries([...v.choices, ...v.others].map((c) => [c.student_id, c.name]));
  const same = v.saved && picked.size === v.student_ids.length && v.student_ids.every((x) => picked.has(x));

  function toggle(id) {
    const n = new Set(picked);
    if (n.has(id)) n.delete(id); else n.add(id);
    setPicked(n);
    setSavedNote(null);
  }
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const r = await saveSessionStudents(recordingId, [...picked], reviewerId);
      apply(r);
      setSavedNote(`Saved: ${r.student_ids.length} student${r.student_ids.length === 1 ? '' : 's'} linked to this session.`);
      if (onChange) onChange(r);
    } catch (e) {
      setErr(setupError(e));
    } finally {
      setBusy(false);
    }
  }
  const box = (c) => (
    <label key={c.student_id} className="student-pick">
      <input type="checkbox" checked={picked.has(c.student_id)} disabled={!can || busy}
        onChange={() => toggle(c.student_id)} data-testid={`session-student-${c.student_id}`} />
      {' '}{c.name}
    </label>
  );
  const sug = v.suggestion || {};
  return (
    <section data-testid="session-students">
      <h3>Students in this session</h3>
      {v.saved ? (
        <p className="muted" data-testid="session-students-saved">
          {v.student_ids.length ? v.student_ids.map((x) => names[x] || x).join(', ') : 'No students'}
        </p>
      ) : (
        <p className="muted" data-testid="session-students-unsaved">
          <span className="badge suggested-badge">not confirmed</span>
          {' '}{sug.student_ids && sug.student_ids.length
            ? `Suggested: the ${sug.student_ids.length} students of ${sug.class_name || 'this class'}. Untick anyone who was absent, then confirm.`
            : 'Tick who was in this session, then confirm.'}
        </p>
      )}
      {can ? (
        <>
          <div className="student-picks" data-testid="session-students-choices">{v.choices.map(box)}</div>
          {v.others.length ? (
            <details>
              <summary>Other students ({v.others.length})</summary>
              <div className="student-picks">{v.others.map(box)}</div>
            </details>
          ) : null}
          <div className="card-row">
            <button type="button" className="btn primary small" onClick={save} data-testid="save-session-students"
              disabled={busy || same || picked.size > v.max}>
              {v.saved ? 'Save students' : `Confirm ${picked.size} student${picked.size === 1 ? '' : 's'}`}
            </button>
          </div>
        </>
      ) : null}
      {savedNote ? <p className="muted" role="status">{savedNote}</p> : null}
      {err ? <p className="pl-error" role="alert">{err}</p> : null}
    </section>
  );
}
