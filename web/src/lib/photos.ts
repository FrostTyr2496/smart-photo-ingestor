// The Photos view's data and the editor's rules (rv-7). Pure functions, so they are tested in photos.test.ts.

/** One capture group from GET /api/groups: a frame (RAW + JPEG + sidecars), with its saved metadata. */
export interface PhotoGroup {
  group_id: number;
  canonical_stem: string | null;
  capture_time: string | null;
  capture_offset: string | null;
  shoot_id: number | null;
  body_nickname: string | null;
  shoot_name: string | null;
  lat: number | null;
  lon: number | null;
  alt: number | null;
  gps_source: string | null;
  place_name: string | null;
  time_shift_s: number | null;
  creator: string | null;
  copyright: string | null;
  title: string | null;
  keywords: string[];
  dirty: boolean | null;
  rel_path: string | null;
  volume_id: string | null;
}

export interface PhotoDay {
  day: string; // YYYY-MM-DD, or UNDATED
  groups: number;
  bodies: string[];
}

export interface MissingCounts {
  groups: number;
  gps: number;
  creator: number;
  keywords: number;
}

export interface PhotoSettings {
  creator: string;
  copyright: string;
  default_offset: string;
}

export const UNDATED = 'undated';

export type Missing = '' | 'any' | 'gps' | 'creator' | 'keywords';
export const MISSING_LABEL: Record<Missing, string> = {
  '': 'All photos',
  any: 'Missing anything',
  gps: 'Missing location',
  creator: 'Missing creator',
  keywords: 'Missing keywords',
};

export const GPS_LABEL: Record<string, string> = {
  camera: 'From the camera',
  match: 'Matched by time',
  interpolated: 'Matched by time',
  map: 'Picked on the map',
  search: 'Place search',
  neighbour: 'From a nearby photo',
};

// ---------------------------------------------------------------- days

export function dayLabel(day: string): string {
  if (day === UNDATED) return 'No date';
  const d = new Date(`${day}T12:00:00`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

/** Years, newest first, each with its months (1-12, newest first) and the first day listed for that month. */
export function monthRail(days: PhotoDay[]): Array<{ year: string; months: Array<{ month: number; day: string; groups: number }> }> {
  const years: Array<{ year: string; months: Array<{ month: number; day: string; groups: number }> }> = [];
  for (const d of days) {
    if (d.day === UNDATED) continue;
    const year = d.day.slice(0, 4);
    const month = Number(d.day.slice(5, 7));
    let y = years[years.length - 1];
    if (!y || y.year !== year) {
      y = { year, months: [] };
      years.push(y);
    }
    const m = y.months[y.months.length - 1];
    if (m && m.month === month) m.groups += d.groups;
    else y.months.push({ month, day: d.day, groups: d.groups });
  }
  return years;
}

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "14:03" from a capture time, or "" when there is none. */
export function clockTime(iso: string | null): string {
  if (!iso) return '';
  const m = /T(\d{2}:\d{2})/.exec(iso);
  return m ? m[1]! : '';
}

// ---------------------------------------------------------------- selection

/** Ids from `from` to `to` (inclusive, either order) in display order; just `to` when either is not shown. */
export function rangeIds(order: number[], from: number | null, to: number): number[] {
  const a = from === null ? -1 : order.indexOf(from);
  const b = order.indexOf(to);
  if (a < 0 || b < 0) return [to];
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  return order.slice(lo, hi + 1);
}

/** The value every group shares, '' when all are empty, or null when they differ. */
export function commonValue(groups: PhotoGroup[], key: 'creator' | 'copyright' | 'title' | 'place_name'): string | null {
  const values = new Set(groups.map((g) => g[key] ?? ''));
  return values.size === 1 ? [...values][0]! : null;
}

export interface KeywordChip {
  word: string;
  on: number; // how many selected photos have it (after pending edits)
  pending: 'add' | 'remove' | null;
}

/** Keywords across the selection, with how many photos carry each, after pending additions and removals. */
export function keywordChips(groups: PhotoGroup[], add: string[], remove: string[]): KeywordChip[] {
  const seen = new Map<string, KeywordChip>();
  for (const g of groups) {
    for (const k of new Set(g.keywords.map((w) => w.trim()).filter(Boolean))) {
      const lc = k.toLowerCase();
      const chip = seen.get(lc);
      if (chip) chip.on += 1;
      else seen.set(lc, { word: k, on: 1, pending: null });
    }
  }
  const dropped = new Set(remove.map((w) => w.toLowerCase()));
  const out = [...seen.values()].filter((c) => !dropped.has(c.word.toLowerCase()));
  for (const w of add) {
    const chip = out.find((c) => c.word.toLowerCase() === w.toLowerCase());
    if (chip) {
      chip.on = groups.length;
      chip.pending = 'add';
    } else out.push({ word: w, on: groups.length, pending: 'add' });
  }
  return out;
}

// ---------------------------------------------------------------- pending edits

export interface PendingLoc {
  lat: number | null; // null with lon null: remove the location
  lon: number | null;
  alt: number | null;
  source: 'match' | 'map' | 'search' | 'neighbour' | null;
}

/** What the user changed in the editor. null means "not touched", so Save leaves that field alone on every photo. */
export interface Pending {
  loc: PendingLoc | null;
  place: string | null;
  creator: string | null;
  copyright: string | null;
  title: string | null;
  kwAdd: string[];
  kwRemove: string[];
  shift: number | null;
}

export const NO_EDITS: Pending = { loc: null, place: null, creator: null, copyright: null, title: null, kwAdd: [], kwRemove: [], shift: null };

export function hasEdits(p: Pending): boolean {
  return Object.keys(buildFields(p)).length > 0;
}

/** Pending edits as the `fields` of POST /api/groups/meta. */
export function buildFields(p: Pending): Record<string, unknown> {
  const f: Record<string, unknown> = {};
  if (p.loc) Object.assign(f, { lat: p.loc.lat, lon: p.loc.lon, alt: p.loc.alt, gps_source: p.loc.source });
  if (p.place !== null) f.place_name = p.place.trim() || null;
  for (const k of ['creator', 'copyright', 'title'] as const) if (p[k] !== null) f[k] = p[k]!.trim() || null;
  if (p.kwAdd.length) f.keywords_add = [...p.kwAdd];
  if (p.kwRemove.length) f.keywords_remove = [...p.kwRemove];
  if (p.shift !== null) f.time_shift_s = p.shift;
  return f;
}

/** A group as it reads after the server applied `fields` (the same order as repo.update_group_meta). */
export function applyFields(g: PhotoGroup, f: Record<string, unknown>): PhotoGroup {
  const out: PhotoGroup = { ...g, keywords: [...g.keywords], dirty: true };
  for (const k of ['lat', 'lon', 'alt', 'gps_source', 'place_name', 'creator', 'copyright', 'title', 'time_shift_s'] as const) {
    if (k in f) (out as unknown as Record<string, unknown>)[k] = f[k];
  }
  if (Array.isArray(f.keywords_add)) out.keywords = out.keywords.concat(f.keywords_add as string[]);
  if (Array.isArray(f.keywords_remove)) {
    const drop = new Set((f.keywords_remove as string[]).map((w) => w.trim().toLowerCase()));
    out.keywords = out.keywords.filter((w) => !drop.has(w.trim().toLowerCase()));
  }
  return out;
}

/** Add comma-separated words to the pending keywords, undoing a pending removal of the same word. */
export function addKeywords(p: Pending, text: string, shown: string[]): Pending {
  let kwAdd = [...p.kwAdd];
  let kwRemove = [...p.kwRemove];
  const have = new Set(shown.map((w) => w.toLowerCase()));
  for (const raw of text.split(',')) {
    const word = raw.trim();
    if (!word) continue;
    const lc = word.toLowerCase();
    if (kwRemove.some((w) => w.toLowerCase() === lc)) {
      kwRemove = kwRemove.filter((w) => w.toLowerCase() !== lc);
      have.add(lc);
    } else if (!kwAdd.some((w) => w.toLowerCase() === lc)) {
      kwAdd.push(word);
      have.add(lc);
    }
  }
  return { ...p, kwAdd, kwRemove };
}

export function removeKeyword(p: Pending, word: string): Pending {
  const lc = word.toLowerCase();
  if (p.kwAdd.some((w) => w.toLowerCase() === lc)) return { ...p, kwAdd: p.kwAdd.filter((w) => w.toLowerCase() !== lc) };
  if (p.kwRemove.some((w) => w.toLowerCase() === lc)) return p;
  return { ...p, kwRemove: [...p.kwRemove, word] };
}

// ---------------------------------------------------------------- time

/** "+01:30" → 90 (minutes); null when it is not an offset. */
export function parseOffset(text: string | null | undefined): number | null {
  const m = /^([+-])(\d{2}):?(\d{2})$/.exec((text || '').trim());
  if (!m) return null;
  return (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3]));
}

/** The local wall time of a frame after a clock shift of `shift` seconds: "2021-01-15 00:39:58 (-05:00)". */
export function shiftedTime(g: Pick<PhotoGroup, 'capture_time' | 'capture_offset'>, shift: number, defaultOffset: string): string {
  const iso = g.capture_time;
  if (!iso) return 'no capture time';
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  const zone = g.capture_offset || defaultOffset || '+00:00';
  const wall = Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +m[6]!) + shift * 1000;
  return `${new Date(wall).toISOString().slice(0, 19).replace('T', ' ')} (${zone})`;
}

/** Seconds from a sign and h/m/s fields. */
export function shiftSeconds(sign: '+' | '-', h: number, m: number, s: number): number {
  const total = Math.max(0, h || 0) * 3600 + Math.max(0, m || 0) * 60 + Math.max(0, s || 0);
  return sign === '-' ? -total : total;
}

export function splitShift(sec: number): { sign: '+' | '-'; h: number; m: number; s: number } {
  const a = Math.abs(Math.round(sec));
  return { sign: sec < 0 ? '-' : '+', h: Math.floor(a / 3600), m: Math.floor((a % 3600) / 60), s: a % 60 };
}

export function fmtDelta(seconds: number): string {
  const sign = seconds < 0 ? '−' : '+';
  const abs = Math.abs(Math.round(seconds));
  if (abs < 60) return `${sign}${abs} s`;
  if (abs < 3600) return `${sign}${Math.floor(abs / 60)} min ${String(abs % 60).padStart(2, '0')} s`;
  return `${sign}${Math.floor(abs / 3600)} h ${String(Math.floor((abs % 3600) / 60)).padStart(2, '0')} min`;
}

/** Seconds between two capture times, ignoring zones (both are the camera's clock). */
export function secondsBetween(a: string | null, b: string | null): number | null {
  if (!a || !b) return null;
  const ta = Date.parse(`${a.slice(0, 19)}Z`);
  const tb = Date.parse(`${b.slice(0, 19)}Z`);
  return Number.isNaN(ta) || Number.isNaN(tb) ? null : (tb - ta) / 1000;
}

/** The photo nearest in time to `target` that has a location and is not selected, within `windowS` seconds. */
export function nearestWithLocation(target: PhotoGroup, pool: PhotoGroup[], selected: Set<number>, windowS: number): { group: PhotoGroup; delta: number } | null {
  let best: { group: PhotoGroup; delta: number } | null = null;
  for (const g of pool) {
    if (g.lat === null || g.lon === null || selected.has(g.group_id)) continue;
    const d = secondsBetween(target.capture_time, g.capture_time);
    if (d === null || Math.abs(d) > windowS) continue;
    if (!best || Math.abs(d) < Math.abs(best.delta)) best = { group: g, delta: d };
  }
  return best;
}

export const fmtCoord = (lat: number, lon: number): string => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
