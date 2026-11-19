import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/actions.json';
import { applyOp, badgeCount, groupActions, highlightText, snoozeUntil, sortActions, type ActionItem } from './actions';

const all = (fixture as unknown as { actions: ActionItem[] }).actions;
const NOW = Date.parse('2026-11-18T22:00:00+00:00');

describe('fixture', () => {
  it('has an action for each rule kind', () => {
    const kinds = new Set(all.map((a) => a.kind));
    for (const k of [
      'card_unbacked', 'card_unindexed', 'single_copy', 'drive_errors', 'job_paused', 'hash_incomplete',
      'drive_stale', 'sidecars_dirty', 'metadata_stale', 'locations_suggested', 'shoots_review', 'duplicates_reclaimable',
    ]) expect(kinds.has(k), k).toBe(true);
  });
});

describe('groupActions', () => {
  const g = groupActions(all, NOW);
  it('splits by severity and hides snoozed ones', () => {
    expect(g.risk.map((a) => a.severity)).toEqual(['critical', 'high', 'high', 'high']);
    expect(g.unfinished.every((a) => a.severity === 'normal')).toBe(true);
    expect(g.housekeeping.every((a) => a.severity === 'low')).toBe(true);
    expect(g.hidden.map((a) => a.id)).toEqual(['metadata-stale:vol-shelf']);
  });
  it('orders by severity, then doable now (a needed drive is connected), then bytes, then the given order', () => {
    expect(g.risk[0]!.id).toBe('card-unbacked:vol-card-a');
    expect(g.risk[1]!.id).toBe('card-unindexed:vol-card-b'); // Card B is connected in the fixture: doable now
    expect(g.risk.slice(2).map((a) => a.id)).toEqual(['single-copy:vol-old', 'drive-errors:vol-old']); // 41 GB beats 0 bytes
  });
  it('a snooze in the past no longer hides', () => {
    const list = all.map((a) => (a.snoozed_until ? { ...a, snoozed_until: '2026-11-01T00:00:00+00:00' } : a));
    expect(groupActions(list, NOW).hidden).toEqual([]);
  });
  it('sortActions does not mutate', () => {
    const copy = [...all];
    sortActions(all);
    expect(all).toEqual(copy);
  });
});

describe('badgeCount', () => {
  it('counts critical + high that are not hidden', () => {
    expect(badgeCount(all, NOW)).toBe(4);
    expect(badgeCount(applyOp(all, { type: 'dismiss', id: 'single-copy:vol-old' }), NOW)).toBe(3);
    expect(badgeCount([], NOW)).toBe(0);
  });
});

describe('highlightText', () => {
  const a = all.find((x) => x.id === 'card-unbacked:vol-card-a')!;
  it('is null when nothing needed is connected', () => {
    expect(highlightText(a)).toBeNull();
    expect(highlightText(a, new Set())).toBeNull();
  });
  it('names the connected drive; live mounts beat the payload flag', () => {
    expect(highlightText(a, new Set(['vol-card-a']))).toBe('Card A is connected — do it now');
    const b = all.find((x) => x.id === 'card-unindexed:vol-card-b')!;
    expect(highlightText(b)).toBe('Card B is connected — do it now');
    expect(highlightText(b, new Set())).toBeNull();
  });
  it('has nothing to highlight without needs', () => {
    expect(highlightText(all.find((x) => x.id === 'shoots-review:all')!)).toBeNull();
  });
});

describe('applyOp', () => {
  const id = 'card-unbacked:vol-card-a';
  it('dismiss, snooze and undo round trip, without mutating', () => {
    const d = applyOp(all, { type: 'dismiss', id });
    expect(d.find((a) => a.id === id)!.dismissed).toBe(true);
    expect(all.find((a) => a.id === id)!.dismissed).toBeUndefined();
    expect(groupActions(d, NOW).hidden.map((a) => a.id)).toContain(id);
    const s = applyOp(all, { type: 'snooze', id, until: snoozeUntil(NOW, 7) });
    expect(s.find((a) => a.id === id)!.snoozed_until).toBe('2026-11-25T22:00:00.000Z');
    const r = applyOp(applyOp(d, { type: 'snooze', id, until: snoozeUntil(NOW, 7) }), { type: 'restore', id });
    const back = r.find((a) => a.id === id)!;
    expect(back.dismissed).toBe(false);
    expect(back.snoozed_until).toBeNull();
    expect(groupActions(r, NOW).risk[0]!.id).toBe(id);
  });
  it('ignores unknown ids', () => {
    expect(applyOp(all, { type: 'dismiss', id: 'nope' })).toEqual(all);
  });
});

describe('doable now first (user ask)', () => {
  it('puts an action whose drive is connected ahead of a bigger one whose drive is not, within a severity', () => {
    const mk = (id: string, vol: string, bytes: number) =>
      ({ id, kind: 'card_unbacked', severity: 'critical', title: id, why: [], needs: [{ volume_id: vol, label: vol, role: 'CARD', mounted: false }], steps: [], do: { label: 'x', kind: 'none' }, cli: null, evidence: { files: 1, bytes }, snoozed_until: null }) as unknown as Parameters<typeof sortActions>[0][number];
    const list = [mk('big-unplugged', 'v1', 1e12), mk('small-plugged', 'v2', 1)];
    expect(sortActions(list, new Set(['v2'])).map((a) => a.id)).toEqual(['small-plugged', 'big-unplugged']);
    expect(sortActions(list).map((a) => a.id)).toEqual(['big-unplugged', 'small-plugged']);
  });
});
