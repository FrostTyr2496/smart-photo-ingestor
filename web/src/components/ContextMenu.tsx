import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { closeMenu, menuStore, nextEnabled, placeMenu, type MenuAction } from '../lib/menu';
import { Icon } from './bits';

/**
 * The one right-click menu (rv-28), mounted once in the app shell. Arrow keys, Home/End, Enter and Esc work; a click
 * outside, scrolling, resizing, leaving the window or a route change closes it.
 */
export function ContextMenuHost() {
  const menu = useSyncExternalStore(menuStore.subscribe, menuStore.get);
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [active, setActive] = useState(-1);

  useLayoutEffect(() => {
    if (!menu || !box.current) {
      setPos(null);
      return;
    }
    const r = box.current.getBoundingClientRect();
    setPos(placeMenu(menu.x, menu.y, r.width, r.height, window.innerWidth, window.innerHeight));
    setActive(-1);
    box.current.focus({ preventScroll: true });
  }, [menu]);

  useEffect(() => {
    if (!menu) return undefined;
    const outside = (e: Event) => {
      if (box.current && e.target instanceof Node && box.current.contains(e.target)) return;
      closeMenu();
    };
    const close = () => closeMenu();
    // A right-click elsewhere reopens through its own handler; capture the press so this one closes first.
    window.addEventListener('pointerdown', outside, true);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    window.addEventListener('blur', close);
    window.addEventListener('hashchange', close);
    return () => {
      window.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      window.removeEventListener('blur', close);
      window.removeEventListener('hashchange', close);
    };
  }, [menu]);

  if (!menu) return null;
  const run = (it: MenuAction) => {
    if (it.disabled) return;
    closeMenu();
    it.onSelect();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation(); // Esc closes the menu only, not the selection under it
      closeMenu();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => nextEnabled(menu.items, i, e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      setActive(e.key === 'Home' ? nextEnabled(menu.items, -1, 1) : nextEnabled(menu.items, 0, -1));
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const it = menu.items[active];
      if (it && it !== 'sep') run(it);
    }
  };

  return createPortal(
    <div
      ref={box}
      role="menu"
      aria-label={menu.title ?? 'Actions'}
      tabIndex={-1}
      onKeyDown={onKey}
      onContextMenu={(e) => e.preventDefault()}
      className="fixed z-[120] min-w-[13rem] max-w-[22rem] rounded-xl border border-line bg-surface/95 p-1 text-sm shadow-xl outline-none backdrop-blur"
      style={pos ? { left: pos.left, top: pos.top } : { left: menu.x, top: menu.y, visibility: 'hidden' }}
    >
      {menu.title && <p className="truncate px-2.5 pb-1 pt-1.5 font-mono text-2xs text-faint" title={menu.title}>{menu.title}</p>}
      {menu.items.map((it, i) =>
        it === 'sep' ? (
          <div key={`sep-${i}`} role="separator" className="mx-1.5 my-1 h-px bg-line" />
        ) : (
          <button
            key={`${it.label}-${i}`}
            type="button"
            role="menuitem"
            disabled={it.disabled}
            onMouseEnter={() => setActive(it.disabled ? -1 : i)}
            onClick={() => run(it)}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left disabled:cursor-default disabled:text-faint ${active === i ? 'bg-accent/15 text-ink' : 'text-ink/90'}`}
          >
            <span className="inline-flex w-4 justify-center text-muted">{it.icon && <Icon name={it.icon} size={13} />}</span>
            <span className="min-w-0 flex-1 truncate">{it.label}</span>
            {it.hint && <span className="text-2xs text-faint">{it.hint}</span>}
          </button>
        ),
      )}
    </div>,
    document.body,
  );
}
