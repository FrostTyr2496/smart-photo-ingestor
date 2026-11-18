import { useEffect, useState } from 'react';

/** Hash routes, so a reload keeps the open view: #/ (tasks) and #/drive/<volume id>. */
export type Route = { view: 'home' } | { view: 'drive'; id: string };

export function parseRoute(hash: string): Route {
  const m = /^#\/drive\/(.+)$/.exec(hash);
  return m && m[1] ? { view: 'drive', id: decodeURIComponent(m[1]) } : { view: 'home' };
}

export const hrefFor = (r: Route): string => (r.view === 'drive' ? `#/drive/${encodeURIComponent(r.id)}` : '#/');

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => {
      setRoute(parseRoute(window.location.hash));
      window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

/** Screens not ported yet open in the classic UI (D-059, until rv-6). */
export const CLASSIC = '/';
