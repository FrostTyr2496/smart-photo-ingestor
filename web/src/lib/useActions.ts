import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { applyOp, loadActions, snoozeUntil, type ActionItem, type LocalOp } from './actions';
import { api } from './api';
import { useStore } from './store';

/** One shared copy of the actions, so the nav badge, the Tasks link and the Actions page agree. */
interface Snap {
  actions: ActionItem[];
  loaded: boolean;
  error: string | null;
}
let snap: Snap = { actions: [], loaded: false, error: null };
const subs = new Set<() => void>();
const set = (s: Snap) => {
  snap = s;
  subs.forEach((f) => f());
};
const subscribe = (f: () => void) => {
  subs.add(f);
  return () => subs.delete(f);
};

let seq = 0;
export async function refreshActions(): Promise<void> {
  const mine = ++seq;
  try {
    const p = await loadActions();
    if (mine === seq) set({ actions: p.actions, loaded: true, error: null });
  } catch (err) {
    if (mine === seq) set({ ...snap, loaded: true, error: (err as Error).message });
  }
}

const path = (id: string, verb: string) => `/api/actions/${encodeURIComponent(id)}/${verb}`;

/** Apply the op locally at once, POST it, and roll back (reload) when the server refuses. */
export async function sendOp(op: LocalOp, days = 7): Promise<void> {
  seq++; // a reload in flight would overwrite the optimistic state
  set({ ...snap, actions: applyOp(snap.actions, op) });
  try {
    await api('POST', path(op.id, op.type), op.type === 'snooze' ? { days } : undefined);
  } catch (err) {
    await refreshActions();
    throw err;
  }
}

export const snoozeOp = (id: string, now: number, days = 7): LocalOp => ({ type: 'snooze', id, until: snoozeUntil(now, days) });

const REFRESH_MS = 60_000;

/** Mount once (the Header does): polls every 60 s and whenever the drives change. */
export function useActionsPolling(): void {
  const { drives } = useStore();
  const key = drives.map((d) => `${d.key}:${d.mounted ? 1 : 0}`).join('|');
  useEffect(() => {
    void refreshActions();
  }, [key]);
  useEffect(() => {
    const t = window.setInterval(() => void refreshActions(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);
}

export function useActions(): Snap & { refresh: () => Promise<void> } {
  const s = useSyncExternalStore(subscribe, () => snap);
  const refresh = useCallback(() => refreshActions(), []);
  return { ...s, refresh };
}
