import type { Drive, TaskRec } from './types';
import { argvWords, commandKey, flagValue, summarize, taskMounts, taskTitle } from './labels';

/** A run counts as long (and earns a done popup) after this long (rv-32, D-115). */
export const LONG_MS = 20_000;
/** A popup that is not a failure goes away by itself after this long. */
export const POPUP_MS = 15_000;

/** Per-browser preferences (lib/prefs.ts): the switch in Settings, and whether permission was already asked. */
export const NOTIFY_PREF = 'notify.enabled';
export const NOTIFY_ASKED_PREF = 'notify.asked';

export type DialogKind = 'import' | 'clear' | 'green';

/** What the pure functions need to know about an open or minimized dialog. */
export interface DialogRef {
  key: string;
  kind: DialogKind;
  mount: string | null;
  volumeId: string | null;
  minimized: boolean;
  openedAt: number;
}

export type Outcome = 'done' | 'paused' | 'failed';

export interface DoneText {
  title: string;
  outcome: Outcome;
  body: string;
}

const isDry = (t: Pick<TaskRec, 'argv'>) => argvWords(t.argv).includes('--dry-run');

/** The dialog a task belongs to: a card clear or import on the dialog's card mount, or a make-green backup of its card. */
export function dialogForTask(t: Pick<TaskRec, 'argv'>, dialogs: DialogRef[]): DialogRef | undefined {
  const words = argvWords(t.argv);
  const key = commandKey(words);
  if (key === 'backup') {
    const card = flagValue(words, '--card');
    return card ? dialogs.find((d) => d.kind === 'green' && d.volumeId === card) : undefined;
  }
  const kind: DialogKind | null = key === 'card_clear' ? 'clear' : key === 'ingest' ? 'import' : null;
  if (!kind) return undefined;
  const mounts = taskMounts(words);
  return dialogs.find((d) => d.kind === kind && d.mount && mounts[0] === d.mount);
}

/**
 * Whether a finished task earns a popup: it ended, it ran long enough, and it is not a dry run whose dialog is open
 * and visible (that dialog already shows the answer). A minimized dialog's dry run still earns one.
 */
export function shouldNotify(t: Pick<TaskRec, 'final' | 'startMs' | 'argv'>, now: number, o: { dialogVisibleFor: boolean }): boolean {
  if (!t.final || t.startMs === null) return false;
  if (now - t.startMs < LONG_MS) return false;
  return !(isDry(t) && o.dialogVisibleFor);
}

export function notificationText(t: TaskRec, drives: Drive[]): DoneText {
  const outcome: Outcome = t.state === 'failed' ? 'failed' : t.state === 'paused' ? 'paused' : 'done';
  const title = taskTitle(t.argv, drives);
  const s = summarize(t);
  return { title: `${outcome === 'failed' ? 'Failed: ' : outcome === 'paused' ? 'Paused: ' : 'Done: '}${title}`, outcome, body: s.text };
}

/** A failure stays until dismissed (null); anything else for POPUP_MS. */
export const popupTtl = (o: Outcome): number | null => (o === 'failed' ? null : POPUP_MS);

export interface DialogStatus {
  line: string;
  value: number | null;
}

export const DIALOG_TITLE: Record<DialogKind, string> = { import: 'Import', clear: 'Clear card', green: 'Make green' };

/** The dock lists minimized dialogs in the order they were opened. */
export function dockOrder(dialogs: DialogRef[]): DialogRef[] {
  return dialogs.filter((d) => d.minimized).sort((a, b) => a.openedAt - b.openedAt);
}

export const dockLine = (kind: DialogKind, s: DialogStatus | undefined): string => `${DIALOG_TITLE[kind]} · ${s?.line || 'working…'}`;
