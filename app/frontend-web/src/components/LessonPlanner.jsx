import React, { useCallback, useEffect, useReducer, useState } from 'react';
import { fetchPlanMaterials, loadPlanner, savePlanner } from '../dataStore.js';
import PlanMaterials from './PlanMaterials.jsx';
import TextBox from './TextBox.jsx';
import { studentWeekHref } from '../routes.js';
import {
  IEP_STATUS, canGoBack, canGoForward, initialNav, navReducer, slots, weekRange,
} from '../plannerWeeks.js';

// Weekly lesson planner (). Three columns: last week (the arrows step it back
// through earlier weeks), this week (anchored, never moves) and next week. Each week:
// lesson plan summary (goals + objectives as bullets), the week's plan, then one card per
// student with the weekly summary, IEP check and teacher note. The server enforces who
// may save; the screen only hides what the server would refuse.

const ERRORS = {
  QUOTES_TRANSCRIPT: 'This repeats 8 or more words in a row from a session transcript. Put it in your own words.',
  READ_ONLY_ROLE: 'Your role can read the planner but not change it.',
  NOT_YOUR_STUDENT: 'Only this student’s teachers (or an admin) can change this.',
  NOT_YOUR_CLASSROOM: 'Only this class’s teacher (or an admin) can change the plan.',
  EMPTY_PLAN: 'Add at least one goal, objective or plan line.',
  BAD_SUMMARY: 'Write a short summary first.',
  BAD_PARENT_EMAIL: 'The parent email is too long.',
  REVIEWER_REQUIRED: 'Sign in to save.',
  AI_DRAFT_QUOTES_TRANSCRIPT: 'The AI draft repeated words from a transcript, so it was thrown away. Try again or write your own.',
  NOTHING_TO_DRAFT_FROM: 'Nothing to draft from yet: no approved session notes or teacher notes for this week.',
  AI_DRAFTS_OFF: 'AI drafts are switched off here.',
  AI_UNAVAILABLE: 'The AI didn’t answer. Try again in a minute.',
  AI_BAD_REPLY: 'The AI answer couldn’t be used. Try again.',
  TOO_MANY_DRAFTS: 'Too many drafts in a short time. Wait a few minutes.',
};
export const errText = (e) => ERRORS[e.code || e.message] || `Couldn’t save (${e.code || e.message}).`;
const lines = (s) => s.split('\n').map((x) => x.trim()).filter(Boolean);
/** Rows for a one-item-per-line box: about two wrapped lines per item at column width. */
const fit = (v) => Math.min(12, Math.max(3, lines(v).length * 2 + 1));
const AI_SOURCE = { offline_stand_in: 'offline stand-in, no AI call', gemini_live: 'Gemini', vertex_gemini_live: 'Gemini (Vertex AI)' };

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** What an AI draft was built from, in words. */
export function draftSources(d) {
  const b = d?.based_on || {};
  if (d?.based_on_week) {
    const parts = [];
    if (b.summaries) parts.push(plural(b.summaries, 'weekly summary').replace('summarys', 'summaries'));
    if (b.previous_plan) parts.push('the plan');
    if (b.teacher_notes) parts.push(plural(b.teacher_notes, 'teacher note'));
    return `the week of ${weekRange(d.based_on_week)}: ${parts.join(', ') || 'nothing saved'}, plus the IEP goals`;
  }
  const parts = [];
  if (b.approved_sessions) parts.push(plural(b.approved_sessions, 'approved session note'));
  if (b.teacher_notes) parts.push(plural(b.teacher_notes, 'teacher note'));
  return `${parts.join(' and ')}, checked against the IEP goals`;
}

export function AiDraftNote({ draft }) {
  if (!draft?.ai) return null;
  return (
    <p className="pl-ai-note" data-testid="ai-draft-note">
      <span className="pl-pill ai">AI draft · not saved</span>
      <span>Check and edit it, then save. Built from {draftSources(draft)}.</span>
      <span className="muted">Drafted by {AI_SOURCE[draft.ai.source] || draft.ai.source}. Names were replaced
        with “the student” before drafting; transcripts are never sent.</span>
    </p>
  );
}

export function Status({ status }) {
  if (!status) return <span className="pl-pill none">No plan yet</span>;
  return <span className={`pl-pill ${status}`}>{status === 'approved' ? 'Approved' : 'Draft'}</span>;
}

export function SaveError({ error }) {
  if (!error) return null;
  return (
    <p className="pl-error" role="alert" data-testid="planner-error">
      {errText(error)}
      {error.match ? <> Matching words: <q>{error.match}</q></> : null}
    </p>
  );
}

function PlanEditor({ plan, onSave, onCancel, onDraft = null, autoDraft = false }) {
  const [goals, setGoals] = useState((plan?.goals || []).join('\n'));
  const [objectives, setObjectives] = useState((plan?.objectives || []).join('\n'));
  const [text, setText] = useState(plan?.plan || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null);
  async function save(approve) {
    setBusy(true);
    setError(null);
    try {
      await onSave({ goals: lines(goals), objectives: lines(objectives), plan: text, approve, ...(draft ? { ai: draft.ai } : {}) });
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  async function aiDraft() {
    setBusy(true);
    setError(null);
    try {
      const d = await onDraft();
      setGoals(d.goals.join('\n'));
      setObjectives(d.objectives.join('\n'));
      setDraft(d);
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  }
  useEffect(() => { if (autoDraft && onDraft) aiDraft(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="pl-form" data-testid="plan-editor">
      {draft ? <AiDraftNote draft={draft} /> : null}
      {onDraft ? (
        <button type="button" className="btn small ai-btn" onClick={aiDraft} disabled={busy} data-testid="plan-ai-draft">
          {draft ? 'Draft again with AI' : 'Draft goals & objectives with AI'}
        </button>
      ) : null}
      <label>Goals <span className="muted">(one per line)</span>
        <TextBox rows={fit(goals)} value={goals} onValue={setGoals} name="goals" /></label>
      <label>Objectives <span className="muted">(one per line)</span>
        <TextBox rows={fit(objectives)} value={objectives} onValue={setObjectives} name="objectives" /></label>
      <label>The week’s plan
        <TextBox rows={4} value={text} onValue={setText} name="plan" /></label>
      <p className="pl-hint">Write in your own words; don’t paste lines from transcripts.</p>
      <SaveError error={error} />
      <div className="pl-actions">
        <button type="button" className="btn small" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="btn small ghost-line" onClick={() => save(false)} disabled={busy}>Save draft</button>
        <button type="button" className="btn small primary" onClick={() => save(true)} disabled={busy}>Save &amp; approve</button>
      </div>
    </div>
  );
}

export function SummaryEditor({ student, start, onSave, onCancel, draft = null }) {
  const [text, setText] = useState(start?.summary || '');
  const [email, setEmail] = useState(start?.parent_email || '');
  const [check, setCheck] = useState(() =>
    Object.fromEntries(student.iep_goals.map((g) => {
      const c = (start?.iep_check || []).find((x) => x.goal_id === g.goal_id) || {};
      return [g.goal_id, { status: c.status || 'not_enough', note: c.note || '' }];
    })));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function save(approve) {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        summary: text, approve, parent_email: email,
        iep_check: student.iep_goals.map((g) => ({ goal_id: g.goal_id, ...check[g.goal_id] })),
        ...(draft ? { ai: draft.ai } : {}),
      });
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  return (
    <div className="pl-form" data-testid="summary-editor">
      {draft ? <AiDraftNote draft={draft} /> : null}
      <label>Weekly summary
        <TextBox rows={4} value={text} onValue={setText} name="summary" /></label>
      {student.iep_goals.map((g) => (
        <div className="pl-check-row" key={g.goal_id}>
          <span className="pl-goal-tag">{g.category || g.goal_id}</span>
          <select value={check[g.goal_id].status} aria-label={`IEP check ${g.goal_id}`}
            onChange={(e) => setCheck({ ...check, [g.goal_id]: { ...check[g.goal_id], status: e.target.value } })}>
            {Object.entries(IEP_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <TextBox rows={2} name="iep_note" value={check[g.goal_id].note} placeholder="one-line reason"
            aria-label={`Reason ${g.goal_id}`}
            onValue={(v) => setCheck((c) => ({ ...c, [g.goal_id]: { ...c[g.goal_id], note: v } }))} />
        </div>
      ))}
      <label>Draft parent email <span className="pl-hint">(end of the week; never sent from here)</span>
        <TextBox rows={7} value={email} onValue={setEmail} name="parent_email" data-testid="parent-email-editor" /></label>
      <p className="pl-hint">Your own words; no transcript quotes.</p>
      <SaveError error={error} />
      <div className="pl-actions">
        <button type="button" className="btn small" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="btn small ghost-line" onClick={() => save(false)} disabled={busy}>Save draft</button>
        <button type="button" className="btn small primary" onClick={() => save(true)} disabled={busy}>Save &amp; approve</button>
      </div>
    </div>
  );
}

function NoteEditor({ note, onSave, onCancel }) {
  const [text, setText] = useState(note?.note || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({ note: text });
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  return (
    <div className="pl-form" data-testid="note-editor">
      <label>Teacher note
        <TextBox rows={3} value={text} onValue={setText} name="note" /></label>
      <SaveError error={error} />
      <div className="pl-actions">
        <button type="button" className="btn small" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="btn small primary" onClick={save} disabled={busy}>Save note</button>
      </div>
    </div>
  );
}

// The weekly summary and draft parent email are built once, on the student's page (Chief of
// Staff, Oct 8): the planner links there for this week instead of editing them here.
function StudentCard({ student: st, week, readOnly, onSave }) {
  const [editing, setEditing] = useState(false);
  const approved = st.sessions.filter((s) => s.approved).length;
  const canEdit = st.can_edit && !readOnly;
  return (
    <article className="pl-student" data-testid="student-card" data-student={st.student_id}>
      <header>
        <strong>{st.name}</strong>
        <span className="muted">
          {st.sessions.length ? `${st.sessions.length} session${st.sessions.length === 1 ? '' : 's'} · ${approved} approved` : 'No sessions'}
        </span>
      </header>
      <p className="pl-report-link">
        <a href={studentWeekHref(st.student_id, week)} data-testid="student-report-link">
          Weekly summary &amp; parent report →
        </a>
      </p>
      {editing ? (
        <NoteEditor note={st.note} onCancel={() => setEditing(false)}
          onSave={async (body) => { await onSave('note', { student_id: st.student_id, week, ...body }); setEditing(false); }} />
      ) : (
        <div className="pl-note">
          <div className="pl-sub">Teacher note{st.note_versions > 1 ? <span className="pl-versions">{st.note_versions} versions</span> : null}</div>
          {st.note?.note ? <p data-testid="note-text">{st.note.note}</p> : <p className="muted">No note.</p>}
          {canEdit ? (
            <button type="button" className="link" onClick={() => setEditing(true)}>{st.note ? 'Edit note' : 'Add note'}</button>
          ) : null}
        </div>
      )}
    </article>
  );
}

export function WeekColumn({ slot, data, canEditPlan, readOnly, onSave, nav, dispatch, aiDrafts = 'off', onDraft = null,
  materials = null, classCode = null, reviewerId = null, onMaterialsChanged = () => {} }) {
  const [editing, setEditing] = useState(false); // false | true | 'ai'
  const aiOn = aiDrafts !== 'off' && !!onDraft && !readOnly;
  const planDraftOk = aiOn && slot.week >= nav.thisWeek;
  const plan = data?.plan;
  const isPast = slot.slot === 'past';
  return (
    <section className={`pl-week pl-${slot.slot}`} data-testid={`week-${slot.slot}`} data-week={slot.week}>
      <header className="pl-week-head">
        {isPast ? (
          <button type="button" className="pl-arrow" aria-label="Earlier week" data-testid="week-back"
            onClick={() => dispatch({ type: 'back' })} disabled={!canGoBack(nav)}>‹</button>
        ) : null}
        <div className="pl-week-title">
          <span className="pl-week-name">{slot.title}{slot.slot === 'current' ? <span className="pl-anchor">Anchored</span> : null}</span>
          <span className="pl-week-range" data-testid="week-range">{weekRange(slot.week)}</span>
        </div>
        {isPast ? (
          <button type="button" className="pl-arrow" aria-label="Later week" data-testid="week-forward"
            onClick={() => dispatch({ type: 'forward' })} disabled={!canGoForward(nav)}>›</button>
        ) : null}
      </header>
      {isPast && canGoForward(nav) ? (
        <button type="button" className="link pl-reset" onClick={() => dispatch({ type: 'reset' })}>Back to last week</button>
      ) : null}

      {!data ? <p className="muted pl-wait">Loading…</p> : (<>
      <div className="pl-plan" data-testid="plan-summary">
        <div className="pl-sub">
          Lesson plan summary <Status status={plan?.status} />
          {plan?.ai_assisted ? <span className="pl-pill ai-saved" title="Started from an AI draft, saved by the teacher">AI-assisted</span> : null}
        </div>
        {editing ? (
          <PlanEditor plan={plan} onCancel={() => setEditing(false)} autoDraft={editing === 'ai'}
            onDraft={planDraftOk ? () => onDraft('plan', { week: slot.week }) : null}
            onSave={async (body) => { await onSave('plan', { week: slot.week, ...body }); setEditing(false); }} />
        ) : (
          <>
            {plan?.goals?.length ? (
              <><h4>Goals</h4><ul data-testid="plan-goals">{plan.goals.map((g, i) => <li key={i}>{g}</li>)}</ul></>
            ) : null}
            {plan?.objectives?.length ? (
              <><h4>Objectives</h4><ul data-testid="plan-objectives">{plan.objectives.map((g, i) => <li key={i}>{g}</li>)}</ul></>
            ) : null}
            <h4>The week’s plan</h4>
            {plan?.plan ? <p className="pl-plan-text" data-testid="plan-text">{plan.plan}</p> : <p className="muted">No plan written yet.</p>}
            {canEditPlan && !readOnly ? (
              <div className="pl-links">
                <button type="button" className="link" onClick={() => setEditing(true)}>{plan ? 'Edit plan' : 'Write plan'}</button>
                {planDraftOk ? (
                  <button type="button" className="btn small ai-btn" onClick={() => setEditing('ai')} data-testid="plan-ai-open">
                    AI draft goals &amp; objectives
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </div>

      {materials ? (
        <PlanMaterials classCode={classCode} week={slot.week} info={materials} items={materials.weeks?.[slot.week] || []}
          readOnly={readOnly} reviewerId={reviewerId} onChanged={onMaterialsChanged} />
      ) : null}

      <div className="pl-students">
        <div className="pl-sub pl-students-title">Students</div>
        {(data?.students || []).length ? (
          data.students.map((st) => (
            <StudentCard key={`${slot.week}-${st.student_id}`} student={st} week={slot.week} readOnly={readOnly} onSave={onSave} />
          ))
        ) : (
          <p className="muted">No students on the class list for this class yet.</p>
        )}
      </div>
      </>)}
    </section>
  );
}

/** The three columns for given data (no fetching; used by the render tests too). */
export function PlannerBoard({ data, nav, dispatch = () => {}, readOnly = false, onSave = async () => {}, onDraft = null,
  materials = null, reviewerId = null, onMaterialsChanged = () => {} }) {
  const byWeek = Object.fromEntries((data?.weeks || []).map((w) => [w.week, w]));
  return (
    <div className="pl-board" data-testid="planner-board">
      {slots(nav).map((s) => (
        <WeekColumn key={s.slot === 'current' ? 'current' : `${s.slot}-${s.week}`} slot={s} data={byWeek[s.week]}
          canEditPlan={!!data?.can_edit_plan} readOnly={readOnly} onSave={onSave} nav={nav} dispatch={dispatch}
          aiDrafts={data?.ai_drafts || 'off'} onDraft={onDraft} materials={materials} classCode={data?.class_code}
          reviewerId={reviewerId} onMaterialsChanged={onMaterialsChanged} />
      ))}
    </div>
  );
}

export default function LessonPlanner({ reviewerId = null, readOnly = false }) {
  const [classCode, setClassCode] = useState(null);
  const [nav, dispatch] = useReducer(navReducer, null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [materials, setMaterials] = useState(null);

  const weeksKey = data ? `${data.class_code}|${data.weeks.map((w) => w.week).join(',')}` : '';
  const loadMaterials = useCallback(async () => {
    if (!data) return;
    try {
      setMaterials(await fetchPlanMaterials(data.class_code, data.weeks.map((w) => w.week), reviewerId));
    } catch (e) {
      setMaterials(null);                 // no materials API (laptop fixtures): the plan works without it
    }
  }, [weeksKey, reviewerId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadMaterials(); }, [weeksKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async (code, past) => {
    setLoading(true);
    try {
      const d = await loadPlanner(code, past, reviewerId);
      setData(d);
      setError(null);
      if (!nav || d.this_week !== nav.thisWeek) dispatch({ type: 'anchor', thisWeek: d.this_week, earliest: d.earliest_week });
      if (!code) setClassCode(d.class_code);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [reviewerId, nav]);

  // first load: the server picks the classroom and says what "this week" is
  useEffect(() => { load(null, null); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // the arrows / classroom picker: reload (this week and next week come back unchanged)
  useEffect(() => {
    if (nav && classCode && data && (data.class_code !== classCode || data.weeks[0].week !== nav.past)) {
      load(classCode, nav.past);
    }
  }, [nav?.past, classCode]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onDraft(what, body) {
    return savePlanner(`draft/${what}`, what === 'plan' ? { class_code: classCode, ...body } : body, reviewerId);
  }

  async function onSave(what, body) {
    await savePlanner(what, what === 'plan' ? { class_code: classCode, ...body } : body, reviewerId);
    await load(classCode, nav.past);
  }

  return (
    <section className="planner" data-testid="lesson-planner">
      <div className="pl-toolbar">
        <div>
          <p className="muted">
            This week stays in the middle. Use ‹ › on the left column to look back at earlier weeks.
            Each student’s weekly summary and draft parent report are on their page in Students.
          </p>
        </div>
        <label className="pl-class">
          <span className="label">Class</span>
          <select value={classCode || ''} onChange={(e) => setClassCode(e.target.value)} data-testid="planner-class">
            {(data?.classrooms || []).map((c) => (
              <option key={c.class_code} value={c.class_code}>{c.class_code}{c.students ? '' : ' (no students yet)'}</option>
            ))}
          </select>
        </label>
      </div>
      {error ? <p className="pl-error" role="alert">Couldn’t load the planner ({error}).</p> : null}
      {nav && data ? (
        <div className={loading ? 'pl-loading' : ''}>
          <PlannerBoard data={data} nav={nav} dispatch={dispatch} readOnly={readOnly} onSave={onSave} onDraft={onDraft}
            materials={materials} reviewerId={reviewerId} onMaterialsChanged={loadMaterials} />
        </div>
      ) : (
        !error && <p className="muted">Loading…</p>
      )}
    </section>
  );
}
