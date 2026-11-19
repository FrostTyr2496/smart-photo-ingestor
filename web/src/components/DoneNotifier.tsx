import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useDialogs } from '../lib/dialogs';
import { taskTitle } from '../lib/labels';
import { LONG_MS, NOTIFY_ASKED_PREF, NOTIFY_PREF, dialogForTask, notificationText, popupTtl, shouldNotify, type Outcome } from '../lib/notify';
import { springs } from '../lib/motion';
import { readPref, writePref } from '../lib/prefs';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import { Icon } from './bits';

interface Popup {
  id: number;
  title: string;
  body: string;
  outcome: Outcome;
  dialogKey: string | null;
}

/** Ask for the browser's notification permission once, the first time a long run is seen (never on page load). */
function askPermissionOnce(): void {
  if (typeof Notification === 'undefined' || !readPref(NOTIFY_PREF, true) || readPref(NOTIFY_ASKED_PREF, false)) return;
  writePref(NOTIFY_ASKED_PREF, true);
  if (Notification.permission === 'default') void Notification.requestPermission();
}

/**
 * Popups when a long task finishes (rv-32, D-115): in the app always, plus a macOS notification when the tab is in the
 * background. Decisions are in lib/notify.ts; this component only watches the store and draws.
 */
export function DoneNotifier() {
  const { tasks, drives, now, endedElsewhere } = useStore();
  const { entries, restoreDialog } = useDialogs();
  const [popups, setPopups] = useState<Popup[]>([]);
  const seenRunning = useRef(new Set<number>());
  const done = useRef(new Set<number>());
  const entriesRef = useRef(entries);
  entriesRef.current = entries;
  const drivesRef = useRef(drives);
  drivesRef.current = drives;

  const dismiss = useCallback((id: number) => setPopups((l) => l.filter((p) => p.id !== id)), []);
  const open = useCallback(
    (p: Popup) => {
      if (p.dialogKey && entriesRef.current.some((e) => e.key === p.dialogKey)) restoreDialog(p.dialogKey);
      else window.location.hash = hrefFor({ view: 'home' });
      dismiss(p.id);
    },
    [dismiss, restoreDialog],
  );

  const show = useCallback(
    (title: string, body: string, outcome: Outcome, dialogKey: string | null) => {
      const p: Popup = { id: Date.now() + Math.random(), title, body, outcome, dialogKey };
      setPopups((l) => [...l.slice(-3), p]);
      const ttl = popupTtl(outcome);
      if (ttl !== null) window.setTimeout(() => dismiss(p.id), ttl);
      if (document.hidden && readPref(NOTIFY_PREF, true) && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        const n = new Notification(title, { body });
        n.onclick = () => {
          window.focus();
          open(p);
          n.close();
        };
      }
    },
    [dismiss, open],
  );

  useEffect(() => {
    const at = Date.now();
    for (const t of tasks) {
      if (!t.final) {
        seenRunning.current.add(t.id);
        if (t.startMs !== null && at - t.startMs >= LONG_MS) askPermissionOnce();
        continue;
      }
      if (!seenRunning.current.has(t.id) || done.current.has(t.id)) continue; // finished before this page saw it
      done.current.add(t.id);
      const dlg = dialogForTask(t, entriesRef.current);
      if (!shouldNotify(t, at, { dialogVisibleFor: Boolean(dlg && !dlg.minimized) })) continue;
      const text = notificationText(t, drivesRef.current);
      show(text.title, text.body, text.outcome, dlg ? dlg.key : null);
    }
  }, [tasks, now, show]);

  // A run started in a terminal that ended (the store sees it leave the activity list).
  const lastEnded = useRef(endedElsewhere.n);
  useEffect(() => {
    if (endedElsewhere.n === lastEnded.current) return;
    lastEnded.current = endedElsewhere.n;
    for (const r of endedElsewhere.runs) {
      const started = Date.parse(r.started_at);
      if (!started || Date.now() - started < LONG_MS) continue;
      show(`Done: ${taskTitle(['--json', ...(r.words ?? [])], drivesRef.current)}`, 'Started in a terminal. Its result is under Recently run on Tasks.', 'done', null);
    }
  }, [endedElsewhere, show]);

  return (
    <div className="pointer-events-none fixed right-4 top-16 z-[310] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2" aria-live="polite">
      <AnimatePresence initial={false}>
        {popups.map((p) => (
          <motion.div
            key={p.id}
            layout
            initial={{ opacity: 0, y: -12, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, transition: { duration: 0.18 } }}
            transition={springs.bouncy}
            className={`pointer-events-auto flex flex-col gap-2 rounded-xl border bg-raised/95 px-3.5 py-3 text-sm shadow-2xl shadow-black/40 backdrop-blur-xl ${p.outcome === 'failed' ? 'border-err/40' : 'border-line-strong'}`}
          >
            <div className="flex items-start gap-2.5">
              <span className={`mt-0.5 ${p.outcome === 'failed' ? 'text-err' : p.outcome === 'paused' ? 'text-maybe' : 'text-go'}`}>
                <Icon name={p.outcome === 'done' ? 'check' : 'warn'} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{p.title}</p>
                {p.body && <p className="mt-0.5 line-clamp-3 text-xs text-muted">{p.body}</p>}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost h-7 text-xs" onClick={() => dismiss(p.id)}>
                Dismiss
              </button>
              <button type="button" className="btn h-7 text-xs" onClick={() => open(p)}>
                Open
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
