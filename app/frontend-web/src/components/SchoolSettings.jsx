import React, { useEffect, useState } from 'react';
import {
  brandingErrorText, logoProblem, nameProblem, NAME_MAX, removeLogo, saveSchoolName, uploadLogo,
} from '../branding.js';
import SchoolBrand from './SchoolBrand.jsx';

/** Settings › School: name and logo shown in the top bar. Admins edit; everyone else reads. */
export default function SchoolSettings({ branding, logoUrl = null, canEdit = false, onChange = () => {}, reviewerId = null }) {
  const [name, setName] = useState(branding.school_name);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  useEffect(() => { setName(branding.school_name); }, [branding.school_name]);   // e.g. after Reset demo state

  async function run(fn, done) {
    setBusy(true); setErr(''); setMsg('');
    try {
      const b = await fn();
      onChange(b);
      setName(b.school_name);
      setMsg(done);
    } catch (e) {
      setErr(brandingErrorText(e));
    } finally {
      setBusy(false);
    }
  }

  function onSaveName(e) {
    e.preventDefault();
    const p = nameProblem(name);
    if (p) { setErr(p); return; }
    run(() => saveSchoolName(name.trim(), reviewerId), 'School name saved.');
  }

  function onFile(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    const p = logoProblem(file);
    if (p) { setErr(p); return; }
    run(() => uploadLogo(file, reviewerId), 'Logo saved.');
  }

  return (
    <div className="school-settings" data-testid="school-settings">
      <h3>School</h3>
      <table className="settings-table"><tbody>
        <tr data-testid="setting-row">
          <th scope="row">School name</th>
          <td>
            {canEdit ? (
              <form onSubmit={onSaveName} className="school-name-form">
                <input type="text" value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)}
                  aria-label="School name" data-testid="school-name-input" disabled={busy} />
                <button type="submit" className="btn small" disabled={busy || name.trim() === branding.school_name}
                  data-testid="school-name-save">Save</button>
              </form>
            ) : branding.school_name}
          </td>
          <td><span className="settings-ro">{canEdit ? 'Admins can change · shown in the top bar' : 'Read-only · admins change this'}</span></td>
        </tr>
        <tr data-testid="setting-row">
          <th scope="row">School logo</th>
          <td>
            <div className="school-logo-row">
              <SchoolBrand branding={branding} logoUrl={logoUrl} />
              {canEdit ? (
                <>
                  <label className="btn ghost small file-btn">
                    {branding.logo ? 'Replace logo' : 'Upload logo'}
                    <input type="file" accept="image/png,image/jpeg,image/svg+xml" onChange={onFile}
                      disabled={busy} data-testid="school-logo-input" />
                  </label>
                  {branding.logo ? (
                    <button type="button" className="btn ghost small" disabled={busy} data-testid="school-logo-remove"
                      onClick={() => run(() => removeLogo(reviewerId), 'Logo removed; the badge is shown.')}>Remove logo</button>
                  ) : null}
                </>
              ) : null}
            </div>
            <p className="hint">PNG, JPEG or SVG, up to 1 MB. SVGs are turned into a PNG when uploaded. Without a logo the badge ({branding.badge_text}) is shown.</p>
          </td>
          <td><span className="settings-ro">{canEdit ? 'Admins can change · shown in the top bar' : 'Read-only · admins change this'}</span></td>
        </tr>
      </tbody></table>
      {msg ? <p className="hint" role="status" data-testid="school-msg">{msg}</p> : null}
      {err ? <p className="pl-error" role="alert" data-testid="school-error">{err}</p> : null}
    </div>
  );
}
