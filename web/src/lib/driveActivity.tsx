// Tracks newly plugged-in drives and when each drive was last used (rv-33). Lives in the shell, not the sidebar, so
// it keeps watching while the sidebar is hidden. Stored per browser (localStorage), like other conveniences.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { busyDriveKeys, driveForRoute, driveHref, seeDrive, touchDrive, trackArrivals, unplugged, type SeenState } from './driveList';
import { driveName } from './labels';
import { takeExpected } from './eject';
import { readPref, writePref } from './prefs';
import type { Route } from './route';
import { useStore } from './store';

// v2 (rv-41): v1 counted page opens as use, so its "touched" times are dropped rather than trusted.
const KEY = 'pv:driveSeen2';
/** A running task re-marks its drives as used at most this often, so a long run doesn't write every poll. */
const BUSY_TOUCH_MS = 60_000;

interface DriveActivity {
  fresh: Record<string, number>;
  touched: Record<string, number>;
  /** Mark a drive as seen (clears its "New" highlight; does not reorder "Recently used"). */
  touch: (key: string) => void;
  /** Drives unplugged while this page was open: key -> when (ms). Cleared when a drive comes back (rv-38). */
  left: Record<string, number>;
}

const Ctx = createContext<DriveActivity>({ fresh: {}, touched: {}, touch: () => {}, left: {} });
export const useDriveActivity = (): DriveActivity => useContext(Ctx);

export function DriveActivityProvider({ route, children }: { route: Route; children: ReactNode }) {
  const { drives, loaded, tasks, elsewhere, toast } = useStore();
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

  // Each poll: who was unplugged since the last one (rv-38). A popup names it (a warning when a task was using it),
  // with an Open link to its page; the sidebar and the drive's page show it as just pulled.
  const mountedRef = useRef<string[] | null>(null);
  const [left, setLeft] = useState<Record<string, number>>({});
  const busyRef = useRef<string[]>([]);
  useEffect(() => {
    if (!loaded) return;
    // An eject the user asked for is not news: no popup, no amber pulse (rv-43).
    const gone = unplugged(mountedRef.current, drives).filter((d) => !takeExpected(d.key));
    const on = drives.filter((d) => d.mounted).map((d) => d.key);
    mountedRef.current = on;
    setLeft((l) => {
      const back = Object.keys(l).filter((k) => on.includes(k));
      if (!gone.length && !back.length) return l;
      const next = { ...l };
      for (const k of back) delete next[k];
      for (const d of gone) next[d.key] = Date.now();
      return next;
    });
    for (const d of gone) {
      const wasBusy = busyRef.current.includes(d.key);
      toast(
        wasBusy ? `${driveName(d)} was unplugged while a task was using it. Check Tasks to see whether it paused.` : `${driveName(d)} was unplugged.`,
        wasBusy ? 'err' : 'ok',
        { label: 'Open', run: () => (window.location.hash = driveHref(d)) },
      );
    }
  }, [drives, loaded, toast]);

  // Opening a drive's page (drive, card or setup), or staying on it while it is plugged back in, clears "New". It is
  // not use: it does not move the drive in "Recently used" (rv-41).
  const routeDrive = driveForRoute(route, drives);
  const routeKey = routeDrive?.key;
  const routeFresh = routeKey !== undefined && seen?.fresh[routeKey] !== undefined;
  useEffect(() => {
    if (routeKey !== undefined) update((s) => (s ? seeDrive(s, routeKey) : s));
  }, [routeKey, routeFresh, update]);

  // Running something on a drive (import, backup, index, check, clear…) is device-level use: it moves the drive up.
  const busy = busyDriveKeys(tasks, elsewhere, drives).join('\n');
  busyRef.current = busy ? busy.split('\n') : [];
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

  // Dismissing "New" or opening the drive: seen, not used (rv-41).
  const touch = useCallback((key: string) => update((s) => (s ? seeDrive(s, key) : s)), [update]);
  const value = useMemo(() => ({ fresh: seen?.fresh ?? {}, touched: seen?.touched ?? {}, touch, left }), [seen, touch, left]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
