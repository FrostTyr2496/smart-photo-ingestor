import { AnimatePresence, motion } from 'framer-motion';
import { useState, type MouseEvent } from 'react';
import { Disclosure, FsPill, Icon, RolePill, ROLE_DOT } from '../components/bits';
import { RefsDialog } from './RefsDialog';
import { ProgressBar } from '../components/ProgressBar';
import { extVM, FinishedRow, RunningCard, taskVM } from '../components/TaskCard';
import { parseSize, relTime } from '../lib/format';
import { argvWords, driveName, driveStatus, flagValue, isCardDrive, liveStep, newestAssignTask, ROLE_WORD, roleKey, staleText } from '../lib/labels';
import { rise, springs } from '../lib/motion';
import { usePref } from '../lib/prefs';
import { hrefFor, type Route } from '../lib/route';
import { useStore } from '../lib/store';
import type { Drive } from '../lib/types';

const RECENT_FINISHED = 3;

const GROUPS = [
  { key: 'mounted', label: 'Mounted', test: (d: Drive) => !d.ignored && d.mounted },
  { key: 'unmounted', label: 'Not connected', test: (d: Drive) => !d.ignored && !d.mounted },
  { key: 'ignored', label: 'Ignored', test: (d: Drive) => d.ignored },
] as const;

function DriveRow({ d, active }: { d: Drive; active: boolean }) {
  const { tasks, elsewhere, setIgnored } = useStore();
  const isNew = !d.volume_id && !isCardDrive(d);
  const live = d.mounted ? liveStep(d, tasks, elsewhere) : null;
  const assign = isNew ? newestAssignTask(tasks, d.mount) : null;
  const assignRole = assign ? ROLE_WORD[flagValue(argvWords(assign.argv), '--role') ?? ''] || 'a drive' : '';
  const status = isNew
    ? assign && !assign.final
      ? { text: `Setting up as ${assignRole}…`, tone: 'running' as const }
      : assign && (assign.state === 'failed' || assign.state === 'paused')
        ? { text: 'Setup failed · click to see why', tone: 'warn' as const }
        : {
            text: d.local ? `The folder ${d.mount} on this Mac. Click to use it as a working drive.` : 'Not set up · click to set it up',
            tone: 'plain' as const,
          }
    : driveStatus(d, live);
  const size = parseSize(d.size);
  const free = parseSize(d.free);
  const used = size && free !== null ? (size - free) / size : null;
  const key = isNew ? 'new' : roleKey(d.role);
  // A card opens in Cards; a new drive opens its setup page.
  const href = isCardDrive(d) ? hrefFor({ view: 'cards', id: d.volume_id }) : d.volume_id ? hrefFor({ view: 'drive', id: d.volume_id }) : hrefFor({ view: 'setup', key: d.key });

  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      className={`group relative block rounded-xl border px-3 py-2.5 ${
        active ? 'border-accent/40 bg-accent/[0.07]' : 'border-line bg-raised/30 hover:border-line-strong hover:bg-raised/60'
      } ${d.mounted ? '' : 'opacity-60 hover:opacity-90'} ${isNew ? 'border-dashed' : ''}`}
    >
      {active && <motion.span layoutId="drive-active" transition={springs.snappy} className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-accent" />}
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${ROLE_DOT[key]} ${live ? 'live-dot' : ''}`} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={d.mount}>
          {driveName(d)}
        </span>
        <button
          type="button"
          title={d.ignored ? 'Show this drive in the list again' : 'Hide this drive under Ignored'}
          className="shrink-0 rounded px-1 text-2xs text-muted opacity-0 hover:text-ink focus:opacity-100 group-hover:opacity-100"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            void setIgnored(d, !d.ignored);
          }}
        >
          {d.ignored ? 'Unignore' : 'Ignore'}
        </button>
        <FsPill fs={d.fs} />
        <RolePill role={d.role} isNew={isNew} />
      </div>
      <p className={`mt-1 truncate pl-4 text-xs ${status.tone === 'running' ? 'text-accent' : status.tone === 'warn' ? 'text-maybe' : 'text-muted'}`}>
        {d.mounted ? status.text : `Not connected${d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : ''}`}
      </p>
      {d.mounted && used !== null && (
        <div className="mt-2 flex items-center gap-2 pl-4">
          {/* A full card is normal; a full drive is not. */}
          <ProgressBar
            value={used}
            size="xs"
            tone={key === 'card' ? 'muted' : used > 0.95 ? 'err' : used > 0.85 ? 'maybe' : 'muted'}
            label="Space used"
          />
          <span className="shrink-0 font-mono text-2xs text-faint">{d.free} free</span>
        </div>
      )}
    </a>
  );
}

function DriveList({ route }: { route: Route }) {
  const { drives, loaded } = useStore();
  const [folded, setFolded] = usePref<string[]>('pv:driveFold', ['ignored']);
  const [refsOpen, setRefsOpen] = useState(false);
  const shown = drives.filter((d) => !d.ignored);
  const toggle = (k: string) => setFolded((f) => (f.includes(k) ? f.filter((x) => x !== k) : [...f, k]));

  return (
    <section className="card p-3" aria-label="Drives">
      <div className="flex items-center justify-between px-1 pb-2">
        <h2 className="label">Drives</h2>
        <span className="font-mono text-2xs text-faint">{shown.filter((d) => d.mounted).length} connected</span>
      </div>
      {loaded && !shown.length && (
        <p className="px-1 py-2 text-sm text-muted">
          {drives.length ? 'Every drive is ignored. Open Ignored to bring one back.' : 'No drives yet. Plug one in, and it appears here.'}
        </p>
      )}
      {!loaded && (
        <div className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl bg-raised/50" />
          ))}
        </div>
      )}
      <div className="flex flex-col gap-1">
        {GROUPS.map((g) => {
          const list = drives.filter(g.test);
          if (!list.length) return null;
          const open = !folded.includes(g.key);
          return (
            <div key={g.key}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => toggle(g.key)}
                className="flex w-full items-center gap-1.5 rounded-md px-1 py-1.5 text-xs text-muted hover:text-ink"
              >
                <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex">
                  <Icon name="chevron" size={11} />
                </motion.span>
                {g.label}
                <span className="font-mono text-2xs text-faint">{list.length}</span>
              </button>
              <AnimatePresence initial={false}>
                {open && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ ...springs.snappy, opacity: { duration: 0.2 } }}
                    className="overflow-hidden"
                  >
                    <div className="flex flex-col gap-1.5 pb-2">
                      {list.map((d, i) => (
                        <motion.div key={d.key} {...rise(i)}>
                          <DriveRow d={d} active={
                              route.view === 'setup'
                                ? route.key === d.key
                                : (route.view === 'drive' || route.view === 'cards') && route.id !== null && route.id === d.volume_id
                            } />
                        </motion.div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
      <button type="button" className="text-xs text-accent hover:underline px-1 pt-2" onClick={() => setRefsOpen(true)}>
        Add GPS reference folder…
      </button>
      <RefsDialog open={refsOpen} onClose={() => setRefsOpen(false)} />
    </section>
  );
}

const TASKS_HREF = hrefFor({ view: 'home' });

/** A click anywhere on a sidebar task opens the Tasks page, except on its own controls (Pause, Details) or while selecting text. */
function openTasks(e: MouseEvent) {
  if ((e.target as HTMLElement).closest('button, a, input, code')) return;
  if (window.getSelection()?.toString()) return;
  window.location.hash = TASKS_HREF;
}

function TaskList() {
  const { tasks, elsewhere, stale, drives, now, driveFiles } = useStore();
  const [earlierOpen, setEarlierOpen] = useState(false);
  const [staleOpen, setStaleOpen] = useState(false);
  const running = tasks.filter((t) => !t.final);
  const finished = tasks.filter((t) => t.final);
  const nRunning = running.length + elsewhere.length;

  return (
    <section className="card p-3" aria-label="Tasks">
      <div className="flex items-center justify-between px-1 pb-2">
        <h2 className="label">
          <a href={TASKS_HREF} className="hover:text-ink">
            Tasks
          </a>
        </h2>
        {nRunning > 0 && (
          <span className="flex items-center gap-1.5 font-mono text-2xs text-accent">
            <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" />
            {nRunning} running
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2">
        <AnimatePresence initial={false}>
          {running.map((t) => (
            <motion.div key={`t${t.id}`} {...rise()} onClick={openTasks} className="cursor-pointer" title="Open Tasks">
              <RunningCard vm={taskVM(t, drives, now, driveFiles)} compact />
            </motion.div>
          ))}
          {elsewhere.map((e) => (
            <motion.div key={`p${e.pid}`} {...rise()} onClick={openTasks} className="cursor-pointer" title="Open Tasks">
              <RunningCard vm={extVM(e, drives, now, driveFiles)} compact />
            </motion.div>
          ))}
        </AnimatePresence>
        {!tasks.length && !elsewhere.length && <p className="px-1 py-1 text-sm text-muted">Nothing has run yet. Tasks you start appear here.</p>}
        {finished.length > 0 && (
          <div className="flex flex-col">
            {finished.slice(0, RECENT_FINISHED).map((t) => (
              <a key={t.id} href={TASKS_HREF} className="rounded-lg hover:bg-raised/60" title="Open Tasks">
                <FinishedRow t={t} drives={drives} />
              </a>
            ))}
          </div>
        )}
        {finished.length > RECENT_FINISHED && (
          <Disclosure summary={`Earlier (${finished.length - RECENT_FINISHED})`} open={earlierOpen} onToggle={setEarlierOpen} className="px-1">
            {finished.slice(RECENT_FINISHED).map((t) => (
              <a key={t.id} href={TASKS_HREF} className="block rounded-lg hover:bg-raised/60" title="Open Tasks">
                <FinishedRow t={t} drives={drives} />
              </a>
            ))}
          </Disclosure>
        )}
        {stale.length > 0 && (
          <Disclosure
            summary={
              <span className="flex items-center gap-1.5">
                Marked running, but stopped <span className="font-mono text-2xs text-faint">{stale.length}</span>
              </span>
            }
            open={staleOpen}
            onToggle={setStaleOpen}
            className="px-1"
          >
            <p className="pb-1.5 text-xs text-faint">A run ended without closing its job (for example the terminal was closed). Nothing is working on these.</p>
            <ul className="flex flex-col gap-1">
              {stale.map((j) => (
                <li key={j.job_id} className="text-xs text-muted">
                  {staleText(j)}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </div>
    </section>
  );
}

export function Sidebar({ route }: { route: Route }) {
  return (
    <aside className="flex flex-col gap-4 lg:sticky lg:top-[72px] lg:max-h-[calc(100vh-88px)] lg:overflow-y-auto lg:pb-4 no-scrollbar">
      <DriveList route={route} />
      <TaskList />
    </aside>
  );
}
