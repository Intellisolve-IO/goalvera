/**
 * One light fill per speaker, stable for the session (sorted label, not turn order).
 * Text on these fills is the app ink (#1c2430); each pair is WCAG AA (see the test).
 * Color is never the only cue: the speaker label is still written out.
 */
export const SPEAKER_TEXT = '#1c2430';

export const SPEAKER_FILLS = Object.freeze([
  '#d8f0e7',
  '#fde7c7',
  '#d6e4f5',
  '#f6dce6',
  '#e4e1f6',
  '#e5f0c4',
  '#f8e3cc',
  '#d2eef3',
]);

/** A darker edge of the same hue per fill: outlines the timeline blocks so light fills read on white. */
export const SPEAKER_EDGES = Object.freeze([
  '#2f8f6b',
  '#c27a1a',
  '#3d6fa8',
  '#b5507a',
  '#6b5fb5',
  '#7a9a2a',
  '#b8763a',
  '#2f8a9a',
]);

function speakerIndex(speaker, speakers) {
  const ordered = [...new Set(speakers)].sort((a, b) =>
    String(a).localeCompare(String(b), undefined, { numeric: true })
  );
  const index = ordered.indexOf(speaker);
  return (index < 0 ? 0 : index) % SPEAKER_FILLS.length;
}

export function speakerFill(speaker, speakers) {
  return SPEAKER_FILLS[speakerIndex(speaker, speakers)];
}

export function speakerEdge(speaker, speakers) {
  return SPEAKER_EDGES[speakerIndex(speaker, speakers)];
}
