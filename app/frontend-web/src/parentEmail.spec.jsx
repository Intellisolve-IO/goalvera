// Weekly parent email (tasks#106): the teacher sees the demo redirect, the recipients (guardians with
// "receives reports" on), the excluded contacts and exactly what will be sent; one click sends.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import { ParentEmailView } from './components/ParentEmailPanel.jsx';
import { ParentEmailSetting } from './components/Settings.jsx';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
const P = {
  student_id: 'stu_0006', week: '2026-10-05', redirect: true, redirect_to: 'snowwhite@staff.example',
  from: 'notifications@staff.example', problem: null, can_send: true, manual_only: true, last_sent: null,
  preview_sha256: 'abc',
  contacts: [
    { contact_id: 'con_0014', first_name: 'Wren', relationship: 'foster parent', email: 'wren.hollis59@example.org', receives_reports: true },
    { contact_id: 'con_0015', first_name: 'Jamal', relationship: 'aunt', email: null, receives_reports: false },
    { contact_id: 'con_0218', first_name: 'Rory', relationship: 'non-custodial parent', email: 'rory.dwarf@example.com', receives_reports: false },
  ],
  emails: [{ contact_id: 'con_0014', guardian_first_name: 'Wren', to: 'snowwhite@staff.example',
    subject: "Sneezy's week of October 5", body: 'Demo: would have gone to Wren at w****************@example.org\n\nSneezy had a calm week.' }],
  excluded: [{ first_name: 'Jamal' }, { first_name: 'Rory' }],
};

describe('Send weekly email', () => {
  test('demo banner, recipients, excluded contacts and the exact email', () => {
    const t = text(renderToStaticMarkup(<ParentEmailView p={P} onSend={() => {}} />));
    expect(t).toContain('Demo mode: parent emails go to snowwhite@staff.example, not to parents.');
    expect(t).toContain('Wren foster parent wren.hollis59@example.org receives reports');
    expect(t).toContain('Rory non-custodial parent rory.dwarf@example.com excluded: not set to receive reports');
    expect(t).toContain('Jamal aunt none on file excluded: no email');
    expect(t).toContain('To: snowwhite@staff.example (instead of Wren)');
    expect(t).toContain('Demo: would have gone to Wren at w****************@example.org');
    expect(t).toContain('Send weekly email (demo: to snowwhite@staff.example)');
    expect(t).toContain('Sent only when you click.');
  });

  test('not approved yet: no preview, button disabled, reason shown', () => {
    const h = renderToStaticMarkup(<ParentEmailView p={{ ...P, problem: 'NOT_APPROVED', can_send: false }} onSend={() => {}} />);
    expect(h).not.toContain('send-preview');
    expect(h).toMatch(/<button[^>]*disabled=""[^>]*data-testid="send-weekly-email"/);
    expect(text(h)).toContain('Approve the weekly report');
  });

  test('admin setting shows the redirect', () => {
    const t = text(renderToStaticMarkup(<ParentEmailSetting initial={{ redirect: true, redirect_domain: 'staff.example',
      sender: 'notifications@staff.example', mail_state: 'outbox' }} />));
    expect(t).toContain('Weekly parent email: demo redirect');
    expect(t).toContain('On · every parent email goes to the teacher’s own staff.example address instead');
  });
});
