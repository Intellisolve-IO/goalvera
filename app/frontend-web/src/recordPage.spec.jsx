import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import App from './App.jsx';
import { RecordView } from './components/RecordPage.jsx';
import { DEMO_RECORD_WARNING, SAMPLE_LESSON_SCRIPT } from './sampleLesson.js';

const teacher = { email: 't@x.org', approved: true, role: 'teacher', read_only: false, classrooms: ['Room 104'], default_classroom: 'Room 104' };
const admin = { email: 'a@x.org', approved: true, role: 'admin', read_only: false, classrooms: ['Room 104'] };
const rbt = { email: 'r@x.org', approved: true, role: 'rbt', read_only: true, classrooms: ['Room 104'] };

const labels = (user) => {
  const html = renderToStaticMarkup(<App user={user} />);
  return [...html.matchAll(/role="tab"[^>]*>([^<]+)</g)].map((m) => m[1]);
};

describe('the Record page', () => {
  test('teachers and admins see it; reviewer, RBT, and unknown roles do not', () => {
    expect(labels(teacher)).toContain('Record');
    expect(labels(admin)).toContain('Record');
    expect(labels(rbt)).not.toContain('Record');
    expect(labels(null)).not.toContain('Record');
    expect(labels({ ...teacher, role: 'reviewer' })).not.toContain('Record');
    expect(labels({ ...teacher, role: 'coach' })).not.toContain('Record');
    expect(labels({ email: 'n@x.org', approved: true })).not.toContain('Record');
  });

  test('an RBT or reviewer who opens #record stays on Recordings', () => {
    for (const user of [rbt, { ...teacher, role: 'reviewer' }, { email: 'n@x.org', approved: true }]) {
      globalThis.window = { location: { hash: '#record' } };
      try {
        const html = renderToStaticMarkup(<App user={user} />);
        expect(html).toContain('<h1>Recordings</h1>');
        expect(html).not.toContain('data-testid="record-page"');
      } finally {
        delete globalThis.window;
      }
    }
  });

  test('the demo shows the warning and the sample lesson, and says audio leaves the phone', () => {
    const html = renderToStaticMarkup(
      <RecordView demo uploadReady classrooms={['Room 104']} classLabel="Room 104" phase="idle" hint
        gaps={[{ offset_seconds: 134, duration_seconds: 40 }]} />,
    );
    expect(html).toContain(DEMO_RECORD_WARNING);
    expect(html).toContain(SAMPLE_LESSON_SCRIPT.split('\n')[0]);
    expect(html).toContain('deleted from this phone');
    expect(html).toContain('Recording paused 2:14 for 40 s');
    expect(html).toContain('Add to Home Screen');
    expect(html).toContain('Keep this screen open');
    expect(html).not.toContain('Atlanta');
  });

  test('a paused session asks the teacher to resume the same one', () => {
    const html = renderToStaticMarkup(<RecordView demo uploadReady phase="paused" classLabel="Room 104" classrooms={['Room 104']} />);
    expect(html).toContain('Recording paused — tap Resume');
    expect(html).toContain('data-testid="record-resume"');
  });

  test('unsent audio can be resumed or discarded', () => {
    const html = renderToStaticMarkup(<RecordView demo={false} uploadReady phase="unsent" />);
    expect(html).toContain('not confirmed');
    expect(html).toContain('data-testid="record-discard"');
    expect(html).not.toContain('data-testid="sample-lesson"');
  });

  test('without an upload host the Record button stays off', () => {
    const html = renderToStaticMarkup(<RecordView demo uploadReady={false} phase="idle" />);
    expect(html).toContain('not set up to receive phone recordings');
    expect(html).toContain('disabled=""');
  });
});
