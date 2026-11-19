import { useEffect, useRef, useState } from 'react';
import { TierPill } from '../components/FileList';
import { Field, Modal } from '../components/Modal';
import { api } from '../lib/api';
import { driveName, mountName, roleKey } from '../lib/labels';
import {
  batchLimit,
  batchLine,
  importArgs,
  inputsKey,
  overCapacity,
  readPlan,
  tierAfterImport,
  type BatchChoice,
  type ImportPlan,
} from '../lib/importPlan';
import { useStore } from '../lib/store';
import type { TaskDetail } from '../lib/types';

type Phase = 'idle' | 'planning' | 'planned' | 'failed';

/** Import a card onto a working drive (ing-*, im-4). The card is only read. A dry run shows the plan; Apply runs the same inputs. */
export function ImportDialog({ card, onClose }: { card: { mount: string; label: string } | null; onClose: () => void }) {
  const { drives, startTask, ask } = useStore();
  const working = drives.filter((t) => roleKey(t.role) === 'working' && !t.ignored);
  const firstConnected = working.find((t) => t.mounted);
  const [target, setTarget] = useState('');
  const [name, setName] = useState('');
  const [choice, setChoice] = useState<BatchChoice>('100');
  const [custom, setCustom] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [plannedKey, setPlannedKey] = useState('');
  const run = useRef(0); // ignore a dry run's answer once the dialog moved on

  useEffect(() => {
    if (card) {
      setTarget(firstConnected?.mount ?? '');
      setName('');
      setChoice('100');
      setCustom('');
      setPhase('idle');
      setPlan(null);
      setWarnings([]);
      setError('');
      setPlannedKey('');
      run.current += 1;
    }
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps

  const limit = batchLimit(choice, custom);
  const inputs = card && target && limit !== undefined ? { source: card.mount, target, name, limit } : null;
  const key = inputs ? inputsKey(inputs) : '';
  const applyReady = phase === 'planned' && Boolean(plan) && plannedKey === key && Boolean(plan && plan.files > 0);

  const dryRun = async () => {
    if (!inputs) return;
    const mine = ++run.current;
    setPhase('planning');
    setPlan(null);
    setWarnings([]);
    setError('');
    const id = await startTask('ingest', { ...importArgs(inputs), dry_run: true });
    if (!id) {
      setPhase('failed');
      setError('The dry run could not start.');
      return;
    }
    for (;;) {
      await new Promise((r) => window.setTimeout(r, 700));
      if (run.current !== mine) return;
      try {
        const d = await api<TaskDetail>('GET', `/api/tasks/${id}?since=-1`);
        if (d.state === 'running') continue;
        const read = readPlan(d.events);
        setWarnings(read.warnings);
        if (read.plan && d.state === 'done') {
          setPlan(read.plan);
          setPlannedKey(inputsKey(inputs));
          setPhase('planned');
        } else {
          setError(read.error || 'The dry run did not finish.');
          setPhase('failed');
        }
        return;
      } catch (err) {
        setError((err as Error).message);
        setPhase('failed');
        return;
      }
    }
  };

  const apply = async () => {
    if (!card || !inputs || !applyReady || !plan) return;
    const ok = await ask({
      title: 'Start the import?',
      lines: [
        `Copy ${batchLine(plan)} (${plan.planned}) from "${card.label}" onto "${mountName(target, drives)}".`,
        name.trim() ? `Shoot name: ${name.trim()}.` : 'Shoot name: chosen from the photo dates.',
        'Each file is copied and checked by checksum. The card is left as it is.',
      ],
      action: 'Import',
    });
    if (!ok) return;
    onClose();
    await startTask('ingest', { ...importArgs(inputs), dry_run: false });
  };

  const hasTarget = working.some((t) => t.mounted);
  const sel = 'h-9';
  return (
    <Modal
      wide
      open={Boolean(card)}
      title="Import from card"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn" disabled={!inputs || phase === 'planning'} onClick={dryRun}>
            {phase === 'planning' ? 'Planning…' : 'Dry run'}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!applyReady}
            title={applyReady ? undefined : 'Run a dry run with these settings first'}
            onClick={apply}
          >
            Apply
          </button>
        </>
      }
    >
      <p className="text-sm text-muted">Card: {card?.label}</p>
      <Field label="Copy onto">
        <select value={target} onChange={(e) => setTarget(e.target.value)} className={sel} disabled={!hasTarget}>
          {!target && <option value="">Choose a drive</option>}
          {working.map((t) => (
            <option key={t.key} value={t.mount} disabled={!t.mounted}>
              {driveName(t)}
              {t.mounted ? '' : ' (not connected)'}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Second drive">
        <select className={sel} disabled title="coming with bk-3" value="">
          <option value="">None (coming with bk-3)</option>
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Batch size">
          <select value={choice} onChange={(e) => setChoice(e.target.value as BatchChoice)} className={sel}>
            <option value="10">First 10 frames</option>
            <option value="100">First 100 frames</option>
            <option value="all">Whole card</option>
            <option value="custom">Custom…</option>
          </select>
        </Field>
        {choice === 'custom' && (
          <Field label="Frames">
            <input value={custom} onChange={(e) => setCustom(e.target.value)} inputMode="numeric" placeholder="e.g. 250" className={sel} />
          </Field>
        )}
      </div>
      {choice === 'custom' && limit === undefined && custom !== '' && <p className="-mt-2 text-xs text-maybe">Use a whole number of 1 or more.</p>}
      <Field label="Shoot name (optional)">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Chosen from the photo dates if empty" className={sel} />
      </Field>
      {!hasTarget && <p className="text-xs text-maybe">No working drive is connected. Plug one in or set one up first.</p>}

      {phase === 'planning' && <div className="card h-24 animate-pulse" />}
      {phase === 'failed' && <p className="text-sm text-err">{error}</p>}
      {plan && phase === 'planned' && (
        <PlanView plan={plan} warnings={warnings} stale={plannedKey !== key} target={mountName(target, drives)} />
      )}
      {phase === 'idle' && <p className="text-xs text-faint">A dry run shows exactly what would be copied. Nothing is written until you Apply.</p>}
    </Modal>
  );
}

function PlanView({ plan, warnings, stale, target }: { plan: ImportPlan; warnings: string[]; stale: boolean; target: string }) {
  const short = overCapacity(plan);
  return (
    <div className={`flex flex-col gap-2 rounded-xl border border-line bg-raised/40 p-3 text-sm ${stale ? 'opacity-60' : ''}`}>
      {stale && <p className="text-xs text-maybe">The settings changed. Run the dry run again to apply.</p>}
      <p className="font-medium">{plan.files === 0 ? 'Nothing to copy: every file is already on the drive.' : batchLine(plan)}</p>
      <p className="text-xs text-muted">
        {plan.groups.toLocaleString('en-US')} frames
        {plan.limit !== null && plan.groups_total > plan.groups ? ` of ${plan.groups_total.toLocaleString('en-US')} on the card` : ''} · {plan.planned} to copy
        · {plan.target_free} free on {target}
      </p>
      {short && <p className="text-xs text-err">The drive does not have enough free space.</p>}
      <div className="flex items-center gap-2 text-xs text-muted">
        After this import the files will be <TierPill tier={tierAfterImport(false)} /> (one drive; a second drive makes them safe to remove).
      </div>
      {plan.folders.length > 0 && (
        <p className="text-xs text-muted">
          Folders: {plan.folders.slice(0, 4).map((f) => `${f.folder} (${f.files})`).join(', ')}
          {plan.folders.length > 4 ? ` and ${plan.folders.length - 4} more` : ''}
        </p>
      )}
      {plan.sample.length > 0 && (
        <ul className="break-all font-mono text-2xs text-faint">
          {plan.sample.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {warnings.map((w) => (
        <p key={w} className="text-xs text-maybe">
          {w}
        </p>
      ))}
    </div>
  );
}
