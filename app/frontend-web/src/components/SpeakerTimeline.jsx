import React, { useEffect, useRef, useState } from 'react';
import SpeakerPicker from './SpeakerPicker.jsx';
import { SPEAKER_TEXT, speakerEdge, speakerFill } from '../speakerColors.js';
import { axisTicks, layoutMarkerLabels, mmss, speakerBlocks } from '../timeline.js';

const MARKER_ROW_PX = 22;

export function markerTone(type) {
  return /incident/i.test(type) ? 'incident' : 'note';
}

/**
 * Horizontal timeline of the recording: an mm:ss axis, one track per
 * diarized speaker (who-is-who dropdown on the left, talking blocks on the
 * right) and a marker track. Clicking a block or marker calls onJump(seconds,
 * turnIndex). Mapping is the same browser-only speaker map as before.
 */
export default function SpeakerTimeline(props) {
  const trackRef = useRef(null);
  const [trackPx, setTrackPx] = useState(600);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return undefined;
    const measure = () => el.clientWidth && setTrackPx(el.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return <TimelineView {...props} trackPx={trackPx} trackRef={trackRef} />;
}

/** The timeline itself, without hooks: trackPx is the marker track's measured width (for label overlap). */
export function TimelineView({
  turns = [],
  speakers = [],
  markers = [],
  duration,
  map = {},
  options = [],
  choices = null,
  onMap,
  onJump,
  readOnly = false,
  trackPx = 600,
  trackRef = null,
}) {
  const blocks = speakerBlocks(turns, duration);
  const { ticks } = axisTicks(duration);
  const placed = layoutMarkerLabels(markers, duration, trackPx);
  const markerRows = placed.reduce((n, m) => Math.max(n, m.row + 1), 1);
  const who = (sp) => (map[sp] ? `${sp} (${map[sp]})` : sp);

  const guides = placed.map((m, i) => (
    <span key={`g${i}`} className={`tl-guide tl-guide-${markerTone(m.type)}`} style={{ left: `${m.left}%` }} aria-hidden="true" />
  ));

  return (
    <div className="speaker-timeline" role="group" aria-label={`Recording timeline, ${mmss(duration)} long`}
      data-testid="speaker-timeline">
      <div className="tl-row tl-axis-row" aria-hidden="true">
        <div className="tl-label tl-axis-label">length {mmss(duration)}</div>
        <div className="tl-track tl-axis">
          {ticks.map((t) => (
            <span key={t.seconds} className={`tl-tick${t.left > 94 ? ' tl-tick-end' : ''}${t.left === 0 ? ' tl-tick-start' : ''}`}
              style={{ left: `${t.left}%` }}>
              {t.label}
            </span>
          ))}
        </div>
      </div>

      {speakers.map((sp) => {
        const fill = speakerFill(sp, speakers);
        const edge = speakerEdge(sp, speakers);
        return (
          <div className="tl-row" key={sp} data-speaker={sp}>
            {choices ? (
              <div className="tl-label speaker-assign" style={{ backgroundColor: fill, color: SPEAKER_TEXT, borderColor: edge }}>
                <span className="speaker-name">{sp}</span>
                <SpeakerPicker speaker={sp} value={map[sp] || ''} choices={choices} readOnly={readOnly}
                  onChange={(v) => onMap && onMap(sp, v)} />
              </div>
            ) : (
              <label className="tl-label speaker-assign" style={{ backgroundColor: fill, color: SPEAKER_TEXT, borderColor: edge }}>
                <span className="speaker-name">{sp}</span>
                <select value={map[sp] || ''} onChange={(e) => onMap && onMap(sp, e.target.value)} aria-label={`Who is ${sp}?`}
                  disabled={readOnly}>
                  {options.map((o) => (
                    <option key={o} value={o}>
                      {o || '— who is this? —'}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="tl-track tl-speaker-track" role="group" aria-label={`${who(sp)} talking`}
              style={{ backgroundColor: `${fill}55` }}>
              {ticks.map((t) => (
                <span key={t.seconds} className="tl-grid" style={{ left: `${t.left}%` }} aria-hidden="true" />
              ))}
              {guides}
              {(blocks[sp] || []).map((b) => {
                const label = `${who(sp)}, ${mmss(b.start)}–${mmss(b.end)}`;
                return (
                  <button key={b.index} type="button" className="tl-block" data-turn={b.index}
                    style={{ left: `${b.left}%`, width: `${b.width}%`, backgroundColor: fill, borderColor: edge }}
                    title={label} aria-label={`${label}. Show in transcript`}
                    onClick={() => onJump && onJump(b.start, b.index)} />
                );
              })}
            </div>
          </div>
        );
      })}

      <div className="tl-row tl-marker-row">
        <div className="tl-label tl-markers-label">Markers</div>
        <div className="tl-track tl-marker-track" ref={trackRef} role="group" aria-label="Markers"
          style={{ height: `${markerRows * MARKER_ROW_PX + 8}px` }}>
          {placed.length === 0 && <span className="tl-empty">No markers</span>}
          {placed.map((m, i) => {
            const tone = markerTone(m.type);
            const label = `${m.type} marker at ${mmss(m.seconds)}`;
            return (
              <React.Fragment key={i}>
                <span className={`tl-flagpole tl-${tone}`} style={{ left: `${m.left}%`, height: `${m.row * MARKER_ROW_PX + 6}px` }}
                  aria-hidden="true" />
                <button type="button" className={`tl-marker tl-${tone}${m.alignRight ? ' tl-marker-left' : ''}`}
                  data-row={m.row} style={{ left: `${m.left}%`, top: `${m.row * MARKER_ROW_PX + 4}px` }}
                  title={`${m.type} · ${mmss(m.seconds)}`} aria-label={`${label}. Show in transcript`}
                  onClick={() => onJump && onJump(m.seconds, null)}>
                  {m.type}
                </button>
              </React.Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}
