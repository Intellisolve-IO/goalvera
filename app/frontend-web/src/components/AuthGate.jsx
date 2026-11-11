import React, { useCallback, useEffect, useState } from 'react';
import TextBox from './TextBox.jsx';
import { DEFAULT_SCHOOL_NAME } from '../branding.js';
import {
  authErrorText,
  currentUser,
  googleEnabled,
  loadAuthConfig,
  onUserChanged,
  refreshUser,
  resendVerification,
  resetPassword,
  signIn,
  signInWithGoogle,
  signOut,
  whoAmI,
} from '../auth.js';

/**
 * Hosted: sign in -> verified email -> on the allowlist -> the review screen.
 * The server enforces every step; this only decides which page to show.
 * Laptop demo (no /api/auth-config): renders the review screen directly.
 */
export default function AuthGate({ children }) {
  const [phase, setPhase] = useState('loading'); // loading|laptop|signed-out|unverified|waiting|approved|error
  const [me, setMe] = useState(null);

  const check = useCallback(async () => {
    const u = currentUser();
    if (!u) return setPhase('signed-out');
    if (!u.emailVerified) return setPhase('unverified');
    try {
      const m = await whoAmI();
      setMe(m);
      setPhase(m.approved ? 'approved' : m.email_verified ? 'waiting' : 'unverified');
    } catch {
      setPhase('error');
    }
  }, []);

  useEffect(() => {
    let unsub = () => {};
    loadAuthConfig().then((cfg) => {
      if (!cfg) return setPhase('laptop');
      unsub = onUserChanged(() => check());
    });
    return () => unsub();
  }, [check]);

  if (phase === 'laptop') return children({ user: null, onSignOut: null });
  if (phase === 'loading') return <Centered><p className="muted">Loading…</p></Centered>;
  if (phase === 'signed-out') return <SignInPage />;
  if (phase === 'unverified') return <VerifyPage onChecked={check} />;
  if (phase === 'waiting') return <WaitingPage email={me && me.email} onCheck={check} />;
  if (phase === 'error')
    return (
      <Centered>
        <h1>Can’t reach the review service</h1>
        <p className="muted">Try again in a moment.</p>
        <button type="button" className="btn primary" onClick={check}>Try again</button>
      </Centered>
    );
  return children({ user: me, onSignOut: () => signOut() });
}

function Centered({ children }) {
  return (
    <div className="auth-page">
      <div className="auth-card">{children}</div>
    </div>
  );
}

/** Accounts are created by an admin (self sign-up is off), so there is no sign-up form.
 *  google: show "Sign in with Google" (the server's auth-config lists "google.com"). */
export function SignInPage({ google = googleEnabled() }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      await signIn(email, password);
    } catch (err) {
      setMsg({ err: true, text: authErrorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function withGoogle() {
    setMsg(null);
    setBusy(true);
    try {
      await signInWithGoogle();
    } catch (err) {
      setMsg({ err: true, text: authErrorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function forgot() {
    if (!email) return setMsg({ err: true, text: 'Enter your email first.' });
    try {
      await resetPassword(email);
    } catch {
      /* same answer either way: don't reveal whether an account exists */
    }
    setMsg({ err: false, text: 'If there is an account for that email, a reset link is on its way.' });
  }

  return (
    <Centered>
      <p className="eyebrow">{DEFAULT_SCHOOL_NAME} · Teacher review</p>
      <h1>Sign in</h1>
      {google && (
        <div className="auth-google">
          <button type="button" className="btn google" onClick={withGoogle} disabled={busy}
            data-testid="google-signin">
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 48 48">
              <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.6 13.3l7.9 6.1C12.4 13.7 17.7 9.5 24 9.5z" />
              <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.1 5.3-4.6 7l7.1 5.5c4.2-3.9 7.2-9.6 7.2-17z" />
              <path fill="#FBBC05" d="M10.5 28.6A14.6 14.6 0 0 1 9.5 24c0-1.6.3-3.2.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z" />
              <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.1-5.5c-2.2 1.5-5 2.3-8.8 2.3-6.3 0-11.6-4.2-13.5-10l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
            </svg>
            Sign in with Google
          </button>
          <p className="muted small">Use your school Google account. It must match your email on the staff list.</p>
          <p className="auth-or"><span>or</span></p>
        </div>
      )}
      <form onSubmit={submit} className="auth-form" data-testid="signin-form">
        <label>
          Email
          <TextBox as="input" type="email" autoComplete="email" required value={email} onValue={setEmail} />
        </label>
        <label>
          Password
          <TextBox as="input" type="password" autoComplete="current-password" required value={password}
            onValue={setPassword} />
        </label>
        {msg && <p className={msg.err ? 'auth-error' : 'auth-ok'} role="alert">{msg.text}</p>}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? 'Please wait…' : 'Sign in'}
        </button>
      </form>
      <p className="auth-switch">
        <button type="button" className="link" onClick={forgot}>Forgot password</button>
        {' · '}Need an account? Ask your school admin.
      </p>
    </Centered>
  );
}

function VerifyPage({ onChecked }) {
  const u = currentUser();
  const [msg, setMsg] = useState(null);
  async function done() {
    const x = await refreshUser();
    if (x && x.emailVerified) onChecked();
    else setMsg('Not verified yet. Open the link in the email, then try again.');
  }
  async function resend() {
    try {
      await resendVerification();
      setMsg('Sent again. Check your inbox (and spam folder).');
    } catch (e) {
      setMsg(authErrorText(e));
    }
  }
  return (
    <Centered>
      <h1>Verify your email</h1>
      <p>
        We sent a link to <strong>{u && u.email}</strong>. Open it to confirm this is your address,
        then come back here.
      </p>
      {msg && <p className="auth-ok" role="status">{msg}</p>}
      <div className="auth-actions">
        <button type="button" className="btn primary" onClick={done}>I’ve verified my email</button>
        <button type="button" className="btn ghost" onClick={resend}>Send the link again</button>
        <button type="button" className="btn ghost" onClick={() => signOut()}>Sign out</button>
      </div>
    </Centered>
  );
}

function WaitingPage({ email, onCheck }) {
  return (
    <Centered>
      <h1 data-testid="waiting-title">Waiting for access</h1>
      <p>
        You’re signed in as <strong>{email}</strong> and your email is verified. An administrator
        still has to add you to the approved list before you can see any sessions.
      </p>
      <div className="auth-actions">
        <button type="button" className="btn primary" onClick={onCheck}>Check again</button>
        <button type="button" className="btn ghost" onClick={() => signOut()}>Sign out</button>
      </div>
    </Centered>
  );
}
