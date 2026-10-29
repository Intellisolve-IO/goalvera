import React, { useEffect, useState } from 'react';
import {
  attachMaterial, continueMaterial, downloadMaterial, fetchMaterials, makeMaterial, resendMaterial,
} from '../dataStore.js';
import { apiErrorText } from '../labels.js';
import MaterialsInstruction from './MaterialsInstruction.jsx';
import {
  POLL_MS, STYLE_LABEL, UNIT, countMax, dueLabel, emailStatusText, formFrom, makingText, mmss, plural, suggestionText,
} from '../materials.js';

/**
 * Teacher Notes and classroom materials (David, Oct 10 2026; tasks#99). When a Teacher Note asks
 * for printable materials, the server suggests up to 3 actions; the teacher picks one, checks the
 * details, and clicks Make it. Nothing is made or emailed before that. The server enforces who
 * may do what (teacher of the session or admin; RBTs read only) and masks names before the AI.
 */
export default function MaterialsPanel({
  recordingId, reviewerId = null, initial = null,
  api = { fetchMaterials, makeMaterial, resendMaterial, downloadMaterial, continueMaterial, attachMaterial }, pollMs = POLL_MS,
}) {
  const [view, setView] = useState(initial);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);          // {note_index, type, count, topic, student_ids, due, regenerate_of}
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [showInstr, setShowInstr] = useState(false);

  function reload() {
    return api.fetchMaterials(recordingId, reviewerId).then(setView).catch((e) => setError(e));
  }
  useEffect(() => {
    if (initial) return;
    setView(null); setForm(null); setMsg('');
    reload();
  }, [recordingId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Photo-style sets are made a picture at a time (the image model allows ~2 a minute): while one is
  // being made and this session is open, ask the server to continue every POLL_MS.
  const making = ((view && view.materials) || []).filter((m) => m.status === 'making').map((m) => m.material_id);
  const makingKey = making.join(',');
  useEffect(() => {
    if (!makingKey || !api.continueMaterial) return undefined;
    let stop = false;
    const tick = async () => {
      for (const mid of makingKey.split(',')) {
        try {
          const r = await api.continueMaterial(recordingId, mid, reviewerId);
          if (stop) return;
          setView((v) => (v ? { ...v, materials: v.materials.map((x) => (x.material_id === mid ? r.material : x)) } : v));
          if (r.material.status === 'ready') {
            setMsg(`Ready: ${r.material.title}.${r.email ? ` ${emailStatusText(r.email.status, r.email.to)}.` : ''}`);
          }
        } catch (err) { /* try again on the next tick */ }
      }
    };
    const id = setInterval(tick, pollMs);
    tick();
    return () => { stop = true; clearInterval(id); };
  }, [makingKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return null;                            // no materials API (laptop fixtures): show nothing
  if (!view || !view.notes || (view.notes.length === 0 && (view.materials || []).length === 0)) return null;

  const types = view.types || [];
  const tinfo = form ? types.find((t) => t.type === form.type) : null;
  const email = view.email || {};

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setMsg('');
    try {
      const body = { ...form, topic: form.topic.trim(), due: form.due || null };
      if (body.regenerate_of == null) delete body.regenerate_of;
      if (body.type !== 'flashcards') delete body.style;
      const r = await api.makeMaterial(recordingId, body, reviewerId);
      setForm(null);
      setMsg(r.material.status === 'making'
        ? `Started: ${r.material.title}. ${makingText(r.material)}`
        : `Made: ${r.material.title}. ${emailStatusText(r.email.status, r.email.to)}.`);
      await reload();
    } catch (err) {
      setMsg(apiErrorText(err.message));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn, done) {
    setBusy(true); setMsg('');
    try {
      const r = await fn();
      setMsg(done(r));
      await reload();
    } catch (err) {
      setMsg(apiErrorText(err.message));
    } finally {
      setBusy(false);
    }
  }

  function toggleStudent(id) {
    setForm((f) => {
      const has = f.student_ids.includes(id);
      if (!has && f.student_ids.length >= 3) return f;
      return { ...f, student_ids: has ? f.student_ids.filter((x) => x !== id) : [...f.student_ids, id] };
    });
  }

  const emailLine = !email.enabled ? 'Email delivery is off for this school: download only.'
    : email.has_address ? `When it’s made, it’s also emailed to your staff address (${email.to}).`
      : 'No staff email on file for this classroom’s teacher: download only.';

  return (
    <section className="materials" data-testid="materials">
      <h3>Teacher Notes and materials</h3>
      {view.can_make && (
        <p className="hint">
          When a Teacher Note asks for printable materials, pick a suggestion, check the details and click
          Make it. Nothing is made or emailed until you do. Materials are AI suggestions: look them over before use.
        </p>
      )}
      {view.can_make && view.teaching_approach ? (
        // the class's domain + methodology go into the AI prompt (Domains tab; tasks#103)
        <p className="hint" data-testid="teaching-approach">
          Designed for <strong>{view.teaching_approach.domain}</strong> ({view.teaching_approach.class_name}):{' '}
          {view.teaching_approach.methodology}
        </p>
      ) : null}
      <ul className="material-notes">
        {view.notes.map((n) => (
          <li key={n.index} className="material-note" data-testid="material-note">
            <span className="material-note-time">Teacher Note at {mmss(n.offset_seconds)}</span>
            {n.pending ? null : n.is_request ? (
              view.can_make ? (
                <span className="material-suggestions">
                  {n.suggestions.map((s, i) => (
                    <button key={i} type="button" className="btn ghost-line small" data-testid="material-suggestion"
                      disabled={busy} onClick={() => setForm({ note_index: n.index, ...formFrom(s, view) })}>
                      {suggestionText(s)}
                    </button>
                  ))}
                  <button type="button" className="btn ghost small" disabled={busy} data-testid="material-other"
                    onClick={() => setForm({ note_index: n.index, ...formFrom({ type: 'worksheet' }, view) })}>
                    Something else…
                  </button>
                </span>
              ) : <span className="muted"> · asks for materials</span>
            ) : <span className="muted"> · no materials request</span>}
          </li>
        ))}
      </ul>

      {form && tinfo && (
        <form className="material-form card" onSubmit={submit} data-testid="material-form">
          <div className="card-row">
            <label>
              Type
              <select value={form.type} disabled={busy} data-testid="material-type"
                onChange={(e) => setForm(formFrom({ ...form, type: e.target.value, count: null }, view,
                  { note_index: form.note_index, regenerate_of: form.regenerate_of }))}>
                {types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
              </select>
            </label>
            {tinfo.styles && (
              <label>
                Style
                <select value={form.style || 'line'} disabled={busy} data-testid="material-style"
                  onChange={(e) => setForm({ ...form, style: e.target.value,
                    count: Math.min(form.count, countMax(tinfo, e.target.value)) })}>
                  {tinfo.styles.map((st) => <option key={st.style} value={st.style}>{st.label}</option>)}
                </select>
              </label>
            )}
            <label>
              {form.type === 'multiplication_table' ? 'Up to (×)'
                : `${UNIT[form.type][0].toUpperCase() + UNIT[form.type].slice(1)}s per page`}
              <input type="number" min={tinfo.min} max={countMax(tinfo, form.style)} value={form.count} disabled={busy}
                data-testid="material-count"
                onChange={(e) => setForm({ ...form, count: Math.min(countMax(tinfo, form.style), Math.max(tinfo.min, Number(e.target.value) || tinfo.min)) })} />
            </label>
            <label>
              For (date)
              <input type="date" value={form.due || ''} disabled={busy} data-testid="material-due"
                onChange={(e) => setForm({ ...form, due: e.target.value })} />
            </label>
          </div>
          <label>
            Topic <span className="hint">(e.g. trains, colors, classroom supplies; 60 characters max)</span>
            <input type="text" maxLength={60} value={form.topic} disabled={busy} data-testid="material-topic"
              onChange={(e) => setForm({ ...form, topic: e.target.value })} />
          </label>
          <fieldset className="material-students">
            <legend>Student(s) <span className="hint">(up to 3; none = the whole classroom)</span></legend>
            {(view.students || []).map((s) => (
              <label key={s.student_id} className="check">
                <input type="checkbox" checked={form.student_ids.includes(s.student_id)} disabled={busy}
                  onChange={() => toggleStudent(s.student_id)} />
                {s.first_name} {s.last_initial}
              </label>
            ))}
          </fieldset>
          {form.style === 'photo' && form.type === 'flashcards' && (
            <p className="hint" data-testid="material-photo-note">
              Photo-style pictures are made one at a time and may take a few minutes (about {Math.ceil(form.count * 0.6)} min
              for {plural(form.count, 'card')}). Keep the session open; it’s emailed when it’s ready.
            </p>
          )}
          <p className="hint">{emailLine}</p>
          <div className="card-row">
            <button type="submit" className="btn primary" disabled={busy} data-testid="material-make">
              {makeLabel(form, tinfo, view, busy)}
            </button>
            <button type="button" className="btn ghost" disabled={busy} onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      )}

      {form && (
        <details className="material-instruction-view" onToggle={(e) => setShowInstr(e.currentTarget.open)}>
          <summary>What the AI is told for this type</summary>
          {showInstr && <MaterialsInstruction reviewerId={reviewerId} only={form.type === 'flashcards' ? `flashcards_${form.style || 'line'}` : form.type} />}
        </details>
      )}

      {msg && <p className="toast-inline" role="status" data-testid="material-msg">{msg}</p>}

      {(view.materials || []).length > 0 && (
        <ul className="material-items">
          {view.materials.map((m) => {
            const last = (m.emails || [])[m.emails.length - 1];
            return (
              <li key={m.material_id} className="material-item card" data-testid="material-item">
                <div className="material-info">
                  <strong>{m.title}</strong>
                  <div className="muted">
                    {m.type_label}{m.type === 'flashcards' && m.style && m.style !== 'line' ? ` (${STYLE_LABEL[m.style]})` : ''}
                    {' · '}{m.type === 'multiplication_table' ? `up to ${m.count}` : `${plural(m.count, UNIT[m.type] || 'item')}${m.renderer === 'photo' ? '' : ' per page'}`}
                    {m.topic ? ` · ${m.topic}` : ''}
                    {m.students.length ? ` · for ${m.students.join(', ')}` : ''}
                    {m.due ? ` · for ${dueLabel(m.due)}` : ''}
                  </div>
                  <div className="hint">
                    Made {m.created_at ? new Date(m.created_at).toLocaleString() : ''}
                    {m.regenerate_of ? ' · new version' : ''}
                    {last ? ` · ${emailStatusText(last.status)}${last.resend ? ' (resent)' : ''}` : ''}
                  </div>
                  {m.in_plan_week ? (
                    <div className="material-in-plan" data-testid="material-in-plan">
                      In the lesson plan · week of {dueLabel(m.in_plan_week)}
                    </div>
                  ) : null}
                  {m.status === 'making' && (
                    <div className="material-making" role="status" data-testid="material-making">{makingText(m)}</div>
                  )}
                  <div className="material-drafts">
                    {(m.drafts || []).map((d) => (
                      <figure key={`${d.draft}-${d.page || 0}`} className="material-draft" data-testid="material-draft">
                        {d.preview_png && (
                          <img className="material-preview" alt={d.page ? `Page ${d.page}` : `Draft ${d.draft}`}
                            src={`data:image/png;base64,${d.preview_png}`} />
                        )}
                        <figcaption>
                          <span>{d.page ? `Page ${d.page} of ${d.pages}` : `Draft ${d.draft}`}</span>
                          {d.variation && <span className="hint"> · {d.variation}</span>}
                          {view.can_make && m.status !== 'making' && m.renderer !== 'photo' && (m.drafts || []).length > 1 && (
                            <button type="button" className="btn ghost-line small" disabled={busy}
                              data-testid="material-download-draft"
                              onClick={() => act(() => api.downloadMaterial(recordingId, m.material_id, reviewerId, d.draft),
                                () => `Draft ${d.draft} downloaded to this computer.`)}>
                              Download
                            </button>
                          )}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
                {view.can_make && m.status !== 'making' && (
                  <div className="material-actions">
                    <button type="button" className="btn ghost-line small" disabled={busy} data-testid="material-download"
                      onClick={() => act(() => api.downloadMaterial(recordingId, m.material_id, reviewerId),
                        () => (m.renderer !== 'photo' && (m.drafts || []).length > 1 ? 'PDF with all drafts downloaded to this computer.' : 'PDF downloaded to this computer.'))}>
                      {m.renderer !== 'photo' && (m.drafts || []).length > 1 ? 'Download all' : 'Download'}
                    </button>
                    {email.enabled && (
                      <button type="button" className="btn ghost small" disabled={busy} data-testid="material-resend"
                        onClick={() => act(() => api.resendMaterial(recordingId, m.material_id, reviewerId),
                          (r) => `${emailStatusText(r.email.status, r.email.to)}.`)}>
                        Resend
                      </button>
                    )}
                    {view.plan_week && !m.in_plan_week && api.attachMaterial ? (
                      <button type="button" className="btn ghost-line small" disabled={busy} data-testid="material-add-to-plan"
                        onClick={() => act(() => api.attachMaterial(recordingId, m.material_id, {}, reviewerId),
                          (r) => `Added to the lesson plan for ${r.class_code}, week of ${dueLabel(r.week)}.`)}>
                        Add to lesson plan
                      </button>
                    ) : null}
                    <button type="button" className="btn ghost small" disabled={busy} data-testid="material-regenerate"
                      onClick={() => setForm({ note_index: m.note_index, regenerate_of: m.material_id,
                        ...formFrom({ ...m, student_ids: m.student_ids }, view) })}>
                      Regenerate
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function makeLabel(form, tinfo, view, busy) {
  const st = tinfo.styles && (tinfo.styles.find((x) => x.style === (form.style || 'line')) || {});
  const renderer = (st && st.renderer) || tinfo.renderer || 'svg';
  if (renderer === 'photo') return busy ? 'Starting…' : 'Make photo-style cards (a few minutes)';
  const n = view.drafts || 3;
  const what = renderer === 'template' ? 'layouts' : 'drafts';
  if (busy) return renderer === 'template' ? `Making ${n} ${what}…` : `Making ${n} ${what}… (up to a minute)`;
  return form.regenerate_of ? `Make new ${what}` : `Make ${n} ${what}`;
}
