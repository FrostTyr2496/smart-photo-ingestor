import { motion } from 'framer-motion';
import { useState } from 'react';
import { fmtElapsed, fmtInt, relTime } from '../lib/format';
import { argvWords, hashOverall, isHash, longLine, pace, shortLine, splitMessage, summarize, taskTitle, unitFor, type Pace } from '../lib/labels';
import { useStore } from '../lib/store';
import type { Drive, ExtRun, TaskRec } from '../lib/types';
import { Disclosure, Icon, StatePill } from './bits';
import { ProgressBar } from './ProgressBar';

const QUIET_WARN_S = 600; // a terminal run with no catalog write for this long "may be stuck"

/** What a card shows, the same for a task the UI started and a run started in a terminal (ui-11). */
export interface TaskVM {
  key: string;
  title: string;
  pace: Pace & { run?: { current: number; total: number } };
  unit: string;
  stats: string;
  path: string;
  note: { text: string; warn: boolean } | null;
  keepAwake: boolean;
  origin: string | null;
  tech: string;
  pauseId: number | null;
}

export function taskVM(t: TaskRec, drives: Drive[], now: number, driveFiles: Record<string, number> = {}): TaskVM {
  const words = argvWords(t.argv);
  const p = pace(t.progress, t.startMs, now);
  const msg = splitMessage(t.note);
  const isProgressMsg = Boolean(t.progress?.message) && t.note === t.progress?.message;
  return {
    key: `t${t.id}`,
    title: taskTitle(t.argv, drives),
    pace: isHash(words) ? hashOverall(p, driveFiles[words[1] ?? '']) : p,
    unit: unitFor(t.argv),
    stats: isProgressMsg ? msg.stats : '',
    path: isProgressMsg ? msg.path : '',
    note: !isProgressMsg && t.note ? { text: t.note, warn: true } : null,
    keepAwake: t.keepAwake,
    origin: null,
    tech: `task ${t.id} · exit ${t.exitCode ?? '–'}\n${t.argv.length ? t.argv.join(' ') : '(not started yet)'}`,
    pauseId: t.id,
  };
}

export function extVM(e: ExtRun, drives: Drive[], now: number, driveFiles: Record<string, number> = {}): TaskVM {
  const words = e.words || [];
  const j = e.job;
  const fromFile = e.source === 'progress_file';
  const progress = e.progress || (j && j.current != null ? { current: j.current, total: j.total || 0 } : null);
  const start = Date.parse(fromFile ? e.phase_started_at || e.started_at : j?.created_at || e.started_at) || null;
  const msg = splitMessage(e.progress?.message);
  let note: TaskVM['note'] = null;
  if (fromFile && e.warning) note = { text: `${e.warnings} warning(s), last: ${e.warning}`, warn: true };
  if (!fromFile && j) {
    if (!j.last_write_at) {
      note = { text: j.kind === 'index_volume' ? 'No files recorded yet. It is probably still walking the drive.' : 'No catalog writes yet.', warn: false };
    } else {
      const quiet = (now - Date.parse(j.last_write_at)) / 1000;
      note =
        quiet > QUIET_WARN_S
          ? { text: `No catalog writes for ${fmtElapsed(quiet)}. It may be stuck on a slow or failing drive.`, warn: true }
          : { text: `Last catalog write ${relTime(j.last_write_at, now)}.`, warn: false };
    }
  }
  return {
    key: `p${e.pid}`,
    title: taskTitle(['--json', ...words], drives),
    pace: isHash(words) && fromFile ? hashOverall(pace(progress, start, now), driveFiles[words[1] ?? '']) : pace(progress, start, now, fromFile ? 5 : 60),
    unit: j?.unit || unitFor(['--json', ...words]),
    stats: fromFile ? msg.stats : '',
    path: fromFile ? msg.path : '',
    note,
    keepAwake: false,
    origin: fromFile
      ? 'Started in a terminal.'
      : 'Started in a terminal. Counts come from the catalog; exact totals show for runs started after ui-11.',
    tech: `pid ${e.pid}${j ? ` · job ${j.job_id} (${j.kind})` : ''} · started ${e.started_at}\nphotovault ${words.join(' ')}`,
    pauseId: null,
  };
}

/**
 * A running task. `compact` (the sidebar) shows the title, the bar and one line; the rest is behind Details.
 * The full card (home page) shows the details inline.
 */
export function RunningCard({ vm, compact = false }: { vm: TaskVM; compact?: boolean }) {
  const { pauseTask, pausing } = useStore();
  const [open, setOpen] = useState(false);
  const [techOpen, setTechOpen] = useState(false);
  const p = vm.pace;
  const isPausing = vm.pauseId !== null && pausing.has(vm.pauseId);

  const details = (
    <div className="flex flex-col gap-1.5 text-xs text-muted">
      {longLine(p, vm.unit) && <p className="tabular">{longLine(p, vm.unit)}</p>}
      {vm.stats && <p className="tabular text-ink/80">{vm.stats}</p>}
      {vm.path && (
        <p className="truncate font-mono text-2xs text-faint" title={vm.path}>
          {vm.path}
        </p>
      )}
      {vm.note && <p className={vm.note.warn ? 'text-maybe' : ''}>{vm.note.text}</p>}
      {vm.keepAwake && (
        <p className="flex items-center gap-1.5 text-faint">
          <Icon name="moon" size={12} /> Keeping the Mac awake
        </p>
      )}
      {vm.origin && <p className="text-faint">{vm.origin}</p>}
      <Disclosure summary="Technical details" open={techOpen} onToggle={setTechOpen} summaryClass="text-2xs">
        <code className="block whitespace-pre-wrap break-all rounded-lg bg-bg/70 p-2 font-mono text-2xs text-faint">{vm.tech}</code>
      </Disclosure>
    </div>
  );

  return (
    <motion.article
      layout="position"
      className={`group relative rounded-xl border border-line bg-raised/50 ${compact ? 'p-3' : 'p-4'} hover:border-line-strong`}
    >
      <div className="flex items-start gap-2">
        <h3 className={`min-w-0 flex-1 font-medium leading-snug ${compact ? 'text-sm' : 'text-base'}`}>
          <span className="line-clamp-2" title={vm.title}>
            {vm.title}
          </span>
        </h3>
        {vm.pauseId !== null ? (
          <button
            type="button"
            onClick={() => pauseTask(vm.pauseId!)}
            disabled={isPausing}
            aria-label={isPausing ? 'Pausing' : `Pause: ${vm.title}`}
            title={isPausing ? 'Pausing after the current file…' : 'Pause'}
            className="flex h-7 shrink-0 items-center gap-1 rounded-full border border-line px-2 text-2xs text-muted hover:border-line-strong hover:text-ink disabled:opacity-50"
          >
            <Icon name="pause" size={11} />
            {!compact && (isPausing ? 'Pausing…' : 'Pause')}
          </button>
        ) : (
          <span className="chip border-line text-faint" title="Started in a terminal">
            Terminal
          </span>
        )}
      </div>
      <ProgressBar value={p.fraction} live tone="accent" size="sm" label={vm.title} className="mt-2.5" />
      <div className="mt-2 flex items-baseline justify-between gap-2 font-mono text-2xs text-muted">
        <span className="tabular">{shortLine(p)}</span>
        {p.perMin !== null && <span className="tabular text-faint">{fmtInt(p.perMin)}/min</span>}
      </div>
      {compact ? (
        <Disclosure summary="Details" open={open} onToggle={setOpen} className="mt-1.5" summaryClass="text-2xs">
          {details}
        </Disclosure>
      ) : (
        <div className="mt-3">{details}</div>
      )}
    </motion.article>
  );
}

/** A finished task: one row with its outcome. */
export function FinishedRow({ t, drives, when }: { t: TaskRec; drives: Drive[]; when?: string }) {
  const s = summarize(t);
  const icon = t.state === 'done' ? 'check' : t.state === 'paused' ? 'pause' : 'x';
  const tone = t.state === 'done' ? 'text-go bg-go/10' : t.state === 'paused' ? 'text-maybe bg-maybe/10' : 'text-err bg-err/10';
  return (
    <div className="flex gap-2.5 rounded-lg px-1 py-1.5">
      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${tone}`}>
        <Icon name={icon} size={11} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <p className="min-w-0 flex-1 truncate text-sm text-ink/90" title={taskTitle(t.argv, drives)}>
            {taskTitle(t.argv, drives)}
          </p>
          {when && <span className="shrink-0 text-xs text-muted">{when}</span>}
        </div>
        <p className={`line-clamp-2 text-xs ${s.problem ? 'text-err' : 'text-muted'}`} title={s.text}>
          {s.text}
        </p>
      </div>
    </div>
  );
}

export function TaskStatePill({ t }: { t: TaskRec }) {
  const label = t.final ? ({ done: 'Done', failed: 'Failed', paused: 'Paused' } as Record<string, string>)[t.state] || t.state : 'Running';
  return <StatePill state={t.final ? t.state : 'running'} text={label} />;
}
