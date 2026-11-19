import { AnimatePresence, motion } from 'framer-motion';
import { useMemo, useState, type MouseEvent } from 'react';
import { Disclosure, FsPill, Icon, RolePill, ROLE_DOT } from '../components/bits';
import { RefsDialog } from './RefsDialog';
import { ProgressBar } from '../components/ProgressBar';
import { extVM, FinishedRow, RunningCard, taskVM } from '../components/TaskCard';
import { useDriveActivity } from '../lib/driveActivity';
import {
  facetOptions,
  filterCount,
  filterDrives,
  freshHeadline,
  NO_FILTER,
  SORTS,
  sortDrives,
  TYPE_LABEL,
  type DriveFilter,
  type SortKey,
  type TypeKey,
} from '../lib/driveList';
import { parseSize, relTime } from '../lib/format';
import { argvWords, driveIO, driveName, driveStatus, flagValue, isCardDrive, liveStep, newestAssignTask, ROLE_WORD, roleKey, staleText } from '../lib/labels';
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

/** A card opens in Cards; a new drive opens its setup page. */
const driveHref = (d: Drive): string =>
  isCardDrive(d) ? hrefFor({ view: 'cards', id: d.volume_id }) : d.volume_id ? hrefFor({ view: 'drive', id: d.volume_id }) : hrefFor({ view: 'setup', key: d.key });

function DriveRow({ d, active, fresh, onSeen }: { d: Drive; active: boolean; fresh: boolean; onSeen: () => void }) {
  const { tasks, elsewhere, setIgnored, drives } = useStore();
  const isNew = !d.volume_id && !isCardDrive(d);
  const live = d.mounted ? liveStep(d, tasks, elsewhere) : null;
  // Being read or written right now (rv-35): the row is shaded with a moving sheen, and says what is happening.
  const io = driveIO(d, tasks, elsewhere, drives);
  const writing = io.some((x) => x.mode === 'write');
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
    : io.length
      ? { text: io.map((x) => x.text).join(' · '), tone: 'running' as const }
      : driveStatus(d, live);
  const size = parseSize(d.size);
  const free = parseSize(d.free);
  const used = size && free !== null ? (size - free) / size : null;
  const key = isNew ? 'new' : roleKey(d.role);
  const href = driveHref(d);

  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      onClick={fresh ? onSeen : undefined}
      // A just-plugged-in drive glows until it is opened or used; the glow then fades out (rv-33).
      className={`group relative block rounded-xl border px-3 py-2.5 transition-[border-color,background-color,box-shadow] duration-700 ${
        fresh
          ? 'border-accent/60 bg-accent/[0.09] shadow-[0_0_0_3px_rgb(var(--c-accent)/0.14)]'
          : io.length
            ? `drive-busy ${writing ? 'drive-busy-write border-go/45' : 'border-accent/45'}`
            : active
            ? 'border-accent/40 bg-accent/[0.07]'
            : 'border-line bg-raised/30 hover:border-line-strong hover:bg-raised/60'
      } ${d.mounted ? '' : 'opacity-60 hover:opacity-90'} ${isNew && !fresh ? 'border-dashed' : ''}`}
    >
      {active && <motion.span layoutId="drive-active" transition={springs.snappy} className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-accent" />}
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${ROLE_DOT[key]} ${live || io.length ? 'live-dot' : ''}`} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={d.mount}>
          {driveName(d)}
        </span>
        <AnimatePresence initial={false}>
          {fresh && (
            <motion.button
              type="button"
              key="new"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.6 } }}
              title="Just plugged in. Open or use it, or click here, to clear this."
              className="chip shrink-0 border-accent/40 bg-accent/15 text-accent hover:bg-accent/25"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onSeen();
              }}
            >
              <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
              New
            </motion.button>
          )}
        </AnimatePresence>
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
      <p
        title={io.length ? status.text : undefined}
        className={`mt-1 truncate pl-4 text-xs ${io.length && writing ? 'text-go' : status.tone === 'running' ? 'text-accent' : status.tone === 'warn' ? 'text-maybe' : 'text-muted'}`}>
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

function Facet<T extends string>({ label, values, chosen, text, onToggle }: { label: string; values: T[]; chosen: T[]; text: (v: T) => string; onToggle: (v: T) => void }) {
  if (values.length < 2 && !chosen.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="w-12 shrink-0 text-2xs text-faint">{label}</span>
      {values.map((v) => {
        const on = chosen.includes(v);
        return (
          <button
            key={v}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(v)}
            className={`chip ${on ? 'border-accent/50 bg-accent/15 text-accent' : 'border-line text-muted hover:border-line-strong hover:text-ink'}`}
          >
            {text(v)}
          </button>
        );
      })}
    </div>
  );
}

/** Sort and filter controls for the drive list (rv-33). Each group (Mounted, Not connected, Ignored) sorts on its own. */
function DriveTools({
  sort,
  setSort,
  filter,
  setFilter,
  options,
  hidden,
}: {
  sort: SortKey;
  setSort: (s: SortKey) => void;
  filter: DriveFilter;
  setFilter: (f: DriveFilter) => void;
  options: { types: TypeKey[]; formats: string[] };
  hidden: number;
}) {
  const [open, setOpen] = usePref('pv:driveFilterOpen', false);
  const n = filterCount(filter);
  const toggle = <K extends keyof DriveFilter>(k: K, v: DriveFilter[K][number]) => {
    const list = filter[k] as string[];
    setFilter({ ...filter, [k]: list.includes(v) ? list.filter((x) => x !== v) : [...list, v] });
  };
  return (
    <div className="flex flex-col gap-1.5 px-1 pb-2">
      <div className="flex items-center gap-2">
        <label className="flex min-w-0 items-center gap-1.5 text-2xs text-faint">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className="h-7 min-w-0 text-xs text-ink" aria-label="Sort drives">
            {SORTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.text}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className={`chip ${n ? 'border-accent/50 bg-accent/10 text-accent' : 'border-line text-muted hover:text-ink'}`}
        >
          Filter{n ? ` · ${n}` : ''}
        </button>
        {n > 0 && (
          <button type="button" className="text-2xs text-muted hover:text-ink" onClick={() => setFilter(NO_FILTER)}>
            Clear
          </button>
        )}
        {hidden > 0 && <span className="ml-auto shrink-0 font-mono text-2xs text-faint">{hidden} hidden</span>}
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ ...springs.snappy, opacity: { duration: 0.2 } }}
            className="flex flex-col gap-1 overflow-hidden"
          >
            <Facet label="Type" values={options.types} chosen={filter.types} text={(v) => TYPE_LABEL[v]} onToggle={(v) => toggle('types', v)} />
            <Facet label="Format" values={options.formats} chosen={filter.formats} text={(v) => v} onToggle={(v) => toggle('formats', v)} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** "New device detected" above the list while any drive just plugged in has not been opened or used (rv-33). */
function FreshBanner({ list, onSeen }: { list: Drive[]; onSeen: (d: Drive) => void }) {
  return (
    <AnimatePresence initial={false}>
      {list.length > 0 && (
        <motion.div
          key="fresh"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0, transition: { duration: 0.6 } }}
          className="overflow-hidden"
        >
          <div className="mx-1 mb-2 flex items-start gap-2 rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-2 text-xs" role="status">
            <span className="live-dot mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-accent">{freshHeadline(list.length)}</p>
              <p className="truncate text-muted">
                {list.map((d, i) => (
                  <span key={d.key}>
                    {i > 0 && ', '}
                    <a href={driveHref(d)} className="text-ink hover:underline" onClick={() => onSeen(d)}>
                      {driveName(d)}
                    </a>
                  </span>
                ))}
              </p>
            </div>
            <button
              type="button"
              title="Dismiss"
              aria-label="Dismiss"
              className="shrink-0 rounded p-0.5 text-muted hover:text-ink"
              onClick={() => list.forEach(onSeen)}
            >
              <Icon name="x" size={12} />
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function DriveList({ route }: { route: Route }) {
  const { drives, loaded } = useStore();
  const { fresh, touched, touch } = useDriveActivity();
  const [folded, setFolded] = usePref<string[]>('pv:driveFold', ['ignored']);
  const [sort, setSort] = usePref<SortKey>('pv:driveSort', 'default');
  const [savedFilter, setFilter] = usePref<DriveFilter>('pv:driveFilter', NO_FILTER);
  const [refsOpen, setRefsOpen] = useState(false);
  const shown = drives.filter((d) => !d.ignored);
  const toggle = (k: string) => setFolded((f) => (f.includes(k) ? f.filter((x) => x !== k) : [...f, k]));
  const options = useMemo(() => facetOptions(drives), [drives]);
  // A saved choice that no drive has any more (say, the only NTFS drive was removed) would hide everything silently.
  const filter: DriveFilter = {
    types: (savedFilter.types || []).filter((t) => options.types.includes(t)),
    formats: (savedFilter.formats || []).filter((f) => options.formats.includes(f)),
  };
  const visible = filterDrives(drives, filter, fresh);
  const freshList = sortDrives(drives.filter((d) => d.mounted && fresh[d.key] !== undefined), 'default', { fresh, touched });

  return (
    <section className="card p-3" aria-label="Drives">
      <div className="flex items-center justify-between px-1 pb-2">
        <h2 className="label">Drives</h2>
        <span className="font-mono text-2xs text-faint">{shown.filter((d) => d.mounted).length} connected</span>
      </div>
      <FreshBanner list={freshList} onSeen={(d) => touch(d.key)} />
      {loaded && drives.length > 1 && (
        <DriveTools
          sort={sort}
          setSort={setSort}
          filter={filter}
          setFilter={setFilter}
          options={options}
          hidden={drives.length - visible.length}
        />
      )}
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
      {loaded && shown.length > 0 && !visible.length && (
        <p className="px-1 py-2 text-sm text-muted">
          No drive matches the filter.{' '}
          <button type="button" className="text-accent hover:underline" onClick={() => setFilter(NO_FILTER)}>
            Clear it
          </button>
        </p>
      )}
      <div className="flex flex-col gap-1">
        {GROUPS.map((g) => {
          const list = sortDrives(visible.filter(g.test), sort, { fresh, touched });
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
                          <DriveRow d={d} fresh={fresh[d.key] !== undefined} onSeen={() => touch(d.key)} active={
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
              <RunningCard vm={taskVM(t, drives, now, driveFiles)} compact href={hrefFor({ view: 'home', task: `t${t.id}` })} />
            </motion.div>
          ))}
          {elsewhere.map((e) => (
            <motion.div key={`p${e.pid}`} {...rise()} onClick={openTasks} className="cursor-pointer" title="Open Tasks">
              <RunningCard vm={extVM(e, drives, now, driveFiles)} compact href={hrefFor({ view: 'home', task: `p${e.pid}` })} />
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
