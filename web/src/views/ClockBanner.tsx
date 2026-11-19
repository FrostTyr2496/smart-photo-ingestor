import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { plural } from '../lib/format';
import { usePref } from '../lib/prefs';
import { useStore } from '../lib/store';

// sp-7 (D-070): a camera whose clock or zone looks off against the phone timeline.
export interface ClockSuggestion {
  body_id: number;
  body: string;
  first_date: string;
  last_date: string;
  frames: number;
  current_shifts: number[];
  delta_s: number;
  matches: number;
  sessions_matched?: number;
  sessions_total?: number;
  runner_up: number;
  groups: { time_shift_s: number; group_ids: number[] }[];
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const suggestionKey = (s: ClockSuggestion): string => `${s.body_id}|${s.first_date}|${s.last_date}|${s.delta_s}`;

function dayMonth(iso: string): string {
  return `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1] ?? ''}`;
}

export function rangeText(s: ClockSuggestion): string {
  if (s.first_date === s.last_date) return dayMonth(s.first_date);
  const same = s.first_date.slice(0, 7) === s.last_date.slice(0, 7);
  return same
    ? `${Number(s.first_date.slice(8, 10))}–${dayMonth(s.last_date)}`
    : `${dayMonth(s.first_date)} – ${dayMonth(s.last_date)}`;
}

export function deltaText(delta_s: number): string {
  const h = Math.abs(delta_s) / 3600;
  const n = Number.isInteger(h) ? String(h) : h.toFixed(2).replace(/0$/, '');
  // A negative shift means the camera reads late.
  return `${n} h ${delta_s < 0 ? 'ahead' : 'behind'}`;
}

export function ClockBanner({ onApplied }: { onApplied: () => void }) {
  const { toast } = useStore();
  const [items, setItems] = useState<ClockSuggestion[]>([]);
  const [dismissed, setDismissed] = usePref<string[]>('pv:clockDismissed', []);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api<{ suggestions: ClockSuggestion[] }>('GET', '/api/clock-suggestions')
      .then((d) => setItems(d.suggestions))
      .catch(() => setItems([]));
  useEffect(() => {
    void load();
  }, []);

  const shown = items.filter((s) => !dismissed.includes(suggestionKey(s)));
  if (!shown.length) return null;

  const apply = async (s: ClockSuggestion) => {
    const ok = window.confirm(
      `Shift ${plural(s.frames, 'photo')} from ${s.body}, ${rangeText(s)}, by ${deltaText(s.delta_s)}? ` +
        'This changes the clock correction only; the files are not touched.',
    );
    if (!ok) return;
    setBusy(true);
    try {
      for (const g of s.groups) {
        await api('POST', '/api/groups/meta', { group_ids: g.group_ids, fields: { time_shift_s: g.time_shift_s } });
      }
      toast(`Shifted ${plural(s.frames, 'photo')}`, 'ok');
      onApplied();
      await load();
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {shown.map((s) => (
        <div key={suggestionKey(s)} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2 text-sm">
          <span>
            {s.body} · {rangeText(s)} looks {deltaText(s.delta_s).replace(/ (ahead|behind)$/, '')} off ({plural(s.sessions_matched ?? s.matches, 'separate outing')} line up with the phone's track after the shift).
          </span>
          <span className="flex items-center gap-2">
            <button type="button" disabled={busy} onClick={() => void apply(s)}>
              Shift {plural(s.frames, 'photo')}
            </button>
            <button type="button" onClick={() => setDismissed((d) => [...d, suggestionKey(s)])}>
              Dismiss
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}
