// Students in this session (): the class roster is offered ticked, marked "not confirmed",
// and one click (Confirm N students) saves it; saved sessions list the students; RBTs see no controls.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SessionStudents, { initialPicks } from './components/SessionStudents.jsx';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const V = {
  recording_id: 'r1', student_ids: [], saved: false, max: 40, can_change: true,
  suggestion: { source: 'class', class_name: 'Room 104 Reading', student_ids: ['stu_0001', 'stu_0006'] },
  choices: [{ student_id: 'stu_0001', name: 'Doc D.' }, { student_id: 'stu_0006', name: 'Sneezy D.' },
    { student_id: 'stu_0003', name: 'Happy D.' }],
  others: [{ student_id: 'stu_0101', name: 'Ava B.' }],
};

describe('Students in this session', () => {
  test('suggestion is ticked but not confirmed; one click confirms', () => {
    expect([...initialPicks(V)]).toEqual(['stu_0001', 'stu_0006']);
    const h = renderToStaticMarkup(<SessionStudents recordingId="r1" initial={V} />);
    expect(text(h)).toContain('not confirmed');
    expect(text(h)).toContain('Suggested: the 2 students of Room 104 Reading');
    expect(text(h)).toContain('Confirm 2 students');
    expect(h).toMatch(/data-testid="session-student-stu_0001"[^>]*checked|checked=""[^>]*data-testid="session-student-stu_0001"/);
    expect(h).not.toMatch(/checked=""[^>]*data-testid="session-student-stu_0003"/);
    expect(text(h)).toContain('Other students (1)');
  });

  test('saved students are listed; read-only users get no controls', () => {
    const saved = { ...V, saved: true, student_ids: ['stu_0006'], can_change: false };
    expect([...initialPicks(saved)]).toEqual(['stu_0006']);
    const h = renderToStaticMarkup(<SessionStudents recordingId="r1" initial={saved} />);
    expect(text(h)).toContain('Sneezy D.');
    expect(h).not.toContain('save-session-students');
    expect(h).not.toContain('not confirmed');
  });
});
