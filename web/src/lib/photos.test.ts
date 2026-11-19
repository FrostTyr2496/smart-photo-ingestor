import { describe, expect, it } from 'vitest';
import {
  fullOffline,
  needsParts,
  offlineTitle,
  sizeBadge,
  suggestDrives,
  addKeywords,
  fmtApart,
  fmtGapShort,
  applyFields,
  autoStep,
  mapsUrl,
  placeCandidates,
  buildFieldsByScope,
  clockPhotos,
  namedFolder,
  planSaves,
  reachPhotos,
  relatedCounts,
  relatedWindowFor,
  buildFields,
  commonValue,
  nominatimPlace,
  placeEdits,
  placeLabel,
  copyName,
  clamp,
  fitBox,
  wrapIndex,
  dayOf,
  timeTag,
  firstInTime,
  vaultCopyName,
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
  dayLine,
  dayStats,
  filterChips,
  filterNote,
  fmtExposure,
  selectionLine,
  volumeCounts,
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
  city: null,
  state: null,
  country: null,
  time_shift_s: 0,
  creator: null,
  copyright: null,
  title: null,
  keywords: [],
  dirty: null,
  rel_path: null,
  volume_id: null,
  utc_time: null,
  body_id: 1,
  sequence_id: null,
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
  // utc_time follows the -05:00 offset the helper gives every frame
  const utcOf = (t: string | null, off = -5) => (t ? `${new Date(Date.parse(`${t}Z`) - off * 3600000).toISOString().slice(0, 19)}+00:00` : null);
  const at = (id: number, t: string | null, over: Partial<PhotoGroup> = {}) => g(id, { capture_time: t, utc_time: utcOf(t), ...over });
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

  it('takes the day by wall_date when the server gives one (sh-0b)', () => {
    const w = (id: number, t: string, wall: string) => at(id, t, { wall_date: wall });
    // the first two were taken on the 15th by the camera but sit on the 16th in the shoot's zone
    const pool = [w(1, '2021-01-15T23:00:00', '2021-01-16'), w(2, '2021-01-15T23:30:00', '2021-01-16'), w(3, '2021-01-16T08:00:00', '2021-01-16'), w(4, '2021-01-16T23:00:00', '2021-01-17')];
    expect(ids(relatedSession(pool, [pool[0]!], 'day'))).toEqual([2, 3]);
  });

  it('returns nothing when the selection has no capture time', () => {
    expect(relatedSession([at(1, '2021-01-15T10:00:00')], [at(2, null)], 3600)).toEqual([]);
  });

  it('builds a window across midnight and for days', () => {
    const sel = [at(1, '2021-01-15T23:30:00')];
    expect(relatedWindow(sel, 3600)).toEqual({ after: '2021-01-15T11:30:00', before: '2021-01-16T11:30:00' });
    expect(relatedWindow(sel, 'day')).toEqual({ after: '2021-01-15T00:00:00', before: '2021-01-15T23:59:59' });
  });

  // Minutes after 10:00 for the ladder: each frame joins at the next step up.
  const ladder = () => {
    const mins = [0, 0.5, 4, 12, 50, 200, 1000];
    return mins.map((m, i) => at(i + 1, new Date(Date.parse('2021-01-15T10:00:00Z') + m * 60000).toISOString().slice(0, 19)));
  };

  it('grows with each step of the ladder and counts them', () => {
    const pool = ladder();
    const sel = [pool[0]!];
    expect(ids(relatedSession(pool, sel, 60))).toEqual([2]);
    expect(ids(relatedSession(pool, sel, 300))).toEqual([2, 3]);
    expect(ids(relatedSession(pool, sel, 900))).toEqual([2, 3, 4]);
    expect(ids(relatedSession(pool, sel, 3600))).toEqual([2, 3, 4, 5]);
    expect(ids(relatedSession(pool, sel, 10800))).toEqual([2, 3, 4, 5, 6]);
    expect(ids(relatedSession(pool, sel, 'day'))).toEqual([2, 3, 4, 5, 6]);
    expect(relatedCounts(pool, sel)).toEqual([1, 2, 3, 4, 5]);
  });

  it('Auto takes the step before the biggest jump', () => {
    expect(autoStep([3, 3, 3, 30, 30])).toBe(900);
    expect(autoStep([1, 2, 3, 4, 5])).toBe(60); // the 1 -> 2 jump is the largest
    expect(autoStep([2, 2, 2, 2, 3])).toBe(3600); // exactly 1.5 counts
    expect(autoStep([0, 0, 4, 4, 12])).toBe(3600); // empty steps are skipped
    const pool = ladder();
    expect(ids(relatedSession(pool, [pool[0]!], 'auto'))).toEqual([2]);
  });

  it('Auto falls back to the largest non-empty step up to 15 min, else 15 min', () => {
    expect(autoStep([0, 0, 5, 5, 5])).toBe(900);
    expect(autoStep([2, 2, 0, 0, 0])).toBe(300);
    expect(autoStep([2, 2, 2, 2, 2])).toBe(900);
    expect(autoStep([0, 0, 0, 4, 5])).toBe(900); // nothing up to 15 min, jump 1.25 is too small
    expect(autoStep([0, 0, 0, 0, 0])).toBe(900);
  });

  it('measures gaps in UTC, not on the naive capture time', () => {
    const a = at(1, '2021-01-15T10:00:00'); // 15:00 UTC
    const b = g(2, { capture_time: '2021-01-15T20:00:30', capture_offset: '+05:00', utc_time: '2021-01-15T15:00:30+00:00' });
    expect(ids(relatedSession([b], [a], 60))).toEqual([2]);
    const c = g(3, { capture_time: '2021-01-15T10:00:30', capture_offset: '+05:00', utc_time: '2021-01-15T05:00:30+00:00' });
    expect(ids(relatedSession([c], [a], 3600))).toEqual([]); // 30 s apart on the clock, 10 h apart in UTC
  });

  it('breaks the chain at a shoot change and at a named-folder change', () => {
    const pool = [at(1, '2021-01-15T10:00:00', { shoot_id: 1 }), at(2, '2021-01-15T10:00:30', { shoot_id: 1 }), at(3, '2021-01-15T10:01:00', { shoot_id: 2 }), at(4, '2021-01-15T10:01:30', { shoot_id: 2 })];
    expect(ids(relatedSession(pool, [pool[0]!], 300))).toEqual([2]);
    expect(ids(relatedSession(pool, [pool[3]!], 300))).toEqual([3]);
    const f = (id: number, t: string, path: string | null) => at(id, t, { rel_path: path });
    const dirs = [f(1, '2021-01-15T10:00:00', 'Harbourton/DCIM/100NCZ_6/a.NEF'), f(2, '2021-01-15T10:00:20', 'Harbourton/DCIM/101NCZ_6/b.NEF'), f(3, '2021-01-15T10:00:40', 'Quayside/DCIM/100NCZ_6/c.NEF'), f(4, '2021-01-15T10:01:00', null)];
    expect(ids(relatedSession(dirs, [dirs[0]!], 300))).toEqual([2]); // a numbered camera folder is not a name
    expect(ids(relatedSession(dirs, [dirs[2]!], 300))).toEqual([4]); // an unknown path never breaks the chain
    expect(namedFolder('DCIM/100NCZ_6/a.NEF')).toBe('');
    expect(namedFolder('a/Harbourton/DCIM/100NCZ_6/a.NEF')).toBe('Harbourton');
    expect(namedFolder(null)).toBeUndefined();
  });

  it('keeps a sequence whole', () => {
    const pool = [at(1, '2021-01-15T10:00:00', { sequence_id: 9 }), at(2, '2021-01-15T10:01:10', { sequence_id: 9 }), at(3, '2021-01-15T10:02:20', { sequence_id: 9 }), at(4, '2021-01-15T10:30:00')];
    expect(ids(relatedSession(pool, [pool[0]!], 60))).toEqual([2, 3]); // 70 s apart, yet the bracket is not split
    expect(ids(relatedSession([pool[1]!, pool[2]!, pool[3]!], [pool[2]!], 60))).toEqual([2]);
  });

  it('never carries a clock shift to another camera body', () => {
    const pool = [
      at(1, '2021-01-15T10:00:00', { body_id: 1 }),
      at(2, '2021-01-15T10:00:10', { body_id: 1 }),
      at(3, '2021-01-15T10:00:20', { body_id: 2 }),
      at(4, '2021-01-15T10:00:30', { body_id: null }),
      at(5, '2021-01-15T18:00:00', { body_id: 1 }),
      at(6, '2021-01-15T18:00:00', { body_id: 2 }),
    ];
    const sel = [pool[0]!];
    expect(ids(clockPhotos('session', pool, sel, 300))).toEqual([2]);
    expect(ids(clockPhotos('day', pool, sel, 300))).toEqual([2, 5]);
    expect(clockPhotos('selection', pool, sel, 300)).toEqual([]);
    expect(ids(reachPhotos('day', pool, sel))).toEqual([2, 3, 4, 5, 6]);
    expect(reachPhotos('selection', pool, sel)).toEqual([]);
  });

  it('fetches enough for 3 h, Auto and a day', () => {
    const sel = [at(1, '2021-01-15T10:00:00')];
    expect(relatedWindowFor(sel, ['auto', 10800])).toEqual({ after: '2021-01-14T22:00:00', before: '2021-01-15T22:00:00' });
    expect(relatedWindowFor(sel, ['auto', 'day'])).toEqual({ after: '2021-01-14T22:00:00', before: '2021-01-15T23:59:59' });
  });

  it('splits an edit by scope and gives each field only its scope\'s photos', () => {
    const p = { ...NO_EDITS, loc: { lat: 1, lon: 2, alt: null, source: 'map' as const }, creator: 'A', title: 'T', kwAdd: ['k'], shift: 60 };
    expect(Object.keys(buildFieldsByScope(p).description)).toEqual(['title', 'keywords_add']);
    const reqs = planSaves(p, [1], { place: [2, 3], description: [], credit: [2, 3, 4], clock: [5] });
    expect(reqs).toEqual([
      { ids: [1, 2, 3], fields: { lat: 1, lon: 2, alt: null, gps_source: 'map' } },
      { ids: [1, 2, 3, 4], fields: { creator: 'A' } },
      { ids: [1], fields: { title: 'T', keywords_add: ['k'] } },
      { ids: [1, 5], fields: { time_shift_s: 60 } },
    ]);
    expect(planSaves(p, [1], { place: [2], description: [2], credit: [2], clock: [2] })).toHaveLength(1);
    expect(planSaves(NO_EDITS, [1], { place: [2], description: [], credit: [], clock: [] })).toEqual([]);
  });

  it('moves utc_time with a saved clock shift', () => {
    const a = at(1, '2021-01-15T10:00:00', { time_shift_s: 0 });
    expect(applyFields(a, { time_shift_s: 90 }).utc_time).toBe('2021-01-15T15:01:30+00:00');
  });
});

describe('copy filename (rv-10)', () => {
  it('uses the basename of rel_path, else the stem', () => {
    expect(copyName({ rel_path: '2026/11/18/20261118_101500_Z6II_0042.NEF', canonical_stem: 'x' })).toBe('20261118_101500_Z6II_0042.NEF');
    expect(copyName({ rel_path: 'DSC_0042.NEF', canonical_stem: 'DSC_0042' })).toBe('DSC_0042.NEF');
    expect(copyName({ rel_path: null, canonical_stem: 'DSC_0042' })).toBe('DSC_0042');
    expect(copyName({ rel_path: null, canonical_stem: null })).toBe('');
  });
});

describe('place names (sp-3)', () => {
  it('splits city, state and country into the Place scope', () => {
    const p = { ...NO_EDITS, city: ' Harbourton ', state: 'Westshire', country: '' };
    const by = buildFieldsByScope(p);
    expect(by.place).toEqual({ city: 'Harbourton', state: 'Westshire', country: null });
    expect(by.credit).toEqual({});
    expect(hasEdits(p)).toBe(true);
    expect(hasEdits(NO_EDITS)).toBe(false);
    const plan = planSaves(p, [1], { place: [2], description: [], credit: [], clock: [] });
    expect(plan).toEqual([{ ids: [1, 2], fields: { city: 'Harbourton', state: 'Westshire', country: null } }]);
  });
  it('labels a point and builds edits from it', () => {
    const f = { place_name: 'Harbourton Quay', city: 'Harbourton', state: 'Westshire', country: 'Freedonia' };
    expect(placeLabel(f)).toBe('Harbourton, Westshire');
    expect(placeLabel({ place_name: 'Quay', city: null, state: null, country: null })).toBe('Quay');
    expect(placeLabel({ place_name: null, city: null, state: null, country: null })).toBe('');
    expect(placeEdits(f)).toEqual({ place: 'Harbourton Quay', city: 'Harbourton', state: 'Westshire', country: 'Freedonia' });
    expect(placeEdits({ place_name: null, city: 'Harbourton', state: null, country: null }).place).toBeNull();
  });
  it('reads city, state and country from a Nominatim address', () => {
    expect(nominatimPlace('Northland Falls', { town: 'Northland', county: 'Westshire', country: 'Freedonia' })).toEqual({
      place_name: 'Northland Falls',
      city: 'Northland',
      state: 'Westshire',
      country: 'Freedonia',
    });
    expect(nominatimPlace('', undefined)).toEqual({ place_name: null, city: null, state: null, country: null });
  });
});

describe('mapsUrl', () => {
  it('opens Google Maps at the coordinates only', () => {
    expect(mapsUrl(12.5, -45.25)).toBe('https://www.google.com/maps/search/?api=1&query=12.500000,-45.250000');
  });
});

describe('fmtApart / fmtGapShort (sp-8)', () => {
  it('words a phone point distance', () => {
    expect(fmtApart(-23 * 3600)).toBe('23 h earlier');
    expect(fmtApart(106 * 60)).toBe('1 h 46 min later');
    expect(fmtApart(-67 * 60)).toBe('1 h 07 min earlier');
  });
  it('shortens a signed gap', () => {
    expect(fmtGapShort(-67 * 60)).toBe('−1 h 07');
    expect(fmtGapShort(88 * 60)).toBe('+1 h 28');
  });
});

describe('vaultCopyName (rv-18)', () => {
  it('prefers the PhotoVault name, else the camera name', () => {
    expect(vaultCopyName({ rel_path: 'a/DSC_1.NEF', canonical_stem: 'M1', vault_name: 'M0001_X.NEF' })).toBe('M0001_X.NEF');
    expect(vaultCopyName({ rel_path: 'a/DSC_1.NEF', canonical_stem: 'M1', vault_name: null })).toBe('DSC_1.NEF');
  });
});

describe('firstInTime and dayOf (rv-19)', () => {
  it('picks the earliest, undated last', () => {
    const a = g(1, { capture_time: '2026-01-02T10:00:00' });
    const b = g(2, { capture_time: '2026-01-02T09:00:00' });
    const c = g(3, { capture_time: null });
    expect(firstInTime([a, c, b])?.group_id).toBe(2);
    expect(firstInTime([c])?.group_id).toBe(3);
    expect(firstInTime([])).toBeNull();
  });
  it('names the day by wall_date first', () => {
    expect(dayOf({ capture_time: '2026-01-02T23:00:00', wall_date: '2026-01-03' })).toBe('2026-01-03');
  });
  it('tags the time with its provenance (sh-0b)', () => {
    const base = { time_correction_s: 0, time_user_shift: false, time_shift_s: 0, capture_offset: '-04:00' };
    expect(timeTag({ ...base, time_provenance: 'exif_offset' })).toEqual({ label: 'camera offset', title: 'camera offset -04:00' });
    expect(timeTag({ ...base, time_provenance: 'aligned', time_correction_s: 3600 })?.title).toBe('aligned to phone (+1 h)');
    expect(timeTag({ ...base, time_provenance: 'assumed_offset' })?.label).toBe('assumed offset');
    expect(timeTag({ ...base, time_provenance: 'assumed_offset', time_user_shift: true, time_shift_s: 120 })?.title).toContain('+2 min manual shift');
    expect(timeTag({ ...base, time_provenance: null })).toBeNull();
  });
  it('names the day', () => {
    expect(dayOf({ capture_time: '2026-01-02T10:00:00' })).toBe('2026-01-02');
    expect(dayOf({ capture_time: null })).toBe(UNDATED);
  });
});

describe('viewer helpers (rv-20)', () => {
  it('wraps stepping at both ends', () => {
    expect(wrapIndex(0, -1, 5)).toBe(4);
    expect(wrapIndex(4, 1, 5)).toBe(0);
    expect(wrapIndex(2, 1, 5)).toBe(3);
    expect(wrapIndex(0, 1, 0)).toBe(0);
  });
  it('fits an image in a box without cropping', () => {
    expect(fitBox(1000, 500, 1.5)).toEqual({ w: 750, h: 500 });
    expect(fitBox(300, 500, 1.5)).toEqual({ w: 300, h: 200 });
  });
  it('clamps', () => {
    expect(clamp(5, 0, 1)).toBe(1);
    expect(clamp(-5, 0, 1)).toBe(0);
  });
});

describe('headers (rv-21)', () => {
  const at = (id: number, t: string | null, over: Partial<PhotoGroup> = {}) => g(id, { capture_time: t, ...over });
  it('formats the exposure line and skips what is missing', () => {
    expect(fmtExposure({ exposure_s: 0.004, fnumber: 2.8, iso: 3200, focal_mm: 24, lens: 'Z 24-70mm f/2.8 S' })).toBe('1/250 s · f/2.8 · ISO 3200 · 24 mm · Z 24-70mm f/2.8 S');
    expect(fmtExposure({ exposure_s: 2, fnumber: 8, iso: null, focal_mm: 50.5, lens: null })).toBe('2 s · f/8 · 50.5 mm');
    expect(fmtExposure({ exposure_s: 1.3, fnumber: null, iso: 100, focal_mm: null, lens: null })).toBe('1.3 s · ISO 100');
    expect(fmtExposure(null)).toBe('');
  });
  it('computes day stats: span, located count, bodies', () => {
    const rows = [
      at(1, '2023-09-10T21:18:00', { lat: 1, lon: 2, body_nickname: 'Z6' }),
      at(2, '2023-09-10T23:40:00', { body_nickname: 'Z6II' }),
      at(3, '2023-09-10T22:00:00', { lat: 1, lon: 2, body_nickname: 'Z6' }),
    ];
    expect(dayStats(rows)).toEqual({ span: '21:18–23:40', located: 2, bodies: ['Z6', 'Z6II'] });
    expect(dayStats([])).toEqual({ span: '', located: 0, bodies: [] });
  });
  it('builds the day line, marking a partial load', () => {
    const rows = [at(1, '2023-09-10T21:18:00', { lat: 1, lon: 2, body_nickname: 'Z6' })];
    expect(dayLine({ day: '2023-09-10', groups: 1, bodies: ['Z6'] }, rows, 1)).toBe('Z6 · 1 photo · 21:18 · 1 located · 1 selected');
    expect(dayLine({ day: '2023-09-10', groups: 5, bodies: ['Z6'] }, rows, 0)).toBe('Z6 · 5 photos · 21:18 · 1+ located');
  });
  it('labels the active filters', () => {
    expect(filterChips(null, '')).toEqual([]);
    expect(filterNote(filterChips('PP 1', 'gps'))).toBe('Filtered: PP 1 · Missing location');
    expect(filterChips(null, 'any')).toEqual([{ key: 'missing', label: 'Missing anything' }]);
  });
  it('summarises a selection and counts photos per drive', () => {
    const sel = [at(1, '2023-09-10T21:18:00', { body_nickname: 'Z6', volume_id: 'a' }), at(2, '2023-09-10T23:40:00', { body_nickname: 'Z6II', volume_id: 'b' }), at(3, '2023-09-10T22:00:00', { volume_id: 'a' })];
    expect(selectionLine(sel)).toBe('Z6, Z6II · 21:18–23:40');
    expect(volumeCounts(sel)).toEqual([{ volume_id: 'a', count: 2 }, { volume_id: 'b', count: 1 }]);
  });
});

describe('full size and which drive to plug in (rv-23)', () => {
  const needs = [
    { volume_id: 'a', label: 'Flats 1', role: 'LEGACY' },
    { volume_id: 'b', label: 'PP 1', role: 'WORKING' },
    { volume_id: 'a', label: 'Flats 1', role: 'LEGACY' },
  ];
  it('badge text', () => {
    expect(sizeBadge(6048, 4024, true)).toBe('6048 × 4024 · full size');
    expect(sizeBadge(1620, 1080, false)).toBe('1620 × 1080 · preview');
    expect(sizeBadge(null, null, false)).toBe('preview');
  });
  it('suggests WORKING first, without repeats', () => {
    expect(suggestDrives(needs).map((n) => n.volume_id)).toEqual(['b', 'a']);
    expect(needsParts(needs)).toEqual({ first: 'PP 1', rest: ' (or Flats 1)' });
    expect(needsParts([])).toEqual({ first: '', rest: '' });
  });
  it('tile icon: offline only when no holding drive is connected', () => {
    const on = (id: string) => id === 'x';
    expect(fullOffline({ held_volumes: ['x', 'y'] }, on)).toBe(false);
    expect(fullOffline({ held_volumes: ['y'] }, on)).toBe(true);
    expect(fullOffline({ held_volumes: [] }, on)).toBe(true);
    expect(fullOffline({ full_available: true }, on)).toBe(false);
    expect(offlineTitle(['PP 1'])).toBe('Full size on PP 1 — not connected');
    expect(offlineTitle([])).toBe('Not on any known drive');
  });
});

describe('placeCandidates (moving an already saved location)', () => {
  const g = (id: number, lat: number | null, lon: number | null) => ({ group_id: id, lat, lon }) as unknown as PhotoGroup;
  const sel = [g(1, 10, 20)];
  const all = [g(2, null, null), g(3, 10.0001, 20.0001), g(4, 10.01, 20)];
  it('keeps unlocated photos and those still at the selection spot, skips photos placed elsewhere', () => {
    expect(placeCandidates(all, sel, true).map((x) => x.group_id)).toEqual([2, 3]);
  });
  it('takes everything when the filter is off', () => {
    expect(placeCandidates(all, sel, false).map((x) => x.group_id)).toEqual([2, 3, 4]);
  });
  it('only unlocated photos when the selection has no location yet', () => {
    expect(placeCandidates(all, [g(1, null, null)], true).map((x) => x.group_id)).toEqual([2]);
  });
});
