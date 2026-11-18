import { AnimatePresence, motion } from 'framer-motion';
import { memo, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { CountUp, Icon } from '../components/bits';
import { api } from '../lib/api';
import { fmtInt, plural } from '../lib/format';
import { driveName } from '../lib/labels';
import {
  applyFields,
  clockTime,
  dayLabel,
  MISSING_LABEL,
  monthRail,
  MONTHS,
  rangeIds,
  UNDATED,
  type Missing,
  type MissingCounts,
  type PhotoDay,
  type PhotoGroup,
  type PhotoSettings,
} from '../lib/photos';
import { usePref } from '../lib/prefs';
import { useStore } from '../lib/store';
import { ClockBanner } from './ClockBanner';
import { PhotoInspector } from './PhotoInspector';

const PAGE = 300; // photos fetched per request within a day
const ALL_PAGE = 1000; // the server's largest page, for "Select day"

interface DayState {
  groups: PhotoGroup[];
  loading: boolean;
  error: string;
}

const qs = (o: Record<string, string | number>): string =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== '' && v !== undefined).map(([k, v]) => [k, String(v)])).toString();

/**
 * Photos (rv-7): every frame in the catalog on a timeline of days, newest first, with a year and month rail to jump.
 * A day's photos load when it scrolls near; select with click, ⌘-click, ⇧-click or "Select day", and edit the
 * selection in the inspector. Thumbnails are the files' embedded previews, read while their drive is connected.
 */
export function Photos() {
  const { drives, toast, epoch } = useStore();
  const [volume, setVolume] = usePref<string>('photos.drive', '');
  const [missing, setMissing] = usePref<Missing>('photos.missing', '');
  const [days, setDays] = useState<PhotoDay[] | null>(null);
  const [counts, setCounts] = useState<MissingCounts | null>(null);
  const [loaded, setLoaded] = useState<Record<string, DayState>>({});
  const [sel, setSel] = useState<Map<number, PhotoGroup>>(() => new Map());
  const [settings, setSettings] = useState<PhotoSettings>({ creator: '', copyright: '', default_offset: '+00:00' });
  const [windowS, setWindowS] = useState(1800);
  const [reload, setReload] = useState(0);
  const [savedEpoch, setSavedEpoch] = useState(0);
  const [relatedIds, setRelatedIds] = useState<Set<number>>(() => new Set());
  const anchor = useRef<number | null>(null);
  const filterKey = `${volume}|${missing}`;
  const filterRef = useRef(filterKey);
  filterRef.current = filterKey;
  const loadedRef = useRef(loaded);
  loadedRef.current = loaded;

  const registered = useMemo(() => drives.filter((d) => d.volume_id), [drives]);
  const byVolume = useMemo(() => new Map(registered.map((d) => [d.volume_id!, d])), [registered]);
  useEffect(() => {
    if (volume && drives.length && !byVolume.has(volume)) setVolume('');
  }, [volume, drives.length, byVolume, setVolume]);

  useEffect(() => {
    api<{ settings: PhotoSettings & { match_window_s: string | number } }>('GET', '/api/settings')
      .then((d) => {
        setSettings({ creator: d.settings.creator ?? '', copyright: d.settings.copyright ?? '', default_offset: d.settings.default_offset ?? '+00:00' });
        setWindowS(Number(d.settings.match_window_s) || 1800);
      })
      .catch(() => undefined);
  }, []);

  // A new filter starts from scratch; a reload (after a save or a finished run) keeps what is on screen.
  useEffect(() => {
    setLoaded({});
    setDays(null);
  }, [filterKey]);
  useEffect(() => {
    let alive = true;
    api<{ days: PhotoDay[]; missing_counts: MissingCounts }>('GET', `/api/photo-days?${qs({ volume_id: volume, missing })}`)
      .then((d) => {
        if (!alive) return;
        setDays(d.days);
        setCounts(d.missing_counts);
      })
      .catch((err: Error) => alive && toast(err.message, 'err'));
    return () => {
      alive = false;
    };
  }, [filterKey, reload, epoch]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchDay = useCallback(
    async (day: string, offset: number, limit: number): Promise<PhotoGroup[]> => {
      const d = await api<{ groups: PhotoGroup[] }>('GET', `/api/groups?${qs({ day, volume_id: volume, missing, limit, offset })}`);
      return d.groups;
    },
    [volume, missing],
  );

  /** Load the first page of a day, or the next one with `more`. */
  const loadDay = useCallback(
    async (day: string, more = false) => {
      const cur = loadedRef.current[day];
      if (cur?.loading || (cur && !more)) return;
      const key = filterRef.current;
      const offset = more ? cur?.groups.length ?? 0 : 0;
      setLoaded((l) => ({ ...l, [day]: { groups: l[day]?.groups ?? [], loading: true, error: '' } }));
      try {
        const rows = await fetchDay(day, offset, PAGE);
        if (filterRef.current !== key) return;
        setLoaded((l) => ({ ...l, [day]: { groups: [...(more ? l[day]?.groups ?? [] : []), ...rows], loading: false, error: '' } }));
      } catch (err) {
        if (filterRef.current !== key) return;
        setLoaded((l) => ({ ...l, [day]: { groups: l[day]?.groups ?? [], loading: false, error: (err as Error).message } }));
      }
    },
    [fetchDay],
  );

  // Every loaded photo, in display order: shift-click ranges and nearby-photo suggestions use it.
  const pool = useMemo(() => (days ?? []).flatMap((d) => loaded[d.day]?.groups ?? []), [days, loaded]);
  const order = useMemo(() => pool.map((g) => g.group_id), [pool]);

  const onTile = useCallback(
    (g: PhotoGroup, e: MouseEvent) => {
      setSel((prev) => {
        const next = new Map(prev);
        if (e.shiftKey) {
          const byId = new Map(pool.map((p) => [p.group_id, p]));
          for (const id of rangeIds(order, anchor.current, g.group_id)) {
            const p = byId.get(id);
            if (p) next.set(id, p);
          }
          return next;
        }
        if (e.metaKey || e.ctrlKey) {
          if (next.has(g.group_id)) next.delete(g.group_id);
          else next.set(g.group_id, g);
        } else if (prev.size === 1 && prev.has(g.group_id)) {
          next.clear();
        } else {
          next.clear();
          next.set(g.group_id, g);
        }
        return next;
      });
      if (!e.shiftKey) anchor.current = g.group_id;
    },
    [pool, order],
  );

  const selectDay = async (d: PhotoDay) => {
    const key = filterRef.current;
    let rows = loadedRef.current[d.day]?.groups ?? [];
    try {
      while (rows.length < d.groups) {
        const more = await fetchDay(d.day, rows.length, ALL_PAGE);
        if (!more.length) break;
        rows = [...rows, ...more];
      }
    } catch (err) {
      toast((err as Error).message, 'err');
      return;
    }
    if (filterRef.current !== key) return;
    setLoaded((l) => ({ ...l, [d.day]: { groups: rows, loading: false, error: '' } }));
    const allIn = rows.length > 0 && rows.every((g) => sel.has(g.group_id));
    setSel((prev) => {
      const next = new Map(prev);
      for (const g of rows) {
        if (allIn) next.delete(g.group_id);
        else next.set(g.group_id, g);
      }
      return next;
    });
  };

  const clear = useCallback(() => setSel(new Map()), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === 'Escape' && !(t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) clear();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clear]);

  const onRelated = useCallback((ids: number[]) => setRelatedIds(new Set(ids)), []);
  useEffect(() => {
    if (!sel.size) setRelatedIds((r) => (r.size ? new Set() : r));
  }, [sel.size]);

  const onSaved = async (fields: Record<string, unknown>, savedIds: number[]) => {
    const ids = new Set(savedIds);
    setSel((prev) => new Map([...prev].map(([id, g]) => [id, ids.has(id) ? applyFields(g, fields) : g])));
    setSavedEpoch((e) => e + 1);
    setLoaded((l) => {
      const out: Record<string, DayState> = {};
      for (const [day, s] of Object.entries(l)) out[day] = { ...s, groups: s.groups.map((g) => (ids.has(g.group_id) ? applyFields(g, fields) : g)) };
      return out;
    });
    setReload((r) => r + 1);
  };

  const selected = useMemo(() => [...sel.values()], [sel]);
  const rail = useMemo(() => monthRail(days ?? []), [days]);
  const filtered = Boolean(volume || missing);

  return (
    <div className="flex flex-col gap-4">
      <ClockBanner onApplied={() => setReload((r) => r + 1)} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">Photos</h1>
          <p className="mt-1 text-sm text-muted">
            {counts ? (
              <>
                <CountUp value={counts.groups} /> photos · <CountUp value={counts.gps} /> without a location · <CountUp value={counts.creator} /> without a creator ·{' '}
                <CountUp value={counts.keywords} /> without keywords
              </>
            ) : (
              'Every photo in the catalog, by the day it was taken.'
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={volume} onChange={(e) => setVolume(e.target.value)} className="h-8" aria-label="Drive">
            <option value="">All drives</option>
            {registered.map((d) => (
              <option key={d.volume_id} value={d.volume_id!}>
                {driveName(d)}
                {d.mounted ? '' : ' (not connected)'}
              </option>
            ))}
          </select>
          <select value={missing} onChange={(e) => setMissing(e.target.value as Missing)} className="h-8" aria-label="Show">
            {(Object.keys(MISSING_LABEL) as Missing[]).map((k) => (
              <option key={k} value={k}>
                {MISSING_LABEL[k]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className={`grid items-start gap-4 ${sel.size ? 'xl:grid-cols-[minmax(0,1fr)_360px]' : ''}`}>
        <div className="grid min-w-0 items-start gap-3 md:grid-cols-[56px_minmax(0,1fr)]">
          <MonthRail rail={rail} undated={days?.some((d) => d.day === UNDATED) ?? false} />
          <div className="flex min-w-0 flex-col gap-1">
            {days === null ? (
              <Skeleton />
            ) : days.length === 0 ? (
              <Empty filtered={filtered} />
            ) : (
              days.map((d) => (
                <DaySection
                  key={`${filterKey}|${d.day}`}
                  d={d}
                  state={loaded[d.day]}
                  need={loadDay}
                  selected={sel}
                  related={relatedIds}
                  allSelected={(loaded[d.day]?.groups.length ?? 0) >= d.groups && (loaded[d.day]?.groups ?? []).every((g) => sel.has(g.group_id))}
                  onTile={onTile}
                  onSelectDay={() => void selectDay(d)}
                  connected={(id) => (id ? byVolume.get(id)?.mounted ?? false : false)}
                />
              ))
            )}
          </div>
        </div>

        <AnimatePresence>
          {sel.size > 0 && (
            <motion.div
              key="inspector"
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 24, transition: { duration: 0.12 } }}
              transition={{ type: 'spring', stiffness: 420, damping: 36 }}
              className="fixed bottom-2 right-2 top-16 z-20 w-[min(380px,calc(100vw-1rem))] xl:sticky xl:top-[72px] xl:bottom-auto xl:right-auto xl:z-auto xl:w-auto xl:self-start"
            >
              <PhotoInspector selected={selected} pool={pool} settings={settings} windowS={windowS} onClear={clear} onSaved={onSaved} onRelated={onRelated} savedEpoch={savedEpoch} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function MonthRail({ rail, undated }: { rail: ReturnType<typeof monthRail>; undated: boolean }) {
  const go = (day: string) => document.getElementById(`day-${day}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!rail.length) return <div className="hidden md:block" />;
  return (
    <nav aria-label="Jump to month" className="no-scrollbar hidden max-h-[calc(100vh-88px)] flex-col gap-2 overflow-y-auto md:sticky md:top-[72px] md:flex">
      {rail.map((y) => (
        <div key={y.year} className="flex flex-col">
          <button type="button" className="label py-1 text-left hover:text-ink" onClick={() => go(y.months[0]!.day)}>
            {y.year}
          </button>
          {y.months.map((m) => (
            <button
              key={m.month}
              type="button"
              onClick={() => go(m.day)}
              className="flex items-center justify-between rounded px-1 py-0.5 text-left text-xs text-muted hover:bg-ink/5 hover:text-ink"
              title={`${MONTHS[m.month - 1]} ${y.year}: ${plural(m.groups, 'photo')}`}
            >
              {MONTHS[m.month - 1]}
            </button>
          ))}
        </div>
      ))}
      {undated && (
        <button type="button" className="label py-1 text-left hover:text-ink" onClick={() => go(UNDATED)}>
          No date
        </button>
      )}
    </nav>
  );
}

function DaySection({
  d,
  state,
  need,
  selected,
  related,
  allSelected,
  onTile,
  onSelectDay,
  connected,
}: {
  d: PhotoDay;
  state: DayState | undefined;
  need: (day: string, more?: boolean) => Promise<void>;
  selected: Map<number, PhotoGroup>;
  related: Set<number>;
  allSelected: boolean;
  onTile: (g: PhotoGroup, e: MouseEvent) => void;
  onSelectDay: () => void;
  connected: (volumeId: string | null) => boolean;
}) {
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    if (state || !box.current) return undefined;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && void need(d.day), { rootMargin: '600px 0px' });
    io.observe(box.current);
    return () => io.disconnect();
  }, [state, need, d.day]);

  const groups = state?.groups ?? [];
  const left = d.groups - groups.length;
  const nSel = groups.reduce((n, g) => n + (selected.has(g.group_id) ? 1 : 0), 0);

  return (
    <section ref={box} id={`day-${d.day}`} className="scroll-mt-[64px]">
      <div className="sticky top-[57px] z-10 -mx-1 flex items-center justify-between gap-3 bg-bg/85 px-1 py-2 backdrop-blur">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate font-medium">{dayLabel(d.day)}</h2>
          <span className="truncate text-xs text-muted">
            {[d.bodies.join(', '), plural(d.groups, 'photo'), nSel ? `${fmtInt(nSel)} selected` : ''].filter(Boolean).join(' · ')}
          </span>
        </div>
        <button type="button" className="btn-ghost h-7 shrink-0 px-2 text-xs" onClick={onSelectDay}>
          {allSelected ? 'Deselect day' : 'Select day'}
        </button>
      </div>
      {state && groups.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5 pb-3">
          {groups.map((g) => (
            <Tile key={g.group_id} g={g} on={selected.has(g.group_id)} related={related.has(g.group_id)} connected={connected(g.volume_id)} onTile={onTile} />
          ))}
        </div>
      ) : (
        <div
          className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5 pb-3"
          style={{ minHeight: Math.min(4, Math.ceil(Math.min(d.groups, PAGE) / 6)) * 120 }}
          aria-hidden="true"
        >
          {Array.from({ length: Math.min(d.groups, 12) }, (_, i) => (
            <div key={i} className="aspect-square animate-pulse rounded-lg bg-ink/5" />
          ))}
        </div>
      )}
      {state?.error && (
        <p className="pb-3 text-sm text-err">
          {state.error}{' '}
          <button type="button" className="underline" onClick={() => void need(d.day, true)}>
            Retry
          </button>
        </p>
      )}
      {state && !state.loading && left > 0 && groups.length > 0 && (
        <button type="button" className="btn mb-3" onClick={() => void need(d.day, true)}>
          Show {fmtInt(Math.min(left, PAGE))} more{left > PAGE ? ` of ${fmtInt(left)}` : ''}
        </button>
      )}
    </section>
  );
}

const Tile = memo(function Tile({ g, on, related, connected, onTile }: { g: PhotoGroup; on: boolean; related: boolean; connected: boolean; onTile: (g: PhotoGroup, e: MouseEvent) => void }) {
  const [failed, setFailed] = useState(false);
  const name = g.canonical_stem || `Photo ${g.group_id}`;
  return (
    <button
      type="button"
      aria-pressed={on}
      title={`${name}${g.capture_time ? ` · ${g.capture_time.replace('T', ' ')}` : ''}${g.place_name ? ` · ${g.place_name}` : ''}`}
      onClick={(e) => onTile(g, e)}
      className={`group relative aspect-square overflow-hidden rounded-lg bg-ink/5 ${related && !on ? '' : 'outline-none'} ring-offset-2 ring-offset-bg transition-shadow focus-visible:ring-2 focus-visible:ring-accent/60 ${on ? 'ring-[3px] ring-accent' : related ? 'outline-dashed outline-2 outline-accent/70 outline-offset-[-2px]' : ''}`}
    >
      {failed || !g.volume_id ? (
        <span className="flex h-full w-full flex-col items-center justify-center gap-1 p-2 text-center text-2xs text-faint">
          <Icon name="image" size={18} />
          <span className="max-w-full truncate font-mono">{name}</span>
          <span>{!g.volume_id ? 'No file indexed' : connected ? 'No preview' : 'Drive not connected'}</span>
        </span>
      ) : (
        <img
          src={`/api/groups/${g.group_id}/thumb`}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className={`h-full w-full object-cover transition-transform duration-300 ${on ? 'scale-[0.94] rounded-md' : 'group-hover:scale-[1.03]'}`}
        />
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/60 to-transparent px-1.5 pb-1 pt-4 text-2xs text-white opacity-0 transition-opacity group-hover:opacity-100">
        <span className="truncate font-mono">{name}</span>
        <span className="tabular">{clockTime(g.capture_time)}</span>
      </span>
      <span className="pointer-events-none absolute right-1 top-1 flex gap-0.5">
        {g.lat !== null && (
          <span className="rounded-full bg-black/45 p-0.5 text-white" title="Has a location">
            <Icon name="pin" size={11} />
          </span>
        )}
        {g.dirty && (
          <span className="rounded-full bg-black/45 p-0.5 text-white" title="Edited; sidecar not written yet">
            <Icon name="edit" size={11} />
          </span>
        )}
      </span>
      {on && (
        <span className="absolute left-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-on-accent shadow">
          <Icon name="check" size={12} />
        </span>
      )}
    </button>
  );
});

function Skeleton() {
  return (
    <div className="flex flex-col gap-3">
      <div className="h-6 w-48 animate-pulse rounded bg-ink/5" />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5">
        {Array.from({ length: 18 }, (_, i) => (
          <div key={i} className="aspect-square animate-pulse rounded-lg bg-ink/5" />
        ))}
      </div>
    </div>
  );
}

function Empty({ filtered }: { filtered: boolean }) {
  return (
    <div className="card flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Icon name="image" size={24} className="text-faint" />
      <p className="font-medium">{filtered ? 'No photos match these filters' : 'No photos yet'}</p>
      <p className="max-w-md text-sm text-muted">
        {filtered
          ? 'Try another drive, or show all photos.'
          : 'Photos appear here once a drive or card has been indexed and its metadata read. Open a drive and run its workflow.'}
      </p>
    </div>
  );
}
