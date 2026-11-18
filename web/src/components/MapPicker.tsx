import type * as L from 'leaflet';
import { useEffect, useRef, useState } from 'react';

// The location map (rv-7, user-approved Leaflet + OpenStreetMap tiles, 2026-11-18). Leaflet is loaded only when a map
// is shown, so the rest of the app never fetches it and no tile is requested until the editor is open. Tiles show
// the area being viewed to OpenStreetMap; no photo, path or catalog data is sent.

type Leaflet = typeof L;
let leafletPromise: Promise<Leaflet> | null = null;
function loadLeaflet(): Promise<Leaflet> {
  leafletPromise ??= Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')]).then(([m]) => (m.default ?? m) as Leaflet);
  return leafletPromise;
}

export interface MapPoint {
  lat: number;
  lon: number;
}

/** A dot for each existing location in the selection, a pin for the pending one; click or drag sets the pending one. */
export function MapPicker({ points, pin, focus, onPick }: { points: MapPoint[]; pin: MapPoint | null; focus: number; onPick: (p: MapPoint) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const lib = useRef<Leaflet | null>(null);
  const dots = useRef<L.LayerGroup | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const pick = useRef(onPick);
  pick.current = onPick;
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    loadLeaflet()
      .then((Lf) => {
        if (!alive || !box.current) return;
        lib.current = Lf;
        const m = Lf.map(box.current, { zoomControl: true, attributionControl: true, worldCopyJump: true }).setView([20, 0], 2);
        Lf.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19,
          attribution: '© OpenStreetMap contributors',
        }).addTo(m);
        m.on('click', (e: L.LeafletMouseEvent) => pick.current({ lat: e.latlng.lat, lon: e.latlng.lng }));
        dots.current = Lf.layerGroup().addTo(m);
        map.current = m;
        setReady(true);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // The selection's existing locations, as small dots.
  useEffect(() => {
    const Lf = lib.current;
    if (!ready || !Lf || !dots.current) return;
    dots.current.clearLayers();
    for (const p of points.slice(0, 500)) {
      Lf.circleMarker([p.lat, p.lon], { radius: 5, weight: 1.5, color: 'rgb(var(--c-bg))', fillColor: 'rgb(var(--c-accent))', fillOpacity: 0.85, interactive: false }).addTo(dots.current);
    }
  }, [ready, points]);

  // The pending location, as a draggable pin (a styled div, so no marker images are needed).
  useEffect(() => {
    const Lf = lib.current;
    const m = map.current;
    if (!ready || !Lf || !m) return;
    if (!pin) {
      marker.current?.remove();
      marker.current = null;
      return;
    }
    if (marker.current) marker.current.setLatLng([pin.lat, pin.lon]);
    else {
      const icon = Lf.divIcon({ className: 'pv-pin', html: '<span></span>', iconSize: [22, 22], iconAnchor: [11, 22] });
      marker.current = Lf.marker([pin.lat, pin.lon], { icon, draggable: true, keyboard: false }).addTo(m);
      marker.current.on('dragend', () => {
        const ll = marker.current!.getLatLng();
        pick.current({ lat: ll.lat, lon: ll.lng });
      });
    }
  }, [ready, pin]);

  // Fit the view when the selection changes (focus bumps), or fly to a pin set from a search or suggestion.
  useEffect(() => {
    const Lf = lib.current;
    const m = map.current;
    if (!ready || !Lf || !m) return;
    m.invalidateSize();
    const all = pin ? [pin] : points;
    if (all.length === 1) m.setView([all[0]!.lat, all[0]!.lon], Math.max(m.getZoom(), 12));
    else if (all.length > 1) m.fitBounds(Lf.latLngBounds(all.map((p) => [p.lat, p.lon] as [number, number])), { padding: [24, 24], maxZoom: 14 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focus]);

  if (failed) return <p className="rounded-lg border border-line bg-ink/5 p-3 text-xs text-muted">The map could not load (offline?). Use a suggestion or a place search.</p>;
  return <div ref={box} className="pv-map h-52 w-full overflow-hidden rounded-lg border border-line bg-ink/5" aria-label="Map: click to set the location" />;
}
