// Shapes of the JSON API (photovault/web/server.py). Only the fields the UI reads.

export interface StepInfo {
  at: string;
  state: string;
  pending?: number;
}

export interface Drive {
  mount: string;
  role: string | null;
  label: string | null;
  fs: string | null;
  size: string | null;
  free: string | null;
  volume_id: string | null;
  mounted: boolean;
  looks_like_card: boolean;
  last_seen_at: string | null;
  local: boolean;
  key: string;
  ignored: boolean;
  meta_scanned?: boolean;
  steps?: Record<string, StepInfo | null>;
}

export interface Progress {
  current?: number;
  total?: number;
  message?: string;
  phase?: string;
}

export interface TaskEvent {
  event: string;
  ts?: string;
  message?: string;
  code?: string;
  [key: string]: unknown;
}

/** One file a run handled: an `item` event (rv-34). `dst` is absent for a card clear. */
export interface FileEvt {
  state: string;
  src: string;
  dst?: string | null;
  error?: string;
}

export interface TaskSummary {
  task_id: number;
  state: string;
  exit_code: number | null;
  argv: string[];
  last_seq: number;
  keep_awake?: boolean;
  /** The first job id a result reported (rv-31); kept by the server after that event leaves its window. */
  job_id?: number | null;
  /** A make-green run's newest "copying N file(s) from A to B" (rv-36). */
  route?: string | null;
}

export interface TaskDetail extends TaskSummary {
  events: TaskEvent[];
  latest_progress?: Progress | null;
}

/** A task the UI started (or found in /api/tasks), with what its events said so far. */
export interface TaskRec {
  id: number;
  argv: string[];
  state: string;
  exitCode: number | null;
  lastSeq: number;
  keepAwake: boolean;
  progress: Progress | null;
  final: boolean;
  startMs: number | null;
  note: string;
  error: string;
  result: Record<string, unknown> | null;
  /** The run's job id once known: from the server's summary, or a result event. */
  jobId: number | null;
  /** The last files the run handled, newest last (rv-34). */
  files?: FileEvt[];
  /** When the run last reported a file or progress (ms), for the stall check (rv-50). */
  lastActMs?: number | null;
  /** The drive pair (labels) a make-green run is copying between right now (rv-36). */
  route?: { from: string; to: string } | null;
}

export interface ExtJob {
  job_id: number;
  kind: string;
  current?: number | null;
  total?: number | null;
  unit?: string;
  created_at: string;
  last_write_at?: string | null;
}

/** A run started outside the UI (ui-11). */
export interface ExtRun {
  pid: number;
  words?: string[];
  source?: string;
  progress?: Progress | null;
  job?: ExtJob | null;
  started_at: string;
  phase_started_at?: string;
  warning?: string;
  warnings?: number;
  /** The last files the run handled, newest last, from its progress file (rv-34). */
  files?: FileEvt[];
  /** When the progress file was last written (rv-50). */
  updated_at?: string | null;
  /** The process is waiting on a disk read or write right now (`ps` state U/D, rv-50). */
  disk_wait?: boolean;
}

/** One finished engine run, UI or terminal, from `<home>/activity/history.jsonl` (rv-30). */
export interface RecentRun {
  pid: number;
  words: string[];
  started_at: string;
  finished_at: string;
  exit_code: number | null;
  state: string;
  result: Record<string, unknown> | null;
  error: string | null;
  warnings?: number;
}

export interface StaleJob {
  job_id: number;
  kind: string;
  label: string | null;
  created_at: string;
}

export interface FilesBytes {
  files: number;
  bytes: number;
}

export interface CardInfo {
  volume_id: string;
  label: string;
  mounted: boolean;
  tier: string;
  /** Files and bytes per tier (only on card, not checked, one copy, backed up), as on the card page. */
  counts?: Record<'only_on_card' | 'unchecked' | 'needs_backup' | 'backed_up', FilesBytes>;
}

export interface Run {
  job_id: number;
  kind: string;
  label: string;
  state: string;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, number> | null;
}

export interface DriveSummary {
  drive: Drive;
  states: Record<'indexed' | 'verified' | 'mismatch' | 'missing', FilesBytes & { hashed: number }>;
  files: number;
  bytes: number;
  kinds: Array<{ kind: string; files: number; bytes: number }>;
  runs: Run[];
  steps: Record<string, StepInfo | null>;
  copies?: {
    hashed: number;
    elsewhere: FilesBytes;
    only_here: FilesBytes;
    dup_here: { assets: number; files: number; extra_bytes: number };
    by_volume: Array<FilesBytes & { volume_id: string; label: string; role: string; mounted: boolean }>;
  } | null;
  cameras?: Array<{ camera: string; name: string; files: number; bytes: number }>;
  usage?: DriveUsage | null;
}

/** Photography vs everything else on a drive (idx-13); null until an index has counted it. */
export interface UsagePart {
  category: string;
  files: number;
  bytes: number;
}

export interface DriveUsage {
  photo: FilesBytes & { parts: UsagePart[] };
  non_photo: FilesBytes & { parts: UsagePart[] };
  unaccounted: number | null;
  used: number | null;
  free: number | null;
  capacity: number | null;
}
