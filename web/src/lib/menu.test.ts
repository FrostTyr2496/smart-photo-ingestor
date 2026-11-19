import { describe, expect, it, vi } from 'vitest';
import { closeMenu, menuStore, nextEnabled, openMenu, placeMenu, tidyItems, type MenuItem } from './menu';

const a = (label: string, disabled = false): MenuItem => ({ label, disabled, onSelect: () => undefined });
const ev = (over: Partial<Parameters<typeof openMenu>[0]> = {}) => ({
  clientX: 100,
  clientY: 200,
  shiftKey: false,
  currentTarget: null,
  preventDefault: vi.fn(),
  stopPropagation: vi.fn(),
  ...over,
});

describe('tidyItems', () => {
  it('drops falsy entries and stray separators', () => {
    const out = tidyItems(['sep', a('A'), false, 'sep', 'sep', null, a('B'), 'sep']);
    expect(out.map((i) => (i === 'sep' ? '-' : i.label))).toEqual(['A', '-', 'B']);
  });
});

describe('placeMenu', () => {
  it('opens at the pointer when it fits', () => {
    expect(placeMenu(100, 100, 200, 150, 1000, 800)).toEqual({ left: 100, top: 100 });
  });
  it('flips left and up near the right and bottom edges', () => {
    expect(placeMenu(950, 750, 200, 150, 1000, 800)).toEqual({ left: 750, top: 600 });
  });
  it('clamps inside the viewport when it fits neither way', () => {
    expect(placeMenu(50, 50, 200, 900, 1000, 800)).toEqual({ left: 50, top: 8 });
  });
});

describe('nextEnabled', () => {
  const items = [a('A'), 'sep', a('B', true), a('C')] as MenuItem[];
  it('skips separators and disabled items, and wraps', () => {
    expect(nextEnabled(items, 0, 1)).toBe(3);
    expect(nextEnabled(items, 3, 1)).toBe(0);
    expect(nextEnabled(items, 0, -1)).toBe(3);
    expect(nextEnabled(items, -1, 1)).toBe(0);
  });
  it('is -1 when nothing is enabled', () => {
    expect(nextEnabled([a('X', true)], -1, 1)).toBe(-1);
  });
});

describe('openMenu (rv-28)', () => {
  it('opens at the pointer and blocks the browser menu', () => {
    const e = ev();
    openMenu(e, [a('Show in Photos')], 'DSC_0001.NEF');
    expect(e.preventDefault).toHaveBeenCalled();
    expect(menuStore.get()).toMatchObject({ x: 100, y: 200, title: 'DSC_0001.NEF' });
    closeMenu();
    expect(menuStore.get()).toBeNull();
  });
  it('leaves Shift+right-click and empty menus to the browser', () => {
    const e = ev({ shiftKey: true });
    openMenu(e, [a('A')]);
    openMenu(ev(), [false, 'sep']);
    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(menuStore.get()).toBeNull();
  });
});
