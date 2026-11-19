import { describe, expect, it } from 'vitest';
import { confirmMatches, readClearPlan, verifyEstimate } from './cardClear';

describe('readClearPlan', () => {
  it('reads the plan and sorts kept reasons by size', () => {
    const { plan, error } = readClearPlan([
      { event: 'warning', code: 'dry_run', message: 'dry run' },
      {
        event: 'result', label: 'card:X', total: 10, deletable_files: 6, deletable_bytes: 600, groups: 3, kept_files: 4,
        kept: { needs_backup: { files: 1, text: 'One copy only' }, only_on_card: { files: 3, text: 'Only on this card' } },
        copy_drives: ['X9 Pro', 'Backup Plus'],
      },
    ]);
    expect(error).toBe('');
    expect(plan?.deletable_files).toBe(6);
    expect(plan?.kept.map((k) => k.code)).toEqual(['only_on_card', 'needs_backup']);
    expect(plan?.copy_drives).toHaveLength(2);
  });
  it('returns the error', () => {
    expect(readClearPlan([{ event: 'error', message: 'not a card' }])).toEqual({ plan: null, error: 'not a card' });
  });
});

describe('confirmMatches', () => {
  it('needs the exact count', () => {
    expect(confirmMatches('6', 6)).toBe(true);
    expect(confirmMatches('7', 6)).toBe(false);
    expect(confirmMatches(' 6', 6)).toBe(false);
    expect(confirmMatches('06', 6)).toBe(true);
    expect(confirmMatches('', 6)).toBe(false);
    expect(confirmMatches('0', 0)).toBe(false);
    expect(confirmMatches('6x', 6)).toBe(false);
  });
});

describe('verify all copies (cc-2)', () => {
  it('reads the verify summary and the kept reasons', () => {
    const { plan } = readClearPlan([
      {
        event: 'result', deletable_files: 2, deletable_bytes: 100, groups: 1, kept_files: 1, kept: {}, copy_drives: ['A', 'B'],
        verify: { all_copies: true, drives: [{ label: 'A', files: 2, bytes: 100, mismatches: 1 }], verified_at: '2026-11-19T10:00:00+00:00' },
        verify_kept: [{ rel_path: 'DCIM/x.NEF', reason: 'copy on A missing or does not match' }],
      },
    ]);
    expect(plan?.verify.all_copies).toBe(true);
    expect(plan?.verify.drives[0]?.mismatches).toBe(1);
    expect(plan?.verify_kept[0]?.reason).toContain('copy on A');
  });
  it('defaults to a bypassed verify when the result has none', () => {
    const { plan } = readClearPlan([{ event: 'result', deletable_files: 1, deletable_bytes: 1, kept: {} }]);
    expect(plan?.verify).toEqual({ all_copies: false, drives: [], verified_at: null });
    expect(plan?.verify_kept).toEqual([]);
  });
  it('estimates the extra read', () => {
    expect(verifyEstimate({ deletable_bytes: 100, copy_drives: ['A', 'B'] })).toEqual({ bytes: 200, drives: ['A', 'B'] });
  });
});
