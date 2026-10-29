import React, { useEffect, useState } from 'react';
import { loadMaterialsEmail, loadParentEmailSetting, loadSettings, saveMaterialsEmail, saveParentEmailSetting } from '../dataStore.js';
import { apiErrorText } from '../labels.js';
import SchoolSettings from './SchoolSettings.jsx';
import MaterialsInstruction from './MaterialsInstruction.jsx';

// Settings tab (David, Oct 8 2026; tasks#85), admins only, last tab. Shows the configuration the
// app runs with. The school name and logo (top bar) can be changed here by admins (Oct 9); every
// other value is read-only and says where it is set. Reset demo state also restores the default
// school name and badge.
const RO = (where) => <span className="settings-ro">Read-only · {where}</span>;

function Row({ label, value, where }) {
  return (
    <tr data-testid="setting-row">
      <th scope="row">{label}</th>
      <td>{value ?? <span className="muted">not set</span>}</td>
      <td>{RO(where)}</td>
    </tr>
  );
}

const MAIL_STATE = {
  ready: 'ready', 'not set up': 'not set up yet (needs the one-time approval)', 'needs re-approval': 'needs to be approved again',
  'not checked': 'not checked yet', outbox: 'test mode (nothing leaves this computer)', off: 'off',
};

/** Admin: email classroom materials to teachers (tasks#99). Off = download only. Every change is logged. */
export function MaterialsEmailSetting({ initial = null, reviewerId = null, load = loadMaterialsEmail, save = saveMaterialsEmail }) {
  const [m, setM] = useState(initial);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!initial) load(reviewerId).then(setM).catch(() => setM(null)); }, [reviewerId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!m) return null;
  async function toggle() {
    setBusy(true); setErr('');
    try { setM(await save(!m.enabled, reviewerId)); } catch (e) { setErr(apiErrorText(e.message)); } finally { setBusy(false); }
  }
  return (
    <table className="settings-table materials-email-setting" data-testid="materials-email-setting"><tbody>
      <tr>
        <th scope="row">Email classroom materials to teachers</th>
        <td>
          {m.enabled ? 'On' : 'Off'} · sent from {m.sender} to the teacher’s own staff address only
          <div className="hint">Email service: {MAIL_STATE[m.mail_state] || m.mail_state}</div>
          {err && <div className="pl-error" role="alert">{err}</div>}
        </td>
        <td>
          <button type="button" className="btn ghost small" onClick={toggle} disabled={busy} data-testid="materials-email-toggle">
            {m.enabled ? 'Turn off' : 'Turn on'}
          </button>
          <span className="settings-ro"> Admins · every change is logged</span>
        </td>
      </tr>
    </tbody></table>
  );
}

/** Admin: demo redirect for the weekly parent email (tasks#106). On (default): every parent email goes to
 * the teacher's own staff.example inbox with a "would have gone to ..." banner. Every change is logged. */
export function ParentEmailSetting({ initial = null, reviewerId = null, load = loadParentEmailSetting, save = saveParentEmailSetting }) {
  const [m, setM] = useState(initial);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!initial) load(reviewerId).then(setM).catch(() => setM(null)); }, [reviewerId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!m) return null;
  async function toggle() {
    setBusy(true); setErr('');
    try { setM(await save(!m.redirect, reviewerId)); } catch (e) { setErr(apiErrorText(e.message)); } finally { setBusy(false); }
  }
  return (
    <table className="settings-table parent-email-setting" data-testid="parent-email-setting"><tbody>
      <tr>
        <th scope="row">Weekly parent email: demo redirect</th>
        <td>
          {m.redirect ? `On · every parent email goes to the teacher’s own ${m.redirect_domain} address instead, with a “would have gone to …” banner`
            : 'Off · parent emails go to guardians with “receives reports” on (still limited to the allowed email domains)'}
          <div className="hint">Sent from {m.sender} only when a teacher clicks Send. Email service: {MAIL_STATE[m.mail_state] || m.mail_state}</div>
          {err && <div className="pl-error" role="alert">{err}</div>}
        </td>
        <td>
          <button type="button" className="btn ghost small" onClick={toggle} disabled={busy} data-testid="parent-email-redirect-toggle">
            {m.redirect ? 'Turn off' : 'Turn on'}
          </button>
          <span className="settings-ro"> Admins · every change is logged</span>
        </td>
      </tr>
    </tbody></table>
  );
}

export function SettingsView({ s, onReset = null, school = null, materials = null }) {
  const roles = s.access?.allowlist_roles || {};
  return (
    <section className="settings" data-testid="settings">
      {school}
      <h3>Access</h3>
      <table className="settings-table"><tbody>
        <Row label="IEP access for RBTs" value={s.iep_access_rbt === 'none' ? 'None (no IEP goals)' : 'Read'} where="class list (roster) setting" />
        <Row label="Teachers’ student access" value="All students and IEPs; lists open on their own classrooms" where="decided Oct 2" />
        <Row label="Sign-in list" value={Object.entries(roles).map(([r, n]) => `${n} ${r}`).join(', ') || '—'} where="sign-in allowlist file" />
      </tbody></table>

      <h3>Retention</h3>
      <table className="settings-table"><tbody>
        <Row label="Audio" value={`${s.retention.audio_days} days`} where="set at deploy" />
        <Row label="Transcripts and drafts" value={`${s.retention.transcripts_and_drafts_days} days`} where="set at deploy" />
        <Row label="Logs" value={`${s.retention.logs_days} days`} where="set at deploy" />
        <Row label="Planner notes and summaries" value={s.retention.planner} where="set at deploy" />
      </tbody></table>

      <h3>Classrooms and roster</h3>
      <table className="settings-table"><tbody>
        <Row label="Class list" value={`${s.roster.classrooms} classrooms · ${s.roster.students} students · ${s.roster.teachers} teachers · ${s.roster.rbts} RBTs${s.roster.staff ? ` · ${s.roster.staff} other staff` : ''}${s.roster.synthetic ? ' · fictional sample data' : ''}`} where="class list (roster) file" />
      </tbody></table>
      <table className="pipeline-table" data-testid="settings-classrooms">
        <thead><tr><th>Classroom</th><th>Teacher</th><th>Students</th><th>Recorder codes</th></tr></thead>
        <tbody>
          {s.classrooms.map((c) => (
            <tr key={c.class_code}><td>{c.class_code}</td><td>{c.teacher_name || '—'}</td>
              <td>{c.students ?? (c.has_student ? 1 : 0)}</td>
              <td className="muted">{(c.aliases || []).join(', ') || '—'}</td></tr>
          ))}
        </tbody>
      </table>

      <h3>Teachers’ default classrooms</h3>
      <p className="hint">Every classroom list opens on the signed-in teacher’s default classroom; admins open on All.</p>
      <table className="pipeline-table" data-testid="settings-defaults">
        <thead><tr><th>Teacher</th><th>Default classroom</th><th /></tr></thead>
        <tbody>
          {(s.teacher_defaults || []).map((d) => (
            <tr key={d.teacher_name}><td>{d.teacher_name}</td><td>{d.default_classroom || '—'}</td>
              <td>{RO(d.set_in_roster ? 'class list (roster)' : 'first own classroom with a student')}</td></tr>
          ))}
        </tbody>
      </table>

      <h3>AI and records</h3>
      <table className="settings-table"><tbody>
        <Row label="AI drafts" value={s.ai.planner_ai} where="set at deploy" />
        <Row label="AI model" value={s.ai.model ? `${s.ai.model} (${s.ai.location})` : null} where="set at deploy" />
        <Row label="Activity journal" value={s.journal.dataset ? `${s.journal.mode} · ${s.journal.dataset} (${s.journal.location})` : s.journal.mode} where="set at deploy" />
        <Row label="Bug reports go to" value={s.bug_reports.repo} where="set at deploy" />
        <Row label="School time zone" value={s.school_timezone} where="set at deploy" />
      </tbody></table>
      {materials}

      <h3>Demo</h3>
      <table className="settings-table"><tbody>
        <Row label="Running version" value={s.build && s.build.commit ? `commit ${s.build.commit} · ${s.build.revision || 'local'}` : null} where="set at deploy (from main)" />
        <tr>
          <th scope="row">Reset demo state</th>
          <td>Clears this browser’s sample edits and approvals and puts back the default school name and badge. Real recordings are not touched.</td>
          <td>{onReset ? <button type="button" className="btn ghost small" onClick={onReset} data-testid="reset-demo">Reset demo state</button> : null}</td>
        </tr>
      </tbody></table>
    </section>
  );
}

export default function Settings({ reviewerId = null, onReset = null, branding = null, logoUrl = null,
  canEdit = false, onBrandingChange = () => {} }) {
  const [s, setS] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { loadSettings(reviewerId).then(setS).catch(setError); }, [reviewerId]);
  if (error) return <p className="pl-error" role="alert">Couldn’t load settings ({error.message}).</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const school = branding ? (
    <SchoolSettings branding={branding} logoUrl={logoUrl} canEdit={canEdit} onChange={onBrandingChange}
      reviewerId={reviewerId} />
  ) : null;
  return <SettingsView s={s} onReset={onReset} school={school}
    materials={canEdit ? (
      <>
        <MaterialsEmailSetting reviewerId={reviewerId} />
        <ParentEmailSetting reviewerId={reviewerId} />
        <MaterialsInstruction reviewerId={reviewerId} />
      </>
    ) : null} />;
}
