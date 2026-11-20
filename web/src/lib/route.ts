import { useEffect, useState } from 'react';

/** Hash routes, so a reload keeps the open view. */
export type Route =
  | { view: 'home'; task?: string }
  | { view: 'drive'; id: string }
  | { view: 'cards'; id: string | null }
  | { view: 'setup'; key: string }
  | { view: 'photos'; missing?: 'gps' | 'creator' | 'keywords' | 'any'; day?: string; photo?: number; shoot?: string }
  | { view: 'actions' }
  | { view: 'review' }
  | { view: 'settings' };

const MISSING_KEYS = ['any', 'gps', 'creator', 'keywords'] as const;

/** `#/photos?missing=gps&day=2023-09-09` (act-3): Photos filtered to a "Missing …" choice and scrolled to a day. */
function photosRoute(query: string): Route {
  const p = new URLSearchParams(query);
  const r: Route = { view: 'photos' };
  const missing = MISSING_KEYS.find((k) => k === p.get('missing'));
  if (missing) r.missing = missing;
  const shoot = p.get('shoot');
  // `#/photos?shoot=<key>` (rv-47): the Trips view scrolled to that shoot (`s7` or a proposal id).
  if (shoot && /^[\w:.-]{1,80}$/.test(shoot)) {
    r.shoot = shoot;
    return r;
  }
  const day = p.get('day');
  const photo = p.get('photo');
  // `#/photos?photo=<group id>&day=<day>` (rv-27): Show in Photos from a file list; the day may be "undated".
  if (photo && /^\d+$/.test(photo) && day && (day === 'undated' || /^\d{4}-\d{2}-\d{2}$/.test(day))) {
    r.photo = Number(photo);
    r.day = day;
    return r;
  }
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) r.day = day;
  return r;
}

export function parseRoute(rawHash: string): Route {
  const q = rawHash.indexOf('?');
  const hash = q < 0 ? rawHash : rawHash.slice(0, q);
  if (hash === '#/review/shoots') return { view: 'review' };
  // `#/?task=t12` (rv-37): Tasks, scrolled to that run (`t<id>` a UI task, `p<pid>` a terminal run).
  if (hash === '#/' || hash === '#' || hash === '') {
    const task = new URLSearchParams(q < 0 ? '' : rawHash.slice(q + 1)).get('task');
    return task && /^[tp]\d+$/.test(task) ? { view: 'home', task } : { view: 'home' };
  }
  if (hash === '#/photos') return photosRoute(q < 0 ? '' : rawHash.slice(q + 1));
  const m = /^#\/(drive|cards|setup|photos|settings|actions)(?:\/(.+))?$/.exec(hash);
  if (!m) return { view: 'home' };
  const id = m[2] ? decodeURIComponent(m[2]) : null;
  if (m[1] === 'drive' && id) return { view: 'drive', id };
  if (m[1] === 'setup' && id) return { view: 'setup', key: id };
  if (m[1] === 'cards') return { view: 'cards', id };
  if (m[1] === 'photos') return { view: 'photos' };
  if (m[1] === 'actions') return { view: 'actions' };
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
    case 'photos': {
      const p = new URLSearchParams();
      if (r.missing) p.set('missing', r.missing);
      if (r.shoot) p.set('shoot', r.shoot);
      if (r.photo) p.set('photo', String(r.photo));
      if (r.day) p.set('day', r.day);
      const q = p.toString();
      return q ? `#/photos?${q}` : '#/photos';
    }
    case 'review':
      return '#/review/shoots';
    case 'actions':
      return '#/actions';
    case 'settings':
      return '#/settings';
    default:
      return r.task ? `#/?task=${encodeURIComponent(r.task)}` : '#/';
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
