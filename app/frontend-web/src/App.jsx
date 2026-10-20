import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  approvePipelineSession,
  approveSession,
  changePipelineClassroom,
  getDays,
  getPipelineSessions,
  loadPipelineSessions,
  getSession,
  getSessions,
  getStudent,
  getTeacher,
  resetOverrides,
  updateSessionAnalysis,
} from './dataStore.js';
import MyProfile from './components/MyProfile.jsx';
import SessionModal from './components/SessionModal.jsx';
import PipelineSessions from './components/PipelineSessions.jsx';
import LessonPlanner from './components/LessonPlanner.jsx';
import Students from './components/Students.jsx';
import Teachers from './components/Teachers.jsx';
import Classes from './components/Classes.jsx';
import Domains from './components/Domains.jsx';
import Settings from './components/Settings.jsx';
import AdminJournal from './components/AdminJournal.jsx';
import SchoolBrand from './components/SchoolBrand.jsx';
import { isAdminUser } from './adminApi.js';
import { DEFAULT_BRANDING, loadBranding, loadLogoUrl, resetBranding } from './branding.js';
import { TABS, hashFor, parseHash, tabsFor } from './routes.js';

export const RECORDINGS_NOTE = 'Below are the most recent recordings, sorted by classroom. Open a recording to assign '
  + 'students and process any incidents. Once students are assigned, the session details can be used in the weekly '
  + 'class planner for sending parent summary emails, assessing IEP performance and managing lesson plans.';

// One line under each tab's title.
export const TAB_NOTES = {
  recordings: RECORDINGS_NOTE,
  students: 'Every student in the school. Open a student for their IEP, the week’s daily session summaries, '
    + 'and the weekly summary and draft parent report.',
  classes: 'Each class: its teachers, students, classroom, schedule and educational domain.',
  planner: 'Weekly lesson plans by class: last week, this week and next week.',
  domains: 'Each subject’s core teaching methodology, key programs and recommended reference book.',
  teachers: 'Teachers, RBTs and other staff, their classrooms and roles.',
  activity: 'Who did what: sign-ins, views, edits, approvals and downloads.',
  settings: 'How this site is set up. Values are shown read-only unless they can be changed here.',
};

export default function App({ user = null, onSignOut = null }) {
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const teacher = getTeacher();
  const days = getDays();
  // Built-in sample sessions (fictional Marcus A., Sophia L.): browser-only fixtures, never sent
  // to the server, so never in reports or the Journal. Laptop demo only: the hosted site (signed in)
  // shows the school's own roster and recordings (Sample Elementary School, Oct 10).
  const samples = useMemo(() => getSessions().map((s) => ({
    ...s, is_sample: true,
    timestamp_start: s.timestamp_start || (days.find((d) => d.key === s.day_key) || {}).date || '',
  })), [tick, days]);
  const [pipelineState, setPipelineState] = useState({ ok: false, count: 0, loading: true });
  const pipeline = useMemo(() => getPipelineSessions(), [tick, pipelineState]);

  const reloadPipeline = useCallback(async () => {
    setPipelineState((s) => ({ ...s, loading: true }));
    const r = await loadPipelineSessions();
    setPipelineState({ ...r, loading: false });
  }, []);

  useEffect(() => {
    reloadPipeline();
  }, [reloadPipeline]);

  const [activeId, setActiveId] = useState(null);
  const admin = isAdminUser(user);
  const tabs = tabsFor(admin);
  const [route, setRoute] = useState(() => parseHash(typeof window !== 'undefined' ? window.location.hash : ''));
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  function navigate(r) {
    setRoute({ studentId: null, week: null, ...r });
    if (typeof window !== 'undefined') window.history.pushState(null, '', hashFor(r));
  }
  // '#recordings/<id>' (materials email link): open that session once the sessions are loaded
  useEffect(() => {
    if (route.sessionId && !pipelineState.loading && getSession(route.sessionId)) setActiveId(route.sessionId);
  }, [route.sessionId, pipelineState.loading]); // eslint-disable-line react-hooks/exhaustive-deps
  // admin-only tabs: anyone else lands on Recordings
  const tab = tabs.some((t) => t.key === route.tab) ? route.tab : 'recordings';
  const title = TABS.find((t) => t.key === tab).title;
  const liveSession = useMemo(
    () => (activeId ? getSession(activeId) : null),
    [activeId, tick]
  );

  function handleSave(recordingId, analysis) {
    updateSessionAnalysis(recordingId, analysis);
    refresh();
  }

  async function handleApprove(recordingId, analysis) {
    const target = getSession(recordingId);
    if (target && target.source === 'pipeline') {
      // Server-side approval: records who approved and when (needed for the PDF).
      // Laptop demo: fixture teacher id. Hosted: the server uses the signed-in user.
      await approvePipelineSession(recordingId, analysis, user ? null : teacher.teacher_id);
      await reloadPipeline();
    } else {
      approveSession(recordingId, analysis);
    }
    refresh();
  }

  async function handleChangeClassroom(recordingId, classCode) {
    await changePipelineClassroom(recordingId, classCode, user ? null : teacher.teacher_id);
    await reloadPipeline();
    refresh();
  }

  // School name + logo in the top bar (Oct 9): everyone sees them, admins change them in Settings.
  const [branding, setBranding] = useState(DEFAULT_BRANDING);
  const [logoUrl, setLogoUrl] = useState(null);
  const brandingReviewer = user ? null : teacher.teacher_id;
  useEffect(() => {
    let live = true;
    loadBranding(brandingReviewer).then((b) => { if (live) setBranding(b); });
    return () => { live = false; };
  }, [brandingReviewer]);
  const logoKey = branding.logo ? branding.logo.url : null;
  useEffect(() => {
    let live = true;
    let made = null;
    if (!logoKey) { setLogoUrl(null); return undefined; }
    loadLogoUrl(branding, brandingReviewer).then((u) => {
      made = u;
      if (live) setLogoUrl(u); else if (u) URL.revokeObjectURL(u);
    });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [logoKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const readOnlyUser = !!(user && user.read_only);
  // Every classroom dropdown opens on the signed-in teacher's default classroom;
  // admins (and the laptop demo) open on All. Everyone can pick any classroom or All.
  const defaultClasses = user && user.default_classroom ? [user.default_classroom] : [];
  const [profileOpen, setProfileOpen] = useState(false);
  const [myName, setMyName] = useState(null);       // null: not changed here; the sign-in's display_name
  const roleLabel = user ? { admin: ' · demo admin', rbt: ' · RBT (read-only)' }[user.role] || '' : '';

  async function handleReset() {
    resetOverrides();
    refresh();
    if (admin) {
      try {
        setBranding(await resetBranding(brandingReviewer));
      } catch {
        /* the button stays; the Settings tab shows the current name */
      }
    }
  }

  return (
    <div className="app">
      {/* Site header: the same box on every tab. Fixed heights, nothing per-tab in
          it, tabs on one line, admin actions at the right end of the tab row. Per-tab title, note
          and filters live in the content area below. */}
      <header className="site-header" data-testid="site-header">
        <div className="site-bar">
          <SchoolBrand branding={branding} logoUrl={logoUrl} />
          {user ? (
            <div className="site-user" data-testid="signed-in-as">
              <span className="label">Signed in{roleLabel}</span>
              {/* the name opens My profile */}
              <button type="button" className="site-user-name" data-testid="open-profile" title={`My profile (${user.email})`}
                onClick={() => setProfileOpen(true)}>
                <strong>{(myName !== null ? myName : user.display_name) || user.email}</strong>
              </button>
              <button type="button" className="link" onClick={onSignOut}>Sign out</button>
            </div>
          ) : (
            <div className="site-user">
              <span className="label">Teacher</span>
              <strong>{teacher.full_name}</strong>
            </div>
          )}
        </div>

        <div className="tab-row">
          <nav className="tabs" role="tablist" aria-label="Screens">
            {tabs.map((t) => (
              <button key={t.key} type="button" role="tab" className="tab" aria-selected={tab === t.key}
                data-testid={t.key === 'recordings' ? 'tab-review' : `tab-${t.key}`}
                onClick={() => navigate({ tab: t.key })}>{t.label}</button>
            ))}
          </nav>
        </div>
      </header>

      {profileOpen && user ? (
        <MyProfile onClose={() => setProfileOpen(false)}
          onSaved={(p) => { setMyName(p.display_name || ''); if ((p.changed || []).includes('default_classroom')) refresh(); }} />
      ) : null}

      <main className="tab-content" data-testid="tab-content">
      <div className="page-head">
        <h1>{title}</h1>
        <p className="page-note" data-testid={tab === 'recordings' ? 'recordings-note' : 'page-note'}>{TAB_NOTES[tab]}</p>
      </div>

      {tab === 'recordings' ? (
        <PipelineSessions
          sessions={pipeline}
          samples={user ? [] : samples}
          state={pipelineState}
          getStudent={getStudent}
          onOpen={(id) => setActiveId(id)}
          onReload={reloadPipeline}
          homeClassrooms={defaultClasses}
        />
      ) : null}
      {tab === 'students' ? (
        <Students route={route} onNavigate={navigate} reviewerId={user ? null : teacher.teacher_id}
          readOnly={readOnlyUser} onOpenRecording={(id) => setActiveId(id)} showSamples={!user} />
      ) : null}
      {tab === 'planner' ? <LessonPlanner reviewerId={user ? null : teacher.teacher_id} readOnly={readOnlyUser} /> : null}
      {tab === 'classes' ? <Classes reviewerId={user ? null : teacher.teacher_id} /> : null}
      {tab === 'domains' ? <Domains reviewerId={user ? null : teacher.teacher_id} /> : null}
      {tab === 'teachers' ? <Teachers reviewerId={user ? null : teacher.teacher_id} /> : null}
      {tab === 'activity' ? <AdminJournal inline /> : null}
      {tab === 'settings' ? (
        <Settings onReset={handleReset} branding={branding} logoUrl={logoUrl} canEdit={admin}
          onBrandingChange={setBranding} />
      ) : null}
      </main>

      {liveSession && (
        <SessionModal
          session={liveSession}
          student={getStudent(liveSession.student_id)}
          onClose={() => setActiveId(null)}
          onSave={handleSave}
          onApprove={handleApprove}
          onChangeClassroom={handleChangeClassroom}
          reviewer={user ? null : teacher}
          readOnlyUser={readOnlyUser}
          classrooms={(user && user.classrooms) || []}
          dayDate={(days.find((d) => d.key === liveSession.day_key) || {}).date || null}
        />
      )}

    </div>
  );
}
