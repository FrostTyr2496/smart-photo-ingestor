import { describe, expect, it } from 'vitest';
import { initialView } from './mapview';

describe('initialView', () => {
  it('goes to street level on a pin', () => {
    expect(initialView({ lat: 1, lon: 2 }, [{ lat: 5, lon: 5 }])).toEqual({ kind: 'center', lat: 1, lon: 2, zoom: 16 });
  });
  it('fits several points, centres one, and shows the world for none', () => {
    expect(initialView(null, [{ lat: 1, lon: 1 }, { lat: 2, lon: 2 }]).kind).toBe('fit');
    expect(initialView(null, [{ lat: 1, lon: 1 }])).toMatchObject({ kind: 'center', zoom: 14 });
    expect(initialView(null, [])).toEqual({ kind: 'world' });
  });
});
