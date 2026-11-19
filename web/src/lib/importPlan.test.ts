import { describe, expect, it } from 'vitest';
import { batchLimit, batchLine, importArgs, inputsKey, overCapacity, readPlan, tierAfterImport } from './importPlan';

const result = { event: 'result', files: 20, groups: 10, limit: 10, groups_total: 240, files_total: 480, planned_bytes: 100, planned: '100 B',
  target_free: '1 KB', target_free_bytes: 1000, folders: [{ folder: 'a', files: 20 }], sample: ['x -> y'], kinds: { raw: 10 } };

describe('batchLimit', () => {
  it('maps choices', () => {
    expect(batchLimit('10', '')).toBe(10);
    expect(batchLimit('100', '')).toBe(100);
    expect(batchLimit('all', '')).toBeNull();
    expect(batchLimit('custom', ' 25 ')).toBe(25);
  });
  it('rejects a bad custom number', () => {
    for (const t of ['', '0', '-3', '1.5', 'abc', '1e3']) expect(batchLimit('custom', t)).toBeUndefined();
  });
});

describe('importArgs and inputsKey', () => {
  const base = { source: '/s', target: '/t', name: ' Trip ', limit: 10 as number | null };
  it('builds arguments', () => {
    expect(importArgs(base)).toEqual({ source: '/s', target: '/t', name: 'Trip', limit: 10 });
    expect(importArgs({ ...base, name: '', limit: null })).toEqual({ source: '/s', target: '/t' });
  });
  it('changes with every input', () => {
    const k = inputsKey(base);
    expect(inputsKey({ ...base, name: 'Trip' })).toBe(k);
    expect(inputsKey({ ...base, name: 'Other' })).not.toBe(k);
    expect(inputsKey({ ...base, limit: null })).not.toBe(k);
    expect(inputsKey({ ...base, target: '/u' })).not.toBe(k);
    expect(inputsKey({ ...base, source: '/c' })).not.toBe(k);
  });
});

describe('readPlan', () => {
  it('reads the result and keeps real warnings', () => {
    const r = readPlan([
      { event: 'warning', code: 'batch', message: 'batch: 10 of 240' },
      { event: 'warning', code: 'dry_run', message: 'dry run' },
      result,
    ]);
    expect(r.warnings).toEqual(['batch: 10 of 240']);
    expect(r.plan?.files_total).toBe(480);
    expect(batchLine(r.plan!)).toBe('20 of 480 files (10 of 240 frames)');
  });
  it('reports an error and no plan', () => {
    const r = readPlan([{ event: 'error', message: 'target is not a volume' }]);
    expect(r.plan).toBeNull();
    expect(r.error).toBe('target is not a volume');
  });
});

describe('capacity and tier', () => {
  it('flags only a known shortfall', () => {
    expect(overCapacity({ planned_bytes: 10, target_free_bytes: 5 })).toBe(true);
    expect(overCapacity({ planned_bytes: 10, target_free_bytes: 50 })).toBe(false);
    expect(overCapacity({ planned_bytes: 10, target_free_bytes: null })).toBe(false);
  });
  it('one drive is yellow, two is green', () => {
    expect(tierAfterImport(false)).toBe('needs_backup');
    expect(tierAfterImport(true)).toBe('backed_up');
  });
});
