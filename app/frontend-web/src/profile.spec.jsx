// My profile (David, Oct 10 2026; tasks#107): the signed-in name opens it; the user edits their own
// display name, preferred name, phone, default classroom (theirs only), title, credential type; role
// and email read-only ("Ask an admin to change this") unless the server flag allows self-edit. Admins
// change role / sign-in email from the Teachers tab.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import { ProfileForm } from './components/MyProfile.jsx';
import { AccessEditor, TeacherTable } from './components/Teachers.jsx';
import { profileError } from './schoolSetup.js';

const base = {
  person_id: 'stf_0001', roster_name: 'Alex Morgan', display_name: 'Alex Morgan', preferred_name: 'Alex',
  phone: '(404) 555-0100', title: 'Lead Special Education Teacher', credential_type: 'Special education certificate',
  default_classroom: 'Room 104', classrooms: ['Room 104'], email: 'snowwhite@example.org', role: 'teacher',
  role_label: 'Teacher', can_edit_access: false, credential_types: ['', 'Special education certificate', 'Other'],
};
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

describe('My profile form', () => {
  test('own fields are editable; role and email are read-only with "Ask an admin"', () => {
    const h = renderToStaticMarkup(<ProfileForm data={base} onSave={() => {}} />);
    for (const id of ['profile-display-name', 'profile-preferred-name', 'profile-phone', 'profile-default-classroom',
      'profile-title', 'profile-credential']) {
      expect(h).toContain(`data-testid="${id}"`);
    }
    expect(h).toContain('value="(404) 555-0100"');
    expect(h).toContain('data-testid="profile-email">snowwhite@example.org<');
    expect(h).toContain('data-testid="profile-role">Teacher<');
    expect(text(h)).toContain('Ask an admin to change this.');
    expect(h).not.toContain('profile-email-input');
    expect(h).not.toContain('profile-role-input');
  });

  test('the default classroom list holds only the person’s own classrooms', () => {
    const h = renderToStaticMarkup(<ProfileForm data={base} onSave={() => {}} />);
    const sel = h.slice(h.indexOf('profile-default-classroom'), h.indexOf('</select>', h.indexOf('profile-default-classroom')));
    expect([...sel.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1])).toEqual(['', 'Room 104']);
  });

  test('RBT and admin get the same form; admins pick "All classrooms" as the empty default', () => {
    const r = renderToStaticMarkup(<ProfileForm data={{ ...base, role: 'rbt', role_label: 'RBT (read-only)' }} onSave={() => {}} />);
    expect(r).toContain('RBT (read-only)');
    expect(r).toContain('data-testid="profile-save"');
    const a = renderToStaticMarkup(<ProfileForm data={{ ...base, role: 'admin', role_label: 'Admin' }} onSave={() => {}} />);
    expect(a).toContain('All classrooms');
  });

  test('with the server flag on, role and email become editable', () => {
    const h = renderToStaticMarkup(<ProfileForm data={{ ...base, can_edit_access: true }} onSave={() => {}} />);
    expect(h).toContain('profile-email-input');
    expect(h).toContain('profile-role-input');
    expect(h).not.toContain('Ask an admin');
  });

  test('server errors in plain words', () => {
    expect(profileError(new Error('ASK_AN_ADMIN'))).toMatch(/Ask an admin/);
    expect(profileError(new Error('CLASSROOM_NOT_ALLOWED'))).toMatch(/your own classrooms/);
    expect(profileError(new Error('LAST_ADMIN'))).toMatch(/at least one admin/);
    expect(profileError(new Error('EMAIL_IN_USE'))).toMatch(/already signs someone in/);
  });
});

describe('Teachers tab: admins change role / sign-in email', () => {
  const data = {
    emails_shown: true, can_edit_access: true,
    teachers: [{ id: 'stf_0001', name: 'Alex Morgan', role: 'Lead Special Education Teacher', classrooms: ['Room 104'],
      emails: ['snowwhite@example.org'], sign_in_email: 'snowwhite@example.org', app_role: 'teacher' },
    { id: 'stf_0002', name: 'Belle Beaumont', role: 'Special Education Teacher', classrooms: ['Room 101'], emails: [],
      sign_in_email: null, app_role: 'none' }],
  };

  test('admins see each person’s app role and a Change button', () => {
    const h = renderToStaticMarkup(<TeacherTable data={data} onEdit={() => {}} />);
    expect(h).toContain('Sign-in (app role)');
    expect((h.match(/data-testid="access-change"/g) || []).length).toBe(2);
    expect(text(h)).toMatch(/Teacher · snowwhite@example.org/);
    expect(text(h)).toMatch(/No access/);
  });

  test('not an admin: no sign-in column, no Change', () => {
    const h = renderToStaticMarkup(<TeacherTable data={{ teachers: data.teachers.map(({ emails, sign_in_email, app_role, ...t }) => t) }} />);
    expect(h).not.toContain('Sign-in (app role)');
    expect(h).not.toContain('access-change');
  });

  test('the editor opens with the current email and role', () => {
    const h = renderToStaticMarkup(<AccessEditor row={data.teachers[0]} onSave={() => {}} onCancel={() => {}} />);
    expect(h).toContain('value="snowwhite@example.org"');
    expect(h).toMatch(/<option value="teacher" selected="">Teacher<\/option>/);
    expect(h).toContain('No access');
  });
});
