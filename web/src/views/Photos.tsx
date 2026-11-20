import { AnimatePresence, motion } from 'framer-motion';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { CountUp, Icon } from '../components/bits';
import { api } from '../lib/api';
import { fmtInt, plural } from '../lib/format';
import { driveName } from '../lib/labels';
import {
  applyFields,
  copyText,
  dayLabel,
  dayLine,
  dayOf,
  filterChips,
  filterNote,
  firstInTime,
  MISSING_LABEL,
  monthRail,
  MONTHS,
  rangeIds,
  fullOffline,
  wrapIndex,
  UNDATED,
  type Missing,
  type MissingCounts,
  type PhotoDay,
  type PhotoGroup,
  type PhotoSettings,
  vaultCopyName,
} from '../lib/photos';
import { openMenu } from '../lib/menu';
import { onlyShoot, sectionsOf, type Outline, type Section } from '../lib/outline';
import { holdCentred } from '../lib/reveal';
import { usePref } from '../lib/prefs';
import { useStore } from '../lib/store';
import { ClockBanner } from './ClockBanner';
import { Lightbox } from './Lightbox';
import { PhotoInspector } from './PhotoInspector';
import { goToSection, TripsRail, TripsView, type TripsShared } from './PhotosTrips';
import { PAGE, Tile, type DayState } from './PhotoTile';

const reducedMotion = (): boolean => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

interface ShootRow {
  shoot_id: number;
  name: string;
  shoot_date: string;
  groups: number;
}

const ALL_PAGE = 1000; // the server's largest page, for "Select day"

const qs = (o: Record<string, string | number>): string =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== '' && v !== undefined).map(([k, v]) => [k, String(v)])).toString();

/**
 * Photos (rv-7): every frame in the catalog on a timeline of days, newest first, with a year and month rail to jump.
 * A day's photos load when it scrolls near; select with click, ⌘-click, ⇧-click or "Select day", and edit the
 * selection in the inspector. Thumbnails are the files' embedded previews, read while their drive is connected.
 */
export function Photos({ missing: wantMissing, day: wantDay, photo: wantPhoto, shoot: wantShoot }: { missing?: Missing; day?: string; photo?: number; shoot?: string } = {}) {
  const { drives, toast, epoch } = useStore();
  const [volume, setVolume] = usePref<string>('photos.drive', '');
  const [missing, setMissing] = usePref<Missing>('photos.missing', '');
  const [shoot, setShoot] = usePref<string>('photos.shoot', '');
  const [shootList, setShootList] = useState<ShootRow[]>([]);
  // Trips (rv-47, D-134) groups the timeline as trip -> shoot; Days is the plain timeline of days.
  const [view, setView] = usePref<'trips' | 'days'>('photos.view', 'trips');
  const [outline, setOutline] = useState<Outline | null>(null);
  const [outlineKey, setOutlineKey] = useState('');
  const [days, setDays] = useState<PhotoDay[] | null>(null);
  // The filter the current `days` were fetched for, so a reveal never reads a list from before the filters cleared.
  const [daysKey, setDaysKey] = useState('');
  const [counts, setCounts] = useState<MissingCounts | null>(null);
  const [loaded, setLoaded] = useState<Record<string, DayState>>({});
  const [sel, setSel] = useState<Map<number, PhotoGroup>>(() => new Map());
  const [settings, setSettings] = useState<PhotoSettings>({ creator: '', copyright: '', default_offset: '+00:00' });
  const [windowS, setWindowS] = useState(1800);
  const [reload, setReload] = useState(0);
  const [savedEpoch, setSavedEpoch] = useState(0);
  const [relatedIds, setRelatedIds] = useState<Set<number>>(() => new Set());
  const anchor = useRef<number | null>(null);
  const filterKey = `${volume}|${missing}|${shoot}`;
  // Loaded sections are keyed by day (Days) or by shoot key (Trips), so they reset with the view too.
  const sectionKey = `${view}|${filterKey}`;
  const filterRef = useRef(sectionKey);
  filterRef.current = sectionKey;
  const loadedRef = useRef(loaded);
  loadedRef.current = loaded;

  const registered = useMemo(() => drives.filter((d) => d.volume_id), [drives]);
  const byVolume = useMemo(() => new Map(registered.map((d) => [d.volume_id!, d])), [registered]);
  useEffect(() => {
    if (volume && drives.length && !byVolume.has(volume)) setVolume('');
  }, [volume, drives.length, byVolume, setVolume]);

  // Applied shoots (sh-4) for the shoot filter; a filter on a shoot that is gone (undone) is dropped.
  useEffect(() => {
    let alive = true;
    api<{ shoots: ShootRow[] }>('GET', '/api/shoots')
      .then((d) => {
        if (!alive) return;
        setShootList(d.shoots);
        if (shoot && !d.shoots.some((x) => String(x.shoot_id) === shoot)) setShoot('');
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [epoch]); // eslint-disable-line react-hooks/exhaustive-deps

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
  }, [sectionKey]);
  useEffect(() => {
    setDays(null);
    setOutline(null);
  }, [filterKey]);
  useEffect(() => {
    if (view !== 'trips') return undefined;
    let alive = true;
    const key = filterKey;
    api<Outline>('GET', `/api/outline?${qs({ volume_id: volume, missing })}`)
      .then((d) => {
        if (!alive) return;
        setOutline(d);
        setOutlineKey(key);
      })
      .catch((err: Error) => alive && toast(err.message, 'err'));
    return () => {
      alive = false;
    };
  }, [view, filterKey, reload, epoch]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let alive = true;
    const key = filterKey;
    api<{ days: PhotoDay[]; missing_counts: MissingCounts }>('GET', `/api/photo-days?${qs({ volume_id: volume, missing, shoot_id: shoot })}`)
      .then((d) => {
        if (!alive) return;
        setDays(d.days);
        setDaysKey(key);
        setCounts(d.missing_counts);
      })
      .catch((err: Error) => alive && toast(err.message, 'err'));
    return () => {
      alive = false;
    };
  }, [filterKey, reload, epoch]); // eslint-disable-line react-hooks/exhaustive-deps

  // A section is a day (Days) or a shoot / loose day by its outline key (Trips).
  const fetchDay = useCallback(
    async (key: string, offset: number, limit: number): Promise<PhotoGroup[]> => {
      const which: Record<string, string> = view === 'trips' ? { shoot_key: key } : { day: key };
      const d = await api<{ groups: PhotoGroup[] }>('GET', `/api/groups?${qs({ ...which, volume_id: volume, missing, shoot_id: shoot, limit, offset })}`);
      return d.groups;
    },
    [view, volume, missing, shoot],
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
  const items = useMemo(() => (outline ? (shoot ? onlyShoot(outline.items, Number(shoot)) : outline.items) : null), [outline, shoot]);
  const flat = useMemo<Section[]>(
    () => (view === 'trips' ? (items ? sectionsOf(items) : []) : (days ?? []).map((d) => ({ key: d.day, groups: d.groups, days: [d.day] }))),
    [view, items, days],
  );
  // The list for the active view is in and was fetched for the current filters.
  const ready = view === 'trips' ? outline !== null && outlineKey === filterKey : days !== null;
  const pool = useMemo(() => flat.flatMap((s) => loaded[s.key]?.groups ?? []), [flat, loaded]);
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

  const selectDay = async (d: { key: string; groups: number }) => {
    const key = filterRef.current;
    let rows = loadedRef.current[d.key]?.groups ?? [];
    try {
      while (rows.length < d.groups) {
        const more = await fetchDay(d.key, rows.length, ALL_PAGE);
        if (!more.length) break;
        rows = [...rows, ...more];
      }
    } catch (err) {
      toast((err as Error).message, 'err');
      return;
    }
    if (filterRef.current !== key) return;
    setLoaded((l) => ({ ...l, [d.key]: { groups: rows, loading: false, error: '' } }));
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

  // A link such as `#/photos?missing=gps&day=2023-09-09` (act-3: the Actions button) sets the "Missing" filter, drops
  // the drive and shoot filters, then scrolls to the day once the filtered days have loaded.
  const [wantScroll, setWantScroll] = useState<string | null>(null);
  useEffect(() => {
    if (wantPhoto || (!wantMissing && !wantDay)) return;
    if (wantMissing) {
      setMissing(wantMissing);
      setVolume('');
      setShoot('');
    }
    setWantScroll(wantDay ?? null);
  }, [wantMissing, wantDay]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!wantScroll || !ready || (wantMissing && missing !== wantMissing)) return;
    setWantScroll(null);
    const sec = flat.find((s) => s.days.includes(wantScroll));
    if (!sec) return;
    const id = view === 'trips' ? `sec-${sec.key}` : `day-${wantScroll}`;
    const t = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: reducedMotion() ? 'auto' : 'smooth' }), 120);
    return () => window.clearTimeout(t);
  }, [wantScroll, ready, flat, view, missing, wantMissing]);

  // `#/photos?shoot=<key>` (rv-47): the Trips view, filters dropped, scrolled to that shoot once the outline is in.
  const [wantSection, setWantSection] = useState<string | null>(null);
  useEffect(() => {
    if (!wantShoot) return;
    setView('trips');
    setVolume('');
    setMissing('');
    setShoot('');
    setWantSection(wantShoot);
  }, [wantShoot]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!wantSection || view !== 'trips' || outline === null || outlineKey !== filterKey || volume || missing || shoot) return;
    setWantSection(null);
    const t = window.setTimeout(() => goToSection(wantSection, !reducedMotion()), 120);
    return () => window.clearTimeout(t);
  }, [wantSection, view, outline, outlineKey, filterKey, volume, missing, shoot]);

  // Show in All Photos (rv-19): drop the filters, load the photo's day (and further pages of it), centre the tile and
  // flash its ring once.
  const [reveal, setReveal] = useState<{ id: number; day: string } | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  // The one function that clears the drive and "missing" filters: the chips' x and Show in All Photos use it (rv-21).
  const clearFilters = useCallback(() => {
    setVolume('');
    setMissing('');
    setShoot('');
  }, [setVolume, setMissing, setShoot]);
  // Show in Photos from a drive or card file list (rv-27): the same reveal, then the photo is selected.
  const selectOnReveal = useRef<number | null>(null);
  useEffect(() => {
    if (!wantPhoto || !wantDay) return;
    clearFilters();
    selectOnReveal.current = wantPhoto;
    setReveal({ id: wantPhoto, day: wantDay });
  }, [wantPhoto, wantDay]); // eslint-disable-line react-hooks/exhaustive-deps
  const revealGroup = useCallback(
    (g: PhotoGroup) => {
      clearFilters();
      setReveal({ id: g.group_id, day: dayOf(g) });
    },
    [clearFilters],
  );
  const showInAll = useCallback(() => {
    const g = firstInTime([...sel.values()]);
    if (g) revealGroup(g);
  }, [sel, revealGroup]);
  useEffect(() => {
    if (!reveal || volume || missing || shoot || !ready || (view === 'days' && daysKey !== filterKey)) return;
    // The sections that could hold the photo: its day (Days), or every shoot / loose day spanning that day (Trips).
    const cands = flat.filter((c) => c.days.includes(reveal.day));
    let st: DayState | undefined;
    for (const c of cands) {
      const cur = loaded[c.key];
      if (!cur) {
        void loadDay(c.key);
        return;
      }
      if (cur.loading) return;
      if (cur.groups.some((g) => g.group_id === reveal.id)) {
        st = cur;
        break;
      }
      if (cur.groups.length < c.groups && !cur.error) {
        void loadDay(c.key, true);
        return;
      }
    }
    if (!st) {
      setReveal(null);
      toast('That photo is not in the timeline.', 'err');
      return;
    }
    const id = reveal.id;
    setReveal(null);
    if (selectOnReveal.current === id) {
      selectOnReveal.current = null;
      const g = st.groups.find((x) => x.group_id === id);
      if (g) {
        setSel(new Map([[id, g]]));
        anchor.current = id;
      }
    }
    setScrollTo(id);
  }, [reveal, volume, missing, shoot, ready, view, flat, daysKey, filterKey, loaded, loadDay, toast]);
  // Scrolling is its own effect: clearing `reveal` above re-runs that effect, whose cleanup would cancel a hold
  // started there. It jumps to the tile and keeps it centred while the days around it load and grow and the
  // inspector narrows the grid (rv-27 fix 2: a single smooth scroll landed on whatever grew into its place).
  const [scrollTo, setScrollTo] = useState<number | null>(null);
  useEffect(() => {
    if (scrollTo === null) return undefined;
    const id = scrollTo;
    // The flash timer is left to run out on its own: it only clears its own id.
    return holdCentred(() => document.querySelector<HTMLElement>(`[data-gid="${id}"]`), {
      onFound: (el) => {
        el.focus({ preventScroll: true });
        setFlash(id);
        window.setTimeout(() => setFlash((f) => (f === id ? null : f)), 1400);
      },
      onDone: () => setScrollTo((s) => (s === id ? null : s)),
    });
  }, [scrollTo]);

  // The full-screen viewer (rv-20): steps through the open photo's day, as loaded, and the selection follows.
  const [viewerId, setViewerId] = useState<number | null>(null);
  const viewerList = useMemo(() => {
    if (viewerId === null) return [];
    for (const s of Object.values(loaded)) if (s.groups.some((g) => g.group_id === viewerId)) return s.groups;
    return [];
  }, [viewerId, loaded]);
  const viewerIndex = viewerList.findIndex((g) => g.group_id === viewerId);
  const openViewer = useCallback((g: PhotoGroup) => {
    if (!g.volume_id) return;
    setSel(new Map([[g.group_id, g]]));
    anchor.current = g.group_id;
    setViewerId(g.group_id);
  }, []);
  const closeViewer = useCallback(() => {
    const id = viewerId;
    setViewerId(null);
    if (id !== null) window.setTimeout(() => document.querySelector<HTMLElement>(`[data-gid="${id}"]`)?.focus({ preventScroll: true }), 0);
  }, [viewerId]);
  const stepViewer = useCallback(
    (d: number) => {
      const next = viewerList[wrapIndex(viewerIndex, d, viewerList.length)];
      if (!next) return;
      setViewerId(next.group_id);
      setSel(new Map([[next.group_id, next]]));
      anchor.current = next.group_id;
    },
    [viewerList, viewerIndex],
  );

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

  // Right-click on a tile (rv-28). Like Finder and Photos: a photo outside the selection becomes the selection; one
  // inside it keeps the selection. Built fresh on each click through a ref, so the memoised tiles keep one handler.
  const filtered = Boolean(volume || missing || shoot);
  const tileMenu = (g: PhotoGroup, e: MouseEvent) => {
    if (!sel.has(g.group_id)) {
      setSel(new Map([[g.group_id, g]]));
      anchor.current = g.group_id;
    }
    const n = sel.has(g.group_id) ? sel.size : 1;
    const day = days?.find((d) => d.day === dayOf(g));
    const sec = view === 'trips' ? flat.find((c) => loaded[c.key]?.groups.some((x) => x.group_id === g.group_id)) : undefined;
    const mounted = g.volume_id ? byVolume.get(g.volume_id)?.mounted ?? false : false;
    const name = vaultCopyName(g) || `Photo ${g.group_id}`;
    openMenu(
      e,
      [
        { label: 'Open', icon: 'expand', hint: 'Space', disabled: !g.volume_id, onSelect: () => openViewer(g) },
        { label: filtered ? 'Show in All Photos' : 'Scroll to this photo', icon: 'image', onSelect: () => revealGroup(g) },
        'sep',
        view === 'days' && day && { label: `Select all of ${dayLabel(day.day)}`, icon: 'check', onSelect: () => void selectDay({ key: day.day, groups: day.groups }) },
        sec && { label: sec.key.startsWith('loose:') ? `Select all of ${dayLabel(dayOf(g))}` : 'Select this whole shoot', icon: 'check', onSelect: () => void selectDay(sec) },
        g.shoot_id !== null && shoot !== String(g.shoot_id) && { label: `Show only ${g.shoot_name ?? 'this shoot'}`, icon: 'camera', onSelect: () => setShoot(String(g.shoot_id)) },
        'sep',
        { label: 'Copy name', icon: 'copy', onSelect: () => void copyText(name).then((ok) => toast(ok ? `Copied ${name}` : 'Could not copy.', ok ? 'ok' : 'err')) },
        Boolean(g.volume_id && g.rel_path && mounted) && {
          label: 'Show in Finder',
          icon: 'folder',
          onSelect: () => void api('POST', `/api/volumes/${encodeURIComponent(g.volume_id!)}/reveal`, { rel_path: g.rel_path }).catch((err: Error) => toast(err.message, 'err')),
        },
        'sep',
        n > 0 && { label: n > 1 ? `Deselect ${fmtInt(n)} photos` : 'Deselect', icon: 'x', hint: 'Esc', onSelect: clear },
      ],
      n > 1 ? `${name} · ${fmtInt(n)} selected` : name,
    );
  };
  const tileMenuRef = useRef(tileMenu);
  tileMenuRef.current = tileMenu;
  const onTileMenu = useCallback((g: PhotoGroup, e: MouseEvent) => tileMenuRef.current(g, e), []);

  const selected = useMemo(() => [...sel.values()], [sel]);
  const rail = useMemo(() => monthRail(days ?? []), [days]);
  const shared: TripsShared = {
    loaded,
    need: loadDay,
    selected: sel,
    related: relatedIds,
    flash,
    onTile,
    onOpen: openViewer,
    onMenu: onTileMenu,
    onSelectSection: (key, groups) => void selectDay({ key, groups }),
    connected: (id) => (id ? byVolume.get(id)?.mounted ?? false : false),
  };
  const chips = useMemo(() => {
    const d = volume ? byVolume.get(volume) : undefined;
    const sh = shoot ? shootList.find((x) => String(x.shoot_id) === shoot) : undefined;
    return filterChips(volume ? (d ? driveName(d) : 'Drive') : null, missing, shoot ? (sh ? sh.name : 'Shoot') : null);
  }, [volume, missing, shoot, byVolume, shootList]);
  const note = filterNote(chips);

  return (
    <div className="flex flex-col gap-4">
      <ClockBanner onApplied={() => setReload((r) => r + 1)} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Photos</h1>
            <div className="flex rounded-lg border border-line p-0.5" role="group" aria-label="Photos view">
              {(['trips', 'days'] as const).map((v) => (
                <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)} className={`h-6 rounded-md px-2.5 text-xs ${view === v ? 'bg-ink/10 text-ink' : 'text-muted hover:text-ink'}`}>
                  {v === 'trips' ? 'Trips' : 'Days'}
                </button>
              ))}
            </div>
            <a
              href="#/review/shoots"
              title="Check the proposed trips and shoots as a dry run (nothing changes until you Apply)"
              className="inline-flex h-7 items-center rounded-full border border-maybe/40 bg-maybe/10 px-3 text-xs font-medium text-maybe hover:bg-maybe/20"
            >
              Review shoots
            </a>
          </div>
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
          {view === 'trips' && (
            <p className="mt-0.5 text-xs text-muted" title="Decisions saved after the plan was made show here once you Re-plan.">
              <span className="text-maybe/70">Names in amber</span> are proposals; confirm them in{' '}
              <a href="#/review/shoots" className="underline hover:text-ink">
                Review →
              </a>
            </p>
          )}
        </div>
      </div>

      {/* Persistent filter bar: stays above the sticky day header while the grid scrolls. */}
      <div className="no-scrollbar sticky top-[57px] z-20 -mx-1 flex h-12 items-center gap-2 overflow-x-auto border-b border-line bg-bg/90 px-1 backdrop-blur" aria-label="Filters">
        <select value={volume} onChange={(e) => setVolume(e.target.value)} className="h-8 shrink-0" aria-label="Drive">
          <option value="">All drives</option>
          {registered.map((d) => (
            <option key={d.volume_id} value={d.volume_id!}>
              {driveName(d)}
              {d.mounted ? '' : ' (not connected)'}
            </option>
          ))}
        </select>
        {shootList.length > 0 && (
          <select value={shoot} onChange={(e) => setShoot(e.target.value)} className="h-8 max-w-[14rem] shrink-0" aria-label="Shoot">
            <option value="">All shoots</option>
            {shootList.map((x) => (
              <option key={x.shoot_id} value={String(x.shoot_id)}>
                {x.name} · {x.shoot_date}
              </option>
            ))}
          </select>
        )}
        <select value={missing} onChange={(e) => setMissing(e.target.value as Missing)} className="h-8 shrink-0" aria-label="Show">
          {(Object.keys(MISSING_LABEL) as Missing[]).map((k) => (
            <option key={k} value={k}>
              {MISSING_LABEL[k]}
            </option>
          ))}
        </select>
        {chips.map((c) => (
          <span key={c.key} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/30 bg-accent/10 py-0.5 pl-2.5 pr-1 text-xs">
            {c.label}
            <button type="button" className="rounded-full p-0.5 text-muted hover:bg-ink/10 hover:text-ink" aria-label={`Clear filter ${c.label}`} onClick={() => (c.key === 'drive' ? setVolume('') : c.key === 'shoot' ? setShoot('') : setMissing(''))}>
              <Icon name="x" size={10} />
            </button>
          </span>
        ))}
        {chips.length > 1 && (
          <button type="button" className="shrink-0 text-xs text-muted underline hover:text-ink" onClick={clearFilters}>
            Clear all
          </button>
        )}
        <span className="ml-auto shrink-0 pl-2 text-xs text-muted">
          {counts ? `${fmtInt(counts.groups)} photos` : ''}
          {sel.size ? ` · ${fmtInt(sel.size)} selected` : ''}
        </span>
      </div>

      <div className={`grid items-start gap-4 ${sel.size ? 'xl:grid-cols-[minmax(0,1fr)_360px]' : ''}`}>
        <div className="grid min-w-0 items-start gap-3 md:grid-cols-[56px_minmax(0,1fr)]">
          {view === 'trips' ? <TripsRail items={items ?? []} /> : <MonthRail rail={rail} undated={days?.some((d) => d.day === UNDATED) ?? false} />}
          <div className="flex min-w-0 flex-col gap-1">
            {!ready ? (
              <Skeleton />
            ) : flat.length === 0 ? (
              <Empty filtered={filtered} />
            ) : view === 'trips' ? (
              <TripsView items={items ?? []} shared={shared} />
            ) : (
              (days ?? []).map((d) => (
                <DaySection
                  key={`${filterKey}|${d.day}`}
                  d={d}
                  state={loaded[d.day]}
                  need={loadDay}
                  selected={sel}
                  related={relatedIds}
                  flash={flash}
                  allSelected={(loaded[d.day]?.groups.length ?? 0) >= d.groups && (loaded[d.day]?.groups ?? []).every((g) => sel.has(g.group_id))}
                  onTile={onTile}
                  onOpen={openViewer}
                  onMenu={onTileMenu}
                  onSelectDay={() => void selectDay({ key: d.day, groups: d.groups })}
                  filterNote=""
                  onClearFilters={clearFilters}
                  onShoot={(id) => setShoot(String(id))}
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
              className="fixed bottom-2 right-2 top-16 z-20 w-[min(380px,calc(100vw-1rem))] xl:sticky xl:top-[117px] xl:bottom-auto xl:right-auto xl:z-auto xl:w-auto xl:self-start"
            >
              <PhotoInspector selected={selected} pool={pool} settings={settings} windowS={windowS} onClear={clear} onSaved={onSaved} onRelated={onRelated} savedEpoch={savedEpoch} onShowInAll={showInAll} onOpenViewer={openViewer} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      {createPortal(
        <AnimatePresence>{viewerId !== null && viewerIndex >= 0 && <Lightbox key="viewer" list={viewerList} index={viewerIndex} onClose={closeViewer} onStep={stepViewer} />}</AnimatePresence>,
        document.body,
      )}
    </div>
  );
}

function MonthRail({ rail, undated }: { rail: ReturnType<typeof monthRail>; undated: boolean }) {
  const go = (day: string) => document.getElementById(`day-${day}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!rail.length) return <div className="hidden md:block" />;
  return (
    <nav aria-label="Jump to month" className="no-scrollbar hidden max-h-[calc(100vh-133px)] flex-col gap-2 overflow-y-auto md:sticky md:top-[117px] md:flex">
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
  flash,
  allSelected,
  onTile,
  onOpen,
  onMenu,
  onSelectDay,
  filterNote: note,
  onClearFilters,
  onShoot,
  connected,
}: {
  d: PhotoDay;
  state: DayState | undefined;
  need: (day: string, more?: boolean) => Promise<void>;
  selected: Map<number, PhotoGroup>;
  related: Set<number>;
  flash: number | null;
  allSelected: boolean;
  onTile: (g: PhotoGroup, e: MouseEvent) => void;
  onOpen: (g: PhotoGroup) => void;
  onMenu: (g: PhotoGroup, e: MouseEvent) => void;
  onSelectDay: () => void;
  filterNote: string;
  onClearFilters: () => void;
  onShoot: (shootId: number) => void;
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
      <div className="sticky top-[105px] z-10 -mx-1 flex items-center justify-between gap-3 bg-bg/85 px-1 py-2 backdrop-blur">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate font-medium">{dayLabel(d.day)}</h2>
          {(d.shoots ?? []).slice(0, 3).map((x) => (
            <button key={x.shoot_id} type="button" onClick={() => onShoot(x.shoot_id)} title={`${x.name}: ${plural(x.groups, 'photo')} this day. Show only this shoot.`} className="shrink-0 truncate rounded-full border border-line px-2 py-0.5 text-xs text-ink/90 hover:border-line-strong">
              {x.name}
            </button>
          ))}
          {(d.shoots?.length ?? 0) > 3 && <span className="shrink-0 text-xs text-muted">+{d.shoots!.length - 3}</span>}
          <span className="truncate text-xs text-muted" title={dayLine(d, groups, nSel)}>
            {dayLine(d, groups, nSel)}
          </span>
          {note && (
            <span className="hidden shrink-0 items-center gap-1 text-xs text-accent sm:inline-flex">
              {note}
              <button type="button" className="rounded-full p-0.5 hover:bg-accent/10" aria-label="Clear filters" onClick={onClearFilters}>
                <Icon name="x" size={10} />
              </button>
            </span>
          )}
        </div>
        <button type="button" className="btn-ghost h-7 shrink-0 px-2 text-xs" onClick={onSelectDay}>
          {allSelected ? 'Deselect day' : 'Select day'}
        </button>
      </div>
      {state && groups.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5 pb-3">
          {groups.map((g) => (
            <Tile key={g.group_id} g={g} on={selected.has(g.group_id)} related={related.has(g.group_id)} flash={flash === g.group_id} connected={connected(g.volume_id)} offline={fullOffline(g, connected)} onTile={onTile} onOpen={onOpen} onMenu={onMenu} />
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
