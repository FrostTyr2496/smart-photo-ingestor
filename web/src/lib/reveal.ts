/**
 * Keep an element centred while the page around it settles (rv-27 fix 2).
 *
 * The Photos timeline renders days that have not loaded as short placeholders. A jump to a photo deep in the timeline
 * makes the days near it load and grow, and opening the inspector narrows the grid, so a single scrollIntoView lands on
 * the wrong photo. This jumps (no smooth scroll), then re-centres every frame the element drifts, until it has stayed
 * put for `settleMs`, `maxMs` has passed, or the user scrolls or presses a key.
 */

export interface HoldOptions {
  /** Give up after this long, found or not. */
  maxMs?: number;
  /** Stop once the element has not needed a re-centre for this long. */
  settleMs?: number;
  /** Pixels the element's centre may drift before it is re-centred. */
  tolerance?: number;
  /** Called once, the first time the element is found and centred. */
  onFound?: (el: HTMLElement) => void;
  /** Called once when the hold ends; `el` is null when it was never found. */
  onDone?: (el: HTMLElement | null) => void;
  /** Injection points for tests. */
  raf?: (cb: () => void) => number;
  cancelRaf?: (id: number) => void;
  now?: () => number;
  viewHeight?: () => number;
  target?: Pick<Window, 'addEventListener' | 'removeEventListener'>;
}

/** How far an element's centre is below (positive) or above (negative) the viewport's centre. */
export function centreOffset(rect: { top: number; height: number }, viewHeight: number): number {
  return rect.top + rect.height / 2 - viewHeight / 2;
}

const USER_INPUT = ['wheel', 'touchmove', 'keydown', 'mousedown'] as const;

/** Start holding; returns a function that stops the hold (safe to call more than once). */
export function holdCentred(find: () => HTMLElement | null, opts: HoldOptions = {}): () => void {
  const maxMs = opts.maxMs ?? 2500;
  const settleMs = opts.settleMs ?? 450;
  const tolerance = opts.tolerance ?? 24;
  const raf = opts.raf ?? ((cb: () => void) => window.requestAnimationFrame(cb));
  const cancelRaf = opts.cancelRaf ?? ((id: number) => window.cancelAnimationFrame(id));
  const now = opts.now ?? (() => performance.now());
  const viewHeight = opts.viewHeight ?? (() => window.innerHeight);
  const target = opts.target ?? window;

  const start = now();
  let lastMove = start;
  let found: HTMLElement | null = null;
  let frame = 0;
  let done = false;

  const stop = () => {
    if (done) return;
    done = true;
    cancelRaf(frame);
    for (const ev of USER_INPUT) target.removeEventListener(ev, stop, true);
    opts.onDone?.(found);
  };

  const tick = () => {
    if (done) return;
    const t = now();
    const el = find();
    if (el) {
      if (Math.abs(centreOffset(el.getBoundingClientRect(), viewHeight())) > tolerance) {
        el.scrollIntoView({ block: 'center', behavior: 'auto' });
        lastMove = t;
      }
      if (!found) {
        found = el;
        lastMove = t;
        opts.onFound?.(el);
      }
      found = el;
      if (t - lastMove >= settleMs) return stop();
    }
    if (t - start >= maxMs) return stop();
    frame = raf(tick);
  };

  for (const ev of USER_INPUT) target.addEventListener(ev, stop, true);
  tick();
  return stop;
}
