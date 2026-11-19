import fixture from './__fixtures__/actions.json';
import { api } from './api';

export type Severity = 'critical' | 'high' | 'normal' | 'low';

export interface ActionNeed {
  volume_id: string;
  label: string;
  role: string | null;
  mounted: boolean;
}

export type ActionDo =
  | { label: string; kind: 'ui'; href: string }
  | { label: string; kind: 'run'; endpoint: string; body?: unknown }
  | { label: string; kind: 'none' };

export interface ActionItem {
  id: string;
  kind: string;
  severity: Severity;
  title: string;
  why: string[];
  needs: ActionNeed[];
  steps: string[];
  do: ActionDo;
  cli: string | null;
  evidence: { files: number; bytes: number };
  snoozed_until: string | null;
  dismissed?: boolean;
}

export interface ActionsPayload {
  generated_at: string;
  counts: Record<string, number>;
  actions: ActionItem[];
}

/**
 * TODO(act-1): drop the fixture fallback once GET /api/actions ships.
 * Until then a 404 (or any failure to reach the endpoint) reads the fixture in the contract format.
 */
export async function loadActions(): Promise<ActionsPayload> {
  try {
    return await api<ActionsPayload>('GET', '/api/actions');
  } catch (err) {
    if (/HTTP 404/.test((err as Error).message)) return fixture as unknown as ActionsPayload;
    throw err;
  }
}

const RANK: Record<Severity, number> = { critical: 0, high: 1, normal: 2, low: 3 };

export const isSnoozed = (a: ActionItem, now: number): boolean => a.snoozed_until !== null && Date.parse(a.snoozed_until) > now;
export const isHidden = (a: ActionItem, now: number): boolean => Boolean(a.dismissed) || isSnoozed(a, now);

/** Doable now: every needed drive is connected (an action needing no drive counts as doable). */
const doable = (a: ActionItem, mountedIds?: ReadonlySet<string>): boolean =>
  a.needs.length === 0 || a.needs.some((n) => (mountedIds ? mountedIds.has(n.volume_id) : n.mounted));

/** Severity, then what can be done right now (a needed drive is connected), then bytes at risk, then the server's order. */
export function sortActions(list: ActionItem[], mountedIds?: ReadonlySet<string>): ActionItem[] {
  return list
    .map((a, i) => ({ a, i, now: doable(a, mountedIds) ? 0 : 1 }))
    .sort((x, y) => RANK[x.a.severity] - RANK[y.a.severity] || x.now - y.now || y.a.evidence.bytes - x.a.evidence.bytes || x.i - y.i)
    .map((x) => x.a);
}

export interface Groups {
  risk: ActionItem[];
  unfinished: ActionItem[];
  housekeeping: ActionItem[];
  hidden: ActionItem[];
}

export function groupActions(list: ActionItem[], now: number, mountedIds?: ReadonlySet<string>): Groups {
  const g: Groups = { risk: [], unfinished: [], housekeeping: [], hidden: [] };
  for (const a of sortActions(list, mountedIds)) {
    if (isHidden(a, now)) g.hidden.push(a);
    else if (a.severity === 'critical' || a.severity === 'high') g.risk.push(a);
    else if (a.severity === 'normal') g.unfinished.push(a);
    else g.housekeeping.push(a);
  }
  return g;
}

/** The nav badge: critical + high that are neither snoozed nor dismissed. */
export const badgeCount = (list: ActionItem[], now: number): number =>
  list.filter((a) => (a.severity === 'critical' || a.severity === 'high') && !isHidden(a, now)).length;

/** The needed drives that are connected right now. `mountedIds` (from the live drives store) wins over the payload's flag. */
export function mountedNeeds(a: ActionItem, mountedIds?: ReadonlySet<string>): ActionNeed[] {
  return a.needs.filter((n) => (mountedIds ? mountedIds.has(n.volume_id) : n.mounted));
}

/** "Card A is connected — do it now", or null when no needed drive is connected. */
export function highlightText(a: ActionItem, mountedIds?: ReadonlySet<string>): string | null {
  const m = mountedNeeds(a, mountedIds);
  if (!m.length) return null;
  const names = m.map((n) => n.label).join(' and ');
  return `${names} ${m.length > 1 ? 'are' : 'is'} connected — do it now`;
}

export type LocalOp = { type: 'dismiss'; id: string } | { type: 'snooze'; id: string; until: string } | { type: 'restore'; id: string };

/** The optimistic reducer. Undo of a dismiss or snooze is a restore; a restore clears both marks. */
export function applyOp(list: ActionItem[], op: LocalOp): ActionItem[] {
  return list.map((a) => {
    if (a.id !== op.id) return a;
    if (op.type === 'dismiss') return { ...a, dismissed: true };
    if (op.type === 'snooze') return { ...a, snoozed_until: op.until };
    return { ...a, dismissed: false, snoozed_until: null };
  });
}

export const snoozeUntil = (now: number, days: number): string => new Date(now + days * 86_400_000).toISOString();
