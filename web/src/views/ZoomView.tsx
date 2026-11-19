import { motion } from 'framer-motion';
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Icon } from '../components/bits';
import { clamp } from '../lib/photos';

const MAX_Z = 8;

/**
 * Zoom and pan inside the lightbox (rv-20), after the user's website ZoomView. The photo is laid out at its fitted
 * ("contain") size and scaled with a transform, so nothing reflows. It opens at 100% (one image pixel per screen
 * pixel) on the spot that was clicked; drag pans, the wheel or +/- change the level, and a click (or Esc, or the
 * Fit button) goes back to fitting. `at` is where the click landed, 0 to 1 across the viewport.
 */
export function ZoomView({
  src,
  ratio,
  natural,
  at,
  reduced,
  onClose,
}: {
  src: string;
  ratio: number;
  /** The image's natural width in pixels, for the 100% level. */
  natural: number;
  at: { x: number; y: number };
  reduced: boolean;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ w: window.innerWidth, h: window.innerHeight });
  const fw = Math.min(view.w, view.h * ratio);
  const fh = fw / ratio;
  const full = Math.max(1.5, natural / fw); // 100%: natural pixels
  const [z, setZ] = useState(full);
  const [focus, setFocus] = useState(() => {
    // the clicked spot as a fraction of the photo
    const w = Math.min(window.innerWidth, window.innerHeight * ratio);
    const h = w / ratio;
    return {
      x: clamp((at.x * window.innerWidth - (window.innerWidth - w) / 2) / w, 0, 1),
      y: clamp((at.y * window.innerHeight - (window.innerHeight - h) / 2) / h, 0, 1),
    };
  });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; moved: number; fx: number; fy: number } | null>(null);

  const maxX = Math.max(0, (fw * z - view.w) / 2);
  const maxY = Math.max(0, (fh * z - view.h) / 2);
  const tx = -(focus.x - 0.5) * 2 * maxX;
  const ty = -(focus.y - 0.5) * 2 * maxY;

  useEffect(() => {
    const onResize = () => setView({ w: window.innerWidth, h: window.innerHeight });
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZ((c) => clamp(c * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), 1, MAX_Z));
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '+' || e.key === '=') setZ((c) => clamp(c * 1.3, 1, MAX_Z));
      else if (e.key === '-' || e.key === '_') setZ((c) => clamp(c / 1.3, 1, MAX_Z));
      else return;
      e.preventDefault();
    };
    window.addEventListener('resize', onResize);
    const el = box.current;
    el?.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', onResize);
      el?.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  const down = (e: ReactPointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, moved: 0, fx: focus.x, fy: focus.y };
    setDragging(true);
  };
  const move = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    d.moved = Math.max(d.moved, Math.abs(dx) + Math.abs(dy));
    // dragging the photo right shows more of its left edge
    setFocus({
      x: maxX > 0 ? clamp(d.fx - dx / (2 * maxX), 0, 1) : 0.5,
      y: maxY > 0 ? clamp(d.fy - dy / (2 * maxY), 0, 1) : 0.5,
    });
  };
  const up = () => {
    setDragging(false);
    // a click without a drag goes back to fitting
    if (drag.current && drag.current.moved < 5) onClose();
    drag.current = null;
  };

  const pill = 'flex h-10 min-w-10 items-center justify-center rounded-full px-3 font-mono text-sm text-white/90 transition-colors hover:bg-white/10 disabled:opacity-30';
  return (
    <motion.div
      ref={box}
      role="group"
      aria-label="Zoomed view"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduced ? 0 : 0.16 }}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      className={`absolute inset-0 z-20 flex touch-none items-center justify-center overflow-hidden bg-black ${dragging ? 'cursor-grabbing' : 'cursor-zoom-out'}`}
    >
      <img
        src={src}
        alt=""
        draggable={false}
        decoding="async"
        className="max-w-none shrink-0 select-none"
        style={{
          width: fw,
          height: fh,
          transform: `translate(${tx}px, ${ty}px) scale(${z})`,
          transition: reduced || dragging ? 'none' : 'transform 160ms ease-out',
        }}
      />
      <div
        onPointerDown={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        className="absolute bottom-4 left-1/2 flex -translate-x-1/2 cursor-default items-center gap-0.5 rounded-full border border-white/10 bg-black/70 p-1 backdrop-blur-xl"
      >
        <button type="button" className={pill} aria-label="Zoom out" disabled={z <= 1} onClick={() => setZ((c) => clamp(c / 1.4, 1, MAX_Z))}>
          −
        </button>
        <span className="tabular w-14 text-center font-mono text-2xs text-white/60" aria-live="polite">
          {Math.round(((z * fw) / natural) * 100)}%
        </span>
        <button type="button" className={pill} aria-label="Zoom in" disabled={z >= MAX_Z} onClick={() => setZ((c) => clamp(c * 1.4, 1, MAX_Z))}>
          +
        </button>
        <button type="button" className={pill} aria-label="Fit to screen" title="Fit (Esc)" onClick={onClose}>
          <Icon name="x" size={16} />
        </button>
      </div>
    </motion.div>
  );
}
