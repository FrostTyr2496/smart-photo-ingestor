// Drives the user just ejected (rv-43). The unplug notice (rv-38) skips them: an eject is meant, not an accident.
const expected = new Map<string, number>();
const WINDOW_MS = 120_000;

export function expectUnplug(key: string, now = Date.now()): void {
  expected.set(key, now);
}

export function cancelUnplug(key: string): void {
  expected.delete(key);
}

/** True (once) when `key` was ejected in the last two minutes, so its disappearance is expected. */
export function takeExpected(key: string, now = Date.now()): boolean {
  const at = expected.get(key);
  if (at === undefined) return false;
  expected.delete(key);
  return now - at < WINDOW_MS;
}
