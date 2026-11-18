/**
 * Shared physics, from mkh4n-website src/lib/motion.js. Every animated component uses these, so the whole UI
 * moves the same way.
 *   bouncy – things arriving: overshoots slightly, then settles.
 *   snappy – selection pills and popovers: fast, no overshoot.
 *   glide  – progress bars: smooth, no overshoot, so a bar never runs past its value.
 */
export const springs = {
  bouncy: { type: 'spring', stiffness: 190, damping: 15, mass: 0.9 },
  snappy: { type: 'spring', stiffness: 420, damping: 34 },
  glide: { type: 'spring', stiffness: 70, damping: 20, mass: 1 },
} as const;

export const fade = (delay = 0) => ({ duration: 0.35, ease: 'easeOut', delay }) as const;

/** A list item arriving: rise and fade, with a small stagger by index. */
export const rise = (i = 0) => ({
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4, transition: { duration: 0.15 } },
  transition: { ...springs.snappy, delay: Math.min(i, 8) * 0.03 },
});
