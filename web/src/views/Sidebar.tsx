import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { Disclosure, FsPill, Icon, RolePill, ROLE_DOT } from '../components/bits';
import { RefsDialog } from './RefsDialog';
import { PULLED_MS } from '../components/Unplugged';
import { openMenu } from '../lib/menu';
import { EjectButton, useEjectBlock } from '../components/Eject';
import { ProgressBar } from '../components/ProgressBar';
import { TIER, TIER_ORDER, TIER_TONE } from '../lib/tiers';
import type { CardInfo } from '../lib/types';
import { extVM, FinishedRow, RunningCard, taskVM } from '../components/TaskCard';
import { useDriveActivity } from '../lib/driveActivity';
import {
  driveHref,
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
/** A drive plugged in this recently plays its landing animation when its row appears (rv-38). */
const LANDED_MS = 10_000;

const GROUPS = [
  { key: 'mounted', label: 'Mounted', test: (d: Drive) => !d.ignored && d.mounted },
  { key: 'unmounted', label: 'Not connected', test: (d: Drive) => !d.ignored && !d.mounted },
  { key: 'ignored', label: 'Ignored', test: (d: Drive) => d.ignored },
] as const;


function DriveRow({
  d,
  active,
  fresh,
  landed = false,
  pulled = false,
  onSeen,
}: {
  d: Drive;
  active: boolean;
  fresh: boolean;
  /** Plugged in seconds ago: play the landing animation. */
  landed?: boolean;
  pulled?: boolean;
  onSeen: () => void;
}) {
  const { tasks, elsewhere, setIgnored, drives, ejectDrive, cards } = useStore();
  const card = d.volume_id ? cards.find((c) => c.volume_id === d.volume_id) : undefined;
  const isNew = !d.volume_id && !isCardDrive(d);
  const live = d.mounted ? liveStep(d, tasks, elsewhere) : null;
  // Being read or written right now (rv-35): the row is shaded with a moving sheen, and says what is happening.
  const io = driveIO(d, tasks, elsewhere, drives);
  const writing = io.some((x) => x.mode === 'write');
  const reading = io.some((x) => x.mode === 'read');
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
  const ejectBlock = useEjectBlock(d);
  const canEject = Boolean(d.volume_id && !d.local && d.mounted);
  const menu = (e: MouseEvent) =>
    openMenu(
      e,
      [
        { label: 'Open', icon: 'open', onSelect: () => (window.location.hash = href) },
        canEject && { label: 'Eject', icon: 'eject', disabled: Boolean(ejectBlock), hint: ejectBlock ? 'In use' : undefined, onSelect: () => void ejectDrive(d) },
        'sep',
        {
          label: d.ignored ? 'Show in the list again' : 'Ignore (move under Ignored)',
          icon: d.ignored ? 'check' : 'minus',
          onSelect: () => void setIgnored(d, !d.ignored),
        },
      ],
      driveName(d),
    );

  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      onClick={fresh ? onSeen : undefined}
      onContextMenu={menu}
      // States (rv-38, see the Key): role = dot + pill; on this page = blue bar + tint; just plugged in = green ring
      // (and it lands on the list with a lifted shadow); read or written now = neutral shimmer; unplugged = amber dashes.
      className={`group relative block rounded-xl border px-3 py-2.5 transition-[border-color,background-color,box-shadow] duration-700 ${
        fresh
          ? `border-go/55 bg-go/[0.05] shadow-[0_0_0_3px_rgb(var(--c-go)/0.12)] ${landed ? 'drive-landed' : ''}`
          : active
            ? 'border-select/45 bg-select/[0.07]'
            : 'border-line bg-raised/30 hover:border-line-strong hover:bg-raised/60'
      } ${io.length ? 'drive-busy' : ''} ${d.mounted ? '' : pulled || active ? 'border-dashed' : 'opacity-60 hover:opacity-90'} ${pulled ? 'drive-pulled' : ''} ${isNew && !fresh ? 'border-dashed' : ''}`}
    >
      {active && <motion.span layoutId="drive-active" transition={springs.snappy} className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-select" />}
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${ROLE_DOT[key]} ${live || io.length ? 'live-dot' : ''}`} aria-hidden="true" />
        <span className="min-w-[4.5rem] flex-1 truncate text-sm font-medium" title={`${driveName(d)}${d.mount ? ` · ${d.mount}` : ''}`}>
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
              className="chip shrink-0 border-go/40 bg-go/15 text-go hover:bg-go/25"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onSeen();
              }}
            >
              <span className="live-dot h-1.5 w-1.5 rounded-full bg-go" aria-hidden="true" />
              New
            </motion.button>
          )}
        </AnimatePresence>
        <FsPill fs={d.fs} />
        <span className="shrink-0 whitespace-nowrap">
          <RolePill role={d.role} isNew={isNew} />
        </span>
      </div>
      {/* The status line, with the row's ⋯ menu at its end (rv-42): shown on hover, it covers no pill. */}
      <div className="mt-1 flex items-center gap-1 pl-4">
        <p
          title={io.length ? status.text : undefined}
          className={`min-w-0 flex-1 truncate text-xs ${status.tone === 'running' ? 'text-ink/85' : status.tone === 'warn' ? 'text-maybe' : 'text-muted'}`}>
          {io.length > 0 && (
            <span className="mr-1 font-mono text-faint" aria-label={writing && reading ? 'Reading and writing' : writing ? 'Writing' : 'Reading'}>
              {writing && reading ? '↓↑' : writing ? '↓' : '↑'}
            </span>
          )}
          {d.mounted ? status.text : pulled ? 'Unplugged just now' : `Not connected${d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : ''}`}
        </p>
        <button
          type="button"
          aria-label={`More for ${driveName(d)}`}
          title="More (or right-click)"
          className="-my-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-faint opacity-0 hover:bg-ink/10 hover:text-ink focus:opacity-100 group-hover:opacity-100"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            menu(e);
          }}
        >
          ⋯
        </button>
      </div>
      {d.mounted && (used !== null || canEject) && (
        <div className="mt-2 flex items-center gap-2 pl-4">
          {/* A full card is normal; a full drive is not. */}
          {used !== null && key === 'card' && card?.counts ? (
            <CardSpaceBar used={used} counts={card.counts} />
          ) : used !== null ? (
            <ProgressBar
              value={used}
              size="xs"
              tone={key === 'card' ? 'muted' : used > 0.95 ? 'err' : used > 0.85 ? 'maybe' : 'muted'}
              label="Space used"
            />
          ) : (
            <span className="flex-1" />
          )}
          {d.free && <span className="shrink-0 font-mono text-2xs text-faint">{d.free} free</span>}
          {/* Eject (rv-43): at the end of the space line, so it never crowds the name. */}
          <EjectButton d={d} variant="icon" />
        </div>
      )}
    </a>
  );
}

/** A card's space bar: the used part split by tier in the same proportions as the card page's bar (by files), so a
 *  card's safety shows in the sidebar too. */
function CardSpaceBar({ used, counts }: { used: number; counts: NonNullable<CardInfo['counts']> }) {
  const total = TIER_ORDER.reduce((n, t) => n + counts[t].files, 0);
  const pct = Math.max(0, Math.min(1, used)) * 100;
  const label = `Space used ${Math.round(pct)} %: ${TIER_ORDER.filter((t) => counts[t].files).map((t) => `${counts[t].files} ${TIER[t].label.toLowerCase()}`).join(', ')}`;
  return (
    <div role="img" aria-label={label} title={label} className="relative h-1 w-full overflow-hidden rounded-full bg-ink/[0.07]">
      <motion.div className="flex h-full gap-px overflow-hidden rounded-full" initial={false} animate={{ width: `${pct > 0 ? Math.max(pct, 1.5) : 0}%` }} transition={springs.glide}>
        {total > 0
          ? TIER_ORDER.filter((t) => counts[t].files).map((t) => <span key={t} className={`h-full ${TIER_TONE[t].solid}`} style={{ width: `${(counts[t].files / total) * 100}%` }} />)
          : <span className="h-full w-full bg-ink/25" />}
      </motion.div>
    </div>
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
            className={`chip ${on ? 'border-select/50 bg-select/15 text-select' : 'border-line text-muted hover:border-line-strong hover:text-ink'}`}
          >
            {text(v)}
          </button>
        );
      })}
    </div>
  );
}

/** Sort and filter controls for the drive list (rv-33). Each group (Mounted, Not connected, Ignored) sorts on its own. */
/** What a drive row's colours and shading mean (rv-38): one channel per meaning, drawn with the real styles. */
function DriveKey() {
  const swatch = 'h-4 w-7 shrink-0 rounded-md border';
  const rows: [JSX.Element, string, string][] = [
    [
      <span className="flex w-7 shrink-0 items-center gap-1">
        <span className={`h-2 w-2 rounded-full ${ROLE_DOT.card}`} />
        <span className={`h-2 w-2 rounded-full ${ROLE_DOT.working}`} />
        <span className={`h-2 w-2 rounded-full ${ROLE_DOT.legacy}`} />
      </span>,
      'Dot and pill',
      'What the drive is: card, working, legacy or master.',
    ],
    [<span className={`${swatch} relative border-select/45 bg-select/[0.07]`}><span className="absolute inset-y-0.5 left-0 w-[2px] rounded-full bg-select" /></span>, 'Blue bar', 'The page you are on.'],
    [<span className={`${swatch} border-go/55 bg-go/[0.05] shadow-[0_0_0_2px_rgb(var(--c-go)/0.15)]`} />, 'Green ring', 'Just plugged in. Clears when you open or use it.'],
    [<span className={`${swatch} drive-busy border-line bg-raised/30`} />, 'Shimmer', 'Being read (↑) or written (↓) by a task right now.'],
    [<span className={`${swatch} border-dashed border-maybe/55`} />, 'Amber dashes', 'Just unplugged, or the page you are on is not connected.'],
    [<span className={`${swatch} border-line bg-raised/30 opacity-50`} />, 'Dimmed', 'Not connected.'],
  ];
  return (
    <ul className="mt-1 flex flex-col gap-1.5 rounded-lg border border-line bg-bg/40 p-2.5">
      {rows.map(([icon, name, text]) => (
        <li key={name} className="flex items-center gap-2.5 text-2xs">
          {icon}
          <span className="w-20 shrink-0 font-medium text-ink/85">{name}</span>
          <span className="text-muted">{text}</span>
        </li>
      ))}
    </ul>
  );
}

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
  const [keyOpen, setKeyOpen] = usePref('pv:driveKeyOpen', false);
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
          className={`chip ${n ? 'border-select/50 bg-select/10 text-select' : 'border-line text-muted hover:text-ink'}`}
        >
          Filter{n ? ` · ${n}` : ''}
        </button>
        {n > 0 && (
          <button type="button" className="text-2xs text-muted hover:text-ink" onClick={() => setFilter(NO_FILTER)}>
            Clear
          </button>
        )}
        <button
          type="button"
          aria-expanded={keyOpen}
          onClick={() => setKeyOpen((o) => !o)}
          title="What the colours and shading mean"
          className={`chip ml-auto ${keyOpen ? 'border-line-strong text-ink' : 'border-line text-muted hover:text-ink'}`}
        >
          Key
        </button>
        {hidden > 0 && <span className="shrink-0 font-mono text-2xs text-faint">{hidden} hidden</span>}
      </div>
      <AnimatePresence initial={false}>
        {keyOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ ...springs.snappy, opacity: { duration: 0.2 } }}
            className="overflow-hidden"
          >
            <DriveKey />
          </motion.div>
        )}
      </AnimatePresence>
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
          <div className="mx-1 mb-2 flex items-start gap-2 rounded-lg border border-go/40 bg-go/10 px-2.5 py-2 text-xs" role="status">
            <span className="live-dot mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-go" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-go">{freshHeadline(list.length)}</p>
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
  const { drives, loaded, now } = useStore();
  const { fresh, touched, touch, left } = useDriveActivity();
  const [folded, setFolded] = usePref<string[]>('pv:driveFold', ['ignored']);
  // A drive just unplugged moves to Not connected: open that group so it stays in sight (rv-38).
  const newest = Math.max(0, ...Object.values(left));
  useEffect(() => {
    if (newest) setFolded((f) => (f.includes('unmounted') ? f.filter((x) => x !== 'unmounted') : f));
  }, [newest, setFolded]);
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
                          <DriveRow d={d} fresh={fresh[d.key] !== undefined} landed={fresh[d.key] !== undefined && (now || Date.now()) - fresh[d.key]! < LANDED_MS} pulled={!d.mounted && left[d.key] !== undefined && (now || Date.now()) - left[d.key]! < PULLED_MS} onSeen={() => touch(d.key)} active={
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
