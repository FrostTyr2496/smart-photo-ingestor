import { motion } from 'framer-motion';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RunningCard, taskVM, extVM, type TaskVM } from '../components/TaskCard';
import { cardRunKind, copyTargets, updatedAgo, type CardRef, type CardRunKind } from '../lib/cardRuns';
import { CountUp, Disclosure, Icon, SelectTile } from '../components/bits';
import { cameraOptions, copyOptions, CopiesCell, FileTable, FilterBar, isPhotoKind, Pager, ShowInPhotos, TierBar, tierCounts, TierPill, type Column } from '../components/FileList';
import { api } from '../lib/api';
import { fmtBytes, fmtDate, fmtInt, plural, relTime } from '../lib/format';
import { argvWords, commandKey, displayLabel, mountName } from '../lib/labels';
import { rise, springs } from '../lib/motion';
import { hrefFor } from '../lib/route';
import type { Drive } from '../lib/types';
import { UnpluggedStrip, useUnpluggedEdge } from '../components/Unplugged';
import { RenameTitle } from '../components/RenameTitle';
import { useDialogs } from '../lib/dialogs';
import { useStore } from '../lib/store';
import { cardHeadline, cardHint, TIER, TIER_HINT, TIER_ORDER, TIER_TONE, type Card, type CardCamera, type Tier } from '../lib/tiers';
import { greenButton, type GreenPlan } from '../lib/makeGreen';

const CARD_PAGE = 1000;
/** While a run works on a card, its counts are re-read this often (they come live from the catalog). */
const LIVE_RELOAD_MS = 8000;

interface CardRun {
  kind: CardRunKind;
  vm: TaskVM;
  targets: string[];
}

/** Every live run (started here or in a terminal) that works on this card. */
function useCardRuns(): (c: CardRef) => CardRun[] {
  const { tasks, elsewhere, drives, now, driveFiles } = useStore();
  return (c) => {
    const out: CardRun[] = [];
    for (const t of tasks) {
      if (t.final) continue;
      const w = argvWords(t.argv);
      const kind = cardRunKind(w, c);
      if (kind) out.push({ kind, vm: taskVM(t, drives, now, driveFiles), targets: copyTargets(w) });
    }
    for (const e of elsewhere) {
      const w = e.words || [];
      const kind = cardRunKind(w, c);
      if (kind) out.push({ kind, vm: extVM(e, drives, now, driveFiles), targets: copyTargets(w) });
    }
    return out;
  };
}

/** Calls `reload` every LIVE_RELOAD_MS while `active` and the tab is visible. */
function useLiveReload(active: boolean, reload: () => void) {
  const ref = useRef(reload);
  ref.current = reload;
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => {
      if (!document.hidden) ref.current();
    }, LIVE_RELOAD_MS);
    return () => window.clearInterval(id);
  }, [active]);
}

const LIVE_PILL: Record<CardRunKind, string> = { copy: 'Backing up', check: 'Checking', clear: 'Clearing' };

/** The most important live run decides the pill and headline: a copy, then a clear, then a check. */
function liveKind(runs: CardRun[]): CardRunKind | null {
  return (['copy', 'clear', 'check'] as const).find((k) => runs.some((r) => r.kind === k)) ?? null;
}

function LivePill({ kind }: { kind: CardRunKind }) {
  return (
    <span className="chip border-accent/40 bg-accent/10 text-accent">
      <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
      {LIVE_PILL[kind]}
    </span>
  );
}

/** "Backup in progress to X and Y: 167 of 2,386 files backed up." */
function liveHeadline(c: Card, kind: CardRunKind, runs: CardRun[], names: (m: string) => string): string {
  if (kind === 'clear') return 'Clearing this card: deleting files that are backed up.';
  if (kind === 'check') return 'Checking this card: comparing its files with your drives.';
  const to = [...new Set(runs.filter((r) => r.kind === 'copy').flatMap((r) => r.targets.map(names)))];
  const where = to.length ? ` to ${to.join(' and ')}` : '';
  return `Backup in progress${where}: ${fmtInt(c.counts.backed_up.files)} of ${plural(c.files, 'file')} backed up.`;
}

interface CardFile {
  rel_path: string;
  size: number;
  kind: string;
  tier: Tier;
  reason: string;
  copies: Array<{ label: string; role: string }>;
  asset_id: number | null;
  camera_name: string;
  dup_here: number;
}

/** A card check runs on this mount right now. */
function useChecking() {
  const { tasks } = useStore();
  return (mount: string | null) =>
    Boolean(mount) && tasks.some((t) => !t.final && commandKey(argvWords(t.argv)) === 'card_check' && argvWords(t.argv)[2] === mount);
}

/** "Make green…" (bk-7, D-108): shown when some files have one copy; enabled only when a connected drive can take a second one. */
function MakeGreenButton({ c, onGreen }: { c: Card; onGreen: (c: Card) => void }) {
  const { epoch } = useStore();
  const [plan, setPlan] = useState<GreenPlan | null>(null);
  const yellow = c.counts.needs_backup.files;
  useEffect(() => {
    if (!c.volume_id || !yellow) return undefined;
    let live = true;
    api<GreenPlan>('GET', `/api/cards/${encodeURIComponent(c.volume_id)}/make-green`)
      .then((p) => live && setPlan(p))
      .catch(() => live && setPlan(null));
    return () => {
      live = false;
    };
  }, [c.volume_id, yellow, epoch]);
  if (!c.volume_id || !yellow) return null;
  const g = greenButton(plan);
  return (
    <button type="button" className="btn h-7 text-xs" disabled={!g.enabled} title={g.title} onClick={() => onGreen(c)}>
      Make green…
    </button>
  );
}

function CardActions({ c, onImport, onClear, onGreen, showFiles }: { c: Card; onImport: (c: Card) => void; onClear: (c: Card) => void; onGreen: (c: Card) => void; showFiles: boolean }) {
  const { startTask } = useStore();
  const checking = useChecking()(c.mount);
  return (
    <div className="flex flex-wrap gap-2">
      {c.mounted && c.mount ? (
        <>
          <button type="button" className="btn h-7 text-xs" disabled={checking} onClick={() => startTask('card_check', { mount: c.mount })}>
            {checking ? (
              <>
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" /> Checking…
              </>
            ) : c.checked_at ? (
              'Check again'
            ) : (
              'Check card'
            )}
          </button>
          <button type="button" className="btn h-7 text-xs" onClick={() => onImport(c)}>
            Import…
          </button>
          <button
            type="button"
            className="btn h-7 text-xs"
            disabled={checking || !c.checked_at}
            title={c.checked_at ? 'Delete the files that are safely backed up' : 'Check the card first'}
            onClick={() => onClear(c)}
          >
            Clear card…
          </button>
        </>
      ) : (
        <>
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${displayLabel(c.label)} to check it`}>
            {c.checked_at ? 'Check again' : 'Check card'}
          </button>
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${displayLabel(c.label)} to import it`}>
            Import…
          </button>
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${displayLabel(c.label)} to clear it`}>
            Clear card…
          </button>
        </>
      )}
      <MakeGreenButton c={c} onGreen={onGreen} />
      {showFiles && c.volume_id && (
        <a href={hrefFor({ view: 'cards', id: c.volume_id })} className="btn h-7 text-xs">
          Show files
        </a>
      )}
    </div>
  );
}

function NotConnected() {
  return <span className="chip border-line-strong text-muted">Not connected</span>;
}

function TestBadge({ c }: { c: Card }) {
  return (
    <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted" title={`Test card: copies on ${c.test_card?.hidden.join(', ')} are hidden`}>
      Test
    </span>
  );
}

function CardTile({ c, onImport, onClear, onGreen }: { c: Card; onImport: (c: Card) => void; onClear: (c: Card) => void; onGreen: (c: Card) => void }) {
  const { drives } = useStore();
  const runs = useCardRuns()(c);
  const kind = liveKind(runs);
  const when = c.mounted
    ? c.checked_at
      ? `Checked ${relTime(c.checked_at)}`
      : 'Never checked'
    : `Not connected${c.last_seen_at ? ` · last seen ${relTime(c.last_seen_at)}` : ''} · as last checked`;
  return (
    <motion.article whileHover={{ y: -2 }} transition={springs.snappy} className={`card flex flex-col gap-2.5 p-4 ${c.mounted ? '' : 'opacity-70'}`}>
      <div className="flex items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate font-semibold">{displayLabel(c.label)}</h3>
        {c.test_card && <TestBadge c={c} />}
        {!c.mounted && <NotConnected />}
        {kind ? <LivePill kind={kind} /> : <TierPill tier={c.tier} />}
      </div>
      <p className={`text-sm ${kind ? 'text-accent' : TIER_TONE[c.tier].text}`}>{kind ? liveHeadline(c, kind, runs, (m) => mountName(m, drives)) : cardHeadline(c)}</p>
      <p className="text-xs text-muted">
        {plural(c.files, 'file')} · {fmtBytes(c.bytes)} · {when}
      </p>
      <TierBar counts={c.counts} total={c.files} live={kind === 'copy'} />
      <p className="text-xs text-faint">{tierCounts(c.counts)}</p>
      {c.cameras.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {c.cameras.map((m) => (
            <TierPill key={m.camera} tier={m.tier} text={`${m.name} · ${fmtInt(m.files)}`} />
          ))}
        </div>
      )}
      {c.cleared_at && <p className="text-xs text-muted">Cleared by PhotoVault on {fmtDate(c.cleared_at)}</p>}
      <CardActions c={c} onImport={onImport} onClear={onClear} onGreen={onGreen} showFiles />
    </motion.article>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5">
      {TIER_ORDER.map((t) => (
        <span key={t} className="flex items-center gap-1.5 text-xs text-muted">
          <span className={`h-2 w-2 rounded-full ${TIER_TONE[t].solid}`} />
          <span className="text-ink/80">{TIER[t].label}</span>
          <span className="hidden text-faint md:inline">· {TIER_HINT[t].split('.')[0]!.toLowerCase()}</span>
        </span>
      ))}
    </div>
  );
}

function CameraTable({ cameras, live = false }: { cameras: CardCamera[]; live?: boolean }) {
  if (!cameras.length) return <p className="text-sm text-muted">Connect a card to see which cameras its photos come from.</p>;
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-raised/40 text-left">
            {['Camera', 'Status', 'Files', 'Backed up so far', 'Breakdown'].map((h) => (
              <th key={h} className={`label px-3 py-2.5 font-normal ${h === 'Files' ? 'text-right' : ''}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cameras.map((m) => (
            <tr key={m.camera} className="border-b border-line last:border-0">
              <td className="px-3 py-2.5 font-medium">{m.name}</td>
              <td className="px-3 py-2.5">
                <TierPill tier={m.tier} />
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-xs">{fmtInt(m.files)}</td>
              <td className="w-1/4 px-3 py-2.5">
                <TierBar counts={m.counts} total={m.files} live={live} />
              </td>
              <td className="px-3 py-2.5 text-xs text-muted">{tierCounts(m.counts)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- list

function CardList({ onImport, onClear, onGreen }: { onImport: (c: Card) => void; onClear: (c: Card) => void; onGreen: (c: Card) => void }) {
  const { epoch } = useStore();
  const [data, setData] = useState<{ cards: Card[]; cameras: CardCamera[] } | null>(null);
  const [error, setError] = useState('');
  const [oldOpen, setOldOpen] = useState(false);
  const runsOf = useCardRuns();
  const load = useCallback(() => {
    api<{ cards: Card[]; cameras: CardCamera[] }>('GET', '/api/cards')
      .then((d) => {
        setData(d);
        setError('');
      })
      .catch((err: Error) => setError(err.message));
  }, []);
  useEffect(load, [load, epoch]);
  const copying = (data?.cards ?? []).filter((c) => c.mounted).some((c) => runsOf(c).some((r) => r.kind === 'copy'));
  useLiveReload(copying, load);

  if (error && !data) return <div className="card p-6 text-err">Could not load cards: {error}</div>;
  if (!data) return <div className="card h-64 animate-pulse" />;
  const connected = data.cards.filter((c) => c.mounted);
  const remembered = data.cards.filter((c) => !c.mounted);
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cards</h1>
        <p className="mt-1 text-sm text-muted">Which files on your cards are safe to remove. A card takes the colour of its least safe file.</p>
      </div>
      <Legend />
      {connected.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {connected.map((c, i) => (
            <motion.div key={c.volume_id ?? c.mount ?? c.label} {...rise(i)}>
              <CardTile c={c} onImport={onImport} onClear={onClear} onGreen={onGreen} />
            </motion.div>
          ))}
        </div>
      ) : (
        <div className="card flex flex-col items-center gap-1 px-6 py-10 text-center">
          <p className="font-medium">No card connected</p>
          <p className="text-sm text-muted">Plug in a camera card. It appears here.</p>
        </div>
      )}
      <section>
        <h2 className="label mb-2">Cameras on connected cards</h2>
        <CameraTable cameras={data.cameras} live={copying} />
      </section>
      {remembered.length > 0 && (
        <Disclosure summary={`Cards seen before (${fmtInt(remembered.length)})`} open={oldOpen} onToggle={setOldOpen}>
          <p className="pb-3 text-xs text-faint">As last checked. A card that was formatted since keeps its old record here.</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {remembered.map((c) => (
              <CardTile key={c.volume_id ?? c.label} c={c} onImport={onImport} onClear={onClear} onGreen={onGreen} />
            ))}
          </div>
        </Disclosure>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- one card

function CardDetail({ id, onImport, onClear, onGreen }: { id: string; onImport: (c: Card) => void; onClear: (c: Card) => void; onGreen: (c: Card) => void }) {
  const { epoch, drives, now } = useStore();
  const cardDrive = drives.find((d) => d.volume_id === id);
  const edge = useUnpluggedEdge(cardDrive);
  const [filter, setFilter] = useState({ tier: '', copies: '', other: '', camera: '', offset: 0 });
  const [data, setData] = useState<{ card: Card; files: CardFile[]; total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [delta, setDelta] = useState<{ at: number; by: Partial<Record<Tier, number>> } | null>(null);
  const prevCounts = useRef<Card['counts'] | null>(null);
  const runsOf = useCardRuns();

  /** `quiet` (a live refresh): keep the table as it is while the new page loads, so it does not dim every 8 s. */
  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    const p = new URLSearchParams({ limit: String(CARD_PAGE), offset: String(filter.offset) });
    if (filter.tier) p.set('tier', filter.tier);
    if (filter.camera) p.set('camera', filter.camera);
    if (filter.copies) p.set('copies', filter.copies);
    if (filter.other) p.set('other', filter.other);
    try {
      setData(await api('GET', `/api/cards/${encodeURIComponent(id)}/files?${p}`));
      setUpdatedAt(Date.now());
      setError('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [id, filter]);
  useEffect(() => {
    void load();
  }, [load, epoch]);

  const runs = data ? runsOf(data.card) : [];
  const kind = liveKind(runs);
  useLiveReload(kind !== null, () => void load(true));

  // Between live refreshes, note how each count moved, so the tiles can show "+12" for a moment.
  useEffect(() => {
    if (!data) return;
    const cur = data.card.counts;
    const prev = prevCounts.current;
    prevCounts.current = cur;
    if (!prev || prev === cur) return;
    const by: Partial<Record<Tier, number>> = {};
    for (const t of TIER_ORDER) if (cur[t].files !== prev[t].files) by[t] = cur[t].files - prev[t].files;
    if (Object.keys(by).length) setDelta({ at: Date.now(), by });
  }, [data]);
  useEffect(() => {
    prevCounts.current = null; // another card: nothing to compare with
  }, [id]);

  const set = (patch: Partial<typeof filter>) => setFilter((f) => ({ ...f, ...patch, offset: 0 }));

  if (error && !data) return <div className="card p-6 text-err">Could not load the card: {error}</div>;
  if (!data) return <div className="card h-96 animate-pulse" />;
  const c = data.card;
  // The live drive list knows at once when the card is pulled; the card's own data is as of its last load (rv-38).
  const mounted = cardDrive ? cardDrive.mounted : c.mounted;

  const columns: Array<Column<CardFile>> = [
    { head: 'Status', cell: (f) => <TierPill tier={f.tier} /> },
    {
      head: 'File',
      path: true,
      cell: (f, _o, _t, name) => (
        <div className="min-w-[16rem]">
          <p className="break-all font-mono text-xs" title={f.rel_path}>
            {name}
          </p>
          <p className="text-2xs text-faint">{f.reason}</p>
        </div>
      ),
    },
    { head: 'Size', num: true, cell: (f) => fmtBytes(f.size) },
    { head: 'Camera', cell: (f) => <span className="text-xs text-muted">{f.camera_name}</span> },
    {
      head: 'Copies',
      cell: (f, open, toggle) => (
        <CopiesCell hashed={f.tier !== 'unchecked'} drives={f.copies} dupHere={f.dup_here} here="this card" open={open} onToggle={toggle} />
      ),
    },
    {
      head: '',
      cell: (f) => (isPhotoKind(f.kind) && c.volume_id ? <ShowInPhotos volumeId={c.volume_id} relPath={f.rel_path} /> : null),
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <section className={`card p-5 ${edge}`}>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            {/* Renameable like a drive (rv-40); a remembered card not in the drive list is renamed by its id. */}
            <RenameTitle
              drive={cardDrive ?? (c.volume_id ? ({ volume_id: c.volume_id, label: c.label, mount: '', mounted: false } as Drive) : null)}
              fallback={displayLabel(c.label)}
            >
              {c.test_card && <TestBadge c={c} />}
              {!mounted && <NotConnected />}
              {kind ? <LivePill kind={kind} /> : <TierPill tier={c.tier} />}
            </RenameTitle>
            <p className={`mt-2 ${kind ? 'text-accent' : TIER_TONE[c.tier].text}`}>{kind ? liveHeadline(c, kind, runs, (m) => mountName(m, drives)) : cardHeadline(c)}</p>
            <p className="mt-1 text-sm text-muted">
              {[
                kind
                  ? `${kind === 'copy' ? 'Counts update as each file is verified. Keep the card in until it finishes.' : 'Counts update as it goes.'} ${updatedAgo(updatedAt, now)}.`
                  : cardHint(c),
                kind ? '' : mounted ? (c.checked_at ? `Checked ${relTime(c.checked_at)}.` : 'Never checked.') : 'Not connected: as last checked.',
                c.cleared_at ? `Cleared by PhotoVault on ${fmtDate(c.cleared_at)}.` : '', c.test_card ? `Test card: copies on ${c.test_card.hidden.join(', ')} are hidden.` : '']
                .filter(Boolean)
                .join(' ')}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <CardActions c={c} onImport={onImport} onClear={onClear} onGreen={onGreen} showFiles={false} />
            <a href={hrefFor({ view: 'cards', id: null })} className="btn h-7 text-xs">
              <Icon name="back" size={12} /> All cards
            </a>
          </div>
        </div>
        {cardDrive && !cardDrive.mounted && (
          <UnpluggedStrip d={cardDrive} extra={c.checked_at ? [{ label: 'Last checked', value: relTime(c.checked_at) || fmtDate(c.checked_at), title: fmtDate(c.checked_at) }] : []} />
        )}
        {runs.length > 0 && (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {runs.map((r) => (
              <RunningCard key={r.vm.key} vm={r.vm} compact href={hrefFor({ view: 'home', task: r.vm.key })} />
            ))}
          </div>
        )}
        <TierBar counts={c.counts} total={c.files} live={kind === 'copy'} className="mt-5" />
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {TIER_ORDER.map((t) => {
            const on = filter.tier === t;
            return (
              <SelectTile key={t} group="tier" active={on} onClick={() => set({ tier: on ? '' : t })}>
                <p className={`text-sm ${TIER_TONE[t].text}`}>{TIER[t].label}</p>
                <p className="mt-1 flex items-baseline gap-2 text-2xl font-semibold tracking-tight">
                  <CountUp value={c.counts[t].files} />
                  {kind && delta?.by[t] ? (
                    <motion.span
                      key={delta.at}
                      className={`font-mono text-xs font-normal ${TIER_TONE[t].text}`}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: [0, 1, 1, 0], y: 0 }}
                      transition={{ duration: 6, times: [0, 0.08, 0.75, 1] }}
                    >
                      {delta.by[t]! > 0 ? '+' : '−'}
                      {fmtInt(Math.abs(delta.by[t]!))}
                    </motion.span>
                  ) : null}
                </p>
                <p className="mt-0.5 font-mono text-2xs text-muted">{fmtBytes(c.counts[t].bytes)}</p>
              </SelectTile>
            );
          })}
        </div>
      </section>

      <section className="card p-5">
        <h2 className="mb-3 text-lg font-semibold">Cameras</h2>
        <CameraTable cameras={c.cameras} live={kind === 'copy'} />
      </section>

      <section className="card flex flex-col gap-3 p-5">
        <h2 className="text-lg font-semibold">Files, least safe first</h2>
        <FilterBar
          show={{
            value: filter.tier,
            options: [{ value: '', text: 'All files' }, ...TIER_ORDER.map((t) => ({ value: t, text: TIER[t].label }))],
            onChange: (tier) => set({ tier }),
          }}
          copies={{
            value: filter.other ? `on:${filter.other}` : filter.copies,
            options: copyOptions('this card', c.copies_by_volume || []),
            onChange: (v) => set(v.startsWith('on:') ? { copies: '', other: v.slice(3) } : { copies: v, other: '' }),
          }}
          camera={{ value: filter.camera, options: cameraOptions(c.cameras), onChange: (camera) => set({ camera }) }}
          shown={data.total}
          total={c.files}
        />
        <FileTable rows={data.files} columns={columns} volumeId={c.volume_id ?? ''} mounted={c.mounted && Boolean(c.volume_id)} loading={loading} summarize={summarizeCardFiles} />
        <Pager offset={filter.offset} count={data.files.length} total={data.total} page={CARD_PAGE} onPage={(offset) => setFilter((f) => ({ ...f, offset }))} />
      </section>
    </div>
  );
}

/** A folder or run of card files: size and how many per tier, worst first. */
function summarizeCardFiles(rows: CardFile[]) {
  return (
    <>
      <span className="font-mono">{fmtBytes(rows.reduce((n, r) => n + r.size, 0))}</span>
      {TIER_ORDER.map((t) => [t, rows.filter((r) => r.tier === t).length] as const)
        .filter(([, n]) => n)
        .map(([t, n]) => (
          <TierPill key={t} tier={t} text={`${fmtInt(n)} ${TIER[t].label.toLowerCase()}`} />
        ))}
    </>
  );
}

export function Cards({ id }: { id: string | null }) {
  // The long-running dialogs live at app level (rv-32), so they survive leaving this page while minimized.
  const { openDialog } = useDialogs();
  const onImport = (c: Card) => openDialog('import', c);
  const onClear = (c: Card) => openDialog('clear', c);
  const onGreen = (c: Card) => openDialog('green', c);
  return id ? <CardDetail id={id} onImport={onImport} onClear={onClear} onGreen={onGreen} /> : <CardList onImport={onImport} onClear={onClear} onGreen={onGreen} />;
}
