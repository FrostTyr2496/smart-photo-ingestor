import { motion, useReducedMotion } from 'framer-motion';
import { springs } from '../lib/motion';

type Tone = 'accent' | 'go' | 'maybe' | 'err' | 'muted';

const FILL: Record<Tone, string> = {
  accent: 'bg-accent',
  go: 'bg-go',
  maybe: 'bg-maybe',
  err: 'bg-err',
  muted: 'bg-ink/30',
};
const GLOW: Record<Tone, string> = {
  accent: 'shadow-[0_0_12px_rgb(var(--c-accent)/0.55)]',
  go: 'shadow-[0_0_12px_rgb(var(--c-go)/0.45)]',
  maybe: 'shadow-[0_0_12px_rgb(var(--c-maybe)/0.45)]',
  err: 'shadow-[0_0_12px_rgb(var(--c-err)/0.45)]',
  muted: '',
};

interface Props {
  /** 0…1, or null for "no total yet" (a sliding segment). */
  value: number | null;
  /** A live bar glides, shimmers and glows; a static one (capacity, hashed share) just sits. */
  live?: boolean;
  tone?: Tone;
  size?: 'xs' | 'sm' | 'md';
  label?: string;
  className?: string;
}

const HEIGHT = { xs: 'h-1', sm: 'h-1.5', md: 'h-2' };

/**
 * A progress bar whose fill glides to each new value on a spring instead of jumping between polls. While live it
 * carries a moving sheen and a soft glow at its edge, so a slow hash still looks alive at 4 %.
 */
export function ProgressBar({ value, live = false, tone = 'accent', size = 'sm', label, className = '' }: Props) {
  const reduce = useReducedMotion();
  const pct = value === null ? null : Math.max(0, Math.min(1, value)) * 100;
  // A sliver is always visible once something is counted, so 0.4 % still reads as "started".
  const shown = pct === null ? 0 : pct > 0 ? Math.max(pct, 1.5) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct === null ? undefined : Math.floor(pct)}
      className={`relative w-full overflow-hidden rounded-full bg-ink/[0.07] ${HEIGHT[size]} ${className}`}
    >
      {pct === null ? (
        <div className={`bar-indet rounded-full ${FILL[tone]} opacity-80`} />
      ) : (
        <motion.div
          className={`relative h-full overflow-hidden rounded-full ${FILL[tone]} ${live ? `bar-sheen ${GLOW[tone]}` : ''}`}
          initial={false}
          animate={{ width: `${shown}%` }}
          transition={reduce ? { duration: 0 } : springs.glide}
        />
      )}
    </div>
  );
}
