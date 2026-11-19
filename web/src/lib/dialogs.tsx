import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Card } from './tiers';
import type { DialogKind, DialogRef, DialogStatus } from './notify';

/** One open or minimized long-running dialog (rv-32, D-115). The state lives here, above the routes, so a minimized
 *  dialog stays mounted (its polling and its dry run continue) while the user moves around the app. */
export interface DialogEntry extends DialogRef {
  card: Card;
  status: DialogStatus;
}

interface Dialogs {
  entries: DialogEntry[];
  openDialog: (kind: DialogKind, card: Card) => void;
  closeDialog: (key: string) => void;
  minimizeDialog: (key: string) => void;
  restoreDialog: (key: string) => void;
  setDialogStatus: (key: string, status: DialogStatus) => void;
}

const Ctx = createContext<Dialogs | null>(null);
export const useDialogs = (): Dialogs => {
  const v = useContext(Ctx);
  if (!v) throw new Error('useDialogs outside DialogsProvider');
  return v;
};

const keyFor = (kind: DialogKind, c: Card) => `${kind}:${kind === 'green' ? c.volume_id : c.mount}`;

export function DialogsProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<DialogEntry[]>([]);
  const seq = useRef(0);

  const openDialog = useCallback((kind: DialogKind, card: Card) => {
    if (kind === 'green' ? !card.volume_id : !card.mount) return;
    const key = keyFor(kind, card);
    setEntries((list) =>
      list.some((e) => e.key === key)
        ? list.map((e) => (e.key === key ? { ...e, minimized: false } : e)) // already open or minimized: bring it back
        : [...list, { key, kind, card, mount: card.mount, volumeId: card.volume_id, minimized: false, openedAt: ++seq.current, status: { line: '', value: null } }],
    );
  }, []);
  const closeDialog = useCallback((key: string) => setEntries((list) => list.filter((e) => e.key !== key)), []);
  const minimizeDialog = useCallback((key: string) => setEntries((list) => list.map((e) => (e.key === key ? { ...e, minimized: true } : e))), []);
  const restoreDialog = useCallback((key: string) => setEntries((list) => list.map((e) => (e.key === key ? { ...e, minimized: false } : e))), []);
  const setDialogStatus = useCallback(
    (key: string, status: DialogStatus) =>
      setEntries((list) => {
        const e = list.find((x) => x.key === key);
        if (!e || (e.status.line === status.line && e.status.value === status.value)) return list; // no change: no re-render
        return list.map((x) => (x.key === key ? { ...x, status } : x));
      }),
    [],
  );

  const value = useMemo(
    () => ({ entries, openDialog, closeDialog, minimizeDialog, restoreDialog, setDialogStatus }),
    [entries, openDialog, closeDialog, minimizeDialog, restoreDialog, setDialogStatus],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
