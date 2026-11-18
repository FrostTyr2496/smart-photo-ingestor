import { useEffect, useState } from 'react';
import { Field, Modal } from '../components/Modal';
import { driveName, mountName, roleKey } from '../lib/labels';
import { useStore } from '../lib/store';

/** Import a card onto a working drive (ing-*). The card is only read; a dry run shows the plan first. */
export function ImportDialog({ card, onClose }: { card: { mount: string; label: string } | null; onClose: () => void }) {
  const { drives, startTask, ask } = useStore();
  const targets = drives.filter((t) => t.mounted && roleKey(t.role) === 'working');
  const [target, setTarget] = useState('');
  const [name, setName] = useState('');
  useEffect(() => {
    if (card) {
      setTarget(targets[0]?.mount ?? '');
      setName('');
    }
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = async (dryRun: boolean) => {
    if (!card || !target) return;
    const shoot = name.trim();
    if (!dryRun) {
      const ok = await ask({
        title: 'Start the import?',
        lines: [
          `Copy the card "${card.label}" onto "${mountName(target, drives)}".`,
          shoot ? `Shoot name: ${shoot}.` : 'Shoot name: chosen from the photo dates.',
          'Each file is copied, checked by checksum, and the card is left as it is. A dry run shows the plan first.',
        ],
        action: 'Import',
      });
      if (!ok) return;
    }
    const args: Record<string, unknown> = { source: card.mount, target, dry_run: dryRun };
    if (shoot) args.name = shoot;
    onClose();
    await startTask('ingest', args);
  };

  const ok = targets.length > 0;
  return (
    <Modal
      open={Boolean(card)}
      title="Import from card"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn" disabled={!ok} onClick={() => go(true)}>
            Dry run
          </button>
          <button type="button" className="btn-primary" disabled={!ok} onClick={() => go(false)}>
            Import
          </button>
        </>
      }
    >
      <p className="text-sm text-muted">Card: {card?.label}</p>
      <Field label="Copy onto">
        <select value={target} onChange={(e) => setTarget(e.target.value)} className="h-9" disabled={!ok}>
          {targets.map((t) => (
            <option key={t.mount} value={t.mount}>
              {driveName(t)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Shoot name (optional)">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Chosen from the photo dates if empty" className="h-9" />
      </Field>
      <p className={`text-xs ${ok ? 'text-faint' : 'text-maybe'}`}>
        {ok ? 'Each file is copied and checked by checksum. The card is never changed.' : 'No working drive is connected. Set one up first.'}
      </p>
    </Modal>
  );
}
