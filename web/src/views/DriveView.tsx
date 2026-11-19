import { motion, useReducedMotion } from 'framer-motion';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CountUp, Disclosure, FsPill, Icon, RolePill, SelectTile } from '../components/bits';
import { cameraOptions, CopiesCell, copyOptions, FileTable, FilterBar, isPhotoKind, Pager, ShowInPhotos, type Column } from '../components/FileList';
import { ProgressBar } from '../components/ProgressBar';
import { extVM, taskVM } from '../components/TaskCard';
import { api } from '../lib/api';
import { fmtBytes, fmtInt, plural } from '../lib/format';
import {
  baseName,
  driveName,
  liveStep,
  liveTaskKey,
  pace,
  roleKey,
  runDetail,
  runWhen,
  STEP_HINT,
  STEP_NAME,
  stepView,
  workflowSteps,
  type StepTone,
  type WorkflowStep,
} from '../lib/labels';
import { rise, springs } from '../lib/motion';
import { hrefFor } from '../lib/route';
import { UnpluggedStrip, useUnpluggedEdge } from '../components/Unplugged';
import { RenameTitle } from '../components/RenameTitle';
import { EjectButton } from '../components/Eject';
import { useStore } from '../lib/store';
import type { Drive, DriveSummary, DriveUsage } from '../lib/types';

const TILES = [
  { key: 'indexed', label: 'Not hashed yet', tone: 'text-accent' },
  { key: 'verified', label: 'Hashed', tone: 'text-go' },
  { key: 'mismatch', label: 'Unreadable', tone: 'text-err' },
  { key: 'missing', label: 'Missing', tone: 'text-maybe' },
] as const;

const FILE_PAGE = 1000;

const STATE_TONE: Record<string, string> = {
  verified: 'border-go/30 bg-go/10 text-go',
  indexed: 'border-accent/30 bg-accent/10 text-accent',
  mismatch: 'border-err/30 bg-err/10 text-err',
  missing: 'border-maybe/30 bg-maybe/10 text-maybe',
};

interface FileFilter {
  state: string;
  copies: string;
  other: string;
  camera: string;
  offset: number;
}
const NO_FILTER: FileFilter = { state: '', copies: '', other: '', camera: '', offset: 0 };

interface DriveFile {
  rel_path: string;
  size: number;
  kind: string;
  state: string;
  asset_id: number | null;
  copies: number | null;
  dup_here: number;
  camera: string;
  camera_name: string;
  copy_drives?: Array<{ label: string; role: string }>;
}
interface FilePage {
  files: DriveFile[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------- pieces

function Ring({ value, size = 44 }: { value: number; size?: number }) {
  const reduce = useReducedMotion();
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--c-ink) / 0.08)" strokeWidth={5} />
      <motion.circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="rgb(var(--c-go))"
        strokeWidth={5}
        strokeLinecap="round"
        strokeDasharray={c}
        initial={{ strokeDashoffset: c }}
        animate={{ strokeDashoffset: c * (1 - Math.max(0, Math.min(1, value))) }}
        transition={reduce ? { duration: 0 } : springs.glide}
      />
    </svg>
  );
}

const DOT: Record<StepTone, string> = {
  done: 'border-go bg-go text-bg',
  running: 'border-accent bg-accent text-on-accent ring-pulse',
  warn: 'border-maybe bg-maybe/15 text-maybe',
  queued: 'border-line-strong bg-raised text-muted',
  never: 'border-line-strong bg-bg text-faint',
};
const WHEN: Record<StepTone, string> = {
  done: 'text-muted',
  running: 'text-accent font-medium',
  warn: 'text-maybe',
  queued: 'text-faint',
  never: 'text-faint',
};

function Timeline({ d, steps, summary }: { d: Drive; steps: WorkflowStep[]; summary: DriveSummary }) {
  const { tasks, elsewhere, workflow, now, runStep, busy, drives, driveFiles } = useStore();
  const live = liveStep(d, tasks, elsewhere);
  const wf = workflow && workflow.volumeId === d.volume_id && !workflow.outcome ? workflow : null;
  const isBusy = busy(d) || Boolean(live);
  // The same numbers as the task cards, so a resumed hash shows the drive's progress here too.
  const files = { ...driveFiles, [d.mount]: summary.files };
  const liveHref = live?.task ? hrefFor({ view: 'home', task: `t${live.task.id}` }) : live?.ext ? hrefFor({ view: 'home', task: `p${live.ext.pid}` }) : null;
  const liveProgress = live?.task ? taskVM(live.task, drives, now, files).pace : live?.ext ? extVM(live.ext, drives, now, files).pace : null;

  const views = steps.map((s, i) => {
    const queued = Boolean(wf && i > wf.at);
    return { step: s, ...stepView(s.id, summary.steps[s.id], live?.step ?? null, queued) };
  });

  return (
    <ol className="grid gap-6 md:gap-4" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
      {views.map((v, i) => {
        const next = views[i + 1];
        // The line after a step is full once the step is done, and fills with its progress while it runs.
        const fill = v.tone === 'done' ? 1 : v.tone === 'running' ? (liveProgress?.fraction ?? 0) : 0;
        return (
          <li key={v.step.id} className="relative min-w-0">
            <div className="flex items-center">
              <span className={`relative z-[1] flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold ${DOT[v.tone]}`}>
                {v.tone === 'done' ? <Icon name="check" size={14} /> : v.tone === 'warn' ? '!' : i + 1}
              </span>
              {next && (
                <div className="mx-2 h-[3px] flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                  <motion.div
                    className={`h-full rounded-full ${v.tone === 'running' ? 'bg-accent' : 'bg-go'}`}
                    initial={false}
                    animate={{ width: `${fill * 100}%` }}
                    transition={springs.glide}
                  />
                </div>
              )}
            </div>
            <h3 className="mt-3 font-semibold">{STEP_NAME[v.step.id]}</h3>
            <p className={`text-sm ${WHEN[v.tone]}`}>{v.when}</p>
            <p className="text-xs text-faint">{STEP_HINT[v.step.id]}</p>
            {v.tone === 'running' && liveProgress && (
              <div className="mt-2.5 pr-4">
                <a href={liveHref ?? undefined} title="Open in Tasks" className="group/live block rounded-md">
                  <ProgressBar value={liveProgress.fraction} live label={`${STEP_NAME[v.step.id]} progress`} />
                  <p className="mt-1.5 font-mono text-2xs text-muted">
                    {liveProgress.total ? `${fmtInt(liveProgress.current)} of ${fmtInt(liveProgress.total)}` : fmtInt(liveProgress.current)}
                    {liveProgress.fraction !== null && <span className="text-faint"> · {Math.floor(liveProgress.fraction * 100)}%</span>}
                    {liveHref && <span className="ml-2 font-sans text-accent opacity-0 group-hover/live:opacity-100">Open in Tasks →</span>}
                  </p>
                </a>
              </div>
            )}
            {v.tone !== 'running' && d.mounted && (
              <button
                type="button"
                className="btn-ghost mt-2 h-7 px-2 text-xs"
                disabled={isBusy}
                onClick={() => runStep(d, v.step)}
                title={isBusy ? 'Something is already running on this drive' : `Run only ${STEP_NAME[v.step.id].toLowerCase()} on ${driveName(d)}`}
              >
                <Icon name="play" size={10} /> Run
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

const USAGE_LABEL: Record<string, string> = {
  tracked: 'Photos and camera video',
  lightroom: 'Lightroom catalogs and previews',
  photo_extras: 'Camera and editor extras',
  video_no_camera: 'Video with no camera (movies, downloads)',
  video_other: 'Other video formats',
  audio: 'Audio',
  image_other: 'Other images (PNG, GIF…)',
  documents: 'Documents',
  archives: 'Archives and disk images',
  software: 'Apps and code',
  other: 'Other files',
};

/** idx-13: one bar for the whole drive, then what each slice holds. */
function UsageCard({ u }: { u: DriveUsage }) {
  const reduce = useReducedMotion();
  const total = u.capacity ?? u.used ?? u.photo.bytes + u.non_photo.bytes;
  const slices = [
    { key: 'photo', label: 'Photography', bytes: u.photo.bytes, bar: 'bg-accent', dot: 'bg-accent' },
    { key: 'non', label: 'Not photography', bytes: u.non_photo.bytes, bar: 'bg-maybe', dot: 'bg-maybe' },
    { key: 'unacc', label: 'Unaccounted', bytes: u.unaccounted ?? 0, bar: 'bg-ink/25', dot: 'bg-ink/25' },
    { key: 'free', label: 'Free', bytes: u.free ?? 0, bar: 'bg-ink/[0.06]', dot: 'bg-ink/10' },
  ].filter((x) => x.bytes > 0);
  const pct = (b: number) => (total > 0 ? (100 * b) / total : 0);
  const share = u.photo.bytes + u.non_photo.bytes > 0 ? u.photo.bytes / (u.photo.bytes + u.non_photo.bytes) : 0;
  return (
    <section className="card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">Space on this drive</h2>
        <p className="text-sm text-muted">
          {Math.round(share * 100)}% of what is counted is photography
        </p>
      </div>
      <div className="mt-4 flex h-3 w-full overflow-hidden rounded-full bg-ink/[0.06]" role="img"
        aria-label={slices.map((x) => `${x.label} ${fmtBytes(x.bytes)}`).join(', ')}>
        {slices.map((x) => (
          <motion.div
            key={x.key}
            className={`h-full ${x.bar}`}
            initial={reduce ? false : { width: 0 }}
            animate={{ width: `${pct(x.bytes)}%` }}
            transition={springs.snappy}
            title={`${x.label}: ${fmtBytes(x.bytes)}`}
          />
        ))}
      </div>
      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <UsageList title="Photography" dot="bg-accent" total={u.photo.bytes} parts={u.photo.parts} />
        <UsageList title="Not photography" dot="bg-maybe" total={u.non_photo.bytes} parts={u.non_photo.parts} />
      </div>
      {(u.unaccounted != null || u.free != null) && (
        <p className="mt-4 text-xs text-muted">
          {u.unaccounted != null && (
            <>
              <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-ink/25 align-middle" />
              Unaccounted {fmtBytes(u.unaccounted)}: system and hidden folders, the recycle bin, and anything the index
              could not read.{' '}
            </>
          )}
          {u.free != null && <>Free {fmtBytes(u.free)}.</>}
        </p>
      )}
    </section>
  );
}

function UsageList({ title, dot, total, parts }: { title: string; dot: string; total: number; parts: DriveUsage['photo']['parts'] }) {
  return (
    <div>
      <p className="flex items-center gap-2 text-sm font-medium">
        <span className={`h-2 w-2 rounded-full ${dot}`} />
        {title}
        <span className="ml-auto font-mono text-ink/80">{fmtBytes(total)}</span>
      </p>
      {parts.length === 0 ? (
        <p className="mt-2 text-xs text-faint">Nothing found</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {parts.filter((p) => p.bytes > 0).map((p) => (
            <li key={p.category} className="flex items-baseline gap-2 text-xs text-muted">
              <span className="min-w-0 flex-1 truncate">{USAGE_LABEL[p.category] ?? p.category}</span>
              <span className="text-faint">{fmtInt(p.files)} files</span>
              <span className="w-20 text-right font-mono text-ink/80">{fmtBytes(p.bytes)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A count tile that filters the file list below; `group` lets the selection ring glide between its siblings. */
function Tile({
  label,
  value,
  sub,
  tone,
  group,
  onClick,
  active,
}: {
  label: string;
  value: number;
  sub: string;
  tone: string;
  group: string;
  onClick: () => void;
  active: boolean;
}) {
  return (
    <SelectTile group={group} active={active} onClick={onClick}>
      <p className={`text-sm ${tone}`}>{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">
        <CountUp value={value} />
      </p>
      <p className="mt-0.5 font-mono text-2xs text-muted">{sub}</p>
    </SelectTile>
  );
}

/** The drive's name (renameable, rv-40) with its role, and the volume's own name on disk when it differs. */
function DriveTitle({ drive }: { drive: Drive }) {
  const name = driveName(drive);
  const volName = drive.local ? '' : baseName(drive.mount);
  return (
    <div>
      <RenameTitle drive={drive}>
        <RolePill role={drive.role} />
      </RenameTitle>
      {volName && volName !== name && (
        <p className="mt-0.5 text-xs text-faint" title="The name macOS shows in Finder">
          Volume name on disk: <span className="font-mono text-muted">{volName}</span>
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- the view

export function DriveView({ id }: { id: string }) {
  const { drives, tasks, elsewhere, workflow, epoch, runWorkflow, busy, toast } = useStore();
  const [summary, setSummary] = useState<DriveSummary | null>(null);
  const [error, setError] = useState('');
  const [runsOpen, setRunsOpen] = useState(false);
  const [filter, setFilter] = useState<FileFilter>(NO_FILTER);
  const [files, setFiles] = useState<FilePage | null>(null);
  const [filesLoading, setFilesLoading] = useState(true);
  const [filesError, setFilesError] = useState('');
  const filesRef = useRef<HTMLElement>(null);
  const drive = drives.find((x) => x.volume_id === id) ?? summary?.drive ?? null;
  const live = drive ? liveStep(drive, tasks, elsewhere) : null;
  const edge = useUnpluggedEdge(drive);

  const load = useCallback(async () => {
    try {
      const s = await api<DriveSummary>('GET', `/api/volumes/${encodeURIComponent(id)}`);
      setSummary(s);
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
  }, [id]);

  useEffect(() => {
    setSummary(null);
    setFilter((f) => (f === NO_FILTER ? f : NO_FILTER));
    setFiles(null);
  }, [id]);
  useEffect(() => {
    void load();
  }, [load, epoch]);

  // The file list loads on its own: the summary is slow, and the list pages through many files.
  const patchFilter = (patch: Partial<FileFilter>) => setFilter((f) => ({ ...f, ...patch, offset: 0 }));
  const pickState = (key: string) => {
    patchFilter({ state: filter.state === key ? '' : key });
    filesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  // Clicking a copies tile or drive row sets the Copies filter (clicking again clears it) and scrolls to the list.
  const pickCopies = (v: string) => {
    const cur = filter.other ? `on:${filter.other}` : filter.copies;
    const next = cur === v ? '' : v;
    patchFilter(next.startsWith('on:') ? { copies: '', other: next.slice(3) } : { copies: next, other: '' });
    filesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const curCopies = filter.other ? `on:${filter.other}` : filter.copies;
  const loadFiles = useCallback(async () => {
    const p = new URLSearchParams({ limit: String(FILE_PAGE), offset: String(filter.offset) });
    if (filter.state) p.set('state', filter.state);
    if (filter.copies) p.set('copies', filter.copies);
    if (filter.other) p.set('other', filter.other);
    if (filter.camera) p.set('camera', filter.camera);
    setFilesLoading(true);
    try {
      setFiles(await api<FilePage>('GET', `/api/volumes/${encodeURIComponent(id)}/files?${p}`));
      setFilesError('');
    } catch (err) {
      setFilesError((err as Error).message);
    } finally {
      setFilesLoading(false);
    }
  }, [id, filter]);
  useEffect(() => {
    void loadFiles();
  }, [loadFiles, epoch]);
  // While something runs here, the counts move: reload every few seconds.
  useEffect(() => {
    if (!live) return undefined;
    const t = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(t);
  }, [live, load]);

  if (error && !summary) {
    return (
      <div className="card p-6">
        <p className="text-err">{error}</p>
        <a href="#/" className="btn mt-4">
          Back to tasks
        </a>
      </div>
    );
  }
  if (!summary || !drive) {
    return (
      <div className="card flex flex-col gap-4 p-5">
        <div className="h-7 w-48 animate-pulse rounded-lg bg-raised" />
        <div className="h-24 animate-pulse rounded-xl bg-raised/60" />
        <div className="grid grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-raised/50" />
          ))}
        </div>
      </div>
    );
  }

  const s = summary;
  const steps = workflowSteps(drive);
  const hashed = Object.values(s.states).reduce((n, x) => n + x.hashed, 0);
  const frac = s.files ? hashed / s.files : 0;
  const wfHere = workflow && workflow.volumeId === drive.volume_id;
  const isBusy = busy(drive) || Boolean(live);
  // The busy chip opens the run on the Tasks page (rv-37); between workflow steps, just Tasks.
  const busyHref = hrefFor({ view: 'home', task: drive ? liveTaskKey(drive, tasks, elsewhere, drives) ?? undefined : undefined });
  const copies = s.copies;
  const maxOther = copies ? Math.max(1, ...copies.by_volume.map((v) => v.files)) : 1;
  const reveal = () =>
    api('POST', `/api/volumes/${encodeURIComponent(id)}/reveal`, {}).catch((err: Error) => toast(err.message, 'err'));
  const revealFile = (relPath: string) =>
    api('POST', `/api/volumes/${encodeURIComponent(id)}/reveal`, { rel_path: relPath }).catch((err: Error) => toast(err.message, 'err'));

  const columns: Array<Column<DriveFile>> = [
    { head: 'Path', path: true, cell: (f, _o, _t, name) => <p className="min-w-[16rem] break-all font-mono text-xs" title={f.rel_path}>{name}</p> },
    { head: 'Size', num: true, cell: (f) => fmtBytes(f.size) },
    { head: 'Kind', cell: (f) => <span className="text-xs text-muted">{f.kind}</span> },
    { head: 'Camera', cell: (f) => <span className="text-xs text-muted">{f.camera_name}</span> },
    {
      head: 'State',
      cell: (f) => <span className={`chip ${STATE_TONE[f.state] ?? 'border-line text-muted'}`}>{TILES.find((t) => t.key === f.state)?.label ?? f.state}</span>,
    },
    {
      head: 'Copies',
      cell: (f, open, toggle) => (
        <CopiesCell
          hashed={f.state === 'verified' && f.copies != null}
          drives={f.copy_drives ?? []}
          dupHere={f.dup_here}
          here="this drive"
          open={open}
          onToggle={toggle}
        />
      ),
    },
  ];
  columns.push({
    head: '',
    cell: (f) => (
      <div className="flex justify-end gap-1">
        {isPhotoKind(f.kind) && <ShowInPhotos volumeId={id} relPath={f.rel_path} />}
        {drive.mounted && (
          <button type="button" className="btn-ghost h-6 whitespace-nowrap px-2 text-2xs" onClick={() => revealFile(f.rel_path)}>
            <Icon name="folder" size={11} /> Show in Finder
          </button>
        )}
      </div>
    ),
  });

  const summarizeFiles = (rows: DriveFile[]) => {
    const counts = new Map<string, number>();
    for (const r of rows) counts.set(r.state, (counts.get(r.state) ?? 0) + 1);
    return (
      <>
        <span className="font-mono">{fmtBytes(rows.reduce((n, r) => n + r.size, 0))}</span>
        {[...counts].map(([state, n]) => (
          <span key={state} className={`chip ${STATE_TONE[state] ?? 'border-line text-muted'}`}>
            {fmtInt(n)} {(TILES.find((t) => t.key === state)?.label ?? state).toLowerCase()}
          </span>
        ))}
      </>
    );
  };

  return (
    <motion.div key={id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={springs.snappy} className="flex flex-col gap-5">
      {/* Head */}
      <section className={`card p-5 ${edge}`}>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <DriveTitle drive={drive} />
            <p className="mt-1 flex items-center gap-2 text-sm text-muted">
              <span className={`h-2 w-2 rounded-full ${drive.mounted ? 'bg-go' : 'bg-skip'}`} />
              {drive.mounted ? 'Connected' : 'Not connected'}
              <span className="text-faint">·</span>
              <FsPill fs={drive.fs} />
              <span className="font-mono text-xs">
                {[drive.size && `${drive.size} total`, drive.free && `${drive.free} free`].filter(Boolean).join(' · ')}
              </span>
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {steps.length > 0 && drive.mounted && isBusy && (
              <a
                href={busyHref}
                title="Open in Tasks"
                className="inline-flex h-8 items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 text-sm text-accent hover:border-accent/60 hover:bg-accent/15"
              >
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-accent" />
                {wfHere ? 'Workflow running' : live ? `Running · ${STEP_NAME[live.step]}` : 'Busy'}
                <span aria-hidden>→</span>
              </a>
            )}
            {steps.length > 0 && drive.mounted && !isBusy && (
              <button type="button" className="btn-primary" onClick={() => runWorkflow(drive)}>
                <Icon name="play" size={11} /> Run workflow
              </button>
            )}
            <button
              type="button"
              className="btn"
              onClick={reveal}
              disabled={!drive.mounted}
              title={drive.mounted ? undefined : 'Connect this drive to show it in Finder'}
            >
              <Icon name="folder" /> Show in Finder
            </button>
            <EjectButton d={drive} />
            <a href="#/" className="btn">
              <Icon name="back" /> Tasks
            </a>
          </div>
        </div>

        <UnpluggedStrip d={drive} />

        {steps.length > 0 && (
          <div className="mt-6 border-t border-line pt-6">
            <Timeline d={drive} steps={steps} summary={s} />
          </div>
        )}
      </section>

      {s.usage && <UsageCard u={s.usage} />}
      {!s.usage && s.files > 0 && (
        <p className="-mt-2 px-1 text-xs text-muted">
          Index this drive again to see how much of it is photography and what the rest is.
        </p>
      )}

      {s.files === 0 && (
        <section className="card flex flex-col items-center gap-1 px-6 py-10 text-center">
          <h2 className="text-lg font-semibold">Nothing indexed on this drive yet</h2>
          <p className="text-sm text-muted">Press Run workflow to list its files, verify them and read their metadata.</p>
        </section>
      )}

      {s.files > 0 && (
        <>
      {/* Files */}
      <section className="card p-5">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <p className="label">Files</p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">
              <CountUp value={s.files} />
            </p>
          </div>
          <div>
            <p className="label">Size</p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">{fmtBytes(s.bytes)}</p>
          </div>
          <div className="flex items-center gap-3">
            <Ring value={frac} />
            <div>
              <p className="label">Hashed</p>
              <p className="mt-1.5 text-2xl font-semibold tracking-tight">
                <CountUp value={frac * 100} format={(n) => `${Math.floor(n)}%`} />
              </p>
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap justify-end gap-1.5">
            {s.kinds.map((k) => (
              <span key={k.kind} className="chip border-line font-mono text-muted" title={fmtBytes(k.bytes)}>
                {k.kind} <span className="text-ink/80">{fmtInt(k.files)}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {TILES.map((t) => {
            const x = s.states[t.key];
            return (
              <Tile
                key={t.key}
                group="state"
                label={t.label}
                value={x.files}
                sub={fmtBytes(x.bytes)}
                tone={t.tone}
                active={filter.state === t.key}
                onClick={() => pickState(t.key)}
              />
            );
          })}
        </div>
      </section>

      {/* Copies */}
      {copies && roleKey(drive.role) !== 'card' && (
        <section className="card p-5">
          <h2 className="text-lg font-semibold">Copies on other drives</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <Tile group="copies" label="On other drives" value={copies.elsewhere.files} sub={fmtBytes(copies.elsewhere.bytes)} tone="text-go" onClick={() => pickCopies('elsewhere')} active={curCopies === 'elsewhere'} />
            <Tile group="copies" label="Only on this drive" value={copies.only_here.files} sub={fmtBytes(copies.only_here.bytes)} tone="text-maybe" onClick={() => pickCopies('only_here')} active={curCopies === 'only_here'} />
            <Tile
              group="copies"
              label="Duplicated on this drive"
              value={copies.dup_here.assets}
              sub={`${fmtBytes(copies.dup_here.extra_bytes)} in extra copies`}
              tone="text-accent"
              onClick={() => pickCopies('dup_here')}
              active={curCopies === 'dup_here'}
            />
          </div>
          {copies.by_volume.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1.5">
              {copies.by_volume.map((v, i) => (
                <motion.li key={v.volume_id} {...rise(i)}>
                  <SelectTile
                    group="copies"
                    row
                    active={curCopies === `on:${v.volume_id}`}
                    onClick={() => pickCopies(`on:${v.volume_id}`)}
                    title={curCopies === `on:${v.volume_id}` ? 'Showing these below · click to show all files' : `Show the files that also have a copy on ${v.label}`}
                    className="flex items-center gap-3"
                  >
                    <RolePill role={v.role} />
                    <span className="w-28 shrink-0 truncate text-sm font-medium">{v.label}</span>
                    <span className={`shrink-0 text-xs ${v.mounted ? 'text-go' : 'text-faint'}`}>{v.mounted ? 'Connected' : 'Not connected'}</span>
                    <ProgressBar value={v.files / maxOther} size="xs" tone="muted" className="flex-1" label={`Share on ${v.label}`} />
                    <span className="shrink-0 font-mono text-2xs text-muted">
                      {plural(v.files, 'file')} · {fmtBytes(v.bytes)}
                    </span>
                    <Icon name="chevron" size={11} className="shrink-0 text-faint transition-transform duration-200 group-hover/tile:translate-x-0.5" />
                  </SelectTile>
                </motion.li>
              ))}
            </ul>
          )}
          {s.states.indexed.files > 0 && (
            <p className="mt-3 text-xs text-faint">
              {fmtInt(s.states.indexed.files)} files not hashed yet, so not compared. Other drives count only once they are hashed too.
            </p>
          )}
        </section>
      )}

      {/* Files list */}
      <section ref={filesRef} className="card flex flex-col gap-3 p-5">
        <h2 className="text-lg font-semibold">Files</h2>
        <FilterBar
          show={{
            value: filter.state,
            options: [{ value: '', text: 'All files' }, ...TILES.map((t) => ({ value: t.key, text: t.label }))],
            onChange: (state) => patchFilter({ state }),
          }}
          copies={{
            value: filter.other ? `on:${filter.other}` : filter.copies,
            options: copyOptions('this drive', s.copies?.by_volume ?? []),
            onChange: (v) => patchFilter(v.startsWith('on:') ? { copies: '', other: v.slice(3) } : { copies: v, other: '' }),
          }}
          camera={{ value: filter.camera, options: cameraOptions(s.cameras ?? []), onChange: (camera) => patchFilter({ camera }) }}
          shown={files?.total ?? 0}
          total={s.files}
        />
        {filesError && <p className="text-sm text-err">Could not list files: {filesError}</p>}
        <FileTable rows={files?.files ?? []} columns={columns} volumeId={id} mounted={drive.mounted} loading={filesLoading} summarize={summarizeFiles} />
        <Pager
          offset={filter.offset}
          count={files?.files.length ?? 0}
          total={files?.total ?? 0}
          page={FILE_PAGE}
          onPage={(offset) => setFilter((f) => ({ ...f, offset }))}
        />
      </section>
        </>
      )}

      {/* Runs */}
      {s.runs.length > 0 && (
        <section className="card p-5">
          <h2 className="text-lg font-semibold">Runs</h2>
          <ul className="mt-3 flex flex-col gap-1.5">
            {s.runs.slice(0, 4).map((r) => (
              <RunRow key={r.job_id} r={r} />
            ))}
          </ul>
          {s.runs.length > 4 && (
            <Disclosure summary={`Older runs (${s.runs.length - 4})`} open={runsOpen} onToggle={setRunsOpen} className="mt-2">
              <ul className="flex flex-col gap-1.5">
                {s.runs.slice(4).map((r) => (
                  <RunRow key={r.job_id} r={r} />
                ))}
              </ul>
            </Disclosure>
          )}
        </section>
      )}
    </motion.div>
  );
}

function RunRow({ r }: { r: DriveSummary['runs'][number] }) {
  const tone = r.state === 'done' ? 'bg-go' : r.state === 'running' ? 'bg-accent live-dot' : r.state === 'failed' ? 'bg-err' : 'bg-maybe';
  return (
    <li className="flex gap-3 rounded-xl border border-line bg-raised/30 px-3 py-2.5">
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone}`} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-sm">
          <span className="font-semibold">{r.label}</span> <span className="text-xs text-muted">{runWhen(r)}</span>
        </p>
        <p className="text-xs text-muted">{runDetail(r)}</p>
      </div>
    </li>
  );
}
