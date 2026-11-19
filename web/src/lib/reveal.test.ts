import { describe, expect, it, vi } from 'vitest';
import { centreOffset, holdCentred } from './reveal';

/** A fake page: the element's top moves when layout "grows" above it, and scrollIntoView centres it again. */
function setup() {
  const VIEW = 800;
  let top = 3000;
  const el = {
    getBoundingClientRect: () => ({ top, height: 100 }),
    scrollIntoView: vi.fn(() => {
      top = VIEW / 2 - 50;
    }),
  } as unknown as HTMLElement;
  let t = 0;
  const frames: Array<() => void> = [];
  const listeners = new Map<string, EventListener>();
  const opts = {
    raf: (cb: () => void) => frames.push(cb),
    cancelRaf: () => undefined,
    now: () => t,
    viewHeight: () => VIEW,
    target: {
      addEventListener: (ev: string, fn: EventListener) => listeners.set(ev, fn),
      removeEventListener: (ev: string) => listeners.delete(ev),
    } as unknown as Window,
  };
  const step = (ms: number) => {
    t += ms;
    frames.shift()?.();
  };
  return { el, opts, step, frames, listeners, grow: (px: number) => (top += px) };
}

describe('centreOffset', () => {
  it('is zero when centred, positive below centre', () => {
    expect(centreOffset({ top: 350, height: 100 }, 800)).toBe(0);
    expect(centreOffset({ top: 450, height: 100 }, 800)).toBe(100);
  });
});

describe('holdCentred (rv-27 fix 2)', () => {
  it('jumps, re-centres when days above grow, then stops once settled', () => {
    const s = setup();
    const onFound = vi.fn();
    const onDone = vi.fn();
    holdCentred(() => s.el, { ...s.opts, onFound, onDone, settleMs: 300 });
    expect(s.el.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(onFound).toHaveBeenCalledOnce();
    s.step(100);
    s.grow(1800); // a day above loads and grows
    s.step(16);
    expect(s.el.scrollIntoView).toHaveBeenCalledTimes(2);
    s.step(200);
    expect(onDone).not.toHaveBeenCalled();
    s.step(200);
    expect(onDone).toHaveBeenCalledWith(s.el);
    expect(s.listeners.size).toBe(0);
  });

  it('waits for the element to render, and gives up after maxMs', () => {
    const s = setup();
    let shown = false;
    const onDone = vi.fn();
    holdCentred(() => (shown ? s.el : null), { ...s.opts, onDone, maxMs: 1000 });
    s.step(100);
    expect(s.el.scrollIntoView).not.toHaveBeenCalled();
    shown = true;
    s.step(16);
    expect(s.el.scrollIntoView).toHaveBeenCalledOnce();

    const t = setup();
    const gaveUp = vi.fn();
    holdCentred(() => null, { ...t.opts, onDone: gaveUp, maxMs: 1000 });
    for (let i = 0; i < 20; i++) t.step(100);
    expect(gaveUp).toHaveBeenCalledWith(null);
  });

  it('stops as soon as the user scrolls', () => {
    const s = setup();
    const onDone = vi.fn();
    holdCentred(() => s.el, { ...s.opts, onDone });
    s.listeners.get('wheel')!(new Event('wheel'));
    expect(onDone).toHaveBeenCalledOnce();
    s.grow(1000);
    s.step(16);
    expect(s.el.scrollIntoView).toHaveBeenCalledTimes(1);
  });
});
