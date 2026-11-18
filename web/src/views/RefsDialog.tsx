import { useEffect, useState } from 'react';
import { Field, Modal } from '../components/Modal';
import { useStore } from '../lib/store';

/** Add a folder of GPS photos as a reference for time matching (rv-5). The folder is read, never changed. */
export function RefsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { startTask, toast } = useStore();
  const [folder, setFolder] = useState('');
  const [label, setLabel] = useState('');
  useEffect(() => {
    if (open) {
      setFolder('');
      setLabel('');
    }
  }, [open]);

  const scan = async () => {
    const f = folder.trim();
    const l = label.trim();
    if (!f || !l) {
      toast('Give a folder path and a source name.', 'err');
      return;
    }
    onClose();
    await startTask('meta_refs', { folder: f, label: l });
  };

  return (
    <Modal
      open={open}
      title="Add GPS reference folder"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={scan}>
            Scan folder
          </button>
        </>
      }
    >
      <Field label="Folder path">
        <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/Users/you/Phone export" className="h-9" />
      </Field>
      <p className="-mt-2 text-xs text-faint">Use the full path. The folder is read, never changed.</p>
      <Field label="Source name">
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Phone" className="h-9" />
      </Field>
    </Modal>
  );
}
