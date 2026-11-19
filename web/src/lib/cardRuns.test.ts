import { describe, expect, it } from 'vitest';
import { cardRunKind, copyTargets, updatedAgo } from './cardRuns';

const card = { volume_id: 'vol-card', mount: '/Volumes/CARD' };

describe('cardRunKind', () => {
  it('sees an import from this card', () => {
    expect(cardRunKind(['ingest', '--source', '/Volumes/CARD', '--target', '/Volumes/X'], card)).toBe('copy');
    expect(cardRunKind(['ingest', '--source', '/Volumes/CARD/', '--target', '/Volumes/X'], card)).toBe('copy');
  });
  it('ignores an import from another card, and dry runs', () => {
    expect(cardRunKind(['ingest', '--source', '/Volumes/OTHER', '--target', '/Volumes/X'], card)).toBeNull();
    expect(cardRunKind(['ingest', '--source', '/Volumes/CARD', '--target', '/Volumes/X', '--dry-run'], card)).toBeNull();
  });
  it('sees a backup by card id or by mount', () => {
    expect(cardRunKind(['backup', '--card', 'vol-card', '--to', '/Volumes/B', '--follow', '7'], card)).toBe('copy');
    expect(cardRunKind(['backup', '--from', '/Volumes/CARD', '--to', '/Volumes/B'], card)).toBe('copy');
    expect(cardRunKind(['backup', '--card', 'vol-other', '--to', '/Volumes/B'], card)).toBeNull();
  });
  it('sees checks and clears', () => {
    expect(cardRunKind(['cards', 'check', '/Volumes/CARD'], card)).toBe('check');
    expect(cardRunKind(['card-status', '/Volumes/CARD'], card)).toBe('check');
    expect(cardRunKind(['cards', 'clear', '/Volumes/CARD', '--confirm', 'x'], card)).toBe('clear');
    expect(cardRunKind(['hash', '/Volumes/CARD'], card)).toBeNull();
  });
  it('needs a mount to match by mount', () => {
    expect(cardRunKind(['ingest', '--source', '/Volumes/CARD'], { volume_id: 'vol-card', mount: null })).toBeNull();
  });
});

describe('copyTargets', () => {
  it('reads the target of an import or a backup', () => {
    expect(copyTargets(['ingest', '--source', '/a', '--target', '/b'])).toEqual(['/b']);
    expect(copyTargets(['backup', '--card', 'v', '--to', '/c'])).toEqual(['/c']);
    expect(copyTargets(['backup', '--card', 'v'])).toEqual([]);
  });
});

describe('updatedAgo', () => {
  it('counts seconds, then minutes', () => {
    expect(updatedAgo(null, 0)).toBe('');
    expect(updatedAgo(1000, 3000)).toBe('Updated just now');
    expect(updatedAgo(0, 12_000)).toBe('Updated 12 s ago');
    expect(updatedAgo(0, 150_000)).toBe('Updated 3 min ago');
  });
});
