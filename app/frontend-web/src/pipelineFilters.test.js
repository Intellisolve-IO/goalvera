import assert from 'node:assert/strict';
import test from 'node:test';
import { filterOptions, filterSessions, initialClassroomFilter } from './pipelineFilters.js';
import { SPEAKER_FILLS, SPEAKER_TEXT, speakerFill } from './speakerColors.js';

const sarahRm = {
  recording_id: 'rec_rm12',
  teacher_name: 'Sarah Jenkins',
  class_code: 'RM12',
  status: 'pending_review',
};
const sarahReading = {
  recording_id: 'rec_reading',
  teacher_name: 'Sarah Jenkins',
  class_code: 'Reading 3A',
  status: 'approved',
};
const unassigned = {
  recording_id: 'rec_art',
  teacher_name: '',
  class_code: 'Art 9Z',
  status: 'pending_review',
  needs_class_assignment: true,
};

test('empty filters mean All', () => {
  const sessions = [sarahRm, sarahReading, unassigned];
  assert.deepEqual(filterSessions(sessions, {}), sessions);
  assert.deepEqual(
    filterSessions(sessions, { teachers: [], classes: [], statuses: [] }),
    sessions
  );
});

test('filters combine and a cleared filter returns to All', () => {
  const sessions = [sarahRm, sarahReading, unassigned];
  const narrowed = filterSessions(sessions, {
    teachers: ['Sarah Jenkins'],
    classes: ['RM12'],
    statuses: ['pending_review'],
  });
  assert.deepEqual(narrowed.map((s) => s.recording_id), ['rec_rm12']);

  const backToAll = filterSessions(sessions, { teachers: [], classes: [], statuses: [] });
  assert.deepEqual(backToAll, sessions);
});

test('needs classroom assignment is its own status option', () => {
  const opts = filterOptions([sarahRm, unassigned]);
  assert.ok(opts.statuses.includes('needs classroom assignment'));
  assert.equal(
    filterSessions([sarahRm, unassigned], { statuses: ['needs classroom assignment'] }).length,
    1
  );
});

test('options come only from the scoped session list', () => {
  // A session that is not in this list must never become a filter choice.
  const notReturnedForThisUser = {
    recording_id: 'rec_hidden',
    teacher_name: 'Jordan Rivera',
    class_code: 'Math 9Z',
    status: 'pending_review',
  };
  const scopedToThisTeacher = [sarahRm, sarahReading];
  const opts = filterOptions(scopedToThisTeacher);

  assert.deepEqual(opts.teachers, ['Sarah Jenkins']);
  assert.deepEqual(opts.classes, ['Reading 3A', 'RM12']);
  assert.deepEqual(opts.statuses, ['approved', 'pending_review']);
  assert.equal(opts.teachers.includes(notReturnedForThisUser.teacher_name), false);
  assert.equal(opts.classes.includes(notReturnedForThisUser.class_code), false);

  const shown = filterSessions(scopedToThisTeacher, {
    teachers: opts.teachers,
    classes: opts.classes,
    statuses: opts.statuses,
  });
  assert.deepEqual(
    shown.map((s) => s.recording_id),
    scopedToThisTeacher.map((s) => s.recording_id)
  );
  assert.equal(shown.some((s) => s.recording_id === notReturnedForThisUser.recording_id), false);
});

function contrast(hexA, hexB) {
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const L = (hex) => {
    const n = parseInt(hex.slice(1), 16);
    const r = lin(((n >> 16) & 255) / 255);
    const g = lin(((n >> 8) & 255) / 255);
    const b = lin((n & 255) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [L(hexA), L(hexB)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

test('each speaker keeps one accessible fill', () => {
  const speakers = ['Speaker 2', 'Speaker 1', 'Speaker 10'];
  assert.equal(speakerFill('Speaker 1', speakers), speakerFill('Speaker 1', [...speakers].reverse()));
  assert.notEqual(speakerFill('Speaker 1', speakers), speakerFill('Speaker 2', speakers));
  for (const fill of SPEAKER_FILLS) {
    assert.ok(contrast(SPEAKER_TEXT, fill) >= 4.5, fill);
  }
});

test('the list opens on the teacher\'s own classrooms, otherwise All', () => {
  const sessions = [sarahRm, sarahReading, unassigned];
  assert.deepEqual(initialClassroomFilter(sessions, ['RM12', 'Math 2B']), ['RM12']);
  assert.deepEqual(initialClassroomFilter(sessions, []), []);
  assert.deepEqual(initialClassroomFilter(sessions, undefined), []);
  assert.deepEqual(initialClassroomFilter(sessions, ['Math 2B']), []);
  const opened = filterSessions(sessions, { classes: initialClassroomFilter(sessions, ['RM12']) });
  assert.deepEqual(opened.map((s) => s.recording_id), ['rec_rm12']);
});
