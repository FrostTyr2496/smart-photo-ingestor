import { motion, useReducedMotion } from 'framer-motion';
import { useCallback, useEffect, useState } from 'react';
import { CountUp, Disclosure, Icon, RolePill } from '../components/bits';
import { ProgressBar } from '../components/ProgressBar';
import { api } from '../lib/api';
import { fmtBytes, fmtInt, plural } from '../lib/format';
import {
  driveName,
  liveStep,
  pace,
  roleKey,
  runDetail,
  runWhen,
  STEP_HINT,
  STEP_NAME,
  stepView,
  workflowSteps,
  type StepTone,
  type WorkflowStep,
} from '../lib/labels';
import { rise, springs } from '../lib/motion';
import { useStore } from '../lib/store';
import type { Drive, DriveSummary } from '../lib/types';

const TILES = [
  { key: 'indexed', label: 'Not hashed yet', tone: 'text-accent', ring: 'hover:border-accent/30' },
  { key: 'verified', label: 'Hashed', tone: 'text-go', ring: 'hover:border-go/30' },
  { key: 'mismatch', label: 'Unreadable', tone: 'text-err', ring: 'hover:border-err/30' },
  { key: 'missing', label: 'Missing', tone: 'text-maybe', ring: 'hover:border-maybe/30' },
] as const;

// ---------------------------------------------------------------- pieces

function Ring({ value, size = 44 }: { value: number; size?: number }) {
  const reduce = useReducedMotion();
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--c-ink) / 0.08)" strokeWidth={5} />
      <motion.circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="rgb(var(--c-go))"
        strokeWidth={5}
        strokeLinecap="round"
        strokeDasharray={c}
        initial={{ strokeDashoffset: c }}
        animate={{ strokeDashoffset: c * (1 - Math.max(0, Math.min(1, value))) }}
        transition={reduce ? { duration: 0 } : springs.glide}
      />
    </svg>
  );
}

const DOT: Record<StepTone, string> = {
  done: 'border-go bg-go text-bg',
  running: 'border-accent bg-accent text-on-accent ring-pulse',
  warn: 'border-maybe bg-maybe/15 text-maybe',
  queued: 'border-line-strong bg-raised text-muted',
  never: 'border-line-strong bg-bg text-faint',
};
const WHEN: Record<StepTone, string> = {
  done: 'text-muted',
  running: 'text-accent font-medium',
  warn: 'text-maybe',
  queued: 'text-faint',
  never: 'text-faint',
};

function Timeline({ d, steps, summary }: { d: Drive; steps: WorkflowStep[]; summary: DriveSummary }) {
  const { tasks, elsewhere, workflow, now, runStep, busy } = useStore();
  const live = liveStep(d, tasks, elsewhere);
  const wf = workflow && workflow.volumeId === d.volume_id && !workflow.outcome ? workflow : null;
  const isBusy = busy(d) || Boolean(live);
  const liveProgress = live?.task
    ? pace(live.task.progress, live.task.startMs, now)
    : live?.ext
      ? pace(live.ext.progress || (live.ext.job ? { current: live.ext.job.current ?? 0, total: live.ext.job.total ?? 0 } : null), null, now)
      : null;

  const views = steps.map((s, i) => {
    const queued = Boolean(wf && i > wf.at);
    return { step: s, ...stepView(s.id, summary.steps[s.id], live?.step ?? null, queued) };
  });

  return (
    <ol className="grid gap-6 md:gap-4" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
      {views.map((v, i) => {
        const next = views[i + 1];
        // The line after a step is full once the step is done, and fills with its progress while it runs.
        const fill = v.tone === 'done' ? 1 : v.tone === 'running' ? (liveProgress?.fraction ?? 0) : 0;
        return (
          <li key={v.step.id} className="relative min-w-0">
            <div className="flex items-center">
              <span className={`relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold ${DOT[v.tone]}`}>
                {v.tone === 'done' ? <Icon name="check" size={14} /> : v.tone === 'warn' ? '!' : i + 1}
              </span>
              {next && (
                <div className="mx-2 h-[3px] flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                  <motion.div
                    className={`h-full rounded-full ${v.tone === 'running' ? 'bg-accent' : 'bg-go'}`}
                    initial={false}
                    animate={{ width: `${fill * 100}%` }}
                    transition={springs.glide}
                  />
                </div>
              )}
            </div>
            <h3 className="mt-3 font-semibold">{STEP_NAME[v.step.id]}</h3>
            <p className={`text-sm ${WHEN[v.tone]}`}>{v.when}</p>
            <p className="text-xs text-faint">{STEP_HINT[v.step.id]}</p>
            {v.tone === 'running' && liveProgress && (
              <div className="mt-2.5 pr-4">
                <ProgressBar value={liveProgress.fraction} live label={`${STEP_NAME[v.step.id]} progress`} />
                <p className="mt-1.5 font-mono text-2xs text-muted">
                  {liveProgress.total ? `${fmtInt(liveProgress.current)} of ${fmtInt(liveProgress.total)}` : fmtInt(liveProgress.current)}
                  {liveProgress.fraction !== null && <span className="text-faint"> · {Math.floor(liveProgress.fraction * 100)}%</span>}
                </p>
              </div>
            )}
            {v.tone !== 'running' && d.mounted && (
              <button
                type="button"
                className="btn-ghost mt-2 h-7 px-2 text-xs"
                disabled={isBusy}
                onClick={() => runStep(d, v.step)}
                title={isBusy ? 'Something is already running on this drive' : `Run only ${STEP_NAME[v.step.id].toLowerCase()} on ${driveName(d)}`}
              >
                <Icon name="play" size={10} /> Run
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Tile({ label, files, bytes, tone, className = '' }: { label: string; files: number; bytes: number; tone: string; className?: string }) {
  return (
    <motion.div whileHover={{ y: -2 }} transition={springs.snappy} className={`rounded-xl border border-line bg-raised/40 p-4 ${className}`}>
      <p className={`text-sm ${tone}`}>{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">
        <CountUp value={files} />
      </p>
      <p className="mt-0.5 font-mono text-2xs text-muted">{fmtBytes(bytes)}</p>
    </motion.div>
  );
}

// ---------------------------------------------------------------- the view

export function DriveView({ id }: { id: string }) {
  const { drives, tasks, elsewhere, workflow, epoch, runWorkflow, busy, toast } = useStore();
  const [summary, setSummary] = useState<DriveSummary | null>(null);
  const [error, setError] = useState('');
  const [runsOpen, setRunsOpen] = useState(false);
  const drive = drives.find((x) => x.volume_id === id) ?? summary?.drive ?? null;
  const live = drive ? liveStep(drive, tasks, elsewhere) : null;

  const load = useCallback(async () => {
    try {
      const s = await api<DriveSummary>('GET', `/api/volumes/${encodeURIComponent(id)}`);
      setSummary(s);
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
  }, [id]);

  useEffect(() => {
    setSummary(null);
  }, [id]);
  useEffect(() => {
    void load();
  }, [load, epoch]);
  // While something runs here, the counts move: reload every few seconds.
  useEffect(() => {
    if (!live) return undefined;
    const t = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(t);
  }, [live, load]);

  if (error && !summary) {
    return (
      <div className="card p-6">
        <p className="text-err">{error}</p>
        <a href="#/" className="btn mt-4">
          Back to tasks
        </a>
      </div>
    );
  }
  if (!summary || !drive) {
    return (
      <div className="card flex flex-col gap-4 p-5">
        <div className="h-7 w-48 animate-pulse rounded-lg bg-raised" />
        <div className="h-24 animate-pulse rounded-xl bg-raised/60" />
        <div className="grid grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-raised/50" />
          ))}
        </div>
      </div>
    );
  }

  const s = summary;
  const steps = workflowSteps(drive);
  const hashed = Object.values(s.states).reduce((n, x) => n + x.hashed, 0);
  const frac = s.files ? hashed / s.files : 0;
  const wfHere = workflow && workflow.volumeId === drive.volume_id;
  const isBusy = busy(drive) || Boolean(live);
  const copies = s.copies;
  const maxOther = copies ? Math.max(1, ...copies.by_volume.map((v) => v.files)) : 1;
  const reveal = () =>
    api('POST', `/api/volumes/${encodeURIComponent(id)}/reveal`, {}).catch((err: Error) => toast(err.message, 'err'));

  return (
    <motion.div key={id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={springs.snappy} className="flex flex-col gap-5">
      {/* Head */}
      <section className="card p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2.5">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{driveName(drive)}</h1>
              <RolePill role={drive.role} />
            </div>
            <p className="mt-1 flex items-center gap-2 text-sm text-muted">
              <span className={`h-2 w-2 rounded-full ${drive.mounted ? 'bg-go' : 'bg-skip'}`} />
              {drive.mounted ? 'Connected' : 'Not connected'}
              <span className="text-faint">·</span>
              <span className="font-mono text-xs">
                {[drive.fs, drive.size && `${drive.size} total`, drive.free && `${drive.free} free`].filter(Boolean).join(' · ')}
              </span>
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {steps.length > 0 && drive.mounted && isBusy && (
              <span className="inline-flex h-8 items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 text-sm text-accent">
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" />
                {wfHere ? 'Workflow running' : live ? `Running · ${STEP_NAME[live.step]}` : 'Busy'}
              </span>
            )}
            {steps.length > 0 && drive.mounted && !isBusy && (
              <button type="button" className="btn-primary" onClick={() => runWorkflow(drive)}>
                <Icon name="play" size={11} /> Run workflow
              </button>
            )}
            {drive.mounted && (
              <button type="button" className="btn" onClick={reveal}>
                <Icon name="folder" /> Show in Finder
              </button>
            )}
            <a href="#/" className="btn">
              <Icon name="back" /> Tasks
            </a>
          </div>
        </div>

        {steps.length > 0 && (
          <div className="mt-6 border-t border-line pt-6">
            <Timeline d={drive} steps={steps} summary={s} />
          </div>
        )}
      </section>

      {/* Files */}
      <section className="card p-5">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <p className="label">Files</p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">
              <CountUp value={s.files} />
            </p>
          </div>
          <div>
            <p className="label">Size</p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">{fmtBytes(s.bytes)}</p>
          </div>
          <div className="flex items-center gap-3">
            <Ring value={frac} />
            <div>
              <p className="label">Hashed</p>
              <p className="mt-1.5 text-2xl font-semibold tracking-tight">
                <CountUp value={frac * 100} format={(n) => `${Math.floor(n)}%`} />
              </p>
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap justify-end gap-1.5">
            {s.kinds.map((k) => (
              <span key={k.kind} className="chip border-line font-mono text-muted" title={fmtBytes(k.bytes)}>
                {k.kind} <span className="text-ink/80">{fmtInt(k.files)}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {TILES.map((t) => {
            const x = s.states[t.key];
            return <Tile key={t.key} label={t.label} files={x.files} bytes={x.bytes} tone={t.tone} className={t.ring} />;
          })}
        </div>
      </section>

      {/* Copies */}
      {copies && roleKey(drive.role) !== 'card' && (
        <section className="card p-5">
          <h2 className="text-lg font-semibold">Copies on other drives</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <Tile label="On other drives" files={copies.elsewhere.files} bytes={copies.elsewhere.bytes} tone="text-go" />
            <Tile label="Only on this drive" files={copies.only_here.files} bytes={copies.only_here.bytes} tone="text-maybe" />
            <motion.div whileHover={{ y: -2 }} transition={springs.snappy} className="rounded-xl border border-line bg-raised/40 p-4">
              <p className="text-sm text-accent">Duplicated on this drive</p>
              <p className="mt-1 text-2xl font-semibold tracking-tight">
                <CountUp value={copies.dup_here.assets} />
              </p>
              <p className="mt-0.5 font-mono text-2xs text-muted">{fmtBytes(copies.dup_here.extra_bytes)} in extra copies</p>
            </motion.div>
          </div>
          {copies.by_volume.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1.5">
              {copies.by_volume.map((v, i) => (
                <motion.li key={v.volume_id} {...rise(i)} className="flex items-center gap-3 rounded-xl border border-line bg-raised/30 px-3 py-2.5">
                  <RolePill role={v.role} />
                  <span className="w-28 shrink-0 truncate text-sm font-medium">{v.label}</span>
                  <span className={`shrink-0 text-xs ${v.mounted ? 'text-go' : 'text-faint'}`}>{v.mounted ? 'Connected' : 'Not connected'}</span>
                  <ProgressBar value={v.files / maxOther} size="xs" tone="muted" className="flex-1" label={`Share on ${v.label}`} />
                  <span className="shrink-0 font-mono text-2xs text-muted">
                    {plural(v.files, 'file')} · {fmtBytes(v.bytes)}
                  </span>
                </motion.li>
              ))}
            </ul>
          )}
          {s.states.indexed.files > 0 && (
            <p className="mt-3 text-xs text-faint">
              {fmtInt(s.states.indexed.files)} files not hashed yet, so not compared. Other drives count only once they are hashed too.
            </p>
          )}
        </section>
      )}

      {/* Runs */}
      {s.runs.length > 0 && (
        <section className="card p-5">
          <h2 className="text-lg font-semibold">Runs</h2>
          <ul className="mt-3 flex flex-col gap-1.5">
            {s.runs.slice(0, 4).map((r) => (
              <RunRow key={r.job_id} r={r} />
            ))}
          </ul>
          {s.runs.length > 4 && (
            <Disclosure summary={`Older runs (${s.runs.length - 4})`} open={runsOpen} onToggle={setRunsOpen} className="mt-2">
              <ul className="flex flex-col gap-1.5">
                {s.runs.slice(4).map((r) => (
                  <RunRow key={r.job_id} r={r} />
                ))}
              </ul>
            </Disclosure>
          )}
        </section>
      )}
    </motion.div>
  );
}

function RunRow({ r }: { r: DriveSummary['runs'][number] }) {
  const tone = r.state === 'done' ? 'bg-go' : r.state === 'running' ? 'bg-accent live-dot' : r.state === 'failed' ? 'bg-err' : 'bg-maybe';
  return (
    <li className="flex gap-3 rounded-xl border border-line bg-raised/30 px-3 py-2.5">
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone}`} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-sm">
          <span className="font-semibold">{r.label}</span> <span className="text-xs text-muted">{runWhen(r)}</span>
        </p>
        <p className="text-xs text-muted">{runDetail(r)}</p>
      </div>
    </li>
  );
}
