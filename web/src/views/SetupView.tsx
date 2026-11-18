import { motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { Icon } from '../components/bits';
import { ProgressBar } from '../components/ProgressBar';
import { fmtElapsed } from '../lib/format';
import {
  argvWords,
  driveName,
  flagValue,
  newestAssignTask,
  ROLE_WORD,
  setupProblem,
  STEP_HINT,
  STEP_NAME,
  type StepId,
} from '../lib/labels';
import { springs } from '../lib/motion';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import { AssignDialog, useSetupIntent, type AssignPreset } from './AssignDialog';

const DOT = {
  done: 'border-go bg-go text-bg',
  running: 'border-accent bg-accent text-on-accent ring-pulse',
  warn: 'border-maybe bg-maybe/15 text-maybe',
  queued: 'border-line-strong bg-raised text-muted',
  never: 'border-line-strong bg-bg text-faint',
} as const;

/** The page for a drive that is being set up (or about to be): the setup step, then the workflow that follows. */
export function SetupView({ driveKey }: { driveKey: string }) {
  const { drives, tasks, now, runWorkflow, loaded } = useStore();
  const drive = drives.find((d) => d.key === driveKey) ?? null;
  const intent = useSetupIntent(driveKey);
  const [dialog, setDialog] = useState<AssignPreset | null>(null);
  const [opened, setOpened] = useState(false);
  const started = useRef(false);

  const mine = intent?.taskId != null ? tasks.find((t) => t.id === intent.taskId) ?? null : null;
  const waiting = intent?.taskId != null && !mine; // just started: the task list has not caught up yet
  const task = mine ?? (waiting ? null : drive ? newestAssignTask(tasks, drive.mount) : null);
  const state = task ? task.state : intent ? 'running' : null;
  const failed = state === 'failed' || state === 'paused';
  const done = state === 'done';

  // Once set up, open the drive and (if asked) start the workflow once.
  const doneDrive = done && drive ? drives.find((d) => d.mount === drive.mount && d.volume_id) ?? null : null;
  useEffect(() => {
    if (!doneDrive || started.current) return;
    started.current = true;
    if (intent?.runAfter) void runWorkflow(doneDrive);
    window.location.hash = hrefFor({ view: 'drive', id: doneDrive.volume_id! });
  }, [doneDrive?.volume_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // No task and no choice yet: this is the entry point for a new drive, so ask right away.
  useEffect(() => {
    if (drive && !task && !intent && !opened && !drive.volume_id) {
      setOpened(true);
      setDialog({});
    }
  }, [drive?.key, Boolean(task), Boolean(intent)]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!drive) {
    return (
      <div className="card px-6 py-10 text-center">
        <p className="font-medium">{loaded ? 'This drive is not connected.' : 'Loading…'}</p>
        <a href={hrefFor({ view: 'home' })} className="btn mt-4 inline-flex">
          Back to tasks
        </a>
      </div>
    );
  }

  const role = intent?.role || (task ? flagValue(argvWords(task.argv), '--role') : '') || '';
  const roleWord = ROLE_WORD[role] || 'a drive';
  const label = intent?.label ?? '';
  const steps: StepId[] = ['index', 'hash', 'scan', ...(role === 'WORKING' ? (['sidecars'] as StepId[]) : [])];
  const after = intent?.runAfter ? 'Starts after setup' : 'Run it after setup';
  const tone = failed ? 'warn' : done ? 'done' : 'running';
  const problem = failed && task ? setupProblem(task, drive.fs) : null;
  const elapsed = task?.startMs ? fmtElapsed(Math.max(0, (now - task.startMs) / 1000)) : '';

  return (
    <div className="flex flex-col gap-5">
      <section className="card p-5">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h1 className="text-2xl font-semibold tracking-tight">{driveName(drive)}</h1>
          <span className={`text-sm ${failed ? 'text-maybe' : 'text-accent'}`}>{failed ? 'Setup failed' : `Setting up as ${roleWord}`}</span>
        </div>
        <p className="mt-1 text-xs text-muted">
          {drive.local ? `${drive.mount} on this Mac` : [drive.fs || 'Unknown format', drive.size && `${drive.size} total`, drive.free && `${drive.free} free`].filter(Boolean).join(' · ')}
        </p>

        <ol className="mt-6 grid gap-6 md:gap-4" style={{ gridTemplateColumns: `repeat(${steps.length + 1}, minmax(0, 1fr))` }}>
          <li className="relative min-w-0">
            <div className="flex items-center">
              <span className={`relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold ${DOT[tone]}`}>
                {done ? <Icon name="check" size={14} /> : failed ? '!' : 1}
              </span>
              <div className="mx-2 h-[3px] flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                <motion.div className="h-full rounded-full bg-go" initial={false} animate={{ width: done ? '100%' : '0%' }} transition={springs.glide} />
              </div>
            </div>
            <h3 className="mt-3 font-semibold">Set up</h3>
            <p className={`text-sm ${failed ? 'text-maybe' : done ? 'text-muted' : 'font-medium text-accent'}`}>{failed ? 'Failed' : done ? 'Done' : 'Running now'}</p>
            <p className="text-xs text-faint">{`Add it to the catalog as ${roleWord}`}</p>
            {!failed && !done && (
              <div className="mt-2.5 pr-4">
                <ProgressBar value={null} live label="Set up" />
              </div>
            )}
          </li>
          {steps.map((id, i) => (
            <li key={id} className="relative min-w-0">
              <div className="flex items-center">
                <span className={`relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold ${failed ? DOT.never : DOT.queued}`}>
                  {i + 2}
                </span>
                {i < steps.length - 1 && <div className="mx-2 h-[3px] flex-1 rounded-full bg-ink/[0.08]" />}
              </div>
              <h3 className="mt-3 font-semibold">{STEP_NAME[id]}</h3>
              <p className="text-sm text-faint">{failed ? 'Not started' : after}</p>
              <p className="text-xs text-faint">{STEP_HINT[id]}</p>
            </li>
          ))}
        </ol>
      </section>

      {problem && task ? (
        <section className="card p-5">
          <h2 className="font-semibold">{`Could not set up ${driveName(drive)} as ${roleWord}`}</h2>
          <div className="mt-2 flex flex-col gap-2 text-sm text-muted">
            {problem.lines.map((line, i) => (
              <p key={i}>{line}</p>
            ))}
            <p>
              <code className="break-words rounded bg-ink/[0.06] px-1.5 py-0.5 font-mono text-xs">{problem.msg}</code>
            </p>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {problem.readOnly ? (
              <button type="button" className="btn-primary" onClick={() => setDialog({ role: 'LEGACY', label })}>
                Set up as legacy drive…
              </button>
            ) : (
              <button type="button" className="btn-primary" onClick={() => setDialog({ role, label })}>
                Try again…
              </button>
            )}
            <a href={hrefFor({ view: 'home' })} className="btn">
              Back to tasks
            </a>
          </div>
        </section>
      ) : (
        <section className="card p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm" role="status">
            <span className="live-dot h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
            <span className="text-muted">{done ? 'Set up. Opening the drive…' : "Writing the drive's ID file and adding it to the catalog…"}</span>
            {!done && elapsed && <span className="font-mono text-2xs text-faint">{elapsed} elapsed</span>}
          </div>
          <p className="mt-2 text-xs text-faint">
            {intent?.runAfter
              ? 'When setup finishes, this page shows the drive and the workflow starts.'
              : 'When setup finishes, this page shows the drive. Press Run workflow there to index and hash it.'}
          </p>
          <a href={hrefFor({ view: 'home' })} className="btn mt-4 inline-flex">
            Back to tasks
          </a>
        </section>
      )}

      <AssignDialog drive={dialog ? drive : null} preset={dialog ?? undefined} onClose={() => setDialog(null)} />
    </div>
  );
}
