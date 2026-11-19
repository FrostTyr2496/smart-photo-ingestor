import { describe, expect, it } from 'vitest';
import { copyable, greenButton, sentence, type GreenPlan } from './makeGreen';

const drive = (label: string) => ({ volume_id: label, label, role: 'WORKING', mount: `/m/${label}` });
const plan = (over: Partial<GreenPlan> = {}): GreenPlan => ({
  card_volume_id: 'c', label: 'Card', files: 5, bytes: 500, possible: true, reason: null,
  sources: [{ source: drive('A'), files: 3, bytes: 300, ok: true, reason: null, target: { ...drive('B'), fs: 'exfat', free_bytes: 1, reasons: [] } },
            { source: drive('X'), files: 2, bytes: 200, ok: false, reason: "the only copy is on X, which isn't connected", target: null }],
  ...over,
});

describe('greenButton', () => {
  it('waits for the plan', () => expect(greenButton(null).enabled).toBe(false));
  it('is disabled with the reason when nothing is possible', () => {
    const g = greenButton(plan({ possible: false, reason: 'Plug in a second working drive' }));
    expect(g).toEqual({ enabled: false, title: 'Plug in a second working drive' });
  });
  it('is disabled when no file has one copy', () => expect(greenButton(plan({ files: 0 })).enabled).toBe(false));
  it('is enabled and names the target', () => expect(greenButton(plan())).toEqual({ enabled: true, title: 'Copy the one-copy files onto B to turn them green' }));
});

describe('copyable', () => {
  it('sums only the sources that can run', () => expect(copyable(plan())).toEqual({ files: 3, bytes: 300 }));
});

describe('sentence', () => {
  it('capitalises', () => expect(sentence('the only copy')).toBe('The only copy'));
});
