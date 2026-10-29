/**
 * Incident numbering (David, Oct 7 2026; tasks#85): incidents are numbered in order
 * within each recording, earliest first, and shown as "Incident 1 · 0:03". The review
 * screen, the approved-notes PDF and the incident print view all number them the same
 * way (server: review_api/incident_print.py incident_numbers / clock).
 */

/** m:ss, or h:mm:ss from an hour on. 3 -> "0:03", 75 -> "1:15", 3725 -> "1:02:05". */
export function clock(seconds) {
  const n = Number(seconds);
  const s = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** [{ number, index, incident }] earliest first; ties keep their original order. */
export function numberIncidents(incidents) {
  const t = (inc) => {
    const n = Number(inc && inc.timestamp_seconds);
    return Number.isFinite(n) ? n : 0;
  };
  return (incidents || [])
    .map((incident, index) => ({ incident, index }))
    .sort((a, b) => t(a.incident) - t(b.incident) || a.index - b.index)
    .map((x, i) => ({ ...x, number: i + 1 }));
}

export function incidentLabel(number, seconds) {
  return `Incident ${number} \u00b7 ${clock(seconds)}`;
}

/** Incident names (David, Oct 8): "Incident 1: Left seat and refused redirection", or plain
 * "Incident 1" when there is no usable name. At most 80 characters (server: incident_names.py). */
export const MAX_INCIDENT_NAME = 80;
export function incidentTitle(number, name) {
  const n = typeof name === 'string' ? name.trim() : '';
  return n ? `Incident ${number}: ${n}` : `Incident ${number}`;
}

const ET_CLOCK = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true,
});

/** Eastern clock time `seconds` into a recording that started at `startIso` (ISO with offset,
 * from the server: session.recording_start). "1:02 PM"; null without a start time. Done in
 * UTC then shown in America/New_York, so a recording across a DST change is still right. */
export function clockAt(startIso, seconds) {
  if (!startIso) return null;
  const t0 = Date.parse(startIso);
  if (!Number.isFinite(t0)) return null;
  const n = Number(seconds);
  const off = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  return ET_CLOCK.format(new Date(t0 + off * 1000)).replace(/\u202f/g, ' ');
}

/** "1:02 PM · 12:34 into recording"; without a start time just "12:34 into recording". */
export function timeLine(startIso, seconds) {
  const at = clockAt(startIso, seconds);
  return [at, `${clock(seconds)} into recording`].filter(Boolean).join(' \u00b7 ');
}

const ET_PARTS = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hourCycle: 'h23',
});
const ET_HMS = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
});

function etParts(ms) {
  const o = {};
  for (const part of ET_PARTS.formatToParts(new Date(ms))) {
    if (part.type !== 'literal') o[part.type] = part.value;
  }
  return {
    year: Number(o.year), month: Number(o.month), day: Number(o.day),
    hour: Number(o.hour) % 24, minute: Number(o.minute), second: Number(o.second),
  };
}

/** Eastern clock of an offset: "1:02 PM", or "1:02:40 PM" when the seconds are not 0.
 * null without a usable start time. */
export function formatClockTime(startIso, seconds) {
  const at = clockAt(startIso, seconds);
  if (!at) return null;
  const t0 = Date.parse(startIso);
  const n = Number(seconds);
  const off = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  if (etParts(t0 + off * 1000).second === 0) return at;
  return ET_HMS.format(new Date(t0 + off * 1000)).replace(/\u202f/g, ' ');
}

