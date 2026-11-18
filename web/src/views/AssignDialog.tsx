import { useEffect, useState, useSyncExternalStore } from 'react';
import { Field, Modal } from '../components/Modal';
import { baseName } from '../lib/labels';
import { hrefFor } from '../lib/route';
import { useStore } from '../lib/store';
import type { Drive } from '../lib/types';

export interface SetupIntent {
  taskId: number | null;
  role: string;
  label: string;
  runAfter: boolean;
}

/** What the user chose for each drive being set up (by drive key), so the setup page can carry on after it. */
export const setupIntent = new Map<string, SetupIntent>();
const listeners = new Set<() => void>();
let version = 0;
export function rememberSetup(key: string, intent: SetupIntent): void {
  setupIntent.set(key, intent);
  version += 1;
  listeners.forEach((l) => l());
}
export function useSetupIntent(key: string): SetupIntent | undefined {
  useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => version,
  );
  return setupIntent.get(key);
}

const ROLES = [
  { value: 'WORKING', text: 'Working drive (copy target)' },
  { value: 'MASTER', text: 'Master backup' },
  { value: 'LEGACY', text: 'Legacy drive (existing photos)' },
];

export interface AssignPreset {
  role?: string;
  label?: string;
  runAfter?: boolean;
}

/** Set up a new drive: pick its role and name. Setup runs as a task; the setup page follows it. */
export function AssignDialog({ drive, preset, onClose }: { drive: Drive | null; preset?: AssignPreset; onClose: () => void }) {
  const { startTask, setIgnored } = useStore();
  const local = Boolean(drive?.local);
  const ntfs = Boolean(drive && !drive.local && /ntfs/i.test(drive.fs || ''));
  const roles = ntfs ? ROLES.filter((r) => r.value === 'LEGACY') : local ? ROLES.filter((r) => r.value !== 'LEGACY') : ROLES;
  const [role, setRole] = useState('WORKING');
  const [name, setName] = useState('');
  const [runAfter, setRunAfter] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!drive) return;
    const wanted = preset?.role && roles.some((r) => r.value === preset.role) ? preset.role : (roles[0]?.value ?? 'LEGACY');
    setRole(ntfs ? 'LEGACY' : wanted);
    setName(preset?.label ?? drive.label ?? '');
    setRunAfter(preset?.runAfter ?? true);
    setBusy(false);
  }, [drive?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (!drive || busy) return;
    setBusy(true);
    const label = name.trim();
    const args: Record<string, unknown> = { mount: drive.mount, role };
    if (label) args.label = label;
    const taskId = await startTask('assign', args);
    setBusy(false);
    // startTask has already shown why it could not start; stay on the dialog rather than open a setup that never runs.
    if (taskId === null) return;
    rememberSetup(drive.key, { taskId, role, label, runAfter });
    onClose();
    window.location.hash = hrefFor({ view: 'setup', key: drive.key });
  };

  const fs = (drive?.fs || '').toUpperCase();
  return (
    <Modal
      open={Boolean(drive)}
      title="Set up drive"
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className="btn-ghost mr-auto"
            onClick={() => {
              if (drive) void setIgnored(drive, true);
              onClose();
            }}
          >
            Ignore drive
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy} onClick={submit}>
            Set up drive
          </button>
        </>
      }
    >
      <p className="text-sm text-muted">
        {drive?.local
          ? `Folder on this Mac: ${drive.mount}. Only this folder is used.`
          : `Drive: ${baseName(drive?.mount)}${drive?.fs ? ' · ' + drive.fs : ''}`}
      </p>
      <Field label="Use it as">
        <select value={role} onChange={(e) => setRole(e.target.value)} className="h-9">
          {roles.map((r) => (
            <option key={r.value} value={r.value}>
              {r.text}
            </option>
          ))}
        </select>
      </Field>
      {ntfs && (
        <p className="text-xs text-maybe">
          {`macOS can read ${fs} drives but cannot write to them, so this drive can only be a legacy drive. To use it as a working drive or master backup, reformat it as APFS or exFAT (this erases it).`}
        </p>
      )}
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="For example, X9 Pro" className="h-9" />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={runAfter} onChange={(e) => setRunAfter(e.target.checked)} />
        Run the workflow when setup finishes (index, hash, scan metadata)
      </label>
    </Modal>
  );
}
