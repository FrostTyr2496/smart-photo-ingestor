import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { usePref } from '../../lib/prefs';
import { useStore } from '../../lib/store';
import {
  describeAll,
  effectivePlan,
  fromServerRows,
  matchesFilter,
  reviewSections,
  tally,
  toServerRows,
  type Decision,
  type StoredDecision,
  type EffTrip,
  type NewDecision,
  type Plan,
  type ReviewFilter,
} from '../../lib/review';
import { ShootCard } from './ShootCard';

/** The hatched frame in the warning hue that marks review as another mode. Rendered by App, outside any transform. */
export function ReviewFrame() {
  return <div className="review-frame" aria-hidden="true" />;
}

const FILTERS: { key: ReviewFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'flagged', label: 'Flagged' },
  { key: 'undecided', label: 'Undecided' },
  { key: 'accepted', label: 'Accepted' },
];

const btn = 'inline-flex h-7 items-center rounded-full border border-line px-2.5 text-xs text-muted hover:border-line-strong hover:text-ink disabled:opacity-40';
const primary = 'inline-flex h-7 items-center rounded-full border border-maybe/50 bg-maybe/15 px-3 text-xs font-medium text-maybe hover:bg-maybe/25 disabled:opacity-40';

interface Replan {
  busy: boolean;
  run: () => void;
}

/** Run the planner as a task (sh-2) and call `done` when it has written a new plan. */
function useReplan(done: () => void): Replan {
  const { startTask, toast } = useStore();
  const [busy, setBusy] = useState(false);
  const run = useCallback(() => {
    void (async () => {
      setBusy(true);
      try {
        const id = await startTask('shoots_plan', {});
        if (id === null) return;
        for (;;) {
          await new Promise((r) => window.setTimeout(r, 1000));
          const t = await api<{ state: string }>('GET', `/api/tasks/${id}?since=-1`);
          if (t.state === 'running') continue;
          if (t.state === 'done') done();
          else toast('The planner did not finish. See Tasks for its log.', 'err');
          return;
        }
      } catch (e) {
        toast((e as Error).message, 'err');
      } finally {
        setBusy(false);
      }
    })();
  }, [startTask, toast, done]);
  return { busy, run };
}

export function ReviewShoots() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    api<Plan>('GET', '/api/shoots/plan')
      .then((p) => alive && setPlan(p))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [reload]);
  const replan = useReplan(useCallback(() => setReload((n) => n + 1), []));

  if (plan) return <Review key={plan.plan_id} plan={plan} replan={replan} />;
  return (
    <div className="flex flex-col gap-4">
      <ModeBar plan={null} pending={0} replan={replan} />
      <div className="rounded-2xl border border-line bg-surface/60 p-6 text-sm text-muted">
        {err === null ? (
          'Loading the plan…'
        ) : err === 'no plan yet' ? (
          <>
            <p className="text-base text-ink">No plan yet.</p>
            <p className="mt-1">The planner reads the catalog and proposes trips and shoots by time and place. It changes nothing: you review the proposal here first.</p>
            <button type="button" className={`${primary} mt-3`} disabled={replan.busy} onClick={replan.run}>
              {replan.busy ? 'Planning…' : 'Make a plan'}
            </button>
            <p className="mt-2 text-xs">Or in a terminal: <code className="font-mono text-ink">uv run photovault --json shoots plan</code></p>
          </>
        ) : (
          `The plan could not be loaded: ${err}`
        )}
      </div>
    </div>
  );
}

function ModeBar({ plan, pending, counts, replan }: { plan: Plan | null; pending: number; counts?: ReturnType<typeof tally>; replan: Replan }) {
  return (
    <div className="sticky top-14 z-20 -mx-1 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-maybe/50 bg-bg/90 px-4 py-2.5 backdrop-blur-xl" role="status">
      <span className="inline-flex items-center gap-2 text-sm font-semibold text-maybe">
        <span className="h-2 w-2 rounded-full bg-maybe" aria-hidden="true" />
        Dry run — nothing changes until you Apply · {pending} {pending === 1 ? 'decision' : 'decisions'} pending
      </span>
      {plan && counts && (
        <span className="text-xs text-muted">
          Plan from {plan.created_at.slice(0, 16).replace('T', ' ')} · {counts.trips} trips · {counts.shoots} shoots · {plan.summary.frames.toLocaleString()} frames · {counts.flagged} flagged
        </span>
      )}
      <span className="ml-auto flex items-center gap-2">
        {plan && (
          <button type="button" className={btn} disabled={replan.busy} onClick={replan.run} title="Plan again from the catalog, with every saved decision folded in">
            {replan.busy ? 'Planning…' : 'Re-plan'}
          </button>
        )}
        <button type="button" disabled className={primary} title="Apply arrives with sh-4">
          Apply
        </button>
        <a href="#/photos" className={btn}>
          Exit review
        </a>
      </span>
    </div>
  );
}

let batchSeq = 0;
const newBatch = (): string => `b${Date.now().toString(36)}${(batchSeq++).toString(36)}`;

function Review({ plan, replan }: { plan: Plan; replan: Replan }) {
  // sh-2: the decisions made after this plan, from the catalog. Earlier ones are already part of the plan.
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [dev, setDev] = useState(false);
  const { toast } = useStore();
  const [filter, setFilter] = useState<ReviewFilter>('all');
  const eff = useMemo(() => effectivePlan(plan, decisions), [plan, decisions]);
  // The saves run one after another; each reads the list as the previous save left it, not as it was at the click.
  const decRef = useRef<Decision[]>([]);
  const commit = (next: Decision[]) => {
    decRef.current = next;
    setDecisions(next);
  };
  const lines = useMemo(() => describeAll(plan, decisions), [plan, decisions]);
  const counts = tally(eff);
  useEffect(() => {
    let alive = true;
    api<{ decisions: StoredDecision[]; dev?: boolean }>('GET', `/api/shoots/decisions?after=${plan.decisions_through ?? 0}`)
      .then((r) => {
        if (!alive) return;
        commit(fromServerRows(r.decisions));
        setDev(Boolean(r.dev));
      })
      .catch((e: Error) => alive && setLoadErr(e.message));
    return () => {
      alive = false;
    };
  }, [plan]);
  // One save at a time, so the stored order is the order of the clicks.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const enqueue = (job: () => Promise<void>) => {
    queue.current = queue.current.then(job).catch((e: Error) => toast(`Not saved: ${e.message}`, 'err'));
  };
  const decide = (d: NewDecision) =>
    enqueue(async () => {
      const rows = toServerRows(d, effectivePlan(plan, decRef.current), newBatch());
      if (rows.length === 0) {
        toast('Nothing to accept.');
        return;
      }
      const r = await api<{ decisions: StoredDecision[] }>('POST', '/api/shoots/decisions', { decisions: rows });
      const ids = r.decisions.map((x) => x.decision_id);
      commit([...decRef.current, { ...d, seq: ids[0]!, rows: ids } as Decision]);
    });
  const undo = (seq: number) =>
    enqueue(async () => {
      const d = decRef.current.find((x) => x.seq === seq);
      if (!d) return;
      await api('POST', '/api/shoots/decisions/delete', { decision_ids: d.rows ?? [d.seq] });
      commit(decRef.current.filter((x) => x.seq !== seq));
    });
  const [connectedFirst, setConnectedFirst] = usePref<boolean>('review.shoots.connectedFirst', true);
  const { drives } = useStore();
  const mountedKey = drives.filter((d) => d.mounted && d.volume_id).map((d) => d.volume_id).sort().join('|');
  const mounted = useMemo(() => new Set(mountedKey ? mountedKey.split('|') : []), [mountedKey]);
  const sections = useMemo(() => reviewSections(eff, mounted, connectedFirst), [eff, mounted, connectedFirst]);

  return (
    <div className="flex flex-col gap-4">
      <ModeBar plan={plan} pending={decisions.length} counts={counts} replan={replan} />
      {loadErr && <p className="text-sm text-err">The saved decisions could not be loaded: {loadErr}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review shoots</h1>
          <p className="mt-1 text-sm text-muted">
            {counts.accepted} accepted · {counts.skipped} skipped · {counts.undecided} undecided
          </p>
        </div>
        <button type="button" className={primary} onClick={() => decide({ kind: 'accept_all_unflagged' })} title="Accept every undecided shoot that has no flags">
          Accept all unflagged
        </button>
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-6">
          {sections.map((sec) => {
            const list = sec.shoots.filter((s) => matchesFilter(s, filter));
            if (list.length === 0) return null;
            return sec.trip ? (
              <TripSection key={sec.trip.proposal_id} trip={sec.trip} shoots={list} eff={eff} decide={decide} mounted={mounted} />
            ) : (
              <section key="no-trip" className="flex flex-col gap-3">
                <h2 className="text-lg font-semibold">Not in a trip</h2>
                {list.map((s) => (
                  <ShootCard key={s.proposal_id} s={s} plan={eff} onDecide={decide} mounted={mounted} />
                ))}
              </section>
            );
          })}
          {eff.shoots.every((s) => !matchesFilter(s, filter)) && <p className="text-sm text-muted">Nothing matches this filter.</p>}
        </div>
        <aside className="flex flex-col gap-4 rounded-2xl border border-maybe/30 bg-surface/70 p-4 lg:sticky lg:top-32" aria-label="Decisions">
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter shoots">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)} className={`h-7 rounded-full px-3 text-xs ${filter === f.key ? 'bg-maybe/20 text-maybe' : 'text-muted hover:text-ink'}`}>
                {f.label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs text-muted" title="Shoots whose photos sit on a connected drive can show previews">
            <input type="checkbox" checked={connectedFirst} onChange={(e) => setConnectedFirst(e.target.checked)} />
            Connected drives first{mounted.size === 0 ? ' (none connected)' : ''}
          </label>
          <div>
            <h2 className="text-sm font-semibold">Pending decisions ({decisions.length})</h2>
            {lines.length === 0 ? (
              <p className="mt-1 text-xs text-muted">None yet. Each action below is recorded here and can be undone.</p>
            ) : (
              <ol className="mt-2 flex max-h-[50vh] flex-col gap-1 overflow-y-auto">
                {lines.map((l, i) => (
                  <li key={l.seq} className="flex items-start justify-between gap-2 rounded-lg px-2 py-1 text-xs hover:bg-ink/5">
                    <span className="min-w-0"><span className="text-faint">{i + 1}.</span> {l.text}</span>
                    <button type="button" className="shrink-0 text-muted underline hover:text-ink" onClick={() => undo(l.seq)}>
                      Undo
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <p className="text-2xs text-faint">
            {dev
              ? 'Fixture plan: decisions are kept in server memory only and go when the server stops.'
              : 'Each decision is saved in the catalog as you make it. Re-plan folds them into a new plan; nothing else changes until Apply.'}
          </p>
        </aside>
      </div>
    </div>
  );
}

function TripSection({ trip, shoots, eff, decide, mounted }: { trip: EffTrip; shoots: ReturnType<typeof effectivePlan>['shoots']; eff: ReturnType<typeof effectivePlan>; decide: (d: NewDecision) => void; mounted: ReadonlySet<string> }) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(trip.name.value);
  const all = eff.shoots.filter((s) => s.trip_id === trip.proposal_id);
  const accepted = all.length > 0 && all.every((s) => s.state === 'accepted' || s.locked);
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line-strong bg-raised/30 p-4" aria-label={trip.name.value}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          {renaming ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                decide({ kind: 'rename', id: trip.proposal_id, value: draft });
                setRenaming(false);
              }}
            >
              <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} className="h-8 min-w-0" aria-label="Trip name" />
              <button type="submit" className={primary}>Save</button>
              <button type="button" className={btn} onClick={() => setRenaming(false)}>Cancel</button>
            </form>
          ) : (
            <h2 className="text-xl font-semibold">
              {trip.name.value} {accepted && <span className="text-sm font-normal text-go">✓ accepted</span>}
            </h2>
          )}
          <p className="text-xs text-muted">
            {trip.start_date} – {trip.end_date} · {all.length} {all.length === 1 ? 'shoot' : 'shoots'}
          </p>
          {trip.name.alternatives.length > 0 && !renaming && (
            <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
              Other names
              {trip.name.alternatives.map((a) => (
                <button key={a.value} type="button" className="rounded-full border border-line px-2 py-0.5 hover:text-ink" onClick={() => decide({ kind: 'rename', id: trip.proposal_id, value: a.value, source: a.source })}>
                  {a.value}
                </button>
              ))}
            </p>
          )}
        </div>
        <div className="flex gap-1.5">
          <button type="button" className={btn} onClick={() => { setDraft(trip.name.value); setRenaming(true); }}>Rename</button>
          <button type="button" className={primary} disabled={accepted} onClick={() => decide({ kind: 'accept_trip', id: trip.proposal_id })}>Accept trip</button>
        </div>
      </header>
      {shoots.map((s) => (
        <ShootCard key={s.proposal_id} s={s} plan={eff} onDecide={decide} mounted={mounted} />
      ))}
    </section>
  );
}
