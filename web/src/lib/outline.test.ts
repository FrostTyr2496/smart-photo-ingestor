import { describe, expect, it } from 'vitest';
import { countsLine, dateRange, onlyShoot, railOf, sectionsOf, shootLine, titleShade, titleTone, type OutlineItem, type OutlineShoot } from './outline';

const shoot = (key: string, over: Partial<OutlineShoot> = {}): OutlineShoot => ({
  key,
  shoot_id: /^s\d+$/.test(key) ? Number(key.slice(1)) : null,
  proposal_id: /^s\d+$/.test(key) ? null : key,
  state: /^s\d+$/.test(key) ? 'accepted' : 'proposed',
  title: `Title ${key}`,
  name: key,
  place: null,
  subject: null,
  shoot_date: '2023-09-09',
  wall: { start: '2023-09-09T08:05:00', end: '2023-09-09T11:30:00' },
  groups: 10,
  bodies: ['Z6'],
  volumes: [{ volume_id: 'v', label: 'X9 Pro', frames: 10 }],
  sample_group_ids: [],
  days: [{ day: '2023-09-09', groups: 10 }],
  trip_key: null,
  ...over,
});

const items: OutlineItem[] = [
  { kind: 'day', key: 'loose:2024-01-02', day: '2024-01-02', groups: 3 },
  { kind: 'trip', key: 't1', name: 'Coast week', state: 'accepted', start_date: '2023-09-07', end_date: '2023-09-10', groups: 20, shoots: [shoot('s1'), shoot('s:2')] },
  { kind: 'shoot', ...shoot('s:3', { shoot_date: '2022-05-01' }) },
  { kind: 'day', key: 'loose:undated', day: 'undated', groups: 1 },
];

describe('outline helpers (rv-47)', () => {
  it('formats date ranges', () => {
    expect(dateRange('2023-09-09', '2023-09-12')).toBe('9–12 Sep 2023');
    expect(dateRange('2023-09-30', '2023-10-02')).toBe('30 Sep – 2 Oct 2023');
    expect(dateRange('2023-12-30', '2024-01-02')).toBe('30 Dec 2023 – 2 Jan 2024');
    expect(dateRange('2023-09-09', '2023-09-09')).toBe('9 Sep 2023');
    expect(dateRange(null, null)).toBe('');
  });
  it('shades proposed titles and not accepted ones', () => {
    expect(titleTone('accepted')).toBe('text-ink');
    expect(titleTone('proposed')).toContain('text-maybe');
  });
  it('flattens the loadable sections in display order', () => {
    expect(sectionsOf(items).map((s) => s.key)).toEqual(['loose:2024-01-02', 's1', 's:2', 's:3', 'loose:undated']);
    expect(sectionsOf(items)[1]!.days).toEqual(['2023-09-09']);
  });
  it('builds the rail: years, then trips and lone shoots', () => {
    const r = railOf(items);
    expect(r.years.map((y) => y.year)).toEqual(['2024', '2023', '2022']);
    expect(r.years[1]!.entries).toMatchObject([{ key: 't1', label: 'Coast week', state: 'accepted' }]);
    expect(r.years[1]!.entries[0]!.sub).toMatch(/photo/);
    expect(r.years[2]!.entries[0]).toMatchObject({ key: 's:3', label: 'Title s:3', state: 'proposed' });
    expect(r.undated).toBe('loose:undated');
  });
  it('keeps one shoot for the Shoot filter', () => {
    const out = onlyShoot(items, 1);
    expect(out).toHaveLength(1);
    const first = out[0]!;
    expect(first.kind === 'trip' && first.shoots.map((s) => s.key)).toEqual(['s1']);
  });
  it('writes the quiet shoot line and counts', () => {
    expect(shootLine(shoot('s1'))).toContain('08:05–11:30 · Z6 · X9 Pro');
    expect(countsLine({ groups: 5 })).toBe('5 photos');
    expect(countsLine({ groups: 5, files: 312, bytes: 9.8e9 })).toContain('312 files');
  });
});

describe('titleShade', () => {
  it('draws accepted names in ink and proposals in amber, fading with confidence', () => {
    expect(titleShade('accepted', 0.1)).toEqual({ className: 'text-ink', opacity: 1, hint: 'Accepted' });
    expect(titleShade('proposed', 1)).toMatchObject({ className: 'text-maybe', opacity: 1, hint: 'Proposed · 100% confidence' });
    expect(titleShade('proposed', 0).opacity).toBe(0.3);
    expect(titleShade('proposed', 0.5).opacity).toBe(0.65);
    expect(titleShade('proposed', null)).toMatchObject({ opacity: 0.6, hint: 'Proposed · Re-plan for a confidence' });
  });
});
