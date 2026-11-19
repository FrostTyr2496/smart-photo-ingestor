import { fmtDate, relTime } from '../lib/format';
import { useDriveActivity } from '../lib/driveActivity';
import { useStore } from '../lib/store';
import type { Drive } from '../lib/types';
import { Icon } from './bits';

/** How long a page edge keeps pulsing after its drive is pulled out (rv-38). */
export const PULLED_MS = 8000;

/** Classes for the head section of a drive's or card's page while it is not connected: a dashed amber edge, pulsing
 * for a few seconds if it was just unplugged. Empty while connected. */
export function useUnpluggedEdge(d: Drive | null | undefined): string {
  const { left } = useDriveActivity();
  const { now } = useStore();
  if (!d || d.mounted) return '';
  const at = left[d.key];
  return `unplugged-edge${at !== undefined && (now || Date.now()) - at < PULLED_MS ? ' unplugged-pulled' : ''}`;
}

/** "Not connected" stats for a drive's or card's page: when it was last seen, where, and how full (rv-38). */
export function UnpluggedStrip({ d, extra = [] }: { d: Drive; extra?: { label: string; value: string; title?: string }[] }) {
  const { left } = useDriveActivity();
  const { now } = useStore();
  if (d.mounted) return null;
  const at = left[d.key];
  const seenIso = at !== undefined ? new Date(at).toISOString() : d.last_seen_at;
  const stats = [
    { label: 'Last seen', value: seenIso ? relTime(seenIso, now || Date.now()) || 'just now' : 'Never', title: seenIso ? fmtDate(seenIso) : undefined },
    ...(d.mount ? [{ label: 'Last mounted at', value: d.mount, title: d.mount }] : []),
    ...(d.size ? [{ label: 'Free when last seen', value: d.free ? `${d.free} of ${d.size}` : d.size }] : []),
    ...extra,
  ];
  return (
    <div className="mt-4 rounded-lg border border-maybe/30 bg-maybe/[0.06] px-3 py-2.5">
      <p className="flex items-center gap-2 text-sm text-maybe">
        <Icon name="drive" size={13} />
        {at !== undefined ? 'Unplugged while you were here.' : 'Not connected.'}
        <span className="text-xs text-muted">Showing what PhotoVault last recorded. Plug it back in to refresh.</span>
      </p>
      <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
        {stats.map((s) => (
          <div key={s.label} className="min-w-0">
            <dt className="text-2xs uppercase tracking-wide text-faint">{s.label}</dt>
            <dd className="truncate font-mono text-xs text-ink/90" title={s.title ?? s.value}>
              {s.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
