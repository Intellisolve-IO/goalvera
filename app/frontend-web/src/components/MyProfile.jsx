import React, { useEffect, useState } from 'react';
import { loadMyProfile, saveMyProfile } from '../dataStore.js';
import { profileError } from '../schoolSetup.js';

const FIELDS = ['display_name', 'preferred_name', 'phone', 'default_classroom', 'title', 'credential_type'];

/**
 * My profile: opened from the signed-in name at the top right.
 * The current user (teacher, RBT or admin) edits their own display name, preferred name, phone,
 * default classroom (one of theirs), title and credential type. Role and sign-in email are shown
 * read-only ("Ask an admin to change this") unless the server allows self-edit (can_edit_access).
 * Every save is journaled on the server (profile_updated: which fields, never the values).
 */
export function ProfileForm({ data, onSave, saving = false, message = null, error = null }) {
  const [f, setF] = useState(() => Object.fromEntries(FIELDS.map((k) => [k, data[k] || ''])));
  const [access, setAccess] = useState({ email: data.email || '', role: data.role === 'reviewer' ? 'teacher' : data.role });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  function submit(e) {
    e.preventDefault();
    const out = { ...f };
    if (data.can_edit_access) {
      if (access.email.trim().toLowerCase() !== (data.email || '').toLowerCase()) out.email = access.email.trim();
      if (access.role !== (data.role === 'reviewer' ? 'teacher' : data.role)) out.role = access.role;
    }
    onSave(out);
  }
  return (
    <form className="profile-form" onSubmit={submit} data-testid="profile-form">
      <div className="profile-grid">
        <label>Display name
          <input value={f.display_name} maxLength={80} onChange={set('display_name')} data-testid="profile-display-name"
            placeholder={data.roster_name || ''} />
        </label>
        <label>Preferred name
          <input value={f.preferred_name} maxLength={40} onChange={set('preferred_name')} data-testid="profile-preferred-name" />
        </label>
        <label>Phone
          <input type="tel" value={f.phone} maxLength={20} onChange={set('phone')} data-testid="profile-phone"
            placeholder="(404) 555-0100" />
        </label>
        <label>Default classroom
          <select value={f.default_classroom} onChange={set('default_classroom')} data-testid="profile-default-classroom">
            <option value="">{data.role === 'admin' ? 'All classrooms' : 'Automatic'}</option>
            {(data.classrooms || []).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label>Title
          <input value={f.title} maxLength={80} onChange={set('title')} data-testid="profile-title" />
        </label>
        <label>Credential type
          <select value={f.credential_type} onChange={set('credential_type')} data-testid="profile-credential">
            {(data.credential_types || ['']).map((c) => <option key={c} value={c}>{c || '—'}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="profile-access" data-testid="profile-access">
        <legend>Sign-in</legend>
        {data.can_edit_access ? (
          <div className="profile-grid">
            <label>Email
              <input type="email" value={access.email} onChange={(e) => setAccess({ ...access, email: e.target.value })}
                data-testid="profile-email-input" />
            </label>
            <label>Role
              <select value={access.role} onChange={(e) => setAccess({ ...access, role: e.target.value })} data-testid="profile-role-input">
                <option value="admin">Admin</option><option value="teacher">Teacher</option><option value="rbt">RBT (read-only)</option>
              </select>
            </label>
          </div>
        ) : (
          <dl className="profile-readonly">
            <dt>Email</dt><dd data-testid="profile-email">{data.email}</dd>
            <dt>Role</dt><dd data-testid="profile-role">{data.role_label}</dd>
            <dd className="hint" data-testid="profile-ask-admin">Ask an admin to change this.</dd>
          </dl>
        )}
      </fieldset>
      {error ? <p className="pl-error" role="alert" data-testid="profile-error">{error}</p> : null}
      {message ? <p className="hint" role="status" data-testid="profile-saved">{message}</p> : null}
      <div className="profile-actions">
        <button type="submit" className="btn primary" disabled={saving} data-testid="profile-save">{saving ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  );
}

export default function MyProfile({ onClose, onSaved = null, load = loadMyProfile, save = saveMyProfile }) {
  const [data, setData] = useState(null);
  const [state, setState] = useState({ saving: false, message: null, error: null });
  const [loadError, setLoadError] = useState(null);
  const [key, setKey] = useState(0);
  useEffect(() => { load().then(setData).catch(setLoadError); }, [load]);

  async function onSave(fields) {
    setState({ saving: true, message: null, error: null });
    try {
      const next = await save(fields);
      setData(next);
      setKey((k) => k + 1);
      setState({ saving: false, error: null,
        message: (next.changed || []).length ? 'Saved. The change is in the activity journal.' : 'Nothing changed.' });
      if (onSaved) onSaved(next);
    } catch (err) {
      setState({ saving: false, message: null, error: profileError(err) });
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal profile-panel" role="dialog" aria-modal="true" aria-labelledby="profile-title"
        data-testid="my-profile" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">Your account</p>
            <h2 id="profile-title">My profile</h2>
            {data && data.roster_name ? <p className="muted">Staff record: {data.roster_name}
              {data.sample_label ? <span className="badge sample-badge fictional-label"> {data.sample_label}</span> : null}</p> : null}
          </div>
          <button type="button" className="btn ghost" onClick={onClose} data-testid="profile-close">Close</button>
        </header>
        <div className="modal-body">
          {loadError ? <p className="pl-error" role="alert">Couldn’t load your profile ({loadError.message}).</p>
            : !data ? <p className="muted">Loading…</p>
              : <ProfileForm key={key} data={data} onSave={onSave} {...state} />}
        </div>
      </div>
    </div>
  );
}
