import { motion } from 'framer-motion';
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { api } from '../lib/api';
import { fmtInt } from '../lib/format';
import { roleKey, roleLabel } from '../lib/labels';
import { springs } from '../lib/motion';
import { useStore } from '../lib/store';
import { TIER, TIER_ORDER, TIER_TONE, nameList, type Tier, type TierCounts } from '../lib/tiers';
import { Icon, RolePill } from './bits';

// ---------------------------------------------------------------- tier bar and pill

/** A card's files as one bar of tier segments, worst first; each segment grows to its share. */
export function TierBar({ counts, total, className = '' }: { counts: TierCounts; total: number; className?: string }) {
  const label = TIER_ORDER.map((t) => `${fmtInt(counts[t].files)} ${TIER[t].label.toLowerCase()}`).join(', ');
  return (
    <div role="img" aria-label={label} className={`flex h-2 w-full gap-[2px] overflow-hidden rounded-full bg-ink/[0.07] ${className}`}>
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
                  <span className="font-medium">{c.label}</span>
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

// ---------------------------------------------------------------- the table

export interface Column<T> {
  head: string;
  num?: boolean;
  cell: (row: T, open: boolean, toggle: (() => void) | null) => ReactNode;
}

/** A file table whose rows can open a copies row under them. */
export function FileTable<T extends { rel_path: string; asset_id?: number | null }>({
  rows,
  columns,
  volumeId,
  loading,
}: {
  rows: T[];
  columns: Array<Column<T>>;
  volumeId: string;
  loading: boolean;
}) {
  const [openRow, setOpenRow] = useState<string | null>(null);
  if (!rows.length) return <p className="py-4 text-center text-sm text-muted">{loading ? 'Loading…' : 'No files in this view.'}</p>;
  return (
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
          {rows.map((r) => {
              const open = openRow === r.rel_path;
              const toggle = r.asset_id ? () => setOpenRow(open ? null : r.rel_path) : null;
              return (
                <Fragment key={r.rel_path}>
                  <tr className="border-b border-line hover:bg-ink/[0.03]">
                    {columns.map((c) => (
                      <td key={c.head} className={`px-3 py-2 align-top ${c.num ? 'text-right font-mono text-xs text-muted' : ''}`}>
                        {c.cell(r, open, toggle)}
                      </td>
                    ))}
                  </tr>
                  {open && r.asset_id && <CopiesRow assetId={r.asset_id} volumeId={volumeId} relPath={r.rel_path} cols={columns.length} />}
                </Fragment>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}
