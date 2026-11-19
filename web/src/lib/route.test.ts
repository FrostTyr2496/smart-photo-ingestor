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

describe('task links (rv-37)', () => {
  it('round-trips a run key and ignores junk', () => {
    expect(parseRoute('#/?task=t12')).toEqual({ view: 'home', task: 't12' });
    expect(parseRoute('#/?task=p4411')).toEqual({ view: 'home', task: 'p4411' });
    expect(parseRoute('#/?task=evil')).toEqual({ view: 'home' });
    expect(parseRoute('#/')).toEqual({ view: 'home' });
    expect(hrefFor({ view: 'home', task: 't12' })).toBe('#/?task=t12');
    expect(hrefFor({ view: 'home' })).toBe('#/');
  });
});
