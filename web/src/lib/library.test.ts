import { describe, expect, it } from 'vitest';
import { confirmLabel, countStep, doneCount, driveSteps, metaStep, phoneStep, planStep, type ChecklistDrive } from './library';

const drive = (over: Partial<ChecklistDrive> = {}): ChecklistDrive => ({
  volume_id: 'vol-a',
  label: 'Ash',
  role: 'WORKING',
  connected: true,
  indexed: true,
  hashed_files: 0,
  total_files: 0,
  thumbs: { have: 0, groups: 0 },
  ...over,
});

describe('countStep', () => {
  it('is done when all have it, partial with a count when some do, not started otherwise', () => {
    expect(countStep(9880, 9880, 'hashed')).toEqual({ state: 'done', text: '9,880 hashed' });
    expect(countStep(1204, 9880, 'hashed')).toEqual({ state: 'partial', text: '1,204 / 9,880 hashed' });
    expect(countStep(0, 9880, 'hashed').state).toBe('none');
    expect(countStep(0, 0, 'hashed').state).toBe('none');
  });
});

describe('driveSteps', () => {
  it('reads index, hash and thumbs for one drive', () => {
    const s = driveSteps(drive({ hashed_files: 1204, total_files: 9880, thumbs: { have: 1100, groups: 9000 } }));
    expect(s.index.state).toBe('done');
    expect(s.hash).toEqual({ state: 'partial', text: '1,204 / 9,880 hashed' });
    expect(s.thumbs).toEqual({ state: 'partial', text: '1,100 / 9,000 thumbs' });
  });
  it('shows an unindexed drive as not started', () => {
    const s = driveSteps(drive({ indexed: false }));
    expect(s.index).toEqual({ state: 'none', text: 'Not indexed' });
    expect(s.hash.state).toBe('none');
  });
});

describe('phone, metadata and plan steps', () => {
  it('phone: not imported, partly analysed, or all analysed', () => {
    expect(phoneStep({ points: 0, analysed: 0 })).toEqual({ state: 'none', text: 'Not imported' });
    expect(phoneStep({ points: 40, analysed: 12 })).toEqual({ state: 'partial', text: '12 / 40 analysed' });
    expect(phoneStep({ points: 40, analysed: 40 })).toEqual({ state: 'done', text: '40 points, analysed' });
  });
  it('metadata: partly or fully scanned groups', () => {
    expect(metaStep({ groups: 0, total: 0 }).state).toBe('none');
    expect(metaStep({ groups: 5, total: 20 })).toEqual({ state: 'partial', text: '5 / 20 groups' });
    expect(metaStep({ groups: 20, total: 20 })).toEqual({ state: 'done', text: '20 groups' });
  });
  it('plan: none until one exists, then its size', () => {
    expect(planStep(null)).toEqual({ state: 'none', text: 'No plan yet' });
    expect(planStep({ plan_id: 'shoots-1', created_at: null, shoots: 1, unconfirmed: 1 })).toEqual({
      state: 'done',
      text: '1 shoot planned',
    });
  });
  it('confirm link wording', () => {
    expect(confirmLabel(1)).toBe('1 shoot to confirm →');
    expect(confirmLabel(182)).toBe('182 shoots to confirm →');
    expect(confirmLabel(0)).toBe('All shoots confirmed');
  });
});

describe('doneCount', () => {
  it('counts the done steps across drives and the rest', () => {
    const c = {
      drives: [drive({ indexed: true, hashed_files: 5, total_files: 5, thumbs: { have: 0, groups: 5 } })],
      thumbs_enabled: false,
      thumbs_error: null,
      phone: { points: 0, analysed: 0 },
      meta_scanned: { groups: 0, total: 0 },
      plan: null,
    };
    // index done, hash done, thumbs not started, phone / metadata / plan not started
    expect(doneCount(c)).toEqual({ done: 2, total: 6 });
  });
});
