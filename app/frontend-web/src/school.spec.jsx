// Classes + Domains tabs: the Domains table shows the document's four
// columns (methodology name bold, then its description); only admins get Add / Edit; the Classes table
// shows teachers, domain, students and the schedule; the class editor handles several meeting times;
// the session class picker marks the suggestion; materials show the class's teaching approach.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import { DomainEditor, DomainTable } from './components/Domains.jsx';
import { ClassEditor, ClassTable } from './components/Classes.jsx';
import { endTime, meetingProblem, setupError } from './schoolSetup.js';
import { parseHash, hashFor } from './routes.js';
import { TAB_NOTES } from './App.jsx';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const DOMAINS = {
  version: 'seed', can_edit: false,
  domains: [{ id: 'dom-mathematics', name: 'Mathematics', methodology: 'Concrete-Pictorial-Abstract (CPA)',
    methodology_detail: 'Conceptual math instruction.', programs: 'Eureka Math (Great Minds)',
    book: 'Elementary and Middle School Mathematics: Teaching Developmentally by John A. Van de Walle', classes: 8 },
  { id: 'dom-x', name: 'Empty', methodology: '', methodology_detail: '', programs: '', book: '', classes: 0 }],
};
const CLASS = {
  id: 'cls-rm104-rdg', name: 'Room 104 Reading', classroom: 'Room 104', teacher_ids: ['stf_0001', 'stf_0010'],
  teachers: [{ id: 'stf_0001', name: 'Snow White' }, { id: 'stf_0010', name: 'Flynn Rider' }],
  domain_id: 'dom-reading-phonics', domain_name: 'Reading & Phonics', domain_methodology: 'Orton-Gillingham (OG) Approach',
  student_ids: ['stu_0001'], students: [{ student_id: 'stu_0001', name: 'Doc D.' }],
  schedule: [{ days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], start: '08:20', minutes: 90 }, { days: ['Sat'], start: '09:00', minutes: 30 }],
  schedule_text: 'Mon–Fri 8:20 AM, 90 min; Sat 9:00 AM, 30 min', can_edit: true, label: 'Fictional sample data',
};
const LOOKUPS = {
  classrooms: ['Room 101', 'Room 104'], own_classrooms: ['Room 104'], default_classroom: 'Room 104',
  domains: [{ id: 'dom-reading-phonics', name: 'Reading & Phonics', methodology: 'Orton-Gillingham (OG) Approach' }],
  staff: [{ id: 'stf_0001', name: 'Snow White', role: 'Lead Special Education Teacher' }, { id: 'stf_0010', name: 'Flynn Rider', role: 'RBT' }],
  students: [{ student_id: 'stu_0001', name: 'Doc D.', classrooms: ['Room 104'] }, { student_id: 'stu_0101', name: 'Ava B.', classrooms: ['Room 101'] }],
};

describe('Domains tab', () => {
  test('the document’s four columns, methodology name bold, a dash for an empty field', () => {
    const h = renderToStaticMarkup(<DomainTable data={DOMAINS} />);
    expect(text(h)).toContain('Subject / Domain Core Educational Methodology Key Curricular Programs Recommended Book / Guide Classes');
    expect(h).toContain('<strong class="ds-method">Concrete-Pictorial-Abstract (CPA)</strong>');
    expect(text(h)).toContain('Eureka Math (Great Minds)');
    expect(text(h)).toContain('Van de Walle');
    expect((h.match(/data-testid="domain-row"/g) || []).length).toBe(2);
    expect(text(h)).toMatch(/Empty — — —/);
  });

  test('Edit buttons only when the server says so (admins)', () => {
    expect(renderToStaticMarkup(<DomainTable data={DOMAINS} onEdit={() => {}} />)).not.toContain('edit-domain-');
    expect(renderToStaticMarkup(<DomainTable data={{ ...DOMAINS, can_edit: true }} onEdit={() => {}} />))
      .toContain('data-testid="edit-domain-dom-mathematics"');
  });

  test('editor: the five fields and Remove for an existing domain', () => {
    const h = renderToStaticMarkup(<DomainEditor initial={DOMAINS.domains[0]} onSave={() => {}} onCancel={() => {}} onRemove={() => {}} />);
    for (const n of ['name', 'methodology', 'methodology_detail', 'programs', 'book']) expect(h).toContain(`name="${n}"`);
    expect(h).toContain('value="Mathematics"');
    expect(h).toContain('data-testid="remove-domain"');
    expect(text(h)).toContain('no names of people');
  });
});

describe('Classes tab', () => {
  test('table: teachers, domain + methodology, students, every meeting time', () => {
    const h = renderToStaticMarkup(<ClassTable rows={[CLASS]} onEdit={() => {}} />);
    expect(text(h)).toContain('Class Classroom Teacher(s) Domain Students Schedule');
    expect(text(h)).toContain('Snow White, Flynn Rider');
    expect(text(h)).toContain('Reading & Phonics Orton-Gillingham (OG) Approach');
    expect(text(h)).toContain('Doc D.');
    expect(text(h)).toContain('Mon–Fri 8:20 AM, 90 min; Sat 9:00 AM, 30 min');
    expect(h).toContain('data-testid="edit-class-cls-rm104-rdg"');
    expect(renderToStaticMarkup(<ClassTable rows={[{ ...CLASS, can_edit: false }]} onEdit={() => {}} />)).not.toContain('edit-class-');
  });

  test('editor: one row per meeting, teacher only sees their own classrooms, students of the classroom', () => {
    const h = renderToStaticMarkup(<ClassEditor data={LOOKUPS} initial={CLASS} onSave={() => {}} onCancel={() => {}} />);
    expect((h.match(/data-testid="meeting"/g) || []).length).toBe(2);
    expect(h).toContain('data-testid="add-meeting"');
    expect(text(h)).not.toContain('Room 101');                 // a teacher: own classrooms only
    expect(text(h)).toContain('Doc D.');
    expect(text(h)).not.toContain('Ava B.');                    // other classrooms behind "show every classroom"
    expect(h).not.toContain('remove-class');
    const a = renderToStaticMarkup(<ClassEditor data={LOOKUPS} initial={CLASS} admin onSave={() => {}} onCancel={() => {}} onRemove={() => {}} />);
    expect(text(a)).toContain('Room 101');
    expect(a).toContain('data-testid="remove-class"');
  });

  test('schedule helpers and plain-words errors', () => {
    expect(endTime('08:20', 90)).toBe('9:50 AM');
    expect(endTime('12:30', 45)).toBe('1:15 PM');
    expect(meetingProblem({ days: [], start: '08:00', minutes: 30 })).toMatch(/day/);
    expect(meetingProblem({ days: ['Mon'], start: '', minutes: 30 })).toMatch(/start/);
    expect(meetingProblem({ days: ['Mon'], start: '08:00', minutes: 2 })).toMatch(/5 to 600/);
    expect(meetingProblem({ days: ['Mon'], start: '08:00', minutes: 30 })).toBeNull();
    expect(setupError(new Error('STALE_VERSION'))).toMatch(/saved a change first/);
    expect(setupError(new Error('DOMAIN_IN_USE'))).toMatch(/another domain first/);
    expect(setupError(new Error('WHAT'))).toMatch(/WHAT/);
  });
});

describe('routes and notes', () => {
  test('#classes and #domains are tabs with a note', () => {
    expect(parseHash('#classes').tab).toBe('classes');
    expect(parseHash('#domains').tab).toBe('domains');
    expect(hashFor({ tab: 'domains' })).toBe('#domains');
    expect(TAB_NOTES.classes).toMatch(/schedule/);
    expect(TAB_NOTES.domains).toMatch(/methodology/);
  });
});
