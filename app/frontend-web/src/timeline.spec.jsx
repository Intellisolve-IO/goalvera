// Speaker timeline (): block positions, ticks, marker label overlap, click-to-jump.
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';
import SpeakerTimeline, { TimelineView } from './components/SpeakerTimeline.jsx';
import {
  axisTicks, blockPosition, layoutMarkerLabels, markerSeconds, scrollTurnIntoView, speakerBlocks,
  timelineDuration, turnIndexAt,
} from './timeline.js';

const turns = [
  { speaker: 'Speaker 1', start_seconds: 0, end_seconds: 10, text: 'a' },
  { speaker: 'Speaker 2', start_seconds: 20, end_seconds: 30, text: 'b' },
  { speaker: 'Speaker 3', start_seconds: 50, end_seconds: 60, text: 'c' },
  { speaker: 'Speaker 1', start_seconds: 90, end_seconds: 100, text: 'd' },
];
const markers = [{ type: 'Incident', offset_seconds: 49.3 }, { type: 'Teacher Notes', offset_seconds: 92.5 }];

describe('positions', () => {
  test('blocks are placed by start and end as a share of the recording', () => {
    expect(blockPosition(turns[1], 100)).toEqual({ left: 20, width: 10 });
    expect(blockPosition({ start_seconds: 95, end_seconds: 130 }, 100)).toEqual({ left: 95, width: 5 }); // clamped
    const b = speakerBlocks(turns, 200);
    expect(Object.keys(b)).toEqual(['Speaker 1', 'Speaker 2', 'Speaker 3']);
    expect(b['Speaker 1'].map((x) => [x.index, x.left, x.width])).toEqual([[0, 0, 5], [3, 45, 5]]);
  });

  test('length: sidecar duration_ms, then duration_seconds, never shorter than the last turn or marker', () => {
    expect(timelineDuration({ durationMs: 103920, turns })).toBeCloseTo(103.92);
    expect(timelineDuration({ durationSeconds: 120, turns })).toBe(120);
    expect(timelineDuration({ turns })).toBe(100);
    expect(timelineDuration({ durationSeconds: 50, turns, markers })).toBe(100);
    expect(markerSeconds({ offset_ms: 49300 })).toBeCloseTo(49.3);
  });

  test('mm:ss ticks at a round interval', () => {
    const { step, ticks } = axisTicks(104);
    expect(step).toBe(15);
    expect(ticks.map((t) => t.label)).toEqual(['00:00', '00:15', '00:30', '00:45', '01:00', '01:15', '01:30']);
    expect(axisTicks(45 * 60).step).toBe(600);
  });
});

describe('marker labels', () => {
  test('far-apart markers share the first row', () => {
    const p = layoutMarkerLabels(markers, 104, 600);
    expect(p.map((m) => m.row)).toEqual([0, 0]);
    expect(p[0].left).toBeCloseTo(47.4, 1);
  });

  test('labels that would overlap drop to the next row, and never overlap within a row', () => {
    const close = [
      { type: 'Incident', offset_seconds: 30 },
      { type: 'Teacher Notes', offset_seconds: 31 },
      { type: 'Incident', offset_seconds: 33 },
      { type: 'Incident', offset_seconds: 80 },
    ];
    const p = layoutMarkerLabels(close, 100, 600);
    expect(p.map((m) => m.row)).toEqual([0, 1, 2, 0]);
    for (const a of p) for (const b of p) {
      if (a !== b && a.row === b.row) expect(a.endPx <= b.startPx || b.endPx <= a.startPx).toBe(true);
    }
  });

  test('a label at the end of the track reads to the left so it is not cut off', () => {
    const [m] = layoutMarkerLabels([{ type: 'Teacher Notes', offset_seconds: 99 }], 100, 600);
    expect(m.alignRight).toBe(true);
    expect(m.endPx).toBeLessThanOrEqual(600);
  });
});

describe('click to jump', () => {
  test('a time maps to the turn containing it, else the nearest turn', () => {
    expect(turnIndexAt(turns, 25)).toBe(1);
    expect(turnIndexAt(turns, 49.3)).toBe(2);     // 0.7 s before turn 2
    expect(turnIndexAt(turns, 92.5)).toBe(3);
    expect(turnIndexAt(turns, 40)).toBe(1);       // 10 s from both turns: the earlier one wins
    expect(turnIndexAt([], 5)).toBe(-1);
  });

  test('scrolls the turns list to the turn', () => {
    const el = { offsetTop: 400 };
    const container = { querySelector: vi.fn(() => el), scrollTo: vi.fn() };
    expect(scrollTurnIntoView(container, 3)).toBe(el);
    expect(container.querySelector).toHaveBeenCalledWith('[data-turn-index="3"]');
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 392, behavior: 'smooth' });
    expect(scrollTurnIntoView({ querySelector: () => null }, 3)).toBe(null);
  });

  test('clicking a block or marker reports the turn / time', () => {
    const onJump = vi.fn();
    const el = TimelineView({ turns, speakers: ['Speaker 1', 'Speaker 2', 'Speaker 3'], markers, duration: 100,
      options: [''], onJump });
    const found = [];
    const walk = (n) => {
      if (!n || typeof n !== 'object') return;
      if (Array.isArray(n)) return n.forEach(walk);
      if (n.props?.onClick && n.type === 'button') found.push(n);
      walk(n.props?.children);
    };
    walk(el);
    const block = found.find((b) => b.props['data-turn'] === 2);
    block.props.onClick();
    expect(onJump).toHaveBeenLastCalledWith(50, 2);
    const marker = found.find((b) => b.props.children === 'Teacher Notes');
    marker.props.onClick();
    expect(onJump).toHaveBeenLastCalledWith(92.5, null);
  });
});

describe('render', () => {
  const props = { turns, speakers: ['Speaker 1', 'Speaker 2', 'Speaker 3'], markers, duration: 100,
    options: ['', 'Teacher', 'Marcus'], map: { 'Speaker 2': 'Marcus' } };

  test('one track per speaker with its dropdown, blocks, markers and labels', () => {
    const html = renderToStaticMarkup(<SpeakerTimeline {...props} />);
    expect(html.match(/<select/g)).toHaveLength(3);
    expect(html).toContain('aria-label="Who is Speaker 1?"');
    expect(html.match(/class="tl-block"/g)).toHaveLength(4);
    expect(html).toContain('left:20%;width:10%');
    expect(html).toContain('title="Speaker 2 (Marcus), 00:20–00:30"');
    expect(html).toMatch(/aria-label="Incident marker at 00:49\. Show in transcript"/);
    expect(html).toContain('>Teacher Notes</button>');
    expect(html).toContain('>Markers</div>');
    expect(html.match(/class="tl-guide /g)).toHaveLength(6); // 2 markers x 3 tracks
    expect(html).not.toMatch(/<select[^>]*disabled/);
  });

  test('read-only: timeline shows, dropdowns are disabled, blocks still jump', () => {
    const html = renderToStaticMarkup(<SpeakerTimeline {...props} readOnly />);
    for (const m of html.matchAll(/<select\b[^>]*>/g)) expect(m[0]).toContain('disabled');
    expect(html).not.toMatch(/<button[^>]*disabled/);
  });
});
