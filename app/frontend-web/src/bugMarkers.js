/**
 * Bug markers. A teacher picks the marker type "Bug",
 * types a note and submits it; the review service files it as a GitHub issue for our
 * support team (or keeps it pending until it can). Bug markers are never session
 * markers: they stay out of the markers line, the timeline, the incident list and its
 * numbering, and every PDF. Server side: review_api/bug_reports.py.
 */
import { clock } from './incidents.js';

/** Marker / event types a teacher can add on the review site. The recorder's own
 * types (Incident, Teacher Notes, Other) are set on the device. */
export const MARKER_TYPES = ['Bug'];
// lower case: the recorder writes "bug" (firmware 0.6.7), the review site "Bug"; both are hidden
export const HIDDEN_MARKER_TYPES = new Set(['bug']);
export function isHiddenMarker(m) {
  return HIDDEN_MARKER_TYPES.has(String((m && m.type) || '').trim().toLowerCase());
}
export const BUG_HINT = 'Don’t include student names; this goes to our support team.';
export const BUG_THANKS = 'Thanks, sent to our team.';

/** Where on the screen the problem is (the server accepts exactly these keys). */
export const BUG_AREAS = [
  ['transcript', 'Transcript'],
  ['timeline', 'Speaker timeline'],
  ['markers', 'Markers'],
  ['iep_goals', 'IEP goal evaluations'],
  ['incidents', 'ABC incident analysis'],
  ['incident_pdf', 'Incident Download PDF'],
  ['summary', 'Session summary'],
  ['approve', 'Approve'],
  ['session_pdf', 'Session Download PDF'],
  ['classroom', 'Classroom'],
  ['other', 'Somewhere else on this screen'],
];

/** Session markers without Bug ones (they are shown only as "Bug reported" items). */
export function sessionMarkers(markers) {
  return (markers || []).filter((m) => !isHiddenMarker(m));
}

/** "1:23", "83", "1:02:05" -> seconds; "" -> null; anything else -> NaN. */
export function parseClock(text) {
  const t = String(text ?? '').trim();
  if (!t) return null;
  if (/^\d+$/.test(t)) return Number(t);
  const m = t.match(/^(?:(\d+):)?(\d{1,2}):(\d{2})$/);
  if (!m) return NaN;
  const [h, mm, ss] = [Number(m[1] || 0), Number(m[2]), Number(m[3])];
  if (ss > 59 || (m[1] && mm > 59)) return NaN;
  return h * 3600 + mm * 60 + ss;
}

/** "Bug reported · 1:23" (the link or "sent to our team" follows it). */
export function bugItemText(r) {
  const at = r && r.offset_seconds !== null && r.offset_seconds !== undefined ? ` · ${clock(r.offset_seconds)}` : '';
  return `Bug reported${r && r.source === 'recorder' ? ' from recorder' : ''}${at}`;
}
