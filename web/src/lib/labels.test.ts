import { describe, expect, it } from 'vitest';
import { fmtBytes, fmtElapsed, fmtLeft, parseSize } from './format';
import {
  commandKey,
  driveStatus,
  liveStep,
  longLine,
  pace,
  shortLine,
  splitMessage,
  stepView,
  summarize,
  taskMounts,
  taskTitle,
  workflowSteps,
} from './labels';
import { Palette } from './palette';
import { PRESETS, SCHEME_TOKENS } from './presets';
import { absorb } from './store';
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
