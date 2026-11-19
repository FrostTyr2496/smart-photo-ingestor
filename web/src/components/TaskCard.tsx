import { motion } from 'framer-motion';
import { useState } from 'react';
import { fmtElapsed, fmtInt, relTime } from '../lib/format';
import { argvWords, commandKey, fileState, mountName, runMounts, taskMounts, hashOverall, isHash, longLine, pace, shortLine, splitMessage, splitPath, statTiles, summarize, taskTitle, unitFor, type Pace } from '../lib/labels';
import { useStore } from '../lib/store';
import type { Drive, ExtRun, FileEvt, TaskRec } from '../lib/types';
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
  /** The command, as `commandKey` names it ("ingest", "backup", "card_clear"…). */
  kind: string;
  /** The last files the run handled, newest last (rv-34). */
  files: FileEvt[];
  /** The drives a copy goes between, by name, when known (rv-36). */
  route: { from: string; to: string } | null;
}

/** A copy's source and target names: from the command words, or a make-green run's reported route. */
function copyRoute(kind: string, mounts: string[], drives: Drive[]): TaskVM['route'] {
  if ((kind !== 'ingest' && kind !== 'backup') || mounts.length < 2) return null;
  const [from, to] = mounts.slice(-2);
  return { from: mountName(from, drives), to: mountName(to, drives) };
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
    kind: commandKey(words),
    files: t.files ?? [],
    route: copyRoute(commandKey(words), runMounts(t, drives, true), drives),
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
    kind: commandKey(words),
    files: e.files ?? [],
    route: copyRoute(commandKey(words), taskMounts(words), drives),
  };
}

const TONE_TEXT = { go: 'text-go', muted: 'text-muted', err: 'text-err' } as const;
const TONE_ICON = { go: 'check', muted: 'minus', err: 'x' } as const;

/** The newest file a run handled (or the file a hash is reading): its name large, its folders below (rv-34). */
function LatestFile({ vm }: { vm: TaskVM }) {
  const last = vm.files[vm.files.length - 1];
  const reading = !last && vm.path;
  if (!last && !reading) return null;
  const src = last ? last.src : vm.path;
  const { name, dir } = splitPath(src);
  const st = last ? fileState(last.state, vm.kind) : null;
  return (
    <div className="min-w-0 rounded-lg border border-line bg-bg/40 px-3 py-2.5">
      <div className="flex items-center gap-2 text-2xs uppercase tracking-wide text-faint">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
        {reading ? 'Reading' : 'Latest file'}
        {st && <span className={`ml-auto normal-case tracking-normal ${TONE_TEXT[st.tone]}`}>{st.label}</span>}
      </div>
      <p className="mt-1 truncate font-mono text-sm text-ink" title={src}>
        {name}
      </p>
      {vm.route && last && (
        <p className="truncate text-xs text-muted">
          {vm.route.from} → {vm.route.to}
        </p>
      )}
      <p className="truncate font-mono text-2xs text-faint" title={last?.dst ? `${src} → ${last.dst}` : src}>
        {dir || '/'}
        {/* A backup keeps the folder layout; name the target folder only when it differs. */}
        {last?.dst && splitPath(last.dst).dir !== dir && <> → {splitPath(last.dst).dir || '/'}</>}
      </p>
      {last?.error && <p className="mt-1 line-clamp-2 text-xs text-err">{last.error}</p>}
    </div>
  );
}

/** The files before the latest one, newest first. */
function RecentFiles({ vm }: { vm: TaskVM }) {
  const earlier = vm.files.slice(0, -1).reverse();
  if (!earlier.length) return null;
  return (
    <div className="min-w-0">
      <p className="mb-1 text-2xs uppercase tracking-wide text-faint">Before that</p>
      <ul className="flex flex-col">
        {earlier.map((f, i) => {
          const st = fileState(f.state, vm.kind);
          const { name, dir } = splitPath(f.src);
          return (
            <li key={`${f.src}-${i}`} className="flex min-w-0 items-center gap-2 py-0.5 text-xs" title={f.error ? `${f.src}: ${f.error}` : f.dst ? `${f.src} → ${f.dst}` : f.src}>
              <Icon name={TONE_ICON[st.tone]} size={11} className={`shrink-0 ${TONE_TEXT[st.tone]}`} />
              <span className="min-w-0 shrink truncate font-mono text-ink/80">{name}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-2xs text-faint">{dir}</span>
              <span className={`shrink-0 text-2xs ${TONE_TEXT[st.tone]}`}>{st.label}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Done / total, the message's stats, and elapsed time as tiles. */
function StatTiles({ vm }: { vm: TaskVM }) {
  const p = vm.pace;
  const tiles: { value: string; label: string; warn: boolean }[] = [];
  if (p.total) tiles.push({ value: `${fmtInt(p.current)} / ${fmtInt(p.total)}`, label: vm.unit, warn: false });
  else if (p.current) tiles.push({ value: fmtInt(p.current), label: vm.unit, warn: false });
  const fromMsg = statTiles(vm.stats);
  const prose = fromMsg.filter((t) => !t.value);
  tiles.push(...fromMsg.filter((t) => t.value));
  if (p.elapsedS) tiles.push({ value: fmtElapsed(p.elapsedS), label: 'elapsed', warn: false });
  if (!tiles.length && !prose.length) return null;
  return (
    <>
      {tiles.length > 0 && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tiles.map((t) => (
            <div key={t.label + t.value} className="min-w-0 rounded-lg border border-line bg-bg/40 px-2.5 py-1.5">
              <dd className={`truncate font-mono text-sm tabular ${t.warn ? 'text-err' : 'text-ink'}`}>{t.value}</dd>
              <dt className="truncate text-2xs text-muted">{t.label}</dt>
            </div>
          ))}
        </dl>
      )}
      {prose.map((t) => (
        <p key={t.label} className="text-xs text-muted">
          {t.label}
        </p>
      ))}
    </>
  );
}

/**
 * A running task. `compact` (the sidebar, a card's page) shows the title, the bar, the latest file name and one line;
 * the rest is behind Details. The full card (Tasks page, rv-34) shows stat tiles, the latest file and the ones before.
 * `badge` labels the card in its header (a follow-up copy's "Then").
 */
export function RunningCard({
  vm,
  compact = false,
  badge,
  href,
  highlight = false,
}: {
  vm: TaskVM;
  compact?: boolean;
  badge?: string;
  /** Clicking the card (not its buttons or disclosures) goes here: the run on the Tasks page (rv-37). */
  href?: string;
  /** Briefly ringed when a link lands on it. */
  highlight?: boolean;
}) {
  const { pauseTask, pausing } = useStore();
  const [open, setOpen] = useState(false);
  const [techOpen, setTechOpen] = useState(false);
  const p = vm.pace;
  const isPausing = vm.pauseId !== null && pausing.has(vm.pauseId);
  const last = vm.files[vm.files.length - 1];

  const footer = (
    <>
      {vm.note && <p className={`text-xs ${vm.note.warn ? 'text-maybe' : 'text-muted'}`}>{vm.note.text}</p>}
      {vm.keepAwake && (
        <p className="flex items-center gap-1.5 text-xs text-faint">
          <Icon name="moon" size={12} /> Keeping the Mac awake
        </p>
      )}
      {vm.origin && <p className="text-xs text-faint">{vm.origin}</p>}
      <Disclosure summary="Technical details" open={techOpen} onToggle={setTechOpen} summaryClass="text-2xs">
        <code className="block whitespace-pre-wrap break-all rounded-lg bg-bg/70 p-2 font-mono text-2xs text-faint">{vm.tech}</code>
      </Disclosure>
    </>
  );

  const compactDetails = (
    <div className="flex flex-col gap-1.5 text-xs text-muted">
      {longLine(p, vm.unit) && <p className="tabular">{longLine(p, vm.unit)}</p>}
      {vm.stats && <p className="tabular text-ink/80">{vm.stats}</p>}
      {vm.path && (
        <p className="truncate font-mono text-2xs text-faint" title={vm.path}>
          {vm.path}
        </p>
      )}
      {footer}
    </div>
  );

  return (
    <motion.article
      layout="position"
      id={`run-${vm.key}`}
      onClick={
        href
          ? (e) => {
              // Buttons, links and disclosures inside the card keep their own click.
              if ((e.target as HTMLElement).closest('button, a, summary, details, code, [role="button"]')) return;
              window.location.hash = href;
            }
          : undefined
      }
      title={href ? 'Open in Tasks' : undefined}
      className={`group relative flex min-w-0 flex-col rounded-xl border bg-raised/50 ${compact ? 'p-3' : 'p-4'} transition-[border-color,box-shadow] duration-500 ${
        highlight ? 'border-accent/70 shadow-[0_0_0_3px_rgb(var(--c-accent)/0.18)]' : 'border-line hover:border-line-strong'
      } ${href ? 'cursor-pointer hover:bg-raised/70' : ''}`}
    >
      <div className="flex items-start gap-2">
        <h3 className={`min-w-0 flex-1 font-medium leading-snug ${compact ? 'text-sm' : 'text-base'}`}>
          {badge && <span className="chip mb-1 mr-2 inline-flex border-line align-middle text-2xs uppercase tracking-wide text-muted">{badge}</span>}
          {href ? (
            <a href={href} className="line-clamp-2 hover:underline" title={`${vm.title}: open in Tasks`}>
              {vm.title}
            </a>
          ) : (
            <span className="line-clamp-2" title={vm.title}>
              {vm.title}
            </span>
          )}
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
        <>
          {last && (
            <p className="mt-1 truncate font-mono text-2xs text-faint" title={last.dst ? `${last.src} → ${last.dst}` : last.src}>
              {splitPath(last.src).name}
            </p>
          )}
          <Disclosure summary="Details" open={open} onToggle={setOpen} className="mt-1.5" summaryClass="text-2xs">
            {compactDetails}
          </Disclosure>
        </>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          <StatTiles vm={vm} />
          <LatestFile vm={vm} />
          <RecentFiles vm={vm} />
          {vm.files.length > 0 && vm.path && (
            <p className="truncate font-mono text-2xs text-faint" title={vm.path}>
              {vm.path}
            </p>
          )}
          <div className="flex flex-col gap-1.5">{footer}</div>
        </div>
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
