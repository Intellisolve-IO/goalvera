// Bug markers. The server decides what may reach the GitHub
// issue (review_api/bug_reports.py, tests/test_bug_reports.py); these prove the screen
// offers "Bug" in the marker type dropdown with the names hint, keeps Bug markers out of
// the markers line and the incident list / numbering, and shows them only as a small
// "Bug reported" item.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SessionModal from './components/SessionModal.jsx';
import BugMarkerForm from './components/BugMarkerForm.jsx';
import { BUG_AREAS, BUG_HINT, MARKER_TYPES, parseClock, sessionMarkers } from './bugMarkers.js';

const inc = (t, b) => ({ timestamp_seconds: t, antecedent: 'a', behavior: b, consequence_intervention: 'c' });
const base = {
  recording_id: 'rec_laptop_demo_20260929_131855_RM12',
  source: 'pipeline',
  status: 'pending_review',
  duration_seconds: 120,
  class_code: 'RM12',
  needs_class_assignment: false,
  viewer_may_act: true,
  markers: [{ type: 'Incident', offset_seconds: 49 }, { type: 'Bug', offset_seconds: 10 },
    { type: 'bug', offset_seconds: 83.1 }, { type: 'BUG', offset_seconds: 90 },      // recorder (firmware 0.6.7): lower case
    { type: 'Teacher Notes', offset_seconds: 92 }],
  processed_analysis: { goal_evaluations: [], incident_analysis: [inc(49, 'Covered-ears')], general_session_summary: '' },
  bug_reports: [
    { report_id: 'bug_20261007T230000_abc123', type: 'Bug', offset_seconds: 83, status: 'filed', issue_number: 501,
      issue_url: 'https://github.com/your-org/your-issues-repo/issues/501' },
    { report_id: 'bug_20261007T230500_def456', type: 'Bug', offset_seconds: null, status: 'pending' },
  ],
};
const noop = () => {};
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const modal = (session, props = {}) => renderToStaticMarkup(
  <SessionModal session={session} student={null} onClose={noop} onSave={noop} onApprove={noop}
    reviewer={null} {...props} />
);

describe('marker type dropdown', () => {
  test('"Bug" is a marker type a teacher can pick', () => {
    expect(MARKER_TYPES).toContain('Bug');
    const html = modal(base);
    const select = html.match(/<select[^>]*data-testid="marker-type"[^>]*>([\s\S]*?)<\/select>/)[1];
    expect([...select.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1])).toEqual(['', 'Bug']);
  });

  test('picking Bug shows the note box with the names hint under it', () => {
    const html = renderToStaticMarkup(<BugMarkerForm submit={noop} initialType="Bug" />);
    expect(html).toContain('data-testid="bug-note"');
    expect(html).toContain(`data-testid="bug-hint">${BUG_HINT.replace('’', '’')}<`);
    expect(BUG_HINT).toBe('Don’t include student names; this goes to our support team.');
    expect(html.indexOf('bug-note')).toBeLessThan(html.indexOf('bug-hint'));
    const areas = html.match(/<select[^>]*data-testid="bug-area"[^>]*>([\s\S]*?)<\/select>/)[1];
    expect([...areas.matchAll(/<option value="([^"]+)">/g)].map((m) => m[1])).toEqual(BUG_AREAS.map(([k]) => k));
  });

  test('RBTs (read-only) get no marker form', () => {
    expect(modal(base, { readOnlyUser: true })).not.toContain('data-testid="add-marker"');
    expect(modal({ ...base, viewer_read_only: true, bug_reports: [] })).not.toContain('data-testid="add-marker"');
  });

  test('"Add marker" is a small control just below the timeline, inside the transcript section', () => {
    const html = modal(base);
    const transcript = html.indexOf('<h3>Transcript</h3>');
    const toggle = html.indexOf('data-testid="add-marker-toggle"');
    expect(transcript).toBeGreaterThan(-1);
    expect(toggle).toBeGreaterThan(transcript);
    expect(toggle).toBeLessThan(html.indexOf('<h3>IEP goal evaluations</h3>'));
    expect(html).toMatch(/<summary>\+ Add marker<\/summary>/);
    expect(html.indexOf('data-testid="add-marker"')).toBeGreaterThan(toggle);       // same form inside it
    expect(html).toContain('data-testid="marker-type"');
  });

  test('time in recording: m:ss, h:mm:ss or seconds', () => {
    expect(['', '83', '1:23', '0:05', '1:02:05', '1:75', 'abc', '1:2'].map(parseClock)).toEqual(
      [null, 83, 83, 5, 3725, NaN, NaN, NaN]);
  });
});

describe('Bug markers stay out of markers, incidents and their numbering', () => {
  test('the markers line and the incident list never show a Bug marker', () => {
    expect(sessionMarkers(base.markers).map((m) => m.type)).toEqual(['Incident', 'Teacher Notes']);
    const html = modal(base);
    // no separate markers section: markers are drawn in the timeline only
    expect(html).not.toContain('markers-section');
    expect(html).not.toMatch(/<h3>Markers<\/h3>|markers-line/);
    const labels = [...html.matchAll(/data-testid="incident-label">([^<]+)</g)].map((m) => m[1]);
    expect(labels).toEqual(['Incident 1']);
    expect(html.match(/data-testid="incident-pdf-\d+"/g)).toHaveLength(1);
  });

  test('a recorder Bug marker shows as "Bug reported from recorder"', () => {
    const html = modal({ ...base, bug_reports: [{ report_id: 'bug_20261008T130000_aaaaaa', type: 'Bug', source: 'recorder',
      offset_seconds: 83, status: 'pending' }] });
    const items = [...html.matchAll(/<li[^>]*data-testid="bug-item"[^>]*>([\s\S]*?)<\/li>/g)].map((m) => text(m[1]).trim());
    expect(items).toEqual(['Bug reported from recorder · 1:23 · sent to our team']);
  });

  test('shown only as a small "Bug reported" item, with the issue link once filed', () => {
    const html = modal(base);
    const items = [...html.matchAll(/<li[^>]*data-testid="bug-item"[^>]*>([\s\S]*?)<\/li>/g)].map((m) => text(m[1]).trim());
    expect(items).toEqual(['Bug reported · 1:23 · issue #501', 'Bug reported · sent to our team']);
    expect(html).toContain('href="https://github.com/your-org/your-issues-repo/issues/501" target="_blank" rel="noopener noreferrer"');
    // the server sends bug_reports only to the reporter and admins; nothing when there are none
    expect(modal({ ...base, bug_reports: [] })).not.toContain('data-testid="bug-reports"');
  });
});
