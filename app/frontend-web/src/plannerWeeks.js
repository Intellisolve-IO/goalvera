// Weekly lesson planner: the three week slots and the arrows.
// "last week, this week and next week. This week stays anchored; the
// arrows step through previous weeks in the last-week slot."
// Weeks are Mondays as 'YYYY-MM-DD' strings; all date math in UTC so the
// browser's timezone can't shift a day. The server decides what "this week" is.

const DAY = 86400000;
const parse = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
};
const fmt = (t) => new Date(t).toISOString().slice(0, 10);

/** Monday of the week containing iso. */
export function weekOf(iso) {
  const t = parse(iso);
  const dow = (new Date(t).getUTCDay() + 6) % 7; // Mon=0 … Sun=6
  return fmt(t - dow * DAY);
}

export const addWeeks = (iso, n) => fmt(parse(iso) + n * 7 * DAY);

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** 'Sep 28 – Oct 2' (Mon–Fri). */
export function weekRange(monday) {
  const a = new Date(parse(monday));
  const b = new Date(parse(monday) + 4 * DAY);
  const left = `${MONTHS[a.getUTCMonth()]} ${a.getUTCDate()}`;
  const right = a.getUTCMonth() === b.getUTCMonth() ? `${b.getUTCDate()}` : `${MONTHS[b.getUTCMonth()]} ${b.getUTCDate()}`;
  return `${left} – ${right}`;
}

/** Start: past slot = last week. thisWeek comes from the server. */
export function initialNav(thisWeek, earliest = null) {
  return { thisWeek, past: addWeeks(thisWeek, -1), earliest };
}

export const lastWeek = (nav) => addWeeks(nav.thisWeek, -1);
export const canGoBack = (nav) => !nav.earliest || nav.past > nav.earliest;
export const canGoForward = (nav) => nav.past < lastWeek(nav);

/** The arrows. 'back' = one week earlier, 'forward' = one week later (never past last week),
 *  'reset' = back to last week. This week and next week never move. */
export function navReducer(nav, action) {
  switch (action.type) {
    case 'back':
      return canGoBack(nav) ? { ...nav, past: addWeeks(nav.past, -1) } : nav;
    case 'forward':
      return canGoForward(nav) ? { ...nav, past: addWeeks(nav.past, 1) } : nav;
    case 'reset':
      return { ...nav, past: lastWeek(nav) };
    case 'anchor': // the server says this week changed (e.g. the page stayed open over a weekend)
      return initialNav(action.thisWeek, action.earliest ?? nav.earliest);
    default:
      return nav;
  }
}

/** The three columns, left to right. */
export function slots(nav) {
  const weeksBack = Math.round((parse(nav.thisWeek) - parse(nav.past)) / (7 * DAY));
  return [
    { slot: 'past', week: nav.past, title: weeksBack === 1 ? 'Last week' : `${weeksBack} weeks ago` },
    { slot: 'current', week: nav.thisWeek, title: 'This week' },
    { slot: 'next', week: addWeeks(nav.thisWeek, 1), title: 'Next week' },
  ];
}

export const IEP_STATUS = {
  evidence_seen: 'Evidence seen',
  not_seen: 'Not yet shown',
  not_enough: 'Not enough to say',
};
