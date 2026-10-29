/**
 * Admin activity journal + activity report (tasks#77), for the admin strip.
 *
 * The server decides access: both endpoints answer 403 ADMIN_ONLY to anyone who
 * isn't an admin, and every view / download is itself journaled. The UI only
 * hides the buttons from people the server would refuse anyway.
 *
 *   GET /api/admin/journal?from=&to=&limit=[&action=]        newest first
 *   GET /api/admin/reports/activity.csv?by=person|student&from=&to=
 *
 * Both calls go through authFetch (Bearer ID token), so a plain link won't do for
 * the CSV: it is fetched, then saved from a blob.
 *
 * Dates: the server filters on UTC calendar days. The journal asks for one extra
 * day and keeps only rows whose Eastern date is inside the range, so its days are
 * Eastern days. The report is counted on the server, so its days stay UTC days.
 */
import { authFetch } from './auth.js';

export const ET = 'America/New_York';
export const JOURNAL_PAGE = 100;
export const JOURNAL_MAX = 5000;          // server cap (Query.MAX_LIMIT)
export const MAX_RANGE_DAYS = 399;        // server allows 400; the journal adds one day

/** Same rule as the server (Reviewer.is_admin): an approved, allowlisted admin. */
export function isAdminUser(user) {
  return !!(user && user.approved && user.role === 'admin' && !user.read_only);
}

/** YYYY-MM-DD of `when` in Eastern time. */
export function etDate(when = new Date()) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', { timeZone: ET, year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(when instanceof Date ? when : new Date(when))
      .map((x) => [x.type, x.value])
  );
  return `${p.year}-${p.month}-${p.day}`;
}

export function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Default range: the last 7 days, today included (Eastern). */
export function lastDays(n = 7, now = new Date()) {
  const to = etDate(now);
  return { from: addDays(to, -(n - 1)), to };
}

/** "Oct 4, 2026, 1:51:07 PM ET" */
export function formatEt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso || '');
  return `${d.toLocaleString('en-US', {
    timeZone: ET, month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
  })} ET`;
}

/** "session_viewed" -> "Session viewed" */
export function actionLabel(a) {
  if (!a) return '';
  const s = String(a).replace(/_/g, ' ').replace(/\b(pdf|iep)\b/g, (w) => w.toUpperCase());
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** null if the range is fine, else a sentence for the person. */
export function rangeProblem(from, to) {
  if (!DATE.test(from || '') || !DATE.test(to || '')) return 'Pick a start and an end date.';
  if (from > to) return 'The start date is after the end date.';
  const days = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000;
  if (days > MAX_RANGE_DAYS) return `Pick a range of at most ${MAX_RANGE_DAYS} days.`;
  return null;
}

export function journalUrl({ from, to, limit = JOURNAL_PAGE, action = '' }) {
  const q = new URLSearchParams({ from, to: addDays(to, 1), limit: String(limit) });
  if (action) q.set('action', action);
  return `/api/admin/journal?${q}`;
}

export function reportUrl({ by = 'person', from, to }) {
  return `/api/admin/reports/activity.csv?${new URLSearchParams({ by, from, to })}`;
}

export class AdminApiError extends Error {
  constructor(status, code, reason = null) {
    super(code || `HTTP ${status}`);
    this.status = status;
    this.code = code || null;
    this.reason = reason || null;
  }
}

async function failure(res) {
  let code = null;
  let reason = null;
  try {
    const body = await res.json();
    code = body.error || null;
    reason = body.reason || null;
  } catch {
    /* not JSON */
  }
  return new AdminApiError(res.status, code, reason);
}

/** Plain words for the server's JOURNAL_READ_FAILED reasons. */
const READ_REASONS = {
  ACCESS_DENIED: 'the review service isn’t allowed to read it',
  READER_TOKEN_FAILED: 'the review service couldn’t get its read access',
  NOT_FOUND: 'its storage couldn’t be found',
  QUERY_REJECTED: 'the storage turned the request down (the range may be too large)',
  BACKEND_ERROR: 'its storage had an error',
};

/** What to tell the person when a call fails. */
export function errorText(err, what = 'the journal') {
  const status = err && err.status;
  if (status === 403) return `Only admins can open ${what}. This account isn’t an admin, so the server refused (403).`;
  if (status === 401) return 'Your sign-in has expired. Sign out and sign in again.';
  if (status === 400) return 'The server didn’t accept those dates. Check the range and try again.';
  if (err && err.code === 'JOURNAL_READ_FAILED') {
    const why = READ_REASONS[err.reason] || 'its storage had an error';
    return `Couldn’t read ${what}: ${why}. New activity is still being recorded. `
      + `Try again; if it keeps happening, tell Delivery the time and this code: ${err.reason || 'JOURNAL_READ_FAILED'}.`;
  }
  if (status === 503) return `The activity journal is unavailable right now, so ${what} can’t be shown. Try again in a minute.`;
  if (status) return `Couldn’t load ${what} (error ${status}${err.code ? `, ${err.code}` : ''}). Try again.`;
  return `Couldn’t reach the review service to load ${what}. Check your connection and try again.`;
}

/**
 * Journal rows for an Eastern date range, newest first.
 * -> { entries, more }  (more: the server may have older rows past `limit`)
 */
export async function loadJournal({ from, to, limit = JOURNAL_PAGE, action = '' }, { fetch = authFetch } = {}) {
  const res = await fetch(journalUrl({ from, to, limit, action }), { cache: 'no-store' });
  if (!res.ok) throw await failure(res);
  const body = await res.json();
  const rows = Array.isArray(body.entries) ? body.entries : [];
  const entries = rows
    .filter((r) => {
      const d = etDate(r.occurred_at);
      return d >= from && d <= to;
    })
    .sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : a.occurred_at > b.occurred_at ? -1 : 0));
  return { entries, more: rows.length >= limit && limit < JOURNAL_MAX };
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Fetch the activity CSV with the signed-in token and save it as a file. -> the file name */
export async function downloadActivityReport({ by, from, to }, { fetch = authFetch, save = saveBlob } = {}) {
  const res = await fetch(reportUrl({ by, from, to }), { cache: 'no-store' });
  if (!res.ok) throw await failure(res);
  const blob = await res.blob();
  const m = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const name = m ? m[1] : `activity-by-${by}_${from}_${to}.csv`;
  save(blob, name);
  return name;
}
