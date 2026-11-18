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
  On by default; `?dev=0` or its × hides it in that browser, `?dev=1` brings it back.
  A palette is shared with `?theme=<id>` or `?bg=…&accent=…`.
- Same API and behaviour as the old plain-JS UI (deleted in rv-6; `git show 503cf49^:photovault/web/static/app.js`): no API, schema or engine change
  unless a task says so. Data from the API is rendered as text by React, never as HTML.
- The new UI is served at `/` (and `/next`, kept for bookmarks) since rv-6; the classic UI is gone.
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
- [x] 3. Drive file lists and the filter bar (ui-7, ui-10) (visual check pending, user)
  - ID: rv-3 (after rv-1). Built by a Haiku agent from a brief; reviewed and checked in a browser.
  - The drive view gains a Files section (shared FilterBar, FileTable with copies rows, Pager, Show in Finder per
    file); the four state tiles filter it and scroll to it; an empty drive says how to fill it. The file list
    reloads on filter and after a run, not every few seconds, so paging is never reset under the user.
  - Result (2026-11-18): `npm test` 16 passed; FLATS1 filtered to Hashed showed 27,326 files with copies.
- [x] 4. New-drive setup, assign and ignore (ui-9, ui-15) (visual check pending, user)
  - ID: rv-4 (after rv-1). Built by a Sonnet agent from a brief; reviewed (a failed start no longer opens a setup
    that never runs) and checked in a browser. Rename stays with vn-2 (spec `backup`).
  - `#/setup/<drive key>`: the Set up drive dialog (roles, NTFS = legacy only with the reason, Mac folder never
    legacy, Run the workflow after setup, Ignore drive), then a setup timeline; a read-only failure explains why and
    offers "Set up as legacy drive…"; when done it opens the drive and starts the workflow if asked. Every drive
    row has Ignore / Unignore on hover.
  - Result (2026-11-18): `npm test` 16 passed (helpers newestAssignTask, setupProblem); "This Mac" opened the
    dialog with the local-folder roles. Setup itself was not run (it writes the drive's ID file).
- [x] 5. Settings and GPS reference folders (visual check pending, user)
  - ID: rv-5 (after rv-1). Photos moved out to rv-7 (user, 2026-11-18). Built by a Haiku agent; reviewed.
  - `#/settings`: creator, copyright, default time zone, GPS match window, Mac folder; Save only when changed.
    "Add GPS reference folder…" (Settings and the sidebar) starts `meta_refs` on a folder.
  - Result (2026-11-18): `npm test` 16 passed; Settings loaded the saved values in a browser.
- [x] 6. Make the new UI the default at `/`; delete the classic UI; update the user guides (visual check pending, user)
  - ID: rv-6 (after rv-2..5). The user asked for it on 2026-11-18 without Photos, which "does not work on the old UI
    anyway"; the Photos nav item shows the rv-7 placeholder until then.
  - `/` (and `/next`, kept for bookmarks) serve the build; `static/index.html`, `app.js` and `app.css` are deleted
    (git history has them, rv-7 starts from there). Photos shows a "being rebuilt" page. README has a Web UI section.
  - Result (2026-11-18): `uv run pytest -q` 490 passed, 4 skipped (`/` serves the build, `/static/app.js` is 404);
    `npm test` 16 passed; the built page loaded with no console errors on every route.

- [x] 7. Photos: the grid and the metadata editor, rebuilt and made to work [stronger] (visual check pending, user)
  - ID: rv-7 (after rv-6). Design chosen by the user on 2026-11-18: a timeline by day, a right-side inspector, Leaflet
    with OpenStreetMap tiles and place search only on Enter; the `assets(group_id)` index approved (D-064).
  - Why the classic Photos failed (found on a copy of the real catalog, 62,121 groups): `GET /api/groups` never
    returned. `assets.group_id` had no index, so finding each group's primary file scanned every asset, once per
    group, before paging. With the index a 200-photo page takes 0.36 s. Shoots, suggestions and saving worked. Also:
    0 shoots exist (grouping into shoots has not run), and 19,357 groups (files indexed but not hashed) had no asset
    link at all, so they showed no file and no thumbnail.
  - Built: migration V4 (`assets_group` index, system-design §5d); `GET /api/photo-days` (days, newest first, with
    cameras and counts; filters drive and missing) and `day=` on `GET /api/groups`; groups not hashed yet are
    located through their index job (`repo.indexed_primary_files`), for the list, the drive filter and thumbnails.
    `#/photos`: days with sticky headers, a year/month rail, a day's photos loaded when it scrolls near (300 at a
    time), click / ⌘-click / ⇧-click / Select day / Esc; tiles show a pin (has a location) and a pencil (edited,
    sidecar not written). Inspector: preview and path (one photo), map (click or drag the pin; the selection's
    locations as dots), time-matched GPS suggestions plus the nearest located photo, place search (Nominatim, on
    Enter, at most 1/s, no referrer), place name, creator / copyright with "Use default", title, keywords with
    per-photo counts, clock shift with a before → after preview; Save applies only touched fields to every selected
    photo. Below 1280 px the inspector is a drawer on the right. Leaflet loads only when the inspector opens.
  - Result (2026-11-18): `uv run pytest -q` 528 passed, 4 skipped; `npm test` 26 passed; build clean. Headless
    Chrome on a copy of the catalog (drives' mount paths cleared, so no drive was read): 165 days, shift-click
    selected 4, a keyword, a creator and a map location saved to all 4, counts updated; no console errors at 1500
    and 900 px. On the copy, a page of a day takes ≤ 0.15 s and the drive filter ≤ 0.14 s. Real thumbnails not seen
    (they read the mounted drive): part of the user's visual check.

- [x] 8. Clean up what the classic UI left behind
  - ID: rv-8 (after rv-6; asked for by the user on 2026-11-18 so old references cause no bugs in the new UI)
  - Find every reference to the classic UI (`static/index.html`, `app.js`, `app.css`, the "Classic" link, `/next` as
    a separate address, classic-only routes, prefs or hash routes) in `photovault/`, `web/src/`, `tests/`, the
    README, `docs/user/` and the live parts of `docs/specs/` and `docs/steering/`. Remove dead code and fix what
    is stale. Keep `/next` serving the build (bookmarks). History (`docs/decisions.md`, done task entries in other
    specs, `docs/legacy/`) stays as written. Keep every API that rv-7 Photos uses (`/api/shoots`, `/api/groups*`,
    `/api/settings`).
  - Verify: `uv run pytest -q`; `cd web && npm run typecheck && npm test && npm run build`; the built page loads
    with no console errors on every route.
  - Result (2026-11-18): `uv run pytest -q` 490 passed, 4 skipped; typecheck, `npm test` (16 passed) and build clean. Only comments and docs changed (stale "classic UI" wording); server and routes had no classic-only code.
