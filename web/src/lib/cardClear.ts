import { fmtBytes } from './format';
// Pure helpers for the "Clear card" dialog (cc-1, D-096): read the dry-run plan and check the typed count.
import type { TaskEvent } from './types';

export interface KeptReason {
  code: string;
  files: number;
  text: string;
}

export interface VerifyDrive {
  label: string;
  files: number;
  bytes: number;
  mismatches: number;
}

export interface VerifyKept {
  rel_path: string;
  reason: string;
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
  /** cc-2 (D-114): the verify-all-copies pass of the dry run; `all_copies` is false when it was bypassed. */
  verify: { all_copies: boolean; drives: VerifyDrive[]; verified_at: string | null };
  verify_kept: VerifyKept[];
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

function readVerify(v: unknown): ClearPlan['verify'] {
  const o = v && typeof v === 'object' ? (v as Record<string, unknown>) : {};
  const drives = Array.isArray(o.drives)
    ? (o.drives as Record<string, unknown>[]).map((d) => ({ label: String(d.label ?? ''), files: num(d.files), bytes: num(d.bytes), mismatches: num(d.mismatches) }))
    : [];
  return { all_copies: o.all_copies === true, drives, verified_at: typeof o.verified_at === 'string' ? o.verified_at : null };
}

/** Roughly what verifying every copy reads: each deletable file once per copy drive (an upper estimate). */
export function verifyEstimate(plan: Pick<ClearPlan, 'deletable_bytes' | 'copy_drives'>): { bytes: number; drives: string[] } {
  return { bytes: plan.deletable_bytes * plan.copy_drives.length, drives: plan.copy_drives };
}

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
        verify: readVerify(e.verify),
        verify_kept: Array.isArray(e.verify_kept)
          ? (e.verify_kept as { rel_path?: string; reason?: string }[]).map((k) => ({ rel_path: String(k.rel_path ?? ''), reason: String(k.reason ?? '') }))
          : [],
      };
    }
  }
  return { plan, error };
}

/** The typed confirmation must be exactly the planned file count (digits only, no spaces). */
export const confirmMatches = (typed: string, count: number): boolean => count > 0 && /^\d+$/.test(typed) && Number(typed) === count;


/** A dry run's live progress as one line ("Re-reading copies: 412 of 2,233 · 9.1 GB of 41.0 GB"), or '' before any. */
export function clearProgressLine(p: { phase?: string; current?: number; total?: number; message?: string; bytes_done?: number; bytes_total?: number } | null | undefined): string {
  if (!p) return '';
  if (p.phase === 'verify' && typeof p.current === 'number' && typeof p.total === 'number') {
    const bytes = typeof p.bytes_done === 'number' && typeof p.bytes_total === 'number' && p.bytes_total > 0 ? ` · ${fmtBytes(p.bytes_done)} of ${fmtBytes(p.bytes_total)}` : '';
    return `Re-reading copies: ${p.current.toLocaleString('en-US')} of ${p.total.toLocaleString('en-US')}${bytes}`;
  }
  return p.message ? p.message.charAt(0).toUpperCase() + p.message.slice(1) : '';
}

/** Fraction done of a verify pass (bytes when known, else copies), or null before it starts. */
export function clearProgressValue(p: { phase?: string; current?: number; total?: number; bytes_done?: number; bytes_total?: number } | null | undefined): number | null {
  if (!p || p.phase !== 'verify') return null;
  if (typeof p.bytes_done === 'number' && typeof p.bytes_total === 'number' && p.bytes_total > 0) return p.bytes_done / p.bytes_total;
  if (typeof p.current === 'number' && typeof p.total === 'number' && p.total > 0) return p.current / p.total;
  return null;
}
