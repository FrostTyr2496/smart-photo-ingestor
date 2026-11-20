import { useEffect, useState, type ChangeEvent } from 'react';
import { Field } from '../components/Modal';
import { api } from '../lib/api';
import { NOTIFY_ASKED_PREF, NOTIFY_PREF } from '../lib/notify';
import { usePref, writePref } from '../lib/prefs';
import { useStore } from '../lib/store';
import { RefsDialog } from './RefsDialog';

interface Settings {
  creator: string;
  copyright: string;
  default_offset: string;
  match_window_s: string;
  match_reach_s: string;
  mac_folder: string;
}

const KEYS: Array<keyof Settings> = ['creator', 'copyright', 'default_offset', 'match_window_s', 'match_reach_s', 'mac_folder'];

interface ReviewThumbs {
  enabled: boolean;
  folder: string;
  frames: number;
  count: number;
  bytes_each: number;
  error: string | null;
}

export function estimateThumbBytes(frames: number, bytesEach: number): number {
  return frames * bytesEach;
}

function formatMb(bytes: number): string {
  const mb = bytes / 1e6;
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

/** Defaults for photo metadata and the Mac photo folder (rv-5). */
export function Settings() {
  const { toast } = useStore();
  const [saved, setSaved] = useState<Settings | null>(null);
  const [form, setForm] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [refsOpen, setRefsOpen] = useState(false);
  const [rt, setRt] = useState<ReviewThumbs | null>(null);
  const [rtFolder, setRtFolder] = useState('');
  const [notify, setNotify] = usePref(NOTIFY_PREF, true);
  const perm = typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
  const onNotify = (on: boolean) => {
    setNotify(on);
    if (on && perm === 'default') {
      writePref(NOTIFY_ASKED_PREF, true);
      void Notification.requestPermission();
    }
  };

  useEffect(() => {
    let alive = true;
    api<{ settings: Settings & { review_thumbs?: ReviewThumbs } }>('GET', '/api/settings')
      .then((d) => {
        if (!alive) return;
        const s = pick(d.settings);
        setSaved(s);
        setForm(s);
        if (d.settings.review_thumbs) {
          setRt(d.settings.review_thumbs);
          setRtFolder(d.settings.review_thumbs.folder);
        }
      })
      .catch((err: Error) => alive && toast(err.message, 'err'));
    return () => {
      alive = false;
    };
  }, [toast]);

  const set = (k: keyof Settings) => (e: ChangeEvent<HTMLInputElement>) => setForm((f) => (f ? { ...f, [k]: e.target.value } : f));
  const changed = Boolean(form && saved && KEYS.some((k) => form[k] !== saved[k]));

  const save = async () => {
    if (!form) return;
    setSaving(true);
    try {
      const d = await api<{ settings: Settings }>('POST', '/api/settings', form);
      const s = pick(d.settings);
      setSaved(s);
      setForm(s);
      toast('Settings saved.');
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setSaving(false);
    }
  };

  const saveRt = async (patch: { enabled?: boolean; folder?: string }) => {
    try {
      const d = await api<{ settings: { review_thumbs: ReviewThumbs } }>('POST', '/api/settings', { review_thumbs: patch });
      setRt(d.settings.review_thumbs);
      setRtFolder(d.settings.review_thumbs.folder);
      toast('Review thumbs saved.');
    } catch (err) {
      toast((err as Error).message, 'err');
    }
  };
  const revealRt = async () => {
    try {
      await api('POST', '/api/settings/review-thumbs/reveal', {});
    } catch (err) {
      toast((err as Error).message, 'err');
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted">Defaults for photo metadata and the photo folder on this Mac.</p>
      </div>

      {!form ? (
        <div className="card h-64 animate-pulse" />
      ) : (
        <section className="card flex flex-col gap-4 p-5">
          <Field label="Creator">
            <input value={form.creator} onChange={set('creator')} className="h-9" />
          </Field>
          <Field label="Copyright">
            <input value={form.copyright} onChange={set('copyright')} className="h-9" />
          </Field>
          <Field label="Default time zone for photos without one">
            <input value={form.default_offset} onChange={set('default_offset')} placeholder="+01:00" className="h-9" />
          </Field>
          <Field label="Match GPS points within (seconds)">
            <input type="number" min={0} max={86400} value={form.match_window_s} onChange={set('match_window_s')} className="h-9" />
          </Field>
          <Field label="Reach for farther suggestions (seconds)">
            <input type="number" min={0} max={86400} value={form.match_reach_s} onChange={set('match_reach_s')} className="h-9" />
          </Field>
          <div className="flex flex-col gap-1.5">
            <Field label="Photo folder on this Mac (used as a working drive)">
              <input value={form.mac_folder} onChange={set('mac_folder')} placeholder="~/Sandbox" className="h-9" />
            </Field>
            <p className="text-xs text-faint">Only this folder is searched for photos on the Mac. It must be inside your home folder.</p>
          </div>
          <div className="flex justify-end">
            <button type="button" className="btn-primary" disabled={saving || !changed} onClick={save}>
              {saving ? 'Saving…' : 'Save settings'}
            </button>
          </div>
        </section>
      )}

      <section className="card flex flex-col gap-2 p-5">
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" role="switch" className="mt-1" checked={notify} onChange={(e) => onNotify(e.target.checked)} />
          <span>
            <span className="font-medium">Notify when long tasks finish</span>
            <span className="mt-0.5 block text-xs text-muted">
              A macOS notification when this tab is in the background. A popup always shows in the app. Stored in this browser only.
              {notify && perm === 'denied' ? ' The browser has blocked notifications for this page: allow them in its site settings.' : ''}
              {perm === 'unsupported' ? ' This browser has no notifications.' : ''}
            </span>
          </span>
        </label>
      </section>

      {rt && (
        <section className="card flex flex-col gap-3 p-5" aria-label="Review thumbs">
          <div>
            <h2 className="text-base font-semibold">Review thumbs</h2>
            <p className="mt-1 text-sm text-muted">
              Small previews (384 px) made while a drive is hashed, so the shoot review can show photos without any drive plugged in.
              They are kept in a folder on this Mac, never on a drive.
            </p>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" role="switch" className="mt-1" checked={rt.enabled} onChange={(e) => void saveRt({ enabled: e.target.checked })} />
            <span>
              <span className="font-medium">Make review thumbs during hash</span>
              <span className="mt-0.5 block text-xs text-muted">
                About {formatMb(estimateThumbBytes(rt.frames, rt.bytes_each))} for {rt.frames.toLocaleString()} frames (about {Math.round(rt.bytes_each / 1000)} KB each). Off by default.
              </span>
            </span>
          </label>
          <Field label="Folder on this Mac">
            <input value={rtFolder} onChange={(e) => setRtFolder(e.target.value)} placeholder="~/Sandbox/PhotoVault Thumbs" className="h-9" />
          </Field>
          {rt.error && <p className="text-xs text-red-500">{rt.error}</p>}
          <p className="text-xs text-faint">{rt.count.toLocaleString()} thumbs in this folder now.</p>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => void revealRt()}>
              Show in Finder
            </button>
            <button type="button" className="btn-primary" disabled={rtFolder.trim() === rt.folder} onClick={() => void saveRt({ folder: rtFolder.trim() })}>
              Save folder
            </button>
          </div>
        </section>
      )}

      <section className="card flex flex-col gap-3 p-5">
        <div>
          <h2 className="text-base font-semibold">GPS reference folders</h2>
          <p className="mt-1 text-sm text-muted">
            Read a folder of photos that have GPS (for example a phone export) so photos without a location can be matched by time. The folder is read, never changed.
          </p>
        </div>
        <div className="flex justify-start">
          <button type="button" className="btn" onClick={() => setRefsOpen(true)}>
            Add GPS reference folder…
          </button>
        </div>
      </section>

      <RefsDialog open={refsOpen} onClose={() => setRefsOpen(false)} />
    </div>
  );
}

function pick(s: Partial<Settings> | undefined): Settings {
  return {
    creator: s?.creator ?? '',
    copyright: s?.copyright ?? '',
    default_offset: s?.default_offset ?? '',
    match_window_s: s?.match_window_s ?? '',
    match_reach_s: s?.match_reach_s ?? '',
    mac_folder: s?.mac_folder ?? '',
  };
}
