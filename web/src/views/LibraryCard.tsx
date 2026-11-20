import { motion } from 'framer-motion';
import { useEffect, useState, type ReactNode } from 'react';
import { Icon } from '../components/bits';
import { api } from '../lib/api';
import {
  confirmLabel,
  doneCount,
  driveSteps,
  metaStep,
  phoneStep,
  planStep,
  type Checklist,
  type Step,
} from '../lib/library';
import { springs } from '../lib/motion';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';

const TONE: Record<Step['state'], string> = { done: 'text-go', partial: 'text-maybe', none: 'text-faint' };

/** One step's text, with a check when it is done (ob-1). */
function StepText({ step }: { step: Step }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs tabular ${TONE[step.state]}`}>
      {step.state === 'done' && <Icon name="check" size={11} />}
      {step.text}
    </span>
  );
}

function Row({ name, children, muted }: { name: string; children: ReactNode; muted?: string }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
      <span className="min-w-[8rem] flex-1 truncate text-sm font-medium">
        {name}
        {muted && <span className="ml-2 text-xs font-normal text-faint">{muted}</span>}
      </span>
      {children}
    </li>
  );
}

/** The library checklist (ob-1, review sh-8 §3): what each drive, the phone import, the metadata scan and the plan have
 *  done so far. Collapsible; reloads when a drive is plugged or unplugged and when a run finishes. */
export function LibraryCard() {
  const { drives, recent } = useStore();
  const [data, setData] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const mountKey = drives
    .filter((d) => d.mounted)
    .map((d) => d.volume_id ?? d.key)
    .join(',');

  useEffect(() => {
    let alive = true;
    api<Checklist>('GET', '/api/library/checklist')
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [mountKey, recent.length]);

  const summary = data ? doneCount(data) : null;
  const plan = data?.plan ?? null;

  return (
    <section className="card" aria-label="Library checklist">
      <div className="flex items-center gap-2 px-4 py-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex text-muted">
            <Icon name="chevron" size={12} />
          </motion.span>
          <h2 className="font-semibold">Library</h2>
          {summary && (
            <span className="truncate text-xs text-muted">
              {summary.done} of {summary.total} steps done
            </span>
          )}
        </button>
        {plan && plan.unconfirmed > 0 && (
          <a href={hrefFor({ view: 'review' })} className="shrink-0 text-sm font-medium text-accent hover:underline">
            {confirmLabel(plan.unconfirmed)}
          </a>
        )}
      </div>

      {open && (
        <div className="border-t border-line px-4 pb-2">
          {error && <p className="py-3 text-xs text-err">Could not read the library: {error}</p>}
          {!error && !data && <p className="py-3 text-xs text-muted">Reading the library…</p>}
          {data && (
            <>
              <p className="label pt-3">Drives</p>
              {data.drives.length === 0 && <p className="py-2 text-xs text-muted">No drives are registered yet.</p>}
              <ul className="divide-y divide-line">
                {data.drives.map((d) => {
                  const s = driveSteps(d);
                  return (
                    <Row key={d.volume_id} name={d.label || d.volume_id} muted={d.connected ? undefined : 'not connected'}>
                      <StepText step={s.index} />
                      <StepText step={s.hash} />
                      <StepText step={s.thumbs} />
                    </Row>
                  );
                })}
              </ul>
              {data.thumbs_error && <p className="pb-1 text-xs text-err">Review thumbs: {data.thumbs_error}</p>}
              <p className="label pt-3">Library</p>
              <ul className="divide-y divide-line">
                <Row name="Phone import (with analysis)">
                  <StepText step={phoneStep(data.phone)} />
                </Row>
                <Row name="Metadata scan">
                  <StepText step={metaStep(data.meta_scanned)} />
                </Row>
                <Row name="Shoots plan">
                  <StepText step={planStep(plan)} />
                  {plan && <span className="text-xs text-faint">{plan.plan_id}</span>}
                </Row>
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
