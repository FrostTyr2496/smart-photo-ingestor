import { useEffect, useRef, useState } from 'react';
import { TierPill } from '../components/FileList';
import { Field, Modal } from '../components/Modal';
import { api } from '../lib/api';
import { driveName, mountName, roleKey } from '../lib/labels';
import {
  batchLimit,
  batchLine,
  backupArgs,
  importArgs,
  importJobId,
  inputsKey,
  overCapacity,
  pairMounts,
  progressLine,
  readPlan,
  readSecond,
  tierAfterImport,
  type BatchChoice,
  type ImportPlan,
  type SecondPlan,
  type TargetAnswer,
} from '../lib/importPlan';
import { fmtBytes, fmtInt } from '../lib/format';
import { planFolders } from '../lib/planTree';
import { useStore } from '../lib/store';
import type { TaskDetail } from '../lib/types';

type Phase = 'idle' | 'planning' | 'planned' | 'failed' | 'applied';

/** Import a card onto a working drive (ing-*, im-4). The card is only read. A dry run shows the plan; Apply runs the same inputs. */
export function ImportDialog({ card, onClose }: { card: { mount: string; label: string; volume_id?: string | null; test_card?: { hidden: string[] } | null } | null; onClose: () => void }) {
  const { drives, startTask, ask, toast } = useStore();
  // D-111: a test card's hidden drives are never offered (copying there would duplicate files already on them).
  const hidden = card?.test_card?.hidden ?? [];
  const working = drives.filter((t) => roleKey(t.role) === 'working' && !t.ignored && !hidden.includes(t.label ?? ''));
  const firstConnected = working.find((t) => t.mounted);
  const [target, setTarget] = useState('');
  const [second, setSecond] = useState('');
  const [secondPlan, setSecondPlan] = useState<SecondPlan | null>(null);
  const [recs, setRecs] = useState<TargetAnswer | null>(null);
  const [name, setName] = useState('');
  const [choice, setChoice] = useState<BatchChoice>('100');
  const [custom, setCustom] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');
  const [plannedKey, setPlannedKey] = useState('');
  const run = useRef(0); // ignore a dry run's answer once the dialog moved on

  useEffect(() => {
    if (card) {
      setTarget(firstConnected?.mount ?? '');
      setSecond('');
      setSecondPlan(null);
      setRecs(null);
      if (card.volume_id) {
        // rec-1: pre-select the recommended pair (the drive that takes the import and the one that takes the trailing copy).
        const mine = run.current + 1;
        api<TargetAnswer>('GET', `/api/cards/${encodeURIComponent(card.volume_id)}/targets`)
          .then((a) => {
            if (run.current !== mine) return;
            const m = pairMounts(a);
            setRecs(a);
            if (m.first) setTarget(m.first);
            if (m.second) setSecond(m.second);
          })
          .catch(() => undefined);
      }
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
  // The trailing copy needs the card's volume id and a connected working or master drive other than the first.
  const seconds = drives.filter((t) => (roleKey(t.role) === 'working' || roleKey(t.role) === 'master') && !t.ignored && t.mounted && t.mount !== target && !hidden.includes(t.label ?? ''));
  const secondOk = Boolean(second) && Boolean(card?.volume_id) && second !== target;
  const inputs = card && target && limit !== undefined ? { source: card.mount, target, name, limit, second: secondOk ? second : '' } : null;
  const key = inputs ? inputsKey(inputs) : '';
  const targetDrive = drives.find((t) => t.mount === target);
  const applyReady = phase === 'planned' && Boolean(plan) && plannedKey === key && Boolean(plan && plan.files > 0);

  /** Wait for a dry-run task to leave `running`; null when the dialog moved on. */
  const waitDone = async (id: number, mine: number, onEvents?: (d: TaskDetail) => void): Promise<TaskDetail | null> => {
    for (;;) {
      await new Promise((r) => window.setTimeout(r, 700));
      if (run.current !== mine) return null;
      const d = await api<TaskDetail>('GET', `/api/tasks/${id}?since=-1`);
      if (d.state !== 'running') return d;
      onEvents?.(d);
    }
  };

  const dryRun = async () => {
    if (!inputs) return;
    const mine = ++run.current;
    setPhase('planning');
    setProgress('');
    setPlan(null);
    setWarnings([]);
    setError('');
    const id = await startTask('ingest', { ...importArgs(inputs), dry_run: true });
    if (!id) {
      setPhase('failed');
      setError('The dry run could not start.');
      return;
    }
    try {
      const d = await waitDone(id, mine, (t) => setProgress(progressLine(t.events)));
      if (!d) return;
      const read = readPlan(d.events);
      if (!read.plan || d.state !== 'done') {
        setWarnings(read.warnings);
        setError(read.error || 'The dry run did not finish.');
        setPhase('failed');
        return;
      }
      let sp: SecondPlan | null = null;
      if (inputs.second && card?.volume_id) {
        // The second drive is checked now (file system, marker, room, a busy writer); the import itself is not there yet.
        const bid = await startTask('backup', { ...backupArgs(card.volume_id, target, inputs.second), dry_run: true });
        const bd = bid ? await waitDone(bid, mine) : null;
        if (!bd) {
          if (run.current === mine) {
            setError('The second drive could not be checked.');
            setPhase('failed');
          }
          return;
        }
        const r2 = readSecond(bd.events);
        if (!r2.second || bd.state !== 'done') {
          setError(r2.error || 'The second drive could not be checked.');
          setPhase('failed');
          return;
        }
        sp = r2.second;
      }
      setWarnings(read.warnings);
      setPlan(read.plan);
      setSecondPlan(sp);
      setPlannedKey(inputsKey(inputs));
      setPhase('planned');
    } catch (err) {
      setError((err as Error).message);
      setPhase('failed');
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
        ...(inputs.second ? [`Each file is then copied from "${mountName(target, drives)}" onto "${mountName(inputs.second, drives)}" as it arrives.`] : []),
      ],
      action: 'Import',
    });
    if (!ok) return;
    const id = await startTask('ingest', { ...importArgs(inputs), dry_run: false });
    if (id) setPhase('applied'); // the dialog stays open so each target folder can be shown in Finder (im-7)
    else onClose();
    if (!id || !inputs.second || !card.volume_id) return;
    // The trailing copy follows the import job, whose id the import's plan reports first.
    for (let i = 0; i < 600; i++) {
      await new Promise((r) => window.setTimeout(r, 700));
      try {
        const d = await api<TaskDetail>('GET', `/api/tasks/${id}?since=-1`);
        const job = importJobId(d.events);
        if (job !== undefined) {
          await startTask('backup', backupArgs(card.volume_id, target, inputs.second, job));
          return;
        }
        if (d.state !== 'running') return;
      } catch {
        return;
      }
    }
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
            {phase === 'applied' ? 'Close' : 'Cancel'}
          </button>
          <button type="button" className="btn" disabled={!inputs || phase === 'planning' || phase === 'applied'} onClick={dryRun}>
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
      <p className="text-sm text-muted">
        Card: {card?.label}
        {hidden.length > 0 && <span className="text-faint"> · Test card: {hidden.join(', ')} not offered (their copies are hidden)</span>}
      </p>
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
      <Field label="Second drive (copies each file as it arrives, so the card can turn green)">
        <select value={second} onChange={(e) => setSecond(e.target.value)} className={sel} disabled={seconds.length === 0 || !card?.volume_id}>
          <option value="">{seconds.length === 0 ? 'None connected' : 'None (files stay on one drive)'}</option>
          {seconds.map((t) => (
            <option key={t.key} value={t.mount}>
              {driveName(t)}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Batch size">
          <select value={choice} onChange={(e) => setChoice(e.target.value as BatchChoice)} className={sel}>
            <option value="10">Next 10 frames</option>
            <option value="100">Next 100 frames</option>
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
      {recs && recs.candidates.length > 0 && (
        <div className="rounded-xl border border-line bg-raised/40 p-3 text-xs text-muted">
          <p className="font-medium text-ink">Recommended: {recs.pair.note}</p>
          <ul className="mt-1 flex flex-col gap-1">
            {recs.candidates.map((c) => (
              <li key={c.volume_id} className={c.usable ? '' : 'text-faint'}>
                <span className="font-medium">{c.label}</span>: {c.reasons.join(' · ')}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!hasTarget && <p className="text-xs text-maybe">No working drive is connected. Plug one in or set one up first.</p>}

      {phase === 'planning' && (
        <div className="card h-24 animate-pulse p-3 text-sm text-muted" role="status">
          {progress || 'Planning…'}
        </div>
      )}
      {phase === 'failed' && <p className="text-sm text-err">{error}</p>}
      {plan && (phase === 'planned' || phase === 'applied') && (
        <PlanView
          plan={plan}
          warnings={warnings}
          stale={phase === 'planned' && plannedKey !== key}
          target={mountName(target, drives)}
          second={secondPlan}
          applied={phase === 'applied'}
          volumeId={targetDrive?.volume_id ?? null}
          onError={(m) => toast(m, 'err')}
        />
      )}
      {phase === 'idle' && <p className="text-xs text-faint">A dry run shows exactly what would be copied. Nothing is written until you Apply.</p>}
    </Modal>
  );
}

function PlanView({
  plan,
  warnings,
  stale,
  target,
  second,
  applied,
  volumeId,
  onError,
}: {
  plan: ImportPlan;
  warnings: string[];
  stale: boolean;
  target: string;
  second: SecondPlan | null;
  applied: boolean;
  volumeId: string | null;
  onError: (m: string) => void;
}) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const folders = planFolders(plan.items, closed);
  const flip = (k: string) =>
    setClosed((c) => {
      const n = new Set(c);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const show = (path: string) => {
    if (volumeId) api('POST', `/api/volumes/${encodeURIComponent(volumeId)}/reveal`, { rel_path: path }).catch((err: Error) => onError(err.message));
  };
  const short = overCapacity(plan);
  return (
    <div className={`flex flex-col gap-2 rounded-xl border border-line bg-raised/40 p-3 text-sm ${stale ? 'opacity-60' : ''}`}>
      {stale && <p className="text-xs text-maybe">The settings changed. Run the dry run again to apply.</p>}
      <p className="font-medium">{plan.files === 0 ? 'Nothing to copy: every file is already on the drive.' : batchLine(plan)}</p>
      <p className="text-xs text-muted">
        {plan.groups.toLocaleString('en-US')} frames
        {plan.limit !== null && plan.groups_total - plan.already.frames > plan.groups ? ` of ${(plan.groups_total - plan.already.frames).toLocaleString('en-US')} still to copy` : ''} · {plan.planned} to copy
        {plan.already.frames > 0 ? ` · ${plan.already.frames.toLocaleString('en-US')} frames already on ${target}` : ''}
        · {plan.target_free} free on {target}
      </p>
      {short && <p className="text-xs text-err">The drive does not have enough free space.</p>}
      {second && (
        <p className={`text-xs ${second.target_free_bytes !== null && plan.planned_bytes > second.target_free_bytes ? 'text-err' : 'text-muted'}`}>
          Second copy onto {second.target} ({second.target_fs}): {plan.planned} to copy · {second.target_free} free
          {second.target_free_bytes !== null && plan.planned_bytes > second.target_free_bytes ? ' · not enough free space' : ''}
        </p>
      )}
      <div className="flex items-center gap-2 text-xs text-muted">
        After this import the files will be <TierPill tier={tierAfterImport(second !== null)} />{' '}
        {second ? '(on two drives once both copies finish)' : '(one drive; a second drive makes them safe to remove)'}
      </div>
      {folders.length > 0 && (
        <div className="flex max-h-56 flex-col overflow-auto rounded-lg border border-line text-xs" role="tree" aria-label={`Folders on ${target}`}>
          {folders.map((f) => (
            <div key={f.key} className="flex items-center gap-2 px-2 py-1 hover:bg-raised" style={{ paddingLeft: 8 + f.depth * 16 }}>
              <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => flip(f.key)} aria-expanded={!closed.has(f.key)}>
                <span className="text-faint">{closed.has(f.key) ? '▸' : '▾'}</span> <span className="font-medium">{f.name}</span>
              </button>
              <span className="shrink-0 text-muted">
                {fmtInt(f.files)} {f.files === 1 ? 'file' : 'files'} · {fmtBytes(f.bytes)}
              </span>
              {applied && volumeId && (
                <button type="button" className="btn-ghost h-6 px-2 text-2xs" onClick={() => show(f.path)}>
                  Show in Finder
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {folders.length === 0 && plan.sample.length > 0 && (
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
