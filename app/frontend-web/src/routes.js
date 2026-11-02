// Tabs and their addresses (David, Oct 8 2026; tasks#85). One tab per hash so links work:
// #record (teachers and admins), #recordings (default), #students, #students/<id>,
// #students/<id>/<monday>, #classes, #planner, #domains, #teachers, #activity, #settings.
// Activity Log and Settings are admins only. Record is hidden from RBTs.
// Classes sits next to Students, Domains next to the Lesson planner (curriculum) (Oct 10; tasks#103).
export const TABS = [
  { key: 'record', label: 'Record', title: 'Record', record: true },
  { key: 'recordings', label: 'Recordings', title: 'Recordings' },
  { key: 'students', label: 'Students', title: 'Students' },
  { key: 'classes', label: 'Classes', title: 'Classes' },
  { key: 'planner', label: 'Lesson planner', title: 'Weekly lesson planner' },
  { key: 'domains', label: 'Domains', title: 'Domains: educational methodology' },
  { key: 'teachers', label: 'Teachers', title: 'Teachers' },
  { key: 'activity', label: 'Activity Log', title: 'Activity Log', admin: true },
  { key: 'settings', label: 'Settings', title: 'Settings', admin: true },
];

export function tabsFor(admin, opts = {}) {
  const canRecord = opts.canRecord === true;
  return TABS.filter((t) => (admin || !t.admin) && (canRecord || !t.record));
}

/** '#students/stu_101/2026-10-05' -> { tab: 'students', studentId: 'stu_101', week: '2026-10-05' } */
export function parseHash(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  const tab = TABS.some((t) => t.key === parts[0]) ? parts[0] : parts[0] === 'review' ? 'recordings' : 'recordings';
  const out = { tab, studentId: null, week: null, sessionId: null };
  // '#recordings/rec_...' opens that session (the link in a materials email; tasks#99)
  if (tab === 'recordings' && /^rec_[A-Za-z0-9_]{1,120}$/.test(parts[1] || '')) out.sessionId = parts[1];
  if (tab === 'students' && parts[1]) {
    out.studentId = parts[1];
    if (/^\d{4}-\d{2}-\d{2}$/.test(parts[2] || '')) out.week = parts[2];
  }
  return out;
}

export function hashFor({ tab, studentId = null, week = null }) {
  if (tab === 'students' && studentId) {
    return `#students/${encodeURIComponent(studentId)}${week ? `/${week}` : ''}`;
  }
  return `#${tab}`;
}

/** The lesson planner's link to a student's weekly summary & parent report. */
export function studentWeekHref(studentId, week) {
  return hashFor({ tab: 'students', studentId, week });
}
