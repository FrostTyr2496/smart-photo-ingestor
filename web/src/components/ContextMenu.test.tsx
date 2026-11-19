import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { closeMenu, menuStore, openMenu } from '../lib/menu';
import { ContextMenuHost } from './ContextMenu';

const ev = () => ({ clientX: 10, clientY: 10, shiftKey: false, currentTarget: null, preventDefault: () => undefined, stopPropagation: () => undefined });

describe('ContextMenuHost (rv-28)', () => {
  it('runs an item with the keyboard, skipping disabled ones, then closes', () => {
    const show = vi.fn();
    const off = vi.fn();
    render(<ContextMenuHost />);
    act(() => openMenu(ev(), [{ label: 'Disabled', disabled: true, onSelect: off }, 'sep', { label: 'Show in All Photos', onSelect: show }], 'DSC_0001.NEF'));
    const menu = screen.getByRole('menu');
    expect(screen.getByText('DSC_0001.NEF')).toBeTruthy();
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    fireEvent.keyDown(menu, { key: 'Enter' });
    expect(show).toHaveBeenCalledOnce();
    expect(off).not.toHaveBeenCalled();
    expect(menuStore.get()).toBeNull();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('closes on Esc and on a press outside', () => {
    render(<ContextMenuHost />);
    act(() => openMenu(ev(), [{ label: 'A', onSelect: () => undefined }]));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    act(() => openMenu(ev(), [{ label: 'A', onSelect: () => undefined }]));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
    act(() => closeMenu());
  });

  it('runs an item on click', () => {
    const fn = vi.fn();
    render(<ContextMenuHost />);
    act(() => openMenu(ev(), [{ label: 'Copy path on the drive', onSelect: fn }]));
    fireEvent.click(screen.getByRole('menuitem', { name: /Copy path/ }));
    expect(fn).toHaveBeenCalledOnce();
  });
});
