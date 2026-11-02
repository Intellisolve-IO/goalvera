import React, { useEffect, useRef, useState } from 'react';
import { authFetch, onRecorderSignOut, recorderSettings } from '../auth.js';
import { createRecordSession } from '../recordSession.js';
import { formatRecordingGap } from '../recordingGaps.js';
import {
  DEMO_RECORD_WARNING, HOME_SCREEN_HINT, KEEP_SCREEN_LINE, MIC_DENIED, PLACEMENT_LINE, PRIVACY_LINE,
  SAMPLE_LESSON_SCRIPT, SAMPLE_LESSON_TITLE, UNSUPPORTED,
} from '../sampleLesson.js';
import { SEGMENT_MS, pickMime } from '../webRecorder.js';
import { idbStore, memoryStore } from '../webRecorderStore.js';
import { createWakeLock } from '../wakeLock.js';

function clock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function makeUpload(base) {
  const root = String(base || '').replace(/\/$/, '');
  async function post(body) {
    const res = await authFetch(`${root}/v1/web/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  return {
    async sign(body) {
      const res = await authFetch(`${root}/v1/web/uploads`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    },
    confirm: (body) => post(body),
    finalize: (body) => post({ ...body, finalize: true }),
    async put(url, headers, blob) {
      const res = await fetch(url, { method: 'PUT', headers, body: blob });
      if (!res.ok) throw new Error('PUT');
    },
  };
}

export function RecordView({
  demo = true, uploadReady = false, classrooms = [], classLabel = '', onClass, phase = 'idle',
  elapsedMs = 0, level = 0, gaps = [], error = null, recordingId = null, hint = false, onDismissHint,
  onRecord, onPause, onResume, onStop, onResumeUnsent, onDiscard, wakeHeld = false,
}) {
  const busy = phase === 'recording' || phase === 'paused' || phase === 'uploading' || phase === 'unsent';
  return (
    <div className="record-page" data-testid="record-page">
      {demo ? <div className="banner" data-testid="record-demo-warning" role="note">{DEMO_RECORD_WARNING}</div> : null}
      <p className="banner record-keep" data-testid="record-keep-open">{KEEP_SCREEN_LINE}</p>
      <p className="muted">{PLACEMENT_LINE}</p>
      <p className="muted" data-testid="record-privacy">{PRIVACY_LINE}</p>
      {!uploadReady ? <p className="banner" data-testid="record-not-configured">This site is not set up to receive phone recordings yet.</p> : null}
      {hint ? (
        <p className="banner" data-testid="record-home-hint">
          {HOME_SCREEN_HINT}{' '}
          <button type="button" className="link" onClick={onDismissHint}>Hide</button>
        </p>
      ) : null}
      {demo ? (
        <section className="record-script" data-testid="sample-lesson">
          <h2>{SAMPLE_LESSON_TITLE}</h2>
          <pre>{SAMPLE_LESSON_SCRIPT}</pre>
        </section>
      ) : null}

      <label className="record-class">
        Classroom
        {classrooms.length ? (
          <select value={classLabel} onChange={(e) => onClass(e.target.value)} disabled={busy && phase !== 'unsent'} data-testid="record-class">
            <option value="">Choose a classroom</option>
            {classrooms.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <input value={classLabel} onChange={(e) => onClass(e.target.value)} disabled={busy && phase !== 'unsent'}
            placeholder="Room 104" data-testid="record-class" maxLength="40" />
        )}
      </label>

      <div className="record-meter" aria-hidden="true"><span style={{ width: `${Math.round(level * 100)}%` }} /></div>
      <p className="record-timer" data-testid="record-timer">{clock(elapsedMs)}</p>
      <p className="record-status" data-testid="record-status">
        {phase === 'recording' && (wakeHeld ? 'Recording. The screen stays on.' : 'Recording. Keep this screen open.')}
        {phase === 'paused' && 'Recording paused — tap Resume'}
        {phase === 'uploading' && 'Uploading'}
        {phase === 'processing' && 'Uploaded. Processing.'}
        {phase === 'unsent' && 'This phone still has part of a recording that is not confirmed.'}
        {phase === 'idle' && 'Ready'}
        {error === 'upload' && ' Upload will retry. Leave this page open, or come back and choose Resume.'}
        {error === 'denied' && ` ${MIC_DENIED}`}
        {error === 'unsupported' && ` ${UNSUPPORTED}`}
        {error === 'class' && ' Choose a classroom first.'}
        {error === 'empty' && ' Nothing was recorded.'}
      </p>

      <div className="record-actions">
        {phase === 'idle' || phase === 'processing' ? (
          <button type="button" className="btn primary record-go" data-testid="record-start" disabled={!uploadReady}
            onClick={onRecord}>Record</button>
        ) : null}
        {phase === 'recording' ? (
          <button type="button" className="btn primary record-stop" data-testid="record-stop" onClick={onStop}>Stop</button>
        ) : null}
        {phase === 'paused' ? (
          <>
            <button type="button" className="btn primary" data-testid="record-resume" onClick={onResume}>Resume</button>
            <button type="button" className="btn ghost" data-testid="record-stop" onClick={onStop}>Stop</button>
          </>
        ) : null}
        {phase === 'unsent' ? (
          <>
            <button type="button" className="btn primary" data-testid="record-resume-unsent" onClick={onResumeUnsent}>Resume</button>
            <button type="button" className="btn ghost" data-testid="record-discard" onClick={onDiscard}>Discard</button>
          </>
        ) : null}
      </div>

      {gaps.length ? (
        <ul className="record-gaps" data-testid="record-gaps">
          {gaps.map((g, i) => <li key={i}>{formatRecordingGap(g)}</li>)}
        </ul>
      ) : null}
      {recordingId ? (
        <p data-testid="record-session-link"><a href={`#recordings/${recordingId}`}>Open the session</a></p>
      ) : null}
    </div>
  );
}

export default function RecordPage({ user = null }) {
  const cfg = recorderSettings();
  const [classrooms, setClassrooms] = useState((user && user.classrooms) || []);
  const [classLabel, setClassLabel] = useState((user && user.default_classroom) || '');
  const [snap, setSnap] = useState(null);
  const [level, setLevel] = useState(0);
  const [tick, setTick] = useState(0);
  const [hint, setHint] = useState(false);
  const [wakeHeld, setWakeHeld] = useState(false);
  const sessionRef = useRef(null);
  const recRef = useRef(null);
  const streamRef = useRef(null);
  const segStart = useRef(0);
  const hold = useRef(false);
  const rotate = useRef(null);
  const wake = useRef(null);
  const meterStop = useRef(null);

  function session() {
    if (!sessionRef.current) {
      const storage = typeof indexedDB === 'undefined' ? memoryStore() : idbStore();
      sessionRef.current = createRecordSession({
        storage, upload: makeUpload(cfg.uploadApi), now: () => Date.now(),
      });
    }
    return sessionRef.current;
  }

  function stopStream() {
    clearTimeout(rotate.current);
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (meterStop.current) meterStop.current();
    meterStop.current = null;
    setLevel(0);
  }

  function startPiece() {
    const stream = streamRef.current;
    if (!stream || typeof MediaRecorder === 'undefined') return;
    const mime = pickMime((t) => MediaRecorder.isTypeSupported(t));
    let rec;
    try {
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    } catch {
      setSnap((s) => ({ ...(s || session().getState()), error: 'unsupported' }));
      return;
    }
    recRef.current = rec;
    segStart.current = Date.now();
    rec.ondataavailable = (ev) => {
      if (!ev.data || !ev.data.size) return;
      session().onSegment({ blob: ev.data, durationMs: Date.now() - segStart.current, mime: rec.mimeType || mime || 'audio/mp4' });
    };
    rec.onstop = () => {
      if (!hold.current && session().getState().phase === 'recording') startPiece();
    };
    rec.onerror = () => {
      hold.current = true;
      session().pause();
    };
    rec.start();
    clearTimeout(rotate.current);
    rotate.current = setTimeout(() => {
      if (rec.state === 'recording') rec.stop();
    }, SEGMENT_MS);
  }

  function startMeter(stream) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 256;
    src.connect(an);
    const data = new Uint8Array(an.fftSize);
    let raf = 0;
    const loop = () => {
      an.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
      setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4));
      raf = requestAnimationFrame(loop);
    };
    loop();
    meterStop.current = () => { cancelAnimationFrame(raf); ctx.close(); };
  }

  async function openMic() {
    if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') return { ok: false, error: 'unsupported' };
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      streamRef.current = stream;
      const track = stream.getAudioTracks()[0];
      if (track) {
        track.addEventListener('ended', () => { hold.current = true; session().pause(); });
        track.addEventListener('mute', () => { hold.current = true; if (recRef.current && recRef.current.state === 'recording') recRef.current.stop(); session().pause(); });
      }
      startMeter(stream);
      return { ok: true };
    } catch (e) {
      const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
      return { ok: false, error: denied ? 'denied' : 'unsupported' };
    }
  }

  function flushRecorder() {
    hold.current = true;
    const rec = recRef.current;
    if (rec && rec.state === 'recording') rec.stop();
  }

  useEffect(() => {
    const s = session();
    const unsub = s.subscribe(setSnap);
    s.boot().then(setSnap);
    wake.current = createWakeLock();
    const onHide = () => {
      if (s.getState().phase !== 'recording') return;
      flushRecorder();
      s.pause();
      wake.current.release();
      setWakeHeld(false);
    };
    const onShow = () => { if (wake.current.wanted) wake.current.onVisible().then(setWakeHeld); };
    const onVis = () => { if (document.visibilityState === 'hidden') onHide(); else onShow(); };
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('pagehide', onHide);
    const offSignOut = onRecorderSignOut(async () => {
      hold.current = true;
      flushRecorder();
      stopStream();
      if (wake.current) await wake.current.release();
      await s.discard();
    });
    const standalone = window.navigator.standalone === true
      || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
    if (!standalone && window.localStorage && localStorage.getItem('goalvera-a2hs') !== '1') setHint(true);
    const id = setInterval(() => setTick((n) => n + 1), 250);
    return () => {
      unsub(); clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('pagehide', onHide);
      offSignOut();
    };
    // session is created once; classrooms load below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (classrooms.length) return undefined;
    let live = true;
    authFetch('/api/classes').then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (live && d && Array.isArray(d.classrooms) && d.classrooms.length) setClassrooms(d.classrooms);
    }).catch(() => {});
    return () => { live = false; };
  }, [classrooms.length]);

  const state = snap || { phase: 'idle', elapsedMs: 0, gaps: [], error: null, recordingId: null, parts: [] };
  const liveElapsed = state.phase === 'recording' ? state.elapsedMs + Math.max(0, Date.now() - (segStart.current || Date.now())) : state.elapsedMs;
  void tick;

  async function onRecord() {
    if (!cfg.uploadApi) return;
    hold.current = false;
    const next = session().start(classLabel);
    setSnap(next);
    if (next.error === 'class' || next.phase !== 'recording') return;
    const opened = await openMic();
    if (!opened.ok) {
      if (session().getState().parts.length) session().pause();
      else await session().discard();
      setSnap({ ...session().getState(), error: opened.error });
      return;
    }
    const held = wake.current ? await wake.current.acquire() : false;
    setWakeHeld(!!held);
    hold.current = false;
    startPiece();
  }

  async function onResume() {
    hold.current = false;
    session().resume();
    const live = streamRef.current && streamRef.current.getAudioTracks().some((t) => t.readyState === 'live');
    if (!live) {
      const opened = await openMic();
      if (!opened.ok) { setSnap({ ...session().getState(), error: opened.error }); return; }
    }
    const held = wake.current ? await wake.current.acquire() : false;
    setWakeHeld(!!held);
    startPiece();
  }

  async function onStop() {
    hold.current = true;
    const rec = recRef.current;
    if (rec && rec.state === 'recording') {
      await new Promise((resolve) => {
        rec.addEventListener('stop', () => resolve(), { once: true });
        rec.stop();
      });
    }
    stopStream();
    if (wake.current) await wake.current.release();
    setWakeHeld(false);
    setSnap(await session().stop());
  }

  return (
    <RecordView
      demo={cfg.demo} uploadReady={!!cfg.uploadApi} classrooms={classrooms} classLabel={classLabel}
      onClass={setClassLabel} phase={state.phase} elapsedMs={liveElapsed} level={level}
      gaps={state.gaps || []} error={state.error} recordingId={state.recordingId} hint={hint}
      wakeHeld={wakeHeld}
      onDismissHint={() => { try { localStorage.setItem('goalvera-a2hs', '1'); } catch { /* private mode */ } setHint(false); }}
      onRecord={onRecord} onPause={() => {}} onResume={onResume} onStop={onStop}
      onResumeUnsent={async () => setSnap(await session().resumeUnsent())}
      onDiscard={async () => { stopStream(); setSnap(await session().discard()); }}
    />
  );
}
