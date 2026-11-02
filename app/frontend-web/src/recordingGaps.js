/** "Recording paused 2:14 for 40 s" — the sentence on the review page. */
export function formatRecordingGap(g) {
  const s = Math.max(0, Math.round(Number(g && g.offset_seconds) || 0));
  const dur = Math.max(0, Math.round(Number(g && g.duration_seconds) || 0));
  const m = Math.floor(s / 60);
  const sec = String(s % 60).padStart(2, '0');
  return `Recording paused ${m}:${sec} for ${dur} s`;
}
