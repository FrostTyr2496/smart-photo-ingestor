import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { Icon } from '../components/bits';
import { fmtCoord, fitBox, mapsUrl, placeLabel, wrapIndex, type PhotoGroup } from '../lib/photos';
import { ZoomView } from './ZoomView';

const preview = (id: number): string => `/api/groups/${id}/preview`;
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

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
    setZoomAt(null);
  }, [g.group_id]);

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
        if (zoomAt) setZoomAt(null);
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
  }, [onClose, onStep, zoomAt]);

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
      if (n && n.group_id !== g.group_id && n.volume_id) new Image().src = preview(n.group_id);
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

  return (
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
            className={`pointer-events-none absolute inset-0 h-full w-full select-none object-contain ${loaded ? 'opacity-0' : failed ? 'opacity-100' : 'opacity-100 blur-sm'} transition-opacity duration-200`}
          />
          {!failed && (
            <img
              src={preview(g.group_id)}
              alt={title}
              draggable={false}
              decoding="async"
              onLoad={(e) => {
                const im = e.currentTarget;
                if (im.naturalWidth && im.naturalHeight) {
                  setRatio(im.naturalWidth / im.naturalHeight);
                  setNatural(im.naturalWidth);
                }
                setLoaded(true);
              }}
              onError={() => setFailed(true)}
              className={`pointer-events-none absolute inset-0 h-full w-full select-none object-contain transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
            />
          )}
        </motion.div>

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
        {zoomAt && loaded && <ZoomView key="zoom" src={preview(g.group_id)} ratio={ratio} natural={natural || 2000} at={zoomAt} reduced={reduced} onClose={() => setZoomAt(null)} />}
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
    </motion.div>
  );
}
