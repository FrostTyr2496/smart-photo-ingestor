/*
 * palette-core.js · v1.0.0
 * Colour palette engine: derive a full set of colour tokens from a background and an accent, write them
 * onto <html> as CSS variables, remember the choice, and share it by URL.
 *
 * No dependencies, no framework, no build step. Works three ways:
 *   - classic <script src="palette-core.js">  → window.Palette
 *   - CommonJS / bundlers (webpack, Jest)     → require('./palette-core') or import { … } from './palette-core'
 *
 * Source of truth: mkh4n-website, src/lib/palette/. Other projects copy this file unchanged and keep their
 * own presets; see README.md beside it.
 *
 * Tokens are written as space-separated RGB triples (--c-bg: 11 12 14) so CSS can add alpha:
 *   background: rgb(var(--c-bg));  border-color: rgb(var(--c-ink) / 0.09);
 */
/* eslint strict: off -- 'use strict' matters when this loads as a classic <script> */
/* global globalThis */
(function (root, factory) {
  /* eslint-disable no-undef */
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Palette = factory();
  /* eslint-enable no-undef */
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var VERSION = '1.0.0';

  /** Tokens deriveTokens() always produces. Anything else a palette sets is passed through as-is. */
  var DERIVED = ['bg', 'surface', 'raised', 'ink', 'ivory', 'muted', 'faint', 'accent', 'on-accent'];

  // ---------------------------------------------------------------------------------------- colour maths

  var isHex = function (v) {
    return typeof v === 'string' && /^#?[0-9a-f]{6}$/i.test(v);
  };
  var withHash = function (v) {
    return v.charAt(0) === '#' ? v : '#' + v;
  };
  var toRgb = function (hex) {
    var h = hex.replace('#', '');
    var full = h.length === 3 ? h.replace(/./g, '$&$&') : h;
    return [0, 2, 4].map(function (i) {
      return parseInt(full.slice(i, i + 2), 16);
    });
  };
  var toHex = function (rgb) {
    return (
      '#' +
      rgb
        .map(function (v) {
          return Math.round(v).toString(16).padStart(2, '0');
        })
        .join('')
    );
  };
  /** Blend hex `a` toward hex `b` by t (0…1). */
  var mix = function (a, b, t) {
    var B = toRgb(b);
    return toHex(
      toRgb(a).map(function (v, i) {
        return v + (B[i] - v) * t;
      })
    );
  };

  /** WCAG relative luminance, 0 (black) … 1 (white). */
  function luminance(hex) {
    var c = toRgb(hex).map(function (v) {
      var s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  /** WCAG contrast ratio between two hex colours, 1 … 21. */
  function contrast(a, b) {
    var la = luminance(a);
    var lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }

  // ---------------------------------------------------------------------------------------- derivation

  /**
   * The whole token set from a background and an accent.
   *   surface / raised   two steps lifted off the background
   *   ink                body text;  ivory  strongest text and primary buttons
   *   muted / faint      secondary and tertiary text
   *   on-accent          text drawn on an accent-filled button (the better-contrast of white and dark)
   * Precedence, lowest first: derived values, schemeTokens[scheme], tokens.
   */
  function deriveTokens(input) {
    var bg = input.bg;
    var accent = input.accent;
    var tokens = input.tokens || {};
    var dark = luminance(bg) < 0.4;
    var scheme = dark ? 'dark' : 'light';
    var schemed = (input.schemeTokens && input.schemeTokens[scheme]) || {};
    var pole = dark ? '#ffffff' : '#000000';
    var ink = tokens.ink || schemed.ink || mix(bg, pole, dark ? 0.92 : 0.84);
    var darkText = dark ? bg : ink;
    var derived = {
      bg: bg,
      surface: mix(bg, ink, 0.035),
      raised: mix(bg, ink, 0.075),
      ink: ink,
      ivory: mix(bg, pole, dark ? 0.95 : 0.92),
      muted: mix(bg, ink, 0.6),
      faint: mix(bg, ink, 0.4),
      accent: accent,
      'on-accent': contrast(accent, '#ffffff') >= contrast(accent, darkText) ? '#ffffff' : darkText,
    };
    return { scheme: scheme, tokens: Object.assign(derived, schemed, tokens) };
  }

  /** Swatch colours for a preset, for pickers. */
  function preview(preset, schemeTokens) {
    return deriveTokens({ bg: preset.bg, accent: preset.accent, tokens: preset.tokens, schemeTokens: schemeTokens }).tokens;
  }

  /**
   * Turn a stored choice into a theme.
   *   presets       [{ id, name, bg, accent, tokens? }]
   *   choice        { preset: id | null, custom: { bg?, accent? } }
   *   defaultId     preset used when the choice names none. null = no theme at all (returns null), so the
   *                 page keeps whatever its own CSS says (e.g. prefers-color-scheme).
   *   schemeTokens  { light: {…}, dark: {…} } extra tokens chosen by the derived scheme, e.g. status colours.
   * A custom background throws away the preset's hand-tuned tokens and derives everything fresh;
   * schemeTokens still apply.
   */
  function resolveTheme(opts) {
    var presets = opts.presets || [];
    var choice = opts.choice || {};
    var custom = choice.custom || {};
    var isCustom = Boolean(custom.bg || custom.accent);
    var find = function (id) {
      return id == null
        ? null
        : presets.filter(function (p) {
            return p.id === id;
          })[0] || null;
    };
    var preset = find(choice.preset) || find(opts.defaultId);
    if (!preset && !isCustom && opts.defaultId === null) return null;
    preset = preset || presets[0];
    if (!preset) return null;

    var source = custom.bg
      ? { bg: custom.bg, accent: custom.accent || preset.accent }
      : { bg: preset.bg, accent: custom.accent || preset.accent, tokens: preset.tokens };
    source.schemeTokens = opts.schemeTokens;
    var derived = deriveTokens(source);
    return {
      id: isCustom ? 'custom' : preset.id,
      presetId: preset.id,
      name: isCustom ? 'Custom' : preset.name,
      isCustom: isCustom,
      scheme: derived.scheme,
      tokens: derived.tokens,
    };
  }

  // ---------------------------------------------------------------------------------------- DOM

  var written = typeof WeakMap === 'function' ? new WeakMap() : null;

  /**
   * Write a theme onto the page. theme = null removes everything a previous call wrote, handing control
   * back to the page's own CSS.
   *   root      element that gets the variables (default <html>)
   *   prefix    variable prefix: 'c' → --c-bg, --c-accent, …
   *   attr      data attribute for the theme id ('palette' → data-palette="forest"); false to skip
   *   schemeAttr data attribute for 'light' | 'dark' (default 'scheme'); false to skip
   *   metaThemeColor  update <meta name="theme-color"> to the background (default true)
   */
  function applyTheme(theme, options) {
    var o = options || {};
    if (typeof document === 'undefined') return;
    var root = o.root || document.documentElement;
    var prefix = o.prefix || 'c';
    var attr = o.attr === undefined ? 'palette' : o.attr;
    var schemeAttr = o.schemeAttr === undefined ? 'scheme' : o.schemeAttr;
    var meta = o.metaThemeColor === false ? null : document.querySelector('meta[name="theme-color"]');

    var state = (written && written.get(root)) || { names: [], applied: false, meta: null };
    // Remember the page's own theme-color the moment a theme takes over, so clearing can put it back.
    if (theme && !state.applied) state.meta = meta ? meta.getAttribute('content') : null;
    var next = [];
    if (theme) {
      Object.keys(theme.tokens).forEach(function (name) {
        var value = theme.tokens[name];
        if (!isHex(value)) return;
        root.style.setProperty('--' + prefix + '-' + name, toRgb(withHash(value)).join(' '));
        next.push(name);
      });
    }
    state.names.forEach(function (name) {
      if (next.indexOf(name) === -1) root.style.removeProperty('--' + prefix + '-' + name);
    });
    state.names = next;
    var wasApplied = state.applied;
    state.applied = Boolean(theme);
    if (written) written.set(root, state);

    root.style.colorScheme = theme ? theme.scheme : '';
    if (attr) {
      if (theme) root.setAttribute('data-' + attr, theme.id);
      else root.removeAttribute('data-' + attr);
    }
    if (schemeAttr) {
      if (theme) root.setAttribute('data-' + schemeAttr, theme.scheme);
      else root.removeAttribute('data-' + schemeAttr);
    }
    if (meta) {
      if (theme) meta.setAttribute('content', theme.tokens.bg);
      else if (wasApplied && state.meta != null) meta.setAttribute('content', state.meta);
    }
  }

  // ---------------------------------------------------------------------------------------- sharing

  /** A choice from ?theme=<id>&bg=<hex>&accent=<hex>, or null when the query names none. */
  function parseQuery(search, presets) {
    var q = new URLSearchParams(search || '');
    var id = q.get('theme');
    var preset =
      (presets || []).some(function (t) {
        return t.id === id;
      }) && id
        ? id
        : null;
    var custom = {};
    ['bg', 'accent'].forEach(function (k) {
      if (isHex(q.get(k))) custom[k] = withHash(q.get(k));
    });
    return preset || Object.keys(custom).length ? { preset: preset, custom: custom } : null;
  }

  /** The query string that reproduces a theme on another browser: '?theme=forest' or '?bg=…&accent=…'. */
  function toQuery(theme) {
    if (!theme) return '';
    var q = new URLSearchParams();
    q.set('theme', theme.presetId);
    if (theme.isCustom) {
      q.set('bg', theme.tokens.bg.replace('#', ''));
      q.set('accent', theme.tokens.accent.replace('#', ''));
    }
    return '?' + q.toString();
  }

  /** One line to paste into a presets array to make the current palette permanent. */
  function presetLine(theme) {
    if (!theme) return '';
    return theme.isCustom
      ? "{ id: 'my-palette', name: 'My palette', bg: '" + theme.tokens.bg + "', accent: '" + theme.tokens.accent + "' },"
      : "'" + theme.id + "'";
  }

  // ---------------------------------------------------------------------------------------- store

  var safeStorage = function () {
    try {
      return typeof window !== 'undefined' ? window.localStorage : null;
    } catch (e) {
      return null;
    }
  };

  /**
   * A small observable store for apps without a framework (or to wrap in one).
   *
   *   var palette = Palette.createStore({ presets: PRESETS, storageKey: 'pv:palette' });
   *   palette.subscribe(function (theme) { … });   // called on every change
   *   palette.select('forest'); palette.customize({ accent: '#ff8800' }); palette.reset();
   *
   * Options: presets, defaultId (null = leave the page's own CSS alone until something is chosen),
   * schemeTokens, storageKey (false = don't persist), readUrl (default true: ?theme= wins over storage),
   * apply (default true: write onto the page on every change; pass applyTheme options as an object).
   */
  function createStore(opts) {
    var presets = opts.presets || [];
    var defaultId = opts.defaultId === undefined ? null : opts.defaultId;
    var key = opts.storageKey === undefined ? 'palette' : opts.storageKey;
    var storage = key ? opts.storage || safeStorage() : null;
    var listeners = [];

    var load = function () {
      try {
        var raw = storage && storage.getItem(key);
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    };
    var save = function (c) {
      try {
        if (!storage) return;
        if (!c.preset && !Object.keys(c.custom || {}).length) storage.removeItem(key);
        else storage.setItem(key, JSON.stringify(c));
      } catch (e) {
        /* private mode, quota: ignore */
      }
    };

    var fromUrl =
      opts.readUrl !== false && typeof window !== 'undefined' ? parseQuery(window.location.search, presets) : null;
    var choice = fromUrl || load() || { preset: null, custom: {} };
    choice.custom = choice.custom || {};
    if (fromUrl) save(choice);

    var applyOpts = opts.apply === undefined || opts.apply === true ? {} : opts.apply;
    var theme;
    var compute = function () {
      theme = resolveTheme({ presets: presets, choice: choice, defaultId: defaultId, schemeTokens: opts.schemeTokens });
      if (applyOpts) applyTheme(theme, applyOpts);
    };
    compute();

    var set = function (next) {
      choice = { preset: next.preset || null, custom: next.custom || {} };
      save(choice);
      compute();
      listeners.slice().forEach(function (fn) {
        fn(theme);
      });
    };

    return {
      presets: presets,
      getTheme: function () {
        return theme;
      },
      getChoice: function () {
        return { preset: choice.preset, custom: Object.assign({}, choice.custom) };
      },
      /** Pick a preset; clears custom colours. */
      select: function (id) {
        set({ preset: id, custom: {} });
      },
      /** Override bg and/or accent on top of the current preset. */
      customize: function (patch) {
        set({ preset: choice.preset || (theme && theme.presetId) || null, custom: Object.assign({}, choice.custom, patch) });
      },
      /** Back to defaultId (or, with defaultId null, to the page's own CSS). */
      reset: function () {
        set({ preset: null, custom: {} });
      },
      subscribe: function (fn) {
        listeners.push(fn);
        return function () {
          listeners = listeners.filter(function (l) {
            return l !== fn;
          });
        };
      },
      preview: function (preset) {
        return preview(preset, opts.schemeTokens);
      },
      presetLine: function () {
        return presetLine(theme);
      },
      shareUrl: function () {
        if (typeof window === 'undefined' || !theme) return '';
        return window.location.origin + window.location.pathname + toQuery(theme);
      },
    };
  }

  return {
    VERSION: VERSION,
    DERIVED: DERIVED,
    isHex: isHex,
    toRgb: toRgb,
    toHex: toHex,
    mix: mix,
    luminance: luminance,
    contrast: contrast,
    deriveTokens: deriveTokens,
    preview: preview,
    resolveTheme: resolveTheme,
    applyTheme: applyTheme,
    parseQuery: parseQuery,
    toQuery: toQuery,
    presetLine: presetLine,
    createStore: createStore,
  };
});
