// Shoot review (sh-3): the plan file format (docs/specs/shoots/tasks.md) and the decision reducer behind the dry-run
// pane. A decision is a small record; the "effective plan" the UI renders is the base plan with every decision folded
// in, in order. Nothing here writes anything: Apply arrives with sh-4.
//
// sh-2: decisions live in the catalog (`shoot_plan_decisions`, through /api/shoots/decisions). The page loads the plan
// and the decisions made after it (`decisions_through`), and replays those; `toServerRows` / `fromServerRows` map a
// review action to the stored rows and back. Split and merge pieces get the planner's id (`shootId`), so a decision
// on a piece still applies when the server re-plans.

export type NameSource = 'folder' | 'phone' | 'other_folder' | 'fallback' | 'user' | string;

export interface NameChoice {
  value: string;
  source: NameSource;
}
export interface PlanName extends NameChoice {
  alternatives: NameChoice[];
}
export interface Gap {
  after_group_id: number;
  gap_s: number;
}
/** sh-8 plan data: a session is [first_group_id, last_group_id]. */
export type Session = [number, number];
export interface DetailChoice {
  value: string;
  source: string;
  confidence: number;
  evidence?: number;
  /** D-128 beta: below the cut-off a full run would apply. */
  beyond_cutoff?: boolean;
}
export interface Detail {
  value: string | null;
  source: string | null;
  confidence: number;
  evidence: number;
  alternatives: DetailChoice[];
  /** Subject only. */
  top_terms?: string[];
  example_caption?: { value: string; confidence: number; beyond_cutoff: boolean } | null;
}
export interface PeopleDetail {
  value: string[];
  source: string | null;
  confidence: number;
  evidence: number;
  /** One chip per name, with its own confidence. */
  names: DetailChoice[];
  alternatives: DetailChoice[];
}
export interface ShootDetails {
  place: Detail;
  subject: Detail;
  people: PeopleDetail;
}
export interface Candidate {
  session: Session;
  score: number;
  band: 'likely' | 'maybe' | 'beyond_cutoff';
  reasons: { term: string; text: string }[];
  home_proposal: string | null;
  frames: number;
  start_instant: string;
  end_instant: string;
  bodies: Record<string, number>;
  beyond_cutoff?: boolean;
}
export interface UnassignedSession {
  session: Session;
  frames: number;
  start_instant: string;
  end_instant: string;
}
export interface PlanShoot {
  proposal_id: string;
  trip_id: string | null;
  name: PlanName;
  shoot_date: string;
  start_instant: string;
  end_instant: string;
  wall: { start: string | null; end: string | null };
  local_offset: { value: string; camera: string; source: string; changes: boolean };
  group_ids: number[];
  counts: { frames: number; by_body: Record<string, number>; located: number };
  sample_group_ids: number[];
  centroid: { lat: number; lon: number } | null;
  points: [number, number][];
  gaps: Gap[];
  flags: string[];
  locked: boolean;
  /** Set by the planner when a stored decision accepted or skipped the shoot. */
  state?: 'accepted' | 'skipped';
  /** The drives holding the frames, most frames first (sh-1). Older plans lack it. */
  volumes?: PlanVolume[];
  /** sh-8: absent in older plans and on locked shoots. */
  members?: { core_sessions: Session[] };
  candidates?: Candidate[];
  details?: ShootDetails;
  /** Accepted through a stack (sh-10). */
  confirmed?: boolean;
}
export interface PlanVolume {
  volume_id: string;
  label: string;
  frames: number;
}
export interface PlanTrip {
  proposal_id: string;
  name: PlanName;
  start_date: string;
  end_date: string;
  shoot_ids: string[];
  flags: string[];
  /** An applied trip (sh-4), shown read-only with its locked shoots. */
  locked?: boolean;
}
/** pl-1: place collections. A spot is a repeated place; an area groups spots. */
export interface PlaceName {
  value: string | null;
  source: string | null;
  confidence: number;
  count: number;
  alternatives: { value: string; source: string; count: number }[];
}
export interface PlaceMember {
  proposal_id: string;
  confidence: number;
  via?: 'coords' | 'name' | 'folder';
  beyond_cutoff?: boolean;
}
export interface PlaceSpot {
  key: string;
  kind: 'spot';
  name: PlaceName;
  centroid: { lat: number; lon: number };
  radius_km: number;
  area_key: string | null;
  members: PlaceMember[];
  frames: number;
  first_date: string;
  last_date: string;
  home: boolean;
}
export interface PlaceArea {
  key: string;
  kind: 'area';
  name: PlaceName;
  spot_keys: string[];
  members: { proposal_id: string; confidence: number }[];
}
export interface Plan {
  plan_id: string;
  created_at: string;
  /** The newest stored decision already folded into this plan (sh-2); absent in older plans and the fixture. */
  decisions_through?: number;
  params: Record<string, number | string | boolean | null>;
  summary: { frames: number; shoots: number; trips: number; unassigned: number; locked: number; flagged: number };
  trips: PlanTrip[];
  shoots: PlanShoot[];
  unassigned_sessions?: UnassignedSession[];
  /** pl-1; absent in older plans. */
  places?: { spots: PlaceSpot[]; areas: PlaceArea[] };
}

/** `rows`: the stored decision ids behind this one (sh-2); several for "Accept trip" and "Accept all unflagged". */
export type Decision = { seq: number; rows?: number[] } & (
  | { kind: 'accept'; id: string }
  | { kind: 'skip'; id: string }
  | { kind: 'rename'; id: string; value: string; source?: NameSource }
  | { kind: 'reject_name'; id: string; value: string }
  | { kind: 'split'; id: string; after_group_id: number }
  | { kind: 'merge'; id: string; with: 'prev' | 'next' }
  | { kind: 'move'; id: string; trip_id: string | null }
  | { kind: 'accept_trip'; id: string }
  | { kind: 'accept_all_unflagged' }
  /** sh-9: "Accept all high-confidence" (stored as one `accept` per shoot, like the older one). */
  | { kind: 'accept_high' }
  /** sh-10: the stack was reviewed; stored as `accept` with `{confirmed: true}`. */
  | { kind: 'confirm'; id: string }
  | { kind: 'include'; id: string; session: Session }
  | { kind: 'exclude'; id: string; session: Session }
  | { kind: 'detail'; id: string; field: DetailField; value: string | string[]; source?: string }
  /** pl-2: one place name for every ticked shoot of a place collection; stored as one `detail` per shoot in one batch. */
  | { kind: 'name_place'; ids: string[]; value: string }
);
export type DetailField = 'place' | 'subject' | 'people';
/** A decision before it gets its sequence number. */
export type NewDecision = Decision extends infer D ? (D extends unknown ? Omit<D, 'seq' | 'rows'> : never) : never;

export type ShootState = 'undecided' | 'accepted' | 'skipped';
export interface EffShoot extends Omit<PlanShoot, 'state'> {
  state: ShootState;
  renamed: boolean;
  /** Made by a split or merge here, so counts, dates and points are estimates. */
  derived: boolean;
  /** Names the user rejected for this shoot (they join the stoplist on Apply). */
  rejected: string[];
  /** sh-10: core sessions sent back to the pool, and candidate sessions pulled in. */
  excluded: Session[];
  included: Session[];
}
export interface EffTrip extends Omit<PlanTrip, 'shoot_ids'> {
  shoot_ids: string[];
  renamed: boolean;
}
export interface EffPlan {
  trips: EffTrip[];
  /** All shoots in time order. */
  shoots: EffShoot[];
  stoplist: string[];
}

export const isTripId = (id: string): boolean => id.startsWith('t:');

// ---- pure shoot operations (also used for previews) ----

const sumBodies = (a: Record<string, number>, b: Record<string, number>): Record<string, number> => {
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) out[k] = (out[k] ?? 0) + v;
  return out;
};

/** Spread a total over the two halves by group count, keeping the sum exact. */
const share = (total: number, left: number, all: number): [number, number] => {
  const l = all > 0 ? Math.round((total * left) / all) : 0;
  return [l, total - l];
};

export function fromPlanShoot(s: PlanShoot): EffShoot {
  return { ...s, state: s.state ?? 'undecided', renamed: s.name.source === 'user', derived: false, rejected: [], excluded: [], included: [], confirmed: s.confirmed ?? false };
}

// ---- proposal ids: the planner's `_sha("s:", first, last)` (photovault/shoots.py) ----

export function sha1Hex(text: string): string {
  const bytes = new TextEncoder().encode(text);
  const len = bytes.length;
  const words = new Uint32Array((((len + 8) >> 6) + 1) * 16);
  for (let i = 0; i < len; i++) words[i >> 2]! |= bytes[i]! << (24 - (i % 4) * 8);
  words[len >> 2]! |= 0x80 << (24 - (len % 4) * 8);
  words[words.length - 1] = len * 8;
  const w = new Uint32Array(80);
  let [a, b, c, d, e] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
  const rol = (x: number, n: number) => (x << n) | (x >>> (32 - n));
  for (let blk = 0; blk < words.length; blk += 16) {
    for (let t = 0; t < 80; t++) w[t] = t < 16 ? words[blk + t]! : rol(w[t - 3]! ^ w[t - 8]! ^ w[t - 14]! ^ w[t - 16]!, 1);
    let [A, B, C, D, E] = [a, b, c, d, e];
    for (let t = 0; t < 80; t++) {
      const f = t < 20 ? (B & C) | (~B & D) : t < 40 ? B ^ C ^ D : t < 60 ? (B & C) | (B & D) | (C & D) : B ^ C ^ D;
      const k = t < 20 ? 0x5a827999 : t < 40 ? 0x6ed9eba1 : t < 60 ? 0x8f1bbcdc : 0xca62c1d6;
      const tmp = (rol(A, 5) + f + E + k + w[t]!) | 0;
      [E, D, C, B, A] = [D, C, rol(B, 30), A, tmp];
    }
    [a, b, c, d, e] = [(a + A) | 0, (b + B) | 0, (c + C) | 0, (d + D) | 0, (e + E) | 0];
  }
  return [a, b, c, d, e].map((x) => (x >>> 0).toString(16).padStart(8, '0')).join('');
}

/** The planner's id for a shoot running from group `first` to group `last`. */
export const shootId = (first: number, last: number): string => `s:${sha1Hex(`${first}-${last}`).slice(0, 16)}`;

/** Cut a shoot after a group (at one of its gaps) into two. Counts, bodies, located and points are shared by group
 *  count, so they are estimates (`derived`). Returns null when the cut is not between two groups. */
export function splitShoot(s: EffShoot, afterGroupId: number): [EffShoot, EffShoot] | null {
  const cut = s.group_ids.indexOf(afterGroupId);
  if (cut < 0 || cut >= s.group_ids.length - 1) return null;
  const n = s.group_ids.length;
  const leftIds = s.group_ids.slice(0, cut + 1);
  const rightIds = s.group_ids.slice(cut + 1);
  const [fl, fr] = share(s.counts.frames, leftIds.length, n);
  const [ll, lr] = share(Math.min(s.counts.located, s.counts.frames), leftIds.length, n);
  const bl: Record<string, number> = {};
  const br: Record<string, number> = {};
  for (const [body, c] of Object.entries(s.counts.by_body)) {
    const [a, b] = share(c, leftIds.length, n);
    if (a) bl[body] = a;
    if (b) br[body] = b;
  }
  const pc = Math.round((s.points.length * leftIds.length) / n);
  const set = new Set(leftIds);
  const mk = (ids: number[], isLeft: boolean): EffShoot => ({
    ...s,
    proposal_id: shootId(ids[0]!, ids[ids.length - 1]!),
    group_ids: ids,
    counts: { frames: isLeft ? fl : fr, by_body: isLeft ? bl : br, located: isLeft ? ll : lr },
    sample_group_ids: s.sample_group_ids.filter((g) => (set.has(g) === isLeft)),
    points: isLeft ? s.points.slice(0, pc) : s.points.slice(pc),
    gaps: s.gaps.filter((g) => g.after_group_id !== afterGroupId && (set.has(g.after_group_id) === isLeft)),
    // The cut moves one end of each half to a time we do not have; the other end keeps the original value.
    start_instant: isLeft ? s.start_instant : '',
    end_instant: isLeft ? '' : s.end_instant,
    wall: isLeft ? { start: s.wall.start, end: null } : { start: null, end: s.wall.end },
    shoot_date: isLeft ? s.shoot_date : (s.wall.end ?? s.shoot_date).slice(0, 10),
    state: 'undecided',
    derived: true,
    name: isLeft ? s.name : { ...s.name, alternatives: [...s.name.alternatives] },
  });
  return [mk(leftIds, true), mk(rightIds, false)];
}

const gapSeconds = (a: EffShoot, b: EffShoot): number | null => {
  const e = Date.parse(a.end_instant);
  const st = Date.parse(b.start_instant);
  return Number.isFinite(e) && Number.isFinite(st) && st >= e ? Math.round((st - e) / 1000) : null;
};

function mergeVolumes(a: PlanVolume[], b: PlanVolume[]): PlanVolume[] {
  const out = new Map<string, PlanVolume>();
  for (const v of [...a, ...b]) {
    const cur = out.get(v.volume_id);
    out.set(v.volume_id, cur ? { ...cur, frames: cur.frames + v.frames } : { ...v });
  }
  return [...out.values()].sort((x, y) => y.frames - x.frames);
}

/** Join two neighbouring shoots (a before b). The earlier one's name, trip and date win. */
export function mergeShoots(a: EffShoot, b: EffShoot): EffShoot {
  const bridge = gapSeconds(a, b);
  const last = a.group_ids[a.group_ids.length - 1]!;
  return {
    ...a,
    proposal_id: shootId(a.group_ids[0]!, b.group_ids[b.group_ids.length - 1]!),
    group_ids: [...a.group_ids, ...b.group_ids],
    counts: { frames: a.counts.frames + b.counts.frames, by_body: sumBodies(a.counts.by_body, b.counts.by_body), located: a.counts.located + b.counts.located },
    sample_group_ids: [...a.sample_group_ids, ...b.sample_group_ids].slice(0, 12),
    points: [...a.points, ...b.points].slice(0, 50),
    gaps: [...a.gaps, ...(bridge !== null ? [{ after_group_id: last, gap_s: bridge }] : []), ...b.gaps].sort((x, y) => y.gap_s - x.gap_s).slice(0, 20),
    start_instant: a.start_instant || b.start_instant,
    end_instant: b.end_instant || a.end_instant,
    wall: { start: a.wall.start ?? b.wall.start, end: b.wall.end ?? a.wall.end },
    flags: [...new Set([...a.flags, ...b.flags])],
    volumes: mergeVolumes(a.volumes ?? [], b.volumes ?? []),
    state: 'undecided',
    derived: true,
    renamed: a.renamed,
    rejected: [...new Set([...a.rejected, ...b.rejected])],
    excluded: [...a.excluded, ...b.excluded],
    included: [...a.included, ...b.included],
    confirmed: false,
    locked: false,
  };
}

const byStart = (x: EffShoot, y: EffShoot): number => {
  const a = x.start_instant || x.end_instant;
  const b = y.start_instant || y.end_instant;
  return a < b ? -1 : a > b ? 1 : x.group_ids[0]! - y.group_ids[0]!;
};

/** The neighbour a merge would use: the previous or next shoot in the same trip (or the same "no trip" group). */
export function neighbour(plan: EffPlan, id: string, dir: 'prev' | 'next'): EffShoot | null {
  const s = plan.shoots.find((x) => x.proposal_id === id);
  if (!s) return null;
  const peers = plan.shoots.filter((x) => x.trip_id === s.trip_id);
  const i = peers.indexOf(s);
  const n = peers[dir === 'prev' ? i - 1 : i + 1];
  return n && !n.locked && !s.locked ? n : null;
}

// ---- the reducer ----

function dateSpan(shoots: EffShoot[]): [string, string] | null {
  const ds = shoots.flatMap((s) => [s.shoot_date, (s.wall.end ?? '').slice(0, 10)]).filter((d) => d.length === 10).sort();
  return ds.length ? [ds[0]!, ds[ds.length - 1]!] : null;
}

function rebuildTrips(trips: EffTrip[], shoots: EffShoot[]): EffTrip[] {
  // Trips left empty stay in the list (a shoot can be moved back into one); the UI hides them.
  return trips.map((t) => {
      const mine = shoots.filter((s) => s.trip_id === t.proposal_id);
      const span = dateSpan(mine);
      return { ...t, shoot_ids: mine.map((s) => s.proposal_id), start_date: span?.[0] ?? t.start_date, end_date: span?.[1] ?? t.end_date };
  });
}

export function initialPlan(plan: Plan): EffPlan {
  const shoots = plan.shoots.map(fromPlanShoot).sort(byStart);
  const trips = plan.trips.map((t) => ({ ...t, renamed: false }));
  return { trips: rebuildTrips(trips, shoots), shoots, stoplist: [] };
}

const patch = (p: EffPlan, id: string, f: (s: EffShoot) => EffShoot): EffPlan => ({
  ...p,
  shoots: p.shoots.map((s) => (s.proposal_id === id && !s.locked ? f(s) : s)),
});

/** Apply one decision. A decision that names something gone (after an undo of an earlier split, say) does nothing. */
export function applyDecision(p: EffPlan, d: Decision): EffPlan {
  switch (d.kind) {
    case 'accept':
      return patch(p, d.id, (s) => ({ ...s, state: 'accepted' }));
    case 'skip':
      return patch(p, d.id, (s) => ({ ...s, state: 'skipped' }));
    case 'rename': {
      const value = d.value.trim();
      if (!value) return p;
      const source = d.source ?? 'user';
      if (isTripId(d.id)) {
        return { ...p, trips: p.trips.map((t) => (t.proposal_id === d.id ? { ...t, renamed: true, name: { value, source, alternatives: t.name.alternatives.filter((a) => a.value !== value) } } : t)) };
      }
      return patch(p, d.id, (s) => ({
        ...s,
        renamed: true,
        name: { value, source, alternatives: [...(s.name.value !== value ? [{ value: s.name.value, source: s.name.source }] : []), ...s.name.alternatives].filter((a, i, all) => a.value !== value && all.findIndex((b) => b.value === a.value) === i) },
      }));
    }
    case 'reject_name': {
      const stoplist = p.stoplist.includes(d.value) ? p.stoplist : [...p.stoplist, d.value];
      const next = patch({ ...p, stoplist }, d.id, (s) => {
        const alts = s.name.alternatives.filter((a) => a.value !== d.value);
        const rejected = [...s.rejected, d.value];
        if (s.name.value !== d.value) return { ...s, rejected, name: { ...s.name, alternatives: alts } };
        const [first, ...rest] = alts;
        return first ? { ...s, rejected, name: { ...first, alternatives: rest } } : { ...s, rejected };
      });
      return next;
    }
    case 'split': {
      const s = p.shoots.find((x) => x.proposal_id === d.id);
      if (!s || s.locked) return p;
      const halves = splitShoot(s, d.after_group_id);
      if (!halves) return p;
      const shoots = p.shoots.flatMap((x) => (x === s ? halves : [x])).sort(byStart);
      return { ...p, shoots, trips: rebuildTrips(p.trips, shoots) };
    }
    case 'merge': {
      const other = neighbour(p, d.id, d.with);
      const s = p.shoots.find((x) => x.proposal_id === d.id);
      if (!s || !other) return p;
      const [a, b] = d.with === 'prev' ? [other, s] : [s, other];
      const merged = mergeShoots(a, b);
      const shoots = [...p.shoots.filter((x) => x !== a && x !== b), merged].sort(byStart);
      return { ...p, shoots, trips: rebuildTrips(p.trips, shoots) };
    }
    case 'move': {
      if (d.trip_id !== null && !p.trips.some((t) => t.proposal_id === d.trip_id)) return p;
      const q = patch(p, d.id, (s) => ({ ...s, trip_id: d.trip_id }));
      return { ...q, trips: rebuildTrips(p.trips, q.shoots) };
    }
    case 'accept_trip': {
      const t = p.trips.find((x) => x.proposal_id === d.id);
      if (!t) return p;
      return { ...p, shoots: p.shoots.map((s) => (s.trip_id === t.proposal_id && !s.locked ? { ...s, state: 'accepted' } : s)) };
    }
    case 'accept_all_unflagged':
      return { ...p, shoots: p.shoots.map((s) => (!s.locked && s.state === 'undecided' && s.flags.length === 0 ? { ...s, state: 'accepted' } : s)) };
    case 'accept_high':
      return { ...p, shoots: p.shoots.map((s) => (isHighConfidence(s) && s.state === 'undecided' ? { ...s, state: 'accepted' } : s)) };
    case 'confirm':
      return patch(p, d.id, (s) => ({ ...s, state: 'accepted', confirmed: true }));
    case 'include':
      return patch(p, d.id, (s) => ({ ...s, excluded: dropSession(s.excluded, d.session), included: addSession(s.included, d.session) }));
    case 'exclude':
      return patch(p, d.id, (s) => ({ ...s, included: dropSession(s.included, d.session), excluded: addSession(s.excluded, d.session) }));
    case 'name_place': {
      const ids = new Set(d.ids);
      return { ...p, shoots: p.shoots.map((s) => (ids.has(s.proposal_id) && !s.locked && s.details ? { ...s, details: withDetail(s.details, 'place', d.value, 'user') } : s)) };
    }
    case 'detail':
      return patch(p, d.id, (s) => (s.details ? { ...s, details: withDetail(s.details, d.field, d.value, d.source ?? 'user') } : s));
  }
}

const sameSession = (a: Session, b: Session): boolean => a[0] === b[0] && a[1] === b[1];
const dropSession = (list: Session[], x: Session): Session[] => list.filter((y) => !sameSession(y, x));
const addSession = (list: Session[], x: Session): Session[] => [...dropSession(list, x), x];
export { sameSession };

/** A user decision on a detail: the field takes the value at confidence 1, the old value becomes an alternative. */
export function withDetail(det: ShootDetails, field: DetailField, value: string | string[], source: string): ShootDetails {
  if (field === 'people') {
    const names = Array.isArray(value) ? value : [value];
    const have = new Map([...det.people.names, ...det.people.alternatives].map((n) => [n.value, n]));
    return { ...det, people: { ...det.people, value: names, source, confidence: 1, names: names.map((n) => (have.get(n) as DetailChoice | undefined) ?? { value: n, source, confidence: 1 }) } };
  }
  const cur = det[field];
  const v = String(value);
  const alts = cur.value && cur.value !== v ? [{ value: cur.value, source: cur.source ?? 'user', confidence: cur.confidence, evidence: cur.evidence }, ...cur.alternatives] : cur.alternatives;
  return { ...det, [field]: { ...cur, value: v, source, confidence: 1, alternatives: alts.filter((a) => a.value !== v) } };
}

/** Confidence at or above which a proposed detail counts as high (D-127 calibration: 87 of 174 subjects reach it). */
export const HIGH_CONFIDENCE = 0.6;

/** A shoot "Accept all high-confidence" takes: unlocked, no flags, and a place and subject at high confidence.
 *  Plans from before sh-8 carry no details; for those it falls back to "no flags". */
export function isHighConfidence(s: EffShoot): boolean {
  if (s.locked || s.flags.length > 0) return false;
  if (!s.details) return true;
  return (s.details.place.value !== null && s.details.place.confidence >= HIGH_CONFIDENCE) && (s.details.subject.value !== null && s.details.subject.confidence >= HIGH_CONFIDENCE);
}

/** The effective plan after every decision, in order. */
export function effectivePlan(plan: Plan, decisions: Decision[]): EffPlan {
  return decisions.reduce(applyDecision, initialPlan(plan));
}

export function addDecision(decisions: Decision[], d: NewDecision): Decision[] {
  const seq = decisions.reduce((m, x) => Math.max(m, x.seq), 0) + 1;
  return [...decisions, { ...d, seq } as Decision];
}

/** Undo one decision by its sequence number. Later ones that depended on it become no-ops when replayed. */
export function undoDecision(decisions: Decision[], seq: number): Decision[] {
  return decisions.filter((d) => d.seq !== seq);
}

// ---- stored rows (sh-2) ----

export interface StoredDecision {
  decision_id: number;
  proposal_id: string;
  kind: 'accept' | 'split' | 'merge' | 'skip' | 'rename' | 'reject_name' | 'move' | 'include' | 'exclude' | 'detail';
  payload: Record<string, unknown>;
  decided_at?: string;
}
export type DecisionRow = Omit<StoredDecision, 'decision_id' | 'decided_at'>;

/** The rows to store for one review action, against the plan as it stands. "Accept trip" and "Accept all unflagged"
 *  become one `accept` per shoot they touch, sharing `batch`; an action that touches nothing gives no rows. */
export function toServerRows(d: NewDecision, p: EffPlan, batch: string): DecisionRow[] {
  switch (d.kind) {
    case 'accept':
    case 'skip':
      return [{ proposal_id: d.id, kind: d.kind, payload: {} }];
    case 'confirm':
      return [{ proposal_id: d.id, kind: 'accept', payload: { confirmed: true } }];
    case 'include':
    case 'exclude':
      return [{ proposal_id: d.id, kind: d.kind, payload: { session: d.session } }];
    case 'detail':
      return [{ proposal_id: d.id, kind: 'detail', payload: { field: d.field, value: d.value, ...(d.source ? { source: d.source } : {}) } }];
    case 'name_place':
      return d.ids.map((id) => ({ proposal_id: id, kind: 'detail' as const, payload: { field: 'place', value: d.value, source: 'user', batch, ui: 'name_place' } }));
    case 'accept_high':
      return p.shoots
        .filter((s) => isHighConfidence(s) && s.state === 'undecided')
        .map((s) => ({ proposal_id: s.proposal_id, kind: 'accept', payload: { batch, ui: 'accept_high' } }));
    case 'rename':
      return [{ proposal_id: d.id, kind: 'rename', payload: { value: d.value, ...(d.source ? { source: d.source } : {}) } }];
    case 'reject_name':
      return [{ proposal_id: d.id, kind: 'reject_name', payload: { value: d.value } }];
    case 'split':
      return [{ proposal_id: d.id, kind: 'split', payload: { after_group_id: d.after_group_id } }];
    case 'merge':
      return [{ proposal_id: d.id, kind: 'merge', payload: { with: d.with } }];
    case 'move':
      return [{ proposal_id: d.id, kind: 'move', payload: { trip_id: d.trip_id } }];
    case 'accept_trip':
      return p.shoots
        .filter((s) => s.trip_id === d.id && !s.locked)
        .map((s) => ({ proposal_id: s.proposal_id, kind: 'accept', payload: { batch, ui: 'accept_trip', trip: d.id } }));
    case 'accept_all_unflagged':
      return p.shoots
        .filter((s) => !s.locked && s.state === 'undecided' && s.flags.length === 0)
        .map((s) => ({ proposal_id: s.proposal_id, kind: 'accept', payload: { batch, ui: 'accept_all_unflagged' } }));
  }
}

/** Stored rows back to review decisions, in order; the rows of one batch become the one action that made them. */
export function fromServerRows(rows: StoredDecision[]): Decision[] {
  const out: Decision[] = [];
  const batches = new Map<string, Decision>();
  for (const r of rows) {
    const p = r.payload;
    const batch = typeof p.batch === 'string' ? p.batch : null;
    if (batch && batches.has(batch)) {
      const b = batches.get(batch)!;
      b.rows!.push(r.decision_id);
      if (b.kind === 'name_place') b.ids.push(r.proposal_id);
      continue;
    }
    const base = { seq: r.decision_id, rows: [r.decision_id] };
    let d: Decision;
    if (batch && p.ui === 'accept_trip' && typeof p.trip === 'string') d = { ...base, kind: 'accept_trip', id: p.trip };
    else if (batch && p.ui === 'accept_all_unflagged') d = { ...base, kind: 'accept_all_unflagged' };
    else if (batch && p.ui === 'name_place') d = { ...base, kind: 'name_place', ids: [r.proposal_id], value: String(p.value ?? '') };
    else if (batch && p.ui === 'accept_high') d = { ...base, kind: 'accept_high' };
    else if (r.kind === 'accept' && p.confirmed === true) d = { ...base, kind: 'confirm', id: r.proposal_id };
    else if (r.kind === 'include' || r.kind === 'exclude') d = { ...base, kind: r.kind, id: r.proposal_id, session: (Array.isArray(p.session) ? p.session.map(Number) : [0, 0]) as Session };
    else if (r.kind === 'detail') d = { ...base, kind: 'detail', id: r.proposal_id, field: (p.field as DetailField) ?? 'place', value: Array.isArray(p.value) ? p.value.map(String) : String(p.value ?? ''), ...(typeof p.source === 'string' ? { source: p.source } : {}) };
    else if (r.kind === 'rename') d = { ...base, kind: 'rename', id: r.proposal_id, value: String(p.value ?? ''), ...(typeof p.source === 'string' ? { source: p.source } : {}) };
    else if (r.kind === 'reject_name') d = { ...base, kind: 'reject_name', id: r.proposal_id, value: String(p.value ?? '') };
    else if (r.kind === 'split') d = { ...base, kind: 'split', id: r.proposal_id, after_group_id: Number(p.after_group_id) };
    else if (r.kind === 'merge') d = { ...base, kind: 'merge', id: r.proposal_id, with: p.with === 'prev' ? 'prev' : 'next' };
    else if (r.kind === 'move') d = { ...base, kind: 'move', id: r.proposal_id, trip_id: typeof p.trip_id === 'string' ? p.trip_id : null };
    else d = { ...base, kind: r.kind, id: r.proposal_id };
    if (batch) batches.set(batch, d);
    out.push(d);
  }
  return out;
}

const nameOf = (p: EffPlan, id: string): string => p.trips.find((t) => t.proposal_id === id)?.name.value ?? p.shoots.find((s) => s.proposal_id === id)?.name.value ?? 'a shoot';

/** One line per decision for the side panel, worded with the names as they were when it was made. */
export function describeAll(plan: Plan, decisions: Decision[]): { seq: number; text: string }[] {
  let cur = initialPlan(plan);
  return decisions.map((d) => {
    const nm = 'id' in d ? nameOf(cur, d.id) : '';
    let text: string;
    switch (d.kind) {
      case 'accept': text = `Accept ${nm}`; break;
      case 'skip': text = `Skip ${nm}`; break;
      case 'rename': text = `Rename ${nm} to "${d.value}"`; break;
      case 'reject_name': text = `Reject the name "${d.value}" (${nm})`; break;
      case 'split': text = `Split ${nm} after photo ${d.after_group_id}`; break;
      case 'merge': text = `Merge ${nm} with the ${d.with === 'prev' ? 'previous' : 'next'} shoot`; break;
      case 'move': text = `Move ${nm} ${d.trip_id ? `to ${nameOf(cur, d.trip_id)}` : 'out of its trip'}`; break;
      case 'accept_trip': text = `Accept trip ${nm}`; break;
      case 'accept_all_unflagged': text = 'Accept all unflagged'; break;
      case 'accept_high': text = 'Accept all high-confidence'; break;
      case 'confirm': text = `Confirm ${nm}`; break;
      case 'include': text = `Add photos ${d.session[0]}–${d.session[1]} to ${nm}`; break;
      case 'exclude': text = `Take photos ${d.session[0]}–${d.session[1]} out of ${nm}`; break;
      case 'name_place': text = `Name the place "${d.value}" for ${d.ids.length} ${d.ids.length === 1 ? 'shoot' : 'shoots'}`; break;
      case 'detail': text = `Set ${d.field} of ${nm} to "${Array.isArray(d.value) ? d.value.join(', ') : d.value}"`; break;
    }
    cur = applyDecision(cur, d);
    return { seq: d.seq, text };
  });
}

// ---- display order (sh-3b) ----

/** Frames of a shoot that sit on a connected drive (so its previews can load). */
export const connectedFrames = (s: Pick<PlanShoot, 'volumes'>, mounted: ReadonlySet<string>): number =>
  (s.volumes ?? []).reduce((n, v) => n + (mounted.has(v.volume_id) ? v.frames : 0), 0);

export interface ReviewSection {
  /** null: the shoots without a trip. */
  trip: EffTrip | null;
  shoots: EffShoot[];
  connected: boolean;
}

/** The sections the page renders: one per trip with shoots, then the shoots without a trip. With `connectedFirst`,
 *  trips holding any frame on a connected drive come first, and loose shoots on a connected drive lead the trip-less
 *  section. The sort is stable, so time order holds within each group, and shoots inside a trip stay in time order. */
export function reviewSections(p: EffPlan, mounted: ReadonlySet<string>, connectedFirst: boolean): ReviewSection[] {
  const on = (s: EffShoot) => connectedFrames(s, mounted) > 0;
  const trips: ReviewSection[] = p.trips
    .map((t) => {
      const shoots = p.shoots.filter((s) => s.trip_id === t.proposal_id);
      return { trip: t, shoots, connected: shoots.some(on) };
    })
    .filter((x) => x.shoots.length > 0);
  const loose = p.shoots.filter((s) => s.trip_id === null);
  if (!connectedFirst) return loose.length ? [...trips, { trip: null, shoots: loose, connected: loose.some(on) }] : trips;
  const rank = (b: boolean) => (b ? 0 : 1);
  const sortedTrips = [...trips].sort((a, b) => rank(a.connected) - rank(b.connected));
  const sortedLoose = [...loose].sort((a, b) => rank(on(a)) - rank(on(b)));
  return sortedLoose.length ? [...sortedTrips, { trip: null, shoots: sortedLoose, connected: sortedLoose.some(on) }] : sortedTrips;
}

export type ReviewFilter ='all' | 'flagged' | 'undecided' | 'accepted';

export const matchesFilter = (s: EffShoot, f: ReviewFilter): boolean =>
  f === 'all' ? true : f === 'flagged' ? s.flags.length > 0 : f === 'undecided' ? s.state === 'undecided' && !s.locked : s.state === 'accepted' || s.locked;

export function tally(p: EffPlan): { shoots: number; trips: number; accepted: number; skipped: number; undecided: number; flagged: number } {
  const open = p.shoots.filter((s) => !s.locked);
  return {
    shoots: p.shoots.length,
    trips: p.trips.filter((t) => t.shoot_ids.length > 0).length,
    accepted: open.filter((s) => s.state === 'accepted').length,
    skipped: open.filter((s) => s.state === 'skipped').length,
    undecided: open.filter((s) => s.state === 'undecided').length,
    flagged: p.shoots.filter((s) => s.flags.length > 0).length,
  };
}

export const FLAG_INFO: Record<string, { label: string; tip: string }> = {
  multi_place: { label: 'several places', tip: 'The frames sit in more than one place. Splitting at a gap may help.' },
  tz_change: { label: 'time zone changes', tip: 'A time-zone change is proposed for this shoot: the camera clock and the local time differ.' },
  body_clock_mismatch: { label: 'bodies disagree', tip: 'Two camera bodies show different clocks for this shoot.' },
  huge_timelapse: { label: 'very large timelapse', tip: 'A very large run of frames. It may be a timelapse that belongs in one shoot, or several.' },
  weak_name: { label: 'weak name', tip: 'The name comes from a weak source (a fallback or an undated folder). Rename it or pick an alternative.' },
};

export const NAME_SOURCE_LABEL: Record<string, string> = { folder: 'folder', phone: 'phone', other_folder: 'other folder', fallback: 'fallback', user: 'you' };
