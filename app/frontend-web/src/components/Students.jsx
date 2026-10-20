import React, { useCallback, useEffect, useMemo, useState } from 'react';
import MultiFilter from './MultiFilter.jsx';
import TextBox from './TextBox.jsx';
import ParentEmailPanel from './ParentEmailPanel.jsx';
import { AiDraftNote, SaveError, Status, SummaryEditor, errText } from './LessonPlanner.jsx';
import { loadStudent, loadStudents, savePlanner } from '../dataStore.js';
import { IEP_STATUS, weekRange } from '../plannerWeeks.js';
import { isSampleId, sampleDetail, sampleDraft, sampleStudents } from '../samples.js';
import { recordedLabel } from '../sessionTitle.js';

// Students tab. Every student for every role (Oct 2 decision);
// the list opens filtered to the viewer's own classrooms. A student's page: the IEP, one school
// week (Mon–Fri) of daily session summaries, then the weekly summary and the draft parent report.
// That summary + report is built only here now; the lesson planner links to it. Nothing is sent.

const IEP_LABELS = {
  plan_type: 'Plan', eligibility: 'Eligibility', present_levels: 'Present levels', accommodations: 'Accommodations',
  modifications: 'Modifications', services: 'Services', placement: 'Placement', behavior_plan: 'Behavior plan',
  esy: 'Extended school year', iep_start_date: 'Start', iep_review_date: 'Annual review', case_manager: 'Case manager',
};
// Long lists read better one per line; short ones stay on one line.
const LIST_FIELDS = new Set(['present_levels', 'accommodations', 'modifications', 'services']);

/** Ops (Oct 10): every sample profile and IEP carries a small "Fictional sample data" label. */
export function FictionalLabel({ label }) {
  return label ? <span className="badge sample-badge fictional-label" data-testid="fictional-label">{label}</span> : null;
}
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];

export function StudentList({ data, samples, onOpen, initialClasses = null }) {
  const rows = data?.students || [];
  const classes = useMemo(() => [...new Set(rows.flatMap((r) => r.class_codes))].sort(), [rows]);
  // opens on the viewer's default classroom; admins: All
  const opening = () => (data?.default_classroom && classes.includes(data.default_classroom) ? [data.default_classroom] : []);
  const [picked, setPicked] = useState(() => initialClasses ?? opening());
  useEffect(() => {
    if (initialClasses === null && data) setPicked(opening());
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = picked.length ? rows.filter((r) => r.class_codes.some((c) => picked.includes(c))) : rows;
  return (
    <section className="students" data-testid="student-list">
      {rows.length ? (
        <div className="pipeline-filters">
          <MultiFilter label="Classroom" options={classes} selected={picked} onChange={setPicked} />
          <button type="button" className="filter-reset" onClick={() => setPicked([])} disabled={!picked.length}>
            Reset to All
          </button>
        </div>
      ) : null}
      {picked.length ? <p className="muted filter-count">Showing {shown.length} of {rows.length} students</p> : null}
      <table className="pipeline-table">
        <thead><tr><th>Student</th><th>Grade</th><th>Classroom</th><th>Teacher</th><th>Plan</th><th>IEP goals</th><th /></tr></thead>
        <tbody>
          {[...shown, ...samples].map((s) => (
            <tr key={s.student_id} data-testid="student-row" className={s.sample ? 'sample-row' : undefined}>
              <td>
                <strong>{s.name}</strong>
                {s.sample ? <span className="badge sample-badge" data-testid="sample-badge">Sample</span> : null}
              </td>
              <td>{s.grade || '—'}</td>
              <td>{s.class_codes.join(', ') || '—'}</td>
              <td>{s.teacher_names.join(', ') || '—'}</td>
              <td>{s.plan_type || '—'}</td>
              <td>{s.iep_goals}</td>
              <td>
                <button type="button" className="btn primary small" onClick={() => onOpen(s.student_id)}
                  data-testid={`open-student-${s.student_id}`}>Open</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!shown.length && !samples.length ? <p className="muted">No students.</p> : null}
      {rows.some((r) => r.label) ? (
        <p className="hint" data-testid="students-fictional-note">
          <FictionalLabel label={rows.find((r) => r.label).label} /> Every student here is invented for the demo.
        </p>
      ) : null}
    </section>
  );
}

function Iep({ d }) {
  if (d.iep_hidden) return <p className="muted" data-testid="iep-hidden">IEP hidden for your role.</p>;
  const iep = d.iep || {};
  const extra = Object.keys(IEP_LABELS).filter((k) => iep[k] !== undefined && iep[k] !== null && iep[k] !== '');
  return (
    <div data-testid="iep">
      {d.label ? <p className="iep-label"><FictionalLabel label={d.label} /></p> : null}
      {(iep.goals || []).length ? (
        <ul className="iep-goals">
          {iep.goals.map((g) => (
            <li key={g.goal_id}><span className="pl-goal-tag">{g.category || g.goal_id}</span> {g.description}</li>
          ))}
        </ul>
      ) : <p className="muted">No IEP goals on file.</p>}
      {extra.map((k) => (Array.isArray(iep[k]) && LIST_FIELDS.has(k) ? (
        <div key={k} className="iep-field" data-testid={`iep-${k}`}><strong>{IEP_LABELS[k]}:</strong>
          <ul className="iep-list">{iep[k].map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      ) : (
        <p key={k} className="iep-field" data-testid={`iep-${k}`}><strong>{IEP_LABELS[k]}:</strong>{' '}
          {Array.isArray(iep[k]) ? iep[k].join('; ') : String(iep[k])}</p>
      )))}
      <p className="hint">Read-only. The IEP is shown as it is on file; editing it isn’t available here yet.</p>
    </div>
  );
}

/** School-year-to-date attendance (fictional sample data, Oct 10): counts only. */
export function Attendance({ a }) {
  if (!a) return null;
  const md = (iso) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : '');
  return (
    <p className="attendance" data-testid="attendance">
      <strong>Attendance {md(a.from)}–{md(a.through)}:</strong>{' '}
      {a.rate != null ? `${a.rate}% · ` : ''}{a.present} of {a.school_days} days present
      {` · ${a.absent_excused} absent excused · ${a.absent_unexcused} absent unexcused · ${a.tardy} tardy`}
    </p>
  );
}

function Days({ d, onOpenRecording }) {
  return (
    <ol className="student-days" data-testid="student-days">
      {d.days.map((day, i) => (
        <li key={day.date} data-testid="student-day">
          <h4>{WEEKDAYS[i]} <span className="muted">{day.date.slice(5).replace('-', '/')}</span></h4>
          {day.sessions.length ? day.sessions.map((s) => (
            <div key={s.recording_id} className="day-session">
              <p className="muted">
                {recordedLabel(s.timestamp_start) || ''}{s.class_code ? ` · ${s.class_code}` : ''}{' '}
                <span className={`badge status-${s.approved ? 'approved' : 'pending_review'}`}>
                  {s.approved ? 'approved' : 'draft, not approved'}</span>
                {s.incidents ? <span className="muted"> · {s.incidents} incident{s.incidents === 1 ? '' : 's'}</span> : null}
                {onOpenRecording ? (
                  <button type="button" className="link" onClick={() => onOpenRecording(s.recording_id)}>Open recording</button>
                ) : null}
              </p>
              <p data-testid="day-summary">{s.summary || <span className="muted">No summary.</span>}</p>
            </div>
          )) : <p className="muted">No recordings.</p>}
        </li>
      ))}
    </ol>
  );
}

function copy(text) {
  try { navigator.clipboard?.writeText(text); } catch { /* no clipboard */ }
}

function SampleReport({ draft, onClose }) {
  const [summary, setSummary] = useState(draft.summary);
  const [email, setEmail] = useState(draft.parent_email);
  return (
    <div className="pl-form" data-testid="sample-report">
      <p className="pl-ai-note"><span className="pl-pill suggest">Sample draft · not saved</span>
        <span>Written in your browser from this sample week’s notes. No AI call; nothing is sent or stored.</span></p>
      <label>Weekly summary<TextBox rows={4} value={summary} onValue={setSummary} name="summary" /></label>
      <label>Draft weekly parent report <span className="pl-hint">(never sent from here)</span>
        <TextBox rows={8} value={email} onValue={setEmail} name="parent_email" /></label>
      <div className="pl-actions">
        <button type="button" className="btn small" onClick={onClose}>Close</button>
        <button type="button" className="btn small ghost-line" onClick={() => copy(email)}>Copy parent report</button>
      </div>
    </div>
  );
}

function WeeklyReport({ d, student, reviewerId, readOnly, onSaved }) {
  const [draft, setDraft] = useState(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const saved = d.summary;
  const shown = saved || d.suggestion;
  const canEdit = d.can_edit && !readOnly;
  const checks = Object.fromEntries((shown?.iep_check || []).map((c) => [c.goal_id, c]));

  async function generate() {
    setError(null);
    if (d.sample) { setDraft(sampleDraft(d)); setEditing(true); return; }
    setBusy(true);
    try {
      setDraft(await savePlanner('draft/summary', { student_id: d.student_id, week: d.week }, reviewerId));
      setEditing(true);
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  }

  let why = null;
  if (!canEdit) why = readOnly ? 'Your role can read this but not draft it.' : 'Only this student’s teachers (or an admin) can draft the parent report.';
  else if (!d.can_draft) why = d.ai_drafts === 'off' ? 'AI drafts are switched off here.' : 'Drafts are for this week or earlier.';

  return (
    <section className="weekly-report" data-testid="weekly-report">
      <h3>Weekly summary &amp; parent report <span className="muted">· {weekRange(d.week)}</span></h3>
      {editing && draft?.sample ? (
        <SampleReport draft={draft} onClose={() => { setEditing(false); setDraft(null); }} />
      ) : editing ? (
        <SummaryEditor key={draft ? JSON.stringify(draft.ai) + draft.summary : 'own'} student={student}
          start={draft || saved || d.suggestion} draft={draft}
          onCancel={() => { setEditing(false); setDraft(null); }}
          onSave={async (body) => {
            await savePlanner('summary', { student_id: d.student_id, week: d.week, ...body }, reviewerId);
            setEditing(false); setDraft(null); onSaved();
          }} />
      ) : (
        <>
          <div className="pl-sub">
            Weekly summary{d.summary_versions > 1 ? <span className="pl-versions">{d.summary_versions} versions</span> : null}{' '}
            {saved ? <Status status={saved.status} /> : d.suggestion ? <span className="pl-pill suggest">Suggested</span> : null}
            {saved?.ai_assisted ? <span className="pl-pill ai-saved">AI-assisted</span> : null}
          </div>
          {shown ? <p className="pl-summary" data-testid="summary-text">{shown.summary}</p>
            : <p className="muted">Nothing yet for this week.</p>}
          {!saved && d.suggestion ? <p className="pl-hint">Counted from approved session notes. Not saved until a teacher saves it.</p> : null}
          {!d.iep_hidden && (d.iep?.goals || []).length && shown ? (
            <ul className="pl-iep" aria-label="IEP check">
              {d.iep.goals.map((g) => {
                const c = checks[g.goal_id];
                return (
                  <li key={g.goal_id} title={g.description}>
                    <span className="pl-goal-tag">{g.category || g.goal_id}</span>
                    <span className={`pl-check ${c ? c.status : 'none'}`}>{c ? IEP_STATUS[c.status] : 'Not checked'}</span>
                    {c?.note ? <span className="pl-check-note">{c.note}</span> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {saved?.parent_email ? (
            <div className="pl-email" data-testid="parent-email">
              <div className="pl-sub">{saved.status === 'approved' ? 'Weekly parent report · approved' : 'Draft weekly parent report · not sent'}</div>
              <p className="pl-email-text">{saved.parent_email}</p>
              <button type="button" className="btn small ghost-line" onClick={() => copy(saved.parent_email)}>Copy</button>
            </div>
          ) : null}
          {canEdit && !d.sample && saved?.parent_email ? (
            // Weekly parent email (): recipients, exact text, demo redirect; one click sends
            <ParentEmailPanel studentId={d.student_id} week={d.week} reviewerId={reviewerId}
              version={`${saved.saved_at || ''}${saved.status}`} />
          ) : null}
          {d.parent_email_hidden && !d.sample ? <p className="muted">The parent report is shown to this student’s teachers and admins.</p> : null}
          {canEdit && (saved || shown) ? (
            <button type="button" className="link" onClick={() => setEditing(true)}>{saved ? 'Edit summary & report' : 'Review & save summary'}</button>
          ) : null}
          {error ? <p className="pl-error" role="alert">{errText(error)}</p> : null}
          <div className="report-generate">
            <button type="button" className="btn primary" onClick={generate} disabled={busy || !!why}
              data-testid="generate-parent-report">
              {busy ? 'Drafting…' : 'Generate draft weekly parent report'}
            </button>
            <span className="hint">{why || (d.sample
              ? 'Sample: written in your browser, no AI.'
              : 'A draft for you to edit. The student’s name is replaced with “the student” before it goes to the AI. Nothing is sent until you approve it and click Send weekly email.')}</span>
          </div>
        </>
      )}
    </section>
  );
}

export function StudentPage({ d, reviewerId = null, readOnly = false, onWeek, onBack, onSaved = () => {}, onOpenRecording = null }) {
  const student = { student_id: d.student_id, iep_goals: d.iep_hidden ? [] : (d.iep?.goals || []) };
  return (
    <section className="student-page" data-testid="student-page">
      <button type="button" className="link" onClick={onBack} data-testid="students-back">← All students</button>
      <header className="student-head">
        <h2>{d.name}{d.sample ? <span className="badge sample-badge" data-testid="sample-badge">Sample</span> : null}
          <FictionalLabel label={d.label} /></h2>
        <p className="muted">{d.grade ? `Grade ${d.grade} · ` : ''}{d.class_codes.join(', ')}{d.teacher_names.length ? ` · ${d.teacher_names.join(', ')}` : ''}</p>
        <Attendance a={d.attendance} />
      </header>
      <section>
        <h3>IEP</h3>
        <Iep d={d} />
      </section>
      <section>
        <div className="week-nav" data-testid="student-week-nav">
          <button type="button" className="pl-arrow" aria-label="Previous week" disabled={!d.prev_week}
            onClick={() => onWeek(d.prev_week)}>‹</button>
          <h3>Week of {weekRange(d.week)}</h3>
          <button type="button" className="pl-arrow" aria-label="Next week" disabled={!d.next_week}
            onClick={() => onWeek(d.next_week)}>›</button>
          {d.week !== d.this_week ? <button type="button" className="link" onClick={() => onWeek(d.this_week)}>This week</button> : null}
        </div>
        <Days d={d} onOpenRecording={d.sample ? null : onOpenRecording} />
      </section>
      <WeeklyReport d={d} student={student} reviewerId={reviewerId} readOnly={readOnly} onSaved={onSaved} />
    </section>
  );
}

export default function Students({ route, onNavigate, reviewerId = null, readOnly = false, onOpenRecording = null,
  showSamples = true }) {
  const [list, setList] = useState(null);
  const [listError, setListError] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  // The built-in Samples (Marcus A., Sophia L.) are for the laptop demo; the hosted site shows the school's roster.
  const samples = useMemo(() => (showSamples ? sampleStudents() : []), [showSamples]);

  useEffect(() => {
    loadStudents(reviewerId).then(setList).catch((e) => { setList({ students: [], home_classrooms: [] }); setListError(e); });
  }, [reviewerId]);

  const load = useCallback(async () => {
    if (!route.studentId) { setDetail(null); return; }
    setError(null);
    if (isSampleId(route.studentId)) { setDetail(sampleDetail(route.studentId, route.week)); return; }
    try {
      setDetail(await loadStudent(route.studentId, route.week, reviewerId));   // journaled: iep_viewed
    } catch (e) {
      setDetail(null);
      setError(e);
    }
  }, [route.studentId, route.week, reviewerId]);
  useEffect(() => { load(); }, [load]);

  if (route.studentId) {
    if (error) return <p className="pl-error" role="alert">Couldn’t open this student ({error.message}).</p>;
    if (!detail) return <p className="muted">Loading…</p>;
    return (
      <StudentPage d={detail} reviewerId={reviewerId} readOnly={readOnly} onSaved={load}
        onWeek={(week) => onNavigate({ tab: 'students', studentId: route.studentId, week })}
        onBack={() => onNavigate({ tab: 'students' })} onOpenRecording={onOpenRecording} />
    );
  }
  return (
    <>
      {listError ? <p className="muted">Couldn’t load the class list ({listError.message}); showing the samples.</p> : null}
      {list ? <StudentList data={list} samples={samples} onOpen={(id) => onNavigate({ tab: 'students', studentId: id })} />
        : <p className="muted">Loading…</p>}
    </>
  );
}
