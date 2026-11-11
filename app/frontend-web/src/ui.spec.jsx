// UI checks, rendered to HTML with react-dom/server. The server enforces every rule;
// these prove the screen doesn't offer what the server would refuse.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SessionModal from './components/SessionModal.jsx';
import PipelineSessions from './components/PipelineSessions.jsx';
import { SignInPage } from './components/AuthGate.jsx';

const PARENT_TEXT = 'Hello family, here is this week’s update about Marcus.';
const analysis = (withEmail) => ({
  goal_evaluations: [{ goal_id: 'g1', status: 'Demonstrated', evidence_quote: 'q', analysis: 'a' }],
  incident_analysis: [],
  general_session_summary: 'Calm session.',
  ...(withEmail ? { draft_parent_email: PARENT_TEXT } : {}),
});
const base = {
  recording_id: 'rec_laptop_demo_20260928_164500_RM12',
  source: 'pipeline',
  status: 'approved',
  approved_at: '2026-10-01T15:00:00+00:00',
  duration_seconds: 600,
  class_code: 'RM12',
  teacher_name: 'Sarah Jenkins',
  needs_class_assignment: false,
};
const noop = () => {};
const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const buttons = (html) => [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => text(m[1]).trim());
const modal = (session, props = {}) =>
  render(
    <SessionModal session={session} student={null} onClose={noop} onSave={noop} onApprove={noop}
      onChangeClassroom={noop} reviewer={null} classrooms={['RM12', 'Reading 3A']} {...props} />
  );

describe('sign-in page', () => {
  test('no "Create an account" link or sign-up form', () => {
    const html = render(<SignInPage />);
    expect(html).not.toMatch(/create an account|create account|sign up|signup/i);
    expect(buttons(html)).toEqual(['Sign in', 'Forgot password']);
  });
});

describe('RBT (read-only)', () => {
  const rbtSession = {
    ...base, viewer_read_only: true, viewer_may_act: false, can_approve: false,
    viewer_may_change_classroom: false, parent_contact_hidden: true, processed_analysis: analysis(false),
  };

  test('no edit, approve, export or move control renders', () => {
    for (const status of ['approved', 'pending_review']) {
      const html = modal({ ...rbtSession, status, approved_at: status === 'approved' ? base.approved_at : null },
        { readOnlyUser: true });
      expect(buttons(html)).toEqual(['Close']);
      expect(html).not.toContain('data-testid="download-pdf"');
      expect(html).not.toContain('data-testid="move-classroom"');
      expect(html).toContain('data-testid="read-only"');
      // every field is read-only, including who-is-who speaker tagging
      for (const m of html.matchAll(/<(textarea|select|input)\b[^>]*>/g)) expect(m[0]).toContain('disabled');
    }
  });

  test('no parent email, even if one were in the data (sample sessions in the page bundle)', () => {
    const html = modal({ ...rbtSession, processed_analysis: analysis(true), parent_contact_hidden: false },
      { readOnlyUser: true });
    expect(html).not.toContain(PARENT_TEXT);
    expect(text(html)).not.toMatch(/parent email/i);
  });
});

describe('teachers', () => {
  test('own session: edit, approve and move are offered; no parent email (weekly summary now)', () => {
    const html = modal({ ...base, status: 'pending_review', approved_at: null, viewer_may_act: true,
      can_approve: true, viewer_may_change_classroom: true, processed_analysis: analysis(true) });
    expect(buttons(html)).toEqual(expect.arrayContaining(['Save Draft', 'Approve', 'Move']));
    expect(html).not.toContain(PARENT_TEXT);              // off the session screen
    expect(text(html)).not.toMatch(/parent email/i);
    expect(html).toContain('<option value="Reading 3A">Reading 3A</option>');
    expect(html).not.toContain('<option value="RM12">');          // not its current classroom
  });

  test('another teacher’s session: read it, no edit, approve disabled, no parent email', () => {
    const html = modal({ ...base, status: 'pending_review', approved_at: null, viewer_may_act: false,
      can_approve: false, viewer_may_change_classroom: false, parent_contact_hidden: true,
      processed_analysis: analysis(false) });
    expect(buttons(html)).not.toContain('Save Draft');
    expect(buttons(html)).not.toContain('Move');
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Approve<\/button>/);
    expect(html).toContain('data-testid="not-your-session"');
    expect(text(html)).not.toMatch(/parent email/i);
  });
});

describe('labels say "Classroom"', () => {
  const sessions = [
    { ...base, recording_id: 'a', status: 'pending_review', class_code: 'RM12' },
    { ...base, recording_id: 'b', status: 'pending_review', class_code: 'Hall', teacher_name: '', needs_class_assignment: true },
  ];
  const bareClass = /\bclass(es)?\b/i;

  test('list, filters and badges', () => {
    const html = render(<PipelineSessions sessions={sessions} state={{ ok: true, loading: false }}
      getStudent={() => null} onOpen={noop} onReload={noop} />);
    expect(text(html)).toContain('Classroom');
    expect(text(html)).toContain('needs classroom assignment');
    expect(text(html)).not.toMatch(bareClass);
    for (const m of html.matchAll(/title="([^"]*)"/g)) expect(m[1]).not.toMatch(bareClass);
  });

  test('session screen, including the needs-a-classroom banner', () => {
    const html = modal({ ...sessions[1], approved_at: null, viewer_may_act: true,
      viewer_may_change_classroom: true, processed_analysis: analysis(true) });
    expect(text(html)).toContain('Needs classroom assignment');
    expect(text(html)).not.toMatch(bareClass);
  });

  test('the list opens on the teacher’s own classroom', () => {
    const html = render(<PipelineSessions sessions={sessions} state={{ ok: true, loading: false }}
      getStudent={() => null} onOpen={noop} onReload={noop} homeClassrooms={['RM12', 'Math 2B']} />);
    expect(text(html)).toContain('Showing 1 of 2');
    expect(html).toMatch(/Classroom<\/span><span class="filter-value">RM12</);
  });
});
