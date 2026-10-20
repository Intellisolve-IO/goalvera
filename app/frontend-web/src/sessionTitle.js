/**
 * Session modal header (): "Oct 1, 2026 · 10:17 AM · Reading 3A".
 *
 * timestamp_start is what the review list's "Recorded" column shows. For pipeline
 * sessions it is the recorder's own clock, taken from the file name
 * (pipeline_service/main.py "recorded_at_local"): a wall-clock time at the school,
 * stored WITHOUT a zone and NOT in UTC. So a zoneless time is shown as it is (no
 * conversion, same as the list, the PDF and the planner). A time that carries a zone
 * ("Z" or "-04:00") is converted to America/New_York, daylight saving included.
 */
export const SCHOOL_TZ = 'America/New_York';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/i;

/** Wall-clock parts at the school for a timestamp, or null if it can't be read. */
export function schoolTimeParts(ts) {
  if (typeof ts !== 'string') return null;
  const m = ISO.exec(ts.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi, , zone] = m;
  if (h === undefined) return { year: +y, month: +mo, day: +d, hour: null, minute: null };
  if (!zone) return { year: +y, month: +mo, day: +d, hour: +h, minute: +mi };
  const t = new Date(ts.trim());
  if (Number.isNaN(t.getTime())) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: SCHOOL_TZ, year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
    }).formatToParts(t).map((p) => [p.type, p.value])
  );
  return { year: +parts.year, month: +parts.month, day: +parts.day, hour: +parts.hour % 24, minute: +parts.minute };
}

/** "Oct 1, 2026 · 10:17 AM" (date only when there is no time), or null. */
export function recordedLabel(ts) {
  const p = schoolTimeParts(ts);
  if (!p || p.month < 1 || p.month > 12) return null;
  const date = `${MONTHS[p.month - 1]} ${p.day}, ${p.year}`;
  if (p.hour === null) return date;
  const hour = p.hour % 12 || 12;
  return `${date} · ${hour}:${String(p.minute).padStart(2, '0')} ${p.hour < 12 ? 'AM' : 'PM'}`;
}

/** The room code in the recording id: rec_<device>_<YYYYMMDD>_<HHMMSS>_<room>. */
export function roomFromRecordingId(rid) {
  const m = /_\d{8}_\d{6}_([A-Za-z0-9_-]+)$/.exec(typeof rid === 'string' ? rid : '');
  return m ? m[1] : null;
}

/** The classroom the review list shows (class_code); the room code only when there is none. */
export function classroomLabel(session) {
  const name = typeof session.class_code === 'string' ? session.class_code.trim() : '';
  return name || roomFromRecordingId(session.recording_id);
}

/** Modal title. dayDate: the sample week's day (sample sessions carry no recording time). */
export function sessionTitle(session, { dayDate = null } = {}) {
  const parts = [recordedLabel(session.timestamp_start) || recordedLabel(dayDate), classroomLabel(session)];
  const shown = parts.filter(Boolean);
  return shown.length ? shown.join(' · ') : 'Session';
}
