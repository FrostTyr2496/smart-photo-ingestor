import { describe, expect, it } from 'vitest';
import { planFolders, readItems } from './planTree';

const it1 = (dst_rel: string, size: number) => ({ src_rel: 'DCIM/x', dst_rel, size });

describe('readItems', () => {
  it('reads triples and drops junk', () => {
    expect(readItems([['a', 'b/c.NEF', 5], 'x', [1, 2]])).toEqual([{ src_rel: 'a', dst_rel: 'b/c.NEF', size: 5 }]);
    expect(readItems(undefined)).toEqual([]);
  });
});

describe('planFolders', () => {
  const items = [it1('2026/Trip/a.NEF', 10), it1('2026/Trip/b.NEF', 20), it1('2026/Trip/raw/c.NEF', 5), it1('2026/Other/d.NEF', 1)];
  it('counts files and bytes per folder, nested', () => {
    const f = planFolders(items);
    expect(f.map((x) => [x.path, x.depth, x.files, x.bytes])).toEqual([
      ['2026', 0, 4, 36],
      ['2026/Other', 1, 1, 1],
      ['2026/Trip', 1, 3, 35],
      ['2026/Trip/raw', 2, 1, 5],
    ]);
  });
  it('hides the children of a closed folder', () => {
    expect(planFolders(items, new Set(['2026/Trip/'])).map((x) => x.path)).toEqual(['2026', '2026/Other', '2026/Trip']);
  });
  it('merges a single-folder chain into one row', () => {
    const f = planFolders([it1('A/B/x.NEF', 3)]);
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ name: 'A/B', path: 'A/B', files: 1 });
  });
});
