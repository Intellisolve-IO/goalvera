import React, { useEffect, useMemo, useRef, useState } from 'react';
import MultiFilter from './MultiFilter.jsx';
import ProcessingRows from './ProcessingRows.jsx';
import { NEEDS_CLASSROOM, filterOptions, filterSessions, groupByClassroom, initialClassroomFilter } from '../pipelineFilters.js';

/** Recordings page: the most recent recordings from the cloud pipeline, by classroom, newest first. */
export default function PipelineSessions({ sessions, samples = [], state, getStudent, onOpen, onReload, homeClassrooms = [] }) {
  const [teachers, setTeachers] = useState([]);
  // Opens on the signed-in teacher's own classrooms (everyone can widen it to All).
  const [classes, setClasses] = useState(() => initialClassroomFilter(sessions, homeClassrooms));
  const opened = useRef(sessions.length > 0);
  useEffect(() => {
    if (opened.current || sessions.length === 0) return;
    opened.current = true;
    setClasses(initialClassroomFilter(sessions, homeClassrooms));
  }, [sessions, homeClassrooms]);
  const [statuses, setStatuses] = useState([]);
  const options = useMemo(() => filterOptions(sessions), [sessions]);
  const shown = useMemo(
    () => filterSessions(sessions, { teachers, classes, statuses }),
    [sessions, teachers, classes, statuses]
  );
  const narrowed = teachers.length > 0 || classes.length > 0 || statuses.length > 0;
  // Samples: their own group at the end (they have no classroom), newest first; the Status filter
  // applies, the Teacher / Classroom filters are for real recordings only.
  const sampleGroup = useMemo(() => {
    const keep = statuses.length ? samples.filter((s) => statuses.includes(s.status)) : samples;
    const ms = (s) => { const v = Date.parse(s.timestamp_start || ''); return Number.isFinite(v) ? v : -Infinity; };
    return keep.length ? [{ classroom: 'Samples', sample: true, sessions: keep.slice().sort((a, b) => ms(b) - ms(a)) }] : [];
  }, [samples, statuses]);
  const groups = useMemo(() => [...groupByClassroom(shown), ...sampleGroup], [shown, sampleGroup]);

  function resetAll() {
    setTeachers([]);
    setClasses([]);
    setStatuses([]);
  }

  return (
    <section className="pipeline">
      <div className="pipeline-head">
        <div />
        <button type="button" className="btn ghost small" onClick={onReload} disabled={state.loading}>
          {state.loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
      {!state.loading && !state.ok && (
        <p className="muted">
          Pipeline read service not running — showing sample data only. Start it with{' '}
          <code>python app/backend-pipeline/review_api/server.py</code>.
        </p>
      )}
      {state.ok ? <ProcessingRows onFinished={onReload} /> : null}
      {state.ok && sessions.length === 0 && <p className="muted">No recordings from the recorders yet.</p>}
      {sessions.length > 0 && (
        <>
          <div className="pipeline-filters">
            <MultiFilter label="Teacher" options={options.teachers} selected={teachers} onChange={setTeachers} />
            <MultiFilter label="Classroom" options={options.classes} selected={classes} onChange={setClasses} />
            <MultiFilter label="Status" options={options.statuses} selected={statuses} onChange={setStatuses} />
            <button type="button" className="filter-reset" onClick={resetAll} disabled={!narrowed}>
              Reset to All
            </button>
          </div>
          {narrowed && (
            <p className="muted filter-count">
              Showing {shown.length} of {sessions.length} recordings
            </p>
          )}
          {shown.length === 0 && <p className="muted">No recordings match these filters.</p>}
        </>
      )}
      {groups.map((g) => (
            <div className="classroom-group" key={g.sample ? "__samples" : g.classroom} data-testid="classroom-group">
              <h3 className="classroom-name">{g.sample ? 'Samples' : g.classroom === '—' ? 'No classroom yet' : g.classroom}</h3>
              <table className="pipeline-table">
                <thead>
                  <tr>
                    <th>Recorded</th>
                    <th>Student</th>
                    <th>Teacher</th>
                    <th>Device</th>
                    <th>Length</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {g.sessions.map((s) => {
                    const stu = getStudent(s.student_id);
                    return (
                      <tr key={s.recording_id} data-testid="recording-row" className={s.is_sample ? 'sample-row' : undefined}>
                        <td>
                          {(s.timestamp_start || '').replace('T', ' ').slice(0, 16)}
                          {s.is_sample ? <span className="badge sample-badge" data-testid="sample-badge">Sample</span> : null}
                        </td>
                        <td>{stu ? `${stu.first_name} ${stu.last_initial}` : '—'}</td>
                        <td>{s.teacher_name || <span className="muted">—</span>}</td>
                        <td><span className="muted">{s.is_sample ? 'Sample data' : s.device_id}</span></td>
                        <td>{Math.max(1, Math.round((s.duration_seconds || 0) / 60))} min</td>
                        <td>
                          {!s.is_sample && s.needs_class_assignment ? (
                            <span className="badge status-needs_class" title="Classroom not in the roster, so no teacher. Assign a classroom before approving.">
                              {NEEDS_CLASSROOM}
                            </span>
                          ) : (
                            <span className={`badge status-${s.status}`}>{s.status}</span>
                          )}
                        </td>
                        <td>
                          <button type="button" className="btn primary small" onClick={() => onOpen(s.recording_id)}>
                            Review
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
      ))}
    </section>
  );
}
