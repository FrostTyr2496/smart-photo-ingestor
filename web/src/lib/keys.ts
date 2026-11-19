/** True when a key press came from somewhere the user is typing, so a bare-key shortcut must not fire. */
export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return el.isContentEditable === true || el.getAttribute?.('contenteditable') === 'true' || el.getAttribute?.('contenteditable') === '';
}

/** True while a modal dialog (the lightbox) is open. */
export function modalOpen(doc: Document = document): boolean {
  return doc.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}
