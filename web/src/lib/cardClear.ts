// Pure helpers for the "Clear card" dialog (cc-1, D-092): read the dry-run plan and check the typed count.
import type { TaskEvent } from './types';

export interface KeptReason {
  code: string;
  files: number;
  text: string;
}

export interface ClearPlan {
  label: string;
  total: number;
  deletable_files: number;
  deletable_bytes: number;
  groups: number;
  kept_files: number;
  kept: KeptReason[];
  copy_drives: string[];
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** The plan a `cards clear --dry-run` task returned, or the error it gave. */
export function readClearPlan(events: TaskEvent[]): { plan: ClearPlan | null; error: string } {
  let plan: ClearPlan | null = null;
  let error = '';
  for (const e of events) {
    if (e.event === 'error' && e.message) error = e.message;
    if (e.event === 'result' && e.deletable_files !== undefined) {
      const kept = e.kept && typeof e.kept === 'object' ? (e.kept as Record<string, { files?: number; text?: string }>) : {};
      plan = {
        label: String(e.label ?? ''),
        total: num(e.total),
        deletable_files: num(e.deletable_files),
        deletable_bytes: num(e.deletable_bytes),
        groups: num(e.groups),
        kept_files: num(e.kept_files),
        kept: Object.entries(kept)
          .map(([code, v]) => ({ code, files: num(v.files), text: String(v.text ?? code) }))
          .sort((a, b) => b.files - a.files),
        copy_drives: Array.isArray(e.copy_drives) ? (e.copy_drives as string[]) : [],
      };
    }
  }
  return { plan, error };
}

/** The typed confirmation must be exactly the planned file count (digits only, no spaces). */
export const confirmMatches = (typed: string, count: number): boolean => count > 0 && /^\d+$/.test(typed) && Number(typed) === count;
