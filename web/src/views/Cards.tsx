import { motion } from 'framer-motion';
import { useCallback, useEffect, useState } from 'react';
import { CountUp, Disclosure, Icon, SelectTile } from '../components/bits';
import { cameraOptions, copyOptions, CopiesCell, FileTable, FilterBar, Pager, TierBar, tierCounts, TierPill, type Column } from '../components/FileList';
import { api } from '../lib/api';
import { fmtBytes, fmtDate, fmtInt, plural, relTime } from '../lib/format';
import { argvWords, commandKey } from '../lib/labels';
import { rise, springs } from '../lib/motion';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import { cardHeadline, cardHint, TIER, TIER_HINT, TIER_ORDER, TIER_TONE, type Card, type CardCamera, type Tier } from '../lib/tiers';
import { ClearDialog } from './ClearDialog';
import { ImportDialog } from './ImportDialog';

const CARD_PAGE = 1000;

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

function CardActions({ c, onImport, onClear, showFiles }: { c: Card; onImport: (c: Card) => void; onClear: (c: Card) => void; showFiles: boolean }) {
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
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${c.label} to check it`}>
            {c.checked_at ? 'Check again' : 'Check card'}
          </button>
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${c.label} to import it`}>
            Import…
          </button>
          <button type="button" className="btn h-7 text-xs" disabled title={`Plug in ${c.label} to clear it`}>
            Clear card…
          </button>
        </>
      )}
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

function CardTile({ c, onImport, onClear }: { c: Card; onImport: (c: Card) => void; onClear: (c: Card) => void }) {
  const when = c.mounted
    ? c.checked_at
      ? `Checked ${relTime(c.checked_at)}`
      : 'Never checked'
    : `Not connected${c.last_seen_at ? ` · last seen ${relTime(c.last_seen_at)}` : ''} · as last checked`;
  return (
    <motion.article whileHover={{ y: -2 }} transition={springs.snappy} className={`card flex flex-col gap-2.5 p-4 ${c.mounted ? '' : 'opacity-70'}`}>
      <div className="flex items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate font-semibold">{c.label}</h3>
        {!c.mounted && <NotConnected />}
        <TierPill tier={c.tier} />
      </div>
      <p className={`text-sm ${TIER_TONE[c.tier].text}`}>{cardHeadline(c)}</p>
      <p className="text-xs text-muted">
        {plural(c.files, 'file')} · {fmtBytes(c.bytes)} · {when}
      </p>
      <TierBar counts={c.counts} total={c.files} />
      <p className="text-xs text-faint">{tierCounts(c.counts)}</p>
      {c.cameras.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {c.cameras.map((m) => (
            <TierPill key={m.camera} tier={m.tier} text={`${m.name} · ${fmtInt(m.files)}`} />
          ))}
        </div>
      )}
      {c.cleared_at && <p className="text-xs text-muted">Cleared by PhotoVault on {fmtDate(c.cleared_at)}</p>}
      <CardActions c={c} onImport={onImport} onClear={onClear} showFiles />
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

function CameraTable({ cameras }: { cameras: CardCamera[] }) {
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
                <TierBar counts={m.counts} total={m.files} />
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

function CardList({ onImport, onClear }: { onImport: (c: Card) => void; onClear: (c: Card) => void }) {
  const { epoch } = useStore();
  const [data, setData] = useState<{ cards: Card[]; cameras: CardCamera[] } | null>(null);
  const [error, setError] = useState('');
  const [oldOpen, setOldOpen] = useState(false);
  useEffect(() => {
    api<{ cards: Card[]; cameras: CardCamera[] }>('GET', '/api/cards')
      .then((d) => {
        setData(d);
        setError('');
      })
      .catch((err: Error) => setError(err.message));
  }, [epoch]);

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
              <CardTile c={c} onImport={onImport} onClear={onClear} />
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
        <CameraTable cameras={data.cameras} />
      </section>
      {remembered.length > 0 && (
        <Disclosure summary={`Cards seen before (${fmtInt(remembered.length)})`} open={oldOpen} onToggle={setOldOpen}>
          <p className="pb-3 text-xs text-faint">As last checked. A card that was formatted since keeps its old record here.</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {remembered.map((c) => (
              <CardTile key={c.volume_id ?? c.label} c={c} onImport={onImport} onClear={onClear} />
            ))}
          </div>
        </Disclosure>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- one card

function CardDetail({ id, onImport, onClear }: { id: string; onImport: (c: Card) => void; onClear: (c: Card) => void }) {
  const { epoch } = useStore();
  const [filter, setFilter] = useState({ tier: '', copies: '', other: '', camera: '', offset: 0 });
  const [data, setData] = useState<{ card: Card; files: CardFile[]; total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    const p = new URLSearchParams({ limit: String(CARD_PAGE), offset: String(filter.offset) });
    if (filter.tier) p.set('tier', filter.tier);
    if (filter.camera) p.set('camera', filter.camera);
    if (filter.copies) p.set('copies', filter.copies);
    if (filter.other) p.set('other', filter.other);
    try {
      setData(await api('GET', `/api/cards/${encodeURIComponent(id)}/files?${p}`));
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

  const set = (patch: Partial<typeof filter>) => setFilter((f) => ({ ...f, ...patch, offset: 0 }));

  if (error && !data) return <div className="card p-6 text-err">Could not load the card: {error}</div>;
  if (!data) return <div className="card h-96 animate-pulse" />;
  const c = data.card;

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
  ];

  return (
    <div className="flex flex-col gap-5">
      <section className="card p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2.5">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{c.label}</h1>
              {!c.mounted && <NotConnected />}
              <TierPill tier={c.tier} />
            </div>
            <p className={`mt-2 ${TIER_TONE[c.tier].text}`}>{cardHeadline(c)}</p>
            <p className="mt-1 text-sm text-muted">
              {[cardHint(c), c.mounted ? (c.checked_at ? `Checked ${relTime(c.checked_at)}.` : 'Never checked.') : 'Not connected: as last checked.', c.cleared_at ? `Cleared by PhotoVault on ${fmtDate(c.cleared_at)}.` : '']
                .filter(Boolean)
                .join(' ')}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <CardActions c={c} onImport={onImport} onClear={onClear} showFiles={false} />
            <a href={hrefFor({ view: 'cards', id: null })} className="btn h-7 text-xs">
              <Icon name="back" size={12} /> All cards
            </a>
          </div>
        </div>
        <TierBar counts={c.counts} total={c.files} className="mt-5" />
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {TIER_ORDER.map((t) => {
            const on = filter.tier === t;
            return (
              <SelectTile key={t} group="tier" active={on} onClick={() => set({ tier: on ? '' : t })}>
                <p className={`text-sm ${TIER_TONE[t].text}`}>{TIER[t].label}</p>
                <p className="mt-1 text-2xl font-semibold tracking-tight">
                  <CountUp value={c.counts[t].files} />
                </p>
                <p className="mt-0.5 font-mono text-2xs text-muted">{fmtBytes(c.counts[t].bytes)}</p>
              </SelectTile>
            );
          })}
        </div>
      </section>

      <section className="card p-5">
        <h2 className="mb-3 text-lg font-semibold">Cameras</h2>
        <CameraTable cameras={c.cameras} />
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
        <FileTable rows={data.files} columns={columns} volumeId={c.volume_id ?? ''} loading={loading} summarize={summarizeCardFiles} />
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
  const [importing, setImporting] = useState<{ mount: string; label: string } | null>(null);
  const onImport = (c: Card) => c.mount && setImporting({ mount: c.mount, label: c.label });
  const [clearing, setClearing] = useState<{ mount: string; label: string } | null>(null);
  const onClear = (c: Card) => c.mount && setClearing({ mount: c.mount, label: c.label });
  return (
    <>
      {id ? <CardDetail id={id} onImport={onImport} onClear={onClear} /> : <CardList onImport={onImport} onClear={onClear} />}
      <ImportDialog card={importing} onClose={() => setImporting(null)} />
      <ClearDialog card={clearing} onClose={() => setClearing(null)} />
    </>
  );
}
