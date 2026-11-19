import { describe, expect, it } from 'vitest';
import { cancelUnplug, expectUnplug, takeExpected } from './eject';

describe('expected unplugs (rv-43)', () => {
  it('is taken once, within two minutes', () => {
    expectUnplug('a', 1000);
    expect(takeExpected('a', 2000)).toBe(true);
    expect(takeExpected('a', 2000)).toBe(false);
    expectUnplug('b', 0);
    expect(takeExpected('b', 200_000)).toBe(false);
    expectUnplug('c', 0);
    cancelUnplug('c');
    expect(takeExpected('c', 1)).toBe(false);
  });
});
