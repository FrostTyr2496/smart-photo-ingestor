import { useEffect, useRef, useState, type ReactNode } from 'react';
import { driveName } from '../lib/labels';
import { useStore } from '../lib/store';
import type { Drive } from '../lib/types';
import { Icon } from './bits';

// A rename asked for from elsewhere (the sidebar's hover "Rename", rv-40): the page's title starts editing when it
// mounts, or at once if it is already showing that drive.
let pending: string | null = null;
const EVENT = 'pv:rename';

/** Open `href` (the drive's or card's page) with its name ready to edit. */
export function renameFromAnywhere(volumeId: string, href: string): void {
  pending = volumeId;
  window.dispatchEvent(new CustomEvent(EVENT, { detail: volumeId }));
  if (window.location.hash !== href) window.location.hash = href;
}

/** The label to save: a card keeps its `card:` prefix (hidden in the UI), so card labels stay alike in the CLI. */
export function labelToSave(current: string | null | undefined, typed: string): string {
  const name = typed.trim();
  return /^card:\s*/i.test(current || '') ? `card:${name}` : name;
}

/**
 * A drive's or card's name as a page title, with a visible Rename button (rv-40; was the drive page's DriveTitle).
 * Renaming changes only the name PhotoVault shows: the volume on disk, its files and Lightroom's paths stay as they are.
 * `children` sit after the name (role pill, status pills).
 */
export function RenameTitle({ drive, fallback, children }: { drive: Drive | null | undefined; fallback?: string; children?: ReactNode }) {
  const { renameDrive } = useStore();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const name = drive ? driveName(drive) : fallback || 'Unnamed';
  const canRename = Boolean(drive?.volume_id);
  const id = drive?.volume_id ?? null;

  const start = () => {
    if (!canRename) return;
    setText(name);
    setEditing(true);
  };

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);
  useEffect(() => setEditing(false), [id]);
  useEffect(() => {
    if (!id) return;
    if (pending === id) {
      pending = null;
      start();
    }
    const on = (e: Event) => {
      if ((e as CustomEvent<string>).detail === id) {
        pending = null;
        start();
      }
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const save = async () => {
    const next = text.trim();
    if (!drive || !next || next === name) {
      setEditing(false);
      return;
    }
    setSaving(true);
    const ok = await renameDrive(drive, labelToSave(drive.label, next));
    setSaving(false);
    if (ok) setEditing(false);
  };

  return (
    <div>
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        {editing ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <input
              ref={inputRef}
              value={text}
              maxLength={64}
              disabled={saving}
              aria-label="Name"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setEditing(false);
              }}
              className="h-9 min-w-0 max-w-md flex-1 text-lg font-semibold"
            />
            <button type="submit" className="btn-primary h-8" disabled={saving || !text.trim()}>
              <Icon name="check" size={12} /> {saving ? 'Saving…' : 'Save'}
            </button>
            <button type="button" className="btn-ghost h-8" disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </form>
        ) : (
          <>
            <h1
              className={`min-w-0 truncate text-2xl font-semibold tracking-tight ${canRename ? 'cursor-text' : ''}`}
              onDoubleClick={start}
              title={canRename ? 'Double-click to rename' : undefined}
            >
              {name}
            </h1>
            {canRename && (
              <button
                type="button"
                className="btn h-7 shrink-0 gap-1 px-2 text-xs"
                onClick={start}
                aria-label={`Rename ${name}`}
                title="Rename in PhotoVault (the drive itself is not changed)"
              >
                <Icon name="edit" size={12} /> Rename
              </button>
            )}
          </>
        )}
        {!editing && children}
      </div>
      {editing && (
        <p className="mt-1 text-xs text-faint">
          Changes the name PhotoVault shows. The drive itself, its files and Lightroom's paths are not touched. Enter saves,
          Esc cancels.
        </p>
      )}
    </div>
  );
}
