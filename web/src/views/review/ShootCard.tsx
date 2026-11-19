import { useMemo, useState } from 'react';
import { FLAG_INFO, NAME_SOURCE_LABEL, mergeShoots, neighbour, splitShoot, type EffPlan, type EffShoot, type NewDecision } from '../../lib/review';
import { MiniMap } from './MiniMap';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const minus = (s: string): string => s.replace('-', '−');

/** "9 Mar 20:05" from a wall-clock ISO string, read as written (no time-zone conversion). */
function wallText(iso: string | null, withDate = true): string {
  if (!iso || iso.length < 16) return '';
  const day = `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1] ?? ''}`;
  return withDate ? `${day} ${iso.slice(11, 16)}` : iso.slice(11, 16);
}

export function datesLine(s: EffShoot): string {
  const a = s.wall.start;
  const b = s.wall.end;
  if (a && b) return a.slice(0, 10) === b.slice(0, 10) ? `${wallText(a)} – ${wallText(b, false)}` : `${wallText(a)} – ${wallText(b)}`;
  const one = a ?? b;
  return one ? `${a ? 'from' : 'to'} ${wallText(one)}` : s.shoot_date;
}

export function gapText(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return h ? `${h} h${m ? ` ${m} min` : ''}` : `${m} min`;
}

export function Thumbs({ ids }: { ids: number[] }) {
  return (
    <div className="flex gap-1 overflow-x-auto no-scrollbar" aria-label="Sample photos">
      {ids.map((id) => (
        <Thumb key={id} id={id} />
      ))}
    </div>
  );
}

function Thumb({ id }: { id: number }) {
  const [bad, setBad] = useState(false);
  return bad ? (
    <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-md bg-ink/5 text-2xs text-faint" title={`Photo ${id}: no preview`}>
      no preview
    </div>
  ) : (
    <img src={`/api/groups/${id}/thumb`} loading="lazy" alt="" onError={() => setBad(true)} className="h-16 w-16 shrink-0 rounded-md bg-ink/5 object-cover" />
  );
}

const btn = 'inline-flex h-7 items-center rounded-full border border-line px-2.5 text-xs text-muted hover:border-line-strong hover:text-ink disabled:opacity-40 disabled:hover:text-muted';
const primary = 'inline-flex h-7 items-center rounded-full border border-maybe/50 bg-maybe/15 px-3 text-xs font-medium text-maybe hover:bg-maybe/25';

function Counts({ s }: { s: EffShoot }) {
  const bodies = Object.entries(s.counts.by_body);
  return (
    <p className="text-xs text-muted">
      <span className="text-ink">{s.counts.frames.toLocaleString()}</span> {s.counts.frames === 1 ? 'frame' : 'frames'}
      {bodies.length > 0 && <> · {bodies.map(([b, n]) => `${b} ${n.toLocaleString()}`).join(', ')}</>} · located {s.counts.located.toLocaleString()}/{s.counts.frames.toLocaleString()}
      {s.derived && <span title="Made by a split or merge here: counts and times are shared by photo count until Apply."> (estimate)</span>}
    </p>
  );
}

/** A compact preview row for the result of a split or merge. */
function PreviewRow({ s }: { s: EffShoot }) {
  return (
    <div className="rounded-lg border border-maybe/30 bg-maybe/5 p-2.5">
      <p className="text-sm font-medium">{s.name.value}</p>
      <p className="text-xs text-muted">{datesLine(s)} · {s.group_ids.length} photos</p>
      <Counts s={s} />
    </div>
  );
}

/** The timeline of a shoot with its gaps as bars sized by gap length; click one to preview the split. */
function GapStrip({ s, pick, active }: { s: EffShoot; pick: (g: number) => void; active: number | null }) {
  const gaps = useMemo(() => s.gaps.filter((g) => s.group_ids.indexOf(g.after_group_id) >= 0 && s.group_ids.indexOf(g.after_group_id) < s.group_ids.length - 1).sort((a, b) => s.group_ids.indexOf(a.after_group_id) - s.group_ids.indexOf(b.after_group_id)), [s]);
  if (gaps.length === 0) return <p className="text-xs text-faint">No gaps to split at.</p>;
  const max = Math.max(...gaps.map((g) => g.gap_s));
  let prev = -1;
  const parts = gaps.flatMap((g) => {
    const idx = s.group_ids.indexOf(g.after_group_id);
    const run = idx - prev;
    prev = idx;
    return [{ kind: 'run' as const, w: run, key: `r${g.after_group_id}` }, { kind: 'gap' as const, g, key: `g${g.after_group_id}` }];
  });
  parts.push({ kind: 'run', w: s.group_ids.length - 1 - prev, key: 'rend' });
  return (
    <div className="flex h-9 items-stretch gap-0.5" role="group" aria-label="Gaps in this shoot">
      {parts.map((p) =>
        p.kind === 'run' ? (
          <div key={p.key} style={{ flex: `${p.w} 1 0` }} className="min-w-[4px] self-center h-3 rounded-sm bg-ink/15" />
        ) : (
          <button
            key={p.key}
            type="button"
            onClick={() => pick(p.g.after_group_id)}
            title={`Gap of ${gapText(p.g.gap_s)} after photo ${p.g.after_group_id}: preview a split here`}
            style={{ width: `${10 + Math.round((p.g.gap_s / max) * 30)}px` }}
            className={`shrink-0 rounded-sm border text-2xs ${active === p.g.after_group_id ? 'border-maybe bg-maybe/40' : 'border-maybe/40 bg-maybe/15 hover:bg-maybe/30'}`}
            aria-label={`Gap of ${gapText(p.g.gap_s)}`}
          />
        ),
      )}
    </div>
  );
}

export function ShootCard({ s, plan, onDecide }: { s: EffShoot; plan: EffPlan; onDecide: (d: NewDecision) => void }) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(s.name.value);
  const [splitting, setSplitting] = useState(false);
  const [cut, setCut] = useState<number | null>(null);
  const [mergeDir, setMergeDir] = useState<'prev' | 'next' | null>(null);
  const id = s.proposal_id;
  const prev = neighbour(plan, id, 'prev');
  const next = neighbour(plan, id, 'next');
  const halves = cut !== null ? splitShoot(s, cut) : null;
  const other = mergeDir === 'prev' ? prev : mergeDir === 'next' ? next : null;
  const merged = other ? (mergeDir === 'prev' ? mergeShoots(other, s) : mergeShoots(s, other)) : null;
  const off = s.local_offset;
  const tripChoices = plan.trips.filter((t) => t.proposal_id !== s.trip_id);
  const ring = s.state === 'accepted' ? 'border-go/40' : s.state === 'skipped' ? 'border-line opacity-60' : 'border-line';

  const close = () => {
    setSplitting(false);
    setCut(null);
    setMergeDir(null);
  };

  return (
    <article className={`flex flex-col gap-3 rounded-2xl border bg-surface/70 p-4 ${ring}`} aria-label={s.name.value}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {renaming ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                onDecide({ kind: 'rename', id, value: draft });
                setRenaming(false);
              }}
            >
              <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} className="h-8 min-w-0 flex-1" aria-label="Shoot name" />
              <button type="submit" className={primary}>Save</button>
              <button type="button" className={btn} onClick={() => setRenaming(false)}>Cancel</button>
            </form>
          ) : (
            <h3 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
              {s.name.value}
              <span className="rounded bg-ink/10 px-1.5 text-2xs font-normal text-muted" title="Where this name came from">{NAME_SOURCE_LABEL[s.name.source] ?? s.name.source}</span>
            </h3>
          )}
          {s.name.alternatives.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span className="text-2xs text-faint">Other names</span>
              {s.name.alternatives.map((a) => (
                <span key={a.value} className="inline-flex items-center rounded-full border border-line text-xs">
                  <button type="button" disabled={s.locked} className="rounded-l-full px-2 py-0.5 hover:bg-ink/5" title={`Use "${a.value}"`} onClick={() => onDecide({ kind: 'rename', id, value: a.value, source: a.source })}>
                    {a.value} <span className="text-2xs text-faint">{NAME_SOURCE_LABEL[a.source] ?? a.source}</span>
                  </button>
                  <button type="button" disabled={s.locked} className="rounded-r-full px-1.5 py-0.5 text-faint hover:text-err" title={`Reject "${a.value}" (adds it to the stoplist)`} aria-label={`Reject ${a.value}`} onClick={() => onDecide({ kind: 'reject_name', id, value: a.value })}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {s.locked && <span className="rounded-full bg-ink/10 px-2 py-0.5 text-muted" title="Already accepted in an earlier Apply. Shown read-only.">locked</span>}
          {s.state === 'accepted' && <span className="rounded-full bg-go/15 px-2 py-0.5 text-go">✓ accepted</span>}
          {s.state === 'skipped' && <span className="rounded-full bg-ink/10 px-2 py-0.5 text-muted">skipped</span>}
          {s.renamed && <span className="rounded-full bg-maybe/15 px-2 py-0.5 text-maybe">renamed</span>}
          {s.derived && <span className="rounded-full bg-maybe/15 px-2 py-0.5 text-maybe" title="Created by a split or merge in this review">split / merged</span>}
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>{datesLine(s)}</span>
        {off.changes && (
          <span className="rounded-full border border-maybe/40 bg-maybe/10 px-2 py-0.5 text-xs text-maybe" title={`The camera clock says ${minus(off.camera)}; the local time here is ${minus(off.value)} (from ${off.source}).`}>
            {minus(off.camera)} → {minus(off.value)}
          </span>
        )}
      </div>
      <Counts s={s} />

      <div className="grid gap-3 sm:grid-cols-[1fr_200px]">
        <Thumbs ids={s.sample_group_ids} />
        <MiniMap points={s.points} />
      </div>

      {s.flags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {s.flags.map((f) => (
            <span key={f} title={FLAG_INFO[f]?.tip ?? f} className="inline-flex items-center gap-1 rounded-full border border-maybe/40 bg-maybe/10 px-2 py-0.5 text-xs text-maybe">
              ⚑ {FLAG_INFO[f]?.label ?? f}
            </span>
          ))}
        </div>
      )}

      {splitting && (
        <div className="flex flex-col gap-2 rounded-xl border border-line bg-bg/40 p-3">
          <p className="text-xs text-muted">Pick a gap to cut at. Bars are sized by gap length.</p>
          <GapStrip s={s} pick={setCut} active={cut} />
          {halves && (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <PreviewRow s={halves[0]} />
                <PreviewRow s={halves[1]} />
              </div>
              <div className="flex gap-2">
                <button type="button" className={primary} onClick={() => { onDecide({ kind: 'split', id, after_group_id: cut! }); close(); }}>Split here</button>
                <button type="button" className={btn} onClick={close}>Cancel</button>
              </div>
            </>
          )}
          {!halves && <button type="button" className={`${btn} self-start`} onClick={close}>Close</button>}
        </div>
      )}

      {merged && other && (
        <div className="flex flex-col gap-2 rounded-xl border border-line bg-bg/40 p-3">
          <p className="text-xs text-muted">Merging with “{other.name.value}” would make:</p>
          <PreviewRow s={merged} />
          <div className="flex gap-2">
            <button type="button" className={primary} onClick={() => { onDecide({ kind: 'merge', id, with: mergeDir! }); close(); }}>Merge</button>
            <button type="button" className={btn} onClick={close}>Cancel</button>
          </div>
        </div>
      )}

      {!s.locked && (
        <footer className="flex flex-wrap items-center gap-1.5">
          <button type="button" className={primary} disabled={s.state === 'accepted'} onClick={() => onDecide({ kind: 'accept', id })}>Accept</button>
          <button type="button" className={btn} onClick={() => { setDraft(s.name.value); setRenaming(true); }}>Rename</button>
          <button type="button" className={btn} title="Reject this name: it joins the stoplist and the next name takes its place" onClick={() => onDecide({ kind: 'reject_name', id, value: s.name.value })}>Reject name</button>
          <button type="button" className={btn} disabled={s.gaps.length === 0} onClick={() => { setMergeDir(null); setSplitting((v) => !v); setCut(null); }}>Split here</button>
          <button type="button" className={btn} disabled={!prev} title={prev ? undefined : 'No previous shoot in this trip'} onClick={() => { setSplitting(false); setMergeDir('prev'); }}>Merge with previous</button>
          <button type="button" className={btn} disabled={!next} title={next ? undefined : 'No next shoot in this trip'} onClick={() => { setSplitting(false); setMergeDir('next'); }}>Merge with next</button>
          <select
            aria-label="Move to trip"
            value=""
            className="h-7 rounded-full text-xs"
            onChange={(e) => {
              const v = e.target.value;
              if (v) onDecide({ kind: 'move', id, trip_id: v === '__none' ? null : v });
            }}
          >
            <option value="">Move to trip…</option>
            {tripChoices.map((t) => (
              <option key={t.proposal_id} value={t.proposal_id}>{t.name.value}</option>
            ))}
            {s.trip_id && <option value="__none">Not in a trip</option>}
          </select>
          <button type="button" className={btn} disabled={s.state === 'skipped'} onClick={() => onDecide({ kind: 'skip', id })}>Skip</button>
        </footer>
      )}
    </article>
  );
}
