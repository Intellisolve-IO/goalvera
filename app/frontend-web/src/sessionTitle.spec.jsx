// Session modal header (): "<recorded date · time ET> · <classroom>" with the
// student, length and status underneath. No eyebrow line, no raw recording id.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SessionModal from './components/SessionModal.jsx';
import { classroomLabel, recordedLabel, roomFromRecordingId, sessionTitle } from './sessionTitle.js';

const RID = 'rec_laptop_demo_20261001_101700_RM12';
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const between = (html, testid) => {
  const m = new RegExp(`data-testid="${testid}"[^>]*>(.*?)</(h2|p)>`).exec(html);
  return m ? text(m[1]) : null;
};
const session = {
  recording_id: RID,
  source: 'pipeline',
  status: 'pending_review',
  timestamp_start: '2026-10-01T10:17:00',
  duration_seconds: 1522,
  class_code: 'Reading 3A',
  teacher_name: 'Sarah Jenkins',
  needs_class_assignment: false,
  viewer_may_act: true,
  processed_analysis: { goal_evaluations: [], incident_analysis: [], general_session_summary: '', draft_parent_email: '' },
};
const marcus = { student_id: 'stu_101', first_name: 'Marcus', last_initial: 'A.' };
const noop = () => {};
const modal = (s, props = {}) => renderToStaticMarkup(
  <SessionModal session={s} student={marcus} onClose={noop} onSave={noop} onApprove={noop} reviewer={null} {...props} />
);

describe('recordedLabel: school time (America/New_York)', () => {
  test('zoneless device time (what the pipeline stores) is shown as is, like the Recorded column', () => {
    expect(recordedLabel('2026-10-01T10:17:00')).toBe('Oct 1, 2026 · 10:17 AM');
    expect(recordedLabel('2026-10-01T00:05:00')).toBe('Oct 1, 2026 · 12:05 AM');
    expect(recordedLabel('2026-10-01T12:30:00')).toBe('Oct 1, 2026 · 12:30 PM');
    expect(recordedLabel('2026-09-28T16:45:00')).toBe('Sep 28, 2026 · 4:45 PM');
  });

  test('UTC / offset times are converted to Eastern, daylight saving included', () => {
    expect(recordedLabel('2026-10-01T14:17:00Z')).toBe('Oct 1, 2026 · 10:17 AM');          // EDT, UTC-4
    expect(recordedLabel('2026-12-01T15:05:00Z')).toBe('Dec 1, 2026 · 10:05 AM');          // EST, UTC-5
    expect(recordedLabel('2026-10-02T02:30:00+00:00')).toBe('Oct 1, 2026 · 10:30 PM');     // date rolls back
    expect(recordedLabel('2026-09-24T10:14:00-04:00')).toBe('Sep 24, 2026 · 10:14 AM');
    expect(recordedLabel('2026-03-08T06:59:00Z')).toBe('Mar 8, 2026 · 1:59 AM');           // just before spring-forward
    expect(recordedLabel('2026-03-08T07:00:00Z')).toBe('Mar 8, 2026 · 3:00 AM');           // just after
    expect(recordedLabel('2026-11-01T05:30:00Z')).toBe('Nov 1, 2026 · 1:30 AM');           // 1:30 EDT
    expect(recordedLabel('2026-11-01T06:30:00Z')).toBe('Nov 1, 2026 · 1:30 AM');           // 1:30 EST
  });

  test('date only, missing or unreadable', () => {
    expect(recordedLabel('2026-09-21')).toBe('Sep 21, 2026');
    expect(recordedLabel(null)).toBeNull();
    expect(recordedLabel('')).toBeNull();
    expect(recordedLabel('yesterday')).toBeNull();
  });
});

describe('classroom', () => {
  test('the classroom the list shows (class_code) wins over the room code', () => {
    expect(classroomLabel({ class_code: 'Reading 3A', recording_id: RID })).toBe('Reading 3A');
    expect(classroomLabel({ class_code: 'RM12', recording_id: RID })).toBe('RM12');
  });

  test('room code from the recording id only when there is no classroom name', () => {
    expect(roomFromRecordingId(RID)).toBe('RM12');
    expect(classroomLabel({ class_code: null, recording_id: RID })).toBe('RM12');
    expect(classroomLabel({ class_code: '  ', recording_id: RID })).toBe('RM12');
    expect(classroomLabel({ recording_id: 'rec_20260921_marcus' })).toBeNull();
  });

  test('title: date · time · classroom; sample sessions use the matrix day', () => {
    expect(sessionTitle(session)).toBe('Oct 1, 2026 · 10:17 AM · Reading 3A');
    expect(sessionTitle({ ...session, class_code: null })).toBe('Oct 1, 2026 · 10:17 AM · RM12');
    expect(sessionTitle({ recording_id: 'rec_20260921_marcus' }, { dayDate: '2026-09-21' })).toBe('Sep 21, 2026');
    expect(sessionTitle({ recording_id: 'rec_20260921_marcus' })).toBe('Session');
  });
});

describe('session modal header', () => {
  test('title is the recorded time + classroom; no eyebrow, no recording id', () => {
    const html = modal(session);
    expect(between(html, 'session-title')).toBe('Oct 1, 2026 · 10:17 AM · Reading 3A');
    expect(html).toContain('aria-labelledby="session-modal-title"');
    expect(html).toContain('id="session-modal-title"');
    const head = text(/<header class="modal-head">(.*?)<\/header>/.exec(html)[1]);
    expect(head).not.toMatch(/session detail|hitl review/i);
    expect(head).not.toContain(RID);
    expect(head).not.toContain('rec_');
    expect(html).not.toContain('class="eyebrow"');
  });

  test('line under the title: student · length · status', () => {
    expect(between(modal(session), 'session-subtitle')).toBe('Marcus A. · 25 min · status: pending_review');
    const noStudent = renderToStaticMarkup(
      <SessionModal session={session} student={null} onClose={noop} onSave={noop} onApprove={noop} reviewer={null} />
    );
    expect(between(noStudent, 'session-subtitle')).toBe('Student · 25 min · status: pending_review');
  });

  test('approved: subtitle still carries who approved', () => {
    const html = modal({ ...session, status: 'approved', approved_at: '2026-10-01T15:00:00+00:00',
      approved_by_name: 'Sarah Jenkins' });
    const sub = between(html, 'session-subtitle');
    expect(sub).toMatch(/^Marcus A\. · 25 min · status: approved · approved by Sarah Jenkins at /);
  });

  test('sample (weekly matrix) session: day from the matrix, no classroom', () => {
    const html = modal({ recording_id: 'rec_20260921_marcus', status: 'approved', duration_seconds: 2700,
      processed_analysis: session.processed_analysis }, { dayDate: '2026-09-21' });
    expect(between(html, 'session-title')).toBe('Sep 21, 2026');
    expect(between(html, 'session-subtitle')).toBe('Marcus A. · 45 min · status: approved');
  });
});
