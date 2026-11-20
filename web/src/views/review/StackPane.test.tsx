import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { effectivePlan, type Plan, type PlanShoot } from '../../lib/review';
import { StackPane } from './StackPane';

const ch = (value: string, source: string, confidence: number, beyond = false) => ({ value, source, confidence, evidence: 2, ...(beyond ? { beyond_cutoff: true } : {}) });
const cand = (first: number, score: number, beyond = false) => ({
  session: [first, first + 1] as [number, number],
  score,
  band: (beyond ? 'beyond_cutoff' : score >= 0.5 ? 'likely' : 'maybe') as 'likely' | 'maybe' | 'beyond_cutoff',
  reasons: [{ term: 'time', text: '+2 h after' }],
  home_proposal: null,
  frames: 5,
  start_instant: '2026-03-10T05:10:00+00:00',
  end_instant: '2026-03-10T05:40:00+00:00',
  bodies: { Z6: 5 },
  ...(beyond ? { beyond_cutoff: true } : {}),
});

const shoot: PlanShoot = {
  proposal_id: 's:abc123abc123',
  trip_id: null,
  name: { value: 'Harbour Moonrise', source: 'folder', alternatives: [] },
  shoot_date: '2026-03-09',
  start_instant: '2026-03-10T03:05:00+00:00',
  end_instant: '2026-03-10T04:30:00+00:00',
  wall: { start: null, end: null },
  local_offset: { value: '+00:00', camera: '+00:00', source: 'phone', changes: false },
  group_ids: [10, 11, 12, 13],
  counts: { frames: 40, by_body: {}, located: 0 },
  sample_group_ids: [],
  centroid: null,
  points: [],
  gaps: [],
  flags: [],
  locked: false,
  members: { core_sessions: [[10, 11], [12, 13]] },
  candidates: [cand(50, 0.7), cand(60, 0.3), cand(70, 0.05, true)],
  details: {
    place: { ...ch('Dune Lookout', 'phone_venue', 0.8), alternatives: [ch('Harbourton', 'phone_area', 0.4), ch('Old Pier', 'phone_area', 0.1, true)] },
    subject: { ...ch('Moonrise', 'phone_caption', 0.7), alternatives: [], top_terms: ['Moonrise'], example_caption: null },
    people: { value: [], source: null, confidence: 0, evidence: 0, names: [], alternatives: [] },
  },
};
const plan: Plan = { plan_id: 'shoots-20260101-000000', created_at: '', params: {}, summary: { frames: 40, shoots: 1, trips: 0, unassigned: 0, locked: 0, flagged: 0 }, trips: [], shoots: [shoot] };

const setup = () => {
  const eff = effectivePlan(plan, []);
  const onConfirm = vi.fn();
  const onDecide = vi.fn();
  render(<StackPane s={eff.shoots[0]!} plan={eff} pretick={0.5} mounted={new Set()} position="1 of 1" onDecide={onDecide} onConfirm={onConfirm} onClose={() => undefined} />);
  return { onConfirm, onDecide };
};

describe('StackPane (sh-10)', () => {
  it('step 1 shows chips with source and confidence, the beta divider and the composed name', () => {
    setup();
    expect(screen.getByText(/iPhone venue 80%/)).toBeTruthy();
    expect(screen.getByText(/iPhone area 40%/)).toBeTruthy();
    expect(screen.getAllByRole('separator')[0]!.textContent).toContain('beyond cut-off (beta)');
    const faint = screen.getByText(/Old Pier/).closest('span[title]') as HTMLElement;
    expect(Number(faint.style.opacity)).toBeCloseTo(0.25 + 0.75 * 0.1);
  });

  it('Tab cycles the place alternatives and the composed name follows', () => {
    setup();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect((screen.getByLabelText('Shoot name') as HTMLInputElement).value).toBe('Harbourton · Moonrise');
  });

  it('Enter walks the steps and confirms with the pre-ticked candidates, the unticked member and the details', () => {
    const { onConfirm } = setup();
    fireEvent.keyDown(window, { key: 'Enter' }); // -> members
    fireEvent.keyDown(window, { key: 'x' }); // untick the first session
    fireEvent.keyDown(window, { key: 'Enter' }); // -> in question
    expect(screen.getByText('beyond cut-off (beta)')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Enter' }); // confirm
    expect(onConfirm).toHaveBeenCalledOnce();
    const kinds = onConfirm.mock.calls[0]![0].map((d: { kind: string }) => d.kind);
    expect(kinds).toEqual(['exclude', 'include', 'detail', 'detail', 'confirm']);
    expect(onConfirm.mock.calls[0]![0][0]).toMatchObject({ session: [10, 11] });
    expect(onConfirm.mock.calls[0]![0][1]).toMatchObject({ session: [50, 51] });
  });

  it('S skips', () => {
    const { onDecide } = setup();
    fireEvent.keyDown(window, { key: 's' });
    expect(onDecide).toHaveBeenCalledWith({ kind: 'skip', id: 's:abc123abc123' });
  });
});
