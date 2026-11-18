import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import { Palette, type Choice, type Preset, type Theme } from './palette';
import { DEFAULT_PRESET, PRESETS, SCHEME_TOKENS } from './presets';
import { readPref, usePref, writePref } from './prefs';

/**
 * React binding for the palette engine, as on mkh4n-website. Components only use token colours (bg-surface,
 * text-ink, text-accent…), and the engine writes those tokens onto <html>.
 * A ?theme=<id> or ?bg=…&accent=… link wins over the stored choice, so a palette can be shared.
 */

interface ThemeCtx {
  theme: Theme;
  presets: Preset[];
  select: (id: string) => void;
  customize: (patch: { bg?: string; accent?: string }) => void;
  reset: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);
export const useTheme = (): ThemeCtx => {
  const v = useContext(Ctx);
  if (!v) throw new Error('useTheme outside ThemeProvider');
  return v;
};

const NONE: Choice = { preset: null, custom: {} };

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = usePref<Choice>('pv:palette', NONE);

  useLayoutEffect(() => {
    const url = Palette.parseQuery(window.location.search, PRESETS);
    if (url) setChoice({ preset: url.preset, custom: url.custom });
  }, [setChoice]);

  const theme = useMemo(
    () => Palette.resolveTheme({ presets: PRESETS, choice, defaultId: DEFAULT_PRESET, schemeTokens: SCHEME_TOKENS })!,
    [choice],
  );
  useLayoutEffect(() => Palette.applyTheme(theme), [theme]);

  const select = useCallback((id: string) => setChoice({ preset: id, custom: {} }), [setChoice]);
  const customize = useCallback(
    (patch: { bg?: string; accent?: string }) => setChoice((c) => ({ ...c, custom: { ...c.custom, ...patch } })),
    [setChoice],
  );
  const reset = useCallback(() => setChoice(NONE), [setChoice]);
  const value = useMemo(() => ({ theme, presets: PRESETS, select, customize, reset }), [theme, select, customize, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// ---------------------------------------------------------------- dev mode

/**
 * Dev mode shows the palette picker. It is on by default (this is a local,
 * single-user tool); ?dev=0 turns it off in this browser and ?dev=1 back on.
 */
const DEV_KEY = 'pv:dev';
const listeners = new Set<() => void>();
const flag = new URLSearchParams(window.location.search).get('dev');
if (flag === '1' || flag === '0') writePref(DEV_KEY, flag === '1');

export const setDevMode = (on: boolean): void => {
  writePref(DEV_KEY, on);
  listeners.forEach((fn) => fn());
};
const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};
export const useDevMode = (): boolean => useSyncExternalStore(subscribe, () => readPref(DEV_KEY, true));
