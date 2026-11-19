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
  city: string | null;
  state: string | null;
  country: string | null;
  time_shift_s: number | null;
  creator: string | null;
  copyright: string | null;
  title: string | null;
  keywords: string[];
  dirty: boolean | null;
  rel_path: string | null;
  volume_id: string | null;
  /** The D-005 file name the photo has or would get on WORKING (rv-18); null without an indexed file. */
  vault_name?: string | null;
  /** Corrected UTC (clock shift applied), ISO; null without a capture time (rv-16). */
  utc_time: string | null;
  body_id: number | null;
  sequence_id: number | null;
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

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/** The index `d` steps from `i` in a list of `n`, wrapping at both ends (the viewer's arrows). */
export const wrapIndex = (i: number, d: number, n: number): number => (n <= 0 ? 0 : (((i + d) % n) + n) % n);

/** The size an image of `ratio` (width / height) takes when contained in a `w` by `h` box. */
export function fitBox(w: number, h: number, ratio: number): { w: number; h: number } {
  const fw = Math.min(w, h * ratio);
  return { w: fw, h: fw / ratio };
}

/** The timeline day a photo sits under: the camera's capture date, or UNDATED (as the server groups them). */
export const dayOf = (g: Pick<PhotoGroup, 'capture_time'>): string => (g.capture_time ? g.capture_time.slice(0, 10) : UNDATED);

/** The first of the photos in time order (undated last, then by id), or null for none (rv-19). */
export function firstInTime<T extends Pick<PhotoGroup, 'capture_time' | 'group_id'>>(list: T[]): T | null {
  let best: T | null = null;
  for (const g of list) {
    if (!best) best = g;
    else {
      const a = g.capture_time ?? '\uffff';
      const b = best.capture_time ?? '\uffff';
      if (a < b || (a === b && g.group_id < best.group_id)) best = g;
    }
  }
  return best;
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

/** The name to copy for one photo (rv-10): the basename of its file on disk, else the stem, else ''. */
export function copyName(g: Pick<PhotoGroup, 'rel_path' | 'canonical_stem'>): string {
  if (g.rel_path) return g.rel_path.split('/').filter(Boolean).pop() ?? g.canonical_stem ?? '';
  return g.canonical_stem ?? '';
}

/** The PhotoVault name to copy (rv-18): `vault_name` from the API, else the camera's name as before. */
export function vaultCopyName(g: Pick<PhotoGroup, 'rel_path' | 'canonical_stem' | 'vault_name'>): string {
  return g.vault_name || copyName(g);
}

/** Copy text with the Clipboard API, falling back to a hidden textarea and execCommand. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the fallback */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
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
export function commonValue(groups: PhotoGroup[], key: 'creator' | 'copyright' | 'title' | 'place_name' | 'city' | 'state' | 'country'): string | null {
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
  city: string | null;
  state: string | null;
  country: string | null;
  creator: string | null;
  copyright: string | null;
  title: string | null;
  kwAdd: string[];
  kwRemove: string[];
  shift: number | null;
}

export const NO_EDITS: Pending = { loc: null, place: null, city: null, state: null, country: null, creator: null, copyright: null, title: null, kwAdd: [], kwRemove: [], shift: null };

export function hasEdits(p: Pending): boolean {
  return Object.keys(buildFields(p)).length > 0;
}

export type ScopeName = 'place' | 'description' | 'credit' | 'clock';

/** Pending edits as the `fields` of POST /api/groups/meta, split by the scope each field belongs to (rv-16). */
export function buildFieldsByScope(p: Pending): Record<ScopeName, Record<string, unknown>> {
  const place: Record<string, unknown> = {};
  const description: Record<string, unknown> = {};
  const credit: Record<string, unknown> = {};
  const clock: Record<string, unknown> = {};
  if (p.loc) Object.assign(place, { lat: p.loc.lat, lon: p.loc.lon, alt: p.loc.alt, gps_source: p.loc.source });
  if (p.place !== null) place.place_name = p.place.trim() || null;
  for (const k of ['city', 'state', 'country'] as const) if (p[k] !== null) place[k] = p[k]!.trim() || null;
  for (const k of ['creator', 'copyright'] as const) if (p[k] !== null) credit[k] = p[k]!.trim() || null;
  if (p.title !== null) description.title = p.title.trim() || null;
  if (p.kwAdd.length) description.keywords_add = [...p.kwAdd];
  if (p.kwRemove.length) description.keywords_remove = [...p.kwRemove];
  if (p.shift !== null) clock.time_shift_s = p.shift;
  return { place, description, credit, clock };
}

/** Pending edits as the `fields` of POST /api/groups/meta. */
export function buildFields(p: Pending): Record<string, unknown> {
  const by = buildFieldsByScope(p);
  return { ...by.place, ...by.credit, ...by.description, ...by.clock };
}

export interface SaveRequest {
  ids: number[];
  fields: Record<string, unknown>;
}

/**
 * The POST /api/groups/meta requests that give each field only to its scope's photos: the selection plus that
 * scope's extra ids. Scopes with the same photos share one request. Fields nobody changed send nothing.
 */
export function planSaves(p: Pending, selectedIds: number[], extra: Record<ScopeName, number[]>): SaveRequest[] {
  const by = buildFieldsByScope(p);
  const out = new Map<string, SaveRequest>();
  for (const scope of ['place', 'credit', 'description', 'clock'] as const) {
    if (!Object.keys(by[scope]).length) continue;
    const ids = [...new Set([...selectedIds, ...extra[scope]])];
    const key = [...ids].sort((x, y) => x - y).join(',');
    const hit = out.get(key);
    if (hit) Object.assign(hit.fields, by[scope]);
    else out.set(key, { ids, fields: { ...by[scope] } });
  }
  return [...out.values()];
}

/** A group as it reads after the server applied `fields` (the same order as repo.update_group_meta). */
export function applyFields(g: PhotoGroup, f: Record<string, unknown>): PhotoGroup {
  const out: PhotoGroup = { ...g, keywords: [...g.keywords], dirty: true };
  if (typeof f.time_shift_s === 'number' && g.utc_time) {
    // keep the corrected UTC in step with the clock shift
    const ms = Date.parse(g.utc_time) + (f.time_shift_s - (g.time_shift_s ?? 0)) * 1000;
    if (!Number.isNaN(ms)) out.utc_time = `${new Date(ms).toISOString().slice(0, 19)}+00:00`;
  }
  for (const k of ['lat', 'lon', 'alt', 'gps_source', 'place_name', 'city', 'state', 'country', 'creator', 'copyright', 'title', 'time_shift_s'] as const) {
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

/** "23 h earlier" / "1 h 46 min later": a phone point's distance from the photo (delta = point minus photo). */
export function fmtApart(seconds: number): string {
  const abs = Math.abs(Math.round(seconds));
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const span = h >= 10 || (h > 0 && m === 0) ? `${h} h` : h > 0 ? `${h} h ${String(m).padStart(2, '0')} min` : `${Math.max(m, 1)} min`;
  return `${span} ${seconds < 0 ? 'earlier' : 'later'}`;
}

/** "−1 h 07" / "+1 h 28": a short signed gap for the stayed-put label. */
export function fmtGapShort(seconds: number): string {
  const sign = seconds < 0 ? '−' : '+';
  const abs = Math.abs(Math.round(seconds));
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  return h > 0 ? `${sign}${h} h ${String(m).padStart(2, '0')}` : `${sign}${Math.max(m, 1)} min`;
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

/** Google Maps at that spot (the documented Maps URLs format); only the coordinates leave the app. */
export const mapsUrl = (lat: number, lon: number): string =>
  `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lon.toFixed(6)}`;

// ---------------------------------------------------------------- related photos (rv-9, rv-16)

const naiveMs = (t: string): number => Date.parse(`${t.slice(0, 19)}Z`);
const fmtNaive = (ms: number): string => new Date(ms).toISOString().slice(0, 19);

/** A gap between neighbouring photos in seconds, 'day' for the whole day, or 'auto' to let the session size decide. */
export type Gap = number | 'day' | 'auto';
/** The numeric steps of the ladder, in seconds: 1 min, 5 min, 15 min, 1 h, 3 h. */
export const LADDER: readonly number[] = [60, 300, 900, 3600, 10800];
const AUTO_JUMP = 1.5;
const AUTO_FALLBACK_MAX_S = 900;

export function gapLabel(gap: Gap): string {
  if (gap === 'day') return 'Same day';
  if (gap === 'auto') return 'Auto';
  return gap % 3600 === 0 ? `${gap / 3600} h` : `${gap / 60} min`;
}

/** The moment a photo was taken in UTC (clock shift applied); the naive capture time when the API gave no UTC. */
function timeMs(g: PhotoGroup): number {
  if (g.utc_time) {
    const ms = Date.parse(g.utc_time);
    if (!Number.isNaN(ms)) return ms;
  }
  return g.capture_time ? naiveMs(g.capture_time) : Number.NaN;
}

const CAMERA_DIR = /^(dcim|\d{3}[a-z0-9_]{5})$/i;

/**
 * The named folder a photo sits in: its nearest parent that is not DCIM or a camera's numbered folder ("100NCZ_6").
 * '' when it only sits in camera folders, undefined when the path is not known.
 */
export function namedFolder(relPath: string | null): string | undefined {
  if (!relPath) return undefined;
  const dirs = relPath.split('/').slice(0, -1);
  for (let i = dirs.length - 1; i >= 0; i -= 1) if (dirs[i] && !CAMERA_DIR.test(dirs[i]!)) return dirs[i];
  return '';
}

function chainBroken(a: PhotoGroup, b: PhotoGroup): boolean {
  if (a.shoot_id !== b.shoot_id) return true;
  const fa = namedFolder(a.rel_path);
  const fb = namedFolder(b.rel_path);
  return fa !== undefined && fb !== undefined && fa !== fb;
}

/**
 * Which step Auto takes, from the session sizes of the numeric steps (`counts[i]` for `LADDER[i]`). Steps with an
 * empty session are skipped. The step just before the largest jump c[next] / c[this] wins when that jump is at least
 * 1.5; otherwise the largest non-empty step up to 15 min, else 15 min.
 */
export function autoStep(counts: number[]): number {
  const live = counts.map((c, i) => ({ c, i })).filter((x) => x.c > 0);
  let best: { ratio: number; i: number } | null = null;
  for (let k = 0; k + 1 < live.length; k += 1) {
    const ratio = live[k + 1]!.c / live[k]!.c;
    if (!best || ratio > best.ratio) best = { ratio, i: live[k]!.i };
  }
  if (best && best.ratio >= AUTO_JUMP) return LADDER[best.i]!;
  const small = live.filter((x) => LADDER[x.i]! <= AUTO_FALLBACK_MAX_S);
  return small.length ? LADDER[small[small.length - 1]!.i]! : AUTO_FALLBACK_MAX_S;
}

/** Session size (selected photos not counted) for each numeric step of the ladder. */
export function relatedCounts(candidates: PhotoGroup[], selected: PhotoGroup[]): number[] {
  return LADDER.map((s) => relatedSession(candidates, selected, s).length);
}

/** The concrete step (seconds, or 'day') a gap means for this selection: Auto becomes the step it picks. */
export function resolveGap(candidates: PhotoGroup[], selected: PhotoGroup[], gap: Gap): number | 'day' {
  return gap === 'auto' ? autoStep(relatedCounts(candidates, selected)) : gap;
}

/**
 * Photos in the same session as the selection: walking outwards from the selection's earliest and latest moment (UTC),
 * each next photo joins while it is within the gap of the previous one, and the walk stops at a shoot change or a
 * change of named folder. 'day' takes every candidate from a selected photo's capture day. Frames that share a
 * sequence (a bracket) join together, so a sequence is never split. The selected photos are not returned.
 */
export function relatedSession(candidates: PhotoGroup[], selected: PhotoGroup[], gap: Gap): PhotoGroup[] {
  const sel = new Set(selected.map((g) => g.group_id));
  const timedSel = selected.filter((g) => !Number.isNaN(timeMs(g)));
  if (!timedSel.length) return [];
  const step = gap === 'auto' ? autoStep(relatedCounts(candidates, selected)) : gap;
  const seen = new Set<number>();
  const all: PhotoGroup[] = [];
  for (const g of [...candidates, ...timedSel]) {
    if (Number.isNaN(timeMs(g)) || seen.has(g.group_id)) continue;
    seen.add(g.group_id);
    all.push(g);
  }
  all.sort((a, b) => timeMs(a) - timeMs(b));
  const member = new Set<number>();
  if (step === 'day') {
    const days = new Set(timedSel.map((g) => g.capture_time?.slice(0, 10)));
    for (const g of all) if (days.has(g.capture_time?.slice(0, 10))) member.add(g.group_id);
  } else {
    let lo = all.findIndex((g) => sel.has(g.group_id));
    let hi = all.length - 1;
    while (hi > lo && !sel.has(all[hi]!.group_id)) hi -= 1;
    const near = (i: number, j: number) => Math.abs(timeMs(all[j]!) - timeMs(all[i]!)) / 1000 <= step && !chainBroken(all[i]!, all[j]!);
    while (lo > 0 && near(lo - 1, lo)) lo -= 1;
    while (hi < all.length - 1 && near(hi, hi + 1)) hi += 1;
    for (const g of all.slice(lo, hi + 1)) member.add(g.group_id);
  }
  const seqs = new Set<number>();
  for (const g of all) if (member.has(g.group_id) && g.sequence_id !== null) seqs.add(g.sequence_id);
  for (const g of all) if (g.sequence_id !== null && seqs.has(g.sequence_id)) member.add(g.group_id);
  return all.filter((g) => member.has(g.group_id) && !sel.has(g.group_id));
}

/** The capture-time range to fetch around the selection: 12 h either side (enough for 3 h and Auto), or the selected days for 'day'. */
export function relatedWindow(selected: PhotoGroup[], gap: Gap): { after: string; before: string } {
  const times = selected.filter((g) => g.capture_time).map((g) => naiveMs(g.capture_time!));
  if (!times.length) return { after: '', before: '' };
  const min = Math.min(...times);
  const max = Math.max(...times);
  if (gap === 'day') {
    const dayStart = (ms: number) => ms - (((ms % 86400000) + 86400000) % 86400000);
    return { after: fmtNaive(dayStart(min)), before: fmtNaive(dayStart(max) + 86400000 - 1000) };
  }
  return { after: fmtNaive(min - 12 * 3600000), before: fmtNaive(max + 12 * 3600000) };
}

/** The range that covers every gap in `gaps` (an ISO string compares in time order). */
export function relatedWindowFor(selected: PhotoGroup[], gaps: Gap[]): { after: string; before: string } {
  const wins = [relatedWindow(selected, 'auto'), ...(gaps.includes('day') ? [relatedWindow(selected, 'day')] : [])];
  if (!wins[0]!.after) return { after: '', before: '' };
  return { after: wins.map((w) => w.after).sort()[0]!, before: wins.map((w) => w.before).sort().reverse()[0]! };
}

// ---------------------------------------------------------------- scopes (rv-16)

/** How far a field's edits reach: the selection alone, or a session / same-day set of related photos. */
export type Reach = 'selection' | Gap;
/** The clock shift's reach: always the same camera body. */
export type ClockReach = 'selection' | 'session' | 'day';

export interface ScopeDefaults {
  place: Reach;
  description: Reach;
  credit: Reach;
  clock: ClockReach;
}
export const SCOPE_DEFAULTS: ScopeDefaults = { place: 'auto', description: 'selection', credit: 'day', clock: 'day' };

/** The related photos (not the selection) a reach gives. */
export function reachPhotos(reach: Reach, candidates: PhotoGroup[], selected: PhotoGroup[]): PhotoGroup[] {
  return reach === 'selection' ? [] : relatedSession(candidates, selected, reach);
}

/** The photos a clock shift reaches: only frames from a camera body that is in the selection (matched on body_id). */
export function clockPhotos(reach: ClockReach, candidates: PhotoGroup[], selected: PhotoGroup[], sessionGap: Gap): PhotoGroup[] {
  if (reach === 'selection') return [];
  const bodies = new Set(selected.map((g) => g.body_id).filter((b): b is number => b !== null));
  const pool = relatedSession(candidates, selected, reach === 'day' ? 'day' : sessionGap);
  return pool.filter((g) => g.body_id !== null && bodies.has(g.body_id));
}

// ---------------------------------------------------------------- place names (sp-3)

/** The place fields a phone point, a "near this time" row or a Nominatim result can carry. */
export interface PlaceFields {
  place_name: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
}

/** "Harbourton, Westshire" from a point's place fields (city, else the place name; then the state); '' when none. */
export function placeLabel(f: PlaceFields): string {
  return [f.city || f.place_name, f.state].filter(Boolean).join(', ');
}

/** The pending place edits for choosing a place. Empty fields stay null, so they are left untouched. */
export function placeEdits(f: PlaceFields): Pick<Pending, 'place' | 'city' | 'state' | 'country'> {
  return { place: f.place_name || null, city: f.city || null, state: f.state || null, country: f.country || null };
}

/** City, state and country from a Nominatim `address` object (addressdetails=1). */
export function nominatimPlace(name: string, address: Record<string, string> | undefined): PlaceFields {
  const a = address ?? {};
  return {
    place_name: name || null,
    city: a.city || a.town || a.village || a.municipality || a.hamlet || a.suburb || null,
    state: a.state || a.region || a.county || null,
    country: a.country || null,
  };
}
