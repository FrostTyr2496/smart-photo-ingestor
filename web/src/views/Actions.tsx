import { AnimatePresence, motion } from 'framer-motion';
import { useMemo, useState } from 'react';
import { Disclosure, Icon } from '../components/bits';
import { groupActions, highlightText, type ActionItem, type ActionNeed } from '../lib/actions';
import { api } from '../lib/api';
import { rise } from '../lib/motion';
import { useStore } from '../lib/store';
import { refreshActions, sendOp, snoozeOp, useActions } from '../lib/useActions';

const SEV: Record<string, { dot: string; word: string }> = {
  critical: { dot: 'bg-err', word: 'Critical' },
  high: { dot: 'bg-maybe', word: 'High' },
  normal: { dot: 'bg-skip', word: 'Normal' },
  low: { dot: 'border border-faint', word: 'Low' },
};

function NeedChip({ n, mounted }: { n: ActionNeed; mounted: boolean }) {
  return (
    <span className="chip border-line-strong bg-raised/60 text-ink/90" title={mounted ? 'Connected' : 'Not connected'}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${mounted ? 'bg-go' : 'border border-faint'}`} />
      {n.label}
      <span className="sr-only">{mounted ? ' (connected)' : ' (not connected)'}</span>
    </span>
  );
}

function CopyCli({ cli }: { cli: string }) {
  const { toast } = useStore();
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-bg/50 py-1 pl-2.5 pr-1">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-2xs text-muted no-scrollbar">{cli}</code>
      <button
        type="button"
        className="btn-ghost h-6 w-6 shrink-0 justify-center px-0"
        aria-label="Copy the command"
        title="Copy the command"
        onClick={() => {
          navigator.clipboard?.writeText(cli).then(
            () => toast('Command copied.'),
            () => toast('Could not copy. Select the command and copy it.', 'err'),
          );
        }}
      >
        <Icon name="copy" size={12} />
      </button>
    </div>
  );
}

function ActionCard({ a, mountedIds, restore }: { a: ActionItem; mountedIds: ReadonlySet<string>; restore?: boolean }) {
  const { toast, ask, now } = useStore();
  const [busy, setBusy] = useState(false);
  const hi = restore ? null : highlightText(a, mountedIds);
  const sev = SEV[a.severity] ?? SEV.low!;

  const run = async () => {
    if (a.do.kind !== 'run') return;
    const d = a.do;
    const ok = await ask({
      title: `${d.label}?`,
      lines: [`This will start: ${a.title}`, `It sends POST ${d.endpoint}${d.body !== undefined ? ` with ${JSON.stringify(d.body)}` : ''}.`, ...(a.cli ? [`Same as: ${a.cli}`] : [])],
      action: d.label,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api('POST', d.endpoint, d.body);
      toast(`Started: ${d.label}. Progress is on the Tasks page.`);
      void refreshActions();
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setBusy(false);
    }
  };

  const op = async (kind: 'dismiss' | 'snooze') => {
    const id = a.id;
    try {
      await sendOp(kind === 'dismiss' ? { type: 'dismiss', id } : snoozeOp(id, now || Date.now()));
      toast(kind === 'dismiss' ? 'Dismissed.' : 'Snoozed for 7 days.', 'ok', {
        label: 'Undo',
        run: () => void sendOp({ type: 'restore', id }).catch((e: Error) => toast(e.message, 'err')),
      });
    } catch (err) {
      toast((err as Error).message, 'err');
    }
  };

  return (
    <article className={`card p-4 ${hi ? 'border-go/50 ring-1 ring-go/30' : ''}`}>
      {hi && (
        <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-go">
          <Icon name="check" size={12} />
          {hi}
        </p>
      )}
      <div className="flex items-start gap-2.5">
        <span className={`mt-[7px] h-2.5 w-2.5 shrink-0 rounded-full ${sev.dot}`} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold leading-snug">
            <span className="sr-only">{sev.word}: </span>
            {a.title}
          </h3>
          {a.why.length > 0 && (
            <ul className="mt-1 flex flex-col gap-0.5 text-sm text-muted">
              {a.why.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {a.needs.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {a.needs.map((n) => (
            <NeedChip key={n.volume_id} n={n} mounted={mountedIds.has(n.volume_id)} />
          ))}
        </div>
      )}
      {a.steps.length > 0 && (
        <ol className="mt-3 list-decimal space-y-0.5 pl-5 text-sm marker:text-faint">
          {a.steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
      )}
      {a.cli && (
        <div className="mt-3">
          <CopyCli cli={a.cli} />
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {restore ? (
          <button type="button" className="btn h-7 text-xs" onClick={() => void sendOp({ type: 'restore', id: a.id }).catch((e: Error) => toast(e.message, 'err'))}>
            Restore
          </button>
        ) : (
          <>
            {a.do.kind === 'ui' && (
              <a href={a.do.href} className="btn-primary h-7 text-xs">
                {a.do.label}
              </a>
            )}
            {a.do.kind === 'run' && (
              <button type="button" className="btn-primary h-7 text-xs" disabled={busy} onClick={() => void run()}>
                {a.do.label}
              </button>
            )}
            <span className="flex-1" />
            <button type="button" className="btn-ghost h-7 text-xs" onClick={() => void op('snooze')}>
              Snooze 7 days
            </button>
            <button type="button" className="btn-ghost h-7 text-xs" onClick={() => void op('dismiss')}>
              Dismiss
            </button>
          </>
        )}
      </div>
    </article>
  );
}

function Group({ title, list, mountedIds }: { title: string; list: ActionItem[]; mountedIds: ReadonlySet<string> }) {
  if (!list.length) return null;
  return (
    <section>
      <h2 className="label mb-2">
        {title} ({list.length})
      </h2>
      <div className="flex flex-col gap-3">
        <AnimatePresence initial={false}>
          {list.map((a, i) => (
            <motion.div key={a.id} layout="position" {...rise(i)} exit={{ opacity: 0, transition: { duration: 0.12 } }}>
              <ActionCard a={a} mountedIds={mountedIds} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </section>
  );
}

export function Actions() {
  const { drives, now } = useStore();
  const { actions, loaded, error } = useActions();
  const [hiddenOpen, setHiddenOpen] = useState(false);
  const mountedIds = useMemo(() => new Set(drives.filter((d) => d.mounted && d.volume_id).map((d) => d.volume_id as string)), [drives]);
  const g = useMemo(() => groupActions(actions, now || Date.now(), mountedIds), [actions, now, mountedIds]);
  const empty = !g.risk.length && !g.unfinished.length && !g.housekeeping.length;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">What to do next</h1>
        <p className="mt-1 text-sm text-muted">Ranked by risk to your photos. Nothing runs until you press a button.</p>
      </div>
      {error && <p className="card p-3 text-sm text-err">Could not load actions: {error}</p>}
      {loaded && !error && empty && (
        <div className="card flex flex-col items-center gap-2 px-6 py-10 text-center">
          <p className="font-medium">Nothing needs doing.</p>
          <p className="max-w-md text-sm text-muted">Your photos are backed up as far as the catalog knows.</p>
        </div>
      )}
      {!loaded && <p className="text-sm text-muted">Loading…</p>}
      <Group title="Photos at risk" list={g.risk} mountedIds={mountedIds} />
      <Group title="Unfinished" list={g.unfinished} mountedIds={mountedIds} />
      <Group title="Housekeeping" list={g.housekeeping} mountedIds={mountedIds} />
      {g.hidden.length > 0 && (
        <Disclosure summary={`Snoozed & dismissed (${g.hidden.length})`} open={hiddenOpen} onToggle={setHiddenOpen}>
          <div className="flex flex-col gap-3">
            {g.hidden.map((a) => (
              <ActionCard key={a.id} a={a} mountedIds={mountedIds} restore />
            ))}
          </div>
        </Disclosure>
      )}
    </div>
  );
}
