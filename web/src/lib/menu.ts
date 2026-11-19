/**
 * Right-click menus (rv-28): one menu at a time, opened from any `contextmenu` event with a list of items. The host
 * (`components/ContextMenu.tsx`) renders it. Hold Shift while right-clicking to get the browser's own menu instead.
 */

export interface MenuAction {
  label: string;
  icon?: string;
  /** A short hint on the right, such as a key ("Space"). */
  hint?: string;
  disabled?: boolean;
  onSelect: () => void;
}
export type MenuItem = MenuAction | 'sep';

export interface MenuState {
  x: number;
  y: number;
  title?: string;
  items: MenuItem[];
}

/** Drop separators at either end and repeated ones, and false/null entries from conditional lists. */
export function tidyItems(items: Array<MenuItem | false | null | undefined>): MenuItem[] {
  const out: MenuItem[] = [];
  for (const it of items) {
    if (!it) continue;
    if (it === 'sep' && (out.length === 0 || out[out.length - 1] === 'sep')) continue;
    out.push(it);
  }
  while (out[out.length - 1] === 'sep') out.pop();
  return out;
}

/** Top-left of a `w` × `h` menu opened at (x, y): flipped left or up when it would leave the viewport, then clamped. */
export function placeMenu(x: number, y: number, w: number, h: number, vw: number, vh: number, pad = 8): { left: number; top: number } {
  let left = x + w + pad > vw ? x - w : x;
  let top = y + h + pad > vh ? y - h : y;
  left = Math.max(pad, Math.min(left, vw - w - pad));
  top = Math.max(pad, Math.min(top, vh - h - pad));
  return { left, top };
}

/** The next enabled action from index `from` in direction `dir` (wrapping), or -1 when there is none. */
export function nextEnabled(items: MenuItem[], from: number, dir: 1 | -1): number {
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const i = (((from + dir * k) % n) + n) % n;
    const it = items[i];
    if (it && it !== 'sep' && !it.disabled) return i;
  }
  return -1;
}

// ---------------------------------------------------------------- the one open menu

let current: MenuState | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const menuStore = {
  get: (): MenuState | null => current,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function closeMenu(): void {
  if (!current) return;
  current = null;
  emit();
}

interface MenuEvent {
  clientX: number;
  clientY: number;
  shiftKey: boolean;
  currentTarget: EventTarget | null;
  preventDefault: () => void;
  stopPropagation: () => void;
}

/**
 * Open a menu from a `contextmenu` event. Shift keeps the browser's menu. A keyboard-opened menu (the Menu key or
 * Shift+F10 report 0, 0) opens under the element instead of the screen corner.
 */
export function openMenu(e: MenuEvent, items: Array<MenuItem | false | null | undefined>, title?: string): void {
  if (e.shiftKey) return;
  const list = tidyItems(items);
  if (!list.length) return;
  e.preventDefault();
  e.stopPropagation();
  let { clientX: x, clientY: y } = e;
  if (x === 0 && y === 0 && e.currentTarget instanceof Element) {
    const r = e.currentTarget.getBoundingClientRect();
    x = r.left + 8;
    y = r.bottom;
  }
  current = { x, y, title, items: list };
  emit();
}
