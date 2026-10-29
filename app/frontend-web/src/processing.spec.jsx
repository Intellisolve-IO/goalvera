// Processing rows (tasks#106): uploads show at once with their stage; failures show a clear state.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import { ProcessingTable } from './components/ProcessingRows.jsx';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

describe('Uploading / processing rows', () => {
  test('stages and a clear failure', () => {
    const h = renderToStaticMarkup(<ProcessingTable uploads={[
      { recording_id: 'a', state: 'uploaded', label: 'Uploaded, waiting to start', class_code: 'Room 104', device_id: 'dev01',
        recorded_at_local: '2026-10-10T09:00:00', processing: true },
      { recording_id: 'b', state: 'transcribing', label: 'Transcribing', class_code: 'Room 104', device_id: 'dev01',
        recorded_at_local: '2026-10-10T09:10:00', duration_seconds: 300, processing: true },
      { recording_id: 'c', state: 'failed', label: 'Failed', error_code: 'STT_3', error_text: 'Transcription failed.',
        class_code: 'Room 104', device_id: 'dev01', recorded_at_local: '2026-10-10T09:20:00', processing: false },
    ]} />);
    expect(text(h)).toContain('Uploading / processing');
    expect((h.match(/data-testid="processing-row"/g) || []).length).toBe(3);
    expect(text(h)).toContain('Uploaded, waiting to start…');
    expect(text(h)).toContain('Transcribing…');
    expect(text(h)).toContain('5 min');
    expect(text(h)).toContain('Failed: Transcription failed.');
    expect(text(h)).toContain('2026-10-10 09:20');
  });

  test('nothing when nothing is processing', () => {
    expect(renderToStaticMarkup(<ProcessingTable uploads={[]} />)).toBe('');
  });
});
