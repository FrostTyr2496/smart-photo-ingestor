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

describe('photos route: Show in Photos (rv-27)', () => {
  it('carries a photo and its day, undated included, and round-trips', () => {
    expect(parseRoute('#/photos?photo=42&day=2023-09-09')).toEqual({ view: 'photos', photo: 42, day: '2023-09-09' });
    expect(parseRoute('#/photos?photo=7&day=undated')).toEqual({ view: 'photos', photo: 7, day: 'undated' });
    expect(parseRoute('#/photos?photo=x&day=undated')).toEqual({ view: 'photos' });
    expect(hrefFor({ view: 'photos', photo: 42, day: '2023-09-09' })).toBe('#/photos?photo=42&day=2023-09-09');
  });
});
