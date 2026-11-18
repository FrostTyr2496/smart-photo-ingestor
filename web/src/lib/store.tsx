import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { driveName, workflowSteps, liveStep, STEP_NAME, type WorkflowStep } from './labels';
import type { Drive, ExtRun, StaleJob, TaskDetail, TaskRec, TaskSummary, CardInfo } from './types';

// Same cadence as the classic UI.
const TASK_FAST_MS = 1000; // poll running tasks this often
const TASK_IDLE_MS = 5000; // and this often when nothing runs
const TASK_LIST_MS = 5000; // re-read the full task list
const ACTIVITY_MS = 3000; // re-read runs started outside the UI (ui-11)
const DRIVE_DEBOUNCE_MS = 300;
const DRIVE_LATE_MS = 2000;

export interface Toast {
  id: number;
  text: string;
  kind: 'ok' | 'err';
}

export interface ConfirmAsk {
  title: string;
  lines: string[];
  action: string;
  resolve: (ok: boolean) => void;
}

export interface Workflow {
  volumeId: string;
  steps: WorkflowStep[];
  at: number;
  outcome: string | null;
}

interface Store {
  loaded: boolean;
  drives: Drive[];
  tasks: TaskRec[]; // newest first
  elsewhere: ExtRun[];
  stale: StaleJob[];
  cards: CardInfo[];
  pausing: Set<number>;
  workflow: Workflow | null;
  /** Bumps whenever a run ends, so open views reload their numbers. */
  epoch: number;
  now: number;
  toasts: Toast[];
  confirm: ConfirmAsk | null;
  startTask: (cmd: string, args: Record<string, unknown>) => Promise<number | null>;
  pauseTask: (id: number) => Promise<void>;
  runWorkflow: (d: Drive) => Promise<void>;
  runStep: (d: Drive, step: WorkflowStep) => Promise<void>;
  busy: (d: Drive) => boolean;
  toast: (text: string, kind?: 'ok' | 'err') => void;
  dismissToast: (id: number) => void;
  ask: (q: Omit<ConfirmAsk, 'resolve'>) => Promise<boolean>;
  setIgnored: (d: Drive, ignored: boolean) => Promise<void>;
}

const Ctx = createContext<Store | null>(null);
export const useStore = (): Store => {
  const v = useContext(Ctx);
  if (!v) throw new Error('useStore outside StoreProvider');
  return v;
};

const newTask = (id: number): TaskRec => ({
  id,
  argv: [],
  state: 'running',
  exitCode: null,
  lastSeq: -1,
  keepAwake: false,
  progress: null,
  final: false,
  startMs: null,
  note: '',
  error: '',
  result: null,
});

/** Copy what a poll reported into the task record (a fresh object, so React sees the change). */
export function absorb(t: TaskRec, d: TaskDetail): TaskRec {
  const n: TaskRec = { ...t, argv: d.argv, state: d.state, exitCode: d.exit_code, lastSeq: d.last_seq, keepAwake: Boolean(d.keep_awake) };
  if (d.latest_progress) n.progress = d.latest_progress;
  for (const e of d.events) {
    if (n.startMs === null && e.ts) n.startMs = Date.parse(e.ts) || null;
    if (e.event === 'result') n.result = e as Record<string, unknown>;
    else if (e.event === 'warning') n.note = e.message || e.code || '';
    else if (e.event === 'error') n.error = e.message || e.code || '';
    else if (e.event === 'progress' && e.message) n.note = e.message;
  }
  if (n.startMs === null) n.startMs = Date.now();
  return n;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState(false);
  const [drives, setDrives] = useState<Drive[]>([]);
  const [taskMap, setTaskMap] = useState<Map<number, TaskRec>>(new Map());
  const [elsewhere, setElsewhere] = useState<ExtRun[]>([]);
  const [stale, setStale] = useState<StaleJob[]>([]);
  const [cards, setCards] = useState<CardInfo[]>([]);
  const [pausing, setPausing] = useState<Set<number>>(new Set());
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirm, setConfirm] = useState<ConfirmAsk | null>(null);

  // The poll loop reads the latest values through refs, so it never restarts.
  const tasksRef = useRef(taskMap);
  tasksRef.current = taskMap;
  const elsewhereRef = useRef(elsewhere);
  elsewhereRef.current = elsewhere;
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;
  const kickRef = useRef<() => void>(() => {});

  const toast = useCallback((text: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts((list) => [...list.slice(-3), { id, text, kind }]);
    window.setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), kind === 'err' ? 9000 : 5000);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((list) => list.filter((x) => x.id !== id)), []);

  const ask = useCallback(
    (q: Omit<ConfirmAsk, 'resolve'>) =>
      new Promise<boolean>((resolve) => {
        setConfirm({
          ...q,
          resolve: (ok) => {
            setConfirm(null);
            resolve(ok);
          },
        });
      }),
    [],
  );

  const loadDrives = useCallback(async () => {
    const data = await api<{ drives: Drive[] }>('GET', '/api/drives');
    setDrives(data.drives);
    setLoaded(true);
    api<{ cards: CardInfo[] }>('GET', '/api/cards')
      .then((c) => setCards(c.cards))
      .catch(() => {});
  }, []);

  const loadActivity = useCallback(async () => {
    const d = await api<{ activity: ExtRun[]; stale_jobs: StaleJob[] }>('GET', '/api/activity');
    const next = d.activity || [];
    const gone = elsewhereRef.current.filter((e) => !next.some((x) => x.pid === e.pid));
    elsewhereRef.current = next;
    setElsewhere(next);
    setStale(d.stale_jobs || []);
    if (gone.length) {
      toast(`${gone.length === 1 ? 'A run' : `${gone.length} runs`} started in a terminal has ended. See its terminal for the result.`);
      setEpoch((n) => n + 1);
      loadDrives().catch(() => {});
    }
  }, [loadDrives, toast]);

  // ---------------------------------------------------------------- the poll loop
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let ticking = false;
    let lastList = 0;
    let lastActivity = 0;

    const tick = async () => {
      if (ticking || stopped) return;
      ticking = true;
      window.clearTimeout(timer);
      try {
        const map = new Map(tasksRef.current);
        let changed = false;
        if (Date.now() - lastList >= TASK_LIST_MS) {
          lastList = Date.now();
          try {
            const { tasks } = await api<{ tasks: TaskSummary[] }>('GET', '/api/tasks');
            for (const s of tasks) {
              if (!map.has(s.task_id)) {
                map.set(s.task_id, { ...newTask(s.task_id), argv: s.argv, state: s.state, exitCode: s.exit_code, final: s.state !== 'running' });
                changed = true;
              }
            }
          } catch {
            /* next tick */
          }
        }
        const finished: TaskRec[] = [];
        for (const t of [...map.values()].filter((x) => !x.final)) {
          try {
            const d = await api<TaskDetail>('GET', `/api/tasks/${t.id}?since=${t.lastSeq}`);
            const n = absorb(t, d);
            if (d.state !== 'running') {
              n.final = true;
              finished.push(n);
            }
            map.set(t.id, n);
            changed = true;
          } catch {
            /* keep the task; the next tick tries again */
          }
        }
        // A task that finished before the page loaded still needs its result, once.
        for (const t of [...map.values()].filter((x) => x.final && x.lastSeq < 0)) {
          try {
            const d = await api<TaskDetail>('GET', `/api/tasks/${t.id}?since=-1`);
            map.set(t.id, { ...absorb(t, d), final: true });
          } catch {
            map.set(t.id, { ...t, lastSeq: 0 }); // do not ask again
          }
          changed = true;
        }
        if (changed) {
          tasksRef.current = map;
          setTaskMap(map);
        }
        if (finished.length) {
          setPausing((p) => {
            const n = new Set(p);
            finished.forEach((t) => n.delete(t.id));
            return n;
          });
          setEpoch((n) => n + 1);
          loadDrives().catch(() => {});
        }
        if (Date.now() - lastActivity >= ACTIVITY_MS) {
          lastActivity = Date.now();
          await loadActivity().catch(() => {});
        }
        setNow(Date.now());
      } finally {
        ticking = false;
        if (!stopped) {
          const anyRunning = [...tasksRef.current.values()].some((t) => !t.final) || elsewhereRef.current.length > 0;
          timer = window.setTimeout(tick, anyRunning ? TASK_FAST_MS : TASK_IDLE_MS);
        }
      }
    };
    kickRef.current = () => {
      lastList = 0;
      void tick();
    };
    void tick();
    loadDrives().catch(() => setLoaded(true));

    // The server sends `event: drives` when a drive is plugged in or leaves.
    let driveTimer = 0;
    let lateTimer = 0;
    const source = typeof EventSource !== 'undefined' ? new EventSource('/api/events') : null;
    source?.addEventListener('drives', () => {
      window.clearTimeout(driveTimer);
      driveTimer = window.setTimeout(() => {
        loadDrives().catch(() => {});
        window.clearTimeout(lateTimer);
        lateTimer = window.setTimeout(() => loadDrives().catch(() => {}), DRIVE_LATE_MS);
      }, DRIVE_DEBOUNCE_MS);
    });
    const onVisible = () => {
      if (!document.hidden) loadDrives().catch(() => {});
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(driveTimer);
      window.clearTimeout(lateTimer);
      source?.close();
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [loadDrives, loadActivity]);

  // ---------------------------------------------------------------- actions
  const startTask = useCallback(
    async (cmd: string, args: Record<string, unknown>) => {
      try {
        const { task_id } = await api<{ task_id: number }>('POST', '/api/run', { cmd, args });
        const map = new Map(tasksRef.current).set(task_id, { ...newTask(task_id), startMs: Date.now() });
        tasksRef.current = map;
        setTaskMap(map);
        kickRef.current();
        return task_id;
      } catch (err) {
        toast((err as Error).message, 'err');
        return null;
      }
    },
    [toast],
  );

  const pauseTask = useCallback(
    async (id: number) => {
      setPausing((p) => new Set(p).add(id));
      try {
        await api('POST', `/api/tasks/${id}/pause`, {});
        toast('Pausing. The task stops after the current file.');
      } catch (err) {
        setPausing((p) => {
          const n = new Set(p);
          n.delete(id);
          return n;
        });
        toast((err as Error).message, 'err');
      }
    },
    [toast],
  );

  const waitForTask = useCallback(
    (id: number) =>
      new Promise<TaskRec>((resolve) => {
        const timer = window.setInterval(() => {
          const t = tasksRef.current.get(id);
          if (t && t.final) {
            window.clearInterval(timer);
            resolve(t);
          }
        }, 500);
      }),
    [],
  );

  const tasks = useMemo(() => [...taskMap.values()].sort((a, b) => b.id - a.id), [taskMap]);

  /** A run started in a terminal on this drive (ui-11) counts too, so the UI never starts a second one beside it. */
  const busy = useCallback(
    (d: Drive) =>
      Boolean(workflowRef.current) ||
      Boolean(liveStep(d, [...tasksRef.current.values()], [])) ||
      elsewhereRef.current.some((e) => (e.words || []).includes(d.mount)),
    [],
  );

  const runWorkflow = useCallback(
    async (d: Drive) => {
      if (busy(d)) return;
      const steps = workflowSteps(d);
      if (!steps.length) return;
      if (steps.some((s) => s.id === 'sidecars')) {
        const ok = await ask({
          title: 'Run the workflow?',
          lines: [
            `On "${driveName(d)}": index, hash and scan metadata, then write XMP sidecar files next to photos with saved edits.`,
            'Nothing is deleted or overwritten.',
          ],
          action: 'Run workflow',
        });
        if (!ok) return;
      }
      const wf: Workflow = { volumeId: d.volume_id!, steps, at: 0, outcome: null };
      workflowRef.current = wf;
      setWorkflow(wf);
      try {
        for (let i = 0; i < steps.length; i++) {
          const step = steps[i]!;
          wf.at = i;
          setWorkflow({ ...wf });
          const id = await startTask(step.cmd, step.args);
          if (!id) {
            wf.outcome = 'failed';
            break;
          }
          const t = await waitForTask(id);
          if (t.state !== 'done') {
            wf.outcome = t.state;
            toast(`Workflow stopped at ${STEP_NAME[step.id]}. ${t.state === 'paused' ? 'It was paused.' : 'It did not finish.'}`, 'err');
            break;
          }
        }
        if (!wf.outcome) toast(`Workflow finished on ${driveName(d)}.`);
      } finally {
        workflowRef.current = null;
        setWorkflow(null);
        setEpoch((n) => n + 1);
      }
    },
    [ask, busy, startTask, toast, waitForTask],
  );

  const runStep = useCallback(
    async (d: Drive, step: WorkflowStep) => {
      if (busy(d)) return;
      if (step.id === 'sidecars') {
        const ok = await ask({
          title: 'Write sidecar files?',
          lines: [`On "${driveName(d)}": write XMP sidecar files next to photos with saved edits.`, 'Nothing is deleted or overwritten.'],
          action: 'Write sidecars',
        });
        if (!ok) return;
      }
      await startTask(step.cmd, step.args);
    },
    [ask, busy, startTask],
  );

  const setIgnored = useCallback(
    async (d: Drive, ignored: boolean) => {
      try {
        await api('POST', '/api/drives/ignore', { key: d.key, ignored });
        setDrives((list) => list.map((x) => (x.key === d.key ? { ...x, ignored } : x)));
        toast(ignored ? `${driveName(d)} is ignored. Find it under Ignored.` : `${driveName(d)} is back in the list.`);
      } catch (err) {
        toast((err as Error).message, 'err');
      }
    },
    [toast],
  );

  const value: Store = {
    loaded,
    drives,
    tasks,
    elsewhere,
    stale,
    cards,
    pausing,
    workflow,
    epoch,
    now,
    toasts,
    confirm,
    startTask,
    pauseTask,
    runWorkflow,
    runStep,
    busy,
    toast,
    dismissToast,
    ask,
    setIgnored,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
