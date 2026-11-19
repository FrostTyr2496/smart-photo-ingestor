import { AnimatePresence, motion } from 'framer-motion';
import { useDialogs, type DialogEntry } from '../lib/dialogs';
import { DIALOG_TITLE, dockLine, dockOrder } from '../lib/notify';
import { springs } from '../lib/motion';
import { ClearDialog } from '../views/ClearDialog';
import { ImportDialog } from '../views/ImportDialog';
import { MakeGreenDialog } from '../views/MakeGreenDialog';
import { ProgressBar } from './ProgressBar';

/** The card object in each entry is stable between renders (the dialogs' effects depend on it).
 *  The three long-running dialogs, mounted once at app level (rv-32). A minimized one stays mounted but hidden. */
function One({ e }: { e: DialogEntry }) {
  const { closeDialog, minimizeDialog, setDialogStatus } = useDialogs();
  const common = {
    minimized: e.minimized,
    onClose: () => closeDialog(e.key),
    onMinimize: () => minimizeDialog(e.key),
    onStatus: (s: { line: string; value: number | null }) => setDialogStatus(e.key, s),
  };
  const c = e.card;
  if (e.kind === 'import') return <ImportDialog card={c} {...common} />;
  if (e.kind === 'clear') return <ClearDialog card={c} {...common} />;
  return <MakeGreenDialog card={c} {...common} />;
}

export function DialogHost() {
  const { entries } = useDialogs();
  return (
    <>
      {entries.map((e) => (
        <One key={e.key} e={e} />
      ))}
      <Dock />
    </>
  );
}

/** Pills for minimized dialogs, bottom-right above the toasts: title, live status, a small bar; click restores. */
function Dock() {
  const { entries, restoreDialog } = useDialogs();
  const pills = dockOrder(entries).map((d) => entries.find((e) => e.key === d.key)!);
  return (
    <div className="pointer-events-none fixed bottom-24 right-4 z-40 flex w-[min(340px,calc(100vw-32px))] flex-col items-end gap-2" aria-live="polite">
      <AnimatePresence initial={false}>
        {pills.map((e) => (
          <motion.button
            key={e.key}
            type="button"
            layout
            initial={{ opacity: 0, y: 12, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.15 } }}
            transition={springs.snappy}
            onClick={() => restoreDialog(e.key)}
            title={`Restore: ${DIALOG_TITLE[e.kind]} · ${e.card.label}`}
            className="pointer-events-auto flex w-full flex-col gap-1.5 rounded-xl border border-line-strong bg-raised/95 px-3.5 py-2.5 text-left text-sm shadow-2xl shadow-black/40 backdrop-blur-xl hover:border-accent/50"
          >
            <span className="flex items-center gap-2">
              <span className="live-dot h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              <span className="min-w-0 flex-1 truncate">{dockLine(e.kind, e.status)}</span>
            </span>
            <ProgressBar value={e.status.value} live />
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  );
}
