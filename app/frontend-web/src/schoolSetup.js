// Plain words for the Domains / Classes server errors.
const ERRORS = {
  STALE_VERSION: 'Someone else saved a change first. The list has been reloaded; make your change again.',
  ADMIN_ONLY: 'Only admins can do that.',
  READ_ONLY_ROLE: 'This account is read-only.',
  NOT_CLASS_TEACHER: 'Only an admin or a teacher of this class can change it.',
  MUST_STAY_ON_CLASS: 'Keep yourself as one of the class’s teachers (an admin can change that).',
  NOT_YOUR_CLASSROOM: 'Pick one of your own classrooms (an admin can move a class anywhere).',
  DOMAIN_EXISTS: 'There is already a domain with that name.',
  DOMAIN_IN_USE: 'Classes still use this domain. Give those classes another domain first.',
  DOMAIN_HAS_NAMES: 'The name or methodology contains a person’s name or an email address. This text goes to the AI with materials, so it can’t contain them.',
  BAD_NAME: 'The domain needs a name (up to 80 characters).',
  BAD_CLASS_NAME: 'The class needs a name (up to 80 characters).',
  BAD_TEACHERS: 'Pick at least one teacher.',
  BAD_STUDENTS: 'One of the students isn’t on the roster.',
  UNKNOWN_CLASSROOM: 'Pick a classroom.',
  UNKNOWN_DOMAIN: 'Pick a domain from the list.',
  BAD_SCHEDULE: 'Each meeting needs at least one day, a start time and a length of 5 to 600 minutes (ending by midnight).',
  CLASS_NOT_IN_CLASSROOM: 'That class is in another classroom. Move the recording to that classroom first.',
  NOT_SESSION_TEACHER: 'Only this recording’s teacher or an admin can change that.',
  UNKNOWN_STUDENT: 'One of the students isn’t on the roster.',
  TOO_MANY_STUDENTS: 'Pick at most 40 students.',
};

export function setupError(err) {
  const code = err && err.message;
  return ERRORS[code] || `Couldn’t save (${code || 'error'}). Try again.`;
}

export const WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** "08:20" + 90 -> "8:20–9:50 AM" style end time label: "9:50 AM". */
export function endTime(start, minutes) {
  const m = /^(\d{2}):(\d{2})$/.exec(start || '');
  if (!m) return '';
  const t = Number(m[1]) * 60 + Number(m[2]) + Number(minutes || 0);
  const h = Math.floor(t / 60) % 24;
  return `${h % 12 || 12}:${String(t % 60).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

/** Client-side check before saving (the server checks again). null = fine. */
export function meetingProblem(m) {
  if (!m || !Array.isArray(m.days) || !m.days.length) return 'Pick at least one day.';
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(m.start || '')) return 'Pick a start time.';
  const n = Number(m.minutes);
  if (!Number.isInteger(n) || n < 5 || n > 600) return 'Length: 5 to 600 minutes.';
  return null;
}

// My profile / Teachers tab access changes
const PROFILE_ERRORS = {
  ASK_AN_ADMIN: 'Ask an admin to change your role or email.',
  ADMIN_ONLY: 'Only admins can do that.',
  CLASSROOM_NOT_ALLOWED: 'Pick one of your own classrooms.',
  BAD_PHONE: 'Phone: digits, spaces, ( ) - . and an optional leading +, 7 to 20 characters.',
  BAD_DISPLAY_NAME: 'Display name: up to 80 characters, no < > { } @.',
  BAD_PREFERRED_NAME: 'Preferred name: up to 40 characters, no < > { } @.',
  BAD_TITLE: 'Title: up to 80 characters, no < > { } @.',
  BAD_CREDENTIAL_TYPE: 'Pick a credential type from the list.',
  BAD_EMAIL: 'That isn’t a valid email address.',
  EMAIL_IN_USE: 'That email already signs someone in.',
  EMAIL_REQUIRED: 'Give this person a sign-in email first.',
  LAST_ADMIN: 'The app needs at least one admin. Make someone else an admin first.',
  BAD_ROLE: 'Pick a role from the list.',
  STALE_VERSION: 'Someone else saved a change at the same moment. Try again.',
  READ_ONLY_ROLE: 'This account is read-only.',
};

export function profileError(err) {
  const code = err && err.message;
  return PROFILE_ERRORS[code] || `Couldn’t save (${code || 'error'}). Try again.`;
}
