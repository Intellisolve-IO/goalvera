/**
 * Client-side filters for the pipeline recordings list.
 *
 * Options and matches come only from the sessions array the caller passes.
 * That array is the session list the server already returned for this user
 * (every classroom for every role; parent emails only for the student's teachers).
 * This module does not read the roster or call an API.
 */

export const NEEDS_CLASSROOM = 'needs classroom assignment';

export function sessionTeacher(session) {
  const name = (session.teacher_name || '').trim();
  return name || '—';
}

export function sessionClass(session) {
  const code = (session.class_code || '').trim();
  return code || '—';
}

/** Same label the Status column shows. */
export function sessionStatus(session) {
  if (session.needs_class_assignment) return NEEDS_CLASSROOM;
  return session.status || '—';
}

function uniqueSorted(values) {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

/** Dropdown options. Empty selection means All; options are only these sessions. */
export function filterOptions(sessions) {
  return {
    teachers: uniqueSorted(sessions.map(sessionTeacher)),
    classes: uniqueSorted(sessions.map(sessionClass)),
    statuses: uniqueSorted(sessions.map(sessionStatus)),
  };
}

/**
 * Keep sessions matching every active filter.
 * An empty list for a filter means All (no narrowing).
 */
export function filterSessions(sessions, filters = {}) {
  const teachers = filters.teachers?.length ? new Set(filters.teachers) : null;
  const classes = filters.classes?.length ? new Set(filters.classes) : null;
  const statuses = filters.statuses?.length ? new Set(filters.statuses) : null;
  return sessions.filter(
    (session) =>
      (!teachers || teachers.has(sessionTeacher(session))) &&
      (!classes || classes.has(sessionClass(session))) &&
      (!statuses || statuses.has(sessionStatus(session)))
  );
}

/**
 * Where the list opens: a teacher's (or RBT's) own classrooms, as the server
 * named them in /api/me home_classrooms, limited to classrooms that are in the
 * list. Nothing of theirs in the list (or an admin): All.
 */
export function initialClassroomFilter(sessions, homeClassrooms) {
  if (!homeClassrooms || homeClassrooms.length === 0) return [];
  const present = new Set(sessions.map(sessionClass));
  return homeClassrooms.filter((c) => present.has(c));
}

function recordedMs(session) {
  const raw = session.recording_start || session.timestamp_start || '';
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : -Infinity;
}

/**
 * Recordings page: [{ classroom, sessions }] with classrooms in order
 * (numeric-aware; "—" for no classroom last) and the newest recording first in each.
 */
export function groupByClassroom(sessions) {
  const groups = new Map();
  for (const s of sessions) {
    const c = sessionClass(s);
    if (!groups.has(c)) groups.set(c, []);
    groups.get(c).push(s);
  }
  const names = [...groups.keys()].sort((a, b) => {
    if (a === '—' || b === '—') return a === b ? 0 : a === '—' ? 1 : -1;
    return a.localeCompare(b, undefined, { numeric: true });
  });
  return names.map((classroom) => ({
    classroom,
    sessions: groups.get(classroom).slice().sort((a, b) => recordedMs(b) - recordedMs(a)),
  }));
}
