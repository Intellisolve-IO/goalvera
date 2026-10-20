// Sample students for the Students tab: the built-in fictional
// Marcus A. and Sophia L. from the HITL fixtures. Browser only: never sent to the server, so
// never in reports or the Journal, and their weekly draft is made here without any AI.
import { getDays, getSessions, getStudents } from './dataStore.js';
import { addWeeks, weekOf } from './plannerWeeks.js';

export const SAMPLE_PREFIX = 'sample-';
export const isSampleId = (id) => typeof id === 'string' && id.startsWith(SAMPLE_PREFIX);
const realId = (id) => id.slice(SAMPLE_PREFIX.length);
const SAMPLE_CLASS = 'Sample class';

export function sampleWeek() {
  return weekOf(getDays()[0].date);
}

export function sampleStudents() {
  return getStudents().map((s) => ({
    student_id: SAMPLE_PREFIX + s.student_id,
    name: `${s.first_name} ${s.last_initial}`,
    class_codes: [SAMPLE_CLASS],
    teacher_names: ['Sarah Jenkins'],
    iep_goals: s.iep_goals.length,
    mine: false,
    sample: true,
  }));
}

/** The same shape as GET /api/students/<id>, from the fixtures (one sample week). */
export function sampleDetail(id, week = null) {
  const st = getStudents().find((s) => s.student_id === realId(id));
  if (!st) return null;
  const wk = week || sampleWeek();
  const days = [0, 1, 2, 3, 4].map((i) => ({ date: addDays(wk, i), sessions: [] }));
  const byDate = Object.fromEntries(days.map((d) => [d.date, d]));
  const dayDate = Object.fromEntries(getDays().map((d) => [d.key, d.date]));
  for (const s of getSessions()) {
    if (s.student_id !== st.student_id) continue;
    const day = byDate[dayDate[s.day_key]];
    if (!day) continue;
    const a = s.processed_analysis || {};
    day.sessions.push({
      recording_id: s.recording_id, approved: s.status === 'approved', class_code: null,
      teacher_name: 'Sarah Jenkins', timestamp_start: dayDate[s.day_key], recording_start: null,
      summary: a.general_session_summary || '', incidents: (a.incident_analysis || []).length,
    });
  }
  return {
    student_id: id, name: `${st.first_name} ${st.last_initial}`, first_name: st.first_name,
    class_codes: [SAMPLE_CLASS], teacher_names: ['Sarah Jenkins'],
    iep: { goals: st.iep_goals }, iep_hidden: false,
    week: wk, week_end: addDays(wk, 4), this_week: sampleWeek(),
    prev_week: addWeeks(wk, -1), next_week: addWeeks(wk, 1),
    days, summary: null, summary_versions: 0, suggestion: null,
    parent_email_hidden: false, can_edit: true, can_draft: true, ai_drafts: 'sample', sample: true,
  };
}

/** A weekly summary + parent email for a sample student, written here from the week's notes
 *  (no AI call; nothing leaves the browser). */
export function sampleDraft(detail) {
  const sessions = detail.days.flatMap((d) => d.sessions.map((s) => ({ ...s, date: d.date })));
  const approved = sessions.filter((s) => s.approved);
  const name = detail.first_name;
  const lines = approved.filter((s) => s.summary).map((s) => s.summary.replace(/\.$/, ''));
  const summary = sessions.length
    ? `${approved.length} approved session${approved.length === 1 ? '' : 's'} this week. ${lines.join('. ')}${lines.length ? '.' : ''}`
    : 'No sessions this week.';
  const goals = (detail.iep?.goals || []).map((g) => g.category || g.goal_id);
  const parent_email = sessions.length
    ? `Dear family,\n\nHere is ${name}'s week in class. ${name} had ${approved.length} reviewed session`
      + `${approved.length === 1 ? '' : 's'}${goals.length ? `, and we worked on ${goals.join(' and ').toLowerCase()} goals` : ''}. `
      + `${lines.length ? `Highlights: ${lines.join('; ').toLowerCase()}.` : ''}\n\nPlease reach out with any questions.\n\nSarah Jenkins`
    : '';
  return { summary, parent_email, iep_check: [], ai: null, sample: true };
}

function addDays(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d) + n * 86400000).toISOString().slice(0, 10);
}
