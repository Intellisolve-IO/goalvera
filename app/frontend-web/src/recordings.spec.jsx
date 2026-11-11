// Recordings page: the old "Teacher weekly matrix" page is now
// "Recordings": no 2-week calendar, no value metrics, a note under the title, and the most recent
// recordings grouped by classroom (newest first in each), each opening the recording.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import App, { RECORDINGS_NOTE } from './App.jsx';
import PipelineSessions from './components/PipelineSessions.jsx';
import { groupByClassroom } from './pipelineFilters.js';
import { getSession, getSessions } from './dataStore.js';

const NOTE = 'Below are the most recent recordings, sorted by classroom. Open a recording to assign students and '
  + 'process any incidents. Once students are assigned, the session details can be used in the weekly class planner '
  + 'for sending parent summary emails, assessing IEP performance and managing lesson plans.';
const rec = (id, cls, at, extra = {}) => ({ recording_id: id, class_code: cls, timestamp_start: at, status: 'pending_review',
  device_id: 'dev1', duration_seconds: 600, teacher_name: 'Sarah Jenkins', ...extra });
const list = [
  rec('a', 'Room B', '2026-10-07T09:00:00'), rec('b', 'Hall', '2026-10-08T10:00:00'),
  rec('c', 'Room B', '2026-10-08T13:00:00'), rec('d', 'Room 9', '2026-10-06T08:00:00'),
  rec('e', '', '2026-10-08T15:00:00', { needs_class_assignment: true }), rec('f', 'Hall', '2026-10-08T08:00:00'),
];
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('Recordings page', () => {
  test('title, tab and the note under the title, as written', () => {
    expect(RECORDINGS_NOTE).toBe(NOTE);
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain('<h1>Recordings</h1>');
    expect(html).toMatch(/data-testid="tab-review"[^>]*>Recordings<\/button>/);
    expect(html).toContain(`data-testid="recordings-note">${NOTE}<`);
    expect(html.indexOf('<h1>Recordings</h1>')).toBeLessThan(html.indexOf('recordings-note'));
  });

  test('no 2-week calendar and no value metrics', () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).not.toMatch(/class="matrix|matrix-wrap|Teacher weekly matrix|Week of/);
    expect(html).not.toMatch(/Value metrics|Sessions processed|Drafts pending HITL|min saved|Cost \/ session|ABC flags|Drafts approved|metric-value/);
    expect(html).toContain('data-testid="tab-planner"');            // the planner stays one click away
  });

  test('grouped by classroom, newest first within each; no classroom last', () => {
    const g = groupByClassroom(list);
    expect(g.map((x) => x.classroom)).toEqual(['Hall', 'Room 9', 'Room B', '—']);
    expect(g.map((x) => x.sessions.map((s) => s.recording_id))).toEqual([['b', 'f'], ['d'], ['c', 'a'], ['e']]);
  });

  test('each recording opens the recording detail', () => {
    const html = renderToStaticMarkup(<PipelineSessions sessions={list} state={{ ok: true, loading: false }}
      getStudent={() => null} onOpen={() => {}} onReload={() => {}} />);
    const heads = [...html.matchAll(/<h3 class="classroom-name">([^<]+)</g)].map((m) => m[1]);
    expect(heads).toEqual(['Hall', 'Room 9', 'Room B', 'No classroom yet']);
    expect(html.match(/data-testid="recording-row"/g)).toHaveLength(6);
    expect((text(html).match(/ Review /g) || []).length).toBe(6);
    expect(html).not.toContain('New recordings from the pipeline');
  });

  test('the built-in samples are back: a "Samples" group at the end, each with a Sample badge, opening as before', () => {
    const samples = [
      { recording_id: 's1', student_id: 'stu_101', status: 'approved', duration_seconds: 2700, timestamp_start: '2026-09-21', is_sample: true },
      { recording_id: 's2', student_id: 'stu_102', status: 'pending_review', duration_seconds: 2400, timestamp_start: '2026-09-24', is_sample: true },
    ];
    const opened = [];
    const el = <PipelineSessions sessions={list} samples={samples} state={{ ok: true, loading: false }}
      getStudent={(id) => ({ stu_101: { first_name: 'Marcus', last_initial: 'A.' }, stu_102: { first_name: 'Sophia', last_initial: 'L.' } })[id]}
      onOpen={(id) => opened.push(id)} onReload={() => {}} homeClassrooms={['Room B']} />;
    const html = renderToStaticMarkup(el);
    const heads = [...html.matchAll(/<h3 class="classroom-name">([^<]+)</g)].map((m) => m[1]);
    expect(heads[heads.length - 1]).toBe('Samples');
    expect(heads).toContain('Room B');                                     // a teacher's own classroom filter
    const sampleRows = [...html.matchAll(/<tr[^>]*class="sample-row"[^>]*>([\s\S]*?)<\/tr>/g)].map((m) => text(m[1]));
    expect(sampleRows).toHaveLength(2);
    expect(sampleRows[0]).toMatch(/2026-09-24 Sample Sophia L\./);             // newest first
    expect(sampleRows[1]).toMatch(/2026-09-21 Sample Marcus A\./);
    for (const r of sampleRows) expect(r).toContain('Review');
    expect(html.match(/data-testid="sample-badge"/g)).toHaveLength(2);
    // real recordings never get the badge
    expect(renderToStaticMarkup(<PipelineSessions sessions={list} state={{ ok: true, loading: false }}
      getStudent={() => null} onOpen={() => {}} onReload={() => {}} />)).not.toContain('sample-badge');
  });

  test('samples show even before any real recording, and the App lists all eight fixtures', () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain('<h3 class="classroom-name">Samples<');
    expect(html.match(/data-testid="sample-badge"/g)).toHaveLength(getSessions().length);
    expect(getSessions()).toHaveLength(8);
    expect(text(html)).toMatch(/Marcus A\./);
    expect(text(html)).toMatch(/Sophia L\./);
    // they open the same detail view as before: the sample session is found by id
    for (const s of getSessions()) expect(getSession(s.recording_id)).not.toBeNull();
  });
});

