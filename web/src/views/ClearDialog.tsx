import { useEffect, useRef, useState } from 'react';
import { Field, Modal } from '../components/Modal';
import { api } from '../lib/api';
import { fmtBytes, plural } from '../lib/format';
import { confirmMatches, readClearPlan, type ClearPlan } from '../lib/cardClear';
import { useStore } from '../lib/store';
import type { TaskDetail } from '../lib/types';

/** Clear a card (cc-1, D-096): a dry run shows what is deleted and what is kept; a typed count starts the real run. */
export function ClearDialog({ card, onClose }: { card: { mount: string; label: string } | null; onClose: () => void }) {
  const { startTask } = useStore();
  const [plan, setPlan] = useState<ClearPlan | null>(null);
  const [error, setError] = useState('');
  const [typed, setTyped] = useState('');
  const run = useRef(0);

  useEffect(() => {
    if (!card) return undefined;
    const mine = ++run.current;
    setPlan(null);
    setError('');
    setTyped('');
    void (async () => {
      const id = await startTask('card_clear', { mount: card.mount, dry_run: true });
      if (!id) return setError('The dry run could not start.');
      for (;;) {
        await new Promise((r) => window.setTimeout(r, 700));
        if (run.current !== mine) return undefined;
        try {
          const d = await api<TaskDetail>('GET', `/api/tasks/${id}?since=-1`);
          if (d.state === 'running') continue;
          const read = readClearPlan(d.events);
          if (read.plan) setPlan(read.plan);
          else setError(read.error || 'The dry run did not finish.');
          return undefined;
        } catch (err) {
          return setError((err as Error).message);
        }
      }
    })();
    return () => {
      run.current += 1;
    };
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = Boolean(plan) && confirmMatches(typed, plan!.deletable_files);
  const go = async () => {
    if (!card || !plan || !ready) return;
    onClose();
    await startTask('card_clear', { mount: card.mount, confirm: plan.deletable_files });
  };

  return (
    <Modal
      wide
      open={Boolean(card)}
      title="Clear card"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={!ready} onClick={go}>
            Delete {plan ? plural(plan.deletable_files, 'file') : 'files'}
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
              {plan.deletable_files === 0
                ? 'Nothing can be deleted from this card right now.'
                : `${plural(plan.deletable_files, 'file')} (${fmtBytes(plan.deletable_bytes)}) in ${plural(plan.groups, 'frame')} would be deleted.`}
            </p>
            {plan.copy_drives.length > 0 && (
              <p className="mt-1 text-xs text-muted">Each is checked again against its copies on {plan.copy_drives.join(' and ')} just before it is deleted.</p>
            )}
          </div>
          {plan.kept.length > 0 && (
            <div>
              <p className="label mb-1">Kept on the card ({plural(plan.kept_files, 'file')})</p>
              <ul className="flex flex-col gap-0.5 text-xs text-muted">
                {plan.kept.map((k) => (
                  <li key={k.code}>
                    {k.files.toLocaleString('en-US')} · {k.text}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-faint">
            Only files in the DCIM folder that are on two drives (or a backup drive) are deleted, a whole frame at a time. The card is not formatted. Any
            surprise stops the run.
          </p>
          {plan.deletable_files > 0 && (
            <Field label={`Type ${plan.deletable_files} to confirm`}>
              <input value={typed} onChange={(e) => setTyped(e.target.value)} inputMode="numeric" className="h-9" placeholder={String(plan.deletable_files)} />
            </Field>
          )}
        </div>
      )}
    </Modal>
  );
}
