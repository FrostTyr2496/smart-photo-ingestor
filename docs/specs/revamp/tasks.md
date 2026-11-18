# Spec: revamp — the web UI rebuilt in the Argus / mkh4n-website stack (D-059)

Goal: the web UI looks and moves like the user's other projects (Argus, mkh4n-website). Motion is meaningful:
progress bars glide and shimmer, numbers count up, panels spring in. The task sidebar is calmer: one line of
progress per task, details on demand.

Rules for every task:
- Stack (D-059, user-approved): Vite 5, React 18, TypeScript, Tailwind 3, framer-motion. Source in `web/`; the
  build goes to `photovault/web/static/next/` and **is committed**, so `uv run photovault serve` works without
  Node. Node is needed only to change the UI: `cd web && npm install && npm run build`.
- Look: Argus tokens (`--c-*` space-separated RGB, Inter + JetBrains Mono, one type scale), the website's ambient
  glow behind the page, and the website's palette engine (`web/src/lib/palette/palette-core.js`, vendored
  unchanged from mkh4n-website `src/lib/palette/`, v1.0.0). Never edit the vendored file; copy a new version over.
- A dev palette picker (bottom left) tries presets, a custom background and accent, and shows the contrast of
  ink, muted text, accent, button text and status colours against the surfaces (WCAG grades, "Lift accent to AA").
  On by default while the revamp is in progress; `?dev=0` or its × hides it in that browser, `?dev=1` brings it back.
  A palette is shared with `?theme=<id>` or `?bg=…&accent=…`.
- Same API, same behaviour as the classic UI (`photovault/web/static/app.js`): no API, schema or engine change
  unless a task says so. Data from the API is rendered as text by React, never as HTML.
- Until rv-6, the new UI lives at `/next` beside the classic UI at `/`; a "Classic" link reaches screens not
  ported yet. rv-6 makes it the default and removes the classic files.
- Respect `prefers-reduced-motion`: no shimmer, no count-up, no springs.

## Tasks

- [x] 1. Scaffold, shell, tasks and drive view (visual check pending, user)
  - ID: rv-1
  - `web/` with Vite + React + TS + Tailwind + framer-motion; tokens from Argus; ambient glow; dev palette picker
    with contrast readout. Server: `GET /next` serves `static/next/index.html`.
  - Shell: header with a live, animated overall progress bar; Tasks / Photos / Cards / Settings nav.
  - Sidebar: drives grouped Mounted / Not connected / Ignored (folding, remembered), each with role, status
    line and a capacity bar; compact task cards (title, animated bar, one stats line, Pause; the current file
    and technical details behind a disclosure); terminal runs and stale jobs as in ui-11.
  - Home: tasks by drive (ui-14). Drive view: timeline with per-step Run and Run workflow (ui-12), stat tiles
    with count-up, copies on other drives (ui-8), runs list.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`; `uv run pytest tests/test_web_api.py -q`;
    a browser check at `/next`.
  - Result (2026-11-18): `npm run typecheck` clean, `npm test` 12 passed, `npm run build` 107 KB gzipped;
    `uv run pytest -q` 490 passed, 4 skipped. Headless Chrome against the live server (read-only views) showed the
    home, drive view, contrast tab, a second palette and phone width; a running hash glided and counted up.
    Fix (2026-11-18): the first build showed a black screen (the UMD palette engine lost its exports in the
    production build). A Vite plugin now wraps that file as an ES module for dev, build and vitest alike; the built
    page was checked in headless Chrome for console errors, not only the dev server.
    Not in rv-1: cards, file lists, setup dialogs, photos, settings (they open the classic UI).

- [x] 2. Cards view and card file lists (card-3, ui-10 filter bar) (visual check pending, user)
  - ID: rv-2 (after rv-1)
  - `#/cards` lists connected cards (tier pill, headline, tier bar, cameras, Check card, Import…, Show files),
    the cameras table, and cards seen before; `#/cards/<id>` has the tier tiles (click to filter), cameras, and
    the file list with the shared filter bar, pager and a copies cell that opens every copy (Show in Finder).
    The Import dialog is ported (dry run, or import after a confirm). The header dot and Cards nav stay in `/next`.
  - Also: a hash shows the drive's progress, not only the files its run started with (a resumed hash read 6 %
    on a drive 55 % hashed). Pace and time left stay the run's (D-061).
  - Verify: `cd web && npm test && npm run build`; built page checked for console errors on each route.
  - Result (2026-11-18): `npm test` 14 passed, build clean; `uv run pytest -q` 490 passed, 4 skipped. Headless
    Chrome on live data: cards list, Card A detail, home showing "62,755 of 112,797 files on the drive".
- [ ] 3. Drive file lists and the filter bar (ui-7, ui-10)
  - ID: rv-3 (after rv-1)
- [ ] 4. New-drive setup, assign / ignore / rename dialogs, import (ui-9, ui-15)
  - ID: rv-4 (after rv-1)
- [ ] 5. Photos grid, editor, GPS references, Settings
  - ID: rv-5 (after rv-1)
- [ ] 6. Make `/next` the default; delete the classic UI; update the user guides
  - ID: rv-6 (after rv-2..5; the user confirms parity first)
