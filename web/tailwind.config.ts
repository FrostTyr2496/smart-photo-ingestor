import type { Config } from 'tailwindcss';

// Every colour is a token written by the palette engine (src/lib/theme.tsx) as space-separated RGB,
// so opacity modifiers work: bg-ink/5, border-accent/40.
const token = (name: string): string => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        surface: token('surface'),
        raised: token('raised'),
        ink: token('ink'),
        ivory: token('ivory'),
        muted: token('muted'),
        faint: token('faint'),
        accent: token('accent'),
        'on-accent': token('on-accent'),
        go: token('go'),
        maybe: token('maybe'),
        err: token('err'),
        skip: token('skip'),
        card: token('card'),
        master: token('master'),
        line: 'rgb(var(--c-ink) / 0.08)',
        'line-strong': 'rgb(var(--c-ink) / 0.14)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      // Argus's scale: 12 micro, 13 secondary, 15 body, 17 lead, 20 section title. Nothing below 11.
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        xs: ['0.75rem', { lineHeight: '1.125rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.9375rem', { lineHeight: '1.5rem' }],
        lg: ['1.0625rem', { lineHeight: '1.6rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem' }],
        '2xl': ['1.75rem', { lineHeight: '2.1rem' }],
      },
      borderRadius: { card: '14px' },
    },
  },
} satisfies Config;
