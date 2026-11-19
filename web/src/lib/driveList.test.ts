import { describe, expect, it } from 'vitest';
import {
  busyDriveKeys,
  driveHref,
  unplugged,
  driveForRoute,
  facetOptions,
  filterDrives,
  lastActivity,
  sortDrives,
  touchDrive,
  trackArrivals,
  typeKey,
  type SeenState,
} from './driveList';
import type { Drive, TaskRec } from './types';

const drive = (over: Partial<Drive>): Drive => ({
  mount: '/mnt/X',
  role: 'WORKING',
  label: null,
  fs: 'apfs',
  size: '1.0 TB',
  free: '100 GB',
  volume_id: 'v-x',
  mounted: true,
  looks_like_card: false,
  last_seen_at: null,
  local: false,
  key: 'v-x',
  ignored: false,
  ...over,
});

const card = drive({ mount: '/mnt/CARD', role: 'CARD', fs: 'exfat', size: '64 GB', free: '10 GB', volume_id: 'v-card', key: 'v-card' });
const work = drive({ mount: '/mnt/WORK', fs: 'exfat', size: '1.0 TB', free: '500 GB', volume_id: 'v-work', key: 'v-work' });
const legacy = drive({ mount: '/mnt/OLD', role: 'LEGACY', size: '4.0 TB', free: '20 GB', volume_id: 'v-old', key: 'v-old' });
const unsetUp = drive({ mount: '/mnt/NEW', role: null, volume_id: null, key: 'mount:NEW', fs: 'ntfs', size: null, free: null });

describe('trackArrivals', () => {
  it('flags nothing on the very first visit', () => {
    const s = trackArrivals(null, [card, work], 1000);
    expect(s.fresh).toEqual({});
    expect(s.keys).toEqual(['v-card', 'v-work']);
  });

  it('flags a drive plugged in since the last poll, even one seen before', () => {
    let s = trackArrivals(null, [work], 1000);
    s = trackArrivals(s, [work, card], 2000);
    expect(s.fresh).toEqual({ 'v-card': 2000 });
    expect(s.touched['v-card']).toBe(2000);
    // Stays fresh across polls until touched.
    s = trackArrivals(s, [work, card], 3000);
    expect(s.fresh).toEqual({ 'v-card': 2000 });
    // Unplug, plug back: new again.
    s = touchDrive(s, 'v-card', 3500);
    s = trackArrivals(s, [work, { ...card, mounted: false }], 4000);
    expect(s.fresh).toEqual({});
    s = trackArrivals(s, [work, card], 5000);
    expect(s.fresh).toEqual({ 'v-card': 5000 });
  });

  it('does not re-flag a drive whose key changed at setup (same mount)', () => {
    let s = trackArrivals(null, [unsetUp], 1000);
    s = trackArrivals(s, [{ ...unsetUp, key: 'v-new', volume_id: 'v-new' }], 2000);
    expect(s.fresh).toEqual({});
  });

  it('drops a fresh drive when it is unplugged', () => {
    let s = trackArrivals(null, [], 1000);
    s = trackArrivals(s, [card], 2000);
    s = trackArrivals(s, [], 3000);
    expect(s.fresh).toEqual({});
  });
});

describe('touchDrive', () => {
  it('clears fresh and records the time', () => {
    const s: SeenState = { keys: ['a'], mounts: ['/a'], fresh: { a: 1, b: 2 }, touched: {} };
    const t = touchDrive(s, 'a', 9);
    expect(t.fresh).toEqual({ b: 2 });
    expect(t.touched).toEqual({ a: 9 });
  });
});

describe('lastActivity', () => {
  it('takes the later of an interaction and a step run', () => {
    const d = { ...work, steps: { index: { at: '1970-01-01T00:00:05Z', state: 'done' }, hash: null } };
    expect(lastActivity(d, {})).toBe(5000);
    expect(lastActivity(d, { 'v-work': 9000 })).toBe(9000);
  });
});

describe('sortDrives', () => {
  const list = [legacy, card, work, unsetUp];
  const none = { fresh: {}, touched: {} };
  const keys = (l: Drive[]) => l.map((d) => d.key);

  it('keeps the server order by default', () => {
    expect(keys(sortDrives(list, 'default', none))).toEqual(['v-old', 'v-card', 'v-work', 'mount:NEW']);
  });
  it('sorts by type, format, size and free space', () => {
    expect(keys(sortDrives(list, 'type', none))).toEqual(['v-card', 'v-work', 'v-old', 'mount:NEW']);
    expect(keys(sortDrives(list, 'format', none))).toEqual(['v-old', 'v-card', 'v-work', 'mount:NEW']);
    expect(keys(sortDrives(list, 'size', none))).toEqual(['v-old', 'v-work', 'v-card', 'mount:NEW']);
    expect(keys(sortDrives(list, 'free', none))).toEqual(['v-work', 'v-old', 'v-card', 'mount:NEW']);
  });
  it('sorts by most recent use', () => {
    expect(keys(sortDrives(list, 'recent', { fresh: {}, touched: { 'v-card': 5, 'v-old': 9 } })).slice(0, 2)).toEqual(['v-old', 'v-card']);
  });
  it('puts fresh drives first, newest arrival first, whatever the sort', () => {
    const seen = { fresh: { 'v-work': 1, 'mount:NEW': 2 }, touched: {} };
    expect(keys(sortDrives(list, 'size', seen))).toEqual(['mount:NEW', 'v-work', 'v-old', 'v-card']);
  });
});

describe('filterDrives and facetOptions', () => {
  const list = [legacy, card, work, unsetUp];
  it('matches every chosen facet, any value within one', () => {
    expect(filterDrives(list, { types: ['card', 'working'], formats: [] }, {}).map((d) => d.key)).toEqual(['v-card', 'v-work']);
    expect(filterDrives(list, { types: ['working'], formats: ['APFS'] }, {})).toEqual([]);
    expect(filterDrives(list, { types: [], formats: ['exFAT'] }, {}).map((d) => d.key)).toEqual(['v-card', 'v-work']);
  });
  it('always shows a fresh drive', () => {
    expect(filterDrives(list, { types: ['legacy'], formats: [] }, { 'v-card': 1 }).map((d) => d.key)).toEqual(['v-old', 'v-card']);
  });
  it('lists the types and formats present', () => {
    expect(facetOptions(list)).toEqual({ types: ['card', 'working', 'legacy', 'new'], formats: ['APFS', 'exFAT', 'NTFS'] });
  });
  it('types a card that is not set up yet as a card', () => {
    expect(typeKey({ ...unsetUp, looks_like_card: true })).toBe('card');
  });
});

describe('interactions', () => {
  it('finds the drive a page is about', () => {
    const list = [card, work, unsetUp];
    expect(driveForRoute({ view: 'cards', id: 'v-card' }, list)?.key).toBe('v-card');
    expect(driveForRoute({ view: 'drive', id: 'v-work' }, list)?.key).toBe('v-work');
    expect(driveForRoute({ view: 'setup', key: 'mount:NEW' }, list)?.key).toBe('mount:NEW');
    expect(driveForRoute({ view: 'cards', id: null }, list)).toBeUndefined();
  });
  it('finds the drives a running task works on', () => {
    const t = {
      argv: ['py', '-m', 'photovault', '--json', 'ingest', '--source', '/mnt/CARD', '--target', '/mnt/WORK/'],
      final: false,
    } as TaskRec;
    expect(busyDriveKeys([t], [], [card, work, legacy])).toEqual(['v-card', 'v-work']);
    expect(busyDriveKeys([{ ...t, final: true }], [], [card, work])).toEqual([]);
    expect(busyDriveKeys([], [{ pid: 1, words: ['index', '/mnt/OLD'], started_at: '' }], [legacy])).toEqual(['v-old']);
  });
});

describe('unplugged (rv-38)', () => {
  it('reports drives connected last poll and listed as not connected now', () => {
    const out = { ...card, mounted: false };
    expect(unplugged(['v-card', 'v-work'], [out, work]).map((d) => d.key)).toEqual(['v-card']);
  });
  it('reports nothing on the first poll, for ignored drives, or for drives that were already out', () => {
    const out = { ...card, mounted: false };
    expect(unplugged(null, [out])).toEqual([]);
    expect(unplugged(['v-card'], [{ ...out, ignored: true }])).toEqual([]);
    expect(unplugged(['v-work'], [out, work])).toEqual([]);
  });
  it('links a card to Cards, a set-up drive to its page, a new one to setup', () => {
    expect(driveHref(card)).toBe('#/cards/v-card');
    expect(driveHref(work)).toBe('#/drive/v-work');
    expect(driveHref(drive({ volume_id: null, key: 'k-new' }))).toBe('#/setup/k-new');
  });
});
