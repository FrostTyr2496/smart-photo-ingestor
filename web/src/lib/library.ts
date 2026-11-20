// The library checklist on the Tasks page (ob-1): what the server's GET /api/library/checklist reports, turned into
// one step per cell. Pure, so the wording and the done / partial / not-started rules are tested without a browser.

import { fmtInt, plural } from './format';

export interface ChecklistDrive {
  volume_id: string;
  label: string | null;
  role: string | null;
  connected: boolean;
  indexed: boolean;
  hashed_files: number;
  total_files: number;
  thumbs: { have: number; groups: number };
}

export interface ChecklistPlan {
  plan_id: string | null;
  created_at: string | null;
  shoots: number;
  unconfirmed: number;
}

export interface Checklist {
  drives: ChecklistDrive[];
  thumbs_enabled: boolean;
  thumbs_error: string | null;
  phone: { points: number; analysed: number };
  meta_scanned: { groups: number; total: number };
  plan: ChecklistPlan | null;
}

export type StepState = 'done' | 'partial' | 'none';

export interface Step {
  state: StepState;
  text: string;
}

const NONE: Step = { state: 'none', text: '—' };

/** A count step: done when all of `total` have it, partial with "have / total unit" when some do, else not started. */
export function countStep(have: number, total: number, unit: string): Step {
  if (total > 0 && have >= total) return { state: 'done', text: `${fmtInt(have)} ${unit}` };
  if (have > 0) return { state: 'partial', text: `${fmtInt(have)} / ${fmtInt(total)} ${unit}` };
  return NONE;
}

/** The three cells of a drive row: indexed (yes or not), hashed files and review thumbs. */
export function driveSteps(d: ChecklistDrive): { index: Step; hash: Step; thumbs: Step } {
  return {
    index: d.indexed ? { state: 'done', text: 'Indexed' } : { state: 'none', text: 'Not indexed' },
    hash: countStep(d.hashed_files, d.total_files, 'hashed'),
    thumbs: countStep(d.thumbs.have, d.thumbs.groups, 'thumbs'),
  };
}

export function phoneStep(p: { points: number; analysed: number }): Step {
  if (p.points <= 0) return { state: 'none', text: 'Not imported' };
  if (p.analysed >= p.points) return { state: 'done', text: `${plural(p.points, 'point')}, analysed` };
  return { state: 'partial', text: `${fmtInt(p.analysed)} / ${fmtInt(p.points)} analysed` };
}

export function metaStep(m: { groups: number; total: number }): Step {
  if (m.total <= 0) return NONE;
  if (m.groups >= m.total) return { state: 'done', text: `${fmtInt(m.total)} groups` };
  if (m.groups > 0) return { state: 'partial', text: `${fmtInt(m.groups)} / ${fmtInt(m.total)} groups` };
  return NONE;
}

export function planStep(p: ChecklistPlan | null): Step {
  if (!p) return { state: 'none', text: 'No plan yet' };
  return { state: 'done', text: `${plural(p.shoots, 'shoot')} planned` };
}

/** "N shoots to confirm →", or the all-confirmed wording once none are left. */
export function confirmLabel(unconfirmed: number): string {
  if (unconfirmed <= 0) return 'All shoots confirmed';
  return `${fmtInt(unconfirmed)} ${unconfirmed === 1 ? 'shoot' : 'shoots'} to confirm →`;
}

/** How many of the checklist's steps are done (a drive counts once per step), for the card's summary line. */
export function doneCount(c: Checklist): { done: number; total: number } {
  const steps: Step[] = [];
  for (const d of c.drives) {
    const s = driveSteps(d);
    steps.push(s.index, s.hash, s.thumbs);
  }
  steps.push(phoneStep(c.phone), metaStep(c.meta_scanned), planStep(c.plan));
  return { done: steps.filter((s) => s.state === 'done').length, total: steps.length };
}
