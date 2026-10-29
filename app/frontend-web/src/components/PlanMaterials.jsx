import React, { useEffect, useState } from 'react';
import { downloadPlanMaterial, makePlanMaterial, planMaterialAction } from '../dataStore.js';
import { apiErrorText } from '../labels.js';
import {
  POLL_MS, STYLE_LABEL, UNIT, countMax, emailStatusText, formFrom, makingText, plural,
} from '../materials.js';

/**
 * Worksheets and other printable materials attached to one week's lesson plan (David, Oct 10; tasks#99).
 * "Create worksheet" uses the same Make flow as a session's Teacher Notes (same renderers, admin
 * instructions, name masking, journal and email-to-teacher); the result is stored with the lesson plan.
 * Materials made from a session can be added here too (session Materials card: "Add to lesson plan").
 */
export const DAYS = [['', 'Any day'], ['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday']];
const DAY = Object.fromEntries(DAYS);

export function lessonText(l) {
  if (!l) return '';
  return [l.day ? DAY[l.day] : '', l.label || ''].filter(Boolean).join(' · ');
}

export default function PlanMaterials({
  classCode, week, info, items = [], readOnly = false, reviewerId = null, onChanged = () => {},
  api = { makePlanMaterial, planMaterialAction, downloadPlanMaterial }, pollMs = POLL_MS,
}) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [list, setList] = useState(items);
  useEffect(() => { setList(items); }, [items]);
  const where = { class_code: classCode, week };
  const can = !!info?.can_make && !readOnly;
  const view = { types: info?.types || [], default_student_ids: [] };
  const tinfo = form ? view.types.find((t) => t.type === form.type) : null;

  const making = list.filter((m) => m.status === 'making').map((m) => m.material_id).join(',');
  useEffect(() => {
    if (!making || !can) return undefined;
    let stop = false;
    const tick = async () => {
      for (const mid of making.split(',')) {
        try {
          const r = await api.planMaterialAction(mid, 'continue', where, reviewerId);
          if (stop) return;
          setList((l) => l.map((x) => (x.material_id === mid ? r.material : x)));
        } catch (e) { /* next tick */ }
      }
    };
    const id = setInterval(tick, pollMs);
    tick();
    return () => { stop = true; clearInterval(id); };
  }, [making]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn, done, after = true) {
    setBusy(true); setMsg('');
    try {
      const r = await fn();
      setMsg(done(r));
      if (after) await onChanged();
    } catch (e) { setMsg(apiErrorText(e.message)); } finally { setBusy(false); }
  }

  function open(extra = {}) {
    setForm({ day: '', label: '', class_id: (info?.classes || []).length === 1 ? info.classes[0].id : '',
      ...formFrom({ type: 'worksheet' }, view), student_ids: [], ...extra });
  }

  async function submit(e) {
    e.preventDefault();
    const { day, label, class_id: cid, ...rest } = form;
    const body = { ...where, ...rest, topic: rest.topic.trim(), due: rest.due || null,
      lesson: day || label.trim() ? { day: day || null, label: label.trim() } : null,
      ...(cid ? { class_id: cid } : {}) };
    if (body.type !== 'flashcards') delete body.style;
    if (body.regenerate_of == null) delete body.regenerate_of;
    await run(() => api.makePlanMaterial(body, reviewerId), (r) => {
      setForm(null);
      return r.material.status === 'making' ? `Started: ${r.material.title}. ${makingText(r.material)}`
        : `Added to the plan: ${r.material.title}. ${emailStatusText(r.email.status, r.email.to)}.`;
    });
  }

  const toggle = (id) => setForm((f) => {
    const has = f.student_ids.includes(id);
    if (!has && f.student_ids.length >= 3) return f;
    return { ...f, student_ids: has ? f.student_ids.filter((x) => x !== id) : [...f.student_ids, id] };
  });

  return (
    <div className="pl-materials" data-testid="plan-materials" data-week={week}>
      <div className="pl-sub">Worksheets &amp; materials{list.length ? <span className="pl-versions">{list.length}</span> : null}</div>
      {list.length === 0 && !form ? <p className="muted">None attached yet.</p> : null}
      <ul className="pl-material-list">
        {list.map((m) => (
          <li key={m.material_id} className="pl-material" data-testid="plan-material">
            {m.preview_png ? (
              <img className="pl-material-thumb" alt="" src={`data:image/png;base64,${m.preview_png}`} />
            ) : null}
            <div className="pl-material-info">
              <strong>{m.title}</strong>
              <span className="muted">
                {m.type_label}{m.type === 'flashcards' && m.style && m.style !== 'line' ? ` (${STYLE_LABEL[m.style]})` : ''}
                {' · '}{m.type === 'multiplication_table' ? `up to ${m.count}` : plural(m.count, UNIT[m.type] || 'item')}
                {m.students?.length ? ` · for ${m.students.join(', ')}` : ''}
              </span>
              {m.lesson ? <span className="pl-lesson" data-testid="plan-material-lesson">{lessonText(m.lesson)}</span> : null}
              {m.attached_from ? <span className="hint">From a session’s Teacher Note</span> : null}
              {m.status === 'making' ? <span className="material-making" role="status">{makingText(m)}</span> : null}
              {can && m.status !== 'making' ? (
                <span className="pl-links">
                  <button type="button" className="link" disabled={busy} data-testid="plan-material-download"
                    onClick={() => run(() => api.downloadPlanMaterial(m.material_id, where, reviewerId),
                      () => 'PDF downloaded to this computer.', false)}>Download</button>
                  {!m.attached_from ? (
                    <button type="button" className="link" disabled={busy} data-testid="plan-material-regenerate"
                      onClick={() => open({ ...formFrom({ ...m }, view), student_ids: m.student_ids || [],
                        day: m.lesson?.day || '', label: m.lesson?.label || '', class_id: m.class_id || '',
                        regenerate_of: m.material_id })}>Regenerate</button>
                  ) : null}
                  {info?.email?.enabled ? (
                    <button type="button" className="link" disabled={busy}
                      onClick={() => run(() => api.planMaterialAction(m.material_id, 'resend', where, reviewerId),
                        (r) => `${emailStatusText(r.email.status, r.email.to)}.`)}>Email me</button>
                  ) : null}
                  <button type="button" className="link danger" disabled={busy} data-testid="plan-material-remove"
                    onClick={() => run(() => api.planMaterialAction(m.material_id, 'remove', where, reviewerId),
                      () => 'Removed from this week’s plan.')}>Remove</button>
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {can && !form ? (
        <button type="button" className="btn small ghost-line" onClick={() => open()} data-testid="plan-create-worksheet">
          + Create worksheet
        </button>
      ) : null}
      {form && tinfo ? (
        <form className="pl-form" onSubmit={submit} data-testid="plan-material-form">
          <div className="card-row">
            <label>Day
              <select value={form.day} disabled={busy} onChange={(e) => setForm({ ...form, day: e.target.value })}
                data-testid="plan-material-day">
                {DAYS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label>Lesson <input type="text" maxLength={60} value={form.label} disabled={busy} placeholder="e.g. Math block"
              onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
          </div>
          {(info?.classes || []).length ? (
            <label>Class <span className="hint">(its subject and teaching approach guide the AI)</span>
              <select value={form.class_id} disabled={busy} onChange={(e) => setForm({ ...form, class_id: e.target.value })}>
                <option value="">None</option>
                {info.classes.map((c) => <option key={c.id} value={c.id}>{c.name}{c.domain_name ? ` · ${c.domain_name}` : ''}</option>)}
              </select>
            </label>
          ) : null}
          <div className="card-row">
            <label>Type
              <select value={form.type} disabled={busy} data-testid="plan-material-type"
                onChange={(e) => setForm({ ...form, ...formFrom({ type: e.target.value, count: null }, view), student_ids: form.student_ids })}>
                {view.types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
              </select>
            </label>
            {tinfo.styles ? (
              <label>Style
                <select value={form.style || 'line'} disabled={busy}
                  onChange={(e) => setForm({ ...form, style: e.target.value, count: Math.min(form.count, countMax(tinfo, e.target.value)) })}>
                  {tinfo.styles.map((st) => <option key={st.style} value={st.style}>{st.label}</option>)}
                </select>
              </label>
            ) : null}
            <label>{form.type === 'multiplication_table' ? 'Up to (×)' : `${(UNIT[form.type] || 'item').replace(/^./, (c) => c.toUpperCase())}s`}
              <input type="number" min={tinfo.min} max={countMax(tinfo, form.style)} value={form.count} disabled={busy}
                onChange={(e) => setForm({ ...form, count: Math.min(countMax(tinfo, form.style), Math.max(tinfo.min, Number(e.target.value) || tinfo.min)) })} />
            </label>
          </div>
          <label>Details <span className="hint">(topic, e.g. “farm animals”, “the 3s and 4s”; 60 characters)</span>
            <input type="text" maxLength={60} value={form.topic} disabled={busy} data-testid="plan-material-topic"
              onChange={(e) => setForm({ ...form, topic: e.target.value })} />
          </label>
          {(info?.students || []).length ? (
            <fieldset className="material-students">
              <legend>Student(s) <span className="hint">(optional, up to 3)</span></legend>
              {info.students.map((s) => (
                <label key={s.student_id} className="check">
                  <input type="checkbox" checked={form.student_ids.includes(s.student_id)} disabled={busy}
                    onChange={() => toggle(s.student_id)} />{s.first_name} {s.last_initial}
                </label>
              ))}
            </fieldset>
          ) : null}
          <p className="pl-hint">
            Names are replaced with “the student” before the AI sees anything.
            {info?.email?.enabled && info?.email?.has_address ? ` It’s also emailed to you (${info.email.to}).` : ''}
          </p>
          <div className="pl-actions">
            <button type="button" className="btn small" disabled={busy} onClick={() => setForm(null)}>Cancel</button>
            <button type="submit" className="btn small primary" disabled={busy} data-testid="plan-material-make">
              {busy ? 'Making… (up to a minute)' : form.regenerate_of ? 'Make a new version' : 'Make it'}
            </button>
          </div>
        </form>
      ) : null}
      {msg ? <p className="toast-inline" role="status">{msg}</p> : null}
    </div>
  );
}
