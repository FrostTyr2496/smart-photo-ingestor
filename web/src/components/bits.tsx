import { animate, AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { fmtInt } from '../lib/format';
import { roleKey, roleLabel, type RoleKey } from '../lib/labels';
import { springs } from '../lib/motion';

// ---------------------------------------------------------------- numbers

/** A number that counts from its last value to the new one (the website's CountUp), formatted with separators. */
export function CountUp({ value, format = fmtInt, className = '' }: { value: number; format?: (n: number) => string; className?: string }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    if (reduce || from.current === value) {
      from.current = value;
      setShown(value);
      return undefined;
    }
    const controls = animate(from.current, value, {
      duration: Math.min(1.2, 0.4 + Math.log10(Math.abs(value - from.current) + 1) * 0.15),
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setShown(v),
    });
    from.current = value;
    return () => controls.stop();
  }, [value, reduce]);
  return <span className={`tabular ${className}`}>{format(shown)}</span>;
}

// ---------------------------------------------------------------- pills

const ROLE_TONE: Record<RoleKey | 'new', string> = {
  working: 'border-go/30 bg-go/10 text-go',
  legacy: 'border-maybe/30 bg-maybe/10 text-maybe',
  master: 'border-master/30 bg-master/10 text-master',
  card: 'border-card/30 bg-card/10 text-card',
  unset: 'border-line-strong bg-ink/5 text-muted',
  new: 'border-dashed border-line-strong bg-transparent text-muted',
};
export const ROLE_DOT: Record<RoleKey | 'new', string> = {
  working: 'bg-go',
  legacy: 'bg-maybe',
  master: 'bg-master',
  card: 'bg-card',
  unset: 'bg-skip',
  new: 'bg-skip',
};

export function RolePill({ role, isNew = false, text }: { role: string | null; isNew?: boolean; text?: string }) {
  const key = isNew ? 'new' : roleKey(role);
  return <span className={`chip ${ROLE_TONE[key]}`}>{text ?? (isNew ? 'New' : roleLabel(role))}</span>;
}

const STATE_TONE: Record<string, string> = {
  running: 'border-accent/30 bg-accent/10 text-accent',
  done: 'border-go/30 bg-go/10 text-go',
  failed: 'border-err/30 bg-err/10 text-err',
  paused: 'border-maybe/30 bg-maybe/10 text-maybe',
};
export function StatePill({ state, text }: { state: string; text: string }) {
  return (
    <span className={`chip ${STATE_TONE[state] ?? 'border-line-strong text-muted'}`}>
      {state === 'running' && <span className="live-dot h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
      {text}
    </span>
  );
}

// ---------------------------------------------------------------- disclosure

/** A details block whose body springs open, with a rotating chevron. */
export function Disclosure({
  summary,
  children,
  open,
  onToggle,
  className = '',
  summaryClass = '',
}: {
  summary: ReactNode;
  children: ReactNode;
  open: boolean;
  onToggle: (open: boolean) => void;
  className?: string;
  summaryClass?: string;
}) {
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => onToggle(!open)}
        className={`flex w-full items-center gap-1.5 text-left text-xs text-muted hover:text-ink ${summaryClass}`}
      >
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="inline-flex">
          <Icon name="chevron" size={12} />
        </motion.span>
        {summary}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ ...springs.snappy, opacity: { duration: 0.2 } }}
            className="overflow-hidden"
          >
            <div className="pt-2">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---------------------------------------------------------------- icons

const PATHS: Record<string, ReactNode> = {
  chevron: <path d="M6 4l4 4-4 4" />,
  back: <path d="M10 3L5 8l5 5" />,
  pause: (
    <>
      <path d="M5.5 3.5v9" />
      <path d="M10.5 3.5v9" />
    </>
  ),
  play: <path d="M5 3.5l7 4.5-7 4.5z" />,
  check: <path d="M3.5 8.5l3 3 6-7" />,
  x: (
    <>
      <path d="M4 4l8 8" />
      <path d="M12 4l-8 8" />
    </>
  ),
  moon: <path d="M12.5 9.5A5 5 0 016.5 3.5a5 5 0 106 6z" />,
  folder: <path d="M2 4.5h4l1.5 1.5H14v6.5H2z" />,
  drive: (
    <>
      <rect x="2" y="4" width="12" height="8" rx="2" />
      <path d="M5 9.5h.01M8 9.5h3" />
    </>
  ),
  tasks: (
    <>
      <path d="M3 4h10M3 8h10M3 12h6" />
    </>
  ),
  warn: (
    <>
      <path d="M8 2.5l6 10.5H2z" />
      <path d="M8 7v2.5M8 11.5h.01" />
    </>
  ),
  palette: (
    <>
      <circle cx="8" cy="8" r="6" />
      <circle cx="5.5" cy="6.5" r=".6" />
      <circle cx="8.5" cy="5" r=".6" />
      <circle cx="10.8" cy="7.2" r=".6" />
    </>
  ),
  external: (
    <>
      <path d="M9 3h4v4" />
      <path d="M13 3L7.5 8.5" />
      <path d="M11 9.5V13H3V5h3.5" />
    </>
  ),
  pin: (
    <>
      <path d="M8 14s4.5-4.2 4.5-7.5a4.5 4.5 0 0 0-9 0C3.5 9.8 8 14 8 14z" />
      <circle cx="8" cy="6.5" r="1.6" />
    </>
  ),
  image: (
    <>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <circle cx="5.5" cy="6.5" r="1" />
      <path d="M14 10.5l-3.5-3.5L4 13" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4" />
      <path d="M10 10l3.5 3.5" />
    </>
  ),
  edit: <path d="M10.5 3l2.5 2.5L6 12.5H3.5V10z" />,
};

export function Icon({ name, size = 14, className = '' }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      {PATHS[name]}
    </svg>
  );
}

/** The PhotoVault mark: an aperture. */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="pv-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="rgb(var(--c-accent))" />
          <stop offset="1" stopColor="rgb(var(--c-card))" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="22" height="22" rx="7" fill="url(#pv-logo)" />
      <g fill="none" stroke="rgb(var(--c-on-accent))" strokeWidth="1.5" strokeLinecap="round" opacity="0.9">
        <circle cx="12" cy="12" r="5.5" />
        <path d="M12 6.5l3 5.2M17.2 10.3l-6 .1M15.2 16.6l-3-5.2M8.8 16.6l3-5.1M6.8 10.4l6-.1" />
      </g>
    </svg>
  );
}
