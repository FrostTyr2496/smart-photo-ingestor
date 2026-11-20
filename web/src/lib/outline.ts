import { fmtBytes, plural } from './format';
import { dayLabel, MONTHS, UNDATED } from './photos';

/** The trips outline (rv-46, D-134): `GET /api/outline`. */
export type OutlineState = 'accepted' | 'proposed';

export interface OutlineShoot {
  key: string;
  shoot_id: number | null;
  proposal_id: string | null;
  state: OutlineState;
  title: string;
  name: string;
  place: string | null;
  subject: string | null;
  shoot_date: string | null;
  wall: { start: string | null; end: string | null };
  groups: number;
  bodies: string[];
  volumes: Array<{ volume_id: string | null; label: string; frames: number }>;
  sample_group_ids: number[];
  days: Array<{ day: string; groups: number }>;
  trip_key: string | null;
  /** Only with a drive filter (`volume_id`). */
  files?: number;
  bytes?: number;
}

export interface OutlineTrip {
  key: string;
  name: string;
  state: OutlineState;
  start_date: string | null;
  end_date: string | null;
  groups: number;
  shoots: OutlineShoot[];
  files?: number;
  bytes?: number;
}

export interface OutlineDay {
  key: string;
  day: string;
  groups: number;
  files?: number;
  bytes?: number;
}

export type OutlineItem = ({ kind: 'trip' } & OutlineTrip) | ({ kind: 'shoot' } & OutlineShoot) | ({ kind: 'day' } & OutlineDay);

export interface Outline {
  plan_id: string | null;
  frames: number;
  trips: OutlineTrip[];
  items: OutlineItem[];
  other?: { files: number; bytes: number };
}

/** Title shading (D-134): normal ink when accepted, the review hue at about 70 % when proposed. */
export const titleTone = (state: OutlineState): string => (state === 'accepted' ? 'text-ink' : 'text-maybe/70');

const parts = (iso: string): { y: number; m: number; d: number } | null => {
  const x = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return x ? { y: +x[1]!, m: +x[2]!, d: +x[3]! } : null;
};
const fmtParts = (p: { y: number; m: number; d: number }): string => `${p.d} ${MONTHS[p.m - 1]} ${p.y}`;

/** "9–12 Sep 2023", "30 Sep – 2 Oct 2023", "30 Dec 2023 – 2 Jan 2024", or one date when both ends match. */
export function dateRange(start: string | null | undefined, end: string | null | undefined): string {
  const a = start ? parts(start) : null;
  const b = end ? parts(end) : null;
  if (!a && !b) return '';
  if (!a || !b) return fmtParts((a ?? b)!);
  if (a.y === b.y && a.m === b.m) return a.d === b.d ? fmtParts(a) : `${a.d}–${b.d} ${MONTHS[a.m - 1]} ${a.y}`;
  if (a.y === b.y) return `${a.d} ${MONTHS[a.m - 1]} – ${b.d} ${MONTHS[b.m - 1]} ${a.y}`;
  return `${fmtParts(a)} – ${fmtParts(b)}`;
}

/** "14:03" from the time part of a naive ISO string. */
const hm = (t: string | null | undefined): string => (t && t.length >= 16 ? t.slice(11, 16) : '');

/** A shoot's quiet line: date, wall-clock range, bodies, drives. */
export function shootLine(s: OutlineShoot): string {
  const a = hm(s.wall.start);
  const b = hm(s.wall.end);
  const span = a && b ? (a === b ? a : `${a}–${b}`) : '';
  const date = s.shoot_date ? dayLabel(s.shoot_date) : '';
  return [date, span, s.bodies.join(', '), s.volumes.map((v) => v.label).slice(0, 3).join(', ')].filter(Boolean).join(' · ');
}

/** "312 files · 9.8 GB", or "312 photos" when the outline has no drive counts. */
export const countsLine = (x: { groups: number; files?: number; bytes?: number }): string =>
  x.files !== undefined ? `${plural(x.files, 'file')} · ${fmtBytes(x.bytes ?? 0)}` : plural(x.groups, 'photo');

/** A loadable section of the trips view: a shoot (key `s7` or a proposal id) or one loose day (`loose:<day>`). */
export interface Section {
  key: string;
  groups: number;
  /** The days it spans, for finding a photo by its day. */
  days: string[];
}

/** Every loadable section in display order: trips' shoots, lone shoots, loose days. */
export function sectionsOf(items: OutlineItem[]): Section[] {
  const out: Section[] = [];
  const shoot = (s: OutlineShoot) => out.push({ key: s.key, groups: s.groups, days: s.days.map((d) => d.day) });
  for (const it of items) {
    if (it.kind === 'trip') it.shoots.forEach(shoot);
    else if (it.kind === 'shoot') shoot(it);
    else out.push({ key: it.key, groups: it.groups, days: [it.day] });
  }
  return out;
}

/** Keep only the shoot with this id (the Photos "Shoot" filter); trips left empty drop out. */
export function onlyShoot(items: OutlineItem[], shootId: number): OutlineItem[] {
  const out: OutlineItem[] = [];
  for (const it of items) {
    if (it.kind === 'trip') {
      const shoots = it.shoots.filter((s) => s.shoot_id === shootId);
      if (shoots.length) out.push({ ...it, shoots, groups: shoots.reduce((n, s) => n + s.groups, 0) });
    } else if (it.kind === 'shoot' && it.shoot_id === shootId) out.push(it);
  }
  return out;
}

/** The date an item sits at on the timeline: a trip's end, a shoot's date, a loose day. */
export const itemDate = (it: OutlineItem): string | null => (it.kind === 'trip' ? it.end_date : it.kind === 'shoot' ? it.shoot_date : it.day);

export interface RailEntry {
  key: string;
  label: string;
  state: OutlineState;
}
export interface RailYear {
  year: string;
  /** The first item of the year, which the year button jumps to. */
  first: string;
  entries: RailEntry[];
}

/** The left rail: years (newest first), then that year's trips and lone shoots by name. Loose days are not listed. */
export function railOf(items: OutlineItem[]): { years: RailYear[]; undated: string | null } {
  const years: RailYear[] = [];
  let undated: string | null = null;
  for (const it of items) {
    const date = itemDate(it);
    if (!date || date === UNDATED) {
      undated ??= it.key;
      continue;
    }
    const year = date.slice(0, 4);
    let y = years[years.length - 1];
    if (!y || y.year !== year) {
      y = { year, first: it.key, entries: [] };
      years.push(y);
    }
    if (it.kind === 'trip') y.entries.push({ key: it.key, label: it.name, state: it.state });
    else if (it.kind === 'shoot') y.entries.push({ key: it.key, label: it.title, state: it.state });
  }
  return { years, undated };
}

/** The line under a trip name: date range, shoots, frames. */
export const tripLine = (t: OutlineTrip): string => [dateRange(t.start_date, t.end_date), plural(t.shoots.length, 'shoot'), countsLine(t)].filter(Boolean).join(' · ');
