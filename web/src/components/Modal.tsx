import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { springs } from '../lib/motion';

/** A centred dialog that springs in. Esc and a click outside close it. */
export function Modal({ open, title, onClose, children, footer, wide }: { wide?: boolean; open: boolean; title: string; onClose: () => void; children: ReactNode; footer: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  // Callers pass a new onClose each render; keep it in a ref so a re-render (e.g. the drive list polling) does not
  // re-run the effect and pull focus back to the first field.
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close.current();
    document.addEventListener('keydown', onKey);
    const t = window.setTimeout(() => box.current?.querySelector<HTMLElement>('input, select, button')?.focus(), 30);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  // Portalled to <body>: the page column animates with a transform, which would make `fixed` relative to it, so the
  // backdrop covered the main column but not the sidebar.
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            ref={box}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={springs.snappy}
            onClick={(e) => e.stopPropagation()}
            className={`w-full ${wide ? 'max-w-xl' : 'max-w-md'} rounded-2xl border border-line-strong bg-raised shadow-2xl shadow-black/60`}
          >
            <h2 className="border-b border-line px-5 py-4 text-lg font-semibold">{title}</h2>
            <div className="flex flex-col gap-4 px-5 py-4">{children}</div>
            <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}
