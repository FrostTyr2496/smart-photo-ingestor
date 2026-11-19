import { AnimatePresence, motion } from 'framer-motion';
import { useState } from 'react';
import { Disclosure, RolePill } from '../components/bits';
import { extVM, FinishedRow, RunningCard, taskVM, type TaskVM } from '../components/TaskCard';
import { plural, relTime } from '../lib/format';
import {
  argvWords,
  driveName,
  driveStatus,
  isCardDrive,
  JOB_WORD,
  liveStep,
  baseName,
  sameMount,
  staleText,
  summarize,
  taskMounts,
  recentTask,
  taskTitle,
} from '../lib/labels';
import { badgeCount } from '../lib/actions';
import { rise } from '../lib/motion';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import { useActions } from '../lib/useActions';
import type { Drive, StaleJob, TaskRec } from '../lib/types';

const RECENT_SHOWN = 8; // finished runs listed before "Show all" (rv-30)

interface Dev {
  key: string;
  drive: Drive | null;
  mount?: string;
  running: TaskVM[];
  finished: TaskRec[];
  stale: StaleJob[];
}

function devSub(d: Drive | null): string {
  if (!d) return 'Not in the drive list';
  if (!d.mounted) return `Not connected${d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : ''}`;
  if (d.local) return `${d.mount} on this Mac · ${d.free ?? '?'} free`;
  return `${d.fs || 'Unknown format'} · ${d.free ?? '?'} free of ${d.size || '?'}`;
}

function DeviceBlock({ dev, idle }: { dev: Dev; idle: boolean }) {
  const { drives, tasks, elsewhere } = useStore();
  const [recentOpen, setRecentOpen] = useState(false);
  const [staleOpen, setStaleOpen] = useState(false);
  const d = dev.drive;
  const name = d ? driveName(d) : baseName(dev.mount) || 'drive';
  const href = d && isCardDrive(d) ? hrefFor({ view: 'cards', id: d.volume_id }) : d && d.volume_id ? hrefFor({ view: 'drive', id: d.volume_id }) : null;
  const last = dev.finished[0];
  const s = last ? summarize(last) : null;

  return (
    <article className={`card ${idle ? 'p-3.5' : 'p-4'}`}>
      <div className="flex items-center gap-2.5">
        <RolePill role={d?.role ?? null} isNew={!d} text={d ? undefined : 'Folder'} />
        <h3 className="min-w-0 flex-1 truncate font-semibold">{name}</h3>
        {!href && d && !d.volume_id && (
          <a href={hrefFor({ view: 'setup', key: d.key })} className="btn-primary h-7 px-2.5 text-xs" aria-label={`Set up ${name}`}>
            Set up
          </a>
        )}
        {href && (
          <a href={href} className="btn h-7 px-2.5 text-xs" aria-label={`Open ${name}`}>
            Open
          </a>
        )}
      </div>
      <p className="mt-1 text-xs text-muted">{devSub(d)}</p>
      {dev.running.length > 0 && (
        <div className={`mt-3 grid gap-3 ${dev.running.length > 1 ? 'xl:grid-cols-2' : ''}`}>
          {dev.running.map((vm) => (
            <RunningCard key={vm.key} vm={vm} />
          ))}
        </div>
      )}
      {dev.stale.length > 0 && (
        <Disclosure
          summary={
            <span className="text-maybe/90">
              {dev.stale.length === 1 ? '1 job is' : `${dev.stale.length} jobs are`} marked running, but no process is doing {dev.stale.length === 1 ? 'it' : 'them'}
            </span>
          }
          open={staleOpen}
          onToggle={setStaleOpen}
          className="mt-3"
        >
          <ul className="flex flex-col gap-1">
            {dev.stale.map((j) => (
              <li key={j.job_id} className="text-xs text-muted">
                Job {j.job_id} · {JOB_WORD[j.kind] || j.kind} · started {relTime(j.created_at) || j.created_at}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {idle && (
        <p className={`mt-2 text-xs ${s?.problem ? 'text-err' : 'text-ink/70'}`}>
          {last ? `${taskTitle(last.argv, drives)}: ${s!.text}` : d && d.volume_id ? driveStatus(d, liveStep(d, tasks, elsewhere)).text : 'Nothing has run here yet.'}
        </p>
      )}
      {!idle && dev.finished.length > 0 && (
        <Disclosure summary={`Finished here (${Math.min(3, dev.finished.length)})`} open={recentOpen} onToggle={setRecentOpen} className="mt-3">
          {dev.finished.slice(0, 3).map((t) => (
            <FinishedRow key={t.id} t={t} drives={drives} />
          ))}
        </Disclosure>
      )}
    </article>
  );
}

/** The home page (ui-14): what is running, grouped by the drive it runs on, then the idle drives. */
export function Home() {
  const { drives, tasks, elsewhere, stale, recent, now, loaded, driveFiles } = useStore();
  const [staleOpen, setStaleOpen] = useState(false);
  const [allRecent, setAllRecent] = useState(false);
  const { actions } = useActions();
  const urgent = badgeCount(actions, now || Date.now());

  const devs = new Map<string, Dev>();
  const devOf = (mount: string): Dev => {
    const d = drives.find((x) => x.mount && sameMount(x.mount, mount));
    const key = d ? d.key || d.volume_id || `mount:${d.mount}` : `path:${mount}`;
    if (!devs.has(key)) devs.set(key, { key, drive: d ?? null, mount, running: [], finished: [], stale: [] });
    return devs.get(key)!;
  };
  const other: TaskVM[] = [];
  for (const t of tasks) {
    const mounts = taskMounts(argvWords(t.argv));
    if (!mounts.length) {
      if (!t.final) other.push(taskVM(t, drives, now, driveFiles));
      continue;
    }
    for (const m of mounts) {
      const dev = devOf(m);
      if (t.final) dev.finished.push(t);
      else dev.running.push(taskVM(t, drives, now, driveFiles));
    }
  }
  for (const e of elsewhere) {
    const mounts = taskMounts(e.words || []);
    if (!mounts.length) {
      other.push(extVM(e, drives, now, driveFiles));
      continue;
    }
    for (const m of mounts) devOf(m).running.push(extVM(e, drives, now, driveFiles));
  }
  const staleLeft: StaleJob[] = [];
  for (const j of stale) {
    const d = drives.find((x) => x.label && x.label === j.label);
    if (d && d.mount) devOf(d.mount).stale.push(j);
    else staleLeft.push(j);
  }

  const busy = [...devs.values()].filter((x) => x.running.length);
  const used = new Set(busy.map((x) => x.key));
  const idle = drives
    .filter((d) => d.mounted && !d.ignored)
    .map((d) => devOf(d.mount))
    .filter((dev) => !used.has(dev.key));
  const nRunning = tasks.filter((t) => !t.final).length + elsewhere.length;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        <p className="mt-1 text-sm text-muted">
          {nRunning
            ? `${plural(nRunning, 'task')} running${busy.length ? ` on ${plural(busy.length, 'drive')}` : ''}${
                elsewhere.length ? ` · ${elsewhere.length} started in a terminal` : ''
              }`
            : loaded
              ? 'Nothing is running'
              : 'Loading…'}
        </p>
      </div>

      {urgent > 0 && (
        <a href="#/actions" className="-mt-3 text-sm font-medium text-maybe hover:underline">
          {urgent === 1 ? '1 action needs' : `${urgent} actions need`} attention →
        </a>
      )}

      {busy.length > 0 && (
        <div className="flex flex-col gap-4">
          <AnimatePresence initial={false}>
            {busy.map((dev, i) => (
              <motion.div key={dev.key} {...rise(i)}>
                <DeviceBlock dev={dev} idle={false} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
      {loaded && !busy.length && (
        <div className="card flex flex-col items-center gap-2 px-6 py-10 text-center">
          <p className="font-medium">Nothing is running.</p>
          <p className="max-w-md text-sm text-muted">Open a drive to index or hash it. Runs started in a terminal show up here too.</p>
        </div>
      )}

      {other.length > 0 && (
        <section>
          <h2 className="label mb-2">Other tasks</h2>
          <div className="grid gap-3 xl:grid-cols-2">
            {other.map((vm) => (
              <RunningCard key={vm.key} vm={vm} />
            ))}
          </div>
        </section>
      )}

      {idle.length > 0 && (
        <section>
          <h2 className="label mb-2">Idle drives ({idle.length})</h2>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {idle.map((dev, i) => (
              <motion.div key={dev.key} {...rise(i)}>
                <DeviceBlock dev={dev} idle />
              </motion.div>
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section>
          <h2 className="label mb-2">Recently run</h2>
          <div className="card flex flex-col p-2">
            {(allRecent ? recent : recent.slice(0, RECENT_SHOWN)).map((r, i) => (
              <FinishedRow
                key={`${r.pid}-${r.finished_at}`}
                t={recentTask(r, i)}
                drives={drives}
                when={relTime(r.finished_at, now || Date.now()) || r.finished_at}
              />
            ))}
            {recent.length > RECENT_SHOWN && (
              <button type="button" className="btn mt-1 h-7 self-start px-2.5 text-xs" onClick={() => setAllRecent((v) => !v)}>
                {allRecent ? 'Show fewer' : `Show all (${recent.length})`}
              </button>
            )}
          </div>
        </section>
      )}

      {staleLeft.length > 0 && (
        <Disclosure summary={`Marked running, but stopped (${staleLeft.length})`} open={staleOpen} onToggle={setStaleOpen}>
          <ul className="flex flex-col gap-1">
            {staleLeft.map((j) => (
              <li key={j.job_id} className="text-xs text-muted">
                {staleText(j)}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
    </div>
  );
}
