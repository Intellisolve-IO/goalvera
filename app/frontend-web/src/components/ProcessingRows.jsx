import React, { useEffect, useRef, useState } from 'react';
import { loadUploadsStatus } from '../dataStore.js';

export const POLL_MS = 15000;      // while something is processing
export const IDLE_MS = 60000;      // otherwise: new uploads still show within a minute

/** "2026-10-10T09:00:00" (recorder's local time) -> "2026-10-10 09:00" */
const when = (s) => (s || '').replace('T', ' ').slice(0, 16);

// Recordings still being processed (): shown as soon as the upload lands, with the stage
// (Uploaded, Transcribing, Drafting notes) or a clear failure; refreshed every 15 s while any is
// processing (every minute otherwise, so a new upload appears by itself). When one finishes, the Recordings list reloads and the normal row takes its place.
export function ProcessingTable({ uploads }) {
  if (!uploads || !uploads.length) return null;
  return (
    <div className="classroom-group processing-group" data-testid="processing-group">
      <h3 className="classroom-name">Uploading / processing</h3>
      <table className="pipeline-table">
        <thead>
          <tr><th>Recorded</th><th>Classroom</th><th>Device</th><th>Length</th><th>Status</th></tr>
        </thead>
        <tbody>
          {uploads.map((u) => (
            <tr key={u.recording_id} data-testid="processing-row" className={u.state === 'failed' ? 'processing-failed' : 'processing-row'}>
              <td>{when(u.recorded_at_local)}</td>
              <td>{u.needs_class_assignment ? <span className="muted">{u.class_code} (not in roster)</span> : u.class_code}</td>
              <td><span className="muted">{u.device_id}</span></td>
              <td>{u.duration_seconds ? `${Math.max(1, Math.round(u.duration_seconds / 60))} min` : '—'}</td>
              <td>
                {u.state === 'failed' ? (
                  <span className="badge status-failed" data-testid="processing-failed" title={u.error_code || ''}>
                    {u.label}: {u.error_text}
                  </span>
                ) : (
                  <span className="badge status-processing" data-testid="processing-state">
                    <span className="spinner" aria-hidden="true" /> {u.label}…
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Notes usually take a few minutes after the upload. This list refreshes by itself.</p>
    </div>
  );
}

export default function ProcessingRows({ enabled = true, onFinished = null, pollMs = POLL_MS, idleMs = IDLE_MS }) {
  const [uploads, setUploads] = useState([]);
  const last = useRef(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    let timer = null;
    async function tick() {
      let v = null;
      try {
        v = await loadUploadsStatus();
      } catch {
        v = null;
      }
      if (!live) return;
      const rows = (v && v.uploads) || [];
      setUploads(rows);
      const ids = new Set(rows.filter((r) => r.processing).map((r) => r.recording_id));
      // one finished (or failed): reload the list so its notes show
      if (last.current && [...last.current].some((id) => !ids.has(id)) && onFinished) onFinished();
      last.current = ids;
      timer = setTimeout(tick, ids.size ? pollMs : idleMs);
    }
    tick();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [enabled, pollMs, idleMs, onFinished]);
  return <ProcessingTable uploads={uploads} />;
}
