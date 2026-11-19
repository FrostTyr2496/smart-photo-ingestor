import { describe, expect, it } from 'vitest';
import { hrefFor, parseRoute } from './route';

describe('photos route (act-3)', () => {
  it('parses missing and day', () => {
    expect(parseRoute('#/photos?missing=gps&day=2023-09-09')).toEqual({ view: 'photos', missing: 'gps', day: '2023-09-09' });
  });
  it('ignores unknown or malformed values', () => {
    expect(parseRoute('#/photos?missing=bogus&day=tomorrow')).toEqual({ view: 'photos' });
    expect(parseRoute('#/photos')).toEqual({ view: 'photos' });
  });
  it('round-trips through hrefFor', () => {
    const r = parseRoute('#/photos?missing=gps&day=2023-09-09');
    expect(hrefFor(r)).toBe('#/photos?missing=gps&day=2023-09-09');
    expect(hrefFor({ view: 'photos' })).toBe('#/photos');
  });
  it('keeps other routes', () => {
    expect(parseRoute('#/drive/abc')).toEqual({ view: 'drive', id: 'abc' });
    expect(parseRoute('#/review/shoots')).toEqual({ view: 'review' });
  });
});
