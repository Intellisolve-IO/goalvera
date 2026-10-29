// Admin journal + activity report (tasks#77): who sees the buttons, and that the
// CSV is fetched with the signed-in user's token from the right URL.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const TOKEN = 'header.payload.signature';
vi.mock('firebase/app', () => ({ initializeApp: () => ({}) }));
vi.mock('firebase/auth', () => ({
  initializeAuth: () => ({ currentUser: { getIdToken: async () => TOKEN } }),
  indexedDBLocalPersistence: {},
  browserLocalPersistence: {},
  connectAuthEmulator: () => {},
  onIdTokenChanged: () => () => {},
  sendEmailVerification: async () => {},
  sendPasswordResetEmail: async () => {},
  signInWithEmailAndPassword: async () => {},
  signOut: async () => {},
}));

const { loadAuthConfig } = await import('./auth.js');
const api = await import('./adminApi.js');
const { default: AdminJournal } = await import('./components/AdminJournal.jsx');

const me = (role, extra = {}) => ({ email: `${role}@example.org`, email_verified: true, approved: true, role, ...extra });
const journalTab = () => renderToStaticMarkup(
  <AdminJournal inline initial={{ loading: false, entries: [], more: false, error: null }} load={async () => ({ entries: [], more: false })} />);

describe('Download report lives in the Activity Log tab (Oct 10; tasks#107)', () => {
  test('the activity report button is on the Activity Log page, not at the top right', () => {
    const html = journalTab();
    expect(html).toContain('data-testid="open-report"');
    expect(html).toContain('Download report (CSV)');
    expect(html).not.toContain('open-journal');
    expect(html).not.toMatch(/Reset demo state|Value metrics|Sessions processed|min saved|Cost \/ session/);
  });

  test('the journal lists the new My profile / access actions', () => {
    expect(journalTab()).toMatch(/Profile updated[\s\S]*Access changed/);
  });
});

describe('activity report download', () => {
  let calls;
  beforeEach(async () => {
    calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, opts = {}) => {
      calls.push({ url, opts });
      if (url === '/api/auth-config') {
        return new Response(JSON.stringify({ apiKey: 'k', authDomain: 'd', projectId: 'p' }), { status: 200 });
      }
      if (url.startsWith('/api/admin/reports/')) {
        return new Response('person,role,total\nSarah J.,teacher,4\n', {
          status: 200,
          headers: { 'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="activity-by-student_2026-09-28_2026-10-04.csv"' },
        });
      }
      return new Response(JSON.stringify({ error: 'ADMIN_ONLY' }), { status: 403 });
    }));
    await loadAuthConfig();                       // hosted mode: authFetch now adds the ID token
  });
  afterEach(() => vi.unstubAllGlobals());

  test('builds the report URL and sends the Bearer token, then saves the file', async () => {
    const saved = [];
    const name = await api.downloadActivityReport({ by: 'student', from: '2026-09-28', to: '2026-10-04' },
      { save: (blob, n) => saved.push({ blob, n }) });
    const call = calls.find((c) => c.url.startsWith('/api/admin/'));
    expect(call.url).toBe('/api/admin/reports/activity.csv?by=student&from=2026-09-28&to=2026-10-04');
    expect(call.opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(name).toBe('activity-by-student_2026-09-28_2026-10-04.csv');
    expect(saved).toHaveLength(1);
    expect(await saved[0].blob.text()).toContain('Sarah J.,teacher,4');
  });

  test('by person is the default grouping in the URL', () => {
    expect(api.reportUrl({ from: '2026-10-01', to: '2026-10-04' }))
      .toBe('/api/admin/reports/activity.csv?by=person&from=2026-10-01&to=2026-10-04');
  });

  test('a 403 comes back as a clear admin-only message, nothing saved', async () => {
    const saved = [];
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'ADMIN_ONLY' }), { status: 403 })));
    const err = await api.downloadActivityReport({ by: 'person', from: '2026-10-01', to: '2026-10-04' },
      { save: () => saved.push(1) }).catch((e) => e);
    expect(err.status).toBe(403);
    expect(err.code).toBe('ADMIN_ONLY');
    expect(api.errorText(err, 'the activity report')).toMatch(/Only admins.*403/);
    expect(saved).toHaveLength(0);
  });
});

describe('journal', () => {
  afterEach(() => vi.unstubAllGlobals());

  test('a journal read failure names the reason instead of "502, INTERNAL"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ error: 'JOURNAL_READ_FAILED', reason: 'ACCESS_DENIED' }), { status: 503 })));
    const err = await api.loadJournal({ from: '2026-10-01', to: '2026-10-07' }).catch((e) => e);
    expect([err.status, err.code, err.reason]).toEqual([503, 'JOURNAL_READ_FAILED', 'ACCESS_DENIED']);
    const text = api.errorText(err, 'the journal');
    expect(text).toMatch(/isn’t allowed to read it/);
    expect(text).toMatch(/still being recorded/);
    expect(text).toContain('ACCESS_DENIED');
    expect(text).not.toMatch(/INTERNAL/);
  });

  test('asks for one extra UTC day and keeps Eastern days only, newest first, with the token', async () => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, opts = {}) => {
      calls.push({ url, opts });
      if (url === '/api/auth-config') {
        return new Response(JSON.stringify({ apiKey: 'k', authDomain: 'd', projectId: 'p' }), { status: 200 });
      }
      return new Response(JSON.stringify({ entries: [
        { event_id: 'a', occurred_at: '2026-10-03T23:30:00+00:00', action: 'sign_in' },          // Oct 3 7:30 PM ET
        { event_id: 'b', occurred_at: '2026-10-05T01:00:00+00:00', action: 'sign_in' },          // Oct 4 9 PM ET
        { event_id: 'c', occurred_at: '2026-10-05T05:00:00+00:00', action: 'sign_in' },          // Oct 5 1 AM ET: out
        { event_id: 'd', occurred_at: '2026-09-28T02:00:00+00:00', action: 'sign_in' },          // Sep 27 ET: out
      ] }), { status: 200 });
    }));
    await loadAuthConfig();
    const r = await api.loadJournal({ from: '2026-09-28', to: '2026-10-04', limit: 4 });
    const call = calls.find((c) => c.url.startsWith('/api/admin/'));
    expect(call.url).toBe('/api/admin/journal?from=2026-09-28&to=2026-10-05&limit=4');
    expect(call.opts.headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(r.entries.map((e) => e.event_id)).toEqual(['b', 'a']);
    expect(r.more).toBe(true);                     // the server filled the page: there may be more
  });

  test('default range is the last 7 days, Eastern', () => {
    expect(api.lastDays(7, new Date('2026-10-04T02:00:00Z'))).toEqual({ from: '2026-09-27', to: '2026-10-03' });
    expect(api.etDate('2026-10-04T17:51:00Z')).toBe('2026-10-04');
    expect(api.formatEt('2026-10-04T17:51:07Z')).toBe('Oct 4, 2026, 1:51:07 PM ET');
  });

  test('rows show time in ET, the person and student the server gave, and the action', () => {
    const html = renderToStaticMarkup(<AdminJournal onClose={() => {}} initial={{ loading: false, more: true, error: null,
      entries: [{ event_id: 'e1', occurred_at: '2026-10-04T17:51:07+00:00', actor: 'Admin #a1b2c3', actor_id: 'uid1',
        actor_role: 'admin', action: 'session_viewed', record_type: 'session', record_id: 'rec_x',
        student: 'Student #9f8e7d', student_id: 'stu_101' }] }} />);
    expect(html).toContain('Oct 4, 2026, 1:51:07 PM ET');
    expect(html).toContain('Admin #a1b2c3');
    expect(html).toContain('Session viewed');
    expect(html).toContain('Student #9f8e7d');
    expect(html).toContain('data-testid="journal-more"');
  });

  test('empty and 403 states', () => {
    const empty = renderToStaticMarkup(<AdminJournal onClose={() => {}}
      initial={{ loading: false, entries: [], more: false, error: null }} />);
    expect(empty).toContain('data-testid="journal-empty"');
    expect(empty).toMatch(/No journal entries from \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}/);
    const denied = renderToStaticMarkup(<AdminJournal onClose={() => {}}
      initial={{ loading: false, entries: [], more: false, error: new api.AdminApiError(403, 'ADMIN_ONLY') }} />);
    expect(denied).toContain('data-testid="journal-error"');
    expect(denied).toMatch(/Only admins can open the journal/);
    expect(denied).not.toContain('journal-table');
  });

  test('date range checks', () => {
    expect(api.rangeProblem('2026-10-04', '2026-10-01')).toMatch(/after/);
    expect(api.rangeProblem('2025-01-01', '2026-10-04')).toMatch(/at most/);
    expect(api.rangeProblem('2026-09-28', '2026-10-04')).toBeNull();
  });

  test('action labels', () => {
    expect(api.actionLabel('session_viewed')).toBe('Session viewed');
    expect(api.actionLabel('pdf_downloaded')).toBe('PDF downloaded');
    expect(api.actionLabel('iep_viewed')).toBe('IEP viewed');
  });
});
