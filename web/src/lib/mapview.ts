export interface LatLon {
  lat: number;
  lon: number;
}
export type MapView = { kind: 'center'; lat: number; lon: number; zoom: number } | { kind: 'fit'; points: LatLon[] } | { kind: 'world' };

/** Street level around a pin; else the one point, or a fit of several; else the whole world. */
export function initialView(pin: LatLon | null, points: LatLon[], pinZoom = 16): MapView {
  if (pin) return { kind: 'center', lat: pin.lat, lon: pin.lon, zoom: pinZoom };
  if (points.length === 1) return { kind: 'center', lat: points[0]!.lat, lon: points[0]!.lon, zoom: 14 };
  if (points.length > 1) return { kind: 'fit', points };
  return { kind: 'world' };
}
