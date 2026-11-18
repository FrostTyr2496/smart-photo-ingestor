import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState } from 'react';
import { springs } from '../lib/motion';
import { Palette, type Tokens } from '../lib/palette';
import { DEFAULT_PRESET } from '../lib/presets';
import { setDevMode, useDevMode, useTheme } from '../lib/theme';
import { Icon } from './bits';

/**
 * Owner-only floating tools, bottom-left (the website's DevTools): a palette picker with presets, a free
 * background and accent, and a live contrast table so an accent can be judged against the surfaces it sits on.
 * Hidden with ?dev=0 (or the × button); ?dev=1 brings it back.
 */
export function DevTools() {
  const dev = useDevMode();
  if (!dev) return null;
  return (
    <div
      role="region"
      aria-label="Developer tools"
      className="fixed bottom-3 left-3 z-[60] flex items-center gap-1 rounded-full border border-line-strong bg-bg/80 py-1 pl-2.5 pr-1 shadow-xl shadow-black/40 backdrop-blur-xl"
    >
      <span className="label text-accent">Dev</span>
      <ThemePicker />
      <button
        type="button"
        onClick={() => setDevMode(false)}
        aria-label="Hide developer tools (?dev=1 brings them back)"
        title="Hide (?dev=1 brings it back)"
        className="flex h-7 w-7 items-center justify-center rounded-full text-muted hover:text-ink"
      >
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}

function Swatch({ tokens, size = 'h-4 w-4' }: { tokens: Tokens; size?: string }) {
  return (
    <span aria-hidden="true" className={`relative inline-block shrink-0 rounded-full border border-white/25 ${size}`} style={{ backgroundColor: tokens.bg }}>
      <span className="absolute inset-[28%] rounded-full" style={{ backgroundColor: tokens.accent }} />
    </span>
  );
}

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-1 cursor-pointer items-center gap-2 rounded-lg border border-line px-2 py-1.5 hover:border-line-strong">
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)} className="h-6 w-6 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0" />
      <span className="min-w-0">
        <span className="block text-2xs text-ink">{label}</span>
        <span className="block font-mono text-2xs uppercase text-faint">{value}</span>
      </span>
    </label>
  );
}

// ---------------------------------------------------------------- contrast

type Grade = 'AAA' | 'AA' | 'Large' | 'Low';
const grade = (r: number): Grade => (r >= 7 ? 'AAA' : r >= 4.5 ? 'AA' : r >= 3 ? 'Large' : 'Low');
const GRADE_TONE: Record<Grade, string> = {
  AAA: 'text-go border-go/30',
  AA: 'text-go border-go/30',
  Large: 'text-maybe border-maybe/30',
  Low: 'text-err border-err/40',
};

interface Pair {
  label: string;
  fg: string;
  bg: string;
  /** What a pass means here: body text needs 4.5, large text and UI parts 3. */
  need: number;
}

function pairs(t: Tokens): Pair[] {
  const c = (k: string) => t[k] ?? '#808080';
  return [
    { label: 'Text on card', fg: c('ink'), bg: c('surface'), need: 4.5 },
    { label: 'Secondary text', fg: c('muted'), bg: c('surface'), need: 4.5 },
    { label: 'Faint text', fg: c('faint'), bg: c('surface'), need: 3 },
    { label: 'Accent on page', fg: c('accent'), bg: c('bg'), need: 4.5 },
    { label: 'Accent on card', fg: c('accent'), bg: c('surface'), need: 4.5 },
    { label: 'Accent on raised', fg: c('accent'), bg: c('raised'), need: 3 },
    { label: 'Button text', fg: c('on-accent'), bg: c('accent'), need: 4.5 },
    { label: 'Done (green)', fg: c('go'), bg: c('surface'), need: 3 },
    { label: 'Warning (amber)', fg: c('maybe'), bg: c('surface'), need: 3 },
    { label: 'Error (red)', fg: c('err'), bg: c('surface'), need: 3 },
  ];
}

/** Mix the accent toward the text colour until it reaches AA on cards; null when it already does or cannot. */
function liftAccent(t: Tokens): string | null {
  const accent = t.accent;
  const surface = t.surface;
  const ink = t.ink;
  if (!accent || !surface || !ink) return null;
  if (Palette.contrast(accent, surface) >= 4.5) return null;
  for (let s = 0.05; s <= 1; s += 0.05) {
    const next = Palette.mix(accent, ink, s);
    if (Palette.contrast(next, surface) >= 4.5) return next;
  }
  return null;
}

function ContrastTable({ tokens, onLift }: { tokens: Tokens; onLift: (hex: string) => void }) {
  const rows = useMemo(() => pairs(tokens), [tokens]);
  const lifted = liftAccent(tokens);
  return (
    <div>
      <ul className="flex flex-col gap-0.5">
        {rows.map((p) => {
          const r = Palette.contrast(p.fg, p.bg);
          const g = grade(r);
          const ok = r >= p.need;
          return (
            <li key={p.label} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-ink/5">
              <span
                className="flex h-5 w-8 shrink-0 items-center justify-center rounded border border-white/10 font-mono text-[10px] font-semibold"
                style={{ backgroundColor: p.bg, color: p.fg }}
                aria-hidden="true"
              >
                Aa
              </span>
              <span className="flex-1 truncate text-2xs text-ink/85">{p.label}</span>
              <span className="font-mono text-2xs text-muted">{r.toFixed(2)}</span>
              <span
                className={`w-11 rounded-full border text-center font-mono text-[10px] leading-4 ${ok ? GRADE_TONE[g] : GRADE_TONE.Low}`}
                title={`Needs ${p.need}:1 here`}
              >
                {g}
              </span>
            </li>
          );
        })}
      </ul>
      {lifted && (
        <button
          type="button"
          onClick={() => onLift(lifted)}
          className="mt-2 flex w-full items-center justify-between rounded-lg border border-line px-2 py-1.5 text-2xs text-muted hover:border-accent/40 hover:text-ink"
        >
          <span>Lift accent to AA on cards</span>
          <span className="flex items-center gap-1.5 font-mono uppercase">
            <span className="h-3 w-3 rounded-full" style={{ backgroundColor: lifted }} />
            {lifted}
          </span>
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- picker

function ThemePicker() {
  const { theme, presets, select, customize, reset } = useTheme();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'palette' | 'contrast'>('palette');
  const [copied, setCopied] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  // A custom palette becomes a new preset line; a preset becomes the default.
  const line = theme.isCustom ? Palette.presetLine(theme) : `export const DEFAULT_PRESET = '${theme.id}';`;
  const hint = theme.isCustom
    ? 'Paste into PRESETS in web/src/lib/presets.ts, then rebuild.'
    : theme.id === DEFAULT_PRESET
      ? 'This is already the default palette.'
      : 'Paste over DEFAULT_PRESET in web/src/lib/presets.ts, then rebuild.';

  useEffect(() => setCopied(false), [theme]);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: PointerEvent) => root.current && !root.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const copy = () => {
    navigator.clipboard?.writeText(line).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Colour palette: ${theme.name}`}
        className="flex h-7 items-center gap-1.5 rounded-full border border-line px-2 font-mono text-2xs text-muted hover:text-ink"
      >
        <Swatch tokens={theme.tokens} size="h-3.5 w-3.5" />
        <span className="hidden sm:inline">{theme.name}</span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="Colour palette"
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={springs.snappy}
            style={{ transformOrigin: 'bottom left' }}
            className="absolute bottom-9 left-0 z-10 max-h-[78vh] w-80 overflow-y-auto rounded-2xl border border-line-strong bg-raised p-2 shadow-2xl shadow-black/70"
          >
            <div className="relative mb-2 flex rounded-lg bg-bg/60 p-0.5" role="tablist">
              {(['palette', 'contrast'] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  role="tab"
                  aria-selected={tab === k}
                  onClick={() => setTab(k)}
                  className={`relative z-[1] flex-1 rounded-md py-1 text-2xs font-medium capitalize ${tab === k ? 'text-ink' : 'text-muted hover:text-ink'}`}
                >
                  {tab === k && <motion.span layoutId="dev-tab" transition={springs.snappy} className="absolute inset-0 -z-[1] rounded-md bg-raised shadow" />}
                  {k}
                </button>
              ))}
            </div>

            {tab === 'palette' ? (
              <>
                <ul role="radiogroup" aria-label="Preset palettes" className="flex flex-col">
                  {presets.map((p) => {
                    const selected = !theme.isCustom && theme.id === p.id;
                    return (
                      <li key={p.id}>
                        <button
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          onClick={() => select(p.id)}
                          className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm ${
                            selected ? 'bg-ink/10 text-ivory' : 'text-ink/80 hover:bg-ink/5'
                          }`}
                        >
                          <Swatch tokens={Palette.preview(p)} />
                          <span className="flex-1 truncate">{p.name}</span>
                          {selected && <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <div className="mt-2 border-t border-line px-1 pt-2">
                  <div className="flex items-center justify-between px-1 pb-2">
                    <p className="label">Custom{theme.isCustom ? ' · active' : ''}</p>
                    <button type="button" onClick={reset} className="font-mono text-2xs text-muted underline-offset-2 hover:text-ink hover:underline">
                      Reset
                    </button>
                  </div>
                  <div className="flex gap-1.5">
                    <ColorField label="Background" value={theme.tokens.bg ?? '#000000'} onChange={(bg) => customize({ bg })} />
                    <ColorField label="Accent" value={theme.tokens.accent ?? '#7daafa'} onChange={(accent) => customize({ accent })} />
                  </div>
                  <p className="px-1 pt-2 text-2xs leading-snug text-faint">Text, borders and surfaces are derived from the background.</p>
                </div>
              </>
            ) : (
              <div className="px-1">
                <ContrastTable tokens={theme.tokens} onLift={(accent) => customize({ accent })} />
                <p className="px-1 pt-2 text-2xs leading-snug text-faint">
                  WCAG ratios. Body text needs 4.5:1 (AA); large text, bars and icons need 3:1.
                </p>
              </div>
            )}

            <div className="mt-2 border-t border-line px-2 pb-1 pt-2">
              <div className="flex items-center justify-between pb-1.5">
                <p className="label">Keep this palette</p>
                <button type="button" onClick={copy} className="font-mono text-2xs text-accent underline-offset-2 hover:underline">
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <code className="block break-all rounded-lg border border-line bg-bg px-2 py-1.5 font-mono text-2xs text-ink/80">{line}</code>
              <p className="pt-1.5 text-2xs leading-snug text-faint">{hint}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
