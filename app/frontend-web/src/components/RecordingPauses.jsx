import React from 'react';
import { formatRecordingGap } from '../recordingGaps.js';

export default function RecordingPauses({ gaps }) {
  const rows = (gaps || []).filter((g) => g && Number.isFinite(Number(g.offset_seconds)));
  if (!rows.length) return null;
  return (
    <section className="recording-pauses" data-testid="recording-gaps">
      <h3>Recording pauses</h3>
      <ul>
        {rows.map((g, i) => <li key={i}>{formatRecordingGap(g)}</li>)}
      </ul>
    </section>
  );
}
