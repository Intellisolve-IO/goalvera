import React, { useEffect, useState } from 'react';
import TranscriptPanel from './TranscriptPanel.jsx';
import { downloadApprovedPdf, fetchAudit, fetchIncidentNames, requestIncidentPrint, submitBugReport } from '../dataStore.js';
import { apiErrorText } from '../labels.js';
import TextBox from './TextBox.jsx';
import { sessionTitle } from '../sessionTitle.js';
import { MAX_INCIDENT_NAME, incidentTitle, numberIncidents, timeLine } from '../incidents.js';
import IncidentPrint from './IncidentPrint.jsx';
import BugMarkerForm from './BugMarkerForm.jsx';
import MaterialsPanel from './MaterialsPanel.jsx';
import MediaPanel from './MediaPanel.jsx';
import SessionClass from './SessionClass.jsx';
import SessionStudents from './SessionStudents.jsx';
import { bugItemText, sessionMarkers } from '../bugMarkers.js';
import RecordingPauses from './RecordingPauses.jsx';

export default function SessionModal({
  session,
  student,
  onClose,
  onSave,
  onApprove,
  onChangeClassroom = null,
  reviewer,
  readOnlyUser = false,
  classrooms = [],
  dayDate = null,
}) {
  const analysis = session.processed_analysis || {
    goal_evaluations: [],
    incident_analysis: [],
    general_session_summary: '',
  };

  const [goals, setGoals] = useState(analysis.goal_evaluations);
  const [incidents, setIncidents] = useState(analysis.incident_analysis);
  const [summary, setSummary] = useState(analysis.general_session_summary);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [downloads, setDownloads] = useState(null);
  const [moveTo, setMoveTo] = useState('');
  const [printing, setPrinting] = useState(null);       // the server's report for the print view
  const [bugs, setBugs] = useState(session.bug_reports || []);   // this viewer's Bug markers
  const [classTick, setClassTick] = useState(0);                 // class picked: materials reload its domain
  useEffect(() => setBugs(session.bug_reports || []), [session.recording_id, session.bug_reports]);

  useEffect(() => {
    const a = session.processed_analysis || {
      goal_evaluations: [],
      incident_analysis: [],
      general_session_summary: '',
    };
    setGoals(a.goal_evaluations || []);
    setIncidents(a.incident_analysis || []);
    setSummary(a.general_session_summary || '');
  }, [session.recording_id, session.status, session.processed_analysis]);

  // First view: incidents without a name get one from the server (AI-written, scrubbed,
  // observed behavior only). Only fills names that are still empty; never a teacher's edit.
  useEffect(() => {
    if (session.source !== 'pipeline' || !(session.processed_analysis?.incident_analysis || []).some((x) => !x.name)) return undefined;
    let live = true;
    fetchIncidentNames(session.recording_id).then((rows) => {
      if (!live) return;
      setIncidents((cur) => cur.map((x, i) => {
        const r = rows.find((row) => row.index === i && row.timestamp_seconds === x.timestamp_seconds);
        return !x.name && r && r.name ? { ...x, name: r.name, name_source: r.name_source || 'ai' } : x;
      }));
    }).catch(() => {});
    return () => { live = false; };
  }, [session.recording_id, session.source, session.processed_analysis]);

  function currentPayload() {
    return {
      goal_evaluations: goals,
      incident_analysis: incidents,
      general_session_summary: summary,
    };
  }

  function flash(msg) {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }

  function handleSave() {
    onSave(session.recording_id, currentPayload());
    flash('Draft edits saved locally');
  }

  async function handleApprove() {
    setBusy(true);
    try {
      await onApprove(session.recording_id, currentPayload());
      flash('Approved — not sent to anyone');
    } catch (e) {
      flash(`Approve failed: ${apiErrorText(e.message)}`);
    } finally {
      setBusy(false);
    }
  }

  const isApproved = session.status === 'approved';
  const isPipeline = session.source === 'pipeline';
  const canDownloadPdf = isPipeline && isApproved && !!session.approved_at;
  const needsClass = isPipeline && !isApproved && !!session.needs_class_assignment;
  // The server decides and enforces all of this; the UI only mirrors it.
  // Read-only (RBT): reads everything, changes nothing, never sees parent emails.
  const readOnly = readOnlyUser || session.viewer_read_only === true;
  // Not this teacher's session or student: read it, but no edit / approve / export.
  const notMine = isPipeline && session.viewer_may_act === false;
  const locked = isApproved || readOnly || notMine;
  const canMove = isPipeline && !readOnly && !isApproved && session.viewer_may_change_classroom === true
    && typeof onChangeClassroom === 'function';
  const moveChoices = classrooms.filter((c) => needsClass || c !== session.class_code);

  async function handleMove() {
    if (!moveTo) return;
    setBusy(true);
    try {
      await onChangeClassroom(session.recording_id, moveTo);
      flash(`Moved to ${moveTo}`);
      setMoveTo('');
    } catch (e) {
      flash(`Move failed: ${apiErrorText(e.message)}`);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!canDownloadPdf) {
      setDownloads(null);
      return;
    }
    fetchAudit(session.recording_id)
      .then((ev) => setDownloads(ev.filter((e) => e.event === 'pdf_downloaded').length))
      .catch(() => setDownloads(null));
  }, [canDownloadPdf, session.recording_id]);

  async function handleDownloadPdf() {
    setBusy(true);
    try {
      await downloadApprovedPdf(session.recording_id, reviewer ? reviewer.teacher_id : null);
      setDownloads((n) => (n ?? 0) + 1);
      flash('PDF downloaded to this computer — nothing was sent');
    } catch (e) {
      flash(`PDF failed: ${apiErrorText(e.message)}`);
    } finally {
      setBusy(false);
    }
  }

  // Download PDF on one incident: same people as the session PDF. Not approved
  // yet: the server returns the stored draft marked DRAFT, with a watermark.
  const showIncidentPdf = isPipeline && !readOnly && !notMine;
  async function handleIncidentPdf(number) {
    setBusy(true);
    try {
      setPrinting(await requestIncidentPrint(session.recording_id, number, reviewer ? reviewer.teacher_id : null));
    } catch (e) {
      flash(`PDF failed: ${apiErrorText(e.message)}`);
    } finally {
      setBusy(false);
    }
  }
  const numbered = numberIncidents(incidents);
  const [openIncident, setOpenIncident] = useState(1);
  const shownIncident = numbered.some((n) => n.number === openIncident) ? openIncident : 1;
  // Bug markers are never session markers: not in the markers line, the timeline or any incident list.
  const markers = sessionMarkers(session.markers);
  const canReportBug = isPipeline && !readOnly;
  function editIncident(idx, patch) {
    setIncidents((cur) => cur.map((x, i) => (i === idx ? { ...x, ...patch } : x)));
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={printing ? undefined : onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <h2 id="session-modal-title" data-testid="session-title">
              {sessionTitle(session, { dayDate })}
            </h2>
            <p className="muted" data-testid="session-subtitle">
              {student
                ? `${student.first_name} ${student.last_initial}`
                : 'Student'}{' '}
              · {Math.round(session.duration_seconds / 60)} min · status:{' '}
              <span className={`badge status-${session.status}`}>
                {session.status}
              </span>
              {session.approved_at && (
                <>
                  {' '}
                  · approved
                  {session.approved_by_name ? ` by ${session.approved_by_name}` : ''} at{' '}
                  {new Date(session.approved_at).toLocaleString()}
                </>
              )}
            </p>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="modal-body">
          {readOnly && (
            <div className="banner" role="note" data-testid="read-only">
              <strong>Read-only.</strong> You can read this session, but you can’t edit, approve,
              download or move it.
            </div>
          )}
          {needsClass && (
            <div className="banner warn" role="alert">
              <strong>Needs classroom assignment.</strong> Classroom “{session.class_code || 'unknown'}” is
              not in the roster, so this recording has no teacher and can’t be approved yet.
              {canMove ? ' Pick its classroom below.' : ' A teacher or an admin can pick its classroom.'}
            </div>
          )}
          {notMine && !needsClass && !readOnly && (
            <div className="banner warn" role="note" data-testid="not-your-session">
              You can read this session. Only its teacher, the student’s teacher, or an admin can
              edit, approve or download it.
            </div>
          )}
          {isPipeline && (canMove || session.classroom_changed_from) && (
            <section data-testid="classroom-section">
              <h3>Classroom</h3>
              <p className="muted">
                {session.class_code || 'None yet'}
                {session.teacher_name ? ` · ${session.teacher_name}` : ''}
                {session.classroom_changed_from && (
                  <>
                    {' '}· moved from {session.classroom_changed_from}
                    {session.classroom_changed_at ? ` on ${new Date(session.classroom_changed_at).toLocaleString()}` : ''}
                  </>
                )}
              </p>
              {canMove && moveChoices.length > 0 && (
                <div className="card-row classroom-move">
                  <label>
                    Move to classroom
                    <select value={moveTo} onChange={(e) => setMoveTo(e.target.value)} disabled={busy}>
                      <option value="">— choose a classroom —</option>
                      {moveChoices.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className="btn ghost" onClick={handleMove} disabled={busy || !moveTo}
                    data-testid="move-classroom">
                    Move
                  </button>
                </div>
              )}
            </section>
          )}
          {isPipeline && !needsClass && (
            // The session's class (Classes tab), suggested from classroom + time.
            <SessionClass recordingId={session.recording_id} classCode={session.class_code}
              reviewerId={reviewer ? reviewer.teacher_id : null} readOnly={readOnly || notMine}
              onChange={() => setClassTick((t) => t + 1)} />
          )}
          {isPipeline && !needsClass && (
            // Students in this session: the class roster is suggested; one click confirms.
            <SessionStudents key={`students-${classTick}`} recordingId={session.recording_id}
              reviewerId={reviewer ? reviewer.teacher_id : null} readOnly={readOnly} />
          )}
          {isPipeline && markers.some((m) => /^teacher notes?$/i.test(String(m.type || '').trim())) && (
            // Teacher Notes with suggested classroom materials: next to the classroom,
            // where the teacher assigns the session. Nothing is made until the teacher picks.
            <MaterialsPanel key={classTick} recordingId={session.recording_id} reviewerId={reviewer ? reviewer.teacher_id : null} />
          )}
          {isPipeline && (session.media || []).length > 0 && (
            // Teacher Notes photos / videos from the phone (QR on the recorder), with AI photo notes.
            <MediaPanel recordingId={session.recording_id} media={session.media}
              reviewerId={reviewer ? reviewer.teacher_id : null} />
          )}
          <RecordingPauses gaps={session.recording_gaps} />
          {session.source === 'pipeline' && (
            <section>
              <h3>Transcript</h3>
              <p className="hint">
                Draft notes below were generated by {session.model || 'Gemini'} from this
                transcript. Check each cite against it before approving.
              </p>
              <TranscriptPanel recordingId={session.recording_id} student={student} readOnly={locked}
                reviewerId={reviewer ? reviewer.teacher_id : null} choicesKey={classTick}
                markers={markers} durationSeconds={session.duration_seconds}
                belowTimeline={(bugs.length > 0 || canReportBug) ? (
                // No separate markers section: markers show in the timeline. Bug reports
                // and the "Add marker" form (type incl. Bug, time, note) sit just below the timeline.
                <div className="bug-markers" data-testid="bug-markers">
                  {bugs.length > 0 && (
                    <ul className="bug-items" data-testid="bug-reports">
                      {bugs.map((b) => (
                        <li key={b.report_id} className="bug-item" data-testid="bug-item">
                          {bugItemText(b)} ·{' '}
                          {b.issue_url ? (
                            <a href={b.issue_url} target="_blank" rel="noopener noreferrer">
                              {`issue #${b.issue_number}`}
                            </a>
                          ) : (
                            <span className="muted">sent to our team</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {canReportBug && (
                    <details className="add-marker-toggle" data-testid="add-marker-toggle">
                      <summary>+ Add marker</summary>
                    <BugMarkerForm
                      durationSeconds={session.duration_seconds}
                      submit={(payload) => submitBugReport(session.recording_id, payload, reviewer ? reviewer.teacher_id : null)}
                      onAdded={(r) => setBugs((cur) => [...cur, r])}
                    />
                    </details>
                  )}
                </div>
              ) : null} />
            </section>
          )}

          <section>
            <h3>IEP goal evaluations</h3>
            {goals.length === 0 && <p className="muted">No goal evaluations.</p>}
            {goals.map((g, idx) => (
              <div className="card" key={idx}>
                <div className="card-row">
                  <label>
                    Goal ID
                    <TextBox as="input"
                      value={g.goal_id}
                      onValue={(v) => {
                        const next = [...goals];
                        next[idx] = { ...g, goal_id: v };
                        setGoals(next);
                      }}
                      disabled={locked} />
                  </label>
                  <label>
                    Status
                    <select
                      value={g.status}
                      onChange={(e) => {
                        const next = [...goals];
                        next[idx] = { ...g, status: e.target.value };
                        setGoals(next);
                      }}
                      disabled={locked}
                    >
                      <option>Demonstrated</option>
                      <option>Struggled</option>
                      <option>Not Observed</option>
                    </select>
                  </label>
                </div>
                <label>
                  Evidence quote (cite)
                  <TextBox
                    rows={2}
                    value={g.evidence_quote}
                    onValue={(v) => {
                      const next = [...goals];
                      next[idx] = { ...g, evidence_quote: v };
                      setGoals(next);
                    }}
                    disabled={locked} />
                </label>
                <label>
                  Analysis
                  <TextBox
                    rows={3}
                    value={g.analysis}
                    onValue={(v) => {
                      const next = [...goals];
                      next[idx] = { ...g, analysis: v };
                      setGoals(next);
                    }}
                    disabled={locked} />
                </label>
              </div>
            ))}
          </section>

          <section className="incidents" data-testid="incidents">
            <h3>Incident analysis</h3>
            {incidents.length === 0 && (
              <p className="muted">No incident flags for this session.</p>
            )}
            {/* One tab per incident: "Incident 1", "Incident 2", ... with the name
                underneath (full name on hover). A single incident is one tab. */}
            {numbered.length > 0 && (
              <div className="incident-tabs" role="tablist" aria-label="Incidents">
                {numbered.map(({ incident: inc, number }) => (
                  <button key={number} type="button" role="tab" className="incident-tab"
                    aria-selected={number === shownIncident} title={inc.name || `Incident ${number}`}
                    data-testid={`incident-tab-${number}`} onClick={() => setOpenIncident(number)}>
                    <span className="incident-tab-label">Incident {number}</span>
                    <span className="incident-tab-name">{inc.name || '\u00a0'}</span>
                  </button>
                ))}
              </div>
            )}
            {numbered.map(({ incident: inc, index: idx, number }) => (
              <div className="card abc" key={idx} data-testid={`incident-${number}`} role="tabpanel"
                hidden={number !== shownIncident}>
                <p className="abc-when" data-testid="incident-time">
                  {timeLine(session.recording_start, inc.timestamp_seconds)}
                  {!session.recording_start && isPipeline
                    ? <span className="hint"> (no reliable start time for this recording)</span> : null}
                </p>
                <div className="abc-head">
                  <h4 data-testid="incident-label">{incidentTitle(number, inc.name)}</h4>
                </div>
                {!locked && (
                  <label className="abc-name">
                    Incident name <span className="hint">(AI-suggested from about 75 seconds of transcript after the marker, with roster names replaced; rename it if you like. {MAX_INCIDENT_NAME} characters max)</span>
                    <TextBox as="input"
                      value={inc.name || ''}
                      maxLength={MAX_INCIDENT_NAME}
                      onValue={(v) => editIncident(idx, { name: v.slice(0, MAX_INCIDENT_NAME), name_source: 'teacher' })}
                      placeholder={`Incident ${number}`}
                      data-testid={`incident-name-${number}`}
                      disabled={locked} />
                  </label>
                )}
                <label>
                  Antecedent
                  <TextBox
                    rows={2}
                    value={inc.antecedent}
                    onValue={(v) => editIncident(idx, { antecedent: v })}
                    disabled={locked} />
                </label>
                <label>
                  Behavior
                  <TextBox
                    rows={2}
                    value={inc.behavior}
                    onValue={(v) => editIncident(idx, { behavior: v })}
                    disabled={locked} />
                </label>
                <label>
                  Consequence / intervention
                  <TextBox
                    rows={2}
                    value={inc.consequence_intervention}
                    onValue={(v) => editIncident(idx, { consequence_intervention: v })}
                    disabled={locked} />
                </label>
                {showIncidentPdf && (
                  <div className="abc-foot">
                    {!isApproved && <span className="hint">Prints as a draft until the session is approved.</span>}
                    <button
                      type="button"
                      className="btn ghost-line small"
                      onClick={() => handleIncidentPdf(number)}
                      disabled={busy}
                      data-testid={`incident-pdf-${number}`}
                    >
                      Download PDF
                    </button>
                  </div>
                )}
              </div>
            ))}
          </section>

          <section>
            <h3>Session summary</h3>
            <TextBox
              rows={4}
              value={summary}
              onValue={(v) => setSummary(v)}
              disabled={locked} />
          </section>

        </div>

        <footer className="modal-foot">
          <div className="foot-note">
            {toast && <span className="toast">{toast}</span>}
            {isApproved && !isPipeline && (
              <span className="hint">PDF download is available for recordings from the pipeline.</span>
            )}
            {canDownloadPdf && downloads !== null && (
              <span className="hint">PDF downloads logged: {downloads}</span>
            )}
          </div>
          <div className="foot-actions">
            {canDownloadPdf && !notMine && !readOnly && (
              <button
                type="button"
                className="btn primary"
                onClick={handleDownloadPdf}
                disabled={busy}
                data-testid="download-pdf"
              >
                Download PDF
              </button>
            )}
            {!readOnly && !notMine && (
              <button
                type="button"
                className="btn ghost"
                onClick={handleSave}
                disabled={isApproved}
              >
                Save Draft
              </button>
            )}
            {!readOnly && (
              <button
                type="button"
                className={`btn ${isApproved ? 'ghost' : 'primary'}`}
                onClick={handleApprove}
                disabled={isApproved || busy || needsClass || notMine}
              >
                {isApproved ? 'Approved (queued)' : 'Approve'}
              </button>
            )}
          </div>
        </footer>
      </div>
      {printing && <IncidentPrint report={printing} onClose={() => setPrinting(null)} />}
    </div>
  );
}
