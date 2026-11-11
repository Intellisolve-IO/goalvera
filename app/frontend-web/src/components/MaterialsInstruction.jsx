import React, { useEffect, useState } from 'react';
import { loadMaterialsInstruction, saveMaterialsInstruction } from '../dataStore.js';
import { apiErrorText } from '../labels.js';

const RENDERER_HINT = {
  svg: (m) => `The AI (${m.model}) draws each draft page as SVG following this instruction.`,
  template: () => 'The AI only picks the parameters (which numbers, which words) following this instruction; '
    + 'the app computes every number and the layout itself.',
  photo: (m) => `The picture model (${m.image_model}) makes one picture per card from this style; `
    + '{subject} is replaced by what the card shows.',
};

/** The instruction the AI follows for each activity type. Admins edit them (Settings) and
 * can reset each to its default; teachers see them read-only. The server decides who may edit
 * (can_edit) and logs every change (who / when / which type). `only` shows a single type. */
export default function MaterialsInstruction({ initial = null, reviewerId = null, only = null,
  load = loadMaterialsInstruction, save = saveMaterialsInstruction }) {
  const [m, setM] = useState(initial);
  const [key, setKey] = useState(only || (initial && initial.instructions[0].key) || null);
  const [text, setText] = useState(() => {
    const list = (initial && initial.instructions) || [];
    return (list.find((x) => x.key === only) || list[0] || {}).instruction || '';
  });
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const item = m && (m.instructions.find((x) => x.key === key) || m.instructions[0]);
  useEffect(() => {
    if (!initial) load(reviewerId).then(setM).catch(() => setM(null));
  }, [reviewerId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (only) setKey(only); }, [only]);
  useEffect(() => { if (item) setText(item.instruction); }, [m, item && item.key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!m || !item) return null;
  async function run(body, done) {
    setBusy(true); setErr(''); setNote('');
    try {
      const x = await save({ key: item.key, ...body }, reviewerId);
      setM(x); setNote(done);
    } catch (e) { setErr(apiErrorText(e.message)); } finally { setBusy(false); }
  }
  const changed = text !== item.instruction;
  return (
    <div className="materials-instruction" data-testid="materials-instruction">
      <h4>AI instructions for classroom materials</h4>
      {!only && (
        <label>
          Activity type
          <select value={item.key} disabled={busy} data-testid="materials-instruction-key"
            onChange={(e) => { setKey(e.target.value); setNote(''); setErr(''); }}>
            {m.instructions.map((x) => (
              <option key={x.key} value={x.key}>{x.label}{x.is_default ? '' : ' (changed)'}</option>
            ))}
          </select>
        </label>
      )}
      <p className="hint">
        {(RENDERER_HINT[item.renderer] || RENDERER_HINT.svg)(m)} The school name in the header is always filled
        in by the app, never by the AI.
        {item.is_default ? ' Using the default.' : ' Changed from the default'}
        {!item.is_default && item.updated_at ? ` on ${new Date(item.updated_at).toLocaleString()}.` : item.is_default ? '' : '.'}
      </p>
      <textarea value={text} readOnly={!m.can_edit} disabled={busy} rows={item.renderer === 'svg' ? 16 : 6} maxLength={m.max}
        data-testid="materials-instruction-text" onChange={(e) => setText(e.target.value)} />
      {err && <div className="pl-error" role="alert">{err}</div>}
      {note && <div className="hint" role="status">{note}</div>}
      {m.can_edit && !only ? (
        <div className="card-row">
          <button type="button" className="btn primary small" disabled={busy || !changed}
            data-testid="materials-instruction-save" onClick={() => run({ instruction: text }, 'Saved. New materials of this type use it.')}>
            Save
          </button>
          <button type="button" className="btn ghost small" disabled={busy || (item.is_default && !changed)}
            data-testid="materials-instruction-reset" onClick={() => run({ reset: true }, 'Back to the default.')}>
            Reset to default
          </button>
          <span className="settings-ro"> Admins · every change is logged</span>
        </div>
      ) : <span className="settings-ro">Read-only · admins can change it in Settings</span>}
    </div>
  );
}
