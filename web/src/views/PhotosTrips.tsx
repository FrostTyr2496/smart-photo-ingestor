import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { fmtInt } from '../lib/format';
import { countsLine, railOf, shootLine, titleShade, tripLine, type OutlineItem, type OutlineShoot, type OutlineTrip } from '../lib/outline';
import { dayLabel, dayOf, fullOffline, type PhotoGroup } from '../lib/photos';
import { PAGE, Tile, type DayState } from './PhotoTile';

/** What the trips view needs from Photos: the shared selection, loaded sections and handlers. */
export interface TripsShared {
  loaded: Record<string, DayState>;
  need: (key: string, more?: boolean) => Promise<void>;
  selected: Map<number, PhotoGroup>;
  related: Set<number>;
  flash: number | null;
  onTile: (g: PhotoGroup, e: MouseEvent) => void;
  onOpen: (g: PhotoGroup) => void;
  onMenu: (g: PhotoGroup, e: MouseEvent) => void;
  onSelectSection: (key: string, groups: number) => void;
  connected: (volumeId: string | null) => boolean;
}

/** Scroll to a section of the trips view by its key. */
export const goToSection = (key: string, smooth = true): void =>
  document.getElementById(`sec-${key}`)?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });

interface SectionProps extends TripsShared {
  /** The key the grid loads by: a shoot key or `loose:<day>`. */
  id: string;
  count: number;
  header: React.ReactNode;
  /** Small day dividers inside the grid (a shoot that spans several days). */
  dividers: boolean;
}

/** One lazily loaded grid with a sticky header: a shoot, or one loose day. Loads when it scrolls near. */
function Section({ id, count, header, dividers, loaded, need, selected, related, flash, onTile, onOpen, onMenu, onSelectSection, connected }: SectionProps) {
  const box = useRef<HTMLElement>(null);
  const state = loaded[id];
  useEffect(() => {
    if (state || !box.current) return undefined;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && void need(id), { rootMargin: '600px 0px' });
    io.observe(box.current);
    return () => io.disconnect();
  }, [state, need, id]);

  const groups = state?.groups ?? [];
  const left = count - groups.length;
  const allIn = groups.length >= count && groups.length > 0 && groups.every((g) => selected.has(g.group_id));
  const cells: React.ReactNode[] = [];
  let prev = '';
  for (const g of groups) {
    const day = dayOf(g);
    if (dividers && day !== prev) {
      cells.push(
        <div key={`d-${day}`} className="col-span-full mt-1 text-2xs font-medium uppercase tracking-wide text-faint first:mt-0">
          {dayLabel(day)}
        </div>,
      );
    }
    prev = day;
    cells.push(
      <Tile key={g.group_id} g={g} on={selected.has(g.group_id)} related={related.has(g.group_id)} flash={flash === g.group_id} connected={connected(g.volume_id)} offline={fullOffline(g, connected)} onTile={onTile} onOpen={onOpen} onMenu={onMenu} />,
    );
  }
  return (
    <section ref={box} id={`sec-${id}`} className="scroll-mt-[64px]">
      <div className="sticky top-[105px] z-10 -mx-1 flex items-center justify-between gap-3 bg-bg/85 px-1 py-2 backdrop-blur">
        <div className="min-w-0">{header}</div>
        <button type="button" className="btn-ghost h-7 shrink-0 px-2 text-xs" onClick={() => onSelectSection(id, count)}>
          {allIn ? 'Deselect' : 'Select all'}
        </button>
      </div>
      {state && groups.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5 pb-3">{cells}</div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1.5 pb-3" style={{ minHeight: Math.min(4, Math.ceil(Math.min(count, PAGE) / 6)) * 120 }} aria-hidden="true">
          {Array.from({ length: Math.min(count, 12) }, (_, i) => (
            <div key={i} className="aspect-square animate-pulse rounded-lg bg-ink/5" />
          ))}
        </div>
      )}
      {state?.error && (
        <p className="pb-3 text-sm text-err">
          {state.error}{' '}
          <button type="button" className="underline" onClick={() => void need(id, true)}>
            Retry
          </button>
        </p>
      )}
      {state && !state.loading && left > 0 && groups.length > 0 && (
        <button type="button" className="btn mb-3" onClick={() => void need(id, true)}>
          Show {fmtInt(Math.min(left, PAGE))} more{left > PAGE ? ` of ${fmtInt(left)}` : ''}
        </button>
      )}
    </section>
  );
}

function ShootSection({ s, shared }: { s: OutlineShoot; shared: TripsShared }) {
  return (
    <Section
      id={s.key}
      count={s.groups}
      dividers={s.days.length > 1}
      {...shared}
      header={
        <>
          <h3 className={`truncate text-base font-medium ${titleShade(s.state, s.confidence, s.suggested).className}`} style={titleShade(s.state, s.confidence, s.suggested).style} title={titleShade(s.state, s.confidence, s.suggested).hint}>{s.title}</h3>
          <p className="truncate text-xs text-muted" title={shootLine(s)}>
            {[shootLine(s), countsLine(s)].filter(Boolean).join(' · ')}
          </p>
        </>
      }
    />
  );
}

function TripBlock({ t, shared }: { t: OutlineTrip; shared: TripsShared }) {
  return (
    <div id={`sec-${t.key}`} className="mt-6 flex scroll-mt-[64px] flex-col gap-1 first:mt-0">
      <header className="border-b border-line pb-2">
        <h2 className={`text-xl font-semibold tracking-tight ${titleShade(t.state, t.confidence).className}`} style={titleShade(t.state, t.confidence).style} title={titleShade(t.state, t.confidence).hint}>{t.name}</h2>
        <p className="mt-0.5 text-sm text-muted">{tripLine(t)}</p>
      </header>
      {t.shoots.map((s) => (
        <ShootSection key={s.key} s={s} shared={shared} />
      ))}
    </div>
  );
}

/** The Trips view of Photos (rv-47): trips with their shoots, lone shoots, and loose days, newest first. */
export function TripsView({ items, shared }: { items: OutlineItem[]; shared: TripsShared }) {
  return (
    <div className="flex flex-col gap-1">
      {items.map((it) =>
        it.kind === 'trip' ? (
          <TripBlock key={it.key} t={it} shared={shared} />
        ) : it.kind === 'shoot' ? (
          <div key={it.key} className="mt-6 first:mt-0">
            <ShootSection s={it} shared={shared} />
          </div>
        ) : (
          <Section
            key={it.key}
            id={it.key}
            count={it.groups}
            dividers={false}
            {...shared}
            header={
              <h3 className="truncate font-medium">
                {dayLabel(it.day)} <span className="text-xs font-normal text-muted">· {countsLine(it)}</span>
              </h3>
            }
          />
        ),
      )}
    </div>
  );
}

/** The left rail of the trips view: years, then that year's trips and lone shoots by name, each with its dates and
 * photo count. Every row is one line (two for a named trip) in both states, so hovering (or focusing) only widens the
 * rail into a floating pane over the grid that shows more of each name (keyboard focus too, not a mouse click's focus): nothing moves up or down, and the grid never
 * reflows. The sticky wrapper carries the z-index (sticky makes its own stacking
 * context), above the section headers (z-10) and below the filter bar (z-20). Unnamed shoots show date · count.
 * The rail follows the grid: the entry holding the selected photo, or else the one under the sticky headers, is
 * highlighted and kept in view. */
export function TripsRail({ items, focusId }: { items: OutlineItem[]; focusId?: number | null }) {
  const { years, undated } = useMemo(() => railOf(items), [items]);
  const keys = useMemo(() => new Set(years.flatMap((y) => y.entries.map((e) => e.key))), [years]);
  const [active, setActive] = useState<string | null>(null);
  const nav = useRef<HTMLElement>(null);

  // Scroll spy: the entry whose section spans the line just under the sticky headers. A loose day between entries
  // highlights nothing.
  useEffect(() => {
    let frame = 0;
    const spy = () => {
      frame = 0;
      const line = 170;
      let hit: string | null = null;
      for (const k of keys) {
        const r = document.getElementById(`sec-${k}`)?.getBoundingClientRect();
        if (r && r.top <= line && r.bottom > line) {
          hit = k;
          break;
        }
      }
      setActive(hit);
    };
    const on = () => {
      if (!frame) frame = requestAnimationFrame(spy);
    };
    spy();
    window.addEventListener('scroll', on, { passive: true });
    return () => {
      window.removeEventListener('scroll', on);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [keys]);

  // A selected photo wins: its trip, or its lone shoot.
  useEffect(() => {
    if (focusId == null) return;
    let el: HTMLElement | null = document.querySelector<HTMLElement>(`[data-gid="${focusId}"]`);
    while (el) {
      if (el.id.startsWith('sec-') && keys.has(el.id.slice(4))) {
        setActive(el.id.slice(4));
        return;
      }
      el = el.parentElement;
    }
  }, [focusId, keys]);

  // Keep the active entry in view in the rail, unless the pointer is browsing the rail.
  useEffect(() => {
    const box = nav.current;
    if (!active || !box || box.matches(':hover')) return;
    const btn = box.querySelector<HTMLElement>(`[data-rail="${CSS.escape(active)}"]`);
    if (!btn) return;
    const top = btn.offsetTop;
    if (top < box.scrollTop + 24 || top + btn.offsetHeight > box.scrollTop + box.clientHeight - 24) {
      box.scrollTo({ top: top - box.clientHeight / 3, behavior: 'smooth' });
    }
  }, [active]);

  if (!years.length && !undated) return <div className="hidden md:block" />;
  return (
    <div className="relative hidden md:sticky md:top-[117px] md:z-[15] md:block md:h-[calc(100vh-133px)]">
      <nav
        ref={nav}
        aria-label="Jump to a trip"
        className="group/rail no-scrollbar absolute inset-y-0 left-0 z-30 flex w-full flex-col gap-2 overflow-y-auto rounded-xl px-1 py-1 transition-[width,background-color,box-shadow] duration-150 hover:w-96 hover:bg-surface/95 hover:shadow-2xl hover:ring-1 hover:ring-line has-[:focus-visible]:w-96 has-[:focus-visible]:bg-surface/95 has-[:focus-visible]:shadow-2xl has-[:focus-visible]:ring-1 has-[:focus-visible]:ring-line hover:backdrop-blur-xl"
      >
        {years.map((y) => (
          <div key={y.year} className="flex flex-col">
            <button type="button" className="label py-1 text-left hover:text-ink" onClick={() => goToSection(y.first)}>
              {y.year}
            </button>
            {y.entries.map((e) => (
              <button
                key={e.key}
                type="button"
                data-rail={e.key}
                aria-current={active === e.key ? 'location' : undefined}
                onClick={() => goToSection(e.key)}
                title={`${e.label} · ${e.sub} · ${titleShade(e.state, e.confidence, e.suggested).hint}`}
                style={titleShade(e.state, e.confidence, e.suggested).style}
                className={`rounded px-1 py-0.5 text-left text-xs leading-snug hover:bg-ink/5 ${active === e.key ? 'bg-ink/10 shadow-[inset_2px_0_0_rgb(var(--c-accent))]' : ''} ${e.state === 'accepted' && !e.suggested ? 'text-muted hover:text-ink' : 'hover:brightness-125'}`}
              >
                {e.label ? (
                  <>
                    <span className="block truncate">{e.label}</span>
                    <span className="block truncate text-2xs text-faint">{e.sub}</span>
                  </>
                ) : (
                  // An unnamed shoot: its date and count are the label, so the row is never blank and the rail does not jump on hover.
                  <span className="block truncate text-2xs text-faint">{e.sub}</span>
                )}
              </button>
            ))}
          </div>
        ))}
        {undated && (
          <button type="button" className="label py-1 text-left hover:text-ink" onClick={() => goToSection(undated)}>
            No date
          </button>
        )}
      </nav>
    </div>
  );
}

