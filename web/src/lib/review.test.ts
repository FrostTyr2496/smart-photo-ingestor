import { describe, expect, it } from 'vitest';
import {
  addDecision,
  describeAll,
  effectivePlan,
  matchesFilter,
  connectedFrames,
  reviewSections,
  fromServerRows,
  sha1Hex,
  shootId,
  toServerRows,
  type DecisionRow,
  type StoredDecision,
  tally,
  undoDecision,
  type Decision,
  type NewDecision,
  type Plan,
  type PlanShoot,
} from './review';

const shoot = (id: string, trip: string | null, ids: number[], over: Partial<PlanShoot> = {}): PlanShoot => ({
  proposal_id: id,
  trip_id: trip,
  name: { value: `Shoot ${id}`, source: 'folder', alternatives: [{ value: `Alt ${id}`, source: 'phone' }] },
  shoot_date: '2026-03-09',
  start_instant: `2026-03-0${ids[0]! % 9}T10:00:00+00:00`,
  end_instant: `2026-03-0${ids[0]! % 9}T12:00:00+00:00`,
  wall: { start: '2026-03-09T03:00:00-07:00', end: '2026-03-09T05:00:00-07:00' },
  local_offset: { value: '-07:00', camera: '-04:00', source: 'phone', changes: true },
  group_ids: ids,
  counts: { frames: ids.length * 10, by_body: { Z6: ids.length * 6, Z6II: ids.length * 4 }, located: ids.length * 5 },
  sample_group_ids: ids.slice(0, 3),
  centroid: { lat: 0, lon: 0 },
  points: ids.map((g, i) => [i * 0.1, i * 0.1] as [number, number]),
  gaps: [{ after_group_id: ids[1]!, gap_s: 3600 }],
  flags: [],
  locked: false,
  ...over,
});

const plan: Plan = {
  plan_id: 'p1',
  created_at: '2026-11-18T00:00:00+00:00',
  params: {},
  summary: { frames: 0, shoots: 3, trips: 2, unassigned: 1, locked: 0, flagged: 1 },
  trips: [
    { proposal_id: 't:1', name: { value: 'Trip One', source: 'phone', alternatives: [] }, start_date: '2026-03-01', end_date: '2026-03-03', shoot_ids: ['s:a', 's:b'], flags: [] },
    { proposal_id: 't:2', name: { value: 'Trip Two', source: 'phone', alternatives: [] }, start_date: '2026-03-05', end_date: '2026-03-05', shoot_ids: ['s:c'], flags: [] },
  ],
  shoots: [
    shoot('s:a', 't:1', [1, 2, 3, 4]),
    shoot('s:b', 't:1', [5, 6, 7, 8, 9, 10], { flags: ['weak_name'], start_instant: '2026-03-02T10:00:00+00:00', end_instant: '2026-03-02T12:00:00+00:00' }),
    shoot('s:c', 't:2', [11, 12], { start_instant: '2026-03-05T10:00:00+00:00', end_instant: '2026-03-05T12:00:00+00:00' }),
  ],
};
// distinct, ordered starts for the first shoot too
plan.shoots[0]!.start_instant = '2026-03-01T10:00:00+00:00';
plan.shoots[0]!.end_instant = '2026-03-01T12:00:00+00:00';

// the planner's ids for the halves of s:b (groups 5-10) cut after 6
const B1 = shootId(5, 6);
const B2 = shootId(7, 10);

const run = (...ds: NewDecision[]): Decision[] => ds.reduce<Decision[]>((acc, d) => addDecision(acc, d), []);
const get = (ds: Decision[], id: string) => effectivePlan(plan, ds).shoots.find((s) => s.proposal_id === id)!;

describe('review reducer', () => {
  it('starts undecided with everything in its trip', () => {
    const p = effectivePlan(plan, []);
    expect(p.shoots.map((s) => s.state)).toEqual(['undecided', 'undecided', 'undecided']);
    expect(p.trips.map((t) => t.shoot_ids)).toEqual([['s:a', 's:b'], ['s:c']]);
  });

  it('accepts and skips a shoot', () => {
    expect(get(run({ kind: 'accept', id: 's:a' }), 's:a').state).toBe('accepted');
    expect(get(run({ kind: 'skip', id: 's:a' }), 's:a').state).toBe('skipped');
    expect(get(run({ kind: 'skip', id: 's:a' }, { kind: 'accept', id: 's:a' }), 's:a').state).toBe('accepted');
  });

  it('renames a shoot, keeping the old name as an alternative, and a trip', () => {
    const ds = run({ kind: 'rename', id: 's:a', value: 'Harbour Moonrise' }, { kind: 'rename', id: 't:1', value: 'Northland' });
    const s = get(ds, 's:a');
    expect(s.name).toEqual({ value: 'Harbour Moonrise', source: 'user', alternatives: [{ value: 'Shoot s:a', source: 'folder' }, { value: 'Alt s:a', source: 'phone' }] });
    expect(s.renamed).toBe(true);
    expect(effectivePlan(plan, ds).trips[0]!.name.value).toBe('Northland');
  });

  it('choosing an alternative chip keeps its source and drops it from the alternatives', () => {
    const s = get(run({ kind: 'rename', id: 's:a', value: 'Alt s:a', source: 'phone' }), 's:a');
    expect(s.name.value).toBe('Alt s:a');
    expect(s.name.source).toBe('phone');
    expect(s.name.alternatives.map((a) => a.value)).toEqual(['Shoot s:a']);
  });

  it('ignores a blank rename', () => {
    expect(get(run({ kind: 'rename', id: 's:a', value: '  ' }), 's:a').renamed).toBe(false);
  });

  it('rejecting the current name promotes the first alternative and joins the stoplist', () => {
    const ds = run({ kind: 'reject_name', id: 's:a', value: 'Shoot s:a' });
    const s = get(ds, 's:a');
    expect(s.name.value).toBe('Alt s:a');
    expect(s.rejected).toEqual(['Shoot s:a']);
    expect(effectivePlan(plan, ds).stoplist).toEqual(['Shoot s:a']);
  });

  it('rejecting an alternative removes only that chip', () => {
    const s = get(run({ kind: 'reject_name', id: 's:a', value: 'Alt s:a' }), 's:a');
    expect(s.name.value).toBe('Shoot s:a');
    expect(s.name.alternatives).toEqual([]);
  });

  it('splits at a gap into two shoots with the right group ranges and counts', () => {
    const ds = run({ kind: 'split', id: 's:b', after_group_id: 6 });
    const p = effectivePlan(plan, ds);
    const left = p.shoots.find((s) => s.proposal_id === B1)!;
    const right = p.shoots.find((s) => s.proposal_id === B2)!;
    expect(p.shoots.some((s) => s.proposal_id === 's:b')).toBe(false);
    expect(left.group_ids).toEqual([5, 6]);
    expect(right.group_ids).toEqual([7, 8, 9, 10]);
    expect(left.counts.frames + right.counts.frames).toBe(60);
    expect(left.counts.frames).toBe(20);
    expect(left.counts.by_body.Z6! + right.counts.by_body.Z6!).toBe(36);
    expect(left.counts.located + right.counts.located).toBe(30);
    expect(left.derived && right.derived).toBe(true);
    expect(left.sample_group_ids.every((g) => left.group_ids.includes(g))).toBe(true);
    expect(right.sample_group_ids.every((g) => right.group_ids.includes(g))).toBe(true);
    expect(left.gaps.concat(right.gaps).some((g) => g.after_group_id === 6)).toBe(false);
    expect(left.points.length + right.points.length).toBe(6);
    expect(left.state).toBe('undecided');
    expect(p.trips[0]!.shoot_ids).toEqual(['s:a', B1, B2]);
  });

  it('does nothing for a split that is not between two groups', () => {
    expect(effectivePlan(plan, run({ kind: 'split', id: 's:b', after_group_id: 10 })).shoots).toHaveLength(3);
    expect(effectivePlan(plan, run({ kind: 'split', id: 's:b', after_group_id: 999 })).shoots).toHaveLength(3);
  });

  it('merges with the next shoot in the same trip, adding the bridging gap', () => {
    const p = effectivePlan(plan, run({ kind: 'merge', id: 's:a', with: 'next' }));
    expect(p.shoots).toHaveLength(2);
    const m = p.shoots[0]!;
    expect(m.group_ids).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(m.counts.frames).toBe(100);
    expect(m.flags).toEqual(['weak_name']);
    expect(m.name.value).toBe('Shoot s:a');
    expect(m.gaps.some((g) => g.after_group_id === 4)).toBe(true);
    expect(p.trips[0]!.shoot_ids).toHaveLength(1);
  });

  it('merges with the previous shoot, and not across trips', () => {
    expect(effectivePlan(plan, run({ kind: 'merge', id: 's:b', with: 'prev' })).shoots).toHaveLength(2);
    expect(effectivePlan(plan, run({ kind: 'merge', id: 's:b', with: 'next' })).shoots).toHaveLength(3);
    expect(effectivePlan(plan, run({ kind: 'merge', id: 's:c', with: 'prev' })).shoots).toHaveLength(3);
  });

  it('moves a shoot to another trip, out of any trip, and back', () => {
    let p = effectivePlan(plan, run({ kind: 'move', id: 's:c', trip_id: 't:1' }));
    expect(p.trips[0]!.shoot_ids).toEqual(['s:a', 's:b', 's:c']);
    expect(p.trips[1]!.shoot_ids).toEqual([]);
    p = effectivePlan(plan, run({ kind: 'move', id: 's:a', trip_id: null }));
    expect(p.shoots.find((s) => s.proposal_id === 's:a')!.trip_id).toBeNull();
    p = effectivePlan(plan, run({ kind: 'move', id: 's:c', trip_id: 't:1' }, { kind: 'move', id: 's:c', trip_id: 't:2' }));
    expect(p.trips[1]!.shoot_ids).toEqual(['s:c']);
    expect(effectivePlan(plan, run({ kind: 'move', id: 's:c', trip_id: 't:nope' })).trips[1]!.shoot_ids).toEqual(['s:c']);
  });

  it('accepts a trip, all of its shoots', () => {
    const p = effectivePlan(plan, run({ kind: 'accept_trip', id: 't:1' }));
    expect(p.shoots.map((s) => s.state)).toEqual(['accepted', 'accepted', 'undecided']);
  });

  it('accepts all unflagged and leaves flagged, skipped and locked alone', () => {
    const locked: Plan = { ...plan, shoots: [...plan.shoots, shoot('s:d', null, [20, 21], { locked: true, start_instant: '2026-03-06T10:00:00+00:00' })] };
    const ds = run({ kind: 'skip', id: 's:c' }, { kind: 'accept_all_unflagged' });
    const p = effectivePlan(locked, ds);
    const st = Object.fromEntries(p.shoots.map((s) => [s.proposal_id, s.state]));
    expect(st).toEqual({ 's:a': 'accepted', 's:b': 'undecided', 's:c': 'skipped', 's:d': 'undecided' });
  });

  it('never changes a locked shoot', () => {
    const locked: Plan = { ...plan, shoots: plan.shoots.map((s) => (s.proposal_id === 's:a' ? { ...s, locked: true } : s)) };
    const p = effectivePlan(locked, run({ kind: 'accept', id: 's:a' }, { kind: 'rename', id: 's:a', value: 'X' }, { kind: 'skip', id: 's:a' }));
    expect(p.shoots.find((s) => s.proposal_id === 's:a')!.state).toBe('undecided');
    expect(p.shoots.find((s) => s.proposal_id === 's:a')!.name.value).toBe('Shoot s:a');
  });
});

describe('undo and bookkeeping', () => {
  it('numbers decisions and undoes one by sequence number', () => {
    const ds = run({ kind: 'accept', id: 's:a' }, { kind: 'skip', id: 's:c' });
    expect(ds.map((d) => d.seq)).toEqual([1, 2]);
    const undone = undoDecision(ds, 1);
    expect(get(undone, 's:a').state).toBe('undecided');
    expect(get(undone, 's:c').state).toBe('skipped');
    expect(addDecision(undone, { kind: 'accept', id: 's:a' })[1]!.seq).toBe(3);
  });

  it('undoing a split turns later decisions on its halves into no-ops', () => {
    const ds = run({ kind: 'split', id: 's:b', after_group_id: 6 }, { kind: 'accept', id: B1 });
    expect(effectivePlan(plan, ds).shoots.find((s) => s.proposal_id === B1)!.state).toBe('accepted');
    const p = effectivePlan(plan, undoDecision(ds, 1));
    expect(p.shoots).toHaveLength(3);
    expect(p.shoots.every((s) => s.state === 'undecided')).toBe(true);
  });

  it('describes decisions with the names at the time', () => {
    const ds = run({ kind: 'rename', id: 's:a', value: 'Moonrise' }, { kind: 'accept', id: 's:a' });
    expect(describeAll(plan, ds).map((d) => d.text)).toEqual(['Rename Shoot s:a to "Moonrise"', 'Accept Moonrise']);
  });

  it('filters and tallies', () => {
    const p = effectivePlan(plan, run({ kind: 'accept', id: 's:a' }));
    expect(p.shoots.filter((s) => matchesFilter(s, 'flagged')).map((s) => s.proposal_id)).toEqual(['s:b']);
    expect(p.shoots.filter((s) => matchesFilter(s, 'accepted')).map((s) => s.proposal_id)).toEqual(['s:a']);
    expect(p.shoots.filter((s) => matchesFilter(s, 'undecided'))).toHaveLength(2);
    expect(tally(p)).toMatchObject({ shoots: 3, trips: 2, accepted: 1, undecided: 2, flagged: 1 });
  });
});

describe('review order (sh-3b)', () => {
  const vol = (volume_id: string, frames: number) => ({ volume_id, label: volume_id, frames });
  const p2: Plan = {
    ...plan,
    shoots: [
      { ...plan.shoots[0]!, volumes: [vol('arch', 40)] },
      { ...plan.shoots[1]!, volumes: [vol('arch', 50), vol('work', 10)] },
      { ...plan.shoots[2]!, volumes: [vol('arch', 20)] },
      shoot('s:d', null, [20, 21], { start_instant: '2026-03-06T10:00:00+00:00', volumes: [vol('arch', 20)] }),
      shoot('s:e', null, [22, 23], { start_instant: '2026-03-07T10:00:00+00:00', volumes: [vol('work', 20)] }),
    ],
  };
  const ids = (m: string[], on: boolean) => reviewSections(effectivePlan(p2, []), new Set(m), on).map((x) => [x.trip?.proposal_id ?? null, x.shoots.map((s) => s.proposal_id)]);

  it('counts frames on connected drives', () => {
    expect(connectedFrames(p2.shoots[1]!, new Set(['work']))).toBe(10);
    expect(connectedFrames(plan.shoots[0]!, new Set(['work']))).toBe(0);
  });

  it('keeps time order when off or when nothing is connected', () => {
    const plain = [['t:1', ['s:a', 's:b']], ['t:2', ['s:c']], [null, ['s:d', 's:e']]];
    expect(ids(['work'], false)).toEqual(plain);
    expect(ids([], true)).toEqual(plain);
  });

  it('puts trips and loose shoots on a connected drive first, shoots inside a trip in time order', () => {
    expect(ids(['work'], true)).toEqual([['t:1', ['s:a', 's:b']], ['t:2', ['s:c']], [null, ['s:e', 's:d']]]);
    // only trip 2 (and loose s:d) on the connected drive
    const q: Plan = { ...p2, shoots: p2.shoots.map((s) => (s.proposal_id === 's:c' ? { ...s, volumes: [vol('work', 20)] } : { ...s, volumes: s.proposal_id === 's:d' ? [vol('work', 20)] : [vol('arch', 1)] })) };
    expect(reviewSections(effectivePlan(q, []), new Set(['work']), true).map((x) => x.trip?.proposal_id ?? null)).toEqual(['t:2', 't:1', null]);
  });

  it('split keeps the drives and merge adds them up', () => {
    const merged = effectivePlan(p2, run({ kind: 'merge', id: 's:a', with: 'next' })).shoots[0]!;
    expect(merged.volumes).toEqual([vol('arch', 90), vol('work', 10)]);
    const halves = effectivePlan(p2, run({ kind: 'split', id: 's:b', after_group_id: 6 })).shoots.filter((s) => [B1, B2].includes(s.proposal_id));
    expect(halves.map((h) => h.volumes)).toEqual([p2.shoots[1]!.volumes, p2.shoots[1]!.volumes]);
  });
});

describe('stored decisions (sh-2)', () => {
  it('ids match the planner (sha1 of "first-last")', () => {
    expect(sha1Hex('')).toBe('da39a3ee5e6b4b0d3255bfef95601890afd80709');
    expect(sha1Hex('a'.repeat(100))).toBe('7f9000257a4918d7072655ea468540cdcbd42e0c');
    expect(shootId(101, 140)).toBe('s:ec9e106b0943e1a5');
    expect(B1).toBe('s:5ea949b329c4990e');
    expect(effectivePlan(plan, run({ kind: 'merge', id: 's:a', with: 'next' })).shoots[0]!.proposal_id).toBe(shootId(1, 10));
  });

  it('reads accepted and renamed shoots from a re-plan', () => {
    const q: Plan = { ...plan, shoots: [{ ...plan.shoots[0]!, state: 'accepted', name: { value: 'Mine', source: 'user', alternatives: [] } }, ...plan.shoots.slice(1)] };
    const s = effectivePlan(q, []).shoots[0]!;
    expect([s.state, s.renamed]).toEqual(['accepted', true]);
  });

  it('expands the batch actions into accepts and folds them back', () => {
    const p = effectivePlan(plan, []);
    const trip = toServerRows({ kind: 'accept_trip', id: 't:1' }, p, 'b1');
    expect(trip.map((r) => [r.proposal_id, r.kind])).toEqual([['s:a', 'accept'], ['s:b', 'accept']]);
    const unflagged = toServerRows({ kind: 'accept_all_unflagged' }, p, 'b2');
    expect(unflagged.map((r) => r.proposal_id)).toEqual(['s:a', 's:c']); // s:b is flagged
    expect(toServerRows({ kind: 'merge', id: 's:a', with: 'next' }, p, 'x')).toEqual([{ proposal_id: 's:a', kind: 'merge', payload: { with: 'next' } }]);

    let n = 10;
    const stored: StoredDecision[] = [
      { proposal_id: 's:b', kind: 'split', payload: { after_group_id: 6 } } as DecisionRow,
      ...trip,
      { proposal_id: B1, kind: 'rename', payload: { value: 'Harbour Moonrise' } } as DecisionRow,
      ...unflagged,
    ].map((r) => ({ ...r, decision_id: ++n }));
    const ds = fromServerRows(stored);
    expect(ds.map((d) => [d.kind, d.seq, d.rows])).toEqual([
      ['split', 11, [11]],
      ['accept_trip', 12, [12, 13]],
      ['rename', 14, [14]],
      ['accept_all_unflagged', 15, [15, 16]],
    ]);
    const eff = effectivePlan(plan, ds);
    expect(eff.shoots.find((s) => s.proposal_id === B1)!.name.value).toBe('Harbour Moonrise');
    expect(eff.shoots.every((s) => s.state === 'accepted' || s.flags.length > 0)).toBe(true);
  });
});
