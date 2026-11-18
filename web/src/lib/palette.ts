// Typed access to the vendored palette engine (./palette/palette-core.js, v1.0.0, copied unchanged from
// mkh4n-website src/lib/palette/). It is UMD; vite.config.ts converts it as CommonJS, so its exports arrive as the
// default import in dev, in the build and in vitest alike.
import core from './palette/palette-core.js';

export type Tokens = Record<string, string>;
export interface Preset {
  id: string;
  name: string;
  bg: string;
  accent: string;
  tokens?: Tokens;
}
export interface Choice {
  preset: string | null;
  custom: { bg?: string; accent?: string };
}
export interface Theme {
  id: string;
  presetId: string;
  name: string;
  isCustom: boolean;
  scheme: 'dark' | 'light';
  tokens: Tokens;
}

interface PaletteApi {
  VERSION: string;
  contrast(a: string, b: string): number;
  mix(a: string, b: string, t: number): string;
  preview(preset: Preset, schemeTokens?: Record<string, Tokens>): Tokens;
  resolveTheme(opts: { presets: Preset[]; choice: Choice; defaultId: string | null; schemeTokens?: Record<string, Tokens> }): Theme | null;
  applyTheme(theme: Theme | null, options?: Record<string, unknown>): void;
  parseQuery(search: string, presets: Preset[]): Choice | null;
  presetLine(theme: Theme): string;
}

export const Palette = core as unknown as PaletteApi;
