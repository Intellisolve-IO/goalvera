// Incident numbering + one incident's print view.
// The server decides who may print and what may be printed (review_api/incident_print.py,
// tests/test_incident_print.py); these prove the screen numbers incidents the same way,
// only offers Download PDF where the server would allow it, and prints nothing beyond
// the server's allowlisted report fields.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SessionModal from './components/SessionModal.jsx';
import IncidentPrint, { IncidentReport } from './components/IncidentPrint.jsx';
import { clock, clockAt, formatClockTime, incidentLabel, incidentTitle, numberIncidents, timeLine } from './incidents.js';

const SCHOOL_ID = 'ATL-0042317';
const LAST_NAME = 'Anderson';
const DOB = '2015-04-03';
const inc = (t, b) => ({ timestamp_seconds: t, antecedent: 'a', behavior: b, consequence_intervention: 'c' });
const analysis = {
  goal_evaluations: [],
  incident_analysis: [inc(75, 'Kicked-the-bin'), inc(3, 'Covered-ears'), inc(3725, 'Left-the-room')],
  general_session_summary: 'Calm session.',
};
const base = {
  recording_id: 'rec_a1b2c3d4e5f6_20261007_132337_Math_2B',
  source: 'pipeline',
  status: 'approved',
  approved_at: '2026-10-07T17:30:00+00:00',
  duration_seconds: 4000,
  class_code: 'Math 2B',
  teacher_name: 'Sarah Jenkins',
  needs_class_assignment: false,
  viewer_may_act: true,
  processed_analysis: analysis,
};
const noop = () => {};
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const modal = (session, props = {}) => renderToStaticMarkup(
  <SessionModal session={session} student={null} onClose={noop} onSave={noop} onApprove={noop}
    reviewer={null} {...props} />
);
const report = {
  recording_id: base.recording_id, number: 1, offset_seconds: 3, of: 3,
  label: 'Incident 1 \u00b7 0:03', title: 'Incident 1 \u00b7 0:03 \u2013 Math 2B \u2013 Oct 7, 2026',
  file_title: 'Incident-1_0-03_Math-2B_2026-10-07', student: 'Marcus A.', class_code: 'Math 2B',
  recorded: 'Oct 7, 2026', teacher_name: 'Sarah Jenkins', approved_on: 'Oct 7, 2026',
  fields: [{ label: 'Antecedent', text: 'Asked to wait.' }, { label: 'Behavior', text: 'Covered his ears.' },
    { label: 'Consequence / intervention', text: 'Headphones offered.' }],
  footer: 'Confidential, contains student information', sample_data: true,
};

describe('numbering', () => {
  test('m:ss, h:mm:ss past an hour', () => {
    expect([0, 3, 75, 600, 3599, 3600, 3725, null, -2, 'x'].map(clock)).toEqual(
      ['0:00', '0:03', '1:15', '10:00', '59:59', '1:00:00', '1:02:05', '0:00', '0:00', '0:00']);
    expect(incidentLabel(1, 3)).toBe('Incident 1 \u00b7 0:03');
  });

  test('earliest first, ties keep their order', () => {
    const list = [inc(75, 'x'), inc(3, 'y'), inc(75, 'z')];
    expect(numberIncidents(list).map((n) => [n.number, n.index])).toEqual([[1, 1], [2, 0], [3, 2]]);
  });

  test('the session shows "Incident N" + time, earliest first, and no bare timestamp label', () => {
    for (const status of ['approved', 'pending_review']) {
      const html = modal({ ...base, status, approved_at: status === 'approved' ? base.approved_at : null });
      const labels = [...html.matchAll(/data-testid="incident-label">([^<]+)</g)].map((m) => m[1]);
      expect(labels).toEqual(['Incident 1', 'Incident 2', 'Incident 3']);       // no names yet: plain number
      const times = [...html.matchAll(/data-testid="incident-time">([^<]+)</g)].map((m) => m[1].trim());
      expect(times).toEqual(['0:03 into recording', '1:15 into recording', '1:02:05 into recording']);
      const t = text(html);
      expect(t.indexOf('Covered-ears')).toBeLessThan(t.indexOf('Kicked-the-bin'));
      expect(t.indexOf('Kicked-the-bin')).toBeLessThan(t.indexOf('Left-the-room'));
      expect(html).not.toMatch(/Timestamp \(sec\)|>\s*Timestamp\s*</i);
    }
  });
});

describe('Download PDF on each incident', () => {
  test('approved, own session: one enabled button per incident', () => {
    const html = modal(base);
    for (const n of [1, 2, 3]) {
      const b = html.match(new RegExp(`<button[^>]*data-testid="incident-pdf-${n}"[^>]*>`))[0];
      expect(b).not.toContain('disabled');
    }
  });

  test('not approved yet: enabled, says it prints as a draft', () => {
    const html = modal({ ...base, status: 'pending_review', approved_at: null });
    expect(html.match(/<button[^>]*data-testid="incident-pdf-1"[^>]*>/)[0]).not.toContain('disabled');
    expect(html).toContain('Prints as a draft until the session is approved.');
    expect(modal(base)).not.toContain('Prints as a draft');
  });

  test('RBT and someone else’s session: no button at all', () => {
    expect(modal({ ...base, viewer_read_only: true, viewer_may_act: false }, { readOnlyUser: true }))
      .not.toContain('incident-pdf-');
    expect(modal({ ...base, viewer_may_act: false })).not.toContain('incident-pdf-');
  });
});

describe('print view (IE8–IE10)', () => {
  test('the report as formatted, branded, with the confidentiality footer', () => {
    const html = renderToStaticMarkup(<IncidentReport report={report} />);
    const t = text(html);
    expect(html).toContain('data-testid="incident-print-title">Incident 1 · 0:03<');
    for (const s of ['Goalvera', 'Sample Elementary School', 'Marcus A.', 'Math 2B', 'Sarah Jenkins', 'Covered his ears.',
      'Antecedent', 'Behavior', 'Consequence / intervention']) expect(t).toContain(s);
    expect(html).toMatch(/data-testid="incident-print-footer">Confidential, contains student information</);
  });

  test('renders only the allowlisted fields: never a school id, full last name or DOB', () => {
    const tainted = {
      ...report, school_id: SCHOOL_ID, last_name: LAST_NAME, dob: DOB, student_last_name: LAST_NAME,
      extra: { date_of_birth: DOB },
    };
    const html = renderToStaticMarkup(<IncidentPrint report={tainted} onClose={noop} autoPrint={false} />);
    for (const never of [SCHOOL_ID, LAST_NAME, DOB]) expect(html).not.toContain(never);
  });

  test('DRAFT watermark and draft line only on drafts; approved report unchanged', () => {
    const draft = {
      ...report, draft: true, status_line: 'Draft, not yet approved by the teacher', approved_on: 'Not yet approved',
      title: `DRAFT \u2013 ${report.title}`, file_title: `DRAFT_${report.file_title}`,
    };
    const d = renderToStaticMarkup(<IncidentReport report={draft} />);
    expect(d).toContain('data-testid="incident-print-watermark"');
    expect(d).toMatch(/data-testid="incident-print-watermark">DRAFT</);
    expect(d).toMatch(/data-testid="incident-print-status">Draft, not yet approved by the teacher</);
    expect(d).toContain('class="ip-sheet ip-draft"');
    expect(d).toContain('Draft notes, downloaded');
    expect(d).toMatch(/incident-print-footer">Confidential, contains student information</);
    for (const r of [report, { ...report, draft: false, status_line: null }]) {
      const a = renderToStaticMarkup(<IncidentReport report={r} />);
      expect(a).not.toContain('incident-print-watermark');
      expect(a).not.toContain('incident-print-status');
      expect(a).not.toMatch(/DRAFT|Draft, not yet approved|ip-draft/);
      expect(a).toContain('Approved notes, downloaded');
    }
    expect(draft.file_title).toBe('DRAFT_Incident-1_0-03_Math-2B_2026-10-07');
  });

  test('the document title (= default file name) is the file-safe incident title', () => {
    expect(report.file_title).toMatch(/^Incident-1_0-03_[A-Za-z0-9-]+_\d{4}-\d{2}-\d{2}$/);
    for (const never of [SCHOOL_ID, LAST_NAME, DOB, 'Marcus']) expect(report.file_title).not.toContain(never);
  });
});

// Incident names + Eastern clock time
describe('names and clock time', () => {
  test('title: "Incident N: name", or plain "Incident N"', () => {
    expect(incidentTitle(1, 'Left seat and refused redirection')).toBe('Incident 1: Left seat and refused redirection');
    expect(incidentTitle(2, '')).toBe('Incident 2');
    expect(incidentTitle(3, null)).toBe('Incident 3');
  });

  test('clock time is Eastern: start + offset, "1:02 PM · 12:34 into recording"', () => {
    expect(clockAt('2026-10-08T12:49:26-04:00', 754)).toBe('1:02 PM');
    expect(timeLine('2026-10-08T12:49:26-04:00', 754)).toBe('1:02 PM \u00b7 12:34 into recording');
    expect(clockAt('2026-10-08T16:49:26Z', 754)).toBe('1:02 PM');            // UTC start, shown in Eastern
    expect(clockAt('2026-12-08T17:49:26Z', 754)).toBe('1:02 PM');            // winter: EST (UTC-5)
    expect(timeLine(null, 754)).toBe('12:34 into recording');                // no start time: offset only
    expect(clockAt('garbage', 3)).toBe(null);
  });

  test('DST edges: spring forward and fall back', () => {
    // Mar 8 2026, 1:59 AM EST + 90 s -> 3:00 AM EDT (2 AM never happens)
    expect(clockAt('2026-03-08T01:59:00-05:00', 90)).toBe('3:00 AM');
    // Nov 1 2026, 1:59 AM EDT + 90 s -> 1:00 AM EST (the second 1 AM)
    expect(clockAt('2026-11-01T01:59:00-04:00', 90)).toBe('1:00 AM');
  });

  test('the session shows the name and the clock time; a teacher can edit the name before approval', () => {
    const named = { ...analysis, incident_analysis: [{ ...inc(3, 'Covered-ears'), name: 'Covered ears during group reading', name_source: 'ai' }] };
    const s = { ...base, processed_analysis: named, recording_start: '2026-10-07T13:23:37-04:00' };
    const approved = modal(s);
    expect(approved).toContain('data-testid="incident-label">Incident 1: Covered ears during group reading<');
    expect(text(approved)).toContain('1:23 PM \u00b7 0:03 into recording');
    expect(approved).not.toContain('incident-name-1');                     // approved: locked
    const draft = modal({ ...s, status: 'pending_review', approved_at: null });
    const box = draft.match(/<input[^>]*data-testid="incident-name-1"[^>]*>/)[0];
    expect(box).toContain('maxLength="80"');
    expect(box).toContain('value="Covered ears during group reading"');
  });

  test('print view: title, clock time at the top; the AI name never in the file name', () => {
    const r = { ...report, heading: 'Incident 1: Covered ears during group reading', name: 'Covered ears during group reading',
      time_line: '1:23 PM \u00b7 0:03 into recording', clock_time: '1:23 PM', file_title: 'Incident-1_1-23PM_Math-2B_2026-10-07' };
    const html = renderToStaticMarkup(<IncidentReport report={r} />);
    expect(html).toContain('data-testid="incident-print-title">Incident 1: Covered ears during group reading<');
    expect(html).toContain('data-testid="incident-print-time">1:23 PM \u00b7 0:03 into recording<');
    expect(html.indexOf('incident-print-time')).toBeLessThan(html.indexOf('ip-facts'));
    expect(r.file_title).not.toMatch(/Covered/);
  });

  test('no draft parent email on the session screen (weekly summary now)', () => {
    const html = modal({ ...base, status: 'pending_review', approved_at: null,
      processed_analysis: { ...analysis, draft_parent_email: 'Hello family, PARENT-TEXT.' } });
    expect(html).not.toContain('PARENT-TEXT');
    expect(html).not.toMatch(/Draft parent email/);
  });

  test('the time is read-only at the top of each incident; no clock or seconds box (Oct 8)', () => {
    const named = { ...analysis, incident_analysis: [inc(754, 'Left-the-seat')] };
    for (const recording_start of ['2026-10-08T12:49:26-04:00', null]) {
      const draft = modal({ ...base, status: 'pending_review', approved_at: null, duration_seconds: 4000,
        recording_start, processed_analysis: named });
      expect(draft).not.toMatch(/incident-clock-|incident-seconds-|Time \(Eastern\)|Time in recording \(seconds\)/);
      const panel = draft.slice(draft.indexOf('data-testid="incident-1"'));
      expect(panel.indexOf('data-testid="incident-time"')).toBeLessThan(panel.indexOf('data-testid="incident-label"'));
      expect(panel.indexOf('data-testid="incident-time"')).toBeLessThan(panel.indexOf('<input'));
    }
    expect(text(modal({ ...base, status: 'pending_review', approved_at: null, recording_start: '2026-10-08T12:49:26-04:00',
      processed_analysis: named }))).toContain('1:02 PM \u00b7 12:34 into recording');
  });

  test('incidents are tabs under "Incident analysis": Incident 1, 2, ... with the name below', () => {
    const named = { ...analysis, incident_analysis: [{ ...inc(75, 'Kicked-the-bin'), name: 'Kicked the bin during math', name_source: 'ai' },
      inc(3, 'Covered-ears')] };
    const html = modal({ ...base, status: 'pending_review', approved_at: null, processed_analysis: named });
    expect(html).toContain('<h3>Incident analysis</h3>');
    const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*data-testid="incident-tab-(\d)"[^>]*>([\s\S]*?)<\/button>/g)];
    expect(tabs.map((m) => text(m[2]).trim())).toEqual(['Incident 1', 'Incident 2 Kicked the bin during math']);
    expect(tabs[0][0]).toContain('aria-selected="true"');
    expect(tabs[1][0]).toContain('title="Kicked the bin during math"');
    // the first incident's panel is shown; the others are hidden until their tab is clicked
    expect(html).toMatch(/data-testid="incident-1" role="tabpanel"(?![^>]*hidden)/);
    expect(html).toMatch(/data-testid="incident-2" role="tabpanel" hidden/);
    const one = modal({ ...base, status: 'pending_review', approved_at: null, processed_analysis: { ...analysis, incident_analysis: [inc(3, 'x')] } });
    expect(one.match(/data-testid="incident-tab-\d"/g)).toHaveLength(1);              // a single incident: one tab
  });

  test('no separate markers section; markers go to the timeline (Oct 8)', () => {
    const html = modal({
      ...base, status: 'pending_review', approved_at: null,
      recording_start: '2026-10-08T12:49:26-04:00',
      markers: [{ type: 'Incident', offset_seconds: 754 }, { type: 'Teacher Notes', offset_seconds: 784 }],
    });
    expect(html).not.toMatch(/markers-section|markers-line|<h3>Markers<\/h3>/);
  });
});
