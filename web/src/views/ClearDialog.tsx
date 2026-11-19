import { useEffect, useRef, useState } from 'react';
import { Field, Modal } from '../components/Modal';
import { api } from '../lib/api';
import { fmtBytes, plural } from '../lib/format';
import { clearProgressLine, clearProgressValue, confirmMatches, readClearPlan, verifyEstimate, type ClearPlan } from '../lib/cardClear';
import { ProgressBar } from '../components/ProgressBar';
import { useStore } from '../lib/store';
import type { DialogStatus } from '../lib/notify';
import type { TaskDetail } from '../lib/types';

/** Stop a dry run nobody is waiting for (pause = SIGINT; a dry run writes nothing). Errors are ignored: it may have ended. */
function stopDryRun(id: number): void {
  api('POST', `/api/tasks/${id}/pause`, {}).catch(() => undefined);
}

/** Clear a card (cc-1, D-096): a dry run shows what is deleted and what is kept; a typed count starts the real run. */
export function ClearDialog({ card, minimized, onClose, onMinimize, onStatus }: { card: { mount: string | null; label: string } | null; minimized?: boolean; onClose: () => void; onMinimize?: () => void; onStatus?: (s: DialogStatus) => void }) {
  const { startTask } = useStore();
  const [plan, setPlan] = useState<ClearPlan | null>(null);
  const [error, setError] = useState('');
  const [typed, setTyped] = useState('');
  const [verifyAll, setVerifyAll] = useState(true);
  const [progress, setProgress] = useState<{ line: string; value: number | null }>({ line: '', value: null });
  const run = useRef(0);

  useEffect(() => {
    if (!card) return undefined;
    const mine = ++run.current;
    let taskId: number | null = null;
    setPlan(null);
    setError('');
    setTyped('');
    setProgress({ line: '', value: null });
    void (async () => {
      const id = await startTask('card_clear', { mount: card.mount ?? '', dry_run: true, verify_all: verifyAll });
      if (!id) return setError('The dry run could not start.');
      taskId = id;
      if (run.current !== mine) return stopDryRun(id); // the option changed or the dialog closed while it started
      for (;;) {
        await new Promise((r) => window.setTimeout(r, 700));
        if (run.current !== mine) return undefined;
        try {
          const d = await api<TaskDetail>('GET', `/api/tasks/${id}?since=-1`);
          if (d.state === 'running') {
            const p = (d.latest_progress ?? null) as Parameters<typeof clearProgressLine>[0];
            setProgress({ line: clearProgressLine(p), value: clearProgressValue(p) });
            continue;
          }
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
      // A superseded dry run would keep re-reading the same drives beside the new one: stop it.
      if (taskId !== null) stopDryRun(taskId);
    };
  }, [card, verifyAll]); // eslint-disable-line react-hooks/exhaustive-deps (a changed option runs a new dry run)

  // rv-32: what the dock pill shows while the dialog is minimized.
  const status = useRef(onStatus);
  status.current = onStatus;
  useEffect(() => {
    if (error) status.current?.({ line: 'Dry run failed', value: null });
    else if (plan) status.current?.({ line: plan.deletable_files === 0 ? 'Dry run done: nothing to delete' : `Dry run done: ${plural(plan.deletable_files, 'file')} could be deleted`, value: 1 });
    else status.current?.({ line: progress.line || 'Planning the dry run…', value: progress.value });
  }, [plan, error, progress]);

  const ready = Boolean(plan) && confirmMatches(typed, plan!.deletable_files);
  const go = async () => {
    if (!card || !plan || !ready) return;
    onClose();
    await startTask('card_clear', { mount: card.mount ?? '', confirm: plan.deletable_files, verify_all: verifyAll });
  };

  return (
    <Modal
      wide
      open={Boolean(card) && !minimized}
      title="Clear card"
      onClose={onClose}
      onMinimize={onMinimize}
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
      <div className="rounded-xl border border-line p-3 text-sm">
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={verifyAll} onChange={(e) => setVerifyAll(e.target.checked)} />
          <span>
            <span className="font-medium">Re-read every copy before deleting (recommended)</span>
            <span className="mt-0.5 block text-xs text-muted">
              {plan && verifyAll
                ? `Read ${fmtBytes(plan.verify.drives.reduce((n, d) => n + d.bytes, 0))} from ${plan.verify.drives.map((d) => d.label).join(' and ') || 'the copy drives'}. `
                : plan
                  ? `Would read about ${fmtBytes(verifyEstimate(plan).bytes)} from ${plan.copy_drives.join(' and ') || 'the copy drives'}. `
                  : ''}
              Slower on hard drives; those drives must stay plugged in. Changing this runs the dry run again.
            </span>
          </span>
        </label>
        {!verifyAll && (
          <p className="mt-2 rounded-lg border border-err/40 bg-err/10 p-2 text-xs text-err">
            Bypassing: only one copy of each file is re-read; the second copy is trusted from the catalog. A copy that went bad since it was verified
            would not be noticed.
          </p>
        )}
      </div>
      {!plan && !error && (
        <div className="rounded-xl border border-line p-3 text-sm">
          <p className="label mb-1">Dry run · nothing is deleted</p>
          <p className="text-muted">{progress.line || (verifyAll ? 'Planning, then re-reading every copy…' : 'Planning…')}</p>
          <ProgressBar value={progress.value} live className="mt-2" />
          {verifyAll && <p className="mt-1 text-xs text-faint">Re-reading every copy takes a while on hard drives. You can untick the option to bypass it.</p>}
        </div>
      )}
      {error && <p className="text-sm text-err">{error}</p>}
      {plan && (
        <div className="flex flex-col gap-3 text-sm">
          <div className="rounded-xl border border-line bg-raised/40 p-3">
            <p className="label mb-1">Dry run · nothing has been deleted</p>
            <p className="font-medium">
              {plan.deletable_files === 0
                ? 'Nothing can be deleted from this card right now.'
                : `${plural(plan.deletable_files, 'file')} (${fmtBytes(plan.deletable_bytes)}) in ${plural(plan.groups, 'frame')} would be deleted.`}
            </p>
            {plan.copy_drives.length > 0 && (
              <p className="mt-1 text-xs text-muted">Each is checked again against its copies on {plan.copy_drives.join(' and ')} just before it is deleted.</p>
            )}
          </div>
          {plan.verify.all_copies && plan.verify.drives.length > 0 && (
            <div>
              <p className="label mb-1">Copies re-read</p>
              <ul className="flex flex-col gap-0.5 text-xs text-muted">
                {plan.verify.drives.map((d) => (
                  <li key={d.label} className={d.mismatches > 0 ? 'text-err' : undefined}>
                    {d.label}: {plural(d.files, 'file')} ({fmtBytes(d.bytes)}) · {d.mismatches > 0 ? `${plural(d.mismatches, 'mismatch')}` : 'all match'}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {plan.verify_kept.length > 0 && (
            <div>
              <p className="label mb-1">Kept because a copy did not check out</p>
              <ul className="flex max-h-32 flex-col gap-0.5 overflow-auto text-xs text-err">
                {plan.verify_kept.map((k) => (
                  <li key={k.rel_path}>
                    {k.rel_path} · {k.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
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
          {plan.deletable_files > 0 && !verifyAll && (
            <p className="text-xs text-err">Bypassing the check of every copy: the second copy is trusted from the catalog.</p>
          )}
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
