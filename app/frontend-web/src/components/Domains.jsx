import React, { useCallback, useEffect, useState } from 'react';
import { loadDomains, saveDomain } from '../dataStore.js';
import { setupError } from '../schoolSetup.js';

// Domains tab (David, Oct 10 2026; tasks#103): the Educational Methodology module. Every Subject /
// Domain with its Core Educational Methodology, Key Curricular Programs and Recommended Book / Guide,
// from David's document. Admins add, edit and remove (journaled); everyone else reads. Classes pick
// their domain from this list, and materials use the class's domain + methodology.

const EMPTY = { name: '', methodology: '', methodology_detail: '', programs: '', book: '' };

export function DomainEditor({ initial, limits = {}, busy, onSave, onCancel, onRemove = null, error = null }) {
  const [d, setD] = useState({ ...EMPTY, ...(initial || {}) });
  const set = (k) => (e) => setD({ ...d, [k]: e.target.value });
  const field = (k, label, multi = false) => (
    <label className="ds-field">
      <span>{label}</span>
      {multi
        ? <textarea name={k} rows={3} value={d[k]} maxLength={limits[k] || 600} onChange={set(k)} />
        : <input name={k} type="text" value={d[k]} maxLength={limits[k] || 300} onChange={set(k)} />}
    </label>
  );
  return (
    <form className="ds-editor" data-testid="domain-editor"
      onSubmit={(e) => { e.preventDefault(); onSave({ name: d.name, methodology: d.methodology,
        methodology_detail: d.methodology_detail, programs: d.programs, book: d.book }); }}>
      {field('name', 'Subject / Domain')}
      {field('methodology', 'Core educational methodology (name)')}
      {field('methodology_detail', 'Methodology description', true)}
      {field('programs', 'Key curricular programs', true)}
      {field('book', 'Recommended book / guide', true)}
      <p className="hint">The domain name and methodology go to the AI with classroom materials, so no names of people.</p>
      {error ? <p className="pl-error" role="alert">{error}</p> : null}
      <div className="ds-actions">
        <button type="submit" className="btn primary small" disabled={busy || !d.name.trim()}>Save</button>
        <button type="button" className="btn small" onClick={onCancel} disabled={busy}>Cancel</button>
        {onRemove ? <button type="button" className="btn danger small" onClick={onRemove} disabled={busy}
          data-testid="remove-domain">Remove</button> : null}
      </div>
    </form>
  );
}

export function DomainTable({ data, editing = null, onEdit = null, children = null }) {
  const rows = data?.domains || [];
  const canEdit = !!(data?.can_edit && onEdit);
  return (
    <table className="pipeline-table domain-table" data-testid="domain-table">
      <thead>
        <tr>
          <th>Subject / Domain</th><th>Core Educational Methodology</th><th>Key Curricular Programs</th>
          <th>Recommended Book / Guide</th><th>Classes</th>{canEdit ? <th /> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => (editing === d.id ? (
          <tr key={d.id}><td colSpan={canEdit ? 6 : 5}>{children}</td></tr>
        ) : (
          <tr key={d.id} data-testid="domain-row">
            <td><strong>{d.name}</strong></td>
            <td>
              {d.methodology ? <strong className="ds-method">{d.methodology}</strong> : null}
              {d.methodology_detail ? <span className="ds-detail"> {d.methodology_detail}</span> : null}
              {!d.methodology && !d.methodology_detail ? <span className="muted">—</span> : null}
            </td>
            <td>{d.programs || <span className="muted">—</span>}</td>
            <td>{d.book || <span className="muted">—</span>}</td>
            <td data-testid="domain-classes">{d.classes || 0}</td>
            {canEdit ? (
              <td><button type="button" className="btn small" onClick={() => onEdit(d.id)}
                data-testid={`edit-domain-${d.id}`}>Edit</button></td>
            ) : null}
          </tr>
        )))}
      </tbody>
    </table>
  );
}

export default function Domains({ reviewerId = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);       // domain id, 'new' or null
  const [busy, setBusy] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const reload = useCallback(() => loadDomains(reviewerId).then(setData).catch(setError), [reviewerId]);
  useEffect(() => { reload(); }, [reload]);

  async function run(args) {
    setBusy(true);
    setSaveErr(null);
    try {
      setData(await saveDomain({ ...args, version: data.version }, reviewerId));
      setEditing(null);
    } catch (e) {
      setSaveErr(setupError(e));
      if (e.message === 'STALE_VERSION') reload();
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="pl-error" role="alert">Couldn’t load domains ({error.message}).</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const current = (data.domains || []).find((d) => d.id === editing);
  const editor = (
    <DomainEditor key={editing || 'none'} initial={current} limits={data.limits} busy={busy} error={saveErr}
      onCancel={() => { setEditing(null); setSaveErr(null); }}
      onSave={(domain) => run({ id: current ? current.id : null, domain })}
      onRemove={current ? () => {
        if (window.confirm(`Remove “${current.name}”?`)) run({ id: current.id, remove: true });
      } : null} />
  );
  return (
    <section data-testid="domains">
      {data.can_edit ? (
        <div className="pipeline-filters">
          <button type="button" className="btn primary small" onClick={() => { setEditing('new'); setSaveErr(null); }}
            disabled={editing === 'new'} data-testid="add-domain">Add domain</button>
        </div>
      ) : null}
      {editing === 'new' ? <div className="ds-new">{editor}</div> : null}
      <DomainTable data={data} editing={editing} onEdit={(id) => { setEditing(id); setSaveErr(null); }}>
        {editor}
      </DomainTable>
      {(data.delivery_formats || []).length ? (
        <div className="ds-formats" data-testid="delivery-formats">
          <h3>How the curriculum is delivered</h3>
          <ul>
            {data.delivery_formats.map((f) => <li key={f.name}><strong>{f.name}:</strong> {f.description}</li>)}
          </ul>
        </div>
      ) : null}
      <p className="hint">
        {data.can_edit ? 'Admins add, edit and remove domains; each change is in the Activity Log. ' : 'Read-only. Admins change domains. '}
        Each class picks its domain on the Classes tab, and classroom materials use the class’s domain and methodology.
        This is reference information, not student data.
      </p>
    </section>
  );
}
