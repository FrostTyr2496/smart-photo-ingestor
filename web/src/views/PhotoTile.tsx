import { memo, useState, type MouseEvent } from 'react';
import { Icon } from '../components/bits';
import { clockTime, fullOffline, offlineTitle, type PhotoGroup } from '../lib/photos';

/** Photos fetched per request within a section (a day, a shoot). */
export const PAGE = 300;

/** One section's loaded photos (a day, or a shoot in the trips view). */
export interface DayState {
  groups: PhotoGroup[];
  loading: boolean;
  error: string;
}

export const Tile = memo(function Tile({ g, on, related, flash, connected, offline, onTile, onOpen, onMenu }: { g: PhotoGroup; on: boolean; related: boolean; flash: boolean; connected: boolean; offline: boolean; onTile: (g: PhotoGroup, e: MouseEvent) => void; onOpen: (g: PhotoGroup) => void; onMenu: (g: PhotoGroup, e: MouseEvent) => void }) {
  const [failed, setFailed] = useState(false);
  const name = g.canonical_stem || `Photo ${g.group_id}`;
  return (
    <button
      type="button"
      aria-pressed={on}
      data-gid={g.group_id}
      title={`${name}${g.capture_time ? ` · ${g.capture_time.replace('T', ' ')}` : ''}${g.place_name ? ` · ${g.place_name}` : ''}`}
      onClick={(e) => onTile(g, e)}
      onDoubleClick={() => onOpen(g)}
      onContextMenu={(e) => onMenu(g, e)}
      onKeyDown={(e) => {
        if (e.key === ' ' && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
          e.preventDefault(); // Space opens the viewer; it no longer toggles the selection
          onOpen(g);
        }
      }}
      onKeyUp={(e) => e.key === ' ' && e.preventDefault()}
      className={`group relative aspect-square overflow-hidden rounded-lg bg-ink/5 ${related && !on ? '' : 'outline-none'} ring-offset-2 ring-offset-bg transition-shadow focus-visible:ring-2 focus-visible:ring-accent/60 ${flash ? 'ring-[5px] ring-accent motion-safe:animate-[pulse_0.7s_ease-in-out_2]' : on ? 'ring-[3px] ring-accent' : related ? 'outline-dashed outline-2 outline-accent/70 outline-offset-[-2px]' : ''}`}
    >
      {failed || !g.volume_id ? (
        <span className="flex h-full w-full flex-col items-center justify-center gap-1 p-2 text-center text-2xs text-faint">
          <Icon name="image" size={18} />
          <span className="max-w-full truncate font-mono">{name}</span>
          <span>{!g.volume_id ? 'No file indexed' : connected ? 'No preview' : 'Drive not connected'}</span>
        </span>
      ) : (
        <img
          src={`/api/groups/${g.group_id}/thumb`}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className={`h-full w-full object-cover transition-transform duration-300 ${on ? 'scale-[0.94] rounded-md' : 'group-hover:scale-[1.03]'}`}
        />
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/60 to-transparent px-1.5 pb-1 pt-4 text-2xs text-white opacity-0 transition-opacity group-hover:opacity-100">
        <span className="truncate font-mono">{name}</span>
        <span className="tabular">{clockTime(g.capture_time)}</span>
      </span>
      <span className="pointer-events-none absolute right-1 top-1 flex gap-0.5">
        {g.lat !== null && (
          <span className="rounded-full bg-black/45 p-0.5 text-white" title="Has a location">
            <Icon name="pin" size={11} />
          </span>
        )}
        {g.dirty && (
          <span className="rounded-full bg-black/45 p-0.5 text-white" title="Edited; sidecar not written yet">
            <Icon name="edit" size={11} />
          </span>
        )}
      </span>
      {offline && !on && (
        <span className="pointer-events-none absolute bottom-1 left-1 rounded-full bg-black/55 p-0.5 text-white/90 group-hover:opacity-0" title={offlineTitle(g.held_on)}>
          <Icon name="driveOff" size={11} />
        </span>
      )}
      {on && (
        <span className="absolute left-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-on-accent shadow">
          <Icon name="check" size={12} />
        </span>
      )}
    </button>
  );
});
