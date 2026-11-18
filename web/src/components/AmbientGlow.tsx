import { useTheme } from '../lib/theme';
import { Palette } from '../lib/palette';

/**
 * Ambient glow, from mkh4n-website src/components/AmbientGlow.js: three large, heavily blurred blobs behind the
 * page, coloured from the palette's accent. The layer is capped at a low opacity so text stays readable, brightens
 * a little while something runs, drifts slowly, and is not drawn on light palettes (where it looks like a stain).
 * Colour changes are a CSS transition: no canvas, no animation loop.
 */
export function AmbientGlow({ active }: { active: boolean }) {
  const { theme } = useTheme();
  if (theme.scheme === 'light') return null;
  const accent = theme.tokens.accent ?? '#7daafa';
  const card = theme.tokens.card ?? '#a78bfa';
  const colors = [accent, Palette.mix(accent, card, 0.6), Palette.mix(accent, theme.tokens.go ?? '#4ade80', 0.35)];
  const blob = 'absolute rounded-full transition-[background-color] duration-[800ms]';
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden transition-opacity duration-1000"
      style={{ opacity: active ? 0.2 : 0.13 }}
    >
      <div className="glow-drift absolute inset-0">
        <div className={blob} style={{ width: '70vmax', height: '70vmax', left: '-22vmax', top: '-30vmax', backgroundColor: colors[0], filter: 'blur(110px)' }} />
        <div className={blob} style={{ width: '58vmax', height: '58vmax', right: '-26vmax', top: '16vh', backgroundColor: colors[1], filter: 'blur(120px)' }} />
        <div className={blob} style={{ width: '64vmax', height: '64vmax', left: '10vw', bottom: '-44vmax', backgroundColor: colors[2], filter: 'blur(130px)' }} />
      </div>
    </div>
  );
}
