import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef } from 'react';
import { springs } from '../lib/motion';
import { useStore } from '../lib/store';
import { Icon } from './bits';

/** Toasts stack bottom-right and spring in. */
export function Toasts() {
  const { toasts, dismissToast } = useStore();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[310] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2" aria-live="polite">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, transition: { duration: 0.18 } }}
            transition={springs.bouncy}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-raised/95 px-3.5 py-3 text-sm shadow-2xl shadow-black/40 backdrop-blur-xl ${
              t.kind === 'err' ? 'border-err/40' : 'border-line-strong'
            }`}
          >
            <span className={`mt-0.5 ${t.kind === 'err' ? 'text-err' : 'text-go'}`}>
              <Icon name={t.kind === 'err' ? 'warn' : 'check'} />
            </span>
            <span className="flex-1">{t.text}</span>
            {t.action && (
              <button
                type="button"
                onClick={() => {
                  t.action?.run();
                  dismissToast(t.id);
                }}
                className="font-medium text-accent hover:underline"
              >
                {t.action.label}
              </button>
            )}
            <button type="button" onClick={() => dismissToast(t.id)} className="text-faint hover:text-ink" aria-label="Dismiss">
              <Icon name="x" size={12} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** The one confirm dialog. Esc and Cancel resolve false. */
export function ConfirmDialog() {
  const { confirm } = useStore();
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!confirm) return undefined;
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && confirm.resolve(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [confirm]);
  return (
    <AnimatePresence>
      {confirm && (
        <motion.div
          className="fixed inset-0 z-[300] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => confirm.resolve(false)}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={confirm.title}
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={springs.snappy}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md rounded-2xl border border-line-strong bg-raised p-5 shadow-2xl shadow-black/60"
          >
            <h2 className="text-lg font-semibold">{confirm.title}</h2>
            {confirm.lines.map((l) => (
              <p key={l} className="mt-2 text-sm text-muted">
                {l}
              </p>
            ))}
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn" onClick={() => confirm.resolve(false)}>
                Cancel
              </button>
              <button type="button" ref={okRef} className="btn-primary" onClick={() => confirm.resolve(true)}>
                {confirm.action}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
