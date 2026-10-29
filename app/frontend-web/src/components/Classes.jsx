import React, { useCallback, useEffect, useMemo, useState } from 'react';
import MultiFilter from './MultiFilter.jsx';
import { FictionalLabel } from './Students.jsx';
import { loadClasses, saveClass } from '../dataStore.js';
import { WEEK, endTime, meetingProblem, setupError } from '../schoolSetup.js';

// Classes tab (David, Oct 10 2026; tasks#103): the actual class. Name, teacher(s), educational domain
// (Domains tab), students, classroom and schedule (one or more meeting patterns: days, start, length).
// Everyone sees every class; the list opens on the viewer's default classroom like the other tabs.
// Admins add, edit and delete; a teacher edits the classes they teach. Every change is journaled.

function Meeting({ m, onChange, onRemove }) {
  const toggle = (d) => onChange({ ...m, days: m.days.includes(d) ? m.days.filter((x) => x !== d) : WEEK.filter((x) => x === d || m.days.includes(x)) });
  return (
    <div className="cl-meeting" data-testid="meeting">
      <div className="cl-days" role="group" aria-label="Days">
        {WEEK.map((d) => (
          <label key={d} className={m.days.includes(d) ? 'on' : undefined}>
            <input type="checkbox" checked={m.days.includes(d)} onChange={() => toggle(d)} />{d}
          </label>
        ))}
      </div>
      <label>Start <input type="time" value={m.start} onChange={(e) => onChange({ ...m, start: e.target.value })} /></label>
      <label>Minutes <input type="number" min={5} max={600} step={5} value={m.minutes}
        onChange={(e) => onChange({ ...m, minutes: e.target.value === '' ? '' : Number(e.target.value) })} /></label>
      <span className="muted">{m.start && m.minutes ? `ends ${endTime(m.start, m.minutes)}` : ''}</span>
      {onRemove ? <button type="button" className="link" onClick={onRemove}>Remove</button> : null}
    </div>
  );
}

export function ClassEditor({ data, initial, busy, error, onSave, onCancel, onRemove = null, admin = false }) {
  const blank = { name: '', classroom: data.default_classroom || data.classrooms[0] || '', teacher_ids: [], domain_id: '',
    student_ids: [], schedule: [{ days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], start: '08:00', minutes: 30 }] };
  const [c, setC] = useState(() => ({ ...blank, ...(initial || {}), domain_id: (initial && initial.domain_id) || '' }));
  const [allStudents, setAllStudents] = useState(false);
  const rooms = admin ? data.classrooms : [...new Set([...(data.own_classrooms || []), initial && initial.classroom].filter(Boolean))];
  const students = (data.students || []).filter((s) => allStudents || s.classrooms.includes(c.classroom) || c.student_ids.includes(s.student_id));
  const toggle = (key, id) => setC({ ...c, [key]: c[key].includes(id) ? c[key].filter((x) => x !== id) : [...c[key], id] });
  const problem = c.schedule.map(meetingProblem).find(Boolean) || (!c.teacher_ids.length ? 'Pick at least one teacher.' : null);
  return (
    <form className="ds-editor cl-editor" data-testid="class-editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ name: c.name, classroom: c.classroom, teacher_ids: c.teacher_ids, domain_id: c.domain_id || null,
          student_ids: c.student_ids, schedule: c.schedule.map((m) => ({ ...m, minutes: Number(m.minutes) })) });
      }}>
      <div className="cl-grid">
        <label className="ds-field"><span>Class name</span>
          <input name="name" type="text" maxLength={80} value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></label>
        <label className="ds-field"><span>Classroom</span>
          <select name="classroom" value={c.classroom} onChange={(e) => setC({ ...c, classroom: e.target.value })}>
            {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
          </select></label>
        <label className="ds-field"><span>Educational domain</span>
          <select name="domain_id" value={c.domain_id} onChange={(e) => setC({ ...c, domain_id: e.target.value })}>
            <option value="">— none —</option>
            {(data.domains || []).map((d) => <option key={d.id} value={d.id}>{d.name}{d.methodology ? ` (${d.methodology})` : ''}</option>)}
          </select></label>
      </div>
      <fieldset className="cl-pick" data-testid="class-teachers">
        <legend>Teacher(s)</legend>
        {(data.staff || []).map((s) => (
          <label key={s.id}><input type="checkbox" checked={c.teacher_ids.includes(s.id)} onChange={() => toggle('teacher_ids', s.id)} />
            {s.name} <span className="muted">· {s.role}</span></label>
        ))}
      </fieldset>
      <fieldset className="cl-pick" data-testid="class-students">
        <legend>Students ({c.student_ids.length}) <label className="muted cl-all"><input type="checkbox" checked={allStudents}
          onChange={() => setAllStudents(!allStudents)} /> show every classroom</label></legend>
        {students.map((s) => (
          <label key={s.student_id}><input type="checkbox" checked={c.student_ids.includes(s.student_id)}
            onChange={() => toggle('student_ids', s.student_id)} />{s.name}</label>
        ))}
      </fieldset>
      <fieldset className="cl-schedule" data-testid="class-schedule">
        <legend>Schedule</legend>
        {c.schedule.map((m, i) => (
          <Meeting key={i} m={m} onChange={(nm) => setC({ ...c, schedule: c.schedule.map((x, j) => (j === i ? nm : x)) })}
            onRemove={c.schedule.length > 1 ? () => setC({ ...c, schedule: c.schedule.filter((_, j) => j !== i) }) : null} />
        ))}
        {c.schedule.length < 10 ? (
          <button type="button" className="link" data-testid="add-meeting"
            onClick={() => setC({ ...c, schedule: [...c.schedule, { days: [], start: c.schedule[0]?.start || '08:00', minutes: 30 }] })}>
            + Add another meeting time</button>
        ) : null}
      </fieldset>
      {problem ? <p className="hint">{problem}</p> : null}
      {error ? <p className="pl-error" role="alert">{error}</p> : null}
      <div className="ds-actions">
        <button type="submit" className="btn primary small" disabled={busy || !c.name.trim() || !!problem}>Save</button>
        <button type="button" className="btn small" onClick={onCancel} disabled={busy}>Cancel</button>
        {onRemove ? <button type="button" className="btn danger small" onClick={onRemove} disabled={busy}
          data-testid="remove-class">Delete class</button> : null}
      </div>
    </form>
  );
}

export function ClassTable({ rows, editing = null, onEdit = null, children = null }) {
  return (
    <table className="pipeline-table class-table" data-testid="class-table">
      <thead>
        <tr><th>Class</th><th>Classroom</th><th>Teacher(s)</th><th>Domain</th><th>Students</th><th>Schedule</th><th /></tr>
      </thead>
      <tbody>
        {rows.map((c) => (editing === c.id ? (
          <tr key={c.id}><td colSpan={7}>{children}</td></tr>
        ) : (
          <tr key={c.id} data-testid="class-row">
            <td><strong>{c.name}</strong></td>
            <td>{c.classroom}</td>
            <td>{c.teachers.map((t) => t.name).join(', ')}</td>
            <td>{c.domain_name ? <>{c.domain_name}{c.domain_methodology ? <div className="muted small">{c.domain_methodology}</div> : null}</>
              : <span className="muted">—</span>}</td>
            <td title={c.students.map((s) => s.name).join(', ')}>
              {c.students.length}{c.students.length ? <div className="muted small cl-names">{c.students.map((s) => s.name).join(', ')}</div> : null}
            </td>
            <td data-testid="class-schedule-text">{c.schedule_text || <span className="muted">—</span>}</td>
            <td>{c.can_edit && onEdit ? <button type="button" className="btn small" onClick={() => onEdit(c.id)}
              data-testid={`edit-class-${c.id}`}>Edit</button> : null}</td>
          </tr>
        )))}
      </tbody>
    </table>
  );
}

export default function Classes({ reviewerId = null, initialClasses = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const [picked, setPicked] = useState(initialClasses);
  const reload = useCallback(() => loadClasses(reviewerId).then((d) => {
    setData(d);
    setPicked((p) => p ?? (d.default_classroom && d.classrooms.includes(d.default_classroom) ? [d.default_classroom] : []));
  }).catch(setError), [reviewerId]);
  useEffect(() => { reload(); }, [reload]);
  const rows = useMemo(() => {
    const all = data?.classes || [];
    return picked && picked.length ? all.filter((c) => picked.includes(c.classroom)) : all;
  }, [data, picked]);

  async function run(args) {
    setBusy(true);
    setSaveErr(null);
    try {
      setData(await saveClass({ ...args, version: data.version }, reviewerId));
      setEditing(null);
    } catch (e) {
      setSaveErr(setupError(e));
      if (e.message === 'STALE_VERSION') reload();
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="pl-error" role="alert">Couldn’t load classes ({error.message}).</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const current = (data.classes || []).find((c) => c.id === editing);
  const editor = (
    <ClassEditor key={editing || 'none'} data={data} initial={current} busy={busy} error={saveErr} admin={data.can_add}
      onCancel={() => { setEditing(null); setSaveErr(null); }}
      onSave={(klass) => run({ id: current ? current.id : null, klass })}
      onRemove={current && data.can_add ? () => {
        if (window.confirm(`Delete “${current.name}”?`)) run({ id: current.id, remove: true });
      } : null} />
  );
  return (
    <section data-testid="classes">
      <div className="pipeline-filters">
        <MultiFilter label="Classroom" options={data.classrooms} selected={picked || []} onChange={setPicked} />
        <button type="button" className="filter-reset" onClick={() => setPicked([])} disabled={!(picked || []).length}>Reset to All</button>
        {data.can_add ? <button type="button" className="btn primary small" onClick={() => { setEditing('new'); setSaveErr(null); }}
          disabled={editing === 'new'} data-testid="add-class">Add class</button> : null}
      </div>
      {(picked || []).length ? <p className="muted filter-count">Showing {rows.length} of {data.classes.length} classes</p> : null}
      {editing === 'new' ? <div className="ds-new">{editor}</div> : null}
      <ClassTable rows={rows} editing={editing} onEdit={(id) => { setEditing(id); setSaveErr(null); }}>{editor}</ClassTable>
      {!rows.length ? <p className="muted">No classes.</p> : null}
      {data.label ? (
        <p className="hint" data-testid="classes-fictional-note">
          <FictionalLabel label={data.label} /> Every class, teacher and student here is invented for the demo.
        </p>
      ) : null}
      <p className="hint">
        {data.can_add ? 'Admins add, edit and delete classes. ' : 'Teachers edit the classes they teach; admins manage the rest. '}
        Each change is in the Activity Log. A recording picks its class on the session page (suggested from its
        classroom and time), and materials use the class’s domain.
      </p>
    </section>
  );
}
