// What people read about drives and tasks. Wording ported from the earlier plain-JS UI, so both
// say the same thing; the API keeps its own values.
import { fmtBytes, fmtDate, fmtElapsed, fmtInt, fmtLeft, plural, relTime } from './format';
import type { Drive, ExtRun, Progress, RecentRun, Run, StaleJob, StepInfo, TaskRec } from './types';

export type RoleKey = 'card' | 'working' | 'legacy' | 'master' | 'unset';

export const ROLE_LABEL: Record<string, string> = {
  CARD: 'Card',
  'CARD (new)': 'Card',
  WORKING: 'Working drive',
  LEGACY: 'Legacy drive',
  MASTER: 'Master backup',
};
export const ROLE_WORD: Record<string, string> = { WORKING: 'working drive', MASTER: 'master backup', LEGACY: 'legacy drive' };
export const STATE_LABEL: Record<string, string> = { running: 'Running', done: 'Done', failed: 'Failed', paused: 'Paused' };
export const JOB_WORD: Record<string, string> = {
  index_volume: 'Index',
  baseline_hash: 'Hash',
  card_ingest: 'Import',
  replicate: 'Backup',
  fixity_audit: 'Audit',
};

export function roleKey(role: string | null | undefined): RoleKey {
  if (!role) return 'unset';
  if (role.startsWith('CARD')) return 'card';
  const lc = role.toLowerCase();
  return lc === 'working' || lc === 'legacy' || lc === 'master' ? lc : 'unset';
}
export const roleLabel = (role: string | null | undefined): string => (role && ROLE_LABEL[role]) || 'Not set up';
export const isCardDrive = (d: Drive): boolean => roleKey(d.role) === 'card' || d.looks_like_card;

export const baseName = (path: string | null | undefined): string => String(path || '').split('/').filter(Boolean).pop() || '';
/** A card's catalog label defaults to `card:<volume name>` (volumes.py); people know it by the volume name alone. */
export const displayLabel = (label: string | null | undefined): string => (label || '').replace(/^card:\s*/i, '');
export const driveName = (d: Drive): string => displayLabel(d.label) || baseName(d.mount) || d.volume_id || 'Unnamed drive';
export const sameMount = (a?: string | null, b?: string | null): boolean =>
  Boolean(a && b) && a!.replace(/\/+$/, '') === b!.replace(/\/+$/, '');

export function mountName(path: string | undefined, drives: Drive[]): string {
  const d = drives.find((x) => x.mount === path);
  return d ? driveName(d) : baseName(path) || 'drive';
}

// ---------------------------------------------------------------- commands

/** argv is ["python", "-m", "photovault", "--json", ...words]; the words after --json are the command. */
export function argvWords(argv: string[] | undefined): string[] {
  const list = argv || [];
  const at = list.indexOf('--json');
  return at >= 0 ? list.slice(at + 1) : list.slice();
}

export function flagValue(words: string[], flag: string): string | undefined {
  const at = words.indexOf(flag);
  return at >= 0 ? words[at + 1] : undefined;
}

export function commandKey(words: string[]): string {
  const [a, b] = words;
  if (a === 'volumes' && b === 'assign') return 'assign';
  if (a === 'cards' && b === 'check') return 'card_check';
  if (a === 'cards' && b === 'clear') return 'card_clear';
  if (a === 'meta') return ({ scan: 'meta_scan', refs: 'meta_refs', sidecars: 'meta_sidecars', phone: 'meta_phone' } as Record<string, string>)[b ?? ''] || 'meta';
  if ((a === 'shoots' || a === 'previews' || a === 'report' || a === 'lrcat' || a === 'metrics') && b) return `${a}_${b}`;
  return a || '';
}

export function taskTitle(argv: string[], drives: Drive[]): string {
  const w = argvWords(argv);
  if (!w.length) return 'Starting…';
  const dry = w.includes('--dry-run');
  const name = (p: string | undefined) => mountName(p, drives);
  switch (commandKey(w)) {
    case 'assign':
      return `Assigning ${name(w[2])} as ${ROLE_WORD[flagValue(w, '--role') ?? ''] || 'drive'}`;
    case 'ingest':
      return `Importing ${name(flagValue(w, '--source'))} → ${name(flagValue(w, '--target'))}${dry ? ' (dry run)' : ''}`;
    case 'resume':
      return 'Resuming an import';
    case 'card-status':
      return `Checking card ${name(w[1])}`;
    case 'card_check':
      return `Checking what is backed up on ${name(w[2])}`;
    case 'card_clear':
      return `${dry ? 'Planning a clear of' : 'Clearing'} card ${name(w[2])}`;
    case 'index':
      return `Indexing ${name(w[1])}`;
    case 'hash':
      return `Hashing ${name(w[1])}`;
    case 'meta_scan':
      return `Scanning metadata on ${name(flagValue(w, '--volume'))}`;
    case 'meta_refs':
      return `Reading GPS references from ${flagValue(w, '--label') || 'a folder'}`;
    case 'meta_sidecars':
      return `Writing sidecars on ${name(flagValue(w, '--volume'))}${dry ? ' (dry run)' : ''}`;
    case 'meta_phone':
      return 'Reading phone locations';
    case 'backup': {
      const id = flagValue(w, '--card');
      const card = id ? drives.find((d) => d.volume_id === id) : undefined;
      const cardName = card ? driveName(card) : 'a card';
      const to = flagValue(w, '--to');
      const from = flagValue(w, '--from');
      const folder = flagValue(w, '--from-folder');
      // A copy that trails an import reads the import's target, not the card: name that hop (card → A, then A → B).
      const via = from && !(card && sameMount(card.mount, from)) ? `${name(from)} ` : '';
      const tail = `${to ? ` ${via ? `from ${via}` : ''}→ ${name(to)}` : ''}${dry ? ' (dry run)' : ''}`;
      if (w.includes('--make-green')) return `Making ${cardName} green${tail}`;
      if (folder) return `Copying folder ${baseName(folder) || folder}${tail}`;
      return `Backing up ${cardName}${tail}`;
    }
    case 'shoots_plan':
      return 'Planning shoots and trips';
    case 'shoots_apply':
      return 'Applying reviewed shoots';
    case 'shoots_undo':
      return 'Undoing applied shoots';
    case 'previews_cache':
      return `Saving previews from ${name(w[2])}`;
    case 'report_duplicates':
      return 'Finding duplicates across drives';
    case 'lrcat_mark':
      return `Marking files a Lightroom catalog uses on ${name(flagValue(w, '--volume'))}`;
    case 'metrics_backfill':
      return 'Estimating past drive work';
    default:
      return w[0] ? `Running ${w.filter((x) => !x.startsWith('-')).slice(0, 2).join(' ')}` : 'Working';
  }
}

/** The mounts a command works on. An import lists its card first, then its target. */
export function taskMounts(words: string[]): string[] {
  let out: Array<string | undefined>;
  switch (commandKey(words)) {
    case 'assign':
    case 'card_check':
    case 'card_clear':
      out = [words[2]];
      break;
    case 'index':
    case 'hash':
    case 'card-status':
      out = [words[1]];
      break;
    case 'meta_scan':
    case 'meta_sidecars':
      out = [flagValue(words, '--volume')];
      break;
    case 'ingest':
      out = [flagValue(words, '--source'), flagValue(words, '--target')];
      break;
    case 'backup':
      out = [flagValue(words, '--from'), flagValue(words, '--to')];
      break;
    case 'previews_cache':
      out = [words[2]];
      break;
    default:
      out = [];
  }
  return out.filter((m): m is string => Boolean(m));
}

/** "copying 12 file(s) from SMALL 1TB to Backup Plus" → { from: "SMALL 1TB", to: "Backup Plus" } (rv-36). */
export function parseRoute(message: string | null | undefined): { from: string; to: string } | null {
  const m = /^copying \d+ file\(s\) from (.+) to (.+)$/.exec(message || '');
  return m ? { from: m[1]!, to: m[2]! } : null;
}

const mountOfLabel = (label: string, drives: Drive[]): string | undefined =>
  drives.find((d) => d.mounted && (d.label === label || driveName(d) === label))?.mount;

/**
 * The drives a live task works on, in order. Most commands name them; `backup --card <id> --make-green` names only the
 * card's id, so its card comes from the drive list and its source and target from the route it reported (rv-36).
 * `touched` leaves the card out: a make-green run copies between working drives and never reads the card.
 */
export function runMounts(t: Pick<TaskRec, 'argv' | 'route'>, drives: Drive[], touched = false): string[] {
  const words = argvWords(t.argv);
  const named = taskMounts(words);
  if (named.length || !words.includes('--make-green')) return named;
  const card = drives.find((d) => d.volume_id && d.volume_id === flagValue(words, '--card'))?.mount;
  const from = t.route ? mountOfLabel(t.route.from, drives) : undefined;
  const to = t.route ? mountOfLabel(t.route.to, drives) : undefined;
  return [touched ? undefined : card, from, to].filter((m): m is string => Boolean(m));
}

/** Progress counts files, except metadata work, which counts frames. */
export function unitFor(argv: string[]): string {
  const kind = commandKey(argvWords(argv));
  return kind === 'meta_scan' || kind === 'meta_sidecars' ? 'frames' : 'files';
}

// ---------------------------------------------------------------- progress

export interface Pace {
  current: number;
  total: number;
  /** 0…1, or null when there is no total. */
  fraction: number | null;
  elapsedS: number;
  perMin: number | null;
  leftS: number | null;
}

export function pace(progress: Progress | null | undefined, startMs: number | null, now: number, minElapsedS = 5): Pace {
  const total = progress && Number(progress.total) > 0 ? Number(progress.total) : 0;
  const current = progress ? Number(progress.current) || 0 : 0;
  const elapsedS = startMs ? Math.max(0, (now - startMs) / 1000) : 0;
  const fraction = total ? Math.min(1, current / total) : null;
  let perMin: number | null = null;
  let leftS: number | null = null;
  if (total && current > 0 && elapsedS >= minElapsedS) {
    const rate = current / elapsedS;
    perMin = rate * 60;
    if (current < total) leftS = (total - current) / rate;
  }
  return { current, total, fraction, elapsedS, perMin, leftS };
}

/**
 * A hash run counts only the files still waiting when it started, so a resumed hash starts again at 0 %. With the
 * drive's file count, its progress becomes the drive's: files hashed before the run plus this run's count, out of
 * every file. Time left and pace stay the run's own (the files left are the same either way).
 */
export function hashOverall(p: Pace, driveFiles: number | undefined): Pace & { run?: { current: number; total: number } } {
  if (!driveFiles || !p.total || p.total > driveFiles) return p;
  const before = driveFiles - p.total;
  return { ...p, fraction: Math.min(1, (before + p.current) / driveFiles), current: before + p.current, total: driveFiles, run: { current: p.current, total: p.total } };
}

export const isHash = (words: string[]): boolean => commandKey(words) === 'hash';

/** The one line a compact task card shows: "5% · 3h 20m left". */
export function shortLine(p: Pace): string {
  if (p.fraction === null) return p.current ? fmtInt(p.current) : 'Starting…';
  const pct = `${Math.floor(p.fraction * 100)}%`;
  return p.leftS !== null ? `${pct} · ${fmtLeft(p.leftS)}` : pct;
}

/** "4,635 of 95,389 files · 10m 11s elapsed": the details line. */
export function longLine(p: Pace & { run?: { current: number; total: number } }, unit: string): string {
  const parts: string[] = [];
  if (p.run) {
    parts.push(`${fmtInt(p.current)} of ${fmtInt(p.total)} ${unit} on the drive`);
    parts.push(`this run ${fmtInt(p.run.current)} of ${fmtInt(p.run.total)}`);
  } else if (p.total) parts.push(`${fmtInt(p.current)} of ${fmtInt(p.total)} ${unit}`);
  else if (p.current) parts.push(`${fmtInt(p.current)} ${unit}`);
  if (p.elapsedS) parts.push(`${fmtElapsed(p.elapsedS)} elapsed`);
  if (p.perMin !== null) parts.push(`${fmtInt(p.perMin)} per min`);
  return parts.join(' · ');
}

/**
 * A progress message such as "115.1 GB hashed, 190.2 MB/s, reading Photos/…/DSC_4103.NEF" split into its
 * stats ("115.1 GB hashed · 190.2 MB/s") and the file being read, so the card can show them apart.
 */
export function splitMessage(message: string | undefined): { stats: string; path: string } {
  const text = message || '';
  const at = text.indexOf(', reading ');
  if (at < 0) return { stats: text, path: '' };
  return { stats: text.slice(0, at).replace(/, /g, ' · '), path: text.slice(at + ', reading '.length) };
}

/** A file an `item` event names (rv-34): how to word its state, and how loud. */
export function fileState(state: string, kind: string): { label: string; tone: 'go' | 'muted' | 'err' } {
  if (state === 'failed') return { label: 'Failed', tone: 'err' };
  if (state === 'skipped_dup') return { label: 'Already there', tone: 'muted' };
  if (state === 'committed') return { label: kind === 'card_clear' ? 'Deleted' : 'Copied', tone: 'go' };
  return { label: state.replace(/_/g, ' '), tone: 'muted' };
}

/** "DCIM/286NCZ_6/DSC_0412.NEF" → { name: "DSC_0412.NEF", dir: "DCIM/286NCZ_6" }. */
export function splitPath(path: string): { name: string; dir: string } {
  const at = path.lastIndexOf('/');
  return at < 0 ? { name: path, dir: '' } : { name: path.slice(at + 1), dir: path.slice(0, at) };
}

/**
 * A progress message's stats ("27.9 GB copied · 182.8 MB/s · 0 failed") as tiles: a value and what it counts.
 * A bare rate is labelled "speed". Text that does not start with a number is one tile with no value.
 */
export function statTiles(stats: string): { value: string; label: string; warn: boolean }[] {
  return stats
    .split(/ · |, /)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const m = /^([\d.,]+(?:\s*[KMGT]?B(?:\/s)?)?)(?:\s+(.*))?$/.exec(p);
      if (!m) return { value: '', label: p, warn: false };
      const value = m[1] ?? '';
      const label = m[2] ?? (value.endsWith('/s') ? 'speed' : '');
      return { value, label, warn: /fail|error/.test(label) && Number(value.replace(/,/g, '')) > 0 };
    });
}

// ---------------------------------------------------------------- results

const TIER_HEAD: Record<string, string> = {
  only_on_card: 'Back up now.',
  unchecked: 'Not checked yet.',
  needs_backup: 'One copy only.',
  backed_up: 'Safe to remove.',
  empty: 'No photos on this card.',
};

type R = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export function resultText(kind: string, r: R, dry: boolean): string {
  switch (kind) {
    case 'ingest':
      if (dry) return `Dry run: ${plural(r.files, 'file')} in ${plural(r.groups, 'frame')} would be copied (${r.planned}). Nothing was written.`;
      return `Copied ${plural(r.copied, 'file')} (${r.copied_size}). ${fmtInt(r.skipped_dup)} already on the drive, skipped. ${fmtInt(r.failed)} failed.`;
    case 'index':
      return `Indexed ${fmtInt(r.indexed)} of ${plural(r.files, 'file')}. ${fmtInt(r.unchanged)} unchanged, ${fmtInt(r.missing)} missing, ${fmtInt(r.errors)} errors.`;
    case 'hash':
      return `Hashed ${plural(r.hashed, 'file')} (${r.size}). ${fmtInt(r.skipped)} already done, ${fmtInt(r.unreadable)} unreadable, ${fmtInt(r.deferred)} deferred.`;
    case 'meta_scan':
      return `Read metadata for ${plural(r.scanned, 'frame')}: ${fmtInt(r.with_gps)} with a location, ${fmtInt(r.errors)} read errors.`;
    case 'meta_refs':
      return `Stored ${plural(r.stored, 'location fix')} from ${r.label}. ${fmtInt(r.errors)} errors.`;
    case 'meta_sidecars':
      if (r.dry_run) return `Would write ${plural(r.would_write, 'sidecar file')}. Nothing was written.`;
      return `Wrote ${plural(r.written, 'sidecar file')}${r.conflicts ? `, ${fmtInt(r.conflicts)} conflicts` : ''}.`;
    case 'assign':
      return `${r.label} is set up as ${ROLE_WORD[r.role] || 'drive'}.`;
    case 'card_check': {
      const c = (r.counts || {}) as R;
      const n = (t: string) => fmtInt((c[t] || {}).files);
      return (
        `${TIER_HEAD[r.tier] ?? ''} ${n('backed_up')} backed up, ${n('needs_backup')} need backup, ` +
        `${n('only_on_card')} only on the card${(c.unchecked || {}).files ? `, ${n('unchecked')} not checked` : ''}.`
      );
    }
    case 'card_clear':
      if (dry) return `Dry run: ${plural(r.deletable_files, 'file')} could be deleted, ${fmtInt(r.kept_files)} kept. Nothing was deleted.`;
      return `Deleted ${plural(r.deleted, 'file')} from the card${r.stopped ? `. Stopped: ${r.stopped}` : ''}.`;
    case 'card-status':
      if (r.state === 'green') return `Safe to format: all ${plural(r.total, 'file')} are verified on a working drive.`;
      return `Not safe to format. ${fmtInt(r.safe)} of ${fmtInt(r.total)} files verified, ${fmtInt(r.unknown)} not imported yet, ${fmtInt(r.changed)} changed, ${fmtInt(r.short)} short.`;
    default:
      return Object.entries(r || {})
        .filter(([k, v]) => typeof v === 'number' && !['v', 'seq', 'job_id'].includes(k))
        .map(([k, v]) => `${fmtInt(v as number)} ${k.replace(/_/g, ' ')}`)
        .join(', ');
  }
}

/** The plain-words result of a finished task. `problem` marks text to show in the error colour. */
export function summarize(t: TaskRec): { text: string; problem: boolean } {
  const words = argvWords(t.argv);
  const text = t.result ? resultText(commandKey(words), t.result, words.includes('--dry-run')) : '';
  if (t.state === 'paused') return { text: text ? `Paused. ${text}` : 'Paused. Run it again to pick up where it stopped.', problem: false };
  if (t.state === 'failed') {
    const why = t.error || `Failed (exit code ${t.exitCode}).`;
    return { text: text ? `${why} ${text}` : why, problem: true };
  }
  return { text: text || 'Finished.', problem: false };
}

/** A finished run from the history as a task record, so `summarize` and `taskTitle` read it the same way. */
export function recentTask(r: RecentRun, i: number): TaskRec {
  return {
    id: -1 - i,
    argv: r.words || [],
    state: r.state,
    exitCode: r.exit_code,
    lastSeq: 0,
    keepAwake: false,
    progress: null,
    final: true,
    startMs: Date.parse(r.started_at) || null,
    note: '',
    error: r.error || '',
    result: r.result,
    jobId: null,
  };
}

/** One line per catalog run on the drive view. */
export function runDetail(r: Run): string {
  const res = r.result;
  if (!res) return 'No stats were saved for this run.';
  if (r.kind === 'index_volume') {
    return `${plural(res.files, 'file')} seen · ${fmtInt(res.indexed)} indexed · ${fmtInt(res.unchanged)} unchanged · ${fmtInt(res.missing)} missing · ${fmtInt(res.errors)} read errors`;
  }
  return `${fmtInt(res.hashed)} of ${fmtInt(res.files)} hashed (${fmtBytes(res.bytes)}) · ${fmtInt(res.skipped)} skipped · ${fmtInt(res.unreadable)} unreadable · ${fmtInt(res.deferred)} deferred · ${fmtInt(res.excluded)} excluded`;
}

export const runWhen = (r: Run): string => `${STATE_LABEL[r.state] || r.state}${r.started_at ? ` · started ${fmtDate(r.started_at)}` : ''}`;

export const staleText = (j: StaleJob): string =>
  `Job ${j.job_id} · ${JOB_WORD[j.kind] || j.kind} · ${j.label || 'unknown drive'} · started ${relTime(j.created_at) || j.created_at}`;

// ---------------------------------------------------------------- workflow

export type StepId = 'index' | 'hash' | 'scan' | 'sidecars';
export const STEP_NAME: Record<StepId, string> = { index: 'Index', hash: 'Hash', scan: 'Scan metadata', sidecars: 'Write sidecars' };
export const STEP_HINT: Record<StepId, string> = {
  index: 'List every file',
  hash: "Verify each file's bytes",
  scan: 'Read dates and locations',
  sidecars: 'Save edits next to the photos',
};
const STEP_BY_CMD: Record<string, StepId> = { index: 'index', hash: 'hash', meta_scan: 'scan', meta_sidecars: 'sidecars' };

export interface WorkflowStep {
  id: StepId;
  cmd: string;
  args: Record<string, unknown>;
}

/** A drive's workflow is a fixed run of engine steps. Cards have none: they are imported, not indexed. */
export function workflowSteps(d: Drive): WorkflowStep[] {
  const key = roleKey(d.role);
  if (!d.volume_id || key === 'card') return [];
  const mount = d.mount;
  const steps: WorkflowStep[] = [
    { id: 'index', cmd: 'index', args: { mount } },
    { id: 'hash', cmd: 'hash', args: { mount } },
    { id: 'scan', cmd: 'meta_scan', args: { mount } },
  ];
  if (key === 'working') steps.push({ id: 'sidecars', cmd: 'meta_sidecars', args: { mount, dry_run: false } });
  return steps;
}

/** The step a live task (UI or terminal) is running on this drive. */
export function liveStep(d: Drive, tasks: TaskRec[], elsewhere: ExtRun[]): { step: StepId; task?: TaskRec; ext?: ExtRun } | null {
  for (const t of tasks) {
    if (t.final) continue;
    const words = argvWords(t.argv);
    if (!words.includes(d.mount)) continue;
    const step = STEP_BY_CMD[commandKey(words)];
    if (step) return { step, task: t };
  }
  for (const e of elsewhere) {
    const words = e.words || [];
    if (!words.includes(d.mount)) continue;
    const step = STEP_BY_CMD[commandKey(words)];
    if (step) return { step, ext: e };
  }
  return null;
}

/** The run working on drive `d` (a UI task first, then a terminal run), as a Tasks link key: `t<id>` / `p<pid>` (rv-37). */
export function liveTaskKey(d: Drive, tasks: TaskRec[], elsewhere: ExtRun[], drives: Drive[]): string | null {
  if (!d.mount) return null;
  const t = tasks.find((x) => !x.final && runMounts(x, drives).some((m) => sameMount(m, d.mount)));
  if (t) return `t${t.id}`;
  const e = elsewhere.find((x) => taskMounts(x.words || []).some((m) => sameMount(m, d.mount)));
  return e ? `p${e.pid}` : null;
}

/** What one running command does to one drive it names (rv-35): read it or write it, in words. */
export interface DriveIO {
  mode: 'read' | 'write';
  text: string;
}

/**
 * Every live task's effect on drive `d`: an import or backup reads its source and writes its target (a dry run only
 * reads the source), hash / index / metadata / card check read, a card clear writes. Empty when the drive is idle.
 */
export function driveIO(d: Drive, tasks: TaskRec[], elsewhere: ExtRun[], drives: Drive[]): DriveIO[] {
  if (!d.mounted || !d.mount) return [];
  const out: DriveIO[] = [];
  const name = (p: string | undefined) => mountName(p, drives);
  const look = (words: string[], mounts: string[]) => {
    const key = commandKey(words);
    const dry = words.includes('--dry-run');
    const at = mounts.findIndex((m) => sameMount(m, d.mount));
    if (at < 0) return;
    if (key === 'ingest' || key === 'backup') {
      if (at === 0) out.push({ mode: 'read', text: dry ? 'Planning a copy' : `Copying to ${name(mounts[1])}` });
      else if (!dry) out.push({ mode: 'write', text: `Receiving from ${name(mounts[0])}` });
      return;
    }
    const READS: Record<string, string> = {
      hash: 'Hashing',
      index: 'Indexing',
      meta_scan: 'Reading metadata',
      meta_sidecars: 'Reading sidecars',
      card_check: 'Checking',
      'card-status': 'Checking',
    };
    if (key === 'card_clear') out.push(dry ? { mode: 'read', text: 'Planning a clear' } : { mode: 'write', text: 'Clearing' });
    else if (READS[key]) out.push({ mode: 'read', text: READS[key] });
  };
  for (const t of tasks) if (!t.final) look(argvWords(t.argv), runMounts(t, drives, true));
  for (const e of elsewhere) look(e.words || [], taskMounts(e.words || []));
  return out;
}

/** "Running · Hash", or "Last run: Hash · 2 hours ago", for a drive in the sidebar. */
export function driveStatus(d: Drive, live: ReturnType<typeof liveStep>): { text: string; tone: 'running' | 'warn' | 'plain' } {
  if (live) return { text: `Running · ${STEP_NAME[live.step]}`, tone: 'running' };
  let best: (StepInfo & { id: StepId }) | null = null;
  for (const [id, v] of Object.entries(d.steps || {})) {
    if (v && v.at && (!best || Date.parse(v.at) > Date.parse(best.at))) best = { id: id as StepId, ...v };
  }
  if (!best) return { text: roleKey(d.role) === 'card' ? 'Ready to import' : 'Not run yet', tone: 'plain' };
  const lead =
    best.state === 'paused' ? 'Paused' : best.state === 'failed' ? 'Failed' : best.state === 'running' || best.state === 'planned' ? 'Interrupted' : 'Last run';
  return { text: `${lead}: ${STEP_NAME[best.id]} · ${relTime(best.at)}`, tone: lead === 'Last run' ? 'plain' : 'warn' };
}

export type StepTone = 'running' | 'queued' | 'never' | 'done' | 'warn';

/** One timeline step's display state, from the live task, the workflow in progress, and the saved last run. */
export function stepView(
  step: StepId,
  last: StepInfo | null | undefined,
  liveId: StepId | null,
  queued: boolean,
): { tone: StepTone; when: string } {
  if (liveId === step) return { tone: 'running', when: 'Running now' };
  if (queued) return { tone: 'queued', when: 'Waiting' };
  if (!last) return { tone: 'never', when: 'Never run' };
  const ago = relTime(last.at);
  const left = last.pending ? ` · ${fmtInt(last.pending)} left` : '';
  if (last.state === 'done') return { tone: 'done', when: `Last run ${ago}` };
  if (last.state === 'incomplete') return { tone: 'warn', when: `${fmtInt(last.pending)} files not hashed yet` };
  if (last.state === 'paused') return { tone: 'warn', when: `Paused ${ago}${left}` };
  if (last.state === 'failed') return { tone: 'warn', when: `Failed ${ago}` };
  return { tone: 'warn', when: `Interrupted ${ago}` };
}

// ---------------------------------------------------------------- drive setup (rv-4)

/** The newest `volumes assign` task for a mount (tasks are newest first). */
export function newestAssignTask(tasks: TaskRec[], mount: string): TaskRec | null {
  for (const t of tasks) {
    const w = argvWords(t.argv);
    if (commandKey(w) === 'assign' && sameMount(w[2], mount)) return t;
  }
  return null;
}

/** Why a setup failed, ported from the earlier plain-JS UI. A read-only drive gets an explanation and an exit. */
export function setupProblem(t: Pick<TaskRec, 'error' | 'exitCode'>, fs: string | null): { readOnly: boolean; lines: string[]; msg: string } {
  const msg = t.error || `The setup stopped (exit code ${t.exitCode ?? 'unknown'}).`;
  if (/Errno 30|Read-only file system/i.test(msg)) {
    return {
      readOnly: true,
      lines: [
        (fs || '').toUpperCase() === 'NTFS'
          ? "macOS can read NTFS drives but cannot write to them, so the drive's ID file could not be written."
          : "This drive is mounted read-only, so the drive's ID file could not be written.",
        'A working drive or master backup gets a small ID file (.photovault-id) at its top, so it is recognised ' +
          'whatever its name. Nothing was written and nothing on the drive changed.',
        "You can set it up as a legacy drive instead: nothing is written to it, and its photos are listed, hashed " +
          'and counted as copies. To use it as a master backup, reformat it as APFS or exFAT first (this erases it).',
      ],
      msg,
    };
  }
  return { readOnly: false, lines: [msg], msg };
}

/** rv-50: a copy or hash finishes a file every few seconds; this long with none means the drive may be stuck. */
export const STALL_S = 180;
const STALL_KINDS = new Set(['ingest', 'backup', 'resume', 'hash', 'card_check']);

/**
 * A warning when a running copy or hash has reported nothing for `STALL_S` (rv-50). `lastMs` is its last file or
 * progress report (null before the first: no warning). `diskWait` is the process waiting on a drive right now.
 */
export function stallNote(
  kind: string,
  lastMs: number | null | undefined,
  now: number,
  opts: { diskWait?: boolean; file?: string } = {},
): { text: string; warn: true } | null {
  if (!STALL_KINDS.has(kind) || lastMs == null) return null;
  const quiet = (now - lastMs) / 1000;
  if (quiet < STALL_S) return null;
  const after = opts.file ? ` (last finished: ${opts.file})` : '';
  const why = opts.diskWait
    ? 'It is waiting on the drive, which usually means a damaged spot the drive keeps retrying.'
    : 'The drive may be stuck on a damaged spot, or asleep.';
  return {
    text:
      `Stalled: no progress for ${fmtElapsed(quiet)}${after}. ${why} ` +
      'Pause takes effect only once the read gives up; if it stays stuck, eject or unplug the drive it reads from (never the target).',
    warn: true,
  };
}
