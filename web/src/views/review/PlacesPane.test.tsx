import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { addDecision, describeAll, effectivePlan, fromServerRows, toServerRows, type Plan, type PlanShoot } from '../../lib/review';
import { PlacesPane, initialTicks } from './PlacesPane';

vi.mock('./MiniMap', () => ({ MiniMap: ({ points }: { points: unknown[] }) => <div data-testid="map">{points.length} pins</div> }));

const shoot = (id: string, date: string, locked = false): PlanShoot => ({
  proposal_id: id, trip_id: null, name: { value: `Walk ${date}`, source: 'folder', alternatives: [] }, shoot_date: date,
  start_instant: `${date}T10:00:00+00:00`, end_instant: `${date}T11:00:00+00:00`, wall: { start: null, end: null },
  local_offset: { value: '+00:00', camera: '+00:00', source: 'camera', changes: false }, group_ids: [1], counts: { frames: 20, by_body: {}, located: 1 },
  sample_group_ids: [], centroid: { lat: 1, lon: 2 }, points: [], gaps: [], flags: [], locked,
  ...(locked ? {} : { details: {
    place: { value: 'Pier Point', source: 'phone_place', confidence: 0.8, evidence: 1, alternatives: [] },
    subject: { value: null, source: null, confidence: 0, evidence: 0, alternatives: [] },
    people: { value: [], source: null, confidence: 0, evidence: 0, names: [], alternatives: [] } } }),
});
const plan: Plan = {
  plan_id: 'p', created_at: '', params: {}, summary: { frames: 80, shoots: 4, trips: 0, unassigned: 0, locked: 1, flagged: 0 }, trips: [],
  shoots: [shoot('s:a00000000001', '2022-01-01'), shoot('s:a00000000002', '2023-01-01'), shoot('s:a00000000003', '2024-01-01'), shoot('s:a00000000004', '2021-01-01', true)],
  places: {
    spots: [{ key: 'p:x', kind: 'spot', name: { value: 'Pier One', source: 'phone_venue', confidence: 0.67, count: 2, alternatives: [{ value: 'Pier Point', source: 'phone_place', count: 1 }] },
      centroid: { lat: 1, lon: 2 }, radius_km: 0.5, area_key: 'a:x', frames: 80, first_date: '2021-01-01', last_date: '2024-01-01', home: false,
      members: [
        { proposal_id: 's:a00000000001', confidence: 0.9, via: 'coords' }, { proposal_id: 's:a00000000002', confidence: 0.4, via: 'coords' },
        { proposal_id: 's:a00000000003', confidence: 0.05, via: 'name', beyond_cutoff: true }, { proposal_id: 's:a00000000004', confidence: 0.9, via: 'coords' }] }],
    areas: [{ key: 'a:x', kind: 'area', name: { value: 'Bayton', source: 'phone_area', confidence: 1, count: 4, alternatives: [] }, spot_keys: ['p:x'], members: [] }],
  },
};

describe('PlacesPane (pl-2)', () => {
  it('ticks by confidence, skips locked and faint members', () => {
    const eff = effectivePlan(plan, []);
    const ticks = initialTicks(plan.places!.spots[0]!.members, new Map(eff.shoots.map((s) => [s.proposal_id, s])), 0.5);
    expect([...ticks]).toEqual(['s:a00000000001']);
  });

  it('names the place for the ticked shoots in one decision, and the beta divider fades faint members', () => {
    const decide = vi.fn();
    render(<PlacesPane plan={plan} eff={effectivePlan(plan, [])} pretick={0.5} beta decide={decide} onOpen={() => undefined} />);
    expect(screen.getByText('beyond cut-off (beta)')).toBeTruthy();
    expect(screen.getByText(/iPhone venue · 2 of 4/)).toBeTruthy();
    const faint = screen.getByLabelText(/Include 2024-01-01/).closest('div[title]') as HTMLElement;
    expect(Number(faint.style.opacity)).toBeCloseTo(0.25 + 0.75 * 0.05);
    fireEvent.click(screen.getByLabelText(/Include 2023-01-01/)); // tick the second
    fireEvent.click(screen.getByRole('button', { name: /Name this place for 2 shoots/ }));
    expect(decide).toHaveBeenCalledWith({ kind: 'name_place', ids: ['s:a00000000001', 's:a00000000002'], value: 'Pier One' });
  });

  it('home is collapsed under Home (hidden)', () => {
    const home = { ...plan, places: { ...plan.places!, spots: [{ ...plan.places!.spots[0]!, home: true }] } };
    render(<PlacesPane plan={home} eff={effectivePlan(home, [])} pretick={0.5} beta decide={() => undefined} onOpen={() => undefined} />);
    expect(screen.getByText('Home (hidden)').closest('details')?.open).toBe(false);
  });
});

describe('name_place decision', () => {
  it('stores one detail row per shoot in one batch, replays, folds and reads back as one action', () => {
    const d = { kind: 'name_place' as const, ids: ['s:a00000000001', 's:a00000000002'], value: 'Pier One' };
    const rows = toServerRows(d, effectivePlan(plan, []), 'b1');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ kind: 'detail', payload: { field: 'place', value: 'Pier One', source: 'user', batch: 'b1' } });
    const eff = effectivePlan(plan, addDecision([], d));
    expect(eff.shoots.find((s) => s.proposal_id === 's:a00000000002')!.details!.place.value).toBe('Pier One');
    expect(eff.shoots.find((s) => s.proposal_id === 's:a00000000003')!.details!.place.value).toBe('Pier Point');
    const back = fromServerRows(rows.map((r, i) => ({ ...r, decision_id: i + 1 })));
    expect(back).toHaveLength(1);
    expect(back[0]).toMatchObject({ kind: 'name_place', ids: d.ids, rows: [1, 2] });
    expect(describeAll(plan, back)[0]!.text).toBe('Name the place "Pier One" for 2 shoots');
  });
});
