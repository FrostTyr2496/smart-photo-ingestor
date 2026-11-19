import { useEffect, useRef, useState } from 'react';
import { Modal } from '../components/Modal';
import { api } from '../lib/api';
import { fmtBytes, plural } from '../lib/format';
import { copyable, sentence, type GreenPlan } from '../lib/makeGreen';
import { useStore } from '../lib/store';
import type { DialogStatus } from '../lib/notify';

/** Make a card's one-copy (yellow) files green (bk-7, D-108): shows the plan (a read-only dry run), Apply copies. */
export function MakeGreenDialog({ card, minimized, onClose, onMinimize, onStatus }: { card: { volume_id: string | null; label: string } | null; minimized?: boolean; onClose: () => void; onMinimize?: () => void; onStatus?: (s: DialogStatus) => void }) {
  const { startTask } = useStore();
  const [plan, setPlan] = useState<GreenPlan | null>(null);
  const [error, setError] = useState('');
  const run = useRef(0);

  useEffect(() => {
    if (!card) return undefined;
    const mine = ++run.current;
    setPlan(null);
    setError('');
    api<GreenPlan>('GET', `/api/cards/${encodeURIComponent(card.volume_id ?? '')}/make-green`)
      .then((p) => run.current === mine && setPlan(p))
      .catch((err: Error) => run.current === mine && setError(err.message));
    return () => {
      run.current += 1;
    };
  }, [card]);

  const status = useRef(onStatus);
  status.current = onStatus;
  useEffect(() => {
    status.current?.({ line: error ? 'Could not plan' : !plan ? 'Planning…' : plan.possible ? `Plan ready: ${plural(copyable(plan).files, 'file')} to copy` : 'Nothing to copy', value: plan ? 1 : null });
  }, [plan, error]);

  const todo = plan ? copyable(plan) : { files: 0, bytes: 0 };
  const go = async () => {
    if (!card || !plan || !plan.possible) return;
    onClose();
    await startTask('backup', { card: card.volume_id ?? '', make_green: true });
  };

  return (
    <Modal
      wide
      open={Boolean(card) && !minimized}
      title="Make green"
      onClose={onClose}
      onMinimize={onMinimize}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={!plan?.possible} onClick={go}>
            Copy {plan?.possible ? plural(todo.files, 'file') : 'files'}
          </button>
        </>
      }
    >
      <p className="text-sm text-muted">Card: {card?.label}</p>
      {!plan && !error && <div className="card h-24 animate-pulse" />}
      {error && <p className="text-sm text-err">{error}</p>}
      {plan && (
        <div className="flex flex-col gap-3 text-sm">
          <div className="rounded-xl border border-line bg-raised/40 p-3">
            <p className="font-medium">
              {plan.possible
                ? `${plural(todo.files, 'file')} (${fmtBytes(todo.bytes)}) will be copied so they are on two drives.`
                : plan.reason
                  ? sentence(plan.reason)
                  : 'Nothing to copy.'}
            </p>
            <p className="mt-1 text-xs text-muted">
              {plural(plan.files, 'file')} on this card ({fmtBytes(plan.bytes)}) have one copy only. Each copy is verified; the source drive is only read.
            </p>
          </div>
          <ul className="flex flex-col gap-2">
            {plan.sources.map((s, i) => (
              <li key={`${s.source.label}-${i}`} className="rounded-xl border border-line p-3">
                <p className="font-medium">
                  {s.source.label}
                  {s.ok && s.target ? ` → ${s.target.label}` : ''}
                  <span className="ml-2 font-mono text-xs font-normal text-muted">
                    {plural(s.files, 'file')} · {fmtBytes(s.bytes)}
                  </span>
                </p>
                {s.source.role === 'LEGACY' && <p className="text-xs text-muted">Read only: nothing on {s.source.label} is changed.</p>}
                {s.ok && s.target ? (
                  <ul className="mt-1 text-xs text-muted">
                    {s.target.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-maybe">{sentence(s.reason ?? 'Cannot be copied now')}</p>
                )}
              </li>
            ))}
          </ul>
          {plan.sources.length > 1 && todo.files > 0 && <p className="text-xs text-faint">Sources are copied one after another, one writer per drive.</p>}
        </div>
      )}
    </Modal>
  );
}
