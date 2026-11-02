import { describe, expect, test } from 'vitest';
import { createRecordSession } from './recordSession.js';
import { memoryStore } from './webRecorderStore.js';
import { checksums } from './webRecorder.js';

function harness({ failTimes = 0 } = {}) {
  let now = 1_700_000_000_000;
  const storage = memoryStore();
  const puts = [];
  let fails = failTimes;
  const finalized = [];
  const discarded = [];
  const upload = {
    async sign(body) {
      return { object: `web/${body.session_id}/${body.filename}`, upload_url: 'https://storage.example/put', headers: { 'Content-MD5': body.md5 } };
    },
    async put(_url, _headers, blob) { puts.push(blob.size); },
    async confirm() {
      if (fails > 0) { fails -= 1; throw new Error('offline'); }
      return { verified: true, safe_to_delete_local_copy: true };
    },
    async finalize(body) { finalized.push(body); return { recording_id: 'rec_web_abc_20261102_180000_Room_104', ok: true }; },
    async discard(body) { discarded.push(body); return { discarded: true, deleted: 1 }; },
  };
  const session = createRecordSession({
    storage,
    upload,
    now: () => now,
    sleep: async () => {},
    checksum: async (blob) => checksums(new Uint8Array(await blob.arrayBuffer())),
    randomBytes: () => new Uint8Array(16).fill(4),
  });
  return { session, storage, puts, finalized, discarded, setNow: (n) => { now = n; } };
}

const blob = (n = 8) => new Blob([new Uint8Array(n)]);

describe('a phone session', () => {
  test('a confirmed piece is deleted from the phone', async () => {
    const { session, storage, puts } = harness();
    session.start('Room 104');
    const after = await session.onSegment({ blob: blob(12), durationMs: 1000, mime: 'audio/mp4' });
    expect(after.parts[0].state).toBe('confirmed');
    expect(after.parts[0].filename).toBe('part_0001.m4a');
    expect(puts).toEqual([12]);
    expect(await storage.listSegments()).toEqual([]);
  });

  test('a pause marks a gap and resume continues the same session', async () => {
    const { session, setNow } = harness();
    session.start('Room 104');
    const id = session.getState().sessionId;
    setNow(1_700_000_000_000);
    expect(session.pause()).toBe(true);
    setNow(1_700_000_040_000);
    expect(session.resume()).toBe(true);
    const st = session.getState();
    expect(st.sessionId).toBe(id);
    expect(st.phase).toBe('recording');
    expect(st.gaps).toEqual([{ offset_seconds: 0, duration_seconds: 40 }]);
  });

  test('reopening offers the unsent piece, and discard removes it', async () => {
    const { session, storage } = harness({ failTimes: 5 });
    session.start('Room 104');
    await session.onSegment({ blob: blob(4), durationMs: 500, mime: 'audio/webm' });
    expect(session.getState().parts[0].state).toBe('error');
    expect((await storage.listSegments()).length).toBe(1);
    const again = createRecordSession({
      storage,
      upload: { sign() { throw new Error('no'); }, put() {}, confirm() {}, finalize() {} },
      now: () => 0,
      sleep: async () => {},
    });
    const booted = await again.boot();
    expect(booted.phase).toBe('unsent');
    expect(booted.classLabel).toBe('Room 104');
    await again.discard();
    expect(await storage.listSegments()).toEqual([]);
    expect((await again.boot()).phase).toBe('idle');
  });

  test('stop finalizes only after every piece is confirmed', async () => {
    const { session, finalized } = harness();
    session.start('Room 104');
    await session.onSegment({ blob: blob(3), durationMs: 60_000, mime: 'audio/mp4' });
    const done = await session.stop();
    expect(done.phase).toBe('processing');
    expect(done.recordingId).toMatch(/^rec_/);
    expect(finalized[0].parts).toEqual([expect.objectContaining({ name: 'part_0001.m4a' })]);
    expect(finalized[0].class_label).toBe('Room 104');
    expect(finalized[0].finalize).toBeUndefined();
  });

  test('discard deletes the server pieces and the phone copy', async () => {
    const { session, storage, discarded } = harness();
    session.start('Room 104');
    const sid = session.getState().sessionId;
    await session.onSegment({ blob: blob(4), durationMs: 500, mime: 'audio/mp4' });
    await session.discard();
    expect(discarded).toEqual([{ session_id: sid }]);
    expect(await storage.listSegments()).toEqual([]);
    expect(session.getState().sessionId).toBe(null);
  });

  test('drop forgets the session the way sign-out does', async () => {
    const { session } = harness();
    session.start('Room 104');
    session.drop();
    expect(session.getState().phase).toBe('idle');
    expect(session.getState().sessionId).toBe(null);
  });
});
