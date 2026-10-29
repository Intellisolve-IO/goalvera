// School name + logo in the top bar (David, Oct 9 2026; tasks#85). Server: review_api/branding.py.
// Everyone signed in reads them; only admins change them (the server refuses anyone else).
import { authFetch } from './auth.js';

// The demo school is fictional Everyday Heroes Academy. Demo School (badge DS) is a second
// profile on the server (review_api/branding.py PROFILES), not shown by default.
export const DEFAULT_SCHOOL_NAME = 'Everyday Heroes Academy';
export const DEFAULT_BRANDING = {
  school_name: DEFAULT_SCHOOL_NAME, badge_text: 'EH', logo: null, is_default: true,
  defaults: { school_name: DEFAULT_SCHOOL_NAME, badge_text: 'EH' },
};
export const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/svg+xml'];
export const LOGO_MAX_BYTES = 1024 * 1024;
export const NAME_MAX = 80;

const hdrs = (reviewerId, extra = {}) => ({ ...extra, ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) });

async function fail(res) {
  let code = '';
  try {
    code = (await res.json()).error || '';
  } catch {
    /* not JSON */
  }
  const e = new Error(code || `HTTP ${res.status}`);
  e.code = code;
  e.status = res.status;
  return e;
}

/** The top bar's name/badge/logo info. Falls back to the defaults if the server can't be reached. */
export async function loadBranding(reviewerId = null) {
  try {
    const res = await authFetch('/api/branding', { headers: hdrs(reviewerId), cache: 'no-store' });
    if (!res.ok) return DEFAULT_BRANDING;
    return { ...DEFAULT_BRANDING, ...(await res.json()) };
  } catch {
    return DEFAULT_BRANDING;
  }
}

/** The logo needs the sign-in token, so it is fetched and shown from a blob: URL. */
export async function loadLogoUrl(branding, reviewerId = null) {
  if (!branding || !branding.logo || !branding.logo.url) return null;
  try {
    const res = await authFetch(branding.logo.url, { headers: hdrs(reviewerId) });
    if (!res.ok) return null;
    const blob = await res.blob();
    if (!['image/png', 'image/jpeg'].includes(blob.type)) return null;
    return URL.createObjectURL(blob);
  } catch {
    return null;
  }
}

/** '' when the file is fine to send, else the reason (checked again, properly, on the server). */
export function logoProblem(file) {
  if (!file) return 'Choose a file.';
  if (!LOGO_TYPES.includes(file.type)) return 'Use a PNG, JPEG or SVG file.';
  if (file.size > LOGO_MAX_BYTES) return 'The logo must be 1 MB or smaller.';
  return '';
}

export function nameProblem(name) {
  const v = String(name || '').trim();
  if (!v) return 'Enter the school name.';
  if (v.length > NAME_MAX) return `Keep it to ${NAME_MAX} characters.`;
  return '';
}

const ERRORS = {
  ADMIN_ONLY: 'Only admins can change this.', LOGO_TYPE: 'Use a PNG, JPEG or SVG file.',
  LOGO_TOO_LARGE: 'The logo must be 1 MB or smaller.', IMAGE_INVALID: 'That image couldn’t be read.',
  SVG_INVALID: 'That SVG couldn’t be read.', SVG_DTD_NOT_ALLOWED: 'That SVG uses features we don’t accept.',
  BAD_NAME: 'Enter a school name of up to 80 characters.',
};
export const brandingErrorText = (e) => ERRORS[e && e.code] || `Couldn’t save (${(e && e.message) || 'error'}).`;

async function post(url, body, reviewerId, ctype = 'application/json') {
  const res = await authFetch(url, {
    method: 'POST', headers: hdrs(reviewerId, { 'Content-Type': ctype }),
    body: ctype === 'application/json' ? JSON.stringify(body || {}) : body,
  });
  if (!res.ok) throw await fail(res);
  return { ...DEFAULT_BRANDING, ...(await res.json()) };
}

export const saveSchoolName = (name, reviewerId = null) => post('/api/admin/branding', { school_name: name }, reviewerId);
export const uploadLogo = (file, reviewerId = null) => post('/api/admin/branding/logo', file, reviewerId, file.type);
export const removeLogo = (reviewerId = null) => post('/api/admin/branding/logo/remove', {}, reviewerId);
export const resetBranding = (reviewerId = null) => post('/api/admin/branding/reset', {}, reviewerId);
