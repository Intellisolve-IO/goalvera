import { describe, expect, test } from 'vitest';
import {
  addWeeks, canGoBack, canGoForward, initialNav, navReducer, slots, weekOf, weekRange,
} from './plannerWeeks.js';

const THIS = '2026-10-05';

describe('weeks', () => {
  test('weekOf gives the Monday', () => {
    expect(weekOf('2026-10-07')).toBe(THIS);
    expect(weekOf('2026-10-05')).toBe(THIS);
    expect(weekOf('2026-10-11')).toBe(THIS); // Sunday
    expect(weekOf('2026-10-12')).toBe('2026-10-12');
  });
  test('addWeeks crosses months and the DST change', () => {
    expect(addWeeks(THIS, -1)).toBe('2026-09-28');
    expect(addWeeks('2026-10-26', 1)).toBe('2026-11-02'); // US DST ends Nov 1
    expect(addWeeks('2026-12-28', 1)).toBe('2027-01-04');
  });
  test('weekRange is Mon–Fri', () => {
    expect(weekRange('2026-09-28')).toBe('Sep 28 – Oct 2');
    expect(weekRange(THIS)).toBe('Oct 5 – 9');
  });
});

describe('navigation: this week stays anchored', () => {
  const start = initialNav(THIS, '2025-10-06');

  test('starts on last week / this week / next week', () => {
    expect(slots(start).map((s) => [s.title, s.week])).toEqual([
      ['Last week', '2026-09-28'], ['This week', THIS], ['Next week', '2026-10-12'],
    ]);
  });

  test('back steps only the past slot; this week and next week never move', () => {
    let nav = start;
    for (let i = 1; i <= 5; i += 1) {
      nav = navReducer(nav, { type: 'back' });
      const [past, cur, next] = slots(nav);
      expect(past.week).toBe(addWeeks('2026-09-28', -i));
      expect(past.title).toBe(`${i + 1} weeks ago`);
      expect([cur.week, next.week]).toEqual([THIS, '2026-10-12']);
    }
  });

  test('forward stops at last week (never shows this week twice)', () => {
    expect(canGoForward(start)).toBe(false);
    expect(navReducer(start, { type: 'forward' })).toBe(start);
    let nav = navReducer(navReducer(start, { type: 'back' }), { type: 'back' });
    expect(canGoForward(nav)).toBe(true);
    nav = navReducer(nav, { type: 'forward' });
    nav = navReducer(nav, { type: 'forward' });
    nav = navReducer(nav, { type: 'forward' });
    expect(nav.past).toBe('2026-09-28');
  });

  test('back stops at the earliest week the server allows', () => {
    let nav = initialNav(THIS, '2026-09-21');
    expect(canGoBack(nav)).toBe(true);
    nav = navReducer(nav, { type: 'back' });
    expect(nav.past).toBe('2026-09-21');
    expect(canGoBack(nav)).toBe(false);
    expect(navReducer(nav, { type: 'back' })).toBe(nav);
  });

  test('reset returns to last week; anchor follows a new this week', () => {
    const nav = navReducer(navReducer(start, { type: 'back' }), { type: 'reset' });
    expect(nav.past).toBe('2026-09-28');
    const moved = navReducer(nav, { type: 'anchor', thisWeek: '2026-10-12' });
    expect(slots(moved).map((s) => s.week)).toEqual([THIS, '2026-10-12', '2026-10-19']);
  });
});
