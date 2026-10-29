import React, { useCallback, useEffect, useState } from 'react';
import { changeAccess, loadTeachers } from '../dataStore.js';
import { profileError } from '../schoolSetup.js';

const ROLE_NAMES = { admin: 'Admin', teacher: 'Teacher', rbt: 'RBT (read-only)', none: 'No access' };

/**
 * Admins (David, Oct 10 2026; tasks#107): change a person's app role and sign-in email. The email is
 * the sign-in key: the server moves the sign-in list entry with it in one change, and journals which
 * fields changed (never the addresses).
 */
export function AccessEditor({ row, onSave, onCancel, saving = false, error = null }) {
  const [email, setEmail] = useState(row.sign_in_email || '');
  const [role, setRole] = useState(row.app_role || 'none');
  function submit(e) {
    e.preventDefault();
    const changes = {};
    if (email.trim().toLowerCase() !== (row.sign_in_email || '').toLowerCase()) changes.email = email.trim();
    if (role !== (row.app_role || 'none')) changes.role = role;
    onSave(changes);
  }
  return (
    <form className="access-edit" onSubmit={submit} data-testid="access-editor">
      <input type="email" value={email} placeholder="sign-in email" aria-label="Sign-in email"
        onChange={(e) => setEmail(e.target.value)} data-testid="access-email" />
      <select value={role} aria-label="App role" onChange={(e) => setRole(e.target.value)} data-testid="access-role">
        {Object.entries(ROLE_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <button type="submit" className="btn primary small" disabled={saving} data-testid="access-save">Save</button>
      <button type="button" className="btn ghost small" onClick={onCancel}>Cancel</button>
      {error ? <span className="pl-error" role="alert" data-testid="access-error">{error}</span> : null}
    </form>
  );
}

// Teachers tab (David, Oct 8 2026; tasks#85): every staff member from the class list (teachers, RBTs,
// BCBA, therapists, office), with their classrooms and the role from their own staff record; RBTs show
// their supervising BCBA (tasks#104). Emails, sign-in and app role: admins only; admins change role /
// sign-in email here (tasks#107). Everyone edits their own profile from their name (My profile).
export function TeacherTable({ data, editing = null, onEdit = null, editor = null }) {
  const rows = data?.teachers || [];
  const emails = !!data?.emails_shown;
  const canEdit = !!data?.can_edit_access && !!onEdit;
  return (
    <table className="pipeline-table" data-testid="teacher-table">
      <thead><tr><th>Name</th>{emails ? <th>Email</th> : null}<th>Classroom(s)</th><th>Default classroom</th><th>Role</th>
        {emails ? <th>Sign-in (app role)</th> : null}</tr></thead>
      <tbody>
        {rows.map((t, i) => {
          const key = t.id || `${t.name}-${i}`;
          return (
            <tr key={key} data-testid="teacher-row">
              <td>{t.name}</td>
              {emails ? <td>{(t.emails || []).join(', ') || <span className="muted">—</span>}</td> : null}
              <td>{t.classrooms.join(', ') || <span className="muted">—</span>}</td>
              <td data-testid="teacher-default">{t.default_classroom || <span className="muted">—</span>}</td>
              <td data-testid="teacher-role">{t.role}{t.supervisor ? <span className="muted"> · supervised by {t.supervisor}</span> : null}
                {t.allowlist_only ? <span className="muted"> · sign-in list only</span> : null}</td>
              {emails ? (
                <td data-testid="teacher-access">
                  {editing === key && editor ? editor(t) : (
                    <>
                      <span data-testid="teacher-app-role">{ROLE_NAMES[t.app_role] || 'No access'}</span>
                      {t.app_role && t.app_role !== 'none' && t.sign_in_email ? <span className="muted"> · {t.sign_in_email}</span> : null}
                      {canEdit ? <> <button type="button" className="link" data-testid="access-change" onClick={() => onEdit(key)}>Change</button></> : null}
                    </>
                  )}
                </td>
              ) : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function Teachers({ reviewerId = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [save, setSave] = useState({ saving: false, error: null });
  const reload = useCallback(() => loadTeachers(reviewerId).then(setData).catch(setError), [reviewerId]);
  useEffect(() => { reload(); }, [reload]);
  if (error) return <p className="pl-error" role="alert">Couldn’t load teachers ({error.message}).</p>;
  if (!data) return <p className="muted">Loading…</p>;

  async function onSave(row, changes) {
    if (!changes.email && !changes.role) { setEditing(null); return; }
    setSave({ saving: true, error: null });
    try {
      await changeAccess({ person: row.id, currentEmail: row.sign_in_email, ...changes });
      setSave({ saving: false, error: null });
      setEditing(null);
      reload();
    } catch (err) {
      setSave({ saving: false, error: profileError(err) });
    }
  }

  return (
    <section data-testid="teachers">
      <TeacherTable data={data} editing={editing} onEdit={(k) => { setSave({ saving: false, error: null }); setEditing(k); }}
        editor={(row) => <AccessEditor row={row} onSave={(c) => onSave(row, c)} onCancel={() => setEditing(null)} {...save} />} />
      {(data.teachers || []).some((t) => t.label) ? (
        <p className="hint" data-testid="teachers-fictional-note">
          <span className="badge sample-badge fictional-label">{data.teachers.find((t) => t.label).label}</span>{' '}
          Every staff member here is invented for the demo.
        </p>
      ) : null}
      <p className="hint">
        {data.can_edit_access
          ? 'Admins change a person’s app role and sign-in email here (the sign-in list follows the email). '
          : 'Emails are shown to admins. '}
        Everyone edits their own name, phone, title and default classroom from their name at the top right (My profile).
        Every classroom list opens on the person’s default classroom.
      </p>
    </section>
  );
}
