import { driveIO, driveName, liveStep } from '../lib/labels';
import { useStore } from '../lib/store';
import type { Drive } from '../lib/types';
import { Icon } from './bits';

/** Why a drive cannot be ejected now, or null when it can (rv-43). */
export function useEjectBlock(d: Drive | null | undefined): string | null {
  const { tasks, elsewhere, drives, busy } = useStore();
  if (!d || !d.volume_id || d.local) return 'Not a removable drive';
  if (!d.mounted) return 'Not connected';
  if (driveIO(d, tasks, elsewhere, drives).length || liveStep(d, tasks, elsewhere) || busy(d)) {
    return `A task is using ${driveName(d)}. Pause it or let it finish first.`;
  }
  return null;
}

/**
 * Eject a drive the way Finder does. `icon` is the sidebar's small ⏏ button; `button` the page header's labelled one.
 * Hidden for the Mac folder and drives that are not connected; disabled (with the reason) while a task uses it.
 */
export function EjectButton({ d, variant = 'button', className = '' }: { d: Drive | null | undefined; variant?: 'icon' | 'button'; className?: string }) {
  const { ejectDrive, ejecting } = useStore();
  const block = useEjectBlock(d);
  if (!d || !d.volume_id || d.local || !d.mounted) return null;
  const busy = ejecting.has(d.key);
  const name = driveName(d);
  const title = busy ? `Ejecting ${name}…` : block ?? `Eject ${name} so it can be unplugged safely`;
  const run = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!block && !busy) void ejectDrive(d);
  };
  if (variant === 'icon') {
    return (
      <button
        type="button"
        onClick={run}
        disabled={Boolean(block) || busy}
        aria-label={`Eject ${name}`}
        title={title}
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-faint hover:bg-ink/10 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent ${busy ? 'live-dot' : ''}`}
      >
        <Icon name="eject" size={12} />
      </button>
    );
  }
  return (
    <button type="button" className={`btn ${className}`} onClick={run} disabled={Boolean(block) || busy} title={title}>
      <Icon name="eject" /> {busy ? 'Ejecting…' : 'Eject'}
    </button>
  );
}
