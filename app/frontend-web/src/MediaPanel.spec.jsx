// Teacher Notes photos / videos in review (tasks#112).
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, test, vi } from 'vitest';

vi.mock('./dataStore.js', () => ({ fetchMediaUrl: vi.fn(async (rid, mid) => `blob:${mid}`) }));
const { default: MediaPanel } = await import('./components/MediaPanel.jsx');

describe('Teacher Notes media panel', () => {
  test('photo shows its AI notes; video says no AI notes yet', () => {
    const h = renderToStaticMarkup(<MediaPanel recordingId="rec_x" media={[
      { media_id: 'm000000000001', kind: 'photo', insights: 'The worksheet is fully colored inside the lines.', related_goal_ids: ['G1'] },
      { media_id: 'm000000000002', kind: 'video' },
    ]} />);
    expect(h).toContain('Teacher Notes photos and videos');
    expect(h).toContain('fully colored inside the lines');
    expect(h).toContain('Related goals: G1');
    expect(h).toContain('AI notes on video are not available yet');
    expect(h).toContain('never in the parent email');
  });
  test('nothing without media', () => {
    expect(renderToStaticMarkup(<MediaPanel recordingId="rec_x" media={[]} />)).toBe('');
  });
});
