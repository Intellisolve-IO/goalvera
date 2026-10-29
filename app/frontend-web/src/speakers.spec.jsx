// Speaker selector from the roster (David, Oct 10 2026; tasks#104): the session's class first (its teachers,
// RBTs and students, first name + last initial), then everyone else (search narrows it), then generic options;
// an earlier choice stays selectable; read-only disables it. RBTs show their supervisor on the Teachers tab.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import SpeakerPicker, { FALLBACK_GENERIC, matches, pickerGroups } from './components/SpeakerPicker.jsx';
import SpeakerTimeline from './components/SpeakerTimeline.jsx';
import { TeacherTable } from './components/Teachers.jsx';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const CHOICES = {
  basis: { kind: 'class', name: 'Room 104 Reading' },
  in_class: [
    { id: 'stf_0001', label: 'Alex M.', kind: 'staff', role: 'Lead Special Education Teacher', classroom: 'Room 104' },
    { id: 'stf_0010', label: 'Flynn R.', kind: 'staff', role: 'RBT', classroom: 'Room 104' },
    { id: 'stu_0001', label: 'Doc D.', kind: 'student', role: 'Student', classroom: 'Room 104', session_student: true },
    { id: 'stu_0006', label: 'Sneezy D.', kind: 'student', role: 'Student', classroom: 'Room 104' },
  ],
  others: [
    { id: 'stf_0009', label: 'Mary P.', kind: 'staff', role: 'BCBA' },
    { id: 'stu_0101', label: 'Ava B.', kind: 'student', role: 'Student', classroom: 'Room 101' },
    { id: 'stu_0102', label: 'Zoe F.', kind: 'student', role: 'Student', classroom: 'Room 103' },
  ],
  generic: ['Unknown speaker', 'Other adult', 'Other student'],
};

describe('speaker picker', () => {
  test('class first, then everyone else, then Other; staff show their role', () => {
    const h = renderToStaticMarkup(<SpeakerPicker speaker="Speaker 1" choices={CHOICES} />);
    const t = text(h);
    expect(h.indexOf('label="In Room 104 Reading"')).toBeLessThan(h.indexOf('label="Everyone else"'));
    expect(h.indexOf('label="Everyone else"')).toBeLessThan(h.indexOf('label="Other"'));
    expect(t).toContain('Alex M. · Lead Special Education Teacher');
    expect(t).toContain('Flynn R. · RBT');
    expect(h).toContain('value="Sneezy D."');
    expect(t).toContain('Ava B. (Room 101)');
    expect(t).toContain('Unknown speaker');
    expect(h).toContain('aria-label="Who is Speaker 1?"');
    expect(h).toContain('data-testid="speaker-search"');
    expect(t).not.toMatch(/Dwarf|Rider|Poppins/);                       // never full last names
  });

  test('search narrows everyone else; an old choice stays selectable', () => {
    const g = pickerGroups(CHOICES, 'room 103', 'Teacher');
    expect(g.others.map((p) => p.label)).toEqual(['Zoe F.']);
    expect(g.keep).toBe('Teacher');
    expect(pickerGroups(CHOICES, '', 'Sneezy D.').keep).toBeNull();
    expect(matches({ label: 'Mary P.', role: 'BCBA' }, 'bcba')).toBe(true);
    expect(pickerGroups(null).generic).toEqual(FALLBACK_GENERIC);
  });

  test('long lists: everyone else is capped until you type', () => {
    const many = { ...CHOICES, others: Array.from({ length: 90 }, (_, i) => ({ id: `s${i}`, label: `Kid${i} A.`, kind: 'student', role: 'Student' })) };
    const g = pickerGroups(many);
    expect(g.others).toHaveLength(60);
    expect(g.more).toBe(30);
    expect(pickerGroups(many, 'kid8').others.length).toBe(11);
  });

  test('read-only: disabled, no search box', () => {
    const h = renderToStaticMarkup(<SpeakerPicker speaker="Speaker 2" value="Doc D." choices={CHOICES} readOnly />);
    expect(h).toMatch(/<select[^>]*disabled/);
    expect(h).not.toContain('speaker-search');
  });

  test('the timeline uses the picker when choices are loaded, the plain list otherwise', () => {
    const base = { turns: [{ speaker: 'Speaker 1', start_seconds: 0, end_seconds: 5, text: 'hi' }], speakers: ['Speaker 1'],
      markers: [], duration: 10, options: ['', 'Unknown speaker'] };
    expect(renderToStaticMarkup(<SpeakerTimeline {...base} choices={CHOICES} />)).toContain('data-testid="speaker-picker"');
    expect(renderToStaticMarkup(<SpeakerTimeline {...base} />)).not.toContain('speaker-picker');
  });
});

describe('Teachers tab', () => {
  test('RBTs are staff rows with their role and supervising BCBA', () => {
    const h = renderToStaticMarkup(<TeacherTable data={{ teachers: [
      { id: 'stf_0010', name: 'Flynn Rider', role: 'RBT (read-only)', classrooms: ['Room 104'],
        supervisor: 'Mary Poppins' }] }} />);
    expect(text(h)).toContain('RBT (read-only) · supervised by Mary Poppins');
  });
});
