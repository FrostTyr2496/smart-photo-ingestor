import { describe, expect, it } from 'vitest';
import { followOf, groupRuns } from './runGroups';

const run = (key: string, mounts: string[], extra: { jobId?: number; follow?: number } = {}) => ({ key, mounts, ...extra });

describe('followOf', () => {
  it('reads --follow', () => {
    expect(followOf(['backup', '--follow', '12'])).toBe(12);
    expect(followOf(['backup'])).toBeUndefined();
    expect(followOf(['backup', '--follow', 'x'])).toBeUndefined();
  });
});

describe('groupRuns', () => {
  it('shows a two-drive run once', () => {
    const g = groupRuns([run('a', ['/c', '/w'])]);
    expect(g).toHaveLength(1);
    expect(g[0]!.mounts).toEqual(['/c', '/w']);
  });
  it('nests the follower under its import and merges drives', () => {
    const g = groupRuns([run('imp', ['/c', '/w'], { jobId: 7 }), run('bk', ['/w', '/s'], { follow: 7 })]);
    expect(g).toHaveLength(1);
    expect(g[0]!.subs.map((s) => s.key)).toEqual(['bk']);
    expect(g[0]!.mounts).toEqual(['/c', '/w', '/s']);
  });
  it('keeps a follower separate when its parent is not live or unknown', () => {
    const g = groupRuns([run('imp', ['/c', '/w']), run('bk', ['/w', '/s'], { follow: 7 })]);
    expect(g.map((x) => x.main.key)).toEqual(['imp', 'bk']);
  });
  it('dedupes mounts with the supplied comparison', () => {
    const g = groupRuns([run('a', ['/x/', '/x'])], (p, q) => p.replace(/\/$/, '') === q.replace(/\/$/, ''));
    expect(g[0]!.mounts).toHaveLength(1);
  });
});
