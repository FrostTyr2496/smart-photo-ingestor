import { describe, expect, it } from 'vitest';
import { backupArgs, batchLimit, pairMounts, batchLine, importArgs, importJobId, inputsKey, overCapacity, progressLine, readPlan, readSecond, tierAfterImport } from './importPlan';

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

describe('trailing copy (bk-3)', () => {
  it('builds the backup arguments, with and without a followed job', () => {
    expect(backupArgs('vid', '/w', '/b', 7)).toEqual({ card: 'vid', source: '/w', target: '/b', follow: 7 });
    expect(backupArgs('vid', '/w', '/b')).toEqual({ card: 'vid', source: '/w', target: '/b' });
  });
  it('finds the import job id in the plan result', () => {
    expect(importJobId([{ event: 'progress' }])).toBeUndefined();
    expect(importJobId([{ event: 'progress' }, { event: 'result', job_id: 12, files: 3 }])).toBe(12);
  });
  it('reads the second drive plan or its refusal', () => {
    const ok = readSecond([{ event: 'result', target: 'Backup Plus', target_fs: 'exfat', target_free: '1 TB', target_free_bytes: 10 ** 12 }]);
    expect(ok.second).toEqual({ target: 'Backup Plus', target_fs: 'exfat', target_free: '1 TB', target_free_bytes: 10 ** 12 });
    expect(readSecond([{ event: 'error', message: 'formatted ntfs' }])).toEqual({ second: null, error: 'formatted ntfs' });
  });
  it('a second drive changes the plan key', () => {
    const base = { source: '/s', target: '/t', name: '', limit: null };
    expect(inputsKey({ ...base, second: '/b' })).not.toBe(inputsKey(base));
  });
});

describe('pairMounts (rec-1)', () => {
  const rec = (id: string, mount: string) => ({ volume_id: id, label: id, role: 'WORKING', mount, usable: true, rank: 1, reasons: [] });
  it('maps the proposed pair to mounts', () => {
    const a = { candidates: [rec('a', '/a'), rec('b', '/b')], pair: { read: 'a', copy: 'b', note: '' } };
    expect(pairMounts(a)).toEqual({ first: '/a', second: '/b' });
  });
  it('gives empty strings when there is no pair', () => {
    expect(pairMounts({ candidates: [], pair: { read: null, copy: null, note: '' } })).toEqual({ first: '', second: '' });
  });
});

describe('batchLine with frames already on the target (D-106)', () => {
  it('counts only what is still to copy', () => {
    const p = readPlan([{ ...result, already_on_target: { frames: 40, files: 80 } }]).plan!;
    expect(p.already).toEqual({ frames: 40, files: 80 });
    expect(batchLine(p)).toBe('20 of 400 files still to copy (10 of 200 frames)');
  });
});

describe('progressLine (ing-14)', () => {
  it('is empty without progress events', () => {
    expect(progressLine([{ event: 'warning', message: 'x' }])).toBe('');
  });
  it('shows phase, counts and message of the newest progress event', () => {
    const ev = [
      { event: 'progress', phase: 'scan', current: 0, total: 0, message: 'scanning' },
      { event: 'progress', phase: 'metadata', current: 2366, total: 2386, message: 'metadata: 2,366 from the catalog, 20 read from the card' },
    ];
    expect(progressLine(ev)).toBe('metadata · 2,366 / 2,386 · metadata: 2,366 from the catalog, 20 read from the card');
    expect(progressLine(ev.slice(0, 1))).toBe('scan · scanning');
  });
});
