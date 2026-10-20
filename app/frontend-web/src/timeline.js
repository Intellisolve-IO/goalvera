/**
 * Pure helpers for the speaker timeline (): positions, ticks, marker
 * label layout and the turn a click jumps to. No DOM, no network, no logging.
 */

export function mmss(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/** Marker time in seconds: the review API sends offset_seconds, the raw sidecar has offset_ms. */
export function markerSeconds(m) {
  if (m && Number.isFinite(Number(m.offset_seconds))) return Number(m.offset_seconds);
  if (m && Number.isFinite(Number(m.offset_ms))) return Number(m.offset_ms) / 1000;
  return null;
}

export function normalizeMarkers(markers) {
  return (markers || [])
    .map((m) => ({ type: String((m && m.type) || 'Marker'), seconds: markerSeconds(m) }))
    .filter((m) => m.seconds !== null && m.seconds >= 0)
    .sort((a, b) => a.seconds - b.seconds);
}

/**
 * Recording length: the sidecar's duration_ms / duration_seconds, else the last
 * turn's end. Never shorter than the last turn or marker, so nothing is clipped.
 */
export function timelineDuration({ durationMs, durationSeconds, turns = [], markers = [] } = {}) {
  let d = 0;
  if (Number(durationMs) > 0) d = Number(durationMs) / 1000;
  else if (Number(durationSeconds) > 0) d = Number(durationSeconds);
  const lastEnd = turns.reduce((mx, t) => Math.max(mx, Number(t.end_seconds) || 0), 0);
  const lastMark = normalizeMarkers(markers).reduce((mx, m) => Math.max(mx, m.seconds), 0);
  return Math.max(d, lastEnd, lastMark) || 1;
}

/** Percent of the track for a time, clamped to [0, 100]. */
export function pct(sec, duration) {
  if (!(duration > 0)) return 0;
  return Math.min(100, Math.max(0, ((Number(sec) || 0) / duration) * 100));
}

/** Left and width (percent) of one talking block. */
export function blockPosition(turn, duration) {
  const left = pct(turn.start_seconds, duration);
  const right = pct(Math.max(turn.end_seconds, turn.start_seconds), duration);
  return { left, width: Math.max(0, right - left) };
}

/** Each speaker's blocks, keeping the turn index so a click can jump to it. */
export function speakerBlocks(turns, duration) {
  const out = {};
  (turns || []).forEach((t, index) => {
    (out[t.speaker] ||= []).push({ index, start: t.start_seconds, end: t.end_seconds, ...blockPosition(t, duration) });
  });
  return out;
}

const TICK_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

/** mm:ss ticks at a round interval giving at most maxTicks + 1 labels. */
export function axisTicks(duration, maxTicks = 8) {
  const step = TICK_STEPS.find((s) => duration / s <= maxTicks) || Math.ceil(duration / maxTicks / 3600) * 3600;
  const ticks = [];
  for (let t = 0; t <= duration + 1e-6; t += step) ticks.push({ seconds: t, left: pct(t, duration), label: mmss(t) });
  return { step, ticks };
}

/**
 * Marker labels sit at their tick and read to the right (or to the left near the
 * end of the track). A label that would overlap one already placed drops to the
 * next row down. Widths are estimated from the text so this stays pure.
 */
export function layoutMarkerLabels(markers, duration, trackPx = 600, { charPx = 6.6, padPx = 14, gapPx = 6 } = {}) {
  const width = Math.max(1, trackPx);
  const rows = []; // right edge (px) of the last label in each row
  return normalizeMarkers(markers).map((m) => {
    const x = (pct(m.seconds, duration) / 100) * width;
    const w = m.type.length * charPx + padPx;
    const alignRight = x + w > width && x - w >= 0;
    const start = alignRight ? x - w : x;
    const end = start + w;
    let row = rows.findIndex((r) => r + gapPx <= start);
    if (row < 0) {
      row = rows.length;
      rows.push(end);
    } else rows[row] = end;
    return { ...m, left: pct(m.seconds, duration), row, alignRight, startPx: start, endPx: end };
  });
}

/** The turn containing time t, else the nearest one (ties go to the earlier turn). -1 if no turns. */
export function turnIndexAt(turns, t) {
  let best = -1;
  let bestDist = Infinity;
  (turns || []).forEach((turn, i) => {
    const s = Number(turn.start_seconds) || 0;
    const e = Math.max(Number(turn.end_seconds) || 0, s);
    const dist = t < s ? s - t : t > e ? t - e : 0;
    if (dist < bestDist) {
      best = i;
      bestDist = dist;
    }
  });
  return best;
}

/**
 * Scroll the turns list (its own scroller) so turn `index` is in view, near the
 * top. Returns the turn element, or null if it is not rendered.
 */
export function scrollTurnIntoView(container, index) {
  if (!container || index < 0) return null;
  const el = container.querySelector(`[data-turn-index="${index}"]`);
  if (!el) return null;
  const top = Math.max(0, el.offsetTop - 8);
  if (typeof container.scrollTo === 'function') container.scrollTo({ top, behavior: 'smooth' });
  else container.scrollTop = top;
  return el;
}
