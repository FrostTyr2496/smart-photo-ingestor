// Tracks newly plugged-in drives and when each drive was last used (rv-33). Lives in the shell, not the sidebar, so
// it keeps watching while the sidebar is hidden. Stored per browser (localStorage), like other conveniences.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { busyDriveKeys, driveForRoute, touchDrive, trackArrivals, type SeenState } from './driveList';
import { readPref, writePref } from './prefs';
import type { Route } from './route';
import { useStore } from './store';

const KEY = 'pv:driveSeen';
/** A running task re-marks its drives as used at most this often, so a long run doesn't write every poll. */
const BUSY_TOUCH_MS = 60_000;

interface DriveActivity {
  fresh: Record<string, number>;
  touched: Record<string, number>;
  /** Mark a drive as interacted with (clears its "New" highlight). */
  touch: (key: string) => void;
}

const Ctx = createContext<DriveActivity>({ fresh: {}, touched: {}, touch: () => {} });
export const useDriveActivity = (): DriveActivity => useContext(Ctx);

export function DriveActivityProvider({ route, children }: { route: Route; children: ReactNode }) {
  const { drives, loaded, tasks, elsewhere } = useStore();
  const [seen, setSeen] = useState<SeenState | null>(() => readPref<SeenState | null>(KEY, null));

  const update = useCallback((f: (s: SeenState | null) => SeenState | null) => {
    setSeen((prev) => {
      const next = f(prev);
      if (next !== prev) writePref(KEY, next);
      return next;
    });
  }, []);

  // Each drive-list poll: who arrived, who left.
  useEffect(() => {
    if (loaded) update((prev) => trackArrivals(prev, drives, Date.now()));
  }, [drives, loaded, update]);

  // Opening a drive's page (drive, card or setup) is an interaction; so is staying on it while it is plugged back in.
  const routeDrive = driveForRoute(route, drives);
  const routeKey = routeDrive?.key;
  const routeFresh = routeKey !== undefined && seen?.fresh[routeKey] !== undefined;
  useEffect(() => {
    if (routeKey !== undefined) update((s) => (s ? touchDrive(s, routeKey, Date.now()) : s));
  }, [routeKey, routeFresh, update]);

  // Running something on a drive (import, backup, index, check, clear…) is an interaction.
  const busy = busyDriveKeys(tasks, elsewhere, drives).join('\n');
  useEffect(() => {
    if (!busy) return;
    const now = Date.now();
    update((s) => {
      if (!s) return s;
      let next = s;
      for (const k of busy.split('\n')) {
        if (next.fresh[k] !== undefined || now - (next.touched[k] ?? 0) >= BUSY_TOUCH_MS) next = touchDrive(next, k, now);
      }
      return next;
    });
  }, [busy, update]);

  const touch = useCallback((key: string) => update((s) => (s ? touchDrive(s, key, Date.now()) : s)), [update]);
  const value = useMemo(() => ({ fresh: seen?.fresh ?? {}, touched: seen?.touched ?? {}, touch }), [seen, touch]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
