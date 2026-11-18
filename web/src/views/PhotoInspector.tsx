import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Icon } from '../components/bits';
import { MapPicker, type MapPoint } from '../components/MapPicker';
import { api } from '../lib/api';
import { fmtInt, plural } from '../lib/format';
import {
  addKeywords,
  buildFields,
  commonValue,
  fmtCoord,
  fmtDelta,
  GPS_LABEL,
  hasEdits,
  keywordChips,
  nearestWithLocation,
  NO_EDITS,
  relatedSession,
  relatedWindow,
  removeKeyword,
  secondsBetween,
  shiftedTime,
  shiftSeconds,
  splitShift,
  type Pending,
  type PendingLoc,
  type PhotoGroup,
  type PhotoSettings,
} from '../lib/photos';
import { usePref } from '../lib/prefs';
import { useStore } from '../lib/store';

interface Suggestion {
  lat: number;
  lon: number;
  alt: number | null;
  source: string;
  delta_s: number;
  confidence: string;
}

interface Place {
  lat: string;
  lon: string;
  name?: string;
  display_name?: string;
}

const NOMINATIM = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=';
const SEARCH_GAP_MS = 1100; // Nominatim's policy: at most one request a second

/**
 * The editor beside the grid (rv-7). Edits are pending until Save, which applies them to every selected photo.
 * Untouched fields are left alone on each photo, so a mixed selection keeps its differences.
 */
export function PhotoInspector({
  selected,
  pool,
  settings,
  windowS,
  onClear,
  onSaved,
  onRelated,
  savedEpoch,
}: {
  selected: PhotoGroup[];
  pool: PhotoGroup[];
  settings: PhotoSettings;
  windowS: number;
  onClear: () => void;
  onSaved: (fields: Record<string, unknown>, ids: number[]) => Promise<void>;
  onRelated: (ids: number[]) => void;
  savedEpoch: number;
}) {
  const { toast } = useStore();
  const [pend, setPend] = useState<Pending>(NO_EDITS);
  const [saving, setSaving] = useState(false);
  const first = selected[0] ?? null;
  const n = selected.length;
  const selKey = selected.map((g) => g.group_id).join(',');
  const edited = hasEdits(pend);

  // Related photos (rv-9): the eligible ones are all ticked unless excluded; Save also goes to the ticked ones.
  const [gap, setGap] = usePref<number | 'day'>('photos.relatedGap', 3600);
  const [onlyNoLoc, setOnlyNoLoc] = useState(true);
  const [excluded, setExcluded] = useState<Set<number>>(() => new Set());
  const rel = useRelated(selected, selKey, gap, onlyNoLoc, savedEpoch);
  useEffect(() => setExcluded(new Set()), [selKey]);
  const relIds = useMemo(() => rel.eligible.filter((g) => !excluded.has(g.group_id)).map((g) => g.group_id), [rel.eligible, excluded]);
  const relKey = relIds.join(',');
  useEffect(() => {
    onRelated(relIds);
  }, [relKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onRelated([]), []); // eslint-disable-line react-hooks/exhaustive-deps
  const total = n + relIds.length;

  // The map refits when the selection changes; a pin from a search or suggestion flies there too.
  const [focus, setFocus] = useState(0);
  useEffect(() => setFocus((f) => f + 1), [selKey]);

  const set = <K extends keyof Pending>(k: K, v: Pending[K]) => setPend((p) => ({ ...p, [k]: v }));
  const setLoc = (loc: PendingLoc, fly = false) => {
    setPend((p) => ({ ...p, loc }));
    if (fly) setFocus((f) => f + 1);
  };

  const save = async () => {
    const fields = buildFields(pend);
    if (!Object.keys(fields).length) return;
    setSaving(true);
    try {
      const ids = [...selected.map((g) => g.group_id), ...relIds];
      await api('POST', '/api/groups/meta', { group_ids: ids, fields });
      await onSaved(fields, ids);
      setPend(NO_EDITS);
      toast(`Saved to ${plural(ids.length, 'photo')}.`);
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setSaving(false);
    }
  };

  const located = selected.filter((g) => g.lat !== null && g.lon !== null);
  const points = useMemo<MapPoint[]>(() => located.map((g) => ({ lat: g.lat!, lon: g.lon! })), [selKey, located.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const pin = pend.loc && pend.loc.lat !== null && pend.loc.lon !== null ? { lat: pend.loc.lat, lon: pend.loc.lon } : null;

  return (
    <aside className="card flex h-full flex-col overflow-hidden bg-surface/95 shadow-2xl xl:sticky xl:top-[72px] xl:h-auto xl:max-h-[calc(100vh-88px)] xl:bg-surface/80 xl:shadow-none">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="truncate font-medium">{n === 1 ? first?.canonical_stem || `Photo ${first?.group_id}` : `${fmtInt(n)} photos selected`}</p>
          <p className="truncate text-xs text-muted">
            {n === 1 && first ? [first.body_nickname, first.capture_time?.replace('T', ' ')].filter(Boolean).join(' · ') : 'Changes apply to every selected photo.'}
          </p>
        </div>
        <button type="button" className="btn-ghost h-7 px-2" onClick={onClear} aria-label="Clear selection">
          <Icon name="x" />
        </button>
      </div>

      <div className="flex flex-col gap-5 overflow-y-auto px-4 py-4">
        {n === 1 && first && <Preview group={first} />}

        <Section title="Location">
          <p className="text-xs text-muted">
            {pend.loc
              ? pend.loc.lat === null
                ? 'Will remove the location.'
                : `New: ${fmtCoord(pend.loc.lat, pend.loc.lon!)} · ${GPS_LABEL[pend.loc.source ?? ''] ?? ''}`
              : n === 1 && first
                ? first.lat !== null && first.lon !== null
                  ? `${fmtCoord(first.lat, first.lon)} · ${GPS_LABEL[first.gps_source ?? 'camera'] ?? first.gps_source}`
                  : 'No location yet. Click the map, search a place or use a suggestion.'
                : `${fmtInt(located.length)} of ${fmtInt(n)} have a location.`}
          </p>
          <MapPicker points={points} pin={pin} focus={focus} onPick={(p) => setLoc({ lat: p.lat, lon: p.lon, alt: null, source: 'map' })} />
          {first && <Suggestions key={first.group_id} first={first} pool={pool} selected={selected} windowS={windowS} onUse={(l) => setLoc(l, true)} />}
          <PlaceSearch
            onPick={(lat, lon, name) => {
              setLoc({ lat, lon, alt: null, source: 'search' }, true);
              set('place', name);
            }}
          />
          <TextField
            label="Place name"
            shared={commonValue(selected, 'place_name')}
            value={pend.place}
            onChange={(v) => set('place', v)}
            placeholder="e.g. a beach or a town"
          />
          {(located.length > 0 || pend.loc) && (
            <div className="flex gap-2">
              {pend.loc && (
                <button type="button" className="btn-ghost h-7 px-2 text-xs" onClick={() => set('loc', null)}>
                  Undo location change
                </button>
              )}
              {located.length > 0 && !(pend.loc && pend.loc.lat === null) && (
                <button type="button" className="btn-ghost h-7 px-2 text-xs" onClick={() => setLoc({ lat: null, lon: null, alt: null, source: null })}>
                  Remove location
                </button>
              )}
            </div>
          )}
        </Section>

        <Section title="Credit">
          <TextField label="Creator" shared={commonValue(selected, 'creator')} value={pend.creator} onChange={(v) => set('creator', v)} fallback={settings.creator} />
          <TextField label="Copyright" shared={commonValue(selected, 'copyright')} value={pend.copyright} onChange={(v) => set('copyright', v)} fallback={settings.copyright} />
          <TextField label="Title" shared={commonValue(selected, 'title')} value={pend.title} onChange={(v) => set('title', v)} />
        </Section>

        <Section title="Keywords">
          <Keywords selected={selected} pend={pend} setPend={setPend} />
        </Section>

        <Section title="Related photos">
          <Related
            selected={selected}
            gap={gap}
            setGap={setGap}
            onlyNoLoc={onlyNoLoc}
            setOnlyNoLoc={setOnlyNoLoc}
            rel={rel}
            excluded={excluded}
            setExcluded={setExcluded}
          />
        </Section>

        <Section title="Clock">
          <ClockShift first={first} n={n} pend={pend} set={(v) => set('shift', v)} defaultOffset={settings.default_offset} />
        </Section>
      </div>

      <AnimatePresence initial={false}>
        {edited && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="border-t border-line bg-raised/60"
          >
            <div className="flex items-center justify-between gap-2 px-4 py-3">
              <button type="button" className="btn-ghost" onClick={() => setPend(NO_EDITS)} disabled={saving}>
                Discard
              </button>
              <button type="button" className="btn-primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : relIds.length ? `Save to ${fmtInt(n)} + ${fmtInt(relIds.length)} related` : total === 1 ? 'Save' : `Save to ${fmtInt(total)} photos`}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <p className="border-t border-line px-4 py-2 text-2xs text-faint">
        Saved to the catalog. “Write sidecars” on a working drive puts the edits next to the files; legacy drives are never written to.
      </p>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="label">{title}</h3>
      {children}
    </section>
  );
}

function Preview({ group }: { group: PhotoGroup }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex aspect-[3/2] items-center justify-center overflow-hidden rounded-lg bg-ink/5">
        {failed ? (
          <span className="flex flex-col items-center gap-1 text-xs text-faint">
            <Icon name="image" size={20} />
            No preview (drive not connected?)
          </span>
        ) : (
          <img src={`/api/groups/${group.group_id}/thumb`} alt="" className="h-full w-full object-contain" onError={() => setFailed(true)} />
        )}
      </div>
      {group.rel_path && <p className="truncate font-mono text-2xs text-faint" title={group.rel_path}>{group.rel_path}</p>}
    </div>
  );
}

/** A text field that shows the shared value, "Mixed" when photos differ, and offers the default from Settings. */
function TextField({
  label,
  shared,
  value,
  onChange,
  fallback,
  placeholder,
}: {
  label: string;
  shared: string | null;
  value: string | null;
  onChange: (v: string | null) => void;
  fallback?: string;
  placeholder?: string;
}) {
  const shown = value ?? shared ?? '';
  const offerDefault = fallback && value === null && shared === '' ? fallback : '';
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center justify-between text-xs text-muted">
        {label}
        {value !== null && value !== (shared ?? '') && <span className="text-accent">changed</span>}
      </span>
      <input
        value={shown}
        placeholder={shared === null && value === null ? 'Mixed. Type to set for all.' : placeholder}
        onChange={(e) => onChange(e.target.value === (shared ?? '') ? null : e.target.value)}
        className="h-9"
      />
      {offerDefault && (
        <button type="button" className="self-start text-xs text-accent hover:underline" onClick={() => onChange(offerDefault)}>
          Use default: {offerDefault}
        </button>
      )}
    </label>
  );
}

function Suggestions({ first, pool, selected, windowS, onUse }: { first: PhotoGroup; pool: PhotoGroup[]; selected: PhotoGroup[]; windowS: number; onUse: (l: PendingLoc) => void }) {
  const [list, setList] = useState<Suggestion[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let alive = true;
    api<{ suggestions: Suggestion[] }>('GET', `/api/groups/${first.group_id}/suggestions`)
      .then((d) => alive && setList(d.suggestions))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [first.group_id]);
  const ids = useMemo(() => new Set(selected.map((g) => g.group_id)), [selected]);
  const near = nearestWithLocation(first, pool, ids, windowS);

  const rows: Array<{ key: string; text: string; sub: string; loc: PendingLoc }> = [];
  for (const [i, s] of (list ?? []).entries()) {
    rows.push({
      key: `s${i}`,
      text: `${GPS_LABEL[s.source] ?? s.source} · ${s.delta_s === 0 ? 'between two fixes' : fmtDelta(s.delta_s)}`,
      sub: `${fmtCoord(s.lat, s.lon)} · ${s.confidence} confidence`,
      loc: { lat: s.lat, lon: s.lon, alt: s.alt ?? null, source: 'match' },
    });
  }
  if (near) {
    rows.push({
      key: 'near',
      text: `From ${near.group.canonical_stem ?? 'a nearby photo'} · ${fmtDelta(near.delta)}`,
      sub: fmtCoord(near.group.lat!, near.group.lon!),
      loc: { lat: near.group.lat, lon: near.group.lon, alt: near.group.alt, source: 'neighbour' },
    });
  }
  if (list === null && !err) return <p className="text-xs text-faint">Looking for GPS matches…</p>;
  if (!rows.length) return <p className="text-xs text-faint">{err || 'No GPS points or located photos near this time. Add a GPS reference folder in Settings to match by time.'}</p>;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-muted">{selected.length > 1 ? 'Suggested from the first photo:' : 'Suggested:'}</p>
      {rows.map((r) => (
        <div key={r.key} className="flex items-center justify-between gap-2 rounded-lg border border-line px-2.5 py-1.5">
          <div className="min-w-0">
            <p className="truncate text-xs">{r.text}</p>
            <p className="truncate font-mono text-2xs text-faint">{r.sub}</p>
          </div>
          <button type="button" className="btn h-7 px-2 text-xs" onClick={() => onUse(r.loc)}>
            Use
          </button>
        </div>
      ))}
    </div>
  );
}

/** Place search runs only on Enter (or the button), at most once a second, and sends only the typed text. */
function PlaceSearch({ onPick }: { onPick: (lat: number, lon: number, name: string) => void }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Place[] | null>(null);
  const [msg, setMsg] = useState('');
  const last = useRef(0);

  const run = async () => {
    const text = q.trim();
    if (!text) return;
    const wait = SEARCH_GAP_MS - (Date.now() - last.current);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    last.current = Date.now();
    setMsg('Searching…');
    setHits(null);
    try {
      const res = await fetch(NOMINATIM + encodeURIComponent(text), { headers: { Accept: 'application/json' }, referrerPolicy: 'no-referrer' });
      if (!res.ok) throw new Error(`search failed (${res.status})`);
      const found = (await res.json()) as Place[];
      setHits(found);
      setMsg(found.length ? '' : 'No matches. You can still type a place name below.');
    } catch (err) {
      setMsg(`Search unavailable (${(err as Error).message}). Click the map or type the place name.`);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      void run();
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-1.5">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Search a place, press Enter" className="h-9 min-w-0 flex-1" />
        <button type="button" className="btn h-9" onClick={() => void run()} disabled={!q.trim()} aria-label="Search place">
          <Icon name="search" />
        </button>
      </div>
      {msg && <p className="text-xs text-faint">{msg}</p>}
      {hits && hits.length > 0 && (
        <ul className="flex flex-col overflow-hidden rounded-lg border border-line">
          {hits.map((h, i) => (
            <li key={i}>
              <button
                type="button"
                className="w-full truncate px-2.5 py-1.5 text-left text-xs hover:bg-ink/5"
                title={h.display_name}
                onClick={() => {
                  onPick(Number(h.lat), Number(h.lon), h.name || h.display_name || '');
                  setHits(null);
                }}
              >
                {h.display_name || h.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Keywords({ selected, pend, setPend }: { selected: PhotoGroup[]; pend: Pending; setPend: (f: (p: Pending) => Pending) => void }) {
  const [text, setText] = useState('');
  const chips = keywordChips(selected, pend.kwAdd, pend.kwRemove);
  const commit = () => {
    if (!text.trim()) return;
    setPend((p) => addKeywords(p, text, chips.map((c) => c.word)));
    setText('');
  };
  return (
    <div className="flex flex-col gap-2">
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c.word.toLowerCase()}
              className={`chip ${c.pending === 'add' ? 'border-accent/40 bg-accent/10 text-accent' : c.on < selected.length ? 'border-dashed border-line-strong text-muted' : 'border-line-strong bg-ink/5 text-ink'}`}
              title={c.on < selected.length ? `On ${c.on} of ${selected.length} photos` : undefined}
            >
              {c.word}
              {c.on < selected.length && <span className="text-faint">{c.on}</span>}
              <button type="button" className="ml-0.5 text-faint hover:text-err" aria-label={`Remove ${c.word}`} onClick={() => setPend((p) => removeKeyword(p, c.word))}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {pend.kwRemove.length > 0 && <p className="text-xs text-muted">Removing: {pend.kwRemove.join(', ')}</p>}
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
        placeholder="Add keywords, press Enter"
        className="h-9"
      />
    </div>
  );
}

function ClockShift({ first, n, pend, set, defaultOffset }: { first: PhotoGroup | null; n: number; pend: Pending; set: (v: number | null) => void; defaultOffset: string }) {
  const current = first?.time_shift_s ?? 0;
  const shift = pend.shift ?? current;
  const parts = splitShift(shift);
  const update = (k: 'sign' | 'h' | 'm' | 's', v: string) => {
    const p = { ...parts, [k]: k === 'sign' ? v : Number(v) };
    const sec = shiftSeconds(p.sign as '+' | '-', p.h, p.m, p.s);
    set(sec === current && n === 1 ? null : sec);
  };
  const num = (k: 'h' | 'm' | 's', max: number) => (
    <label className="flex items-center gap-1 text-xs text-muted">
      <input type="number" min={0} max={max} value={parts[k]} onChange={(e) => update(k, e.target.value)} className="h-9 w-14 text-right tabular" />
      {k}
    </label>
  );
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted">If the camera clock was wrong, shift the capture time. GPS matching uses the shifted time.</p>
      <div className="flex items-center gap-2">
        <select value={parts.sign} onChange={(e) => update('sign', e.target.value)} className="h-9 w-14" aria-label="Earlier or later">
          <option value="+">+</option>
          <option value="-">−</option>
        </select>
        {num('h', 999)}
        {num('m', 59)}
        {num('s', 59)}
      </div>
      {first && (
        <p className="text-xs text-faint">
          {n > 1 ? 'First photo: ' : ''}
          {shiftedTime(first, current, defaultOffset)}
          {pend.shift !== null && pend.shift !== current && <span className="text-accent"> → {shiftedTime(first, pend.shift, defaultOffset)}</span>}
        </p>
      )}
      {n > 1 && pend.shift === null && <p className="text-2xs text-faint">Each photo keeps its own shift unless you change it here.</p>}
    </div>
  );
}

const REL_LIMIT = 1000;
const REL_SHOWN = 200;
const GAPS: Array<{ label: string; value: number | 'day' }> = [
  { label: '15 min', value: 900 },
  { label: '1 h', value: 3600 },
  { label: '3 h', value: 10800 },
  { label: 'Same day', value: 'day' },
];

interface RelatedState {
  eligible: PhotoGroup[];
  session: number; // photos in the session before the location filter
  fetched: number;
  loading: boolean;
  error: string;
}

/** Fetch photos around the selection and pick the session; refetches on a new selection, a new gap or a save. */
function useRelated(selected: PhotoGroup[], selKey: string, gap: number | 'day', onlyNoLoc: boolean, savedEpoch: number): RelatedState {
  const [data, setData] = useState<{ key: string; rows: PhotoGroup[] } | null>(null);
  const [error, setError] = useState('');
  const sel = useRef(selected);
  sel.current = selected;
  const key = `${selKey}|${gap}`;
  useEffect(() => {
    const { after, before } = relatedWindow(sel.current, gap);
    if (!after) {
      setData({ key, rows: [] });
      return undefined;
    }
    let alive = true;
    setError('');
    api<{ groups: PhotoGroup[] }>('GET', `/api/groups?${new URLSearchParams({ after, before, limit: String(REL_LIMIT) })}`)
      .then((d) => alive && setData({ key, rows: d.groups }))
      .catch((e: Error) => {
        if (!alive) return;
        setError(e.message);
        setData({ key, rows: [] });
      });
    return () => {
      alive = false;
    };
  }, [key, savedEpoch]); // eslint-disable-line react-hooks/exhaustive-deps

  return useMemo(() => {
    if (!data || data.key !== key) return { eligible: [], session: 0, fetched: 0, loading: true, error: '' };
    const session = relatedSession(data.rows, selected, gap);
    return {
      eligible: onlyNoLoc ? session.filter((g) => g.lat === null) : session,
      session: session.length,
      fetched: data.rows.length,
      loading: false,
      error,
    };
  }, [data, key, selected, gap, onlyNoLoc, error]);
}

function Related({
  selected,
  gap,
  setGap,
  onlyNoLoc,
  setOnlyNoLoc,
  rel,
  excluded,
  setExcluded,
}: {
  selected: PhotoGroup[];
  gap: number | 'day';
  setGap: (g: number | 'day') => void;
  onlyNoLoc: boolean;
  setOnlyNoLoc: (v: boolean) => void;
  rel: RelatedState;
  excluded: Set<number>;
  setExcluded: (s: Set<number>) => void;
}) {
  const gapLabel = gap === 'day' ? 'the same day' : `within ${GAPS.find((x) => x.value === gap)?.label ?? ''} of each other`;
  const toggle = (id: number) => {
    const next = new Set(excluded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExcluded(next);
  };
  const shown = rel.eligible.slice(0, REL_SHOWN);
  const ticked = rel.eligible.filter((g) => !excluded.has(g.group_id)).length;
  const hasTime = selected.some((g) => g.capture_time);
  const delta = (g: PhotoGroup): string => {
    let best: number | null = null;
    for (const s of selected) {
      const d = secondsBetween(s.capture_time, g.capture_time);
      if (d !== null && (best === null || Math.abs(d) < Math.abs(best))) best = d;
    }
    return best === null ? '' : ` · ${fmtDelta(best)}`;
  };

  let summary: string;
  if (!hasTime) summary = 'No related photos: the selection has no capture time.';
  else if (rel.loading) summary = 'Looking for related photos…';
  else if (rel.error) summary = `Could not load related photos: ${rel.error}`;
  else if (rel.eligible.length) summary = `${plural(rel.eligible.length, 'related photo')} (taken ${gapLabel})`;
  else if (rel.session > 0) summary = `No related photos: ${plural(rel.session, 'photo')} nearby already ${rel.session === 1 ? 'has' : 'have'} a location.`;
  else summary = `No related photos taken ${gapLabel}.`;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="How close in time">
        {GAPS.map((o) => (
          <button
            key={o.label}
            type="button"
            aria-pressed={gap === o.value}
            className={`btn h-7 px-2 text-xs ${gap === o.value ? 'border-accent/50 bg-accent/10 text-accent' : ''}`}
            onClick={() => setGap(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={onlyNoLoc} onChange={(e) => setOnlyNoLoc(e.target.checked)} />
        Only without a location
      </label>
      <p className="text-xs text-muted">{summary}</p>
      {rel.fetched >= REL_LIMIT && <p className="text-xs text-faint">The list may be cut short: only the first {fmtInt(REL_LIMIT)} photos nearby were checked.</p>}
      {rel.eligible.length > 0 && (
        <>
          <div className="flex items-center justify-between text-xs text-muted">
            <span>{fmtInt(ticked)} ticked</span>
            <span className="flex gap-2">
              <button type="button" className="text-accent hover:underline" onClick={() => setExcluded(new Set())}>
                All
              </button>
              <button type="button" className="text-accent hover:underline" onClick={() => setExcluded(new Set(rel.eligible.map((g) => g.group_id)))}>
                None
              </button>
            </span>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(52px,1fr))] gap-1">
            {shown.map((g) => (
              <RelatedThumb key={g.group_id} g={g} on={!excluded.has(g.group_id)} title={`${g.canonical_stem || `Photo ${g.group_id}`} · ${g.capture_time?.replace('T', ' ') ?? ''}${delta(g)}`} onToggle={() => toggle(g.group_id)} />
            ))}
          </div>
          {rel.eligible.length > shown.length && (
            <p className="text-xs text-faint">+{fmtInt(rel.eligible.length - shown.length)} more (all ticked unless you chose None)</p>
          )}
        </>
      )}
    </div>
  );
}

function RelatedThumb({ g, on, title, onToggle }: { g: PhotoGroup; on: boolean; title: string; onToggle: () => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      aria-pressed={on}
      title={title}
      onClick={onToggle}
      className={`relative aspect-square overflow-hidden rounded-md bg-ink/5 outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${on ? 'ring-2 ring-accent' : 'opacity-60'}`}
    >
      {failed || !g.volume_id ? (
        <span className="flex h-full w-full items-center justify-center p-0.5 text-center font-mono text-[9px] leading-tight text-faint">{g.canonical_stem || g.group_id}</span>
      ) : (
        <img src={`/api/groups/${g.group_id}/thumb`} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} className="h-full w-full object-cover" />
      )}
      {on && (
        <span className="absolute left-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-on-accent shadow">
          <Icon name="check" size={10} />
        </span>
      )}
    </button>
  );
}
