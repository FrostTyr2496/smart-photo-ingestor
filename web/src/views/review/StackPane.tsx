import { useEffect, useMemo, useRef, useState } from 'react';
import { sameSession, type Candidate, type Detail, type DetailChoice, type EffPlan, type EffShoot, type NewDecision, type Session } from '../../lib/review';
import { composeName, confirmDecisions, detailSourceLabel, fade, initialDraft, pct, type StackDraft } from '../../lib/stacks';
import { ShootCard, datesLine } from './ShootCard';

const btn = 'inline-flex h-7 items-center rounded-full border border-line px-2.5 text-xs text-muted hover:border-line-strong hover:text-ink disabled:opacity-40';
const primary = 'inline-flex h-7 items-center rounded-full border border-maybe/50 bg-maybe/15 px-3 text-xs font-medium text-maybe hover:bg-maybe/25 disabled:opacity-40';

const STEPS = ['Details', 'Members', 'In question'] as const;
type FieldKey = 'place' | 'subject' | 'people' | 'name';
const FIELDS: FieldKey[] = ['place', 'subject', 'people', 'name'];

/** The dashed divider that fences off everything a full run would drop (D-128). */
function BetaDivider({ children = 'beyond cut-off (beta)' }: { children?: string }) {
  return (
    <div className="my-1 flex items-center gap-2 text-2xs text-faint" role="separator">
      <span className="flex-1 border-t border-dashed border-line-strong" />
      {children}
      <span className="flex-1 border-t border-dashed border-line-strong" />
    </div>
  );
}

/** One value with its source and confidence; the opacity falls with confidence and the number is on hover. */
function Chip({ c, selected, onPick, onRemove }: { c: DetailChoice; selected?: boolean; onPick?: () => void; onRemove?: () => void }) {
  const num = `${pct(c.confidence)} confidence${c.evidence ? ` · ${c.evidence} pieces of evidence` : ''}${c.beyond_cutoff ? ' · beyond cut-off' : ''}`;
  return (
    <span style={{ opacity: fade(c.confidence) }} title={`${detailSourceLabel(c.source)} · ${num}`} className={`inline-flex items-center rounded-full border text-xs ${selected ? 'border-maybe bg-maybe/15 text-ink' : 'border-line text-muted hover:text-ink'}`}>
      <button type="button" className="px-2.5 py-1" onClick={onPick} disabled={!onPick} aria-pressed={selected}>
        {c.value} <span className="text-2xs text-faint">{detailSourceLabel(c.source)} {pct(c.confidence)}</span>
      </button>
      {onRemove && (
        <button type="button" className="pr-2 text-faint hover:text-err" aria-label={`Remove ${c.value}`} onClick={onRemove}>
          ×
        </button>
      )}
    </span>
  );
}

function TypeIn({ label, placeholder, onSubmit }: { label: string; placeholder: string; onSubmit: (v: string) => void }) {
  const [v, setV] = useState('');
  return (
    <input
      aria-label={label}
      placeholder={placeholder}
      value={v}
      className="h-7 w-48 text-xs"
      onChange={(e) => setV(e.target.value)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter' && v.trim()) {
          e.preventDefault();
          onSubmit(v.trim());
          setV('');
        }
      }}
    />
  );
}

/** The options for a place or subject field: the plan's value first, alternatives, then those beyond the cut-off. */
function optionsOf(d: Detail | undefined, draftValue: string | null): { normal: DetailChoice[]; beyond: DetailChoice[] } {
  const alts = d?.alternatives ?? [];
  const cur: DetailChoice[] = d?.value ? [{ value: d.value, source: d.source ?? 'user', confidence: d.confidence, evidence: d.evidence }] : [];
  const all = [...cur, ...alts.filter((a) => !a.beyond_cutoff)];
  // A value the user typed is not in the plan's list: keep it visible.
  if (draftValue && !all.some((c) => c.value === draftValue) && !alts.some((c) => c.value === draftValue)) all.unshift({ value: draftValue, source: 'user', confidence: 1 });
  return { normal: all, beyond: alts.filter((a) => a.beyond_cutoff) };
}

function DetailRow({ title, active, children }: { title: string; active: boolean; children: React.ReactNode }) {
  return (
    <section className={`flex flex-col gap-1.5 rounded-xl border p-3 ${active ? 'border-maybe/50 bg-maybe/5' : 'border-line'}`}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  );
}

function Tile({ id, count, on, cursor, onClick, caption }: { id: number; count: number; on: boolean; cursor: boolean; onClick: () => void; caption: string }) {
  const [bad, setBad] = useState(false);
  return (
    <button type="button" onClick={onClick} aria-pressed={on} title={caption} className={`relative flex h-24 w-24 shrink-0 flex-col overflow-hidden rounded-lg border text-left ${cursor ? 'ring-2 ring-maybe' : ''} ${on ? 'border-go/50' : 'border-line opacity-40'}`}>
      {bad ? <span className="flex flex-1 items-center justify-center bg-ink/5 text-2xs text-faint">no preview</span> : <img src={`/api/groups/${id}/thumb`} loading="lazy" alt="" onError={() => setBad(true)} className="h-full w-full bg-ink/5 object-cover" />}
      <span className="absolute inset-x-0 bottom-0 flex justify-between bg-bg/80 px-1.5 py-0.5 text-2xs">
        <span>{on ? '✓' : '✗'}</span>
        <span>{count.toLocaleString()}</span>
      </span>
    </button>
  );
}

const hhmm = (iso: string): string => iso.slice(11, 16);

function CandidateRow({ c, on, cursor, toggle }: { c: Candidate; on: boolean; cursor: boolean; toggle: () => void }) {
  const [bad, setBad] = useState(false);
  const bodies = Object.entries(c.bodies).map(([b, n]) => `${b} ${n}`).join(', ');
  return (
    <li style={{ opacity: fade(c.score) }} title={`score ${c.score.toFixed(2)}${c.beyond_cutoff ? ' · beyond cut-off' : ''}`} className={`flex items-center gap-3 rounded-lg border p-2 ${cursor ? 'ring-2 ring-maybe' : ''} ${on ? 'border-go/50' : 'border-line'}`}>
      <input type="checkbox" checked={on} onChange={toggle} aria-label={`Include photos ${c.session[0]} to ${c.session[1]}`} />
      {bad ? <span className="flex h-14 w-14 items-center justify-center rounded-md bg-ink/5 text-2xs text-faint">no preview</span> : <img src={`/api/groups/${c.session[0]}/thumb`} loading="lazy" alt="" onError={() => setBad(true)} className="h-14 w-14 rounded-md bg-ink/5 object-cover" />}
      <div className="min-w-0 flex-1 text-xs">
        <p className="text-sm">
          {c.frames.toLocaleString()} {c.frames === 1 ? 'frame' : 'frames'} · {hhmm(c.start_instant)}–{hhmm(c.end_instant)} UTC {bodies && <span className="text-muted">· {bodies}</span>}
        </p>
        <p className="text-muted">{c.reasons.length ? c.reasons.map((r) => r.text).join(' · ') : 'no reason recorded'}</p>
      </div>
      <span className="shrink-0 text-xs tabular-nums text-muted">
        {c.band === 'beyond_cutoff' ? 'faint' : c.band} {pct(c.score)}
      </span>
    </li>
  );
}

export function StackPane({
  s,
  plan,
  pretick,
  mounted,
  position,
  onDecide,
  onConfirm,
  onClose,
}: {
  s: EffShoot;
  plan: EffPlan;
  pretick: number;
  mounted: ReadonlySet<string>;
  position: string;
  onDecide: (d: NewDecision) => void;
  /** The decisions for Confirm, in order; the view stores them and moves to the next stack. */
  onConfirm: (ds: NewDecision[]) => void;
  onClose: () => void;
}) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<StackDraft>(() => initialDraft(s, pretick));
  const [field, setField] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const d = s.details;
  const core: Session[] = useMemo(() => s.members?.core_sessions ?? [], [s]);
  const cands = useMemo(() => s.candidates ?? [], [s]);
  const normalC = cands.filter((c) => !c.beyond_cutoff);
  const beyondC = cands.filter((c) => c.beyond_cutoff);
  const shownC = [...normalC, ...beyondC];
  const patch = (p: Partial<StackDraft>) => setDraft((x) => ({ ...x, ...p }));

  const pick = (f: 'place' | 'subject', c: DetailChoice) =>
    setDraft((x) => {
      const next = { ...x, [f]: c.value, [f === 'place' ? 'placeSource' : 'subjectSource']: c.source };
      return x.nameEdited ? next : { ...next, name: composeName(next.place, next.subject) || x.name };
    });
  const cycle = (f: 'place' | 'subject') => {
    const o = optionsOf(d?.[f], draft[f]);
    const all = [...o.normal, ...o.beyond];
    if (all.length < 2) return;
    const i = all.findIndex((c) => c.value === draft[f]);
    pick(f, all[(i + 1) % all.length]!);
  };
  const toggleCore = (x: Session) => setDraft((v) => ({ ...v, excluded: v.excluded.some((y) => sameSession(x, y)) ? v.excluded.filter((y) => !sameSession(x, y)) : [...v.excluded, x] }));
  const toggleCand = (x: Session) => setDraft((v) => ({ ...v, ticked: v.ticked.some((y) => sameSession(x, y)) ? v.ticked.filter((y) => !sameSession(x, y)) : [...v.ticked, x] }));
  const confirm = () => onConfirm(confirmDecisions(s, draft));
  const skip = () => onDecide({ kind: 'skip', id: s.proposal_id });

  // Keyboard: the latest handler lives in a ref so the one window listener never goes stale.
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (t?.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    const len = step === 0 ? FIELDS.length : step === 1 ? core.length : shownC.length;
    const k = e.key.toLowerCase();
    if (k === 'enter') {
      if (t?.closest('button, a')) return;
      e.preventDefault();
      if (step < 2) {
        setStep(step + 1);
        setCursor(0);
      } else confirm();
    } else if (k === 'tab' && step === 0 && (FIELDS[field] === 'place' || FIELDS[field] === 'subject')) {
      e.preventDefault();
      cycle(FIELDS[field] as 'place' | 'subject');
    } else if (k === 'j' || k === 'k') {
      const dir = k === 'j' ? 1 : -1;
      if (step === 0) setField((f) => Math.max(0, Math.min(FIELDS.length - 1, f + dir)));
      else setCursor((c) => Math.max(0, Math.min(Math.max(0, len - 1), c + dir)));
    } else if (k === 'x') {
      if (step === 1 && core[cursor]) toggleCore(core[cursor]!);
      if (step === 2 && shownC[cursor]) toggleCand(shownC[cursor]!.session);
    } else if (k === 's') skip();
    else if (k === 'escape') onClose();
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  const coreCount = (x: Session) => s.group_ids.filter((g) => g >= x[0] && g <= x[1]).length;
  const evidence = [
    ...(d?.subject.example_caption ? [{ key: 'cap', text: `“${d.subject.example_caption.value}”`, source: 'phone_caption', c: d.subject.example_caption.confidence, beyond: d.subject.example_caption.beyond_cutoff }] : []),
    ...[...(d ? [d.place, d.subject] : [])].flatMap((x, i) => [...(x.value ? [{ value: x.value, source: x.source ?? '', confidence: x.confidence }] : []), ...x.alternatives].filter((a) => a.source.startsWith('phone')).map((a) => ({ key: `${i}-${a.value}-${a.source}`, text: a.value, source: a.source, c: a.confidence, beyond: 'beyond_cutoff' in a && Boolean(a.beyond_cutoff) }))),
  ];

  return (
    <article className="flex flex-col gap-4 rounded-2xl border border-maybe/40 bg-surface/80 p-4" aria-label={`Review ${s.name.value}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-2xs text-faint">{position}</p>
          <h2 className="text-lg font-semibold">{draft.name || s.name.value}</h2>
          <p className="text-xs text-muted">
            {datesLine(s)} · {s.counts.frames.toLocaleString()} frames
            {s.state !== 'undecided' && <span className="ml-2 rounded-full bg-ink/10 px-2 py-0.5">{s.confirmed ? 'confirmed' : s.state}</span>}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button type="button" className={btn} onClick={skip} title="Skip this shoot (S)">Skip</button>
          <button type="button" className={btn} onClick={onClose} title="Back to the queue (Esc)">Close</button>
        </div>
      </header>

      <ol className="flex gap-1" aria-label="Steps">
        {STEPS.map((n, i) => (
          <li key={n}>
            <button type="button" aria-current={step === i ? 'step' : undefined} onClick={() => { setStep(i); setCursor(0); }} className={`h-7 rounded-full px-3 text-xs ${step === i ? 'bg-maybe/20 text-maybe' : 'text-muted hover:text-ink'}`}>
              {i + 1} {n}
              {i === 2 && cands.length > 0 ? ` (${cands.length})` : ''}
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="flex flex-col gap-3">
          {(['place', 'subject'] as const).map((f, i) => {
            const o = optionsOf(d?.[f], draft[f]);
            return (
              <DetailRow key={f} title={f === 'place' ? 'Place' : 'Subject'} active={field === i}>
                <div className="flex flex-wrap items-center gap-1.5" onClick={() => setField(i)}>
                  {o.normal.length === 0 && <span className="text-xs text-muted">Nothing proposed.</span>}
                  {o.normal.map((c) => (
                    <Chip key={`${c.value}|${c.source}`} c={c} selected={draft[f] === c.value} onPick={() => pick(f, c)} />
                  ))}
                  <TypeIn label={`Type a ${f}`} placeholder={`Type a ${f}…`} onSubmit={(v) => pick(f, { value: v, source: 'user', confidence: 1 })} />
                </div>
                {o.beyond.length > 0 && (
                  <>
                    <BetaDivider />
                    <div className="flex flex-wrap gap-1.5">
                      {o.beyond.map((c) => (
                        <Chip key={`${c.value}|${c.source}`} c={c} selected={draft[f] === c.value} onPick={() => pick(f, c)} />
                      ))}
                    </div>
                  </>
                )}
                {f === 'subject' && d?.subject.example_caption && (
                  <p style={{ opacity: fade(d.subject.example_caption.confidence) }} className="text-xs text-muted" title={`${pct(d.subject.example_caption.confidence)} confidence${d.subject.example_caption.beyond_cutoff ? ' · beyond cut-off' : ''}`}>
                    {d.subject.example_caption.beyond_cutoff && <span className="mr-1 text-faint">(beyond cut-off, beta)</span>}
                    iPhone caption: “{d.subject.example_caption.value}”
                  </p>
                )}
              </DetailRow>
            );
          })}
          <DetailRow title="People" active={field === 2}>
            <div className="flex flex-wrap items-center gap-1.5" onClick={() => setField(2)}>
              {(d?.people.names ?? []).concat(draft.people.filter((n) => !(d?.people.names ?? []).some((c) => c.value === n)).map((n) => ({ value: n, source: 'user', confidence: 1 }))).map((c) => (
                <Chip key={c.value} c={c} selected={draft.people.includes(c.value)} onPick={() => patch({ people: draft.people.includes(c.value) ? draft.people.filter((n) => n !== c.value) : [...draft.people, c.value] })} />
              ))}
              <TypeIn label="Add a person" placeholder="Add a person…" onSubmit={(v) => !draft.people.includes(v) && patch({ people: [...draft.people, v] })} />
            </div>
            {(d?.people.alternatives ?? []).length > 0 && (
              <>
                <BetaDivider />
                <div className="flex flex-wrap gap-1.5">
                  {d!.people.alternatives.map((c) => (
                    <Chip key={c.value} c={c} selected={draft.people.includes(c.value)} onPick={() => patch({ people: draft.people.includes(c.value) ? draft.people.filter((n) => n !== c.value) : [...draft.people, c.value] })} />
                  ))}
                </div>
              </>
            )}
          </DetailRow>
          <DetailRow title="Name (place · subject)" active={field === 3}>
            <input
              aria-label="Shoot name"
              value={draft.name}
              className="h-8 w-full"
              onFocus={() => setField(3)}
              onChange={(e) => patch({ name: e.target.value, nameEdited: true })}
              onKeyDown={(e) => e.stopPropagation()}
            />
          </DetailRow>
        </div>
      )}

      {step === 1 && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted">Each tile is one session, in time order. Untick one to send its frames back to the pool (X). {draft.excluded.length} unticked.</p>
          <div className="flex flex-wrap gap-2">
            {core.map((x, i) => (
              <Tile key={`${x[0]}-${x[1]}`} id={x[0]} count={coreCount(x)} on={!draft.excluded.some((y) => sameSession(x, y))} cursor={cursor === i} onClick={() => { setCursor(i); toggleCore(x); }} caption={`Photos ${x[0]}–${x[1]}`} />
            ))}
            {core.length === 0 && <p className="text-xs text-muted">This plan has no session data (older plan): re-plan to see members.</p>}
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="flex flex-col gap-2">
          {cands.length === 0 ? (
            <p className="text-xs text-muted">No frames in question for this shoot.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {normalC.map((c, i) => (
                <CandidateRow key={c.session.join('-')} c={c} on={draft.ticked.some((y) => sameSession(c.session, y))} cursor={cursor === i} toggle={() => { setCursor(i); toggleCand(c.session); }} />
              ))}
              {beyondC.length > 0 && <BetaDivider />}
              {beyondC.map((c, i) => (
                <CandidateRow key={c.session.join('-')} c={c} on={draft.ticked.some((y) => sameSession(c.session, y))} cursor={cursor === normalC.length + i} toggle={() => { setCursor(normalC.length + i); toggleCand(c.session); }} />
              ))}
            </ul>
          )}
          {evidence.length > 0 && (
            <div className="mt-2 rounded-xl border border-line p-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Phone evidence (context only)</h3>
              <ul className="mt-1 flex flex-col gap-1">
                {evidence.map((e) => (
                  <li key={e.key} style={{ opacity: fade(e.c) }} className="text-xs text-muted" title={`${pct(e.c)} confidence`}>
                    {detailSourceLabel(e.source)}: {e.text} <span className="text-faint">{pct(e.c)}{e.beyond ? ' · beyond cut-off' : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={btn} disabled={step === 0} onClick={() => { setStep(step - 1); setCursor(0); }}>Back</button>
        {step < 2 ? (
          <button type="button" className={primary} onClick={() => { setStep(step + 1); setCursor(0); }}>Next (Enter)</button>
        ) : (
          <button type="button" className={primary} onClick={confirm}>Confirm (Enter)</button>
        )}
        {step < 2 && <button type="button" className={btn} onClick={confirm} title="Confirm now, with the details and members as shown">Confirm now</button>}
        <button type="button" className={`${btn} ml-auto`} onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>Split, merge, move…</button>
        <span className="w-full text-2xs text-faint">Keys: Enter next / confirm · Tab cycle alternatives · J K move · X tick · S skip · Esc close</span>
      </footer>
      {advanced && <ShootCard s={s} plan={plan} onDecide={onDecide} mounted={mounted} />}
    </article>
  );
}
