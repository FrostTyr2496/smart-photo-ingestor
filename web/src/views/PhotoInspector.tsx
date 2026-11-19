import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../components/bits';
import { springs } from '../lib/motion';
import { MapPicker, type MapPoint } from '../components/MapPicker';
import { api } from '../lib/api';
import { fmtInt, plural } from '../lib/format';
import { driveName } from '../lib/labels';
import {
  addKeywords,
  autoStep,
  clockPhotos,
  commonValue,
  copyName,
  copyText,
  vaultCopyName,
  fmtCoord,
  fmtExposure,
  suggestDrives,
  selectionLine,
  volumeCounts,
  type GroupDetail,
  mapsUrl,
  fmtDelta,
  gapLabel,
  GPS_LABEL,
  fmtApart,
  fmtGapShort,
  hasEdits,
  nominatimPlace,
  placeEdits,
  placeLabel,
  type PlaceFields,
  keywordChips,
  LADDER,
  nearestWithLocation,
  NO_EDITS,
  planSaves,
  reachPhotos,
  relatedCounts,
  relatedSession,
  relatedWindowFor,
  removeKeyword,
  SCOPE_DEFAULTS,
  secondsBetween,
  shiftedTime,
  shiftSeconds,
  splitShift,
  type ClockReach,
  type Gap,
  type Pending,
  type PendingLoc,
  type PhotoGroup,
  type PhotoSettings,
  type Reach,
  type ScopeName,
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
  place_name?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  score?: number;
  via?: { stem: string; delta_s: number } | null;
  ends?: { before_s: number; after_s: number; km: number; stayed: boolean; before_place: string | null; after_place: string | null };
}

/** One row of GET /api/groups/<id>/places: a distinct phone place near the photo's time (sp-3). */
interface NearPlace extends PlaceFields {
  lat: number;
  lon: number;
  alt: number | null;
  delta_s: number;
}

interface Place {
  lat: string;
  lon: string;
  name?: string;
  display_name?: string;
  address?: Record<string, string>;
}

const NOMINATIM = 'https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=5&q=';
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
  onShowInAll,
  onOpenViewer,
}: {
  selected: PhotoGroup[];
  pool: PhotoGroup[];
  settings: PhotoSettings;
  windowS: number;
  onClear: () => void;
  onSaved: (fields: Record<string, unknown>, ids: number[]) => Promise<void>;
  onRelated: (ids: number[]) => void;
  savedEpoch: number;
  onShowInAll: () => void;
  onOpenViewer: (g: PhotoGroup) => void;
}) {
  const { toast, drives } = useStore();
  const [pend, setPend] = useState<Pending>(NO_EDITS);
  const [saving, setSaving] = useState(false);
  const first = selected[0] ?? null;
  const n = selected.length;
  const selKey = selected.map((g) => g.group_id).join(',');
  const edited = hasEdits(pend);

  // Related photos (rv-9, rv-16): each kind of edit has its own reach. The ticked list is the Place scope, and
  // unticking a photo leaves it out of every scope.
  const [placeReach, setPlaceReach] = usePref<Reach>('photos.scope.place', SCOPE_DEFAULTS.place);
  const [descReach, setDescReach] = usePref<Reach>('photos.scope.description', SCOPE_DEFAULTS.description);
  const [creditReach, setCreditReach] = usePref<Reach>('photos.scope.credit', SCOPE_DEFAULTS.credit);
  const [clockReach, setClockReach] = usePref<ClockReach>('photos.scope.clock', SCOPE_DEFAULTS.clock);
  const [onlyNoLoc, setOnlyNoLoc] = useState(true);
  const [excluded, setExcluded] = useState<Set<number>>(() => new Set());
  const needDay = [placeReach, descReach, creditReach, clockReach].includes('day');
  const rel = useRelated(selected, selKey, needDay, savedEpoch);
  useEffect(() => setExcluded(new Set()), [selKey]);
  const scopes = useMemo(() => {
    const keep = (l: PhotoGroup[]) => l.filter((g) => !excluded.has(g.group_id));
    const counts = relatedCounts(rel.rows, selected);
    const dayCount = relatedSession(rel.rows, selected, 'day').length;
    const sessionGap: Gap = placeReach === 'selection' ? 'auto' : placeReach;
    const placeAll = reachPhotos(placeReach, rel.rows, selected);
    const placeList = onlyNoLoc ? placeAll.filter((g) => g.lat === null) : placeAll;
    const clock = clockPhotos(clockReach, rel.rows, selected, sessionGap);
    const extra: Record<ScopeName, number[]> = {
      place: keep(placeList).map((g) => g.group_id),
      description: keep(reachPhotos(descReach, rel.rows, selected)).map((g) => g.group_id),
      credit: keep(reachPhotos(creditReach, rel.rows, selected)).map((g) => g.group_id),
      clock: keep(clock).map((g) => g.group_id),
    };
    return {
      counts,
      dayCount,
      autoS: autoStep(counts),
      placeList,
      extra,
      clockCounts: {
        session: clockPhotos('session', rel.rows, selected, sessionGap).length,
        day: clockPhotos('day', rel.rows, selected, sessionGap).length,
      },
    };
  }, [rel.rows, selected, placeReach, descReach, creditReach, clockReach, onlyNoLoc, excluded]);
  const relIds = scopes.extra.place;
  const relKey = relIds.join(',');
  useEffect(() => {
    onRelated(relIds);
  }, [relKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onRelated([]), []); // eslint-disable-line react-hooks/exhaustive-deps
  const selIds = useMemo(() => selected.map((g) => g.group_id), [selKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const plan = useMemo(() => planSaves(pend, selIds, scopes.extra), [pend, selIds, scopes.extra]);
  const planIds = new Set(plan.flatMap((r) => r.ids));
  const alsoCount = [...planIds].filter((id) => !selIds.includes(id)).length;

  // The map refits when the selection changes; a pin from a search or suggestion flies there too.
  const [focus, setFocus] = useState(0);
  const [big, setBig] = useState(false);
  useEffect(() => setFocus((f) => f + 1), [selKey]);

  const set = <K extends keyof Pending>(k: K, v: Pending[K]) => setPend((p) => ({ ...p, [k]: v }));
  const setLoc = (loc: PendingLoc, fly = false) => {
    setPend((p) => ({ ...p, loc }));
    if (fly) setFocus((f) => f + 1);
  };

  const copyOne = async (name: string) => {
    if (await copyText(name)) toast(`Copied ${name}`);
    else toast('Could not copy. Select the name by hand.', 'err');
  };

  const save = async () => {
    if (!plan.length) return;
    setSaving(true);
    try {
      for (const req of plan) {
        await api('POST', '/api/groups/meta', { group_ids: req.ids, fields: req.fields });
        await onSaved(req.fields, req.ids);
      }
      setPend(NO_EDITS);
      toast(`Saved to ${plural(planIds.size, 'photo')}.`);
    } catch (err) {
      toast((err as Error).message, 'err');
    } finally {
      setSaving(false);
    }
  };

  const detail = useDetail(n === 1 && first ? first.group_id : null);
  const exposure = n === 1 ? fmtExposure(detail?.exif ?? null) : '';
  const chips = useMemo<Chip[]>(() => {
    if (n === 1 && detail) {
      return detail.instances.map((i) => ({ key: `${i.volume_id}|${i.rel_path}`, volume_id: i.volume_id, label: i.label || i.role || 'Drive', mounted: i.mounted, hint: `${i.state}: ${i.rel_path}`, count: 0 }));
    }
    if (n === 1) return [];
    return volumeCounts(selected).map((c) => {
      const d = drives.find((x) => x.volume_id === c.volume_id);
      return { key: c.volume_id, volume_id: c.volume_id, label: d ? driveName(d) : 'Drive', mounted: d?.mounted ?? false, hint: plural(c.count, 'selected photo'), count: c.count };
    });
  }, [n, detail, selKey, drives]); // eslint-disable-line react-hooks/exhaustive-deps

  const located = selected.filter((g) => g.lat !== null && g.lon !== null);
  const points = useMemo<MapPoint[]>(() => located.map((g) => ({ lat: g.lat!, lon: g.lon! })), [selKey, located.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const pin = pend.loc && pend.loc.lat !== null && pend.loc.lon !== null ? { lat: pend.loc.lat, lon: pend.loc.lon } : null;

  return (
    <aside className="card flex h-full flex-col overflow-hidden bg-surface/95 shadow-2xl xl:h-auto xl:max-h-[calc(100vh-88px)] xl:bg-surface/80 xl:shadow-none">
      <div className="flex flex-col gap-1 border-b border-line px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <p className="min-w-0 flex-1 truncate font-medium" title={n === 1 ? first?.canonical_stem ?? '' : undefined}>
            {n === 1 ? first?.canonical_stem || `Photo ${first?.group_id}` : `${fmtInt(n)} selected`}
          </p>
          <div className="flex shrink-0 items-center">
            {n === 1 && first && vaultCopyName(first) && (
              <>
                <IconBtn icon="copy" label={`Copy name: ${vaultCopyName(first)}`} onClick={() => void copyOne(vaultCopyName(first))} />
                {copyName(first) && copyName(first) !== vaultCopyName(first) && (
                  <IconBtn icon="camera" label={`Copy camera name: ${copyName(first)}`} onClick={() => void copyOne(copyName(first))} />
                )}
              </>
            )}
            <IconBtn icon="locate" label="Show in All Photos: clear the filters and scroll the grid to this photo" onClick={onShowInAll} />
            {n === 1 && first && first.volume_id && <IconBtn icon="expand" label="Full screen" onClick={() => onOpenViewer(first)} />}
            <IconBtn icon="x" label="Clear selection" onClick={onClear} />
          </div>
        </div>
        <p className="text-xs text-muted" title={exposure || undefined}>
          {n === 1 && first
            ? [first.body_nickname, first.capture_time?.replace('T', ' '), exposure].filter(Boolean).join(' · ')
            : [selectionLine(selected), 'Changes apply to every selected photo.'].filter(Boolean).join(' · ')}
        </p>
        <DriveChips chips={chips} showing={n === 1 ? detail?.preview_from ?? null : null} loaded={n !== 1 || detail !== null} />
      </div>

      <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-4 pt-3">
        {n === 1 && first && <Preview group={first} plugIn={plugInHint(detail)} onOpen={() => onOpenViewer(first)} />}

        <Section title="Location">
          <p className="text-xs text-muted">
            {pend.loc
              ? pend.loc.lat === null
                ? 'Will remove the location.'
                : <>New: <Coord lat={pend.loc.lat} lon={pend.loc.lon!} /> · {GPS_LABEL[pend.loc.source ?? ''] ?? ''}</>
              : n === 1 && first
                ? first.lat !== null && first.lon !== null
                  ? <><Coord lat={first.lat} lon={first.lon} /> · {GPS_LABEL[first.gps_source ?? 'camera'] ?? first.gps_source}</>
                  : 'No location yet. Click the map, search a place or use a suggestion.'
                : `${fmtInt(located.length)} of ${fmtInt(n)} have a location.`}
          </p>
          <div className="relative">
            <MapPicker points={points} pin={pin} focus={focus} onPick={(p) => setLoc({ lat: p.lat, lon: p.lon, alt: null, source: 'map' })} />
            <button type="button" className="btn absolute right-2 top-2 z-[500] h-7 px-2 text-xs" title="Expand map" aria-label="Expand map" onClick={() => setBig(true)}>
              Expand
            </button>
          </div>
          {pin && pend.loc && pend.loc.source !== 'map' && (
            <button type="button" className="btn-ghost -mt-1 h-7 self-start px-2 text-xs" onClick={() => setBig(true)}>
              Refine on map
            </button>
          )}
          {big && (
            <MapWindow
              points={points}
              start={pin}
              onCancel={() => setBig(false)}
              onDone={(p) => {
                setLoc({ lat: p.lat, lon: p.lon, alt: null, source: 'map' }, true);
                setBig(false);
              }}
            />
          )}
          {first && <Suggestions key={first.group_id} first={first} pool={pool} selected={selected} windowS={windowS} onUse={(l, f) => { setLoc(l, true); if (f) setPend((p) => ({ ...p, ...placeEdits(f) })); }} />}
          <PlaceSearch
            groupId={first?.group_id ?? null}
            onPick={(loc, f) => {
              setLoc(loc, true);
              setPend((p) => ({ ...p, ...placeEdits(f) }));
            }}
          />
          <TextField
            label="Place name"
            shared={commonValue(selected, 'place_name')}
            value={pend.place}
            onChange={(v) => set('place', v)}
            placeholder="e.g. a beach or a town"
          />
          <div className="grid grid-cols-3 gap-2">
            <TextField label="City" shared={commonValue(selected, 'city')} value={pend.city} onChange={(v) => set('city', v)} />
            <TextField label="State" shared={commonValue(selected, 'state')} value={pend.state} onChange={(v) => set('state', v)} />
            <TextField label="Country" shared={commonValue(selected, 'country')} value={pend.country} onChange={(v) => set('country', v)} />
          </div>
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

        <Section title="Clock">
          <ClockShift first={first} n={n} pend={pend} set={(v) => set('shift', v)} defaultOffset={settings.default_offset} />
        </Section>

        <Section title="Applies to">
          <AppliesTo
            selected={selected}
            rel={rel}
            placeReach={placeReach}
            setPlaceReach={setPlaceReach}
            descReach={descReach}
            setDescReach={setDescReach}
            creditReach={creditReach}
            setCreditReach={setCreditReach}
            clockReach={clockReach}
            setClockReach={setClockReach}
            onlyNoLoc={onlyNoLoc}
            setOnlyNoLoc={setOnlyNoLoc}
            excluded={excluded}
            setExcluded={setExcluded}
            placeList={scopes.placeList}
            descCount={scopes.extra.description.length}
            creditCount={scopes.extra.credit.length}
            clockCount={scopes.extra.clock.length}
            clockCounts={scopes.clockCounts}
            autoS={scopes.autoS}
            counts={scopes.counts}
            dayCount={scopes.dayCount}
          />
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
                {saving ? 'Saving…' : alsoCount ? `Save to ${fmtInt(n)} + ${fmtInt(alsoCount)} related` : n === 1 ? 'Save' : `Save to ${fmtInt(n)} photos`}
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

interface Chip {
  key: string;
  volume_id: string;
  label: string;
  mounted: boolean;
  hint: string;
  count: number;
}

function IconBtn({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button type="button" className="btn-ghost h-7 w-7 justify-center px-0 text-muted hover:text-ink" title={label} aria-label={label} onClick={onClick}>
      <Icon name={icon} />
    </button>
  );
}

/** The drives holding the photo: a filled dot when plugged in, a hollow one when not; the one the preview is read from is marked. */
function DriveChips({ chips, showing, loaded }: { chips: Chip[]; showing: string | null; loaded: boolean }) {
  if (!loaded && !chips.length) return <div className="h-5 w-24 animate-pulse rounded-full bg-ink/5" aria-hidden="true" />;
  if (!chips.length) return <p className="text-2xs text-faint">No drive holds this photo yet.</p>;
  const from = chips.find((c) => c.volume_id === showing);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {chips.map((c) => {
        const on = c.volume_id === showing;
        return (
          <span
            key={c.key}
            title={`${c.mounted ? 'Connected' : 'Not connected'} · ${c.hint}${on ? ' · the preview is loaded from here' : ''}`}
            className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-2xs ${on ? 'border-accent/50 bg-accent/10 text-ink' : 'border-line text-muted'}`}
          >
            <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${c.mounted ? 'bg-go' : 'border border-faint'}`} />
            <span className="truncate">{c.label}</span>
            {c.count > 0 && <span className="tabular text-faint">{fmtInt(c.count)}</span>}
            {on && <Icon name="image" size={10} className="text-accent" />}
          </span>
        );
      })}
      {showing !== null || chips.some((c) => c.mounted) ? (
        from && <span className="text-2xs text-faint">showing from {from.label}</span>
      ) : (
        <span className="text-2xs text-faint">no drive connected</span>
      )}
    </div>
  );
}

/** GET /api/groups/<id>/detail for the one open photo (rv-21); null while loading or for a selection. */
function useDetail(groupId: number | null): GroupDetail | null {
  const [d, setD] = useState<GroupDetail | null>(null);
  useEffect(() => {
    setD(null);
    if (groupId === null) return undefined;
    let alive = true;
    api<GroupDetail>('GET', `/api/groups/${groupId}/detail`)
      .then((x) => alive && setD(x))
      .catch(() => alive && setD({ group_id: groupId, instances: [], preview_from: null, exif: null }));
    return () => {
      alive = false;
    };
  }, [groupId]);
  return d;
}

/** The drive to plug in for full size, or null when one is connected or the photo has no drive (rv-23). */
function plugInHint(d: GroupDetail | null): string | null {
  if (!d || !d.instances.length || d.instances.some((i) => i.mounted)) return null;
  return suggestDrives(d.instances)[0]?.label || null;
}

function Preview({ group, plugIn, onOpen }: { group: PhotoGroup; plugIn: string | null; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={onOpen}
        disabled={failed}
        title="Open full screen"
        aria-label="Open full screen"
        className="flex aspect-[3/2] cursor-zoom-in items-center justify-center overflow-hidden rounded-lg bg-ink/5 disabled:cursor-default"
      >
        {failed ? (
          <span className="flex flex-col items-center gap-1 text-xs text-faint">
            <Icon name="image" size={20} />
            No preview (drive not connected?)
          </span>
        ) : (
          <img src={`/api/groups/${group.group_id}/thumb`} alt="" className="h-full w-full object-contain" onError={() => setFailed(true)} />
        )}
      </button>
      {plugIn && <p className="text-2xs text-faint">Thumbnail · plug in {plugIn} for full size</p>}
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

function Suggestions({ first, pool, selected, windowS, onUse }: { first: PhotoGroup; pool: PhotoGroup[]; selected: PhotoGroup[]; windowS: number; onUse: (l: PendingLoc, f?: PlaceFields) => void }) {
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

  const rows: Array<{ key: string; text: string; sub: ReactNode; loc: PendingLoc; fields?: PlaceFields; quiet?: boolean }> = [];
  for (const [i, s] of (list ?? []).entries()) {
    const fields: PlaceFields = { place_name: s.place_name ?? null, city: s.city ?? null, state: s.state ?? null, country: s.country ?? null };
    const label = placeLabel(fields);
    const tier = s.confidence;
    const outer = tier === 'far' || tier === 'weak' || tier === 'hint';
    const when = outer ? fmtApart(s.delta_s) : s.delta_s === 0 ? 'between two fixes' : fmtDelta(s.delta_s);
    const name = label || GPS_LABEL[s.source] || s.source;
    const prefix = tier === 'hint' ? (s.delta_s < 0 ? 'Last known: ' : 'Next known: ') : '';
    const via = s.via ? `via ${s.via.stem} · phone ${fmtDelta(s.via.delta_s)} · ` : '';
    let text = `${prefix}${name} · ${when}`;
    if (s.ends) {
      const e = s.ends;
      const gaps = `${fmtGapShort(e.before_s)} / ${fmtGapShort(e.after_s)}`;
      text = e.stayed
        ? `At ${e.before_place ?? e.after_place ?? 'the same spot'} before and after (${gaps})`
        : `Between ${e.before_place ?? 'one place'} and ${e.after_place ?? 'another'}, ${e.km} km apart (${gaps})`;
    }
    const tierText = tier === 'hint' ? 'a hint, not a match' : tier === 'weak' ? 'weak: check it' : tier === 'far' ? 'far: check it' : `${tier} confidence`;
    rows.push({
      key: `s${i}`,
      quiet: (s.score ?? 1) < 0.4,
      text,
      fields,
      sub: <>{via}<Coord lat={s.lat} lon={s.lon} /> · {tierText}</>,
      loc: { lat: s.lat, lon: s.lon, alt: s.alt ?? null, source: 'match' },
    });
  }
  if (near) {
    rows.push({
      key: 'near',
      text: `From ${near.group.canonical_stem ?? 'a nearby photo'} · ${fmtDelta(near.delta)}`,
      sub: <Coord lat={near.group.lat!} lon={near.group.lon!} />,
      loc: { lat: near.group.lat, lon: near.group.lon, alt: near.group.alt, source: 'neighbour' },
    });
  }
  if (list === null && !err) return <p className="text-xs text-faint">Looking for GPS matches…</p>;
  if (!rows.length) return <p className="text-xs text-faint">{err || 'No GPS points or located photos near this time. Add a GPS reference folder in Settings to match by time.'}</p>;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-muted">{selected.length > 1 ? 'Suggested from the first photo:' : 'Suggested:'}</p>
      {rows.map((r) => (
        <div key={r.key} className={`flex items-center justify-between gap-2 rounded-lg border border-line px-2.5 py-1.5 ${r.quiet ? 'opacity-60' : ''}`}>
          <div className="min-w-0">
            <p className="truncate text-xs">{r.text}</p>
            <p className="truncate font-mono text-2xs text-faint">{r.sub}</p>
          </div>
          <button type="button" className="btn h-7 px-2 text-xs" onClick={() => onUse(r.loc, r.fields)}>
            Use
          </button>
        </div>
      ))}
    </div>
  );
}

/** The expanded map (rv-24): a floating window to place the pin precisely. Done keeps it; Cancel or Esc leaves it as it was. */
function MapWindow({ points, start, onCancel, onDone }: { points: MapPoint[]; start: MapPoint | null; onCancel: () => void; onDone: (p: MapPoint) => void }) {
  const [draft, setDraft] = useState<MapPoint | null>(start);
  const [focus, setFocus] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    // Capture phase on window, so Esc closes only this window and not the inspector or the viewer behind it.
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      e.preventDefault();
      cancel.current();
    };
    window.addEventListener('keydown', onKey, true);
    root.current?.focus();
    return () => {
      window.removeEventListener('keydown', onKey, true);
      prev?.focus?.();
    };
  }, []);
  return createPortal(
    <motion.div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-0 backdrop-blur-sm sm:p-6" initial={{ opacity: 0 }} animate={{ opacity: 1 }} onClick={onCancel}>
      <motion.div
        ref={root}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Place the pin on the map"
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={springs.snappy}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full flex-col gap-3 border-line-strong bg-raised p-3 shadow-2xl shadow-black/60 outline-none sm:h-[80vh] sm:w-[80vw] sm:rounded-2xl sm:border"
      >
        <PlaceSearch groupId={null} onPick={(loc) => { if (loc.lat !== null) { setDraft({ lat: loc.lat, lon: loc.lon! }); setFocus((f) => f + 1); } }} />
        <MapPicker big points={points} pin={draft} focus={focus} onPick={setDraft} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted">{draft ? <Coord lat={draft.lat} lon={draft.lon} /> : 'Click the map to place the pin, then drag it to fine-tune.'}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
            <button type="button" className="btn" disabled={!draft} onClick={() => draft && onDone(draft)}>Done</button>
          </div>
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}

/** Coordinates as a link that opens the spot in Google Maps in a new tab. */
function Coord({ lat, lon }: { lat: number; lon: number }) {
  return (
    <a
      href={mapsUrl(lat, lon)}
      target="_blank"
      rel="noopener noreferrer"
      title="Open in Google Maps"
      className="font-mono underline decoration-dotted underline-offset-2 hover:text-accent"
      onClick={(e) => e.stopPropagation()}
    >
      {fmtCoord(lat, lon)}
    </a>
  );
}

/** Place search runs only on Enter (or the button), at most once a second, and sends only the typed text. */
function PlaceSearch({ groupId, onPick }: { groupId: number | null; onPick: (loc: PendingLoc, f: PlaceFields) => void }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Place[] | null>(null);
  const [msg, setMsg] = useState('');
  const last = useRef(0);
  const [focused, setFocused] = useState(false);
  const [near, setNear] = useState<NearPlace[]>([]);
  useEffect(() => {
    setNear([]);
    if (groupId === null) return;
    let alive = true;
    api<{ places: NearPlace[] }>('GET', `/api/groups/${groupId}/places?window_s=3600`)
      .then((d) => alive && setNear(d.places))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [groupId]);

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
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Search a place, press Enter" onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} className="h-9 min-w-0 flex-1" />
        <button type="button" className="btn h-9" onClick={() => void run()} disabled={!q.trim()} aria-label="Search place">
          <Icon name="search" />
        </button>
      </div>
      {focused && !q.trim() && near.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted">Near this time, from your phone:</p>
          <ul className="flex flex-col overflow-hidden rounded-lg border border-line">
            {near.map((p, i) => (
              <li key={i}>
                <button
                  type="button"
                  className="w-full truncate px-2.5 py-1.5 text-left text-xs hover:bg-ink/5"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => onPick({ lat: p.lat, lon: p.lon, alt: p.alt, source: 'match' }, p)}
                >
                  {[p.place_name, placeLabel({ ...p, place_name: null }) || null].filter(Boolean).join(', ')} · {fmtDelta(p.delta_s)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
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
                  onPick({ lat: Number(h.lat), lon: Number(h.lon), alt: null, source: 'search' }, nominatimPlace(h.name || h.display_name || '', h.address));
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

interface RelatedState {
  rows: PhotoGroup[]; // every photo fetched around the selection (the selection included)
  fetched: number;
  loading: boolean;
  error: string;
}

/** Fetch photos around the selection; refetches on a new selection, when a scope needs the whole day, or after a save. */
function useRelated(selected: PhotoGroup[], selKey: string, needDay: boolean, savedEpoch: number): RelatedState {
  const [data, setData] = useState<{ key: string; rows: PhotoGroup[] } | null>(null);
  const [error, setError] = useState('');
  const sel = useRef(selected);
  sel.current = selected;
  const key = `${selKey}|${needDay ? 'day' : 'near'}`;
  useEffect(() => {
    const { after, before } = relatedWindowFor(sel.current, needDay ? ['day'] : ['auto']);
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
    if (!data || data.key !== key) return { rows: [], fetched: 0, loading: true, error: '' };
    return { rows: data.rows, fetched: data.rows.length, loading: false, error };
  }, [data, key, error]);
}

interface ScopeOption {
  value: string;
  label: string;
}

const encode = (v: Reach | ClockReach): string => String(v);
const decode = (s: string): Reach => (s === 'selection' || s === 'auto' || s === 'day' ? s : Number(s));

/** One row of "Applies to": which fields, a picker, and how many other photos it reaches right now. */
function ScopeRow({
  label,
  fields,
  value,
  options,
  onChange,
  count,
  children,
}: {
  label: string;
  fields: string;
  value: string;
  options: ScopeOption[];
  onChange: (v: string) => void;
  count: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium">{label}</span>
        <span className="truncate text-2xs text-faint">{fields}</span>
      </div>
      <div className="flex items-center gap-2">
        <select value={value} onChange={(e) => onChange(e.target.value)} className="h-8 min-w-0 flex-1 text-xs" aria-label={`${label} applies to`}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="shrink-0 text-xs text-muted tabular">{count}</span>
      </div>
      {children}
    </div>
  );
}

const ladderOptions = (counts: number[], dayCount: number, auto: number): ScopeOption[] => [
  { value: 'selection', label: 'Selection only' },
  { value: 'auto', label: `Auto · ${gapLabel(auto)} (${fmtInt(counts[LADDER.indexOf(auto)] ?? 0)})` },
  ...LADDER.map((s, i) => ({ value: String(s), label: `${gapLabel(s)} (${fmtInt(counts[i] ?? 0)})` })),
  { value: 'day', label: `Same day (${fmtInt(dayCount)})` },
];

const more = (n: number): string => (n === 0 ? 'only these' : `+${fmtInt(n)} more`);

function AppliesTo({
  selected,
  rel,
  placeReach,
  setPlaceReach,
  descReach,
  setDescReach,
  creditReach,
  setCreditReach,
  clockReach,
  setClockReach,
  onlyNoLoc,
  setOnlyNoLoc,
  excluded,
  setExcluded,
  placeList,
  descCount,
  creditCount,
  clockCount,
  clockCounts,
  autoS,
  counts,
  dayCount,
}: {
  selected: PhotoGroup[];
  rel: RelatedState;
  placeReach: Reach;
  setPlaceReach: (v: Reach) => void;
  descReach: Reach;
  setDescReach: (v: Reach) => void;
  creditReach: Reach;
  setCreditReach: (v: Reach) => void;
  clockReach: ClockReach;
  setClockReach: (v: ClockReach) => void;
  onlyNoLoc: boolean;
  setOnlyNoLoc: (v: boolean) => void;
  excluded: Set<number>;
  setExcluded: (s: Set<number>) => void;
  placeList: PhotoGroup[];
  descCount: number;
  creditCount: number;
  clockCount: number;
  clockCounts: { session: number; day: number };
  autoS: number;
  counts: number[];
  dayCount: number;
}) {
  const hasTime = selected.some((g) => g.capture_time);
  const toggle = (id: number) => {
    const next = new Set(excluded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExcluded(next);
  };
  const shown = placeList.slice(0, REL_SHOWN);
  const ticked = placeList.filter((g) => !excluded.has(g.group_id)).length;
  const delta = (g: PhotoGroup): string => {
    let best: number | null = null;
    for (const s of selected) {
      const d = secondsBetween(s.capture_time, g.capture_time);
      if (d !== null && (best === null || Math.abs(d) < Math.abs(best))) best = d;
    }
    return best === null ? '' : ` · ${fmtDelta(best)}`;
  };
  const opts = ladderOptions(counts, dayCount, autoS);
  const clockOpts: ScopeOption[] = [
    { value: 'selection', label: 'Selection only' },
    { value: 'session', label: `Same camera · this session (${fmtInt(clockCounts.session)})` },
    { value: 'day', label: `Same camera · same day (${fmtInt(clockCounts.day)})` },
  ];
  const state = !hasTime ? 'The selection has no capture time, so nothing else can be related.' : rel.loading ? 'Looking for related photos…' : rel.error ? `Could not load related photos: ${rel.error}` : '';

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">Each kind of edit reaches its own set of photos. Untick a photo below to leave it out of all of them.</p>
      {state && <p className="text-xs text-faint">{state}</p>}
      <ScopeRow label="Place" fields="GPS, place name" value={encode(placeReach)} options={opts} onChange={(v) => setPlaceReach(decode(v))} count={more(ticked)}>
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={onlyNoLoc} onChange={(e) => setOnlyNoLoc(e.target.checked)} />
          Only without a location
        </label>
        {rel.fetched >= REL_LIMIT && <p className="text-xs text-faint">The list may be cut short: only the first {fmtInt(REL_LIMIT)} photos nearby were checked.</p>}
        {placeList.length > 0 && (
          <>
            <div className="flex items-center justify-between text-xs text-muted">
              <span>{fmtInt(ticked)} ticked</span>
              <span className="flex gap-2">
                <button type="button" className="text-accent hover:underline" onClick={() => setExcluded(new Set())}>
                  All
                </button>
                <button type="button" className="text-accent hover:underline" onClick={() => setExcluded(new Set(placeList.map((g) => g.group_id)))}>
                  None
                </button>
              </span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(52px,1fr))] gap-1">
              {shown.map((g) => (
                <RelatedThumb key={g.group_id} g={g} on={!excluded.has(g.group_id)} title={`${g.canonical_stem || `Photo ${g.group_id}`} · ${g.capture_time?.replace('T', ' ') ?? ''}${delta(g)}`} onToggle={() => toggle(g.group_id)} />
              ))}
            </div>
            {placeList.length > shown.length && <p className="text-xs text-faint">+{fmtInt(placeList.length - shown.length)} more (all ticked unless you chose None)</p>}
          </>
        )}
      </ScopeRow>
      <ScopeRow label="Description" fields="title, keywords" value={encode(descReach)} options={opts} onChange={(v) => setDescReach(decode(v))} count={more(descCount)} />
      <ScopeRow label="Credit" fields="creator, copyright" value={encode(creditReach)} options={opts} onChange={(v) => setCreditReach(decode(v))} count={more(creditCount)} />
      <ScopeRow label="Clock shift" fields="same camera body only" value={clockReach} options={clockOpts} onChange={(v) => setClockReach(v as ClockReach)} count={more(clockCount)} />
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
