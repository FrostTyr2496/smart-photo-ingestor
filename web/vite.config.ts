import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The build is served by photovault's own server at /next, with assets under /static/next/ (D-059).
// `npm run dev` proxies /api to a running `uv run photovault serve` on 8765.
export default defineConfig({
  base: '/static/next/',
  plugins: [react()],
  build: {
    outDir: '../photovault/web/static/next',
    emptyOutDir: true,
    // Stable names keep the committed build's diffs readable; the server sends Cache-Control: no-store.
    rollupOptions: { output: { entryFileNames: 'assets/app.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]' } },
  },
  server: { port: 5174, proxy: { '/api': 'http://127.0.0.1:8765' } },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], setupFiles: ['./test/setup.ts'] },
});
