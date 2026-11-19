import { describe, expect, it } from 'vitest';
import { confirmMatches, readClearPlan } from './cardClear';

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
