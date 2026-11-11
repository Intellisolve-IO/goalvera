// School name + logo setting: top bar brand, Settings › School,
// and the calls the browser makes. The server re-checks everything (tests/test_branding.py).
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

vi.mock('firebase/app', () => ({ initializeApp: () => ({}) }));
vi.mock('firebase/auth', () => ({
  initializeAuth: () => ({ currentUser: null }), indexedDBLocalPersistence: {}, browserLocalPersistence: {},
  connectAuthEmulator: () => {}, onIdTokenChanged: () => () => {}, sendEmailVerification: async () => {},
  sendPasswordResetEmail: async () => {}, signInWithEmailAndPassword: async () => {}, signOut: async () => {},
}));

const br = await import('./branding.js');
const { default: SchoolBrand } = await import('./components/SchoolBrand.jsx');
const { default: SchoolSettings } = await import('./components/SchoolSettings.jsx');
const { SettingsView } = await import('./components/Settings.jsx');

const custom = { ...br.DEFAULT_BRANDING, school_name: 'Peachtree Learning Academy', is_default: false,
  logo: { content_type: 'image/png', url: '/api/branding/logo?v=abc' } };

afterEach(() => vi.unstubAllGlobals());

describe('top bar brand', () => {
  test('default: Everyday Heroes Academy with the EH badge (until the built-in logo loads)', () => {
    const html = renderToStaticMarkup(<SchoolBrand branding={br.DEFAULT_BRANDING} />);
    expect(html).toContain('>Everyday Heroes Academy<');
    expect(html).toContain('data-testid="school-badge"');
    expect(html).toContain('>EH<');
    expect(html).not.toContain('<img');
  });
  test('custom name and logo; the name keeps a tooltip and the one-line (ellipsis) class', () => {
    const html = renderToStaticMarkup(<SchoolBrand branding={custom} logoUrl="blob:http://x/1" />);
    expect(html).toContain('src="blob:http://x/1"');
    expect(html).toContain('class="school-name" title="Peachtree Learning Academy"');
    expect(html).not.toContain('school-badge');
  });
});

describe('Settings › School', () => {
  test('admins get the name box and logo upload (PNG, JPEG, SVG)', () => {
    const html = renderToStaticMarkup(<SchoolSettings branding={custom} logoUrl="blob:x" canEdit />);
    expect(html).toContain('data-testid="school-name-input"');
    expect(html).toContain('accept="image/png,image/jpeg,image/svg+xml"');
    expect(html).toContain('data-testid="school-logo-remove"');
  });
  test('everyone else reads it only', () => {
    const html = renderToStaticMarkup(<SchoolSettings branding={custom} canEdit={false} />);
    expect(html).not.toMatch(/<(input|select|textarea|button)/);
    expect(html).toContain('Peachtree Learning Academy');
    expect(html).toContain('Read-only · admins change this');
  });
  test('the School section sits at the top of the Settings tab', () => {
    const s = { access: {}, retention: {}, roster: {}, classrooms: [], teacher_defaults: [], ai: {}, journal: {}, bug_reports: {} };
    const html = renderToStaticMarkup(<SettingsView s={s} school={<SchoolSettings branding={custom} />} />);
    expect(html.indexOf('school-settings')).toBeLessThan(html.indexOf('<h3>Access</h3>'));
  });
});

describe('checks and calls', () => {
  test('file and name checks before sending', () => {
    expect(br.logoProblem({ type: 'image/gif', size: 10 })).toMatch(/PNG, JPEG or SVG/);
    expect(br.logoProblem({ type: 'image/svg+xml', size: br.LOGO_MAX_BYTES + 1 })).toMatch(/1 MB/);
    expect(br.logoProblem({ type: 'image/svg+xml', size: 900 })).toBe('');
    expect(br.nameProblem('  ')).toMatch(/Enter/);
    expect(br.nameProblem('x'.repeat(81))).toMatch(/80/);
  });
  test('the logo goes up as the raw file with its own content type', async () => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, opts = {}) => {
      calls.push([url, opts]);
      return new Response(JSON.stringify(custom), { status: 200 });
    }));
    const file = new Blob(['<svg/>'], { type: 'image/svg+xml' });
    const b = await br.uploadLogo(file);
    expect(b.school_name).toBe('Peachtree Learning Academy');
    expect(calls[0][0]).toBe('/api/admin/branding/logo');
    expect(calls[0][1].method).toBe('POST');
    expect(calls[0][1].headers['Content-Type']).toBe('image/svg+xml');
    expect(calls[0][1].body).toBe(file);
  });
  test('a teacher trying to save gets the admins-only message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'ADMIN_ONLY' }), { status: 403 })));
    const e = await br.saveSchoolName('X').catch((x) => x);
    expect(br.brandingErrorText(e)).toBe('Only admins can change this.');
  });
  test('top bar falls back to the defaults if the server is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
    expect((await br.loadBranding()).school_name).toBe('Everyday Heroes Academy');
  });
});
