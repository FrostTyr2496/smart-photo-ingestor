import { describe, expect, it } from 'vitest';
import {
  addKeywords,
  applyFields,
  buildFields,
  commonValue,
  hasEdits,
  keywordChips,
  monthRail,
  nearestWithLocation,
  NO_EDITS,
  rangeIds,
  relatedSession,
  relatedWindow,
  removeKeyword,
  shiftedTime,
  shiftSeconds,
  splitShift,
  UNDATED,
  type PhotoGroup,
} from './photos';

const g = (id: number, over: Partial<PhotoGroup> = {}): PhotoGroup => ({
  group_id: id,
  canonical_stem: `DSC_${id}`,
  capture_time: '2021-01-15T10:00:00',
  capture_offset: '-05:00',
  shoot_id: null,
  body_nickname: 'Z6',
  shoot_name: null,
  lat: null,
  lon: null,
  alt: null,
  gps_source: null,
  place_name: null,
  time_shift_s: 0,
  creator: null,
  copyright: null,
  title: null,
  keywords: [],
  dirty: null,
  rel_path: null,
  volume_id: null,
  ...over,
});

describe('selection', () => {
  it('selects a range in display order, either direction', () => {
    expect(rangeIds([5, 6, 7, 8], 6, 8)).toEqual([6, 7, 8]);
    expect(rangeIds([5, 6, 7, 8], 8, 5)).toEqual([5, 6, 7, 8]);
    expect(rangeIds([5, 6, 7, 8], null, 7)).toEqual([7]);
    expect(rangeIds([5, 6], 99, 6)).toEqual([6]);
  });

  it('finds the shared value, or null when photos differ', () => {
    expect(commonValue([g(1, { creator: 'A' }), g(2, { creator: 'A' })], 'creator')).toBe('A');
    expect(commonValue([g(1), g(2)], 'creator')).toBe('');
    expect(commonValue([g(1, { creator: 'A' }), g(2)], 'creator')).toBeNull();
  });
});

describe('keywords', () => {
  it('counts each keyword across the selection, after pending edits', () => {
    const sel = [g(1, { keywords: ['beach', 'Sun'] }), g(2, { keywords: ['Beach'] })];
    const chips = keywordChips(sel, ['dusk', 'sun'], ['beach']);
    expect(chips).toEqual([
      { word: 'Sun', on: 2, pending: 'add' },
      { word: 'dusk', on: 2, pending: 'add' },
    ]);
  });

  it('adding undoes a removal, removing undoes an addition', () => {
    let p = removeKeyword(NO_EDITS, 'beach');
    expect(p.kwRemove).toEqual(['beach']);
    p = addKeywords(p, 'Beach, dusk , ,dusk', ['beach']);
    expect(p).toMatchObject({ kwAdd: ['dusk'], kwRemove: [] });
    p = removeKeyword(p, 'DUSK');
    expect(p.kwAdd).toEqual([]);
    expect(hasEdits(p)).toBe(false);
  });
});

describe('fields', () => {
  it('sends only what was touched', () => {
    expect(buildFields(NO_EDITS)).toEqual({});
    const f = buildFields({ ...NO_EDITS, loc: { lat: 1, lon: 2, alt: null, source: 'map' }, creator: '  ', title: ' T ', shift: -60 });
    expect(f).toEqual({ lat: 1, lon: 2, alt: null, gps_source: 'map', creator: null, title: 'T', time_shift_s: -60 });
  });

  it('applies fields the way the server does', () => {
    const before = g(1, { keywords: ['a', 'B'], creator: 'X' });
    const after = applyFields(before, { lat: 3, lon: 4, keywords_add: ['c'], keywords_remove: ['b'] });
    expect(after).toMatchObject({ lat: 3, lon: 4, creator: 'X', keywords: ['a', 'c'], dirty: true });
    expect(before.keywords).toEqual(['a', 'B']);
  });
});

describe('time', () => {
  it('shifts the wall time and keeps the zone', () => {
    expect(shiftedTime(g(1), 0, '+00:00')).toBe('2021-01-15 10:00:00 (-05:00)');
    expect(shiftedTime(g(1), -3600 * 11, '+00:00')).toBe('2021-01-14 23:00:00 (-05:00)');
    expect(shiftedTime(g(1, { capture_offset: null }), 90, '+02:00')).toBe('2021-01-15 10:01:30 (+02:00)');
    expect(shiftedTime(g(1, { capture_time: null }), 0, '')).toBe('no capture time');
  });

  it('round-trips a shift through its fields', () => {
    expect(shiftSeconds('-', 1, 2, 3)).toBe(-3723);
    expect(splitShift(-3723)).toEqual({ sign: '-', h: 1, m: 2, s: 3 });
  });

  it('finds the nearest located photo that is not selected', () => {
    const t = g(1);
    const pool = [g(2, { lat: 1, lon: 1, capture_time: '2021-01-15T10:05:00' }), g(3, { lat: 2, lon: 2, capture_time: '2021-01-15T09:58:00' }), g(4, { lat: 9, lon: 9, capture_time: '2021-01-15T10:00:30' })];
    expect(nearestWithLocation(t, pool, new Set([4]), 1800)).toMatchObject({ group: { group_id: 3 }, delta: -120 });
    expect(nearestWithLocation(t, pool, new Set([4]), 60)).toBeNull();
  });
});

describe('month rail', () => {
  it('groups days into years and months, skipping undated', () => {
    const rail = monthRail([
      { day: '2026-10-27', groups: 5, bodies: [] },
      { day: '2026-10-07', groups: 2, bodies: [] },
      { day: '2026-03-01', groups: 1, bodies: [] },
      { day: '2021-01-15', groups: 4, bodies: [] },
      { day: UNDATED, groups: 9, bodies: [] },
    ]);
    expect(rail).toEqual([
      { year: '2026', months: [{ month: 10, day: '2026-10-27', groups: 7 }, { month: 3, day: '2026-03-01', groups: 1 }] },
      { year: '2021', months: [{ month: 1, day: '2021-01-15', groups: 4 }] },
    ]);
  });
});

describe('related photos', () => {
  const at = (id: number, t: string | null) => g(id, { capture_time: t });
  const ids = (l: PhotoGroup[]) => l.map((x) => x.group_id);

  it('stops at the first gap that is too long, in both directions', () => {
    const pool = [
      at(1, '2021-01-15T08:00:00'),
      at(2, '2021-01-15T09:30:00'),
      at(3, '2021-01-15T09:50:00'),
      at(4, '2021-01-15T10:00:00'),
      at(5, '2021-01-15T10:20:00'),
      at(6, '2021-01-15T12:00:00'),
    ];
    expect(ids(relatedSession(pool, [pool[3]!], 1800))).toEqual([2, 3, 5]);
    expect(ids(relatedSession(pool, [pool[3]!], 7200))).toEqual([1, 2, 3, 5, 6]);
  });

  it('dedupes, ignores untimed candidates and includes photos between selected ones', () => {
    const a = at(1, '2021-01-15T10:00:00');
    const b = at(2, '2021-01-15T10:10:00');
    const c = at(3, '2021-01-15T11:00:00');
    const out = relatedSession([c, c, at(9, null), b, a], [a, c], 60);
    expect(ids(out)).toEqual([2]);
  });

  it('takes the whole day in day mode', () => {
    const pool = [at(1, '2021-01-14T23:59:00'), at(2, '2021-01-15T01:00:00'), at(3, '2021-01-15T23:00:00'), at(4, '2021-01-16T00:00:00')];
    expect(ids(relatedSession(pool, [pool[1]!], 'day'))).toEqual([3]);
  });

  it('returns nothing when the selection has no capture time', () => {
    expect(relatedSession([at(1, '2021-01-15T10:00:00')], [at(2, null)], 3600)).toEqual([]);
  });

  it('builds a window across midnight and for days', () => {
    const sel = [at(1, '2021-01-15T23:30:00')];
    expect(relatedWindow(sel, 3600)).toEqual({ after: '2021-01-15T11:30:00', before: '2021-01-16T11:30:00' });
    expect(relatedWindow(sel, 'day')).toEqual({ after: '2021-01-15T00:00:00', before: '2021-01-15T23:59:59' });
  });
});
