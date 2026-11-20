import { motion } from 'framer-motion';
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../lib/api';
import { allKeys, buildTree, visibleRows, type FolderNode, type RunNode } from '../lib/filetree';
import { fmtBytes, fmtInt } from '../lib/format';
import { openMenu, placeMenu } from '../lib/menu';
import { copyText, dayLabel } from '../lib/photos';
import { countsLine, dateRange, shootLine, titleTone, type Outline, type OutlineShoot, type OutlineState } from '../lib/outline';
import { displayLabel, roleKey, roleLabel } from '../lib/labels';
import { springs } from '../lib/motion';
import { usePref } from '../lib/prefs';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import { TIER, TIER_ORDER, TIER_TONE, nameList, type Tier, type TierCounts } from '../lib/tiers';
import { Icon, RolePill } from './bits';

// ---------------------------------------------------------------- tier bar and pill

/** A card's files as one bar of tier segments, worst first; each segment grows to its share. */
/** `live` (a copy is running): a pulse flows left to right, from "only on this card" towards "backed up". */
export function TierBar({ counts, total, live = false, className = '' }: { counts: TierCounts; total: number; live?: boolean; className?: string }) {
  const label = TIER_ORDER.map((t) => `${fmtInt(counts[t].files)} ${TIER[t].label.toLowerCase()}`).join(', ');
  return (
    <div role="img" aria-label={label} className={`relative flex h-2 w-full gap-[2px] overflow-hidden rounded-full bg-ink/[0.07] ${live ? 'bar-flow' : ''} ${className}`}>
      {total > 0 &&
        TIER_ORDER.filter((t) => counts[t].files).map((t) => (
          <motion.span
            key={t}
            title={`${TIER[t].label}: ${fmtInt(counts[t].files)}`}
            className={`h-full ${TIER_TONE[t].solid}`}
            initial={{ width: 0 }}
            animate={{ width: `${(counts[t].files / total) * 100}%` }}
            transition={springs.glide}
          />
        ))}
    </div>
  );
}

export function TierPill({ tier, text }: { tier: Tier | 'empty'; text?: string }) {
  return (
    <span className={`chip ${TIER_TONE[tier].soft}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${TIER_TONE[tier].solid}`} aria-hidden="true" />
      {text ?? TIER[tier].pill}
    </span>
  );
}

export const tierCounts = (counts: TierCounts): string =>
  TIER_ORDER.filter((t) => counts[t].files)
    .map((t) => `${fmtInt(counts[t].files)} ${TIER[t].label.toLowerCase()}`)
    .join(' · ');

// ---------------------------------------------------------------- filter bar and pager

export interface Option {
  value: string;
  text: string;
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: Option[]; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className="h-8 max-w-[16rem] text-sm text-ink">
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.text}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The filter bar shared by drive and card file lists (ui-10): Show, Copies, Camera, then "x / N files". */
export function FilterBar({
  show,
  copies,
  camera,
  shown,
  total,
}: {
  show: { value: string; options: Option[]; onChange: (v: string) => void };
  copies: { value: string; options: Option[]; onChange: (v: string) => void };
  camera: { value: string; options: Option[]; onChange: (v: string) => void };
  shown: number;
  total: number;
}) {
  const filtered = Boolean(show.value || copies.value || camera.value);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-raised/30 px-3 py-2">
      <Select label="Show" {...show} />
      <Select label="Copies" {...copies} />
      <Select label="Camera" {...camera} />
      <div className="ml-auto flex items-baseline gap-1.5" role="status" aria-live="polite" title={filtered ? 'Files in this view / all files' : 'All files'}>
        <span className={`font-mono text-sm ${filtered ? 'text-accent' : 'text-ink'}`}>
          {fmtInt(shown)} / {fmtInt(total)}
        </span>
        <span className="text-xs text-muted">{total === 1 ? 'file' : 'files'}</span>
      </div>
    </div>
  );
}

export const copyOptions = (here: string, byVolume: Array<{ volume_id: string; label: string; role: string }>): Option[] => [
  { value: '', text: 'All files' },
  { value: 'elsewhere', text: 'On other drives' },
  { value: 'only_here', text: `Only on ${here}` },
  { value: 'dup_here', text: `Duplicated on ${here}` },
  ...byVolume.map((v) => ({ value: `on:${v.volume_id}`, text: `Also on ${v.label} (${roleLabel(v.role)})` })),
];

export const cameraOptions = (cameras: Array<{ camera: string; name: string; files: number }>): Option[] => [
  { value: '', text: 'All cameras' },
  ...cameras.map((m) => ({ value: m.camera, text: `${m.name} (${fmtInt(m.files)})` })),
];

export function Pager({ offset, count, total, page, onPage }: { offset: number; count: number; total: number; page: number; onPage: (o: number) => void }) {
  return (
    <div className="flex items-center justify-center gap-3 pt-3">
      <button type="button" className="btn h-7 text-xs" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - page))}>
        Previous
      </button>
      <span className="font-mono text-2xs text-muted">{count ? `${fmtInt(offset + 1)}–${fmtInt(offset + count)} of ${fmtInt(total)}` : 'No files'}</span>
      <button type="button" className="btn h-7 text-xs" disabled={offset + count >= total} onClick={() => onPage(offset + page)}>
        Next
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- copies

interface AssetCopy {
  volume_id: string;
  label: string;
  role: string;
  rel_path: string;
  lr_referenced: boolean;
  mounted: boolean;
}

/** Where else a file's bytes are; a click lists every copy with Show in Finder. */
export function CopiesCell({
  hashed,
  drives,
  dupHere,
  here,
  open,
  onToggle,
}: {
  hashed: boolean;
  drives: Array<{ label: string; role: string }>;
  dupHere: number;
  here: string;
  open: boolean;
  onToggle: (() => void) | null;
}) {
  if (!hashed) return <span className="text-xs text-faint">–</span>;
  const names = drives.map((v) => `${v.label} (${roleLabel(v.role)})`);
  const pills = (
    <span className="flex flex-wrap gap-1">
      {names.length ? (
        <span className="chip border-go/30 bg-go/10 text-go" title={`Also on ${nameList(names)}`}>
          On {nameList(drives.map((v) => v.label))}
        </span>
      ) : (
        <span className="chip border-maybe/30 bg-maybe/10 text-maybe">Only on {here}</span>
      )}
      {dupHere > 0 && (
        <span className="chip border-accent/30 bg-accent/10 text-accent">
          ×{dupHere + 1} on {here}
        </span>
      )}
    </span>
  );
  if (!onToggle || (!names.length && !dupHere)) return pills;
  return (
    <button type="button" onClick={onToggle} aria-expanded={open} title="Show every copy" className="flex items-center gap-1 rounded-md text-left hover:opacity-80">
      {pills}
      <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex text-faint">
        <Icon name="chevron" size={11} />
      </motion.span>
    </button>
  );
}

function CopiesRow({ assetId, volumeId, relPath, cols }: { assetId: number; volumeId: string; relPath: string; cols: number }) {
  const { toast } = useStore();
  const [copies, setCopies] = useState<AssetCopy[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    api<{ copies: AssetCopy[] }>('GET', `/api/assets/${assetId}/copies`)
      .then((d) => live && setCopies(d.copies))
      .catch((err: Error) => live && setError(err.message));
    return () => {
      live = false;
    };
  }, [assetId]);
  const reveal = (c: AssetCopy) =>
    api('POST', `/api/volumes/${encodeURIComponent(c.volume_id)}/reveal`, { rel_path: c.rel_path }).catch((err: Error) => toast(err.message, 'err'));
  return (
    <tr>
      <td colSpan={cols} className="px-3 pb-3 pt-0">
        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={springs.snappy} className="rounded-lg border border-line bg-bg/50 p-2">
          {error && <p className="text-xs text-err">Could not list copies: {error}</p>}
          {!copies && !error && <p className="text-xs text-muted">Finding copies…</p>}
          <ul className="flex flex-col gap-1">
            {copies?.map((c) => {
              const self = c.volume_id === volumeId && c.rel_path === relPath;
              const isCard = roleKey(c.role) === 'card';
              return (
                <li key={`${c.volume_id}:${c.rel_path}`} className="flex flex-wrap items-center gap-2 text-xs">
                  <RolePill role={c.role} />
                  <span className="font-medium">{displayLabel(c.label)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-2xs text-muted" title={c.rel_path}>
                    {c.rel_path}
                  </span>
                  {self && <span className="text-faint">(this file)</span>}
                  {isCard && !self && <span className="text-faint">(a card, so it does not count as a copy)</span>}
                  {c.lr_referenced && <span className="chip border-line text-muted">Lightroom</span>}
                  {c.mounted ? (
                    <button type="button" className="btn-ghost h-6 px-2 text-2xs" onClick={() => reveal(c)}>
                      <Icon name="folder" size={11} /> Show in Finder
                    </button>
                  ) : (
                    <span className="text-faint">not connected</span>
                  )}
                </li>
              );
            })}
          </ul>
        </motion.div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- show in photos

/** File kinds that belong to a photo in the timeline (index.META_KINDS, plus "photo"). */
const PHOTO_KINDS = new Set(['raw', 'jpeg', 'heic', 'video', 'derivative', 'photo']);
export const isPhotoKind = (kind: string | null | undefined): boolean => Boolean(kind && PHOTO_KINDS.has(kind));

interface FilePhoto {
  group_id: number;
  day: string;
}
const photoLookups = new Map<string, Promise<FilePhoto>>();

/** A file's photo and timeline day (rv-27). Found photos are remembered; misses are asked again next time. */
export function lookupPhoto(volumeId: string, relPath: string): Promise<FilePhoto> {
  const key = `${volumeId}\n${relPath}`;
  let p = photoLookups.get(key);
  if (!p) {
    p = api<FilePhoto>('GET', `/api/volumes/${encodeURIComponent(volumeId)}/photo?rel_path=${encodeURIComponent(relPath)}`);
    photoLookups.set(key, p);
    p.catch(() => photoLookups.delete(key));
  }
  return p;
}

/** Open Photos on this file's photo: filters cleared, scrolled to and selected (rv-27). */
export function showFileInPhotos(volumeId: string, relPath: string, onError: (msg: string) => void): Promise<void> {
  return lookupPhoto(volumeId, relPath)
    .then((r) => {
      window.location.hash = hrefFor({ view: 'photos', photo: r.group_id, day: r.day });
    })
    .catch((err: Error) => onError(err.message));
}

/** Opens Photos on this file's photo, selected (rv-27). The server finds its photo and day. */
export function ShowInPhotos({ volumeId, relPath }: { volumeId: string; relPath: string }) {
  const { toast } = useStore();
  const [busy, setBusy] = useState(false);
  const go = () => {
    setBusy(true);
    void showFileInPhotos(volumeId, relPath, (m) => toast(m, 'err')).finally(() => setBusy(false));
  };
  return (
    <button type="button" className="btn-ghost h-6 whitespace-nowrap px-2 text-2xs" disabled={busy} onClick={go} title="Open this photo in Photos">
      <Icon name="image" size={11} /> Show in Photos
    </button>
  );
}

// ---------------------------------------------------------------- hover preview (rv-29)

const PREVIEW = 240;
const HOVER_MS = 350;

/** A photo row's embedded preview, floating beside the pointer. Read from the cache or a connected drive. */
function HoverPreview({ volumeId, relPath, x, y }: { volumeId: string; relPath: string; x: number; y: number }) {
  const [state, setState] = useState<{ gid: number } | 'none' | null>(null);
  const [img, setImg] = useState<'loading' | 'ok' | 'failed'>('loading');
  useEffect(() => {
    let live = true;
    setState(null);
    setImg('loading');
    lookupPhoto(volumeId, relPath)
      .then((r) => live && setState({ gid: r.group_id }))
      .catch(() => live && setState('none'));
    return () => {
      live = false;
    };
  }, [volumeId, relPath]);
  const name = relPath.split('/').pop() ?? relPath;
  const { left, top } = placeMenu(x + 16, y + 16, PREVIEW, PREVIEW + 24, window.innerWidth, window.innerHeight);
  return createPortal(
    <div
      aria-hidden="true"
      className="pointer-events-none fixed z-[110] flex flex-col overflow-hidden rounded-xl border border-line bg-surface/95 shadow-xl backdrop-blur"
      style={{ left, top, width: PREVIEW }}
    >
      <div className="flex items-center justify-center bg-ink/5" style={{ height: PREVIEW }}>
        {state && state !== 'none' && img !== 'failed' && (
          <img
            src={`/api/groups/${state.gid}/thumb`}
            alt=""
            decoding="async"
            onLoad={() => setImg('ok')}
            onError={() => setImg('failed')}
            className={`max-h-full max-w-full object-contain ${img === 'ok' ? '' : 'hidden'}`}
          />
        )}
        {(state === null || (state !== 'none' && img === 'loading')) && <span className="h-5 w-5 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />}
        {(state === 'none' || img === 'failed') && (
          <span className="flex flex-col items-center gap-1 px-4 text-center text-2xs text-faint">
            <Icon name="image" size={18} />
            {state === 'none' ? 'Not in Photos yet' : 'No preview: connect the drive once to cache it'}
          </span>
        )}
      </div>
      <p className="truncate px-2 py-1 font-mono text-2xs text-muted">{name}</p>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------- by trip (rv-48)

/** A shoot's (or "Not in a shoot" day's, or "Other files'") files, loaded when it opens. */
export interface ShootFiles<T> {
  rows: T[];
  total: number;
}

interface FetchState<T> {
  data?: ShootFiles<T>;
  loading: boolean;
  error: string;
}

function TreeRow({ depth, open, onToggle, cols, title, tone, meta }: { depth: number; open: boolean; onToggle: () => void; cols: number; title: string; tone: string; meta: string }) {
  return (
    <tr className={`border-b border-line ${depth === 0 ? 'bg-raised/30' : ''} hover:bg-ink/[0.03]`}>
      <td colSpan={cols} className="px-3 py-1.5">
        <button type="button" aria-expanded={open} onClick={onToggle} className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 text-left" style={{ paddingLeft: depth * INDENT }}>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex shrink-0 self-center text-faint">
            <Icon name="chevron" size={11} />
          </motion.span>
          <span className={`${depth === 0 ? 'text-sm font-semibold' : 'text-sm font-medium'} ${tone}`}>{title}</span>
          <span className="text-2xs text-muted">{meta}</span>
        </button>
      </td>
    </tr>
  );
}

/**
 * The Trips view of a file list (rv-48, D-134): trip, then shoot, then its files; days with no shoot under "Not in a
 * shoot"; files with no photo group under "Other files". A shoot's files load when it opens, with the list's filters.
 */
function FileTrips<T extends { rel_path: string }>({
  volumeId,
  cols,
  fetchShoot,
  filterKey,
  renderFile,
}: {
  volumeId: string;
  cols: number;
  fetchShoot: (key: string) => Promise<ShootFiles<T>>;
  filterKey: string;
  renderFile: (row: T, depth: number) => ReactNode;
}) {
  const { epoch } = useStore();
  const [outline, setOutline] = useState<Outline | null>(null);
  const [error, setError] = useState('');
  const [shut, setShut] = useState<Set<string>>(new Set()); // trips default to open, everything else to closed
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<Record<string, FetchState<T>>>({});
  const stamp = useRef('');

  useEffect(() => {
    let live = true;
    api<Outline>('GET', `/api/outline?volume_id=${encodeURIComponent(volumeId)}`)
      .then((d) => live && (setOutline(d), setError('')))
      .catch((err: Error) => live && setError(err.message));
    return () => {
      live = false;
    };
  }, [volumeId, epoch]);

  const load = (key: string) => {
    const mine = stamp.current;
    setFiles((f) => ({ ...f, [key]: { data: f[key]?.data, loading: true, error: '' } }));
    fetchShoot(key)
      .then((data) => stamp.current === mine && setFiles((f) => ({ ...f, [key]: { data, loading: false, error: '' } })))
      .catch((err: Error) => stamp.current === mine && setFiles((f) => ({ ...f, [key]: { data: f[key]?.data, loading: false, error: err.message } })));
  };
  // New filters: forget what was loaded and fetch the open ones again.
  useEffect(() => {
    stamp.current = filterKey;
    setFiles({});
    open.forEach(load);
  }, [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const flip = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };
  const toggleShoot = (key: string) => {
    const opening = !open.has(key);
    setOpen((o) => flip(o, key));
    if (opening && !files[key]) load(key);
  };

  if (error) return <tr><td colSpan={cols} className="px-3 py-4 text-sm text-err">Could not load the outline: {error}</td></tr>;
  if (!outline) return <tr><td colSpan={cols} className="px-3 py-4 text-center text-sm text-muted">Loading…</td></tr>;

  const filesOf = (key: string, depth: number): ReactNode => {
    const st = files[key];
    if (!st || (st.loading && !st.data)) return <tr key={`${key}-l`}><td colSpan={cols} className="py-2 text-xs text-muted" style={{ paddingLeft: 12 + depth * INDENT }}>Loading…</td></tr>;
    if (st.error) return <tr key={`${key}-e`}><td colSpan={cols} className="py-2 text-xs text-err" style={{ paddingLeft: 12 + depth * INDENT }}>Could not list files: {st.error}</td></tr>;
    const d = st.data!;
    return (
      <Fragment key={`${key}-f`}>
        {d.rows.map((r) => renderFile(r, depth))}
        {d.rows.length === 0 && <tr><td colSpan={cols} className="py-2 text-xs text-muted" style={{ paddingLeft: 12 + depth * INDENT }}>No files match the filters.</td></tr>}
        {d.total > d.rows.length && (
          <tr><td colSpan={cols} className="py-2 text-xs text-muted" style={{ paddingLeft: 12 + depth * INDENT }}>Showing the first {fmtInt(d.rows.length)} of {fmtInt(d.total)}. Use the List view for the rest.</td></tr>
        )}
      </Fragment>
    );
  };
  const shootRows = (s: OutlineShoot, depth: number): ReactNode =>
    (s.files ?? 0) > 0 && (
      <Fragment key={s.key}>
        <TreeRow depth={depth} open={open.has(s.key)} onToggle={() => toggleShoot(s.key)} cols={cols} title={s.title} tone={titleTone(s.state)} meta={[shootLine(s), countsLine(s)].filter(Boolean).join(' · ')} />
        {open.has(s.key) && filesOf(s.key, depth + 1)}
      </Fragment>
    );

  const loose = outline.items.filter((i) => i.kind === 'day' && (i.files ?? 0) > 0);
  const looseFiles = loose.reduce((n, i) => n + (i.files ?? 0), 0);
  const looseBytes = loose.reduce((n, i) => n + (i.bytes ?? 0), 0);
  const other = outline.other;
  return (
    <>
      {outline.items.map((it) => {
        if (it.kind === 'shoot') return shootRows(it, 0);
        if (it.kind !== 'trip' || !(it.files ?? 0)) return null;
        const isOpen = !shut.has(it.key);
        return (
          <Fragment key={it.key}>
            <TreeRow depth={0} open={isOpen} onToggle={() => setShut((x) => flip(x, it.key))} cols={cols} title={it.name} tone={titleTone(it.state as OutlineState)} meta={[dateRange(it.start_date, it.end_date), `${fmtInt(it.shoots.length)} ${it.shoots.length === 1 ? 'shoot' : 'shoots'}`, countsLine(it)].join(' · ')} />
            {isOpen && it.shoots.map((s) => shootRows(s, 1))}
          </Fragment>
        );
      })}
      {loose.length > 0 && (
        <>
          <TreeRow depth={0} open={open.has('~loose')} onToggle={() => setOpen((o) => flip(o, '~loose'))} cols={cols} title="Not in a shoot" tone="text-ink" meta={`${fmtInt(looseFiles)} files · ${fmtBytes(looseBytes)}`} />
          {open.has('~loose') &&
            loose.map((it) =>
              it.kind === 'day' ? (
                <Fragment key={it.key}>
                  <TreeRow depth={1} open={open.has(it.key)} onToggle={() => toggleShoot(it.key)} cols={cols} title={dayLabel(it.day)} tone="text-ink" meta={countsLine(it)} />
                  {open.has(it.key) && filesOf(it.key, 2)}
                </Fragment>
              ) : null,
            )}
        </>
      )}
      {other && other.files > 0 && (
        <>
          <TreeRow depth={0} open={open.has('other')} onToggle={() => toggleShoot('other')} cols={cols} title="Other files" tone="text-ink" meta={`${fmtInt(other.files)} files · ${fmtBytes(other.bytes)}`} />
          {open.has('other') && filesOf('other', 1)}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- the table

export interface Column<T> {
  head: string;
  num?: boolean;
  /** The column that holds the file name: indented in the folder view. */
  path?: boolean;
  /** `name` is the file name in the folder view and the full path in the list view. */
  cell: (row: T, open: boolean, toggle: (() => void) | null, name: string) => ReactNode;
}

const INDENT = 18;

function GroupRow<T>({
  node,
  depth,
  open,
  onToggle,
  cols,
  summarize,
}: {
  node: FolderNode<T> | RunNode<T>;
  depth: number;
  open: boolean;
  onToggle: () => void;
  cols: number;
  summarize?: (rows: T[]) => ReactNode;
}) {
  const folder = node.kind === 'folder';
  return (
    <tr className={`border-b border-line ${folder ? 'bg-raised/30' : ''} hover:bg-ink/[0.03]`}>
      <td colSpan={cols} className="px-3 py-1.5">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
          style={{ paddingLeft: depth * INDENT }}
          title={folder ? node.key.slice(0, -1) : `${fmtInt(node.rows.length)} files named like ${node.pattern}`}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex text-faint">
              <Icon name="chevron" size={11} />
            </motion.span>
            {folder ? (
              <>
                <span className="inline-flex text-muted">
                  <Icon name="folder" size={13} />
                </span>
                <span className="break-all font-mono text-xs font-medium">{node.name}</span>
              </>
            ) : (
              <>
                <span className="break-all font-mono text-xs">{node.label}</span>
                {node.exts.length > 0 && <span className="text-2xs text-faint">{node.exts.join(' · ')}</span>}
              </>
            )}
            <span className="chip border-line text-2xs text-muted">{fmtInt(node.rows.length)}</span>
          </span>
          {summarize && <span className="ml-auto flex flex-wrap items-center gap-2 text-2xs text-muted">{summarize(node.rows)}</span>}
        </button>
      </td>
    </tr>
  );
}

/**
 * A file table whose rows can open a copies row under them. The folder view (rv-26) nests rows by folder and
 * collapses runs of similar names; the list view is the flat page in server order.
 */
export function FileTable<T extends { rel_path: string; asset_id?: number | null; kind?: string | null }>({
  rows,
  columns,
  volumeId,
  mounted = false,
  loading,
  summarize,
  pager,
  fetchShoot,
  filterKey = '',
}: {
  rows: T[];
  columns: Array<Column<T>>;
  volumeId: string;
  /** The drive or card is connected: the menu offers Show in Finder. */
  mounted?: boolean;
  loading: boolean;
  /** One line for a folder or run row: e.g. total size and states. */
  summarize?: (rows: T[]) => ReactNode;
  /** The page controls under the Folders and List views (the Trips view loads per shoot instead). */
  pager?: ReactNode;
  /** One shoot's files (`other` = no photo group) with the list's filters; turns on the Trips view (rv-48). */
  fetchShoot?: (key: string) => Promise<ShootFiles<T>>;
  /** Changes when the list's filters do, so the Trips view reloads. */
  filterKey?: string;
}) {
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [view, setView] = usePref<'trips' | 'tree' | 'list'>('pv.files.view', 'tree');
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(rows), [rows]);
  const keys = useMemo(() => allKeys(tree), [tree]);
  const { toast } = useStore();
  // Hover preview (rv-29): a photo row shows its preview after a short rest; moving off, clicking or scrolling hides it.
  const [hover, setHover] = useState<{ relPath: string; x: number; y: number } | null>(null);
  const hoverTimer = useRef(0);
  const pointer = useRef({ x: 0, y: 0 });
  const endHover = () => {
    window.clearTimeout(hoverTimer.current);
    setHover(null);
  };
  useEffect(() => {
    if (!hover) return undefined;
    const hide = () => setHover(null);
    window.addEventListener('scroll', hide, true);
    return () => window.removeEventListener('scroll', hide, true);
  }, [hover]);
  useEffect(() => () => window.clearTimeout(hoverTimer.current), []);
  if (!rows.length) return <p className="py-4 text-center text-sm text-muted">{loading ? 'Loading…' : 'No files in this view.'}</p>;

  const isTrips = view === 'trips' && Boolean(fetchShoot);
  const isTree = view === 'tree' || (view === 'trips' && !fetchShoot);
  const flip = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };
  const visible = isTree
    ? visibleRows(tree, closed, opened)
    : rows.map((r) => ({ node: { kind: 'file' as const, key: r.rel_path, name: r.rel_path, row: r }, depth: 0 }));

  const fileRow = (r: T, name: string, depth: number) => {
    const open = openRow === r.rel_path;
    const toggle = r.asset_id ? () => setOpenRow(open ? null : r.rel_path) : null;
    const photo = Boolean(volumeId) && isPhotoKind(r.kind);
    const base = r.rel_path.split('/').pop() ?? r.rel_path;
    const copied = (what: string) => (ok: boolean) => toast(ok ? `Copied the ${what}.` : 'Could not copy.', ok ? 'ok' : 'err');
    const onMenu = (e: React.MouseEvent) => {
      endHover();
      openMenu(
        e,
        [
          photo && { label: 'Show in All Photos', icon: 'image', onSelect: () => void showFileInPhotos(volumeId, r.rel_path, (m) => toast(m, 'err')) },
          mounted && {
            label: 'Show in Finder',
            icon: 'folder',
            onSelect: () =>
              void api('POST', `/api/volumes/${encodeURIComponent(volumeId)}/reveal`, { rel_path: r.rel_path }).catch((err: Error) => toast(err.message, 'err')),
          },
          toggle && { label: open ? 'Hide copies' : 'Show every copy', icon: 'drive', onSelect: toggle },
          'sep',
          { label: 'Copy file name', icon: 'copy', onSelect: () => void copyText(base).then(copied('file name')) },
          { label: 'Copy path on the drive', icon: 'copy', onSelect: () => void copyText(r.rel_path).then(copied('path')) },
        ],
        r.rel_path,
      );
    };
    const hoverProps = photo
      ? {
          onMouseEnter: (e: React.MouseEvent) => {
            pointer.current = { x: e.clientX, y: e.clientY };
            window.clearTimeout(hoverTimer.current);
            hoverTimer.current = window.setTimeout(() => setHover({ relPath: r.rel_path, ...pointer.current }), HOVER_MS);
          },
          // The pointer is tracked in a ref: re-rendering a 1000-row table on every move would be slow.
          onMouseMove: (e: React.MouseEvent) => {
            pointer.current = { x: e.clientX, y: e.clientY };
          },
          onMouseLeave: endHover,
          onMouseDown: endHover,
        }
      : {};
    return (
      <Fragment key={r.rel_path}>
        <tr className="border-b border-line hover:bg-ink/[0.03]" onContextMenu={onMenu} {...hoverProps}>
          {columns.map((c) => (
            <td
              key={c.head}
              className={`px-3 py-2 align-top ${c.num ? 'text-right font-mono text-xs text-muted' : ''}`}
              style={c.path && depth ? { paddingLeft: 12 + depth * INDENT + 17 } : undefined}
            >
              {c.cell(r, open, toggle, name)}
            </td>
          ))}
        </tr>
        {open && r.asset_id && <CopiesRow assetId={r.asset_id} volumeId={volumeId} relPath={r.rel_path} cols={columns.length} />}
      </Fragment>
    );
  };

  const toolBtn = 'btn-ghost h-6 px-2 text-2xs';
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-line p-0.5" role="group" aria-label="File view">
          {(fetchShoot ? (['trips', 'tree', 'list'] as const) : (['tree', 'list'] as const)).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={(isTrips ? 'trips' : isTree ? 'tree' : 'list') === v}
              onClick={() => setView(v)}
              className={`h-6 rounded-md px-2.5 text-2xs ${(isTrips ? 'trips' : isTree ? 'tree' : 'list') === v ? 'bg-ink/10 text-ink' : 'text-muted hover:text-ink'}`}
            >
              {v === 'trips' ? 'Trips' : v === 'tree' ? 'Folders' : 'List'}
            </button>
          ))}
        </div>
        {isTree && (keys.folders.length > 0 || keys.runs.length > 0) && (
          <>
            <button
              type="button"
              className={toolBtn}
              onClick={() => {
                setClosed(new Set());
                setOpened(new Set(keys.runs));
              }}
            >
              Expand all
            </button>
            <button
              type="button"
              className={toolBtn}
              onClick={() => {
                setClosed(new Set(keys.folders));
                setOpened(new Set());
              }}
            >
              Collapse all
            </button>
          </>
        )}
      </div>
      <div className={`overflow-x-auto rounded-xl border border-line transition-opacity ${loading ? 'opacity-60' : ''}`}>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line bg-raised/40 text-left">
              {columns.map((c) => (
                <th key={c.head} className={`label px-3 py-2.5 font-normal ${c.num ? 'text-right' : ''}`}>
                  {c.head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isTrips && fetchShoot && (
              <FileTrips<T> volumeId={volumeId} cols={columns.length} fetchShoot={fetchShoot} filterKey={filterKey} renderFile={(r, depth) => fileRow(r, r.rel_path.split('/').pop() ?? r.rel_path, depth)} />
            )}
            {!isTrips && visible.map(({ node, depth }) =>
              node.kind === 'file' ? (
                fileRow(node.row, node.name, depth)
              ) : (
                <GroupRow
                  key={node.key}
                  node={node}
                  depth={depth}
                  cols={columns.length}
                  summarize={summarize}
                  open={node.kind === 'folder' ? !closed.has(node.key) : opened.has(node.key)}
                  onToggle={() => (node.kind === 'folder' ? setClosed((s) => flip(s, node.key)) : setOpened((s) => flip(s, node.key)))}
                />
              ),
            )}
          </tbody>
        </table>
      </div>
      {!isTrips && pager}
      {hover && <HoverPreview volumeId={volumeId} relPath={hover.relPath} x={hover.x} y={hover.y} />}
    </div>
  );
}
