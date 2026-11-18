import type { Preset, Tokens } from './palette';

/**
 * Palettes the dev picker offers. The first is the default: Argus's night palette. To keep a palette you made in
 * the picker, press Copy there and paste the line into this list (then rebuild).
 */
export const PRESETS: Preset[] = [
  {
    id: 'argus',
    name: 'Argus · Night',
    bg: '#070a12',
    accent: '#7daafa',
    tokens: { surface: '#0e131f', raised: '#161d2d', ink: '#eaeef5', ivory: '#f4f6fa', muted: '#96a0b2', faint: '#7e889c' },
  },
  {
    id: 'obsidian',
    name: 'Gallery Obsidian',
    bg: '#0b0c0e',
    accent: '#e3b576',
    tokens: { surface: '#111215', raised: '#18191d', ink: '#ededef', ivory: '#f3eee3', muted: '#96969e', faint: '#686870' },
  },
  { id: 'midnight', name: 'Midnight', bg: '#090c14', accent: '#7fb2ff' },
  { id: 'aurora', name: 'Aurora', bg: '#070d10', accent: '#5eead4' },
  { id: 'nebula', name: 'Nebula', bg: '#0c0a14', accent: '#c4a1ff' },
  { id: 'forest', name: 'Forest · Moss', bg: '#0a0f0c', accent: '#a8c97f' },
  { id: 'forest-ember', name: 'Forest · Ember', bg: '#0c110d', accent: '#e0a458' },
  { id: 'pine', name: 'Pine & Fog', bg: '#0d1414', accent: '#8fd0c0' },
  { id: 'sandstone', name: 'Sandstone Dusk', bg: '#120d0b', accent: '#e07a4f' },
  { id: 'void', name: 'Void', bg: '#000000', accent: '#7daafa' },
  { id: 'paper', name: 'Paper (light)', bg: '#f4f1ea', accent: '#2f5fb3', tokens: { surface: '#fbf9f4', raised: '#ffffff' } },
];

export const DEFAULT_PRESET = 'argus';

/** Status colours stay fixed per scheme whatever the palette, so "done" and "failed" always read the same. */
export const SCHEME_TOKENS: Record<'dark' | 'light', Tokens> = {
  dark: { go: '#4ade80', maybe: '#facc15', err: '#f87171', skip: '#94a3b8', card: '#a78bfa', master: '#60a5fa' },
  light: { go: '#16803d', maybe: '#a16207', err: '#b91c1c', skip: '#475569', card: '#6d28d9', master: '#1d4ed8' },
};
