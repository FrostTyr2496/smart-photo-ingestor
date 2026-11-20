import { useMemo, useState } from 'react';
import type { EffPlan, EffShoot, NewDecision, PlaceArea, PlaceMember, PlaceSpot, Plan } from '../../lib/review';
import { DETAIL_SOURCE_LABEL, fade, stackTitle } from '../../lib/stacks';
import { MiniMap } from './MiniMap';

const btn = 'inline-flex h-7 items-center rounded-full border border-line px-2.5 text-xs text-muted hover:border-line-strong hover:text-ink disabled:opacity-40';
const primary = 'inline-flex h-7 items-center rounded-full border border-maybe/50 bg-maybe/15 px-3 text-xs font-medium text-maybe hover:bg-maybe/25 disabled:opacity-40';

const years = (a: string, b: string): string => (a.slice(0, 4) === b.slice(0, 4) ? a.slice(0, 4) : `${a.slice(0, 4)}–${b.slice(0, 4)}`);

/** "iPhone venue · 4 of 7": the name's source and how many of the shoots carry it. */
export function nameSourceLine(spot: Pick<PlaceSpot, 'name' | 'members'>): string {
  const src = spot.name.source ? (DETAIL_SOURCE_LABEL[spot.name.source] ?? (spot.name.source === 'user' ? 'you' : spot.name.source.replace(/_/g, ' '))) : 'no name';
  return `${src} · ${spot.name.count} of ${spot.members.length}`;
}

/** Members start ticked at or above the pre-tick cut-off; beta members below the show cut-off and applied shoots do not. */
export function initialTicks(members: PlaceMember[], shoots: Map<string, EffShoot>, pretick: number): Set<string> {
  return new Set(members.filter((m) => !m.beyond_cutoff && m.confidence >= pretick && shoots.get(m.proposal_id)?.locked === false).map((m) => m.proposal_id));
}

function Member({ m, s, ticked, onTick, onOpen }: { m: PlaceMember; s: EffShoot; ticked: boolean; onTick: (on: boolean) => void; onOpen: () => void }) {
  const t = stackTitle(s);
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line px-2 py-1.5 text-sm" style={{ opacity: fade(m.confidence) }} title={`Confidence ${Math.round(m.confidence * 100)}%${m.via && m.via !== 'coords' ? ` · joined by ${m.via}` : ''}`}>
      <input type="checkbox" checked={ticked} disabled={s.locked} onChange={(e) => onTick(e.target.checked)} aria-label={`Include ${t.date} ${t.name}`} />
      <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" onClick={onOpen} title="Open this shoot's stack">
        <span className="text-muted">{t.date}</span> · {t.place ?? <em className="text-faint">no place</em>} · {t.subject ?? t.name}
        {s.locked && <span className="ml-2 text-xs text-faint">applied</span>}
        {s.state === 'accepted' && !s.locked && <span className="ml-2 text-xs text-go">✓</span>}
      </button>
      <span className="text-xs text-muted">{s.counts.frames.toLocaleString()} frames</span>
      <span className="text-xs text-faint">{'●'.repeat(Math.round(m.confidence * 4)).padEnd(4, '○')}</span>
    </div>
  );
}

function SpotCard({ spot, shoots, pretick, beta, decide, onOpen }: { spot: PlaceSpot; shoots: Map<string, EffShoot>; pretick: number; beta: boolean; decide: (d: NewDecision) => void; onOpen: (id: string) => void }) {
  const members = spot.members.filter((m) => shoots.has(m.proposal_id));
  const [ticks, setTicks] = useState(() => initialTicks(members, shoots, pretick));
  const [name, setName] = useState(spot.name.value ?? '');
  const normal = members.filter((m) => !m.beyond_cutoff);
  const faint = members.filter((m) => m.beyond_cutoff);
  const pins = useMemo(() => members.flatMap((m) => shoots.get(m.proposal_id)?.centroid ?? []).map((c): [number, number] => [c.lat, c.lon]), [members, shoots]);
  const ids = members.filter((m) => ticks.has(m.proposal_id)).map((m) => m.proposal_id);
  const tick = (id: string, on: boolean) => setTicks((cur) => { const n = new Set(cur); if (on) n.add(id); else n.delete(id); return n; });
  const row = (m: PlaceMember) => <Member key={m.proposal_id} m={m} s={shoots.get(m.proposal_id)!} ticked={ticks.has(m.proposal_id)} onTick={(on) => tick(m.proposal_id, on)} onOpen={() => onOpen(m.proposal_id)} />;
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line-strong bg-raised/30 p-4" aria-label={spot.name.value ?? 'Unnamed place'}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-semibold">{spot.name.value ?? <em className="font-normal text-faint">No name yet</em>}</h3>
          <p className="text-xs text-muted">
            {spot.members.length} shoots · {years(spot.first_date, spot.last_date)} · {spot.frames.toLocaleString()} frames · {nameSourceLine(spot)}
          </p>
          {spot.name.alternatives.length > 0 && (
            <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
              Other names
              {spot.name.alternatives.map((a) => (
                <button key={a.value} type="button" className="rounded-full border border-line px-2 py-0.5 hover:text-ink" onClick={() => setName(a.value)} title={`${DETAIL_SOURCE_LABEL[a.source] ?? a.source} · ${a.count} ${a.count === 1 ? 'shoot' : 'shoots'}`}>
                  {a.value} · {a.count}
                </button>
              ))}
            </p>
          )}
        </div>
        <div className="w-full sm:w-56">
          <MiniMap points={pins} />
        </div>
      </header>
      <div className="flex flex-col gap-1.5">
        {normal.map(row)}
        {beta && faint.length > 0 && (
          <>
            <div className="flex items-center gap-2 text-2xs text-faint" role="separator">
              <span className="flex-1 border-t border-dashed border-line-strong" />
              beyond cut-off (beta)
              <span className="flex-1 border-t border-dashed border-line-strong" />
            </div>
            {faint.map(row)}
          </>
        )}
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && ids.length > 0) decide({ kind: 'name_place', ids, value: name.trim() });
        }}
      >
        <input value={name} onChange={(e) => setName(e.target.value)} className="h-8 min-w-0 flex-1" aria-label="Place name" placeholder="Place name" />
        <button type="submit" className={primary} disabled={!name.trim() || ids.length === 0} title="One place decision per ticked shoot, undone together">
          Name this place for {ids.length} {ids.length === 1 ? 'shoot' : 'shoots'}
        </button>
        <button type="button" className={btn} onClick={() => setTicks(new Set())} disabled={ticks.size === 0}>Untick all</button>
      </form>
    </section>
  );
}

/** Place mode (pl-2): the plan's place collections, biggest first, areas as headers, home collapsed. */
export function PlacesPane({ plan, eff, pretick, beta, decide, onOpen }: { plan: Plan; eff: EffPlan; pretick: number; beta: boolean; decide: (d: NewDecision) => void; onOpen: (id: string) => void }) {
  const shoots = useMemo(() => new Map(eff.shoots.map((s) => [s.proposal_id, s])), [eff]);
  const places = plan.places;
  const groups = useMemo(() => {
    if (!places) return [];
    const byArea = new Map<string, PlaceSpot[]>();
    for (const sp of places.spots.filter((x) => !x.home)) byArea.set(sp.area_key ?? sp.key, [...(byArea.get(sp.area_key ?? sp.key) ?? []), sp]);
    const areas = new Map<string, PlaceArea>(places.areas.map((a) => [a.key, a]));
    return [...byArea.entries()]
      .map(([key, spots]) => ({ area: areas.get(key) ?? null, spots, n: spots.reduce((t, s) => t + s.members.length, 0) }))
      .sort((a, b) => b.n - a.n);
  }, [places]);
  if (!places) return <p className="text-sm text-muted">This plan has no place collections. Re-plan to make them.</p>;
  if (places.spots.length === 0) return <p className="text-sm text-muted">No place was visited by two or more shoots.</p>;
  const home = places.spots.filter((x) => x.home);
  const card = (sp: PlaceSpot) => <SpotCard key={sp.key} spot={sp} shoots={shoots} pretick={pretick} beta={beta} decide={decide} onOpen={onOpen} />;
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) =>
        g.spots.length > 1 && g.area ? (
          <section key={g.area.key} className="flex flex-col gap-3">
            <h2 className="text-xl font-semibold">
              {g.area.name.value ?? 'Area'} <span className="text-sm font-normal text-muted">area · {g.spots.length} places · {g.n} shoots</span>
            </h2>
            {g.spots.map(card)}
          </section>
        ) : (
          g.spots.map(card)
        ),
      )}
      {home.length > 0 && (
        <details className="text-sm text-muted">
          <summary className="cursor-pointer">Home (hidden)</summary>
          <div className="mt-2 flex flex-col gap-3">{home.map(card)}</div>
        </details>
      )}
    </div>
  );
}
