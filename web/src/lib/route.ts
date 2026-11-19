import { useEffect, useState } from 'react';

/** Hash routes, so a reload keeps the open view. */
export type Route =
  | { view: 'home' }
  | { view: 'drive'; id: string }
  | { view: 'cards'; id: string | null }
  | { view: 'setup'; key: string }
  | { view: 'photos' }
  | { view: 'review' }
  | { view: 'settings' };

export function parseRoute(hash: string): Route {
  if (hash === '#/review/shoots') return { view: 'review' };
  const m = /^#\/(drive|cards|setup|photos|settings)(?:\/(.+))?$/.exec(hash);
  if (!m) return { view: 'home' };
  const id = m[2] ? decodeURIComponent(m[2]) : null;
  if (m[1] === 'drive' && id) return { view: 'drive', id };
  if (m[1] === 'setup' && id) return { view: 'setup', key: id };
  if (m[1] === 'cards') return { view: 'cards', id };
  if (m[1] === 'photos') return { view: 'photos' };
  if (m[1] === 'settings') return { view: 'settings' };
  return { view: 'home' };
}

export function hrefFor(r: Route): string {
  switch (r.view) {
    case 'drive':
      return `#/drive/${encodeURIComponent(r.id)}`;
    case 'setup':
      return `#/setup/${encodeURIComponent(r.key)}`;
    case 'cards':
      return r.id ? `#/cards/${encodeURIComponent(r.id)}` : '#/cards';
    case 'photos':
      return '#/photos';
    case 'review':
      return '#/review/shoots';
    case 'settings':
      return '#/settings';
    default:
      return '#/';
  }
}

export const routeKey = (r: Route): string => hrefFor(r);

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
