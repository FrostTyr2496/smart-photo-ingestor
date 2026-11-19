// The sidebar's drive list (rv-33): which drives were just plugged in, when each was last used, and how the list is
// sorted and filtered. Pure functions; the React side is in driveActivity.tsx and Sidebar.tsx.
import { fsLabel, parseSize } from './format';
import { argvWords, driveName, isCardDrive, roleKey, sameMount, taskMounts } from './labels';
import type { Route } from './route';
import type { Drive, ExtRun, TaskRec } from './types';

/** What the browser remembers between polls and page loads (localStorage, per browser). */
export interface SeenState {
  /** Drive keys and mount paths that were connected at the last poll. */
  keys: string[];
  mounts: string[];
  /** Drives plugged in since, not yet interacted with: key -> when it arrived (ms). */
  fresh: Record<string, number>;
  /** Last interaction with each drive (opened, ran something on it, plugged in): key -> ms. */
  touched: Record<string, number>;
}

/**
 * Compare the drives now connected with the last poll. A drive whose key and mount path were both absent last time
 * has just been plugged in (seen before or not), so it is fresh until touched. With no memory at all (first visit),
 * nothing is fresh: every drive would light up. An unplugged drive stops being fresh.
 */
export function trackArrivals(prev: SeenState | null, drives: Drive[], now: number): SeenState {
  const on = drives.filter((d) => d.mounted);
  const keys = on.map((d) => d.key);
  const mounts = on.map((d) => d.mount);
  if (!prev) return { keys, mounts, fresh: {}, touched: {} };
  const fresh: Record<string, number> = {};
  const touched = { ...prev.touched };
  for (const d of on) {
    const was = prev.fresh[d.key];
    if (was !== undefined) fresh[d.key] = was;
    else if (!prev.keys.includes(d.key) && !prev.mounts.includes(d.mount)) {
      fresh[d.key] = now;
      touched[d.key] = now;
    }
  }
  return { keys, mounts, fresh, touched };
}

/** The user interacted with a drive: it is no longer new, and it moves up in "Recently used". */
export function touchDrive(s: SeenState, key: string, now: number): SeenState {
  const { [key]: _gone, ...fresh } = s.fresh;
  return { ...s, fresh, touched: { ...s.touched, [key]: now } };
}

/** When a drive was last used: the latest of an interaction this browser saw and a step run on it (index, hash…). */
export function lastActivity(d: Drive, touched: Record<string, number>): number {
  let best = touched[d.key] ?? 0;
  for (const s of Object.values(d.steps || {})) {
    const t = s?.at ? Date.parse(s.at) : NaN;
    if (t > best) best = t;
  }
  return best;
}

// ---------------------------------------------------------------- type and format

export type TypeKey = 'card' | 'working' | 'master' | 'legacy' | 'new' | 'unset';
export const TYPE_ORDER: TypeKey[] = ['card', 'working', 'master', 'legacy', 'new', 'unset'];
export const TYPE_LABEL: Record<TypeKey, string> = {
  card: 'Cards',
  working: 'Working',
  master: 'Master',
  legacy: 'Legacy',
  new: 'Not set up',
  unset: 'No role',
};

export function typeKey(d: Drive): TypeKey {
  if (isCardDrive(d)) return 'card';
  if (!d.volume_id) return 'new';
  const k = roleKey(d.role);
  return k === 'card' ? 'card' : k;
}

export const formatKey = (d: Drive): string => fsLabel(d.fs) || 'Unknown';

// ---------------------------------------------------------------- sort and filter

export type SortKey = 'default' | 'recent' | 'type' | 'format' | 'size' | 'free' | 'name';
export const SORTS: Array<{ value: SortKey; text: string }> = [
  { value: 'default', text: 'Default' },
  { value: 'recent', text: 'Recently used' },
  { value: 'type', text: 'Type' },
  { value: 'format', text: 'Format' },
  { value: 'size', text: 'Size' },
  { value: 'free', text: 'Free space' },
  { value: 'name', text: 'Name' },
];

export interface DriveFilter {
  types: TypeKey[];
  formats: string[];
}
export const NO_FILTER: DriveFilter = { types: [], formats: [] };
export const filterCount = (f: DriveFilter): number => f.types.length + f.formats.length;

const byName = (a: Drive, b: Drive) => driveName(a).localeCompare(driveName(b), undefined, { sensitivity: 'base', numeric: true });
/** Bigger first; unknown sizes last. */
const desc = (x: number | null, y: number | null) => (x === null ? (y === null ? 0 : 1) : y === null ? -1 : y - x);

/**
 * Sort within a group. Fresh drives always come first (newest arrival first), whatever the sort, so a card just
 * plugged in is at the top. "Default" keeps the server's order. Ties fall back to the name.
 */
export function sortDrives(list: Drive[], sort: SortKey, seen: Pick<SeenState, 'fresh' | 'touched'>): Drive[] {
  const index = new Map(list.map((d, i) => [d.key, i]));
  const cmp = (a: Drive, b: Drive): number => {
    const fa = seen.fresh[a.key];
    const fb = seen.fresh[b.key];
    if (fa !== undefined || fb !== undefined) {
      if (fa === undefined) return 1;
      if (fb === undefined) return -1;
      if (fa !== fb) return fb - fa;
    }
    let c = 0;
    switch (sort) {
      case 'recent':
        c = lastActivity(b, seen.touched) - lastActivity(a, seen.touched);
        break;
      case 'type':
        c = TYPE_ORDER.indexOf(typeKey(a)) - TYPE_ORDER.indexOf(typeKey(b));
        break;
      case 'format':
        c = formatKey(a).localeCompare(formatKey(b));
        break;
      case 'size':
        c = desc(parseSize(a.size), parseSize(b.size));
        break;
      case 'free':
        c = desc(parseSize(a.free), parseSize(b.free));
        break;
      case 'name':
        break;
      default:
        return (index.get(a.key) ?? 0) - (index.get(b.key) ?? 0);
    }
    return c || byName(a, b);
  };
  return [...list].sort(cmp);
}

/** Keep drives that match every chosen facet (any value within a facet). A fresh drive always shows. */
export function filterDrives(list: Drive[], f: DriveFilter, fresh: Record<string, number>): Drive[] {
  return list.filter(
    (d) =>
      fresh[d.key] !== undefined ||
      ((!f.types.length || f.types.includes(typeKey(d))) && (!f.formats.length || f.formats.includes(formatKey(d)))),
  );
}

/** The types and formats present among these drives, in display order, for the filter chips. */
export function facetOptions(drives: Drive[]): { types: TypeKey[]; formats: string[] } {
  const types = new Set(drives.map(typeKey));
  const formats = [...new Set(drives.map(formatKey))].sort((a, b) => (a === 'Unknown' ? 1 : b === 'Unknown' ? -1 : a.localeCompare(b)));
  return { types: TYPE_ORDER.filter((t) => types.has(t)), formats };
}

/** "New device detected", or "2 new devices detected". */
export const freshHeadline = (n: number): string => (n === 1 ? 'New device detected' : `${n} new devices detected`);

// ---------------------------------------------------------------- what counts as an interaction

/** The drive a page is about: its drive page, its card page, or its setup page. */
export function driveForRoute(route: Route, drives: Drive[]): Drive | undefined {
  if (route.view === 'setup') return drives.find((d) => d.key === route.key);
  if ((route.view === 'drive' || route.view === 'cards') && route.id) return drives.find((d) => d.volume_id === route.id);
  return undefined;
}

/** Keys of the drives a running task works on (UI-started or from a terminal). */
export function busyDriveKeys(tasks: TaskRec[], elsewhere: ExtRun[], drives: Drive[]): string[] {
  const mounts = new Set<string>();
  for (const t of tasks) if (!t.final) for (const m of taskMounts(argvWords(t.argv))) mounts.add(m);
  for (const e of elsewhere) for (const m of taskMounts(e.words || [])) mounts.add(m);
  return drives.filter((d) => [...mounts].some((m) => sameMount(m, d.mount))).map((d) => d.key);
}
