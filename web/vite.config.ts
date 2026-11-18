import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

/**
 * The vendored palette engine (src/lib/palette/palette-core.js) is UMD and must stay byte-identical to its source.
 * Wrap it as an ES module with a default export, the same way in dev, in the build and in vitest. Leaving it to each
 * tool's CommonJS handling gave a black screen in the build and a missing export in dev.
 */
function umdAsEsm(): Plugin {
  return {
    name: 'umd-as-esm',
    enforce: 'pre',
    transform(code, id) {
      if (!id.split('?')[0]!.endsWith('/palette/palette-core.js')) return null;
      return { code: `const module = { exports: {} };\n${code}\nexport default module.exports;\n`, map: null };
    },
  };
}

// The build is served by photovault's own server at /next, with assets under /static/next/ (D-059).
// `npm run dev` proxies /api to a running `uv run photovault serve` on 8765.
export default defineConfig({
  base: '/static/next/',
  plugins: [umdAsEsm(), react()],
  build: {
    outDir: '../photovault/web/static/next',
    emptyOutDir: true,
    // Stable names keep the committed build's diffs readable; the server sends Cache-Control: no-store.
    rollupOptions: { output: { entryFileNames: 'assets/app.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]' } },
  },
  server: { port: 5174, proxy: { '/api': 'http://127.0.0.1:8765' } },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], setupFiles: ['./test/setup.ts'] },
});
