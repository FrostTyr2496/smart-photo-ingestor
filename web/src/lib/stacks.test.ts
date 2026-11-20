import { describe, expect, it } from 'vitest';
import { effectivePlan, fromServerRows, isHighConfidence, toServerRows, type Decision, type Detail, type EffShoot, type Plan, type PlanShoot, type ShootDetails } from './review';
import { composeName, confirmDecisions, fade, initialDraft, matchesQueueFilter, minConfidence, queueProgress, sortShoots } from './stacks';

const det = (value: string | null, conf: number, alts: Detail['alternatives'] = []): Detail => ({ value, source: value ? 'phone_venue' : null, confidence: conf, evidence: 2, alternatives: alts });
const details = (place: [string | null, number], subject: [string | null, number]): ShootDetails => ({
  place: det(...place),
  subject: det(...subject),
  people: { value: ['Ana Example'], source: 'phone_person', confidence: 0.8, evidence: 2, names: [{ value: 'Ana Example', source: 'phone_person', confidence: 0.8 }], alternatives: [] },
});

const shoot = (id: string, first: number, over: Partial<PlanShoot> = {}): PlanShoot => ({
  proposal_id: id,
  trip_id: null,
  name: { value: `Shoot ${id}`, source: 'folder', alternatives: [] },
  shoot_date: '2026-03-09',
  start_instant: `2026-03-0${first}T10:00:00+00:00`,
  end_instant: `2026-03-0${first}T12:00:00+00:00`,
  wall: { start: null, end: null },
  local_offset: { value: '+00:00', camera: '+00:00', source: 'phone', changes: false },
  group_ids: [first * 10, first * 10 + 1, first * 10 + 2],
  counts: { frames: first * 10, by_body: {}, located: 0 },
  sample_group_ids: [],
  centroid: null,
  points: [],
  gaps: [],
  flags: [],
  locked: false,
  members: { core_sessions: [[first * 10, first * 10 + 1], [first * 10 + 2, first * 10 + 2]] },
  candidates: [],
  details: details(['Dune Lookout', 0.8], ['Astro', 0.7]),
  ...over,
});

const cand = (first: number, score: number, beyond = false) => ({
  session: [first, first + 1] as [number, number],
  score,
  band: (beyond ? 'beyond_cutoff' : score >= 0.5 ? 'likely' : 'maybe') as 'likely' | 'maybe' | 'beyond_cutoff',
  reasons: [{ term: 'time', text: '+1 h after' }],
  home_proposal: null,
  frames: 4,
  start_instant: '',
  end_instant: '',
  bodies: {},
  ...(beyond ? { beyond_cutoff: true } : {}),
});

const plan = (shoots: PlanShoot[]): Plan => ({
  plan_id: 'shoots-20260101-000000',
  created_at: '2026-01-01T00:00:00+00:00',
  params: { pretick_min: 0.5, show_min: 0.1, beta: true },
  summary: { frames: 0, shoots: shoots.length, trips: 0, unassigned: 0, locked: 0, flagged: 0 },
  trips: [],
  shoots,
  unassigned_sessions: [{ session: [900, 901], frames: 40, start_instant: '', end_instant: '' }],
});
const eff = (p: Plan, ds: Decision[] = []) => effectivePlan(p, ds);
const mounted = new Set<string>();

describe('beta display', () => {
  it('fades from 1 at full confidence to 0.25 at none', () => {
    expect(fade(1)).toBe(1);
    expect(fade(0)).toBe(0.25);
    expect(fade(0.5)).toBeCloseTo(0.625);
    expect(fade(7)).toBe(1);
  });
});

describe('queue', () => {
  const a = shoot('s:a', 1, { details: details(['P', 0.9], ['S', 0.9]) });
  const b = shoot('s:b', 2, { details: details(['P', 0.9], ['S', 0.3]) });
  const c = shoot('s:c', 3, { details: details(['P', 0.9], ['S', 0.9]), candidates: [cand(500, 0.6), cand(600, 0.3), cand(700, 0.05, true)] });
  const d = shoot('s:d', 4, { details: details([null, 0], [null, 0]), flags: ['tz_change'] });
  const e = eff(plan([a, b, c, d]));
  const ids = (l: EffShoot[]) => l.map((s) => s.proposal_id);

  it('minimum confidence counts a missing value as zero', () => {
    expect(minConfidence(e.shoots[0]!)).toBe(0.9);
    expect(minConfidence(e.shoots[1]!)).toBe(0.3);
    expect(minConfidence(e.shoots[3]!)).toBe(0);
  });
  it('sorts easiest first: high confidence, then few candidates, then time', () => {
    expect(ids(sortShoots(e.shoots, 'easiest', mounted))).toEqual(['s:a', 's:c', 's:b', 's:d']);
  });
  it('sorts biggest and newest', () => {
    expect(ids(sortShoots(e.shoots, 'biggest', mounted))).toEqual(['s:d', 's:c', 's:b', 's:a']);
    expect(ids(sortShoots(e.shoots, 'newest', mounted))).toEqual(['s:d', 's:c', 's:b', 's:a']);
  });
  it('sorts oldest first, and reverse flips any order', () => {
    expect(ids(sortShoots(e.shoots, 'oldest', mounted))).toEqual(['s:a', 's:b', 's:c', 's:d']);
    expect(ids(sortShoots(e.shoots, 'newest', mounted, true))).toEqual(['s:a', 's:b', 's:c', 's:d']);
    expect(ids(sortShoots(e.shoots, 'easiest', mounted, true))).toEqual(['s:d', 's:b', 's:c', 's:a']);
  });
  it('sorts connected first', () => {
    const v = shoot('s:v', 5, { volumes: [{ volume_id: 'v1', label: 'X', frames: 9 }] });
    const l = eff(plan([a, v])).shoots;
    expect(ids(sortShoots(l, 'connected', new Set(['v1'])))).toEqual(['s:v', 's:a']);
  });
  it('filters by chip; frames in question ignore the beyond-cut-off ones', () => {
    const match = (f: Parameters<typeof matchesQueueFilter>[1]) => e.shoots.filter((s) => matchesQueueFilter(s, f, mounted)).map((s) => s.proposal_id);
    expect(match('needs_subject')).toEqual(['s:b', 's:d']);
    expect(match('in_question')).toEqual(['s:c']);
    expect(match('tz_change')).toEqual(['s:d']);
    expect(match('bodies')).toEqual([]);
    expect(match('no_previews')).toEqual(['s:a', 's:b', 's:c', 's:d']);
  });
  it('progress counts accepted shoots and unassigned frames', () => {
    const q = eff(plan([a, b]), [{ seq: 1, kind: 'accept', id: 's:a' }]);
    expect(queueProgress(q.shoots, 40)).toEqual({ done: 1, total: 2, unassigned: 40 });
  });
  it('accept all high-confidence takes only shoots with high place and subject and no flags', () => {
    expect(e.shoots.filter(isHighConfidence).map((s) => s.proposal_id)).toEqual(['s:a', 's:c']);
    const rows = toServerRows({ kind: 'accept_high' }, e, 'b1');
    expect(rows.map((r) => [r.proposal_id, r.kind, r.payload.ui])).toEqual([['s:a', 'accept', 'accept_high'], ['s:c', 'accept', 'accept_high']]);
    const back = fromServerRows(rows.map((r, i) => ({ ...r, decision_id: i + 1 })));
    expect(back).toHaveLength(1);
    expect(back[0]!.kind).toBe('accept_high');
    const after = eff(plan([a, b, c, d]), back);
    expect(after.shoots.map((s) => s.state)).toEqual(['accepted', 'undecided', 'accepted', 'undecided']);
    // candidates stay open: nothing was included
    expect(after.shoots[2]!.included).toEqual([]);
  });
});

describe('stack decisions', () => {
  const s0 = shoot('s:a', 1, { candidates: [cand(500, 0.6), cand(600, 0.3), cand(700, 0.05, true)] });
  const p = plan([s0]);

  it('pre-ticks candidates at or above the cut-off only', () => {
    const d = initialDraft(eff(p).shoots[0]!, 0.5);
    expect(d.ticked).toEqual([[500, 501]]);
  });

  it('composes the name from place and subject', () => {
    expect(composeName('Dune Lookout', 'Astro')).toBe('Dune Lookout · Astro');
    expect(composeName('Dune Lookout', null)).toBe('Dune Lookout');
    expect(composeName(null, null)).toBe('');
  });

  it('confirm stores excludes, includes, details, the name and the confirm, in that order', () => {
    const s = eff(p).shoots[0]!;
    const d = initialDraft(s, 0.5);
    d.excluded = [[10, 11]];
    d.ticked = [[500, 501], [600, 601]];
    d.subject = 'Milky Way';
    d.subjectSource = 'user';
    d.name = 'Dune Lookout · Milky Way';
    const ds = confirmDecisions(s, d);
    expect(ds.map((x) => x.kind)).toEqual(['exclude', 'include', 'include', 'detail', 'detail', 'rename', 'confirm']);
    const rows = ds.flatMap((x) => toServerRows(x, eff(p), 'b'));
    expect(rows[0]).toEqual({ proposal_id: 's:a', kind: 'exclude', payload: { session: [10, 11] } });
    expect(rows[1]).toEqual({ proposal_id: 's:a', kind: 'include', payload: { session: [500, 501] } });
    expect(rows[3]).toEqual({ proposal_id: 's:a', kind: 'detail', payload: { field: 'place', value: 'Dune Lookout', source: 'phone_venue' } });
    expect(rows[4]).toEqual({ proposal_id: 's:a', kind: 'detail', payload: { field: 'subject', value: 'Milky Way', source: 'user' } });
    expect(rows[6]).toEqual({ proposal_id: 's:a', kind: 'accept', payload: { confirmed: true } });
  });

  it('replays stored rows: details, members and the confirmed accept', () => {
    const stored = [
      { decision_id: 1, proposal_id: 's:a', kind: 'exclude' as const, payload: { session: [10, 11] } },
      { decision_id: 2, proposal_id: 's:a', kind: 'include' as const, payload: { session: [500, 501] } },
      { decision_id: 3, proposal_id: 's:a', kind: 'detail' as const, payload: { field: 'subject', value: 'Milky Way' } },
      { decision_id: 4, proposal_id: 's:a', kind: 'detail' as const, payload: { field: 'people', value: [] } },
      { decision_id: 5, proposal_id: 's:a', kind: 'accept' as const, payload: { confirmed: true } },
    ];
    const s = eff(p, fromServerRows(stored)).shoots[0]!;
    expect(s.excluded).toEqual([[10, 11]]);
    expect(s.included).toEqual([[500, 501]]);
    expect(s.details!.subject).toMatchObject({ value: 'Milky Way', source: 'user', confidence: 1 });
    expect(s.details!.subject.alternatives[0]).toMatchObject({ value: 'Astro' });
    expect(s.details!.people.value).toEqual([]);
    expect(s.state).toBe('accepted');
    expect(s.confirmed).toBe(true);
    // an included candidate is no longer "in question"
    expect(matchesQueueFilter(s, 'in_question', mounted)).toBe(true); // 600-601 is still open
  });
});
