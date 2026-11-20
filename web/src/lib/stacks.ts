// sh-9 / sh-10: the queue of "shoots to confirm" (sort, filters, collapsed rows) and the draft of one stack.
// Pure helpers only; the views are in views/review/.

import { HIGH_CONFIDENCE, connectedFrames, sameSession, type Candidate, type Detail, type EffShoot, type NewDecision, type Session, type ShootDetails } from './review';

// ---- beta display (D-128) ----

/** Opacity of anything low-confidence: 1 at full confidence, 0.25 at none. */
export const fade = (confidence: number): number => 0.25 + 0.75 * Math.max(0, Math.min(1, confidence));

/** Where a value came from, in plain words (phone-derived sources are named so the iOS data is visible). */
export const DETAIL_SOURCE_LABEL: Record<string, string> = {
  phone_caption: 'iPhone caption',
  phone_label: 'iPhone label',
  phone_activity: 'iPhone activity',
  phone_landmark: 'iPhone landmark',
  phone_event: 'iPhone event',
  phone_holiday: 'iPhone holiday',
  phone_album: 'iPhone album',
  phone_venue: 'iPhone venue',
  phone_place: 'iPhone place',
  phone_area: 'iPhone area',
  phone_person: 'iPhone person',
  folder: 'folder',
  user: 'you',
  model: 'model',
};
export const detailSourceLabel = (s: string | null | undefined): string => (s ? (DETAIL_SOURCE_LABEL[s] ?? s) : 'none');

export const pct = (c: number): string => `${Math.round(c * 100)}%`;

// ---- the queue ----

export type SortMode = 'easiest' | 'biggest' | 'newest' | 'oldest' | 'connected';
export const SORTS: { key: SortMode; label: string }[] = [
  { key: 'easiest', label: 'Easiest first' },
  { key: 'biggest', label: 'Biggest' },
  { key: 'newest', label: 'Newest first' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'connected', label: 'Connected first' },
];

export type QueueFilter = 'needs_subject' | 'in_question' | 'tz_change' | 'bodies' | 'no_previews';
export const QUEUE_FILTERS: { key: QueueFilter; label: string }[] = [
  { key: 'needs_subject', label: 'Needs subject' },
  { key: 'in_question', label: 'Frames in question' },
  { key: 'tz_change', label: 'Time zone change' },
  { key: 'bodies', label: 'Bodies disagree' },
  { key: 'no_previews', label: 'No previews' },
];

/** Candidates still open for a shoot: not already pulled in, and not below the cut-off. */
export const openCandidates = (s: EffShoot): Candidate[] => (s.candidates ?? []).filter((c) => !c.beyond_cutoff && !s.included.some((i) => sameSession(i, c.session)));

/** The lowest confidence among a shoot's place and subject (0 when either is missing). Plans without details: 1. */
export function minConfidence(s: Pick<EffShoot, 'details'>): number {
  if (!s.details) return 1;
  const d = s.details;
  return Math.min(d.place.value === null ? 0 : d.place.confidence, d.subject.value === null ? 0 : d.subject.confidence);
}

export const needsSubject = (s: EffShoot): boolean => !!s.details && (s.details.subject.value === null || s.details.subject.confidence < HIGH_CONFIDENCE);

export function matchesQueueFilter(s: EffShoot, f: QueueFilter, mounted: ReadonlySet<string>): boolean {
  switch (f) {
    case 'needs_subject': return needsSubject(s);
    case 'in_question': return openCandidates(s).length > 0;
    case 'tz_change': return s.flags.includes('tz_change');
    case 'bodies': return s.flags.includes('body_clock_mismatch');
    case 'no_previews': return connectedFrames(s, mounted) === 0;
  }
}

const newestKey = (s: EffShoot): string => s.start_instant || s.end_instant || s.shoot_date;

/** Order for the queue. Easiest: highest minimum confidence, then fewest candidates, then time. Stable. `reverse` flips
 * any order (so Easiest reversed puts the hardest first). */
export function sortShoots(list: EffShoot[], mode: SortMode, mounted: ReadonlySet<string>, reverse = false): EffShoot[] {
  const time = (a: EffShoot, b: EffShoot) => (newestKey(a) < newestKey(b) ? -1 : newestKey(a) > newestKey(b) ? 1 : 0);
  const cmp: Record<SortMode, (a: EffShoot, b: EffShoot) => number> = {
    easiest: (a, b) => minConfidence(b) - minConfidence(a) || openCandidates(a).length - openCandidates(b).length || time(a, b),
    biggest: (a, b) => b.counts.frames - a.counts.frames || time(a, b),
    newest: (a, b) => time(b, a),
    oldest: (a, b) => time(a, b),
    connected: (a, b) => connectedFrames(b, mounted) - connectedFrames(a, mounted) || time(a, b),
  };
  const sorted = [...list].sort(cmp[mode] ?? cmp.easiest);
  return reverse ? sorted.reverse() : sorted;
}

/** "N of M confirmed · K frames unassigned" counts. Confirmed = accepted; locked shoots are not in the count. */
export function queueProgress(shoots: EffShoot[], unassignedFrames: number): { done: number; total: number; unassigned: number } {
  const open = shoots.filter((s) => !s.locked);
  return { done: open.filter((s) => s.state === 'accepted').length, total: open.length, unassigned: unassignedFrames };
}

/** The collapsed row's title parts: `date · place · subject`. */
export function stackTitle(s: EffShoot): { date: string; place: string | null; subject: string | null } {
  return { date: s.shoot_date, place: s.details?.place.value ?? null, subject: s.details?.subject.value ?? null };
}

// ---- one stack's draft (sh-10) ----

export interface StackDraft {
  place: Detail['value'];
  placeSource: string | null;
  subject: Detail['value'];
  subjectSource: string | null;
  people: string[];
  name: string;
  /** True once the user typed in the name, so a changed place or subject no longer rewrites it. */
  nameEdited: boolean;
  /** Core sessions the user has unticked. */
  excluded: Session[];
  /** Candidate sessions ticked. */
  ticked: Session[];
}

/** `place · subject`, skipping an empty part. */
export const composeName = (place: string | null, subject: string | null): string => [place, subject].filter((x) => x && x.trim()).join(' · ');

export function initialDraft(s: EffShoot, pretick: number): StackDraft {
  const d = s.details;
  const ticked: Session[] = [
    ...s.included,
    ...(s.candidates ?? []).filter((c) => !c.beyond_cutoff && c.score >= pretick && !s.included.some((i) => sameSession(i, c.session))).map((c) => c.session),
  ];
  return {
    place: d?.place.value ?? null,
    placeSource: d?.place.source ?? null,
    subject: d?.subject.value ?? null,
    subjectSource: d?.subject.source ?? null,
    people: d?.people.value ?? [],
    name: s.name.value,
    nameEdited: false,
    excluded: s.excluded,
    ticked,
  };
}

/** The decisions that Confirm stores, in order: member unticks, candidate ticks, details, the name, then the confirm. */
export function confirmDecisions(s: EffShoot, d: StackDraft): NewDecision[] {
  const out: NewDecision[] = [];
  const id = s.proposal_id;
  for (const x of d.excluded) if (!s.excluded.some((y) => sameSession(x, y))) out.push({ kind: 'exclude', id, session: x });
  for (const x of d.ticked) if (!s.included.some((y) => sameSession(x, y))) out.push({ kind: 'include', id, session: x });
  const det: ShootDetails | undefined = s.details;
  const field = (f: 'place' | 'subject', value: string | null, source: string | null) => {
    if (!value || !value.trim()) return;
    const cur = det?.[f];
    if (cur && cur.value === value && cur.source === 'user') return;
    out.push({ kind: 'detail', id, field: f, value, source: source ?? 'user' });
  };
  field('place', d.place, d.placeSource);
  field('subject', d.subject, d.subjectSource);
  const was = det?.people.value ?? [];
  if (d.people.length !== was.length || d.people.some((n, i) => n !== was[i])) out.push({ kind: 'detail', id, field: 'people', value: d.people, source: 'user' });
  const name = d.name.trim();
  if (name && name !== s.name.value) out.push({ kind: 'rename', id, value: name });
  out.push({ kind: 'confirm', id });
  return out;
}
