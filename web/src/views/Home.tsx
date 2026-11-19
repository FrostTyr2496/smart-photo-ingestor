import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useState } from 'react';
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
  runMounts,
  taskTitle,
} from '../lib/labels';
import { groupRuns, followOf, type RunGroup, type RunLike } from '../lib/runGroups';
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
        {href ? (
          <a href={href} title={`Open ${name}`} className="min-w-0 flex-1 truncate font-semibold hover:underline">
            {name}
          </a>
        ) : (
          <h3 className="min-w-0 flex-1 truncate font-semibold">{name}</h3>
        )}
        {!href && d && !d.volume_id && (
          <a href={hrefFor({ view: 'setup', key: d.key })} className="btn-primary h-7 px-2.5 text-xs" aria-label={`Set up ${name}`}>
            Set up
          </a>
        )}
      </div>
      <p className="mt-1 text-xs text-muted">{devSub(d)}</p>
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
    </article>
  );
}

interface Entry extends RunLike {
  vm: TaskVM;
}

/** A drive named in a run's header: role, name, free space and an Open link. */
function DriveChip({ mount, drives }: { mount: string; drives: Drive[] }) {
  const d = drives.find((x) => x.mount && sameMount(x.mount, mount)) ?? null;
  const name = d ? driveName(d) : baseName(mount) || 'drive';
  const href = d && isCardDrive(d) ? hrefFor({ view: 'cards', id: d.volume_id }) : d && d.volume_id ? hrefFor({ view: 'drive', id: d.volume_id }) : null;
  const body = (
    <>
      <RolePill role={d?.role ?? null} isNew={!d} text={d ? undefined : 'Folder'} />
      <span className={`min-w-0 truncate text-sm font-semibold ${href ? 'group-hover/chip:underline' : ''}`}>{name}</span>
      {d?.mounted && d.free && <span className="shrink-0 text-xs text-muted">{d.free} free</span>}
    </>
  );
  // The chip itself opens the drive or card (rv-35): no separate Open button.
  return href ? (
    <a
      href={href}
      title={`Open ${name}`}
      className="group/chip -mx-1.5 flex min-w-0 items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-raised/70"
    >
      {body}
    </a>
  ) : (
    <div className="flex min-w-0 items-center gap-2">{body}</div>
  );
}

/** One run (rv-31): its drives once in the header, then the run's card with any follow-up copy beside it (rv-34). */
function RunGroupBlock({ group, drives, lit }: { group: RunGroup<Entry>; drives: Drive[]; lit: string | null }) {
  return (
    <article className="card p-4">
      {group.mounts.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          {group.mounts.map((m, i) => (
            <div key={m} className="flex min-w-0 items-center gap-2">
              {i > 0 && <span className="text-faint" aria-hidden>→</span>}
              <DriveChip mount={m} drives={drives} />
            </div>
          ))}
        </div>
      )}
      {group.subs.length > 0 ? (
        <div className="grid items-start gap-3 lg:grid-cols-2">
          <RunningCard vm={group.main.vm} highlight={lit === group.main.key} />
          {group.subs.map((s) => (
            <RunningCard key={s.key} vm={s.vm} badge="Then" highlight={lit === s.key} />
          ))}
        </div>
      ) : (
        <RunningCard vm={group.main.vm} highlight={lit === group.main.key} />
      )}
    </article>
  );
}

/** The home page (ui-14): what is running, one block per run (a follow-up copy nested under its import), then the idle drives. */
export function Home({ task }: { task?: string }) {
  const { drives, tasks, elsewhere, stale, recent, now, loaded, driveFiles } = useStore();
  // A link to one run (`#/?task=t12`, rv-37): scroll to its card and ring it for a few seconds.
  const [lit, setLit] = useState<string | null>(null);
  useEffect(() => {
    if (!task) return;
    let tries = 0;
    let timer = 0;
    const find = () => {
      const el = document.getElementById(`run-${task}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setLit(task);
        timer = window.setTimeout(() => setLit(null), 2500);
      } else if (tries++ < 20) timer = window.setTimeout(find, 100); // the page may still be entering or loading
    };
    timer = window.setTimeout(find, 150);
    return () => window.clearTimeout(timer);
  }, [task]);
  const [staleOpen, setStaleOpen] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);
  const [allRecent, setAllRecent] = useState(false);
  const { actions } = useActions();
  const urgent = badgeCount(actions, now || Date.now());

  const devs = new Map<string, Dev>();
  const devOf = (mount: string): Dev => {
    const d = drives.find((x) => x.mount && sameMount(x.mount, mount));
    const key = d ? d.key || d.volume_id || `mount:${d.mount}` : `path:${mount}`;
    if (!devs.has(key)) devs.set(key, { key, drive: d ?? null, mount, finished: [], stale: [] });
    return devs.get(key)!;
  };
  const entries: Entry[] = [];
  const finishedTasks: TaskRec[] = [];
  for (const t of tasks) {
    const words = argvWords(t.argv);
    const mounts = t.final ? taskMounts(words) : runMounts(t, drives);
    if (t.final) {
      finishedTasks.push(t);
      for (const m of mounts) devOf(m).finished.push(t);
      continue;
    }
    const jid = t.jobId ?? (t.result && typeof t.result.job_id === 'number' ? t.result.job_id : undefined);
    entries.push({ key: `t${t.id}`, mounts, jobId: jid, follow: followOf(words), vm: taskVM(t, drives, now, driveFiles) });
  }
  for (const e of elsewhere) {
    const words = e.words || [];
    entries.push({ key: `p${e.pid}`, mounts: taskMounts(words), jobId: e.job?.job_id, follow: followOf(words), vm: extVM(e, drives, now, driveFiles) });
  }
  const groups = groupRuns(entries);
  const busyKeys = new Set<string>();
  for (const g of groups) for (const m of g.mounts) busyKeys.add(devOf(m).key);
  const staleLeft: StaleJob[] = [];
  for (const j of stale) {
    const d = drives.find((x) => x.label && x.label === j.label);
    if (d && d.mount) devOf(d.mount).stale.push(j);
    else staleLeft.push(j);
  }

  for (const dev of devs.values()) if (busyKeys.has(dev.key)) staleLeft.push(...dev.stale);
  const used = busyKeys;
  const idle = drives
    .filter((d) => d.mounted && !d.ignored)
    .map((d) => devOf(d.mount))
    .filter((dev) => !used.has(dev.key));
  const nRunning = entries.length;
  // Live cards shown below; the empty card shows only when there are none, whatever kind of task runs.
  const anyRunning = nRunning > 0;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        <p className="mt-1 text-sm text-muted">
          {nRunning
            ? `${plural(nRunning, 'task')} running${busyKeys.size ? ` on ${plural(busyKeys.size, 'drive')}` : ''}${
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

      {/* Running: every live task, on a drive or not, from the UI or a terminal (rv-30). */}
      <section>
        <h2 className="label mb-2">Running{anyRunning ? ` (${nRunning})` : ''}</h2>
        {groups.length > 0 && (
          <div className="flex flex-col gap-4">
            <AnimatePresence initial={false}>
              {groups.map((g, i) => (
                <motion.div key={g.main.key} {...rise(i)}>
                  <RunGroupBlock group={g} drives={drives} lit={lit} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
        {!anyRunning && (
          <div className="card flex flex-col items-center gap-2 px-6 py-8 text-center">
            <p className="font-medium">{loaded ? 'Nothing is running.' : 'Loading…'}</p>
            {loaded && (
              <p className="max-w-md text-sm text-muted">
                Any task shows up here while it runs (imports, backups, card checks, indexing, hashing and the rest), whether you start it here or in a terminal.
              </p>
            )}
          </div>
        )}
      </section>

      {finishedTasks.length > 0 && (
        <Disclosure summary={`Finished this session (${finishedTasks.length})`} open={doneOpen} onToggle={setDoneOpen}>
          {finishedTasks.map((t) => (
            <FinishedRow key={t.id} t={t} drives={drives} />
          ))}
        </Disclosure>
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
