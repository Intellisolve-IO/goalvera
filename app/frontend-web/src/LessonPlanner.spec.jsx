// Weekly lesson planner (), rendered to HTML with react-dom/server.
// The middle column is always this week; the arrows only change the left column.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import { PlannerBoard } from './components/LessonPlanner.jsx';
import { initialNav, navReducer } from './plannerWeeks.js';

const THIS = '2026-10-05';
const goals = [{ goal_id: 'g1', category: 'Behavioral', description: 'calming strategy' },
  { goal_id: 'g2', category: 'Academic', description: 'regrouping' }];
const student = (over = {}) => ({
  student_id: 'stu_101', name: 'Marcus A.', iep_goals: goals, can_edit: true,
  sessions: [{ recording_id: 'rec_x', date: '2026-10-05', approved: true }], transcripts_available: 1,
  summary: null, note: null,
  suggestion: { summary: '1 approved session this week.', iep_check: [
    { goal_id: 'g1', status: 'evidence_seen', note: 'Demonstrated in 1 of 1 approved session.' },
    { goal_id: 'g2', status: 'not_enough', note: 'Not observed in 1 approved session.' }] },
  ...over,
});
const week = (w, over = {}) => ({
  slot: 'x', week: w, plan: { goals: [`goal for ${w}`], objectives: [`objective for ${w}`], plan: `plan for ${w}`, status: 'draft' },
  students: [student()], ...over,
});
const data = (pastWeek, over = {}) => ({
  this_week: THIS, can_edit_plan: true,
  weeks: [week(pastWeek), week(THIS), week('2026-10-12')], ...over,
});
const render = (el) => renderToStaticMarkup(el);
const column = (html, slot) => {
  const start = html.indexOf(`data-testid="week-${slot}"`);
  const next = ['past', 'current', 'next'].map((s) => html.indexOf(`data-testid="week-${s}"`)).filter((i) => i > start);
  return html.slice(start, next.length ? Math.min(...next) : undefined);
};
const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('lesson planner board', () => {
  const nav0 = initialNav(THIS, '2025-10-06');

  test('last week | this week (anchored) | next week', () => {
    const html = render(<PlannerBoard data={data('2026-09-28')} nav={nav0} />);
    expect([...html.matchAll(/data-week="([^"]+)"/g)].map((m) => m[1])).toEqual(['2026-09-28', THIS, '2026-10-12']);
    expect(text(column(html, 'current'))).toMatch(/This week Anchored/);
    expect(column(html, 'past')).toMatch(/aria-label="Earlier week"/);
    expect(column(html, 'current')).not.toMatch(/pl-arrow/);
    expect(column(html, 'next')).not.toMatch(/pl-arrow/);
  });

  test('arrows change only the left column', () => {
    const before = render(<PlannerBoard data={data('2026-09-28')} nav={nav0} />);
    const nav2 = navReducer(navReducer(nav0, { type: 'back' }), { type: 'back' });
    const after = render(<PlannerBoard data={data('2026-09-14')} nav={nav2} />);
    expect(column(after, 'past')).toMatch(/data-week="2026-09-14"/);
    expect(text(column(after, 'past'))).toMatch(/3 weeks ago/);
    expect(column(after, 'current')).toBe(column(before, 'current'));
    expect(column(after, 'next')).toBe(column(before, 'next'));
  });

  test('each week: plan summary first (goals, objectives as bullets), then the plan, then students', () => {
    const t = text(column(render(<PlannerBoard data={data('2026-09-28')} nav={nav0} />), 'current'));
    const order = ['Lesson plan summary', 'Goals', `goal for ${THIS}`, 'Objectives', `objective for ${THIS}`,
      'The week’s plan', `plan for ${THIS}`, 'Students', 'Marcus A.', 'Weekly summary &amp; parent report →', 'Teacher note'];
    const idx = order.map((s) => t.indexOf(s));
    expect(idx.every((i) => i >= 0)).toBe(true);
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
    expect(column(render(<PlannerBoard data={data('2026-09-28')} nav={nav0} />), 'current'))
      .toMatch(/<ul data-testid="plan-goals"><li>goal for 2026-10-05<\/li><\/ul>/);
  });

  test('student card: no weekly summary or parent email here; a link to the student page for that week', () => {
    const html = render(<PlannerBoard data={data('2026-09-28')} nav={nav0} />);
    for (const [slot, wk] of [['past', '2026-09-28'], ['current', THIS], ['next', '2026-10-12']]) {
      const col = column(html, slot);
      expect(col).toContain(`href="#students/stu_101/${wk}" data-testid="student-report-link">Weekly summary &amp; parent report →</a>`);
    }
    const t = text(html);
    expect(t).not.toMatch(/Suggested|Not saved until you save it|Draft parent email|Review &amp; save summary|Evidence seen/);
    expect(html).not.toContain('summary-ai-draft');
  });

  test('read-only (RBT): no edit controls, arrows still there', () => {
    const html = render(<PlannerBoard data={data('2026-09-28')} nav={nav0} readOnly />);
    const labels = [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => text(m[1]).trim());
    expect(labels).toEqual(['‹', '›']);
    expect(html).toContain('data-testid="student-report-link"');            // RBTs can still read the student page
  });

  test('a teacher of another classroom sees no plan or student edit links', () => {
    const d = data('2026-09-28', { can_edit_plan: false });
    d.weeks = d.weeks.map((w) => ({ ...w, students: [student({ can_edit: false })] }));
    const html = render(<PlannerBoard data={d} nav={nav0} />);
    expect(html).not.toMatch(/Edit plan|Write plan|summary<\/button>|note<\/button>/);
    expect(html).toContain('data-testid="student-report-link"');
  });

});

describe('AI drafts on the planner', () => {
  const nav0 = initialNav(THIS, '2025-10-06');
  const buttons = (html) => [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)]
    .map((m) => text(m[1]).replace(/&amp;/g, '&').trim());

  test('AI buttons: plan drafts from this week on; no summary AI draft here (it is on the student page)', () => {
    const d = data('2026-09-28', { ai_drafts: 'offline' });
    const html = render(<PlannerBoard data={d} nav={nav0} onDraft={async () => ({})} />);
    expect(buttons(column(html, 'past'))).not.toContain('AI draft goals & objectives');
    expect(buttons(column(html, 'current'))).toContain('AI draft goals & objectives');
    expect(buttons(column(html, 'next'))).toContain('AI draft goals & objectives');
    for (const s of ['past', 'current', 'next']) expect(buttons(column(html, s))).not.toContain('AI draft');
  });

  test('no AI buttons when drafts are off, for read-only roles, or for other teachers', () => {
    const on = data('2026-09-28', { ai_drafts: 'offline' });
    const off = data('2026-09-28', { ai_drafts: 'off' });
    const other = data('2026-09-28', { ai_drafts: 'live', can_edit_plan: false });
    other.weeks = other.weeks.map((w) => ({ ...w, students: [student({ can_edit: false })] }));
    for (const html of [render(<PlannerBoard data={off} nav={nav0} onDraft={async () => ({})} />),
      render(<PlannerBoard data={on} nav={nav0} readOnly onDraft={async () => ({})} />),
      render(<PlannerBoard data={other} nav={nav0} onDraft={async () => ({})} />)]) {
      expect(html).not.toMatch(/AI draft/);
    }
  });

  test('teacher note versions are shown; a saved summary stays off the planner', () => {
    const d = data('2026-09-28');
    d.weeks[0].students = [student({
      summary: { summary: 'Saved-SUMMARY.', iep_check: [], status: 'approved', parent_email: 'PARENT-EMAIL' },
      summary_versions: 2, note: { note: 'n' }, note_versions: 3,
    })];
    const t = text(column(render(<PlannerBoard data={d} nav={nav0} />), 'past'));
    expect(t).toMatch(/Teacher note 3 versions/);
    expect(t).not.toMatch(/Saved-SUMMARY|PARENT-EMAIL/);
  });

  test('draftSources says what the draft was built from', async () => {
    const { draftSources } = await import('./components/LessonPlanner.jsx');
    expect(draftSources({ based_on: { approved_sessions: 2, teacher_notes: 1 } }))
      .toBe('2 approved session notes and 1 teacher note, checked against the IEP goals');
    expect(draftSources({ based_on_week: '2026-10-05', based_on: { summaries: 1, previous_plan: true, teacher_notes: 0 } }))
      .toBe('the week of Oct 5 – 9: 1 weekly summary, the plan, plus the IEP goals');
  });
});

describe('save errors render', () => {
  test('quote refusal and AI errors show plain text (and the matching words)', async () => {
    const { SaveError } = await import('./components/LessonPlanner.jsx');
    const e = Object.assign(new Error('QUOTES_TRANSCRIPT'), { code: 'QUOTES_TRANSCRIPT', match: 'a b c d e f g h' });
    expect(text(render(<SaveError error={e} />))).toMatch(/8 or more words in a row .* Matching words: a b c d e f g h/);
    const d = Object.assign(new Error('x'), { code: 'AI_DRAFT_QUOTES_TRANSCRIPT' });
    expect(text(render(<SaveError error={d} />))).toMatch(/AI draft repeated words from a transcript, so it was thrown away/);
    expect(text(render(<SaveError error={new Error('WEIRD')} />))).toMatch(/Couldn’t save \(WEIRD\)/);
  });
});
