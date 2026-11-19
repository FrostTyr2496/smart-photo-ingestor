import { commandKey, flagValue, sameMount, taskMounts } from './labels';

/** What a live run is doing to a card: copying its files off, checking it, or clearing it. */
export type CardRunKind = 'copy' | 'check' | 'clear';

export interface CardRef {
  volume_id: string | null;
  mount: string | null;
}

/**
 * Whether a live run (its command words, without `--json`) works on this card, and how. A dry run changes nothing,
 * so it does not count. An import names the card by mount (`--source`); a backup by id (`--card`) or mount (`--from`).
 */
export function cardRunKind(words: string[], card: CardRef): CardRunKind | null {
  if (words.includes('--dry-run')) return null;
  const onMount = (m: string | undefined) => sameMount(m, card.mount);
  switch (commandKey(words)) {
    case 'ingest':
      return onMount(flagValue(words, '--source')) ? 'copy' : null;
    case 'resume':
      return null; // a resume names a job, not a card: the drive list shows it
    case 'backup':
      return (card.volume_id && flagValue(words, '--card') === card.volume_id) || onMount(flagValue(words, '--from')) ? 'copy' : null;
    case 'card_check':
    case 'card-status':
      return taskMounts(words).some(onMount) ? 'check' : null;
    case 'card_clear':
      return taskMounts(words).some(onMount) ? 'clear' : null;
    default:
      return null;
  }
}

/** The mounts a copy writes to, so the page can say where the files are going. */
export function copyTargets(words: string[]): string[] {
  const to = flagValue(words, commandKey(words) === 'ingest' ? '--target' : '--to');
  return to ? [to] : [];
}

/** "Updated 8 s ago" / "Updated 2 min ago": finer than relTime, which says "just now" for a whole minute. */
export function updatedAgo(at: number | null, now: number): string {
  if (at === null) return '';
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 5) return 'Updated just now';
  if (s < 60) return `Updated ${s} s ago`;
  return `Updated ${Math.round(s / 60)} min ago`;
}
