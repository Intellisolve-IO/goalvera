/**
 * Sign-in for the hosted review screen: Google Cloud Identity Platform
 * (the Firebase Auth SDK talks to it). The server decides access: it checks
 * the ID token, the verified email and the allowlist on every API call.
 *
 * Providers come from the server's auth-config "providers" (IDP_PROVIDERS):
 * "password" always, and "google.com" once Sign in with Google is switched on
 * (tasks#108). The server refuses a token from any provider not in that list,
 * and still checks the verified email and the staff directory on every call.
 */
import { initializeApp } from 'firebase/app';
import { clearLocalAudio } from './webRecorderStore.js';
import {
  GoogleAuthProvider,
  browserLocalPersistence,
  browserPopupRedirectResolver,
  connectAuthEmulator,
  indexedDBLocalPersistence,
  initializeAuth,
  linkWithCredential,
  onIdTokenChanged,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as fbSignOut,
} from 'firebase/auth';

let auth = null;
let config = null;
let pendingGoogle = null; // a Google credential to connect after a password sign-in (same address)

/** null = laptop demo (no sign-in; the review API runs with --auth off). */
export async function loadAuthConfig() {
  try {
    const res = await fetch('/api/auth-config', { cache: 'no-store' });
    if (!res.ok) return null;
    config = await res.json();
  } catch {
    return null;
  }
  const app = initializeApp({
    apiKey: config.apiKey,
    authDomain: config.authDomain,
    projectId: config.projectId,
  });
  // The popup resolver (and its frame) is loaded only when Sign in with Google is on.
  auth = initializeAuth(app, {
    persistence: [indexedDBLocalPersistence, browserLocalPersistence],
    ...(googleEnabled() ? { popupRedirectResolver: browserPopupRedirectResolver } : {}),
  });
  if (config.emulator) connectAuthEmulator(auth, config.emulator, { disableWarnings: true });
  return config;
}

export const isHosted = () => auth !== null;

/** What the Record page needs from /api/auth-config. The sample lesson is on only when the server says demoRecorder is true. */
export function recorderSettings() {
  if (!config) return { uploadApi: '', demo: false, hosted: false };
  return {
    uploadApi: config.uploadApi || '',
    demo: config.demoRecorder === true,
    hosted: true,
  };
}
export const googleEnabled = () => !!(config && (config.providers || []).includes('google.com'));
export const currentUser = () => (auth ? auth.currentUser : null);
export const onUserChanged = (cb) => onIdTokenChanged(auth, cb);

export async function signIn(email, password) {
  const cred = await signInWithEmailAndPassword(auth, email.trim(), password);
  if (pendingGoogle) {
    // this address first tried Google: connect it now, so next time the Google button works
    const g = pendingGoogle;
    pendingGoogle = null;
    try {
      await linkWithCredential(cred.user, g);
    } catch {
      /* already connected, or a different Google account: the password sign-in still stands */
    }
  }
  return cred;
}

/** Sign in with Google (popup). Access is still decided by the server: verified email, exact
 *  match with a staff record, and access not removed. */
export async function signInWithGoogle() {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: 'select_account' });
  try {
    return await signInWithPopup(auth, provider);
  } catch (e) {
    if (e && e.code === 'auth/account-exists-with-different-credential') {
      pendingGoogle = GoogleAuthProvider.credentialFromError(e);
    }
    throw e;
  }
}

export async function resendVerification() {
  if (auth.currentUser) {
    await sendEmailVerification(auth.currentUser, { url: `${window.location.origin}/` });
  }
}

export async function resetPassword(email) {
  return sendPasswordResetEmail(auth, email.trim(), { url: `${window.location.origin}/` });
}

/** Re-read the account (after clicking the email link) and get a fresh token. */
export async function refreshUser() {
  if (!auth.currentUser) return null;
  await auth.currentUser.reload();
  await auth.currentUser.getIdToken(true);
  return auth.currentUser;
}

let recorderSignOut = null;

/** The Record page registers this so sign-out can stop the mic and delete leftover audio first. */
export function onRecorderSignOut(fn) {
  recorderSignOut = fn;
  return () => { if (recorderSignOut === fn) recorderSignOut = null; };
}

export async function signOut() {
  if (recorderSignOut) {
    try { await recorderSignOut(); } catch { /* still sign out and still delete local audio */ }
  }
  await clearLocalAudio();
  if (auth) await fbSignOut(auth);
}

/** fetch() with the signed-in user's ID token (hosted); plain fetch on the laptop demo. */
export async function authFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (auth && auth.currentUser) {
    headers.Authorization = `Bearer ${await auth.currentUser.getIdToken()}`;
  }
  return fetch(url, { ...opts, headers });
}

export async function whoAmI() {
  const res = await authFetch('/api/me', { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function authErrorText(e) {
  const code = (e && e.code) || '';
  return (
    {
      'auth/invalid-credential': 'That email and password don’t match an account.',
      'auth/invalid-login-credentials': 'That email and password don’t match an account.',
      'auth/wrong-password': 'That email and password don’t match an account.',
      'auth/user-not-found': 'That email and password don’t match an account.',
      'auth/email-already-in-use': 'There is already an account for this email. Sign in instead.',
      'auth/invalid-email': 'Enter a valid email address.',
      'auth/weak-password': 'Use a longer password (at least 10 characters).',
      'auth/password-does-not-meet-requirements': 'Use a longer password (at least 10 characters).',
      'auth/too-many-requests': 'Too many attempts. Wait a few minutes and try again.',
      'auth/network-request-failed': 'Network problem. Check your connection and try again.',
      'auth/admin-restricted-operation': 'That Google account isn’t set up for this site. Ask your school admin.',
      'auth/account-exists-with-different-credential':
        'This email already signs in with a password. Sign in with your password below once; Google will then be connected to it.',
      'auth/popup-blocked': 'Your browser blocked the Google window. Allow pop-ups for this site and try again.',
      'auth/popup-closed-by-user': 'The Google window was closed before signing in.',
      'auth/cancelled-popup-request': 'The Google window was closed before signing in.',
      'auth/operation-not-allowed': 'Sign in with Google isn’t switched on for this site yet.',
      'auth/unauthorized-domain': 'Sign in with Google isn’t allowed from this address. Use the school’s review site link.',
    }[code] || 'Something went wrong. Try again.'
  );
}
