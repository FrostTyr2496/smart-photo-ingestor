import { sameMount } from './labels';

/** What grouping needs to know about one live run (rv-31). */
export interface RunLike {
  key: string;
  /** Mounts the run works on, in order (an import: card, then target). */
  mounts: string[];
  /** This run's own job id, once known (an import reports it in its plan). */
  jobId?: number;
  /** The job id this run follows (`backup --follow <job>`). */
  follow?: number;
}

export interface RunGroup<T extends RunLike> {
  main: T;
  /** Runs that follow `main` (the trailing second-drive copy). */
  subs: T[];
  /** Every distinct drive the group touches, `main`'s first. */
  mounts: string[];
}

/** The job a command follows: `--follow N` in its words. */
export function followOf(words: string[]): number | undefined {
  const at = words.indexOf('--follow');
  const n = at >= 0 ? Number(words[at + 1]) : NaN;
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

/**
 * One group per run. A run that follows a job whose run is also live becomes a subtask of it;
 * a follower whose parent is not live (or not known) stays a group of its own.
 */
export function groupRuns<T extends RunLike>(runs: T[], same: (a: string, b: string) => boolean = sameMount): RunGroup<T>[] {
  const parentOf = new Map<string, T>();
  for (const r of runs) {
    if (r.follow === undefined) continue;
    const p = runs.find((x) => x !== r && x.jobId === r.follow && x.follow === undefined);
    if (p) parentOf.set(r.key, p);
  }
  const groups: RunGroup<T>[] = [];
  for (const r of runs) {
    if (parentOf.has(r.key)) continue;
    const subs = runs.filter((x) => parentOf.get(x.key) === r);
    const mounts: string[] = [];
    for (const m of [...r.mounts, ...subs.flatMap((s) => s.mounts)]) if (!mounts.some((x) => same(x, m))) mounts.push(m);
    groups.push({ main: r, subs, mounts });
  }
  return groups;
}
