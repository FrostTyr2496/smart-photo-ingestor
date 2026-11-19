import type * as L from 'leaflet';
import { useEffect, useRef, useState } from 'react';
import { loadLeaflet } from '../../components/MapPicker';

/** A read-only map of a shoot's points (sh-3). Leaflet and the tiles load only once the card scrolls into view. */
export function MiniMap({ points }: { points: [number, number][] }) {
  const box = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el || points.length === 0) return;
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && (setSeen(true), io.disconnect()), { rootMargin: '120px' });
    io.observe(el);
    return () => io.disconnect();
  }, [points.length]);

  useEffect(() => {
    if (!seen || !box.current || points.length === 0) return;
    let alive = true;
    let map: L.Map | null = null;
    loadLeaflet()
      .then((Lf) => {
        if (!alive || !box.current) return;
        map = Lf.map(box.current, {
          zoomControl: false,
          attributionControl: false,
          dragging: false,
          scrollWheelZoom: false,
          doubleClickZoom: false,
          boxZoom: false,
          keyboard: false,
          touchZoom: false,
        });
        Lf.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);
        for (const [lat, lon] of points) {
          Lf.circleMarker([lat, lon], { radius: 4, weight: 1.5, color: 'rgb(var(--c-bg))', fillColor: 'rgb(var(--c-maybe))', fillOpacity: 0.9, interactive: false }).addTo(map);
        }
        if (points.length === 1) map.setView(points[0]!, 11);
        else map.fitBounds(Lf.latLngBounds(points), { padding: [12, 12], maxZoom: 13 });
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      map?.remove();
    };
  }, [seen, points]);

  if (points.length === 0) {
    return <div className="flex h-28 items-center justify-center rounded-lg border border-dashed border-line text-2xs text-faint">No locations</div>;
  }
  if (failed) return <div className="flex h-28 items-center justify-center rounded-lg border border-line text-2xs text-faint">Map unavailable</div>;
  return <div ref={box} className="pv-map h-28 w-full overflow-hidden rounded-lg border border-line bg-ink/5" aria-label="Map of this shoot's photo locations" />;
}
