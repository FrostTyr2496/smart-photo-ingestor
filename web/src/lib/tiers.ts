// What a card's files are, ranked by where else their bytes are (card-3, D-046). Ported from the earlier plain-JS UI.
import { plural } from './format';
import { roleLabel } from './labels';
import type { FilesBytes } from './types';

export type Tier = 'only_on_card' | 'unchecked' | 'needs_backup' | 'backed_up';
export const TIER_ORDER: Tier[] = ['only_on_card', 'unchecked', 'needs_backup', 'backed_up']; // worst first

export const TIER: Record<Tier | 'empty', { label: string; pill: string; headline: string }> = {
  only_on_card: { label: 'Only on this card', pill: 'Back up now', headline: 'Back up now.' },
  unchecked: { label: 'Not checked', pill: 'Not checked', headline: 'Not checked yet.' },
  needs_backup: { label: 'Needs backup', pill: 'Needs backup', headline: 'Needs backup.' },
  backed_up: { label: 'Backed up', pill: 'Safe to remove', headline: 'Safe to remove.' },
  empty: { label: 'Empty', pill: 'Empty', headline: 'No photos on this card.' },
};

export const TIER_HINT: Record<Tier | 'empty', string> = {
  only_on_card: 'No other drive has these files. Import or copy them before you format the card.',
  unchecked: 'Check the card to compare it with your other drives. Checking only reads the card.',
  needs_backup: 'They are on a working or legacy drive, but not on a backup (master) drive yet.',
  backed_up: 'Every file is on a backup (master) drive.',
  empty: '',
};

/** Tailwind classes per tier: text, soft fill, solid fill. */
export const TIER_TONE: Record<Tier | 'empty', { text: string; soft: string; solid: string }> = {
  only_on_card: { text: 'text-err', soft: 'border-err/30 bg-err/10 text-err', solid: 'bg-err' },
  unchecked: { text: 'text-skip', soft: 'border-skip/30 bg-skip/10 text-skip', solid: 'bg-skip' },
  needs_backup: { text: 'text-maybe', soft: 'border-maybe/30 bg-maybe/10 text-maybe', solid: 'bg-maybe' },
  backed_up: { text: 'text-go', soft: 'border-go/30 bg-go/10 text-go', solid: 'bg-go' },
  empty: { text: 'text-muted', soft: 'border-line-strong text-muted', solid: 'bg-skip' },
};

export type TierCounts = Record<Tier, FilesBytes>;

export interface CardCamera {
  camera: string;
  name: string;
  tier: Tier;
  files: number;
  counts: TierCounts;
}

export interface CopyVolume extends FilesBytes {
  volume_id: string;
  label: string;
  role: string;
}

export interface Card {
  volume_id: string | null;
  label: string;
  mount: string | null;
  mounted: boolean;
  last_seen_at: string | null;
  registered?: boolean;
  tier: Tier | 'empty';
  files: number;
  bytes: number;
  counts: TierCounts;
  cameras: CardCamera[];
  copies_by_volume: CopyVolume[];
  checked_at: string | null;
}

/** "A", "A and B", "A, B and C". */
export const nameList = (names: string[]): string =>
  names.length < 2 ? names[0] || '' : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

/** The drives that hold copies of a card's files: "X9 Pro (Working drive) and Flats 1 (Legacy drive)". */
export function cardCopyNames(c: Card, roles?: string[]): string {
  return nameList(
    (c.copies_by_volume || []).filter((v) => !roles || roles.includes(v.role)).map((v) => `${v.label || 'drive'} (${roleLabel(v.role)})`),
  );
}

export function cardHint(c: Card): string {
  if (c.tier === 'needs_backup') {
    const on = cardCopyNames(c, ['WORKING', 'LEGACY']);
    return on ? `They are on ${on}, but not on a backup (master) drive yet.` : TIER_HINT.needs_backup;
  }
  if (c.tier === 'backed_up') {
    const on = cardCopyNames(c, ['MASTER']);
    return on ? `Every file is on ${on}.` : TIER_HINT.backed_up;
  }
  return TIER_HINT[c.tier];
}

export function cardHeadline(c: Card): string {
  if (c.tier === 'only_on_card') return `Back up now: ${plural(c.counts.only_on_card.files, 'file')} only on this card.`;
  if (c.tier === 'unchecked') return `Not checked: ${plural(c.counts.unchecked.files, 'file')} still to compare.`;
  if (c.tier === 'needs_backup') {
    const on = cardCopyNames(c, ['WORKING', 'LEGACY']);
    return `Needs backup: ${plural(c.counts.needs_backup.files, 'file')} ${on ? `only on ${on}, ` : ''}not on a backup drive yet.`;
  }
  if (c.tier === 'backed_up') {
    const on = cardCopyNames(c, ['MASTER']);
    return `Safe to remove: every file is on ${on ? `backup drive ${on}` : 'a backup drive'}.`;
  }
  return TIER.empty.headline;
}

/** The worst tier among connected cards, for the header dot. */
export function worstTier(cards: Array<{ mounted: boolean; tier: string }>): Tier | 'empty' | null {
  const connected = cards.filter((c) => c.mounted);
  if (!connected.length) return null;
  return TIER_ORDER.find((t) => connected.some((c) => c.tier === t)) || 'empty';
}
