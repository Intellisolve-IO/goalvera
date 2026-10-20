// Teacher Notes and classroom materials (): what the teacher sees, and who gets buttons.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';

vi.mock('firebase/app', () => ({ initializeApp: () => ({}) }));
vi.mock('firebase/auth', () => ({
  initializeAuth: () => ({ currentUser: null }), indexedDBLocalPersistence: {}, browserLocalPersistence: {},
  connectAuthEmulator: () => {}, onIdTokenChanged: () => () => {}, sendEmailVerification: async () => {},
  sendPasswordResetEmail: async () => {}, signInWithEmailAndPassword: async () => {}, signOut: async () => {},
}));

const { default: MaterialsPanel } = await import('./components/MaterialsPanel.jsx');
const { MaterialsEmailSetting } = await import('./components/Settings.jsx');
const { default: MaterialsInstruction } = await import('./components/MaterialsInstruction.jsx');
const { default: PlanMaterials, lessonText } = await import('./components/PlanMaterials.jsx');
const { WeekColumn } = await import('./components/LessonPlanner.jsx');
const { countMax, dueDate, emailStatusText, formFrom, makingText, suggestionText } = await import('./materials.js');
const { parseHash } = await import('./routes.js');

const TYPES = [
  { type: 'worksheet', label: 'Worksheet', min: 2, default: 5, max: 8 },
  { type: 'coloring', label: 'Coloring page', min: 1, default: 1, max: 4 },
  { type: 'flashcards', label: 'Flashcards', min: 2, default: 8, max: 12, renderer: 'svg', photo_max: 8,
    styles: [{ style: 'line', label: 'Line art', renderer: 'svg' }, { style: 'color', label: 'Color', renderer: 'svg' },
      { style: 'photo', label: 'Photo-style (slow)', renderer: 'photo' }] },
  { type: 'labels', label: 'Classroom labels', min: 2, default: 8, max: 12 },
  { type: 'multiplication_table', label: 'Multiplication table', min: 5, default: 12, max: 12, renderer: 'template' },
];
const VIEW = {
  recording_id: 'rec_x', can_make: true, types: TYPES, default_student_ids: ['stu_0001'],
  students: [{ student_id: 'stu_0001', first_name: 'Doc', last_initial: 'D.' }],
  notes: [
    { index: 0, offset_seconds: 40, is_request: true, suggestions: [
      { type: 'flashcards', count: 8, topic: 'trains', due: 'tomorrow' },
      { type: 'worksheet', count: 5, topic: 'trains', due: 'tomorrow' }] },
    { index: 1, offset_seconds: 125, is_request: false, suggestions: [] },
  ],
  drafts: 3,
  materials: [{ material_id: 'mat_20261010T130000_abcdef', type: 'flashcards', type_label: 'Flashcards', count: 8,
    topic: 'trains', due: '2026-10-11', title: 'Train cards', students: ['Doc'], student_ids: ['stu_0001'],
    note_index: 0, created_at: '2026-10-10T17:00:00Z', emails: [{ at: '2026-10-10T17:00:05Z', status: 'emailed' }],
    drafts: [1, 2, 3].map((n) => ({ draft: n, variation: `focus ${n}`, preview_png: 'iVBORw0KGgo=' })) }],
  email: { enabled: true, to: 's********@staff.example', has_address: true },
};

describe('materials panel', () => {
  test('teacher sees each Teacher Note, up to 3 suggestions, and the made material with its buttons', () => {
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={VIEW} />);
    expect(html).toContain('Teacher Note at 0:40');
    expect(html).toContain('Flashcards · 8 cards · trains · for tomorrow');
    expect((html.match(/data-testid="material-draft"/g) || []).length).toBe(3);
    expect((html.match(/data-testid="material-download-draft"/g) || []).length).toBe(3);
    expect(html).toContain('8 cards per page');
    expect(html).toContain('Draft 2');
    expect(html).toContain('src="data:image/png;base64,');
    expect(html).not.toContain('<svg');
    expect(html).toContain('no materials request');
    expect(html).toContain('Nothing is made or emailed until you do');
    expect((html.match(/data-testid="material-suggestion"/g) || []).length).toBe(2);
    for (const t of ['material-download', 'material-resend', 'material-regenerate']) expect(html).toContain(t);
    expect(html).toContain('Emailed');
    expect(html).not.toContain('Dwarf');
  });

  test('read-only viewers get no buttons', () => {
    const v = { ...VIEW, can_make: false, notes: [{ ...VIEW.notes[0], pending: true, suggestions: [] }], students: [] };
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={v} />);
    expect(html).not.toContain('material-suggestion');
    expect(html).not.toContain('material-download');
    expect(html).not.toContain('material-regenerate');
  });

  test('email off: no Resend', () => {
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={{ ...VIEW, email: { enabled: false } }} />);
    expect(html).not.toContain('material-resend');
    expect(html).toContain('material-download');
  });

  test('a photo-style set being made shows its progress and no downloads yet', () => {
    const making = { ...VIEW.materials[0], material_id: 'mat_20261010T140000_aaaaaa', renderer: 'photo', style: 'photo',
      status: 'making', pictures_made: 2, pictures_total: 8, eta_minutes: 4,
      drafts: [{ draft: 1, variation: 'photo-style pictures', preview_png: 'iVBORw0KGgo=' }], emails: [] };
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={{ ...VIEW, materials: [making] }}
      api={{}} />);
    expect(html).toContain('may take a few minutes (2 of 8 done, about 4 min left)');
    expect(html).toContain('Flashcards (photo-style)');
    expect(html).not.toContain('material-download');
    expect(html).not.toContain('material-resend');
  });

  test('a finished photo-style set shows its pages and one Download', () => {
    const done = { ...VIEW.materials[0], renderer: 'photo', style: 'photo', status: 'ready',
      drafts: [1, 2].map((p) => ({ draft: 1, page: p, pages: 2, variation: 'photo-style pictures', preview_png: 'iVBORw0KGgo=' })) };
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={{ ...VIEW, materials: [done] }} />);
    expect(html).toContain('Page 2 of 2');
    expect(html).not.toContain('material-download-draft');
    expect(html).toContain('>Download<');
  });

  test('a code-built table says up to N and has one layout per draft', () => {
    const table = { ...VIEW.materials[0], type: 'multiplication_table', type_label: 'Multiplication table', count: 12,
      renderer: 'template', status: 'ready' };
    const html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={{ ...VIEW, materials: [table] }} />);
    expect(html).toContain('Multiplication table · up to 12');
  });

  test('nothing to show: renders nothing', () => {
    expect(renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={{ ...VIEW, notes: [], materials: [] }} />)).toBe('');
  });
});

describe('materials helpers', () => {
  test('due words become dates', () => {
    const now = new Date(2026, 9, 10, 9, 0);
    expect(dueDate('today', now)).toBe('2026-10-10');
    expect(dueDate('tomorrow', now)).toBe('2026-10-11');
    expect(dueDate('2026-10-20', now)).toBe('2026-10-20');
    expect(dueDate('next week', now)).toBe('');
  });

  test('form from a suggestion clamps the count', () => {
    const f = formFrom({ type: 'coloring', count: 99, topic: 'trains', due: '' }, VIEW);
    expect(f).toMatchObject({ type: 'coloring', count: 4, student_ids: ['stu_0001'], due: '' });
    expect(f).not.toHaveProperty('pictures');
    expect(formFrom({ type: 'flashcards', count: null }, VIEW).count).toBe(8);
  });

  test('flashcards keep their style; photo-style sets are smaller', () => {
    const f = formFrom({ type: 'flashcards', count: 12, style: 'photo' }, VIEW);
    expect(f).toMatchObject({ style: 'photo', count: 8 });
    expect(formFrom({ type: 'flashcards', count: 12 }, VIEW)).toMatchObject({ style: 'line', count: 12 });
    expect(formFrom({ type: 'coloring', count: 1 }, VIEW)).not.toHaveProperty('style');
    expect(countMax(TYPES[2], 'photo')).toBe(8);
    expect(suggestionText({ type: 'flashcards', style: 'color', count: 8, topic: 'farm animals', due: '' }))
      .toBe('Flashcards (color) · 8 cards · farm animals');
    expect(suggestionText({ type: 'multiplication_table', count: 12, topic: '', due: '' })).toBe('Multiplication table · up to 12');
    expect(makingText({ pictures_made: 0, pictures_total: 3, eta_minutes: 2 })).toContain('may take a few minutes');
  });

  test('suggestion and email words', () => {
    expect(suggestionText({ type: 'labels', count: 1, topic: '', due: '' })).toBe('Classroom labels · 1 label');
    expect(emailStatusText('emailed', 's***@x.io')).toBe('Emailed to s***@x.io');
    expect(emailStatusText('no_staff_email')).toBe('Not emailed: no staff email on file');
  });

  test('a materials email link opens its session', () => {
    expect(parseHash('#recordings/rec_dev01_20261009_101500_Room104').sessionId).toBe('rec_dev01_20261009_101500_Room104');
    expect(parseHash('#recordings/../etc').sessionId).toBe(null);
  });
});

describe('admin email setting', () => {
  test('shows on/off, the sender and the service state', () => {
    const html = renderToStaticMarkup(<MaterialsEmailSetting initial={{ enabled: true, sender: 'notifications@staff.example', mail_state: 'not set up' }} />);
    expect(html).toContain('On · sent from notifications@staff.example');
    expect(html).toContain('Turn off');
    expect(html).toContain('needs the one-time approval');
  });
});

describe('AI instruction for classroom materials', () => {
  const I = { instructions: [
    { key: 'coloring', label: 'Coloring page (SVG, line art)', renderer: 'svg', instruction: 'You are an expert SVG designer…',
      default: 'You are an expert SVG designer…', is_default: true, updated_at: null },
    { key: 'multiplication_table', label: 'Multiplication table (template parameters)', renderer: 'template',
      instruction: 'Choose the table…', default: 'Choose the table…', is_default: true, updated_at: null },
    { key: 'flashcards_color', label: 'Flashcards, color (SVG)', renderer: 'svg', instruction: 'Bright colors',
      default: 'x', is_default: false, updated_at: '2026-10-10T20:00:00Z' },
  ], max: 8000, model: 'gemini-3.8-flash', image_model: 'gemini-3.1-flash-lite-image' };
  test('admin can pick a type, edit and reset', () => {
    const html = renderToStaticMarkup(<MaterialsInstruction initial={{ ...I, can_edit: true }} />);
    expect(html).toContain('materials-instruction-save');
    expect(html).toContain('materials-instruction-key');
    expect(html).toContain('Flashcards, color (SVG) (changed)');
    expect(html).toContain('Reset to default');
    expect(html).toContain('gemini-3.8-flash');
    expect(html).toContain('You are an expert SVG designer…');
    expect(html).not.toMatch(/<textarea[^>]*readonly/i);
  });
  test('teacher sees it read-only', () => {
    const html = renderToStaticMarkup(<MaterialsInstruction initial={{ ...I, can_edit: false }} />);
    expect(html).toMatch(/<textarea[^>]*readonly/i);
    expect(html).not.toContain('materials-instruction-save');
    expect(html).toContain('admins can change it in Settings');
  });
  test('one type only (the Make form view): template wording, no picker', () => {
    const html = renderToStaticMarkup(<MaterialsInstruction initial={{ ...I, can_edit: true }} only="multiplication_table" />);
    expect(html).toContain('the app computes every number');
    expect(html).toContain('Choose the table…');
    expect(html).not.toContain('materials-instruction-key');
    expect(html).not.toContain('materials-instruction-save');
  });
});

describe('lesson plan materials', () => {
  const INFO = { can_make: true, types: TYPES, students: VIEW.students, classes: [{ id: 'cls_m', name: 'Math 2B', domain_name: 'Mathematics' }],
    email: { enabled: true, has_address: true, to: 's***@staff.example' } };
  const ITEM = { ...VIEW.materials[0], type: 'multiplication_table', type_label: 'Multiplication table', count: 12,
    title: 'Multiplication 1-12', renderer: 'template', status: 'ready', lesson: { day: 'tue', label: 'Math block' },
    preview_png: 'iVBORw0KGgo=' };

  test('a week shows its attached worksheet with the lesson, download, regenerate and remove', () => {
    const html = renderToStaticMarkup(<PlanMaterials classCode="Room 104" week="2026-10-05" info={INFO} items={[ITEM]} />);
    expect(html).toContain('Multiplication 1-12');
    expect(html).toContain('Tuesday · Math block');
    expect(html).toContain('up to 12');
    for (const t of ['plan-material-download', 'plan-material-regenerate', 'plan-material-remove', 'plan-create-worksheet']) {
      expect(html).toContain(t);
    }
    expect(html).not.toContain('<svg');
  });

  test('read-only viewers and other teachers see the list without buttons', () => {
    const html = renderToStaticMarkup(<PlanMaterials classCode="Room 104" week="2026-10-05" info={{ ...INFO, can_make: false }} items={[ITEM]} />);
    expect(html).toContain('Multiplication 1-12');
    expect(html).not.toContain('plan-create-worksheet');
    expect(html).not.toContain('plan-material-remove');
  });

  test('a session material copied in says where it came from (no regenerate)', () => {
    const html = renderToStaticMarkup(<PlanMaterials classCode="Room 104" week="2026-10-05" info={INFO}
      items={[{ ...ITEM, attached_from: true, lesson: null }]} />);
    expect(html).toContain('From a session’s Teacher Note');
    expect(html).not.toContain('plan-material-regenerate');
  });

  test('the planner week column shows the materials between the plan and the students', () => {
    const slot = { slot: 'current', week: '2026-10-05', title: 'This week' };
    const nav = { thisWeek: '2026-10-05', past: '2026-09-28', earliest: '2025-10-06' };
    const html = renderToStaticMarkup(<WeekColumn slot={slot} nav={nav} dispatch={() => {}} canEditPlan readOnly={false}
      onSave={async () => {}} data={{ plan: null, students: [] }} classCode="Room 104"
      materials={{ ...INFO, weeks: { '2026-10-05': [ITEM] } }} />);
    expect(html.indexOf('plan-materials')).toBeGreaterThan(html.indexOf('plan-summary'));
    expect(html).toContain('Multiplication 1-12');
  });

  test('session materials offer Add to lesson plan, or say they are in it', () => {
    const v = { ...VIEW, plan_week: '2026-10-05' };
    let html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x" initial={v} />);
    expect(html).toContain('material-add-to-plan');
    html = renderToStaticMarkup(<MaterialsPanel recordingId="rec_x"
      initial={{ ...v, materials: [{ ...VIEW.materials[0], in_plan_week: '2026-10-05' }] }} />);
    expect(html).not.toContain('material-add-to-plan');
    expect(html).toContain('In the lesson plan');
    expect(lessonText({ day: 'fri', label: '' })).toBe('Friday');
  });
});
