/**
 * One phone recording. Pieces are kept until the server confirms them, then deleted.
 * A lock, an app switch or a call pauses this same session and marks the gap.
 */
import { checksumBlob, cleanClassCode, localStamp, newSessionId, offsetMinutes, partName, round1 } from './webRecorder.js';

function blank() {
  return {
    phase: 'idle',
    sessionId: null,
    classLabel: '',
    startedLocal: null,
    offsetMinutes: 0,
    elapsedMs: 0,
    gaps: [],
    parts: [],
    error: null,
    recordingId: null,
    finalized: false,
    gapOpenWall: null,
    pausedAtMs: 0,
  };
}

function metaOf(state) {
  return {
    sessionId: state.sessionId,
    classLabel: state.classLabel,
    startedLocal: state.startedLocal,
    offsetMinutes: state.offsetMinutes,
    elapsedMs: state.elapsedMs,
    gaps: state.gaps,
    parts: state.parts.map(({ index, filename, bytes, state: st, md5, crc32c }) => (
      { index, filename, bytes, state: st, md5, crc32c })),
    recordingId: state.recordingId,
    finalized: state.finalized,
    gapOpenWall: state.gapOpenWall,
    pausedAtMs: state.pausedAtMs,
  };
}

export function createRecordSession(io) {
  let state = blank();
  const listeners = new Set();
  let pumping = false;
  let finalizeWanted = false;
  let generation = 0;
  const sleep = io.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const sumOf = io.checksum || checksumBlob;

  function emit(persist = true) {
    if (persist && state.sessionId) io.storage.saveMeta(metaOf(state)).catch(() => {});
    for (const fn of listeners) fn(snapshot());
  }

  function snapshot() {
    return { ...state, parts: state.parts.map((p) => ({ ...p })), gaps: state.gaps.map((g) => ({ ...g })) };
  }

  async function uploadPart(part) {
    const row = part.blob ? { blob: part.blob } : await io.storage.getSegment(state.sessionId, part.index);
    const blob = row && row.blob;
    if (!blob) throw new Error('MISSING');
    const sum = await sumOf(blob);
    const signed = await io.upload.sign({
      session_id: state.sessionId, filename: part.filename, size: sum.size, md5: sum.md5, crc32c: sum.crc32c,
    });
    await io.upload.put(signed.upload_url, signed.headers, blob);
    const conf = await io.upload.confirm({
      session_id: state.sessionId, object: signed.object, size: sum.size, md5: sum.md5, crc32c: sum.crc32c,
    });
    if (!conf || !conf.verified || !conf.safe_to_delete_local_copy) throw new Error('NOT_VERIFIED');
    await io.storage.deleteSegment(state.sessionId, part.index);
    part.blob = null;
    part.md5 = sum.md5;
    part.crc32c = sum.crc32c;
    part.bytes = sum.size;
  }

  async function pump() {
    if (pumping) return;
    pumping = true;
    const gen = generation;
    try {
      for (;;) {
        if (gen !== generation) return;
        const part = state.parts.find((p) => p.state === 'saved' || p.state === 'error');
        if (!part) break;
        part.state = 'uploading';
        emit();
        try {
          await uploadPart(part);
          part.state = 'confirmed';
          part.tries = 0;
          state.error = null;
        } catch {
          part.tries = (part.tries || 0) + 1;
          part.state = part.tries >= 5 ? 'error' : 'saved';
          state.error = 'upload';
          emit();
          if (part.tries >= 5) break;
          await sleep(Math.min(30000, 400 * (2 ** part.tries)));
          continue;
        }
        emit();
      }
    } finally {
      pumping = false;
    }
    if (gen === generation) await maybeFinalize();
  }

  async function maybeFinalize() {
    if (!finalizeWanted || !state.sessionId) return;
    if (state.parts.some((p) => p.state !== 'confirmed')) {
      state.phase = 'uploading';
      emit();
      return;
    }
    if (!state.parts.length) {
      state.error = 'empty';
      state.phase = 'paused';
      finalizeWanted = false;
      emit();
      return;
    }
    const res = await io.upload.finalize({
      session_id: state.sessionId,
      class_label: state.classLabel,
      started_local: state.startedLocal,
      offset_minutes: state.offsetMinutes,
      parts: state.parts.map((p) => ({ name: p.filename, size: p.bytes, md5: p.md5 })),
      gaps: state.gaps,
    });
    state.finalized = true;
    state.recordingId = res.recording_id;
    state.phase = 'processing';
    state.error = null;
    finalizeWanted = false;
    await io.storage.clear();
    emit(false);
  }

  return {
    getState: snapshot,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },

    async boot() {
      const meta = await io.storage.loadMeta();
      const segs = await io.storage.listSegments();
      if (!meta || !meta.sessionId || meta.finalized) {
        if (segs.length) await io.storage.clear();
        state = blank();
        emit(false);
        return snapshot();
      }
      state = { ...blank(), ...meta, parts: (meta.parts || []).map((p) => ({ ...p })) };
      const have = new Set(segs.map((s) => s.index));
      for (const p of state.parts) {
        if (p.state !== 'confirmed') p.state = have.has(p.index) ? 'saved' : 'error';
      }
      const unsentAudio = segs.length > 0 || state.parts.some((p) => p.state !== 'confirmed');
      const unfinished = state.parts.length > 0 && !state.finalized;
      state.phase = (unsentAudio || unfinished) ? 'unsent' : 'idle';
      if (state.phase === 'idle') {
        await io.storage.clear();
        state = blank();
      }
      emit(false);
      return snapshot();
    },

    start(classLabel) {
      const label = String(classLabel || '').trim();
      if (!cleanClassCode(label)) {
        state.error = 'class';
        emit(false);
        return snapshot();
      }
      if (!state.sessionId || state.finalized) {
        state = {
          ...blank(),
          sessionId: newSessionId(io.randomBytes && io.randomBytes(16)),
          classLabel: label,
          startedLocal: localStamp(new Date(io.now())),
          offsetMinutes: offsetMinutes(new Date(io.now())),
        };
      } else if (state.phase === 'paused' || state.phase === 'unsent') {
        closeGap();
      }
      state.classLabel = label;
      state.phase = 'recording';
      state.error = null;
      emit();
      return snapshot();
    },

    async onSegment({ blob, durationMs, mime }) {
      if (!state.sessionId || !blob || !blob.size) return snapshot();
      if (state.phase !== 'recording' && state.phase !== 'paused' && state.phase !== 'uploading') return snapshot();
      const index = state.parts.length + 1;
      const part = {
        index, filename: partName(index, mime), bytes: blob.size, state: 'saved', blob, md5: null, crc32c: null,
      };
      state.parts.push(part);
      state.elapsedMs += Math.max(0, durationMs || 0);
      await io.storage.putSegment(state.sessionId, index, {
        blob, filename: part.filename, sessionId: state.sessionId, index,
      });
      emit();
      await pump();
      return snapshot();
    },

    pause() {
      if (state.phase !== 'recording') return false;
      state.gapOpenWall = io.now();
      state.pausedAtMs = state.elapsedMs;
      state.phase = 'paused';
      emit();
      return true;
    },

    resume() {
      if (state.phase !== 'paused' && state.phase !== 'unsent') return false;
      closeGap();
      state.phase = 'recording';
      state.error = null;
      emit();
      return true;
    },

    async resumeUnsent() {
      if (state.phase !== 'unsent') return snapshot();
      state.phase = 'uploading';
      state.error = null;
      emit();
      await pump();
      if (!state.finalized && !finalizeWanted) {
        state.phase = 'paused';
        emit();
      }
      return snapshot();
    },

    async stop() {
      if (!state.sessionId || state.finalized) return snapshot();
      if (state.phase === 'recording') {
        state.phase = 'uploading';
        emit();
      }
      finalizeWanted = true;
      await pump();
      return snapshot();
    },

    async discard() {
      const sid = state.sessionId;
      generation += 1;
      finalizeWanted = false;
      pumping = false;
      if (sid && io.upload && typeof io.upload.discard === 'function') {
        try {
          await io.upload.discard({ session_id: sid });
        } catch {
          /* Phone copies are still removed below. The bucket lifecycle deletes web/<session>/ after a day. */
        }
      }
      await io.storage.clear();
      state = blank();
      emit(false);
      return snapshot();
    },

    /** Sign-out: forget the in-memory session. The caller deletes IndexedDB. */
    drop() {
      generation += 1;
      finalizeWanted = false;
      pumping = false;
      state = blank();
      for (const fn of listeners) fn(snapshot());
    },

    retry() { return pump(); },
  };

  function closeGap() {
    if (state.gapOpenWall == null) return;
    const dur = Math.max(0, io.now() - state.gapOpenWall);
    state.gaps.push({
      offset_seconds: round1(state.pausedAtMs / 1000),
      duration_seconds: round1(dur / 1000),
    });
    state.gapOpenWall = null;
  }
}
