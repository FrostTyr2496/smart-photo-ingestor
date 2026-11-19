// Pure helpers for the Import dialog (im-4, D-091): batch size, the plan a dry run returns, and the tier it reaches.
import type { TaskEvent } from './types';

export type BatchChoice = '10' | '100' | 'all' | 'custom';

/** The `limit` argument: a positive whole number, `null` for the whole card, `undefined` when the custom text is not valid. */
export function batchLimit(choice: BatchChoice, custom: string): number | null | undefined {
  if (choice === 'all') return null;
  if (choice === 'custom') {
    const t = custom.trim();
    if (!/^\d+$/.test(t)) return undefined;
    const n = Number(t);
    return n >= 1 && Number.isSafeInteger(n) ? n : undefined;
  }
  return Number(choice);
}

export interface ImportInputs {
  source: string;
  target: string;
  name: string;
  limit: number | null;
}

/** The ingest arguments; `dry_run` is added by the caller. */
export function importArgs(i: ImportInputs): Record<string, unknown> {
  const args: Record<string, unknown> = { source: i.source, target: i.target };
  if (i.name.trim()) args.name = i.name.trim();
  if (i.limit !== null) args.limit = i.limit;
  return args;
}

/** Two dry runs match when every input matches; Apply is allowed only for the inputs that were planned. */
export const inputsKey = (i: ImportInputs): string => JSON.stringify([i.source, i.target, i.name.trim(), i.limit]);

export interface ImportPlan {
  files: number;
  groups: number;
  limit: number | null;
  groups_total: number;
  files_total: number;
  planned_bytes: number;
  planned: string;
  target_free: string;
  target_free_bytes: number | null;
  folders: Array<{ folder: string; files: number }>;
  sample: string[];
  kinds: Record<string, number>;
}

export interface PlanRead {
  plan: ImportPlan | null;
  warnings: string[];
  error: string;
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** What a finished dry run said: its `result`, its warnings (without the "dry run" notice) and its error, if any. */
export function readPlan(events: TaskEvent[]): PlanRead {
  const out: PlanRead = { plan: null, warnings: [], error: '' };
  for (const e of events) {
    if (e.event === 'warning' && e.code !== 'dry_run' && e.message) out.warnings.push(e.message);
    else if (e.event === 'error' && e.message) out.error = e.message;
    else if (e.event === 'result' && e.files !== undefined) {
      out.plan = {
        files: num(e.files),
        groups: num(e.groups),
        limit: typeof e.limit === 'number' ? e.limit : null,
        groups_total: num(e.groups_total),
        files_total: num(e.files_total),
        planned_bytes: num(e.planned_bytes),
        planned: String(e.planned ?? ''),
        target_free: String(e.target_free ?? 'unknown'),
        target_free_bytes: typeof e.target_free_bytes === 'number' ? e.target_free_bytes : null,
        folders: Array.isArray(e.folders) ? (e.folders as ImportPlan['folders']) : [],
        sample: Array.isArray(e.sample) ? (e.sample as string[]) : [],
        kinds: e.kinds && typeof e.kinds === 'object' ? (e.kinds as Record<string, number>) : {},
      };
    }
  }
  return out;
}

/** True when the plan needs more than the target has free; unknown free space does not block. */
export const overCapacity = (p: Pick<ImportPlan, 'planned_bytes' | 'target_free_bytes'>): boolean =>
  p.target_free_bytes !== null && p.planned_bytes > p.target_free_bytes;

/** The tier the files reach: one drive is "One copy" (yellow); a second writable drive makes them "Backed up" (green). */
export const tierAfterImport = (secondDrive: boolean): 'needs_backup' | 'backed_up' => (secondDrive ? 'backed_up' : 'needs_backup');

/** "20 of 480 files" for a batch, "480 files" for the whole card. */
export function batchLine(p: ImportPlan): string {
  const f = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'file' : 'files'}`;
  if (p.limit !== null && p.files_total > p.files) return `${p.files.toLocaleString('en-US')} of ${f(p.files_total)} (${p.groups} of ${p.groups_total} frames)`;
  return f(p.files);
}
