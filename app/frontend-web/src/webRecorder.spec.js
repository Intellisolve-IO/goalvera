import { describe, expect, test } from 'vitest';
import { createHash } from 'node:crypto';
import {
  canRecord, checksums, cleanClassCode, extensionFor, newSessionId, partName, pickMime,
} from './webRecorder.js';
import { formatRecordingGap } from './recordingGaps.js';
import { createWakeLock } from './wakeLock.js';
import { DEMO_RECORD_WARNING, SAMPLE_LESSON_SCRIPT } from './sampleLesson.js';

describe('phone recorder helpers', () => {
  test('only a teacher or an admin may record', () => {
    expect(canRecord({ role: 'teacher' })).toBe(true);
    expect(canRecord({ role: 'admin' })).toBe(true);
    expect(canRecord(null)).toBe(false);
    expect(canRecord({})).toBe(false);
    expect(canRecord({ role: 'reviewer' })).toBe(false);
    expect(canRecord({ role: 'rbt', read_only: true })).toBe(false);
    expect(canRecord({ role: 'rbt' })).toBe(false);
    expect(canRecord({ role: 'coach' })).toBe(false);
    expect(canRecord({ role: '' })).toBe(false);
    expect(canRecord({ role: 'teacher', read_only: true })).toBe(false);
  });

  test('class codes follow the board filename rule', () => {
    expect(cleanClassCode('Room 104')).toBe('Room-104');
    expect(cleanClassCode('Reading 3A')).toBe('Reading-3A');
    expect(cleanClassCode('  Math 2B ')).toBe('Math-2B');
  });

  test('Safari mp4 is preferred, and WebM is accepted', () => {
    expect(pickMime((t) => t === 'audio/mp4')).toBe('audio/mp4');
    expect(pickMime((t) => t.startsWith('audio/webm'))).toBe('audio/webm;codecs=opus');
    expect(pickMime(() => false)).toBe(null);
    expect(extensionFor('audio/mp4')).toBe('m4a');
    expect(extensionFor('audio/webm;codecs=opus')).toBe('webm');
    expect(partName(1, 'audio/mp4')).toBe('part_0001.m4a');
  });

  test('MD5 and CRC32C match the bytes Google will check', () => {
    const bytes = new TextEncoder().encode('123456789');
    const md5 = createHash('md5').update(bytes).digest('base64');
    expect(checksums(bytes).md5).toBe(md5);
    expect(checksums(bytes).crc32c).toBe('4waSgw=='); // crc32c('123456789') == 0xe3069283
    expect(checksums(bytes).size).toBe(9);
  });

  test('a session id is unguessable-shaped and a gap reads as a sentence', () => {
    expect(newSessionId(new Uint8Array(16).fill(0xab))).toBe(`s${'ab'.repeat(16)}`);
    expect(formatRecordingGap({ offset_seconds: 134, duration_seconds: 40 })).toBe('Recording paused 2:14 for 40 s');
  });

  test('the demo script does not name a real student', () => {
    expect(DEMO_RECORD_WARNING).toMatch(/Do not record real students/);
    expect(SAMPLE_LESSON_SCRIPT).toContain('Student A');
  });

  test('wake lock is requested again when the page is visible', async () => {
    let asks = 0;
    const nav = { wakeLock: { request: async () => { asks += 1; return { addEventListener() {}, release: async () => {} }; } } };
    const doc = { visibilityState: 'hidden' };
    const lock = createWakeLock(nav, doc);
    expect(await lock.acquire()).toBe(false);
    expect(asks).toBe(0);
    doc.visibilityState = 'visible';
    expect(await lock.onVisible()).toBe(true);
    expect(asks).toBe(1);
    expect(lock.held).toBe(true);
  });
});
