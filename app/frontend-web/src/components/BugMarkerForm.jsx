import React, { useState } from 'react';
import TextBox from './TextBox.jsx';
import { BUG_AREAS, BUG_HINT, BUG_THANKS, MARKER_TYPES, parseClock } from '../bugMarkers.js';
import { apiErrorText } from '../labels.js';

/**
 * "Add a marker" on a session: marker type dropdown (Bug), time in the
 * recording, where on the screen, and a note. Teachers and admins only (the server
 * refuses RBTs). submit(payload) -> the server's answer.
 */
export default function BugMarkerForm({ submit, durationSeconds = null, onAdded = null, initialType = '' }) {
  const [type, setType] = useState(initialType);
  const [time, setTime] = useState('');
  const [area, setArea] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const seconds = parseClock(time);
  const badTime = Number.isNaN(seconds) || (seconds !== null && durationSeconds && seconds > durationSeconds + 1);
  const ready = type === 'Bug' && note.trim().length > 0 && !badTime && !busy;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await submit({ type, note, area: area || 'other', offset_seconds: seconds });
      setMsg({ ok: true, text: (res && res.message) || BUG_THANKS });
      setNote('');
      setTime('');
      setArea('');
      setType('');
      if (onAdded) onAdded(res);
    } catch (err) {
      setMsg({ ok: false, text: apiErrorText(err.message) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card marker-add" onSubmit={handleSubmit} data-testid="add-marker">
      <div className="card-row">
        <label>
          Marker type
          <select value={type} onChange={(e) => setType(e.target.value)} disabled={busy} data-testid="marker-type">
            <option value="">— choose a type —</option>
            {MARKER_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label>
          Time in recording (m:ss)
          <TextBox as="input" value={time} onValue={setTime} placeholder="e.g. 1:23" disabled={busy}
            inputMode="numeric" data-testid="marker-time" aria-invalid={badTime ? 'true' : 'false'} />
        </label>
        {type === 'Bug' && (
          <label>
            Where on this screen
            <select value={area} onChange={(e) => setArea(e.target.value)} disabled={busy} data-testid="bug-area">
              <option value="">— choose —</option>
              {BUG_AREAS.map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {badTime && <p className="hint warn-text">Type the time as m:ss, within the recording.</p>}
      {type === 'Bug' && (
        <label>
          What went wrong?
          <TextBox rows={3} value={note} onValue={setNote} disabled={busy} data-testid="bug-note" maxLength={1500} />
          <span className="hint" data-testid="bug-hint">{BUG_HINT}</span>
        </label>
      )}
      <div className="abc-foot">
        {msg && (
          <span className={msg.ok ? 'hint ok-text' : 'hint warn-text'} role="status" data-testid="bug-result">
            {msg.text}
          </span>
        )}
        <button type="submit" className="btn ghost-line small" disabled={!ready} data-testid="add-marker-submit">
          {type === 'Bug' ? 'Send bug report' : 'Add marker'}
        </button>
      </div>
    </form>
  );
}
