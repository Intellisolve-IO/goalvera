// Sign in with Google (tasks#108): the button shows only when the server lists "google.com" in
// auth-config; password sign-in stays. Access is still decided by the server (verified email, exact
// staff-list match, access not removed); the page only shows plain messages for Google's refusals.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';

vi.mock('firebase/app', () => ({ initializeApp: () => ({}) }));
vi.mock('firebase/auth', () => ({
  GoogleAuthProvider: class { setCustomParameters() {} static credentialFromError() { return null; } },
  browserLocalPersistence: {}, browserPopupRedirectResolver: {}, connectAuthEmulator: () => {},
  indexedDBLocalPersistence: {}, initializeAuth: () => ({}), linkWithCredential: async () => {},
  onIdTokenChanged: () => () => {}, sendEmailVerification: async () => {}, sendPasswordResetEmail: async () => {},
  signInWithEmailAndPassword: async () => {}, signInWithPopup: async () => {}, signOut: async () => {},
}));

const { SignInPage } = await import('./components/AuthGate.jsx');
const { authErrorText, googleEnabled } = await import('./auth.js');

describe('Sign in page', () => {
  test('Google on: "Sign in with Google" above the password form', () => {
    const h = renderToStaticMarkup(<SignInPage google />);
    expect(h).toContain('data-testid="google-signin"');
    expect(h).toContain('Sign in with Google');
    expect(h).toContain('It must match your email on the staff list.');
    expect(h.indexOf('google-signin')).toBeLessThan(h.indexOf('signin-form'));
    expect(h).toContain('data-testid="signin-form"');               // password sign-in stays
  });

  test('Google off: password form only', () => {
    const h = renderToStaticMarkup(<SignInPage google={false} />);
    expect(h).not.toContain('google-signin');
    expect(h).toContain('data-testid="signin-form"');
  });

  test('no auth-config loaded yet means no Google button', () => {
    expect(googleEnabled()).toBe(false);
  });

  test('plain messages for Google refusals', () => {
    expect(authErrorText({ code: 'auth/admin-restricted-operation' })).toBe(
      'That Google account isn’t set up for this site. Ask your school admin.');
    expect(authErrorText({ code: 'auth/account-exists-with-different-credential' })).toContain('Sign in with your password');
    expect(authErrorText({ code: 'auth/popup-blocked' })).toContain('Allow pop-ups');
    expect(authErrorText({ code: 'auth/operation-not-allowed' })).toContain('isn’t switched on');
  });
});
