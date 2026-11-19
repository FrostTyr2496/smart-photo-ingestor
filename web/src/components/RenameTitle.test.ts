import { describe, expect, it } from 'vitest';
import { labelToSave } from './RenameTitle';

describe('labelToSave (rv-40)', () => {
  it('keeps a card label prefix and trims', () => {
    expect(labelToSave('card:NIKON Z 6_2', '  Trip card ')).toBe('card:Trip card');
    expect(labelToSave('CARD: old', 'New')).toBe('card:New');
  });
  it('leaves other drives as typed', () => {
    expect(labelToSave('SMALL 1TB', 'Fast SSD')).toBe('Fast SSD');
    expect(labelToSave(null, 'X')).toBe('X');
  });
});
