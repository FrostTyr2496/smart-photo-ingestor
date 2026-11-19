import { describe, expect, it } from 'vitest';
import { fmtBytes, fmtElapsed, fmtLeft, parseSize } from './format';
import {
  commandKey,
  driveStatus,
  hashOverall,
  liveStep,
  newestAssignTask,
  setupProblem,
  longLine,
  pace,
  shortLine,
  splitMessage,
  stepView,
  recentTask,
  summarize,
  taskMounts,
  taskTitle,
  displayLabel,
  workflowSteps,
} from './labels';
import { Palette } from './palette';
import { PRESETS, SCHEME_TOKENS } from './presets';
import { absorb } from './store';
import { cardHeadline, worstTier, type Card } from './tiers';
import type { Drive, TaskRec } from './types';

const drive = (over: Partial<Drive> = {}): Drive => ({
  mount: '/mnt/Big',
  role: 'LEGACY',
  label: 'Big',
  fs: 'apfs',
  size: '12.0 TB',
  free: '5.9 TB',
  volume_id: 'v1',
  mounted: true,
  looks_like_card: false,
  last_seen_at: null,
  local: false,
  key: 'v1',
  ignored: false,
  ...over,
});

const task = (over: Partial<TaskRec> = {}): TaskRec => ({
  id: 1,
  argv: ['python', '-m', 'photovault', '--json', 'hash', '/mnt/Big'],
  state: 'running',
  exitCode: null,
  lastSeq: 0,
  keepAwake: false,
  progress: null,
  final: false,
  startMs: null,
  note: '',
  error: '',
  result: null,
  jobId: null,
  ...over,
});

describe('format', () => {
  it('uses decimal units like the server', () => {
    expect(fmtBytes(115_100_000_000)).toBe('115 GB');
    expect(fmtBytes(1_500)).toBe('1.5 KB');
    expect(parseSize('5.9 TB')).toBe(5.9e12);
  });
  it('says elapsed and remaining time briefly', () => {
    expect(fmtElapsed(611)).toBe('10m 11s');
    expect(fmtLeft(12_000)).toBe('3h 20m left');
    expect(fmtLeft(45)).toBe('45s left');
  });
});

describe('commands', () => {
  it('titles and mounts a hash task', () => {
    const t = task();
    expect(taskTitle(t.argv, [drive()])).toBe('Hashing Big');
    expect(taskMounts(['hash', '/mnt/Big'])).toEqual(['/mnt/Big']);
    expect(taskMounts(['ingest', '--source', '/mnt/card', '--target', '/mnt/x9'])).toEqual(['/mnt/card', '/mnt/x9']);
    expect(commandKey(['meta', 'sidecars'])).toBe('meta_sidecars');
  });
  it('gives a working drive the sidecar step and a card none', () => {
    expect(workflowSteps(drive({ role: 'WORKING' })).map((s) => s.id)).toEqual(['index', 'hash', 'scan', 'sidecars']);
    expect(workflowSteps(drive({ role: 'LEGACY' })).map((s) => s.id)).toEqual(['index', 'hash', 'scan']);
    expect(workflowSteps(drive({ role: 'CARD' }))).toEqual([]);
  });
});

describe('progress', () => {
  it('computes percent, pace and time left', () => {
    const now = 1_000_000;
    const p = pace({ current: 4635, total: 95389 }, now - 611_000, now);
    expect(Math.floor(p.fraction! * 100)).toBe(4);
    expect(Math.round(p.perMin!)).toBe(455);
    expect(shortLine(p)).toMatch(/^4% · 3h \d+m left$/);
    expect(longLine(p, 'files')).toBe('4,635 of 95,389 files · 10m 11s elapsed · 455 per min');
  });
  it('has no fraction without a total', () => {
    const p = pace({ current: 10 }, null, 0);
    expect(p.fraction).toBeNull();
    expect(shortLine(p)).toBe('10');
  });
  it('splits the file being read off a progress message', () => {
    expect(splitMessage('115.1 GB hashed, 190.2 MB/s, reading Photos/a/DSC_1.NEF')).toEqual({
      stats: '115.1 GB hashed · 190.2 MB/s',
      path: 'Photos/a/DSC_1.NEF',
    });
    expect(splitMessage('walking')).toEqual({ stats: 'walking', path: '' });
  });
  it('absorbs events: progress, result, start time', () => {
    const t = absorb(task({ startMs: null }), {
      task_id: 1,
      state: 'done',
      exit_code: 0,
      argv: task().argv,
      last_seq: 3,
      events: [
        { event: 'progress', ts: '2026-11-18T10:00:00Z', message: 'reading x' },
        { event: 'result', hashed: 2, size: '1 GB', skipped: 0, unreadable: 0, deferred: 0 },
      ],
      latest_progress: { current: 2, total: 2 },
    });
    expect(t.startMs).toBe(Date.parse('2026-11-18T10:00:00Z'));
    expect(t.progress).toEqual({ current: 2, total: 2 });
    expect(summarize({ ...t, final: true }).text).toBe('Hashed 2 files (1 GB). 0 already done, 0 unreadable, 0 deferred.');
  });
  it('keeps the job id the server remembers, after its result event is gone (rv-31)', () => {
    const base = { task_id: 1, state: 'running', exit_code: null, argv: task().argv, last_seq: 900, latest_progress: null };
    expect(absorb(task(), { ...base, job_id: 41, events: [{ event: 'progress', current: 750 }] }).jobId).toBe(41);
    expect(absorb(task(), { ...base, events: [{ event: 'result', job_id: 7 }] }).jobId).toBe(7);
  });
});

describe('drive state', () => {
  it('finds the live step from a UI task or a terminal run', () => {
    const d = drive();
    expect(liveStep(d, [task()], [])?.step).toBe('hash');
    expect(liveStep(d, [task({ final: true })], [{ pid: 9, words: ['index', '/mnt/Big'], started_at: '' }])?.step).toBe('index');
    expect(driveStatus(d, liveStep(d, [task()], []))).toEqual({ text: 'Running · Hash', tone: 'running' });
  });
  it('never calls a hash with files waiting done (ui-13)', () => {
    expect(stepView('hash', { at: '2026-11-18T00:00:00Z', state: 'incomplete', pending: 74800 }, null, false)).toEqual({
      tone: 'warn',
      when: '74,800 files not hashed yet',
    });
    expect(stepView('hash', null, 'hash', false).tone).toBe('running');
    expect(stepView('scan', null, null, true).tone).toBe('queued');
  });
});

describe('palette', () => {
  it('loads the vendored engine and resolves the default palette', () => {
    expect(Palette.VERSION).toBe('1.0.0');
    const theme = Palette.resolveTheme({ presets: PRESETS, choice: { preset: null, custom: {} }, defaultId: 'argus', schemeTokens: SCHEME_TOKENS })!;
    expect(theme.id).toBe('argus');
    expect(theme.scheme).toBe('dark');
    expect(theme.tokens.go).toBe('#4ade80');
  });
  it('keeps body text readable on every preset', () => {
    for (const p of PRESETS) {
      const t = Palette.preview(p, SCHEME_TOKENS);
      expect(Palette.contrast(t.ink!, t.surface!), p.id).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe('resumed hash', () => {
  it('shows the drive progress, not only the files this run started with', () => {
    const now = 1_000_000;
    const run = pace({ current: 4683, total: 54725 }, now - 266_000, now);
    const all = hashOverall(run, 112797);
    expect(all.current).toBe(112797 - 54725 + 4683);
    expect(Math.floor(all.fraction! * 100)).toBe(55);
    expect(all.leftS).toBe(run.leftS); // the files left are the same either way
    expect(longLine(all, 'files')).toMatch(/^62,755 of 112,797 files on the drive · this run 4,683 of 54,725 · /);
    expect(hashOverall(run, undefined)).toBe(run); // unknown drive size: the run's own numbers
  });
});

describe('cards', () => {
  const counts = (o: number, u: number, n: number, b: number) => ({
    only_on_card: { files: o, bytes: 0 },
    unchecked: { files: u, bytes: 0 },
    needs_backup: { files: n, bytes: 0 },
    backed_up: { files: b, bytes: 0 },
  });
  const card = (tier: Card['tier'], c: Card['counts'], copies: Card['copies_by_volume'] = []): Card => ({
    volume_id: 'c1', label: 'Card A', mount: '/mnt/a', mounted: true, last_seen_at: null, tier,
    files: 10, bytes: 0, counts: c, cameras: [], copies_by_volume: copies, checked_at: null,
  });
  it('words each tier as the original UI did', () => {
    expect(cardHeadline(card('only_on_card', counts(3, 0, 7, 0)))).toBe('Back up now: 3 files only on this card.');
    expect(cardHeadline(card('needs_backup', counts(0, 0, 10, 0), [{ volume_id: 'x', label: 'X9', role: 'WORKING', files: 10, bytes: 0 }]))).toBe(
      'One copy: 10 files only on X9 (Working drive). Format only if you must.',
    );
    expect(worstTier([{ mounted: true, tier: 'backed_up' }, { mounted: true, tier: 'needs_backup' }, { mounted: false, tier: 'only_on_card' }])).toBe('needs_backup');
    expect(worstTier([{ mounted: false, tier: 'only_on_card' }])).toBeNull();
  });
});

describe('drive setup helpers', () => {
  const task = (id: number, mount: string): TaskRec =>
    ({ id, argv: ['python', '-m', 'photovault', '--json', 'volumes', 'assign', mount, '--role', 'LEGACY'], state: 'done', error: '' }) as unknown as TaskRec;
  it('finds the newest assign task for a mount', () => {
    const list = [task(3, '/mnt/B'), task(2, '/mnt/A'), task(1, '/mnt/A')];
    expect(newestAssignTask(list, '/mnt/A')?.id).toBe(2);
    expect(newestAssignTask(list, '/mnt/C')).toBeNull();
  });
  it('detects a read-only failure', () => {
    const p = setupProblem({ error: "OSError: [Errno 30] Read-only file system: '/x'", exitCode: 1 }, 'ntfs');
    expect(p.readOnly).toBe(true);
    expect(p.lines[0]).toMatch(/NTFS/);
    const q = setupProblem({ error: 'boom', exitCode: 1 }, 'apfs');
    expect(q.readOnly).toBe(false);
    expect(q.lines).toEqual(['boom']);
  });
});

describe('recentTask (rv-30)', () => {
  const base = { pid: 7, started_at: '2026-11-19T10:00:00Z', finished_at: '2026-11-19T10:05:00Z', warnings: 0 };
  it('reads a finished hash from the history like a UI task', () => {
    const t = recentTask({ ...base, words: ['hash', '/mnt/x'], exit_code: 0, state: 'done', error: null,
      result: { hashed: 2, size: '1 GB', skipped: 0, unreadable: 0, deferred: 0 } }, 0);
    expect(t.final).toBe(true);
    expect(summarize(t).text).toBe('Hashed 2 files (1 GB). 0 already done, 0 unreadable, 0 deferred.');
  });
  it('shows the error of a failed run, and a run that crashed without one', () => {
    const failed = recentTask({ ...base, words: ['index', '/x'], exit_code: 1, state: 'failed', error: 'disk gone', result: null }, 1);
    expect(summarize(failed)).toEqual({ text: 'disk gone', problem: true });
    const crashed = recentTask({ ...base, words: ['index', '/x'], exit_code: null, state: 'failed', error: null, result: null }, 2);
    expect(summarize(crashed).problem).toBe(true);
    expect(failed.id).not.toBe(crashed.id);
  });
});

describe('taskTitle covers every task command (rv-30)', () => {
  const drives = [{ volume_id: 'card-1', label: 'NIKON', mount: '/mnt/card' }, { volume_id: 'w', label: 'SMALL 1TB', mount: '/mnt/small' }] as never;
  const t = (...w: string[]) => taskTitle(['python', '-m', 'photovault', '--json', ...w], drives);
  it('names backups, make green and the catalog-only commands', () => {
    expect(t('backup', '--card', 'card-1', '--make-green')).toBe('Making NIKON green');
    expect(t('backup', '--card', 'card-1', '--to', '/mnt/small', '--dry-run')).toBe('Backing up NIKON → SMALL 1TB (dry run)');
    expect(t('backup', '--from-folder', '/mnt/small/2024', '--to', '/mnt/small')).toBe('Copying folder 2024 → SMALL 1TB');
    expect(t('shoots', 'plan')).toBe('Planning shoots and trips');
    expect(t('previews', 'cache', '/mnt/small')).toBe('Saving previews from SMALL 1TB');
    expect(t('report', 'duplicates')).toBe('Finding duplicates across drives');
    expect(t('something', 'new', '--x')).toBe('Running something new');
  });
  it('names the card an import reads, without the catalog\'s card: prefix', () => {
    const cardDrives = [{ volume_id: 'card-1', label: 'card:NIKON Z 6_2', mount: '/mnt/card' }, { volume_id: 'w', label: 'SMALL 1TB', mount: '/mnt/small' }] as never;
    const title = (...w: string[]) => taskTitle(['--json', ...w], cardDrives);
    expect(title('ingest', '--source', '/mnt/card', '--target', '/mnt/small')).toBe('Importing NIKON Z 6_2 → SMALL 1TB');
    expect(title('backup', '--card', 'card-1', '--to', '/mnt/small')).toBe('Backing up NIKON Z 6_2 → SMALL 1TB');
    // A follow copy reads the import's target: card → SMALL 1TB, then SMALL 1TB → the second drive.
    expect(title('backup', '--card', 'card-1', '--from', '/mnt/small', '--to', '/mnt/plus', '--follow', '7')).toBe('Backing up NIKON Z 6_2 from SMALL 1TB → plus');
    expect(title('backup', '--card', 'card-1', '--from', '/mnt/card', '--to', '/mnt/small')).toBe('Backing up NIKON Z 6_2 → SMALL 1TB');
    expect(title('ingest', '--source', '/mnt/EOS_DIGITAL', '--target', '/mnt/small', '--dry-run')).toBe('Importing EOS_DIGITAL → SMALL 1TB (dry run)');
  });
});

describe('displayLabel', () => {
  it('drops the card: prefix only', () => {
    expect(displayLabel('card:NIKON Z 6_2')).toBe('NIKON Z 6_2');
    expect(displayLabel('Card: X')).toBe('X');
    expect(displayLabel('Backup Plus')).toBe('Backup Plus');
    expect(displayLabel(null)).toBe('');
  });
});
