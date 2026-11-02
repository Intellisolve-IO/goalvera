// Tabs (David, Oct 8 2026; tasks#85): the same header on every tab; Recordings, Students, Classes,
// Lesson planner, Domains, Teachers (Oct 10: Classes + Domains, tasks#103), then Activity Log and Settings for admins only (Settings last); the Students
// page (IEP, one school week Mon–Fri, then "Generate draft weekly parent report"); samples labelled
// Sample; every classroom list opens on the teacher's default classroom, admins on All.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test } from 'vitest';
import App, { RECORDINGS_NOTE, TAB_NOTES } from './App.jsx';
import { StudentList, StudentPage } from './components/Students.jsx';
import { TeacherTable } from './components/Teachers.jsx';
import { SettingsView } from './components/Settings.jsx';
import AdminJournal from './components/AdminJournal.jsx';
import { TABS, hashFor, parseHash, studentWeekHref, tabsFor } from './routes.js';
import { isSampleId, sampleDetail, sampleDraft, sampleStudents } from './samples.js';

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const admin = { email: 'a@x.org', approved: true, role: 'admin', read_only: false };
const teacher = { email: 't@x.org', approved: true, role: 'teacher', read_only: false, default_classroom: 'RM12' };
const tabLabels = (html) => [...html.matchAll(/role="tab"[^>]*>([^<]+)</g)].map((m) => m[1]);

describe('tabs and the shared header', () => {
  test('tab order; Activity Log and Settings for admins only, Settings last', () => {
    // Classes next to Students, Domains next to the Lesson planner (Oct 10; tasks#103)
    const recording = { canRecord: true };
    expect(tabsFor(true, recording).map((t) => t.label)).toEqual(['Record', 'Recordings', 'Students', 'Classes', 'Lesson planner', 'Domains',
      'Teachers', 'Activity Log', 'Settings']);
    expect(tabsFor(false, recording).map((t) => t.label)).toEqual(['Record', 'Recordings', 'Students', 'Classes', 'Lesson planner', 'Domains', 'Teachers']);
    expect(tabsFor(true).map((t) => t.label)).not.toContain('Record');
    expect(tabsFor(false).map((t) => t.label)).not.toContain('Record');
    expect(tabLabels(renderToStaticMarkup(<App user={admin} />))).toEqual(tabsFor(true, recording).map((t) => t.label));
    expect(tabLabels(renderToStaticMarkup(<App user={teacher} />))).toEqual(tabsFor(false, recording).map((t) => t.label));
    expect(tabLabels(renderToStaticMarkup(<App user={{ ...teacher, role: 'reviewer' }} />))).not.toContain('Record');
    expect(tabLabels(renderToStaticMarkup(<App user={{ ...teacher, role: 'rbt', read_only: true }} />))).not.toContain('Settings');
    expect(tabLabels(renderToStaticMarkup(<App user={{ ...teacher, role: 'rbt', read_only: true }} />))).not.toContain('Record');
  });

  test('no Download button at the top right any more; the signed-in name opens My profile (Oct 10)', () => {
    for (const user of [admin, teacher, { ...teacher, role: 'rbt', read_only: true }]) {
      const html = renderToStaticMarkup(<App user={user} />);
      const head = html.slice(html.indexOf('data-testid="site-header"'), html.indexOf('</header>'));
      expect(head).not.toContain('open-report');
      expect(head).not.toMatch(/Download report|admin-strip|open-journal|Reset demo state|Value metrics/);
      expect(head).toMatch(/data-testid="open-profile"[^>]*>[\s\S]*?<strong>[^<]+<\/strong>/);
    }
    // the display name from My profile shows when there is one
    expect(renderToStaticMarkup(<App user={{ ...teacher, display_name: 'Sarah J.' }} />)).toContain('<strong>Sarah J.</strong>');
  });

  test('the site header never changes between tabs: nothing per-tab above the tab row (Oct 8)', () => {
    const header = (h) => h.slice(h.indexOf('data-testid="site-header"'), h.indexOf('</header>'));
    for (const user of [admin, teacher]) {
      const heads = tabsFor(user === admin).map((t) => {
        globalThis.window = { location: { hash: `#${t.key}` } };   // the tab comes from the hash
        let h;
        try { h = renderToStaticMarkup(<App user={user} />); } finally { delete globalThis.window; }
        expect(h).toContain(`<h1>${t.title}</h1>`);
        // the title + note are in the content area, after the header
        expect(h.indexOf('<h1>')).toBeGreaterThan(h.indexOf('</header>'));
        expect(header(h)).not.toMatch(/<h1|page-note|filter/);
        return header(h).replace(/ aria-selected="(true|false)"/g, '');
      });
      expect(new Set(heads).size).toBe(1);
    }
  });

  test('routes: one hash per tab and per student week; unknown -> Recordings', () => {
    expect(parseHash('#students/stu_101/2026-10-05')).toEqual({ tab: 'students', studentId: 'stu_101', week: '2026-10-05', sessionId: null });
    expect(parseHash('#record').tab).toBe('record');
    expect(parseHash('#planner').tab).toBe('planner');
    expect(parseHash('#nope').tab).toBe('recordings');
    expect(parseHash('').tab).toBe('recordings');
    expect(hashFor({ tab: 'settings' })).toBe('#settings');
    expect(studentWeekHref('stu_101', '2026-10-05')).toBe('#students/stu_101/2026-10-05');
    for (const t of TABS) expect(TAB_NOTES[t.key]).toBeTruthy();
    expect(TAB_NOTES.recordings).toBe(RECORDINGS_NOTE);
  });
});

const list = {
  students: [
    { student_id: 'stu_101', name: 'Marcus A.', class_codes: ['RM12'], teacher_names: ['Sarah Jenkins'], iep_goals: 3, mine: true },
    { student_id: 'stu_201', name: 'Jamal B.', class_codes: ['Art 9Z'], teacher_names: ['Jordan Rivera'], iep_goals: 1, mine: false },
  ],
  home_classrooms: ['RM12'], default_classroom: 'RM12',
};
const rows = (html) => [...html.matchAll(/data-testid="student-row"[^>]*>([\s\S]*?)<\/tr>/g)].map((m) => text(m[1]).trim());

describe('Students tab', () => {
  test('opens on the teacher’s default classroom; samples listed and labelled Sample', () => {
    const html = renderToStaticMarkup(<StudentList data={list} samples={sampleStudents()} onOpen={() => {}} />);
    const r = rows(html);
    expect(r[0]).toMatch(/^Marcus A\. — RM12/);
    expect(r.some((x) => x.startsWith('Jamal'))).toBe(false);                // filtered to RM12 to start
    expect(r.filter((x) => /Sample/.test(x)).map((x) => x.split(' Sample')[0])).toEqual(['Marcus A.', 'Sophia L.']);
    expect(text(html)).toContain('Showing 1 of 2 students');
  });

  test('sample-school rows: grade, plan and the Fictional sample data note (Ops, Oct 10)', () => {
    const fict = { students: [{ student_id: 'stu_0001', name: 'Doc D.', class_codes: ['Room 104'], teacher_names: ['Alex Morgan'],
      iep_goals: 5, mine: true, grade: '5', plan_type: 'IEP', label: 'Fictional sample data' }],
    home_classrooms: ['Room 104'], default_classroom: 'Room 104' };
    const html = renderToStaticMarkup(<StudentList data={fict} samples={[]} onOpen={() => {}} />);
    expect(rows(html)[0]).toMatch(/^Doc D\. 5 Room 104 Alex Morgan IEP 5/);
    expect(html).toContain('data-testid="students-fictional-note"');
    expect(text(html)).toContain('Fictional sample data');
  });

  test('a sample-school student page: Fictional sample data label on the profile and the IEP, lists one per line', () => {
    const d = {
      student_id: 'stu_0001', name: 'Doc D.', first_name: 'Doc', grade: '5', label: 'Fictional sample data',
      class_codes: ['Room 104'], teacher_names: ['Alex Morgan'], iep_hidden: false,
      iep: { goals: [{ goal_id: 'g1', category: 'Academic', description: 'Answers inferential questions.' }],
        plan_type: 'IEP', eligibility: ['Autism', 'Speech-Language Impairment'],
        services: ['Speech-language therapy: 30 min x 2/week (Speech room)', 'Specialized instruction: 1500 min x 1/week'],
        case_manager: 'Alex Morgan' },
      week: '2026-10-05', week_end: '2026-10-09', this_week: '2026-10-05', prev_week: null, next_week: null,
      days: [0, 1, 2, 3, 4].map((i) => ({ date: `2026-10-0${5 + i}`, sessions: [] })),
      summary: null, summary_versions: 0, suggestion: null, parent_email_hidden: false, can_edit: false,
      can_draft: false, ai_drafts: 'off',
      attendance: { school_days: 49, present: 47, absent_excused: 0, absent_unexcused: 2, tardy: 3, rate: 95.9,
        from: '2026-08-03', through: '2026-10-09' },
    };
    const html = renderToStaticMarkup(<StudentPage d={d} onWeek={() => {}} onBack={() => {}} />);
    expect((html.match(/data-testid="fictional-label"/g) || []).length).toBe(2);       // header + IEP
    expect(text(html)).toContain('Grade 5 · Room 104 · Alex Morgan');
    expect(text(html)).toContain('Eligibility: Autism; Speech-Language Impairment');
    expect(text(html)).toContain('Attendance 8/3–10/9: 95.9% · 47 of 49 days present · 0 absent excused · 2 absent unexcused · 3 tardy');
    expect((html.match(/<li>/g) || []).length).toBeGreaterThanOrEqual(2);               // services, one per line
  });

  test('admins (no default classroom) open on All: every student', () => {
    const html = renderToStaticMarkup(<StudentList data={{ ...list, default_classroom: null, home_classrooms: [] }}
      samples={[]} onOpen={() => {}} />);
    expect(rows(html).map((x) => x.split(' ')[0])).toEqual(['Marcus', 'Jamal']);
  });

  const detail = {
    student_id: 'stu_101', name: 'Marcus A.', first_name: 'Marcus', class_codes: ['RM12'], teacher_names: ['Sarah Jenkins'],
    iep: { goals: [{ goal_id: 'g1', category: 'Behavioral', description: 'Calming strategy' }], accommodations: ['Headphones'] },
    iep_hidden: false, week: '2026-10-05', week_end: '2026-10-09', this_week: '2026-10-05',
    prev_week: '2026-09-28', next_week: '2026-10-12',
    days: ['05', '06', '07', '08', '09'].map((d) => ({ date: `2026-10-${d}`, sessions: d === '06' ? [{
      recording_id: 'rec_x', approved: true, class_code: 'RM12', timestamp_start: '2026-10-06T09:20:00',
      summary: 'Calm morning, goal shown.', incidents: 1 }] : [] })),
    summary: { summary: 'A calm week.', iep_check: [], status: 'draft', parent_email: 'Dear family, PARENT-REPORT' },
    summary_versions: 1, suggestion: null, parent_email_hidden: false, can_edit: true, can_draft: true, ai_drafts: 'live',
  };

  test('student page: IEP, then Mon–Fri daily summaries, then the weekly summary + report, button last', () => {
    const html = renderToStaticMarkup(<StudentPage d={detail} onWeek={() => {}} onBack={() => {}} />);
    const t = text(html);
    const order = ['IEP', 'Calming strategy', 'Accommodations: Headphones', 'Week of Oct 5 – 9', 'Monday', 'Tuesday',
      'Calm morning, goal shown.', 'Wednesday', 'Thursday', 'Friday', 'Weekly summary & parent report', 'A calm week.',
      'PARENT-REPORT', 'Generate draft weekly parent report'];
    const idx = order.map((s) => t.indexOf(s));
    expect(idx.every((i) => i >= 0)).toBe(true);
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
    expect(html.match(/data-testid="student-day"/g)).toHaveLength(5);
    expect(html).toMatch(/<button[^>]*data-testid="generate-parent-report"[^>]*>/);
    expect(html.match(/<button[^>]*data-testid="generate-parent-report"[^>]*>/)[0]).not.toContain('disabled');
    expect(t).toContain('“the student”');
    expect(t).toMatch(/Nothing is sent/);
  });

  test('another teacher: reads the IEP, no parent report, no Generate', () => {
    const other = { ...detail, can_edit: false, can_draft: false, parent_email_hidden: true,
      summary: { summary: 'A calm week.', iep_check: [], status: 'draft' } };
    const html = renderToStaticMarkup(<StudentPage d={other} onWeek={() => {}} onBack={() => {}} />);
    expect(html).toContain('Calming strategy');
    expect(html).not.toContain('PARENT-REPORT');
    expect(html.match(/<button[^>]*data-testid="generate-parent-report"[^>]*>/)[0]).toContain('disabled');
  });

  test('IEP hidden for the role says so', () => {
    const html = renderToStaticMarkup(<StudentPage d={{ ...detail, iep: null, iep_hidden: true }} onWeek={() => {}} onBack={() => {}} />);
    expect(html).toContain('data-testid="iep-hidden"');
    expect(html).not.toContain('Calming strategy');
  });

  test('sample students: fixture IEP, sample week, a local draft with no AI and nothing sent', () => {
    const [m] = sampleStudents();
    expect(isSampleId(m.student_id)).toBe(true);
    const d = sampleDetail(m.student_id);
    expect(d.sample).toBe(true);
    expect(d.days.map((x) => x.date)).toEqual(['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25']);
    expect(d.days[0].sessions[0].summary).toMatch(/Steady morning/);
    const html = renderToStaticMarkup(<StudentPage d={d} onWeek={() => {}} onBack={() => {}} />);
    expect(html).toContain('data-testid="sample-badge"');
    expect(text(html)).toMatch(/Sample: written in your browser, no AI/);
    const draft = sampleDraft(d);
    expect(draft.ai).toBe(null);
    expect(draft.parent_email).toMatch(/^Dear family/);
    expect(sampleDetail(m.student_id, '2026-09-28').days.every((x) => !x.sessions.length)).toBe(true);
  });
});

describe('Teachers, Settings, Activity Log', () => {
  const teachers = { emails_shown: false, teachers: [
    { id: 'usr_demo_sarah', name: 'Sarah Jenkins', role: 'Teacher', classrooms: ['Math 2B', 'RM12'], default_classroom: 'RM12' },
    { id: 'rbt_demo_rm12', name: 'Demo RBT', role: 'RBT (read-only)', classrooms: ['RM12'], default_classroom: null }] };

  test('teachers: name, classrooms, default classroom, role; emails only when the server sends them', () => {
    const html = renderToStaticMarkup(<TeacherTable data={teachers} />);
    expect(text(html)).toMatch(/Sarah Jenkins Math 2B, RM12 RM12 Teacher/);
    expect(html).not.toContain('<th>Email</th>');
    const adminView = renderToStaticMarkup(<TeacherTable data={{ emails_shown: true, teachers: [{ ...teachers.teachers[0], emails: ['s@x.org'] }] }} />);
    expect(adminView).toContain('<th>Email</th>');
    expect(adminView).toContain('s@x.org');
  });

  const settings = {
    iep_access_rbt: 'read', access: { allowlist_roles: { admin: 1, teacher: 2 } },
    retention: { audio_days: 7, transcripts_and_drafts_days: 30, logs_days: 7, planner: 'kept to pilot end' },
    roster: { classrooms: 4, students: 1, teachers: 1, rbts: 1, synthetic: true },
    classrooms: [{ class_code: 'RM12', teacher_name: 'Sarah Jenkins', has_student: true, iep_goals: 3 }],
    teacher_defaults: [{ teacher_name: 'Sarah Jenkins', default_classroom: 'RM12', set_in_roster: false }],
    ai: { planner_ai: 'live', model: 'gemini-3.8-flash', location: 'us' },
    journal: { mode: 'bigquery', dataset: 'goalvera_journal', location: 'us-east1' },
    bug_reports: { repo: 'your-org/your-issues-repo' }, school_timezone: 'America/New_York',
  };

  test('settings: every option shown read-only with where it is set; demo reset is the one action', () => {
    const html = renderToStaticMarkup(<SettingsView s={settings} onReset={() => {}} />);
    const t = text(html);
    for (const s of ['IEP access for RBTs', 'Read', 'Audio', '7 days', '30 days', 'RM12', 'gemini-3.8-flash',
      'goalvera_journal', 'your-org/your-issues-repo', 'America/New_York', 'Default classroom', 'Sarah Jenkins']) {
      expect(t).toContain(s);
    }
    expect(html).toContain('data-testid="reset-demo"');
    expect((t.match(/Read-only ·/g) || []).length).toBeGreaterThan(8);
    expect(html).not.toMatch(/<(input|select|textarea)/);
  });

  test('Activity Log: the journal on its own tab (no pop-up, no Close)', () => {
    const html = renderToStaticMarkup(<AdminJournal inline initial={{ loading: false, entries: [], more: false, error: null }} />);
    expect(html).toContain('data-testid="journal-panel"');
    expect(html).not.toMatch(/modal-backdrop|>Close</);
    expect(html).toContain('journal-from');
  });
});
