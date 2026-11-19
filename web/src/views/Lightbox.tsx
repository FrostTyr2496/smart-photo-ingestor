import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../components/bits';
import { fmtCoord, fitBox, mapsUrl, needsParts, placeLabel, sizeBadge, suggestDrives, wrapIndex, type Need, type PhotoGroup } from '../lib/photos';
import { ZoomView } from './ZoomView';

const fullUrl = (id: number): string => `/api/groups/${id}/preview?full=1`;
const headerNum = (v: string | null): number | null => (v && Number(v) > 0 ? Number(v) : null);

/** How often the mounts are rechecked while the "plug in a drive" card is open (rv-23). */
const RECHECK_MS = 5000;

interface FullImage {
  url: string;
  w: number | null;
  h: number | null;
  full: boolean;
}
const iconBtn = 'flex h-10 w-10 items-center justify-center rounded-full text-white/80 transition-colors hover:bg-white/10 hover:text-white';
const kbd = 'rounded border border-white/15 px-1 font-mono text-2xs text-white/60';

/** Label/value rows of the info overlay (rv-20): what the catalog knows about the photo. */
export function infoRows(g: PhotoGroup): Array<[string, string, string?]> {
  const rows: Array<[string, string | null | undefined, string?]> = [
    ['Name', g.vault_name || g.canonical_stem],
    ['Camera', g.body_nickname],
    ['Taken', g.capture_time?.replace('T', ' ')],
    ['Location', g.lat !== null && g.lon !== null ? fmtCoord(g.lat, g.lon) : null, g.lat !== null && g.lon !== null ? mapsUrl(g.lat, g.lon) : undefined],
    ['Place', placeLabel(g) || g.country],
    ['Keywords', g.keywords.length ? g.keywords.join(', ') : null],
  ];
  return rows.filter((r): r is [string, string, string?] => Boolean(r[1]));
}

/**
 * The full-screen viewer (rv-20), modelled on the user's website lightbox: the photo uncropped on a black scrim,
 * portalled to the page body by Photos. Left and right step (wrapping), Esc closes (or leaves zoom), I toggles the info
 * overlay, F full screen. A swipe steps on touch; a click on the photo opens ZoomView at 100% and a click outside
 * the photo closes. The thumbnail shows, blurred, until the large embedded preview has loaded.
 */
export function Lightbox({
  list,
  index,
  onClose,
  onStep,
}: {
  list: PhotoGroup[];
  index: number;
  onClose: () => void;
  onStep: (delta: number) => void;
}) {
  const g = list[index]!;
  const reduced = useReducedMotion() ?? false;
  const dialog = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const dragged = useRef(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [ratio, setRatio] = useState(1.5);
  const [natural, setNatural] = useState(0);
  const [zoomAt, setZoomAt] = useState<{ x: number; y: number } | null>(null);
  const [info, setInfo] = useState(false);
  const [img, setImg] = useState<FullImage | null>(null);
  const [needs, setNeeds] = useState<Need[] | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const gid = g.group_id;
  const cardOpen = needs !== null && !dismissed && img === null && !failed;

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
    setZoomAt(null);
    setImg(null);
    setNeeds(null);
    setDismissed(false);
    setAttempt(0);
  }, [gid]);

  // The full-size image, fetched so its size headers and a 409 (no drive connected) can be read (rv-23).
  useEffect(() => {
    const ctl = new AbortController();
    fetch(fullUrl(gid), { signal: ctl.signal })
      .then(async (res) => {
        if (res.status === 409) {
          const b = (await res.json()) as { needs?: Need[] };
          setNeeds(b.needs ?? []);
          return;
        }
        if (!res.ok) {
          setFailed(true);
          return;
        }
        const blob = await res.blob();
        setNeeds(null);
        setImg({ url: URL.createObjectURL(blob), w: headerNum(res.headers.get('X-Image-Width')), h: headerNum(res.headers.get('X-Image-Height')), full: res.headers.get('X-Image-Full') === '1' });
      })
      .catch((e: unknown) => {
        if (!(e instanceof DOMException && e.name === 'AbortError')) setFailed(true);
      });
    return () => ctl.abort();
  }, [gid, attempt]);

  useEffect(() => {
    const url = img?.url;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [img]);

  // While the card is open, look again for a mounted drive; it stops when the card or the viewer closes.
  useEffect(() => {
    if (!cardOpen) return undefined;
    const t = window.setInterval(() => setAttempt((a) => a + 1), RECHECK_MS);
    return () => window.clearInterval(t);
  }, [cardOpen]);

  const toggleFullscreen = () => {
    const el = dialog.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void el.requestFullscreen?.().catch(() => undefined);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'Escape') {
        if (cardOpen) setDismissed(true);
        else if (zoomAt) setZoomAt(null);
        else onClose();
      } else if (e.key === 'ArrowRight') onStep(1);
      else if (e.key === 'ArrowLeft') onStep(-1);
      else if (e.key === 'i' || e.key === 'I') setInfo((v) => !v);
      else if (e.key === 'f' || e.key === 'F') toggleFullscreen();
      else if (e.key === 'Tab') {
        const nodes = dialog.current?.querySelectorAll<HTMLElement>('button, a[href]');
        if (!nodes?.length) return;
        const first = nodes[0]!;
        const last = nodes[nodes.length - 1]!;
        if (e.shiftKey && document.activeElement === first) last.focus();
        else if (!e.shiftKey && document.activeElement === last) first.focus();
        else return;
      } else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose, onStep, zoomAt, cardOpen]);

  // Lock page scroll, move focus in, and leave full screen when the viewer goes.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = prev;
      if (document.fullscreenElement) void document.exitFullscreen?.();
    };
  }, []);

  // Warm the neighbours once this one is on screen, so the arrows feel instant.
  useEffect(() => {
    if (!loaded && !failed) return;
    for (const d of [1, -1]) {
      const n = list[wrapIndex(index, d, list.length)];
      if (n && n.group_id !== g.group_id && n.volume_id) new Image().src = `/api/groups/${n.group_id}/thumb`;
    }
  }, [loaded, failed, index, list, g.group_id]);

  // A click on the photo zooms in on that spot; a click on the empty space around it closes.
  const onStageClick = (e: MouseEvent<HTMLDivElement>) => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    const r = e.currentTarget.getBoundingClientRect();
    const box = fitBox(r.width, r.height, ratio);
    const px = (e.clientX - (r.left + (r.width - box.w) / 2)) / box.w;
    const py = (e.clientY - (r.top + (r.height - box.h) / 2)) / box.h;
    if (px < 0 || px > 1 || py < 0 || py > 1) onClose();
    else if (loaded) setZoomAt({ x: e.clientX / window.innerWidth, y: e.clientY / window.innerHeight });
  };

  const dur = reduced ? 0 : 0.16;
  const title = g.vault_name || g.canonical_stem || `Photo ${g.group_id}`;
  const rows = infoRows(g);

  // Portalled to <body> so the page column's transform does not confine the full-screen viewer (same as Modal).
  return createPortal(
    <motion.div
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label={`${title}, photo ${index + 1} of ${list.length}`}
      tabIndex={-1}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: dur }}
      className="fixed inset-0 z-[100] flex flex-col bg-black/95 text-white outline-none backdrop-blur-sm"
    >
      <header className="flex h-14 shrink-0 items-center gap-2 pl-4 pr-2 sm:pl-5 sm:pr-3">
        <p className="tabular shrink-0 font-mono text-xs text-white/60" aria-hidden="true">
          {index + 1} <span className="text-white/40">/ {list.length}</span>
        </p>
        <div className="min-w-0 flex-1 px-2">
          <h2 className="truncate font-mono text-sm font-medium">{title}</h2>
          {g.capture_time && <p className="truncate font-mono text-2xs text-white/60">{g.capture_time.replace('T', ' ')}</p>}
        </div>
        <button
          type="button"
          onClick={() => setInfo((v) => !v)}
          aria-pressed={info}
          title="Info (I)"
          className={`flex h-9 items-center gap-1.5 rounded-full border px-3 font-mono text-2xs transition-colors ${info ? 'border-accent/60 bg-accent/15 text-white' : 'border-white/15 text-white/60 hover:text-white'}`}
        >
          Info <span className={`${kbd} hidden sm:inline`}>I</span>
        </button>
        <button type="button" onClick={toggleFullscreen} title="Full screen (F)" className="hidden h-9 items-center gap-1.5 rounded-full border border-white/15 px-3 font-mono text-2xs text-white/60 transition-colors hover:text-white sm:flex">
          Full screen <span className={kbd}>F</span>
        </button>
        <button type="button" onClick={onClose} title="Close (Esc)" aria-label="Close" className={iconBtn}>
          <Icon name="x" size={18} />
        </button>
      </header>

      <div ref={stage} className="relative min-h-0 flex-1" onClick={(e) => e.target === e.currentTarget && onClose()}>
        {/* Swipe on touch steps; the photo is always object-contain, never cropped. */}
        <motion.div
          key={g.group_id}
          drag="x"
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={reduced ? 0 : 0.5}
          onDragStart={() => {
            dragged.current = true;
          }}
          onDragEnd={(_, i) => {
            if (i.offset.x < -70 || i.velocity.x < -500) onStep(1);
            else if (i.offset.x > 70 || i.velocity.x > 500) onStep(-1);
          }}
          initial={{ opacity: 0, scale: reduced ? 1 : 0.985 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: reduced ? 0 : 0.18 }}
          className="absolute inset-x-2 inset-y-1 cursor-zoom-in touch-pan-y sm:inset-x-16"
          onClick={onStageClick}
        >
          <img
            src={`/api/groups/${g.group_id}/thumb`}
            alt=""
            aria-hidden="true"
            draggable={false}
            className={`pointer-events-none absolute inset-0 h-full w-full select-none object-contain ${loaded ? 'opacity-0' : failed || needs ? 'opacity-100' : 'opacity-100 blur-sm'} transition-opacity duration-200`}
          />
          {!failed && img && (
            <img
              src={img.url}
              alt={title}
              draggable={false}
              decoding="async"
              onLoad={(e) => {
                const im = e.currentTarget;
                if (im.naturalWidth && im.naturalHeight) {
                  setRatio(im.naturalWidth / im.naturalHeight);
                  setNatural(img.w ?? im.naturalWidth);
                }
                setLoaded(true);
              }}
              onError={() => setFailed(true)}
              className={`pointer-events-none absolute inset-0 h-full w-full select-none object-contain transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
            />
          )}
        </motion.div>

        {loaded && img && (
          <p className="pointer-events-none absolute right-3 top-2 z-10 rounded-full bg-black/55 px-2.5 py-1 font-mono text-2xs text-white/80" aria-live="polite">
            {sizeBadge(img.w, img.h, img.full)}
          </p>
        )}

        {cardOpen && needs && (
          <div role="status" className="absolute inset-x-3 bottom-4 z-10 mx-auto flex max-w-md items-start gap-3 rounded-2xl border border-white/10 bg-black/75 p-4 backdrop-blur-xl">
            <div className="min-w-0 flex-1">
              {needs.length ? (
                <p className="text-sm">
                  Full size is on <strong>{needsParts(needs).first}</strong>
                  {needsParts(needs).rest}. Plug one in to see it.
                </p>
              ) : (
                <p className="text-sm">Full size is not on any known drive.</p>
              )}
              <div className="mt-2 flex flex-wrap gap-1">
                {suggestDrives(needs).map((n) => (
                  <span key={n.volume_id} className="inline-flex max-w-full items-center gap-1 rounded-full border border-white/20 px-2 py-0.5 font-mono text-2xs text-white/80">
                    <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full border border-white/50" />
                    <span className="truncate">{n.label || n.role || 'Drive'}</span>
                  </span>
                ))}
              </div>
              <p className="mt-2 font-mono text-2xs text-white/50">Looking every few seconds. Showing the thumbnail meanwhile.</p>
            </div>
            <button type="button" onClick={() => setDismissed(true)} aria-label="Dismiss" title="Dismiss (Esc)" className={`${iconBtn} -mr-2 -mt-2 h-8 w-8`}>
              <Icon name="x" size={14} />
            </button>
          </div>
        )}

        {failed && (
          <p className="pointer-events-none absolute inset-x-0 bottom-3 text-center font-mono text-2xs text-white/60">
            No large preview in this file (drive not connected?). Showing the thumbnail.
          </p>
        )}

        <button type="button" onClick={() => onStep(-1)} aria-label="Previous photo" title="Previous (←)" className={`${iconBtn} absolute left-3 top-1/2 hidden -translate-y-1/2 bg-black/40 sm:flex`}>
          <Icon name="back" size={20} />
        </button>
        <button type="button" onClick={() => onStep(1)} aria-label="Next photo" title="Next (→)" className={`${iconBtn} absolute right-3 top-1/2 hidden -translate-y-1/2 bg-black/40 sm:flex`}>
          <Icon name="chevron" size={20} />
        </button>

        <AnimatePresence>
          {info && (
            <motion.aside
              key="info"
              initial={{ opacity: 0, y: reduced ? 0 : 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: reduced ? 0 : 12 }}
              transition={{ duration: dur }}
              aria-label="Photo details"
              className="absolute inset-x-3 bottom-3 z-10 max-h-[45%] overflow-y-auto rounded-2xl border border-white/10 bg-black/70 p-4 backdrop-blur-xl sm:inset-x-auto sm:bottom-4 sm:left-4 sm:w-80"
            >
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 font-mono text-xs">
                {rows.map(([k, v, href]) => (
                  <div key={k} className="contents">
                    <dt className="text-white/40">{k}</dt>
                    <dd className="break-words">
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer" title="Open in Google Maps" className="underline decoration-white/30 underline-offset-2 hover:text-accent">
                          {v}
                        </a>
                      ) : (
                        v
                      )}
                    </dd>
                  </div>
                ))}
                {!rows.length && <p className="col-span-2 text-white/60">Nothing recorded for this photo yet.</p>}
              </dl>
            </motion.aside>
          )}
        </AnimatePresence>
      </div>

      <AnimatePresence>
        {zoomAt && loaded && img && <ZoomView key="zoom" src={img.url} ratio={ratio} natural={natural || 2000} at={zoomAt} reduced={reduced} onClose={() => setZoomAt(null)} />}
      </AnimatePresence>

      <footer className="flex h-11 shrink-0 items-center justify-center gap-4 font-mono text-2xs text-white/40">
        <span className="sm:hidden">swipe to step · tap to zoom</span>
        <span className="hidden items-center gap-1.5 sm:flex">
          <span className={kbd}>←</span>
          <span className={kbd}>→</span> step
        </span>
        <span className="hidden items-center gap-1.5 sm:flex">
          <span className={kbd}>click</span> zoom
        </span>
        <span className="hidden items-center gap-1.5 sm:flex">
          <span className={kbd}>I</span> info
        </span>
        <span className="hidden items-center gap-1.5 sm:flex">
          <span className={kbd}>F</span> full screen
        </span>
        <span className="hidden items-center gap-1.5 sm:flex">
          <span className={kbd}>esc</span> close
        </span>
      </footer>
    </motion.div>,
    document.body,
  );
}
