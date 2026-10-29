import React, { useEffect, useRef, useState } from 'react';
import { fetchTranscript, getSpeakerMap, loadSpeakers, setSpeakerMap } from '../dataStore.js';
import { SPEAKER_TEXT, speakerFill } from '../speakerColors.js';
import { mmss, scrollTurnIntoView, timelineDuration, turnIndexAt } from '../timeline.js';
import SpeakerTimeline from './SpeakerTimeline.jsx';

const FLASH_MS = 1800;

/**
 * Diarized transcript with human speaker mapping. Speaker separation only
 * gives "Speaker 1/2/3"; the reviewer decides who is who (kept in this browser, never sent to the AI).
 * The choices are students and staff from the roster (tasks#104), the session's class first.
 * The timeline above the turns (tasks#82) holds the who-is-who dropdowns.
 */
export default function TranscriptPanel({ recordingId, student, readOnly = false, markers = null, durationSeconds = null,
  durationMs = null, belowTimeline = null, reviewerId = null, choicesKey = 0 }) {
  const [data, setData] = useState(null);
  const [choices, setChoices] = useState(null);   // roster lookup (tasks#104); null = the fixed fallback list
  const [error, setError] = useState(null);
  const [map, setMap] = useState(() => getSpeakerMap(recordingId));
  const [flash, setFlash] = useState(-1);
  const turnsRef = useRef(null);
  const flashTimer = useRef(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    setFlash(-1);
    setMap(getSpeakerMap(recordingId));
    fetchTranscript(recordingId)
      .then((d) => live && setData(d))
      .catch(() => live && setError('Transcript unavailable'));
    return () => {
      live = false;
    };
  }, [recordingId]);

  // who-is-who choices from the roster: the session's class first, then everyone (re-read when the class changes)
  useEffect(() => {
    let live = true;
    setChoices(null);
    loadSpeakers(recordingId, reviewerId).then((c) => live && setChoices(c)).catch(() => {});
    return () => {
      live = false;
    };
  }, [recordingId, reviewerId, choicesKey]);

  useEffect(() => () => clearTimeout(flashTimer.current), []);

  // belowTimeline (the "Add marker" control) shows even when the transcript can't load
  if (error) return <><p className="muted">{error}</p>{belowTimeline}</>;
  if (!data) return <><p className="muted">Loading transcript…</p>{belowTimeline}</>;

  const turns = data.speaker_turns || [];
  const speakers = [...new Set(turns.map((t) => t.speaker))];
  // fallback when the lookup isn't available (laptop samples, older server): generic choices only
  const options = ['', student ? student.first_name : 'Student', 'Unknown speaker', 'Other adult', 'Other student'];
  const marks = markers && markers.length ? markers : data.markers || [];
  const duration = timelineDuration({
    durationMs: durationMs ?? data.duration_ms,
    durationSeconds: durationSeconds ?? data.duration_seconds,
    turns,
    markers: marks,
  });

  function update(label, value) {
    const next = { ...map, [label]: value };
    setMap(next);
    setSpeakerMap(recordingId, next);
  }

  function jump(seconds, index) {
    const i = index ?? turnIndexAt(turns, seconds);
    if (i < 0) return;
    const el = scrollTurnIntoView(turnsRef.current, i);
    if (el && typeof el.focus === 'function') el.focus({ preventScroll: true });
    clearTimeout(flashTimer.current);
    setFlash(i);
    flashTimer.current = setTimeout(() => setFlash(-1), FLASH_MS);
  }

  return (
    <div className="transcript">
      <SpeakerTimeline turns={turns} speakers={speakers} markers={marks} duration={duration} map={map}
        options={options} choices={choices} onMap={update} onJump={jump} readOnly={readOnly} />
      {belowTimeline}
      <ol className="turns" ref={turnsRef} aria-label="Transcript turns">
        {turns.map((t, i) => (
          <li key={i} className={`turn${flash === i ? ' turn-flash' : ''}`} data-turn-index={i} tabIndex={-1}
            style={{ backgroundColor: speakerFill(t.speaker, speakers), color: SPEAKER_TEXT }}>
            <span className="ts">
              [{mmss(t.start_seconds)}–{mmss(t.end_seconds)}]
            </span>{' '}
            <strong className="speaker-label">
              {map[t.speaker] ? `${map[t.speaker]} (${t.speaker})` : t.speaker}:
            </strong>{' '}
            <span className="turn-text">{t.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
