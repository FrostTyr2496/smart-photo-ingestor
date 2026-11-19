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

- [x] 9. Photos: apply edits to related photos (visual check pending, user)
  - ID: rv-9 (after rv-7; asked for by the user on 2026-11-18). UI built by a Sonnet agent from a brief; reviewed.
  - `GET /api/groups` gains `after=` / `before=` (capture time `YYYY-MM-DDTHH:MM:SS`, inclusive). The inspector gets a
    "Related photos" section: photos taken in the same session as the selection (each within a gap of the next:
    15 min, 1 h or 3 h, or the same day), fetched around the selection; "Only without a location" (on by default)
    hides photos that already have one. Eligible photos show as small thumbnails, all ticked; click toggles.
    They are outlined in the main grid too. Save applies the touched fields to the selection plus the ticked
    related photos ("Save to 1 + 8 related").
  - Verify: `uv run pytest tests/test_meta_repo.py tests/test_web_api.py -q`; `cd web && npm test && npm run build`;
    a browser check that saving with related photos ticked updates them.
  - Result (2026-11-18): `uv run pytest -q` 529 passed, 4 skipped; `npm test` 31 passed; build clean (built in a clean
    worktree, so another session's uncommitted progress-bar work stays out of it). Headless Chrome on a catalog copy:
    one photo selected → "774 related photos (taken within 1 h of each other)", 299 loaded tiles outlined, "Save to
    1 + 774 related" saved a keyword to all 775 (checked through the API). Note: at 1 h a busy day chains into one
    session; the user picks 15 min for tighter groups. Selected photos missing from the fetch still anchor the session.

- [x] 12. Sidebar: file system tag on every drive
  - ID: rv-12 (user ask, 2026-11-18)
  - Each sidebar drive row and the drive page header show the file system as a small monospace tag
    (`fsLabel`: apfs → APFS, exfat → exFAT, msdos → FAT32, ntfs → NTFS, hfs → HFS+; unknown upper-cased, none hidden).
  - Verify: `cd web && npm test` (format.test.ts).
  - Result (2026-11-18): `cd web && npm test` → 33 passed; typecheck clean.

- [x] 13. Clickable tiles look clickable, and the selection glides
  - ID: rv-13 (user ask, 2026-11-18)
  - One `SelectTile` (bits.tsx) for every tile that filters a list: the drive page's state tiles, its copies tiles
    ("On other drives", "Only on this drive", "Duplicated on this drive") and per-drive copy rows, and the Cards
    tier tiles. Hover lifts the tile, strengthens its border and shows "Show"; the selected one carries an accent
    ring that slides between siblings (Framer Motion `layoutId`, instant under reduced motion) and a "Shown ×"
    tag; clicking it again clears the filter.
  - Verify: `cd web && npm run typecheck`; **[user]** visual check.
  - Result (2026-11-18): `npm run typecheck` → clean; the user looked at it on seeded data ("looks good").

- [x] 14. Rename a drive in PhotoVault
  - ID: rv-14 (user ask, 2026-11-18). Covers the display half of vn-1; vn-2 (renaming the volume on disk) stays open.
  - `POST /api/volumes/<id>/label {label}` sets `volumes.label` only (trimmed, 1–64 characters, no control
    characters); nothing on the drive changes, so Lightroom paths are safe. The drive page title has Rename
    (or double-click): Enter saves, Esc cancels. When the nickname differs from the mount's name, the header shows
    "Volume name on disk: <name>".
  - Verify: `uv run pytest tests/test_web_api.py -q -k label`
  - Result (2026-11-18): `uv run pytest -q` → 530 passed, 4 skipped. Out of scope: renaming the volume on disk (vn-2).

- [x] 15. The drive list loads fast
  - ID: rv-15 (user ask, 2026-11-18; D-068)
  - `/api/drives` took about 2 s with six mounts, and `/api/cards` and each drive page paid it again: every mount
    ran `diskutil info` one after another. `scan_mounts` now probes in parallel, and `volumes._diskutil_info`
    caches the default runner's successful answers for 10 min per (mount path, device id). Markers and DCIM are
    read fresh on every probe; an injected runner (tests) and failures are never cached.
  - Verify: `uv run pytest tests/test_volumes.py -q`
  - Result (2026-11-18): `uv run pytest -q` → 532 passed, 4 skipped. On a copy of the real catalog with no mounts,
    the drive list's catalog queries take about 0.3 s (`volume_last_steps`, `meta_scan_status`); left as is.

- [x] 10. Photos: Copy filename
  - ID: rv-10 (user ask, 2026-11-18; restored from the shelved draft, D-070). Small; can ride along with sp-3.
  - The inspector's header has "Copy filename" (one photo): it copies the file's name on disk (the basename of
    `rel_path`: the D-005 name on WORKING, the original name on a legacy drive), or the stem when no file is indexed.
    A toast confirms. Uses the Clipboard API, with a fallback when it is unavailable.
  - Verify: `cd web && npm test`; **[user]** visual check.
  - Result (2026-11-18): `cd web && npm run typecheck && npm test && npm run build` → typecheck clean, 44 tests passed (`copyName` covered), build committed. Visual check is the user's.

- [x] 17. Coordinates open in Google Maps
  - ID: rv-17 (user ask, 2026-11-18)
  - Every coordinate pair in the inspector (current location, a pending new location, suggestion rows) is a link to
    `https://www.google.com/maps/search/?api=1&query=<lat>,<lon>` (Google's documented Maps URLs), opened in a new
    tab with `noopener noreferrer`. Only the coordinates are sent; nothing is fetched until the user clicks.
  - Verify: `cd web && npm test` (photos.test.ts `mapsUrl`).
  - Result (2026-11-18): `cd web && npm run typecheck && npm test` → clean, 48 passed; build committed.

- [x] 18. Copy the PhotoVault name, not only the camera's
  - ID: rv-18 (user ask, 2026-11-18; amends rv-10)
  - "Copy name" copies the D-005 name (`system-design.md` §6: `M{num}_{date}-{time}-{subsec}_{body}_{ident}` plus the
    primary file's extension, with the shutter count as `ident`). It is the name the file has, or would get, on
    WORKING. Compute it with the same code path that ing-12 uses for frames already seen on a legacy drive (no second
    implementation). Expose it as `vault_name` on `GET /api/groups` rows, or on a per-group endpoint if computing it for
    every row is too slow. A second action, "Copy camera name", copies the original filename (rv-10's behaviour).
    If a field the name needs (for example subsec) is not in the catalog, use what ingest would use, and note it.
  - Verify: `uv run pytest tests/test_web_api.py tests/test_naming.py -q`; `cd web && npm test`.
  - Result (2026-11-18): `naming.vault_name` (the stored stem once the group has a shoot, else `canonical_stem` + `member_filename`, the code ing-12 calls) feeds `vault_name` on `GET /api/groups` rows (catalog fields only, no file reads). The catalog has no sub-second, so it is `00` until import. Header: "Copy name" (vault) and a small "Camera name". web/api tests pass.

- [x] 19. "Show in All Photos" brings the selection back into view
  - ID: rv-19 (user ask, 2026-11-18)
  - The inspector header has "Show in All Photos". It clears the drive and "missing" filters to All photos (keeping
    the selection), loads the selected photo's day, scrolls the grid until that tile is centred, and briefly flashes its
    ring. With several photos selected, it uses the first in time order. It works when the photo's day is far
    off-screen or not loaded yet.
  - Verify: `cd web && npm run typecheck && npm test`; **[user]** check in the browser.
  - Result (2026-11-18): inspector button "Show in All Photos" clears drive and missing filters, loads the first selected photo's day (further pages if needed), centres the tile (`scrollIntoView({block:'center'})`, smooth unless reduced motion) and pulses its ring twice. Helpers `dayOf`, `firstInTime` tested. Not seen in a browser.

- [x] 20. Full-screen viewer (lightbox), modelled on the user's website
  - ID: rv-20 (user ask, 2026-11-18). Reference: the user's `Lightbox.js` and `ZoomView.js` (a copy is given to the
    agent; never commit them, and port the behaviour rather than the code).
  - Open from a tile (double-click or Space), and from the inspector's preview (click). It shows the photo uncropped
    (`object-contain`) on a black scrim, portalled to `document.body`, with a Framer Motion fade.
  - Keys: ←/→ step through the current day's loaded photos in grid order, wrapping; Esc closes; `I` toggles an info
    overlay (camera, time, location with the rv-17 Maps link, place, keywords); `F` uses the browser's
    `requestFullscreen` (exit on Esc or `F`). Swipe steps on touch. Click zooms to 100% and drag pans (ZoomView);
    click again fits.
  - Focus returns to the tile on close. Stepping changes the selection, so the inspector follows.
  - Image: `/api/groups/<id>/preview` returns the largest embedded JPEG (`JpgFromRaw` first, then `PreviewImage`),
    cached under `<home>/previews/`. The thumbnail shows until it loads.
  - Verify: `uv run pytest tests/test_web_api.py -q -k preview`; `cd web && npm run typecheck && npm test`; **[user]**
    browser check.
  - Result (2026-11-18): `Lightbox.tsx` + `ZoomView.tsx`, portalled from Photos; opens on double-click or Space on a tile and by clicking the inspector preview. Keys ←/→ (wrap, selection follows), Esc (leaves zoom first), I, F; swipe; click zooms to 100% and drag pans. `GET /api/groups/<id>/preview` (JpgFromRaw then PreviewImage) caches under `<home>/previews/`; thumbnail blurred until it loads. Reduced motion respected. API test `-k preview`, helper tests pass. Not seen in a browser.

- [x] 21. Condensed headers with stats, active filters, and where the photo lives
  - ID: rv-21 (user ask, 2026-11-18, D-076)
  - **Inspector header**, at most three compact lines:
    1. The stem, then icon buttons with tooltips: Copy name, Camera name, Show in All Photos, Full screen, Close. These
       replace the text buttons.
    2. The body · the capture time · exposure from EXIF (`1/250 s · f/2.8 · ISO 3200 · 24 mm · <lens>`).
    3. Drive chips, one per volume holding the photo: the label, a filled dot when mounted and a hollow one when not,
       and the instance state (verified / indexed) on hover. The drive the preview is **loaded from** is marked
       ("showing from PP 1").
    With several photos selected: "N selected · bodies · time span", and drive chips with counts.
  - **Day header** (sticky): one line. `Sun, Sep 10, 2023 · Z6, Z6II · 1,894 photos · 21:18–05:33 · 12 located ·
    1 selected`, with "Select day" kept on the right.
  - **Active filters**: when a drive or "missing" filter is on, a chip row under the page title and a short note in the
    sticky day header ("Filtered: PP 1 · Missing location ✕"). ✕ clears it, and rv-19 uses the same clear function.
  - API `GET /api/groups/<id>/detail` returns:
    - `instances` (volume_id, label, role, mounted, rel_path, state);
    - `preview_from` (the volume `_thumb_source` would read; refactor it to report that);
    - `exif` (exposure_s, fnumber, iso, focal_mm, lens, from ExifTool on the mounted primary file, read-only, cached
      under `<home>/exif/<group_id>.json`; null when no drive is mounted).
    The header loads it lazily for one photo. Day stats come from the rows already fetched, plus the day's count.
  - Verify: `uv run pytest tests/test_web_api.py -q -k detail`; `cd web && npm run typecheck && npm test`;
    **[user]** browser check.
  - Result (2026-11-18): `GET /api/groups/<id>/detail` (instances with mounted, `preview_from` via `_thumb_source_from`, exif through `metadata.read_metadata`, cached in `<home>/exif/`); inspector header is stem + icon buttons, body · time · exposure, and drive chips; day header line adds time span and located count (`+` while the day is partly loaded); filter chips under the title and a note in the day header, sharing `clearFilters` with Show in All. Pure helpers tested in `photos.test.ts`; 2 API tests. Not seen in a browser.

- [x] 22. Hideable left sidebar
  - ID: rv-22 (user ask, 2026-11-18)
  - The drives sidebar (`web/src/views/Sidebar.tsx`, laid out in `App.tsx`) collapses to nothing, giving the page its full
    width. Ways to toggle it: a button in the header, `[` as a keyboard shortcut (ignored while typing in a field), and a
    small "Drives" handle on the left edge when hidden. The state is remembered with `usePref`. Collapsing and expanding
    animate with Framer Motion (instant under reduced motion). On narrow screens, where it already overlays or stacks,
    keep the current behaviour and add only the toggle. The sticky inspector and month rail must still line up after a
    toggle.
  - Verify: `cd web && npm run typecheck && npm test`; **[user]** browser check.
  - Result (2026-11-18): header toggle, `[` shortcut (skips fields, contenteditable, open dialogs), left-edge "Drives"
    handle, `shell.sidebarHidden` pref, spring width/opacity (instant under reduced motion). Sticky offsets are
    viewport-based, so Photos is unchanged. Browser check pending.

- [x] 23. Full-size photos when the drive is plugged in, and which drive to plug in otherwise
  - ID: rv-23 (user ask, 2026-11-18, D-077). Builds on rv-20 (viewer) and rv-21 (`/api/groups/<id>/detail`).
  - `GET /api/groups/<id>/preview?full=1`, read-only, nothing ever written to the drive:
    - a JPEG or PNG primary is served as the file itself;
    - a raw gets its full-resolution embedded JPEG (`JpgFromRaw`, then `PreviewImage`);
    - TIFF/PSD get their embedded preview, if any, else the thumbnail.
    It is cached under `<home>/previews/` (keyed by group id and size), and the response says the image's pixel size
    (header or JSON sidecar) so the UI can tell full size from preview. A 409 `{needs: [{volume_id, label, role}]}`
    means no drive holding the file is mounted, and lists the drives that hold it (WORKING first, then by last seen).
  - Viewer (`Lightbox.tsx`): asks for `full=1`, showing the thumbnail first; a small badge reads "6048 × 4024 · full
    size" or "1620 × 1080 · preview". On a 409 it shows a popup card over the image: "Full size is on **PP 1** (or
    Flats 1). Plug one in to see it." with drive chips like rv-21's. While the card is open, the mounts are rechecked
    every 5 s (or through the drives store if it already polls); when one mounts, the full image loads by itself and
    the card goes away. Esc or ✕ dismisses the card, not the viewer.
  - Inspector preview: under the image, one line: "Thumbnail · plug in PP 1 for full size" when not mounted, nothing
    when mounted.
  - Verify: `uv run pytest tests/test_web_api.py -q -k preview`; `cd web && npm run typecheck && npm test`;
    **[user]** browser check with one drive mounted and one not.
  - Grid (user ask, same day): each `GET /api/groups` row gains `full_available` (a primary `indexed`/`verified`
    instance sits on a drive mounted now), `held_on` (up to 3 drive labels) and `held_volumes` (ids, so the UI
    recomputes from the drives store without refetching). One query for all rows. `Tile` shows a small drive-off
    icon (bottom-left, dark backdrop) when unavailable; tooltip "Full size on PP 1 — not connected" or "Not on any
    known drive".
  - Result (2026-11-18): done. The viewer fetches `?full=1` (headers `X-Image-Width/Height/Full`; 409 body `needs`),
    rechecks every 5 s while the card is open. Raw full images are cached in `<home>/previews/full/`; JPEG/PNG
    are streamed from the drive each time (not cached). `uv run pytest -q`, `npm test` green.

- [x] 24. Expand the map into a floating window to place the pin precisely
  - ID: rv-24 (user ask, 2026-11-18)
  - The inspector's map (`web/src/components/MapPicker.tsx`) gets an expand button (top-right, tooltip "Expand map").
    After **Use** on a suggestion or a place result, a small "Refine on map" link under the map opens the same thing.
  - It opens a floating window (the existing `Modal`, or a portal like the lightbox), about 80 % of the viewport, with:
    - the same pin and points;
    - zoom set around the pin (street level when a pin exists, else fit the points);
    - click to move the pin and drag to fine-tune;
    - the coordinates shown live, as the rv-17 Maps link;
    - a place search box (the same Nominatim-on-Enter rules);
    - **Done**, which keeps the new pin as a pending location with source `map` (it then saves through the normal Save),
      and **Cancel** / Esc, which leaves the pin as it was.
  - A satellite / street toggle only if a free tile source already in use allows it. Otherwise OSM only, with no new
    tile provider.
  - Leaflet stays lazy-loaded; the window reuses the inspector's Leaflet instance or creates its own.
  - Verify: `cd web && npm run typecheck && npm test`; **[user]** browser check.
  - Result (2026-11-18): done. Expand button on the small map and a "Refine on map" link after Use open a portal window (80 %, full screen under 640 px) with a street-level pin, click/drag, live Coord link, place search, Done / Cancel / Esc (captured, so only the window closes). OSM tiles only, loaded when the window opens; ResizeObserver handles invalidateSize. `initialView` tested; typecheck, 68 tests, build pass.

- [x] 25. A persistent filter bar above the day headers
  - ID: rv-25 (user ask, 2026-11-18)
  - The drive and "Show" selects, the active filter chips (✕ each, Clear all) and "N photos · N selected" move into one
    sticky bar under the app header (`top-[57px]`, 48 px). Day headers stick below it (`top-[105px]`), and the month rail
    and inspector at `top-[117px]`, with their max heights reduced to match. The day header's own filter note is off,
    since the bar always shows the filters.
  - Result (2026-11-18): `cd web && npm run typecheck && npm test` → clean, 93 passed; build committed. Browser check pending.

- [x] 26. Drive and card file lists as a collapsible folder tree
  - ID: rv-26 (user ask, 2026-11-18)
  - `FileTable` gets a **Folders / List** toggle (remembered per browser). Folders nests the page by `rel_path`, merges
    single-child chains (`DCIM/100NIKON`), and collapses runs of ≥ 5 names that differ only in digits and extension
    (`DSC_0001 – DSC_0150 · JPG · NEF`), closed by default. Folder and run rows show the count, total size and states
    (drive) or tiers (card). Expand all / Collapse all. Logic in `web/src/lib/filetree.ts`. Page size 200 → 1000 (the
    server maximum), since collapsed groups keep the page short. The card list's tree is in name order, not urgency;
    the tier pills on each group carry the urgency, and List keeps the server order.
  - Result (2026-11-18): `cd web && npm run typecheck && npm test` → clean, 133 passed; build committed. Browser check pending.

- [x] 27. Show in Photos from drive and card file lists
  - ID: rv-27 (user ask, 2026-11-18)
  - Photo rows (kinds raw, jpeg, heic, video, derivative) get a **Show in Photos** button. `GET
    /api/volumes/<id>/photo?rel_path=` finds the file's capture group (through its asset when hashed, else its index
    job item) and its timeline day (offset-shoot wall date, else capture date, else `undated`); 404 when the file is
    in no photo. The button opens `#/photos?photo=<group>&day=<day>`, which clears the Photos filters, loads that day,
    centres and flashes the tile (the rv-19 reveal) and selects it, so the inspector opens.
  - Result (2026-11-18): `uv run pytest -q` and `cd web && npm run typecheck && npm test` → green (134 web); build committed. Browser check pending.
  - Fix (2026-11-19, user report: it did not scroll): the reveal waited for the filter key `'|'`, but sh-4 made it
    three parts (`'||'`), so it never ran (Show in All Photos too); it also cleared its own scroll timer by clearing
    `reveal`. Scrolling is now a separate effect that retries until the tile renders, then focuses it; the reveal
    waits for days fetched under the cleared filters. `npm run typecheck && npm test` → 134 passed. Browser check pending.
  - Fix 2 (2026-11-19, user report: still did not scroll to the photo): days not loaded yet render placeholders of at
    most 4 rows, while a loaded day can be thousands of pixels tall. The days the scroll passed, and the ones near the
    target, loaded and grew during and after the smooth scroll, so it ended on other photos; opening the inspector
    narrowed the grid and moved the tile again. `web/src/lib/reveal.ts` `holdCentred` now jumps (no smooth scroll) and
    re-centres the tile every frame it drifts, until it has stayed put for 450 ms (2.5 s at most), and stops at once on
    wheel, touch, key or mouse input. Tested with a fake page that grows above the tile. Browser check pending.

- [x] 28. Right-click menus
  - ID: rv-28 (user ask, 2026-11-19)
  - One menu at a time (`web/src/lib/menu.ts`, host `components/ContextMenu.tsx` in the shell): opens at the pointer,
    flips at the window edges, arrow keys / Home / End / Enter / Esc, closes on a click outside, scroll, resize or route
    change. Shift+right-click keeps the browser's own menu. The Menu key or Shift+F10 open it under the focused element.
  - Built now (the user's main case is the first two):
    - **Photo tile** (Photos): Open (Space) · Show in All Photos (when filtered; "Scroll to this photo" when not) ·
      Select all of <day> · Show only <shoot> · Copy name · Show in Finder (drive connected) · Deselect (Esc).
      Right-clicking a photo outside the selection selects it; inside the selection it keeps the selection.
    - **File row** (drive and card file lists, Folders and List views): Show in All Photos (photo kinds; the rv-27
      route, so it clears the filters, scrolls to the photo and selects it) · Show in Finder (connected) · Show every
      copy / Hide copies (hashed) · Copy file name · Copy path on the drive.
  - Proposed, not built (each would be its own task; numbers are suggestions):
    - **Folder / run row** (file lists): Expand all below / Collapse · Show folder in Finder · Show these files in
      Photos (needs a "photos in folder" filter on the server) · Copy folder path.
    - **Copy entry** (the copies row under a file): Show in Finder · Open that drive's page · Copy path.
    - **Drive row** (sidebar, Home, drive page header): Open · Show in Finder · Photos on this drive (Photos with the
      drive filter) · Index / Hash / Read metadata (the existing steps) · Rename… · Ignore · Copy mount path.
    - **Card row / card tile** (sidebar, Cards): Open · Check card · Import… · Back up… · Clear… (only when green) ·
      Show in Finder · Photos from this card.
    - **Task card** (Home, Tasks): Pause / Resume · Copy the command · Open the drive or card it ran on · Dismiss when
      final.
    - **Action row** (Actions): Do it · Snooze · Dismiss · Copy the command.
    - **Day header** (Photos): Select day · Show only this day's shoot · Copy date.
    - **Shoot chip** (day header) and **shoot card** (Review): Show only this shoot · Rename… · Accept / Split / Merge
      (review only).
    - **Month rail entry**: Jump · Select month (load and select every photo in it; may be large, so it asks first).
    - **Filter chip**: Remove · Remove all others.
    - **Keyword chip** (inspector): Remove from selection · Add to every selected photo · Show photos with this keyword
      (needs a keyword filter).
    - **Inspector map / location suggestion**: Use this location · Open in Maps · Copy coordinates.
    - **Lightbox photo**: Show in All Photos (closes the viewer) · Show in Finder · Copy name.
    - Not planned: header navigation links and form controls keep the browser's menu (open in new tab, paste).
  - Result (2026-11-19): `cd web && npm run typecheck && npm test` → clean; tests for `tidyItems`, `placeMenu`,
    `nextEnabled`, `openMenu`, and the host's keyboard, click and close paths. Build committed. Browser check pending.

- [x] 29. Hover preview in drive and card file lists
  - ID: rv-29 (user ask, 2026-11-19)
  - Resting the pointer on a photo row (kinds raw, jpeg, heic, video, derivative) for 350 ms shows a 240 px preview
    beside the pointer: the row's photo through `GET /api/volumes/<id>/photo?rel_path=` (found photos remembered per
    page load, misses asked again), then `/api/groups/<id>/thumb` (the cached or embedded preview, act-4). "Not in
    Photos yet" when the file is in no photo; "No preview" when its drive was never connected with the preview read.
    Leaving the row, pressing, scrolling or opening the menu hides it. No server change.
  - Result (2026-11-19): `cd web && npm run typecheck && npm test` → clean. Build committed. Browser check pending.

- [x] 30. Recently run tasks on the Tasks page
  - ID: rv-30 (user ask, 2026-11-19; D-109)
  - Finished runs used to show only inside a busy drive's card (3 at most), were lost when `serve` restarted, and
    runs with no mount or started in a terminal never showed. Now every engine run except listing and query
    commands (`QUIET_COMMANDS`: serve, drives, jobs, metrics, volumes list/probe, cards list, meta missing,
    catalog init) appends one line at exit to `<home>/activity/history.jsonl` (`ActivityEmitter.close(exit_code)`):
    words, start and finish times, exit code and state (0 done, 4 paused, else failed; none = crashed), the final
    `result` without its lists (scalars only past 4,000 chars) and the last error. Trimmed to 200 lines past 512 KB.
  - `GET /api/activity` adds `recent` (newest 30). Tasks shows "Recently run": the newest 8 with a "Show all" button,
    each with its title, plain-words result (same `summarize` as UI tasks) and how long ago it ended. A UI task's
    end re-reads it at once; the "terminal run has ended" toast now points there. No schema or protocol change.
  - Verify: `uv run pytest tests/test_activity.py -q`; `cd web && npm run typecheck && npm test`.
  - Result (2026-11-19): 33 activity tests pass; web 166 tests clean; full suite 772 passed, 4 skipped. Build
    committed. Browser check pending.
  - Follow-up (user, 2026-11-19): Tasks now reads Running → Recently run → Idle drives. Running holds every live
    task (drive cards, then "Not on a drive"), and its empty card shows only when nothing at all runs (before, a
    task with no drive, e.g. `shoots plan`, still showed "Nothing is running"). Every command has a title: backup
    (card, Make green, folder), meta phone, shoots plan/apply/undo, previews cache, report duplicates, lrcat mark,
    metrics backfill; an unknown one reads "Running <command>" instead of "Working". A running backup shows on its
    `--from`/`--to` drives' cards, and previews cache on its drive's.
  - Result (follow-up): `cd web && npm run typecheck && npm test` → 167 passed. Build committed.

- [x] 31. One block per run on the Tasks page
  - ID: rv-31 (user ask, 2026-11-19; D-112)
  - A run on two drives (an import card -> target) showed twice, once per drive group. Now Running has one block per
    run: a header naming its drives (role badge, name, free space, Open link each, joined by arrows) above the run's
    card. A `backup --follow <job>` run is nested under its import as "Then" (links: the follow run's `--follow N`
    equals the import's `job_id`, from its `result` event for UI tasks or `job.job_id` for terminal runs). A follower
    whose parent is not live, or has not reported its job id yet, stays a block of its own. UI-started finished runs
    are one "Finished this session" list instead of per drive; Recently run (rv-30) is unchanged; idle drives keep
    their last-run line. Pure grouping in `web/src/lib/runGroups.ts`.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 176 web tests pass (17 files); build committed. Not seen in a browser.


- [x] 32. Minimize long-running dialogs; notify when long tasks finish
  - ID: rv-32 (user ask, 2026-11-19; D-115)
  - Clear card, Import and Make green get a Minimize button (a click outside minimizes too; Esc still closes). Their
    state moved from `Cards` to `DialogsProvider` (`web/src/lib/dialogs.tsx`) with one `DialogHost` in `App.tsx`, so a
    minimized dialog stays mounted (polling and dry run continue) across pages. A dock of pills at the bottom right
    shows each one's live status line and bar; a click restores. When a task that ran 20 s or more ends, a popup
    (Open / Dismiss; failures stay) shows, plus a browser notification when the tab is hidden, if allowed. A dry run
    under a visible dialog gets no popup. Settings has the on/off switch (per browser). Pure rules in
    `web/src/lib/notify.ts`.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 189 web tests pass (18 files, 8 new in `notify.test.ts`); build committed. Not seen in a browser.

- [x] 33. "New device detected" highlight; sort and filter the drives sidebar
  - ID: rv-33 (user ask, 2026-11-19; D-116)
  - A drive plugged in since the last poll (or since the page was last open), seen before or not, glows in the
    sidebar with a "New" chip, floats to the top of its group, and is named in a "New device detected" banner above
    the list. It fades out once the user interacts with it: opens its drive, card or setup page (or is on that page
    when it is plugged back in), runs something on it (any UI or terminal task whose mounts include it), or clicks
    "New" / the banner's dismiss. Unplugging clears it; the very first visit flags nothing. A drive whose key changes
    at setup (same mount) is not re-flagged. Tracked in the shell (`DriveActivityProvider`), so it keeps watching
    while the sidebar is hidden; stored per browser (`pv:driveSeen`), no server change.
  - The Drives list gains Sort (Default, Recently used, Type, Format, Size, Free space, Name) and a Filter panel of
    Type and Format chips (only values present show; any value within a facet, all facets must match). Sorting is
    within each group (Mounted, Not connected, Ignored); a new drive ignores the filter. "Recently used" = the later
    of an interaction this browser saw (open, task, plug-in) and the drive's last step run (`steps.at`). Choices are
    remembered per browser. Pure rules in `web/src/lib/driveList.ts`.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 215 web tests pass (20 files, 16 new in `driveList.test.ts`); build
    committed. Not seen in a browser.

- [x] 34. Tasks page: runs side by side, with stats and file names
  - ID: rv-34 (user ask, 2026-11-19; D-117)
  - Like the card page, an import and its follow-up copy sit side by side on Tasks (one column below `lg`); the copy
    carries a "Then" badge. The full card shows stat tiles (files done / total, then the numbers in the progress
    message such as copied bytes, speed and failures, then elapsed), the latest file handled (name large, source
    folder → target folder, its state, any error), and the five before it (Copied / Already there / Failed;
    Deleted for a card clear). A hash with no `item` events shows the file it is reading. The compact card (sidebar,
    card page) adds the latest file name under its bar.
  - File names come from the existing `item` events (no protocol change): the store keeps the last 6 per task;
    terminal runs get the same through the progress file (`files`, last 6, written with the throttled progress).
    `item` is emitted once a file is done, so "latest" is the file just finished, not the one in flight.
  - Verify: `uv run pytest tests/test_activity.py -q`; `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): 28 activity tests pass (1 new); typecheck clean; 219 web tests pass (4 new); build
    committed; `uv run pytest -q` → 796 passed, 4 skipped. Not seen in a browser.

- [x] 35. Drives open from their own chips; busy drives shaded in the sidebar
  - ID: rv-35 (user ask, 2026-11-19; D-118)
  - Tasks: the Open buttons are gone from a run's drive header and from the idle-drive cards; the drive chip (role,
    name, free space) or the drive's name is the link, underlined on hover.
  - Sidebar: a drive a live task (UI or terminal) is reading or writing gets a tint with a slow sheen passing over
    it (accent = reading, green = writing; still under reduced motion), and its status line says what is happening,
    e.g. "Copying to Backup Plus" on the source, "Receiving from Card A" on the target, both on a drive that is
    written by an import and read by its backup. Hash / index / metadata / card check read; a card clear writes; a
    dry run only reads its source. Pure rule `driveIO` in `web/src/lib/labels.ts`. The "New" glow wins over it.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 222 web tests pass (3 new); build committed. Not seen in a browser.

- [x] 36. Make green shows like the other copies
  - ID: rv-36 (user report, 2026-11-19; D-119)
  - `backup --card <id> --make-green` names no drive in its words, so its run had no drive header, no sidebar
    shading and no "from → to". Its drives now come from the card id (drive list) and the route it reports
    ("copying N file(s) from A to B"); the server keeps the newest route per task (`route` in the task summary),
    since copy progress overwrites it. The card is shown first in the header but not shaded (it is not read).
    `runMounts` in `web/src/lib/labels.ts`; the sidebar's busy-drive keys use it too. Terminal make-green runs still
    show no drives (their progress file keeps only the newest message).
  - Stat tiles now split a copy message without a file path ("2.8 GB copied, 48.7 MB/s, 0 failed"); rv-34 left it as
    one tile. The latest-file box names the drive pair and prints the folder once when both sides match.
  - Verify: `uv run pytest tests/test_web_api.py -q`; `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): 2 route/job-id server tests pass (1 new); 225 web tests pass (3 new); build committed.
    Needs a `serve` restart for the server's `route`. Not seen in a browser.

- [x] 37. A running task opens its run on the Tasks page
  - ID: rv-37 (user ask, 2026-11-19; D-120)
  - New route `#/?task=<key>` (`t<id>` a UI task, `p<pid>` a terminal run): Tasks scrolls that run's card into view
    and rings it for 2.5 s. Linked from: the task cards on a card's page and in the sidebar (click anywhere but their
    Pause / Details / Technical details; the title is a real link for keyboard and new-tab use), and on a drive's
    page the running step's progress and the "Running · … / Busy" chip (which now also finds an import or backup on
    the drive, `liveTaskKey`; between workflow steps it opens Tasks).
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 227 web tests pass (2 new); build committed. Not seen in a browser.

- [x] 38. Unplugged drives: notice, sidebar, page edge and "last seen"
  - ID: rv-38 (user ask, 2026-11-19; D-121)
  - When a connected drive turns up not connected at the next poll (ignored drives excepted; first load reports
    nothing), a popup says "<name> was unplugged." with Open (a warning, "while a task was using it", if a live task
    named it). The sidebar opens its Not connected group, and the row shows amber dashes, pulses for 8 s and reads
    "Unplugged just now". The page stays where it is. A drive or card page whose device is not connected gets a
    dashed amber edge (pulsing for 8 s after the unplug) and a strip: Last seen (the unplug time when seen here, else
    `last_seen_at`), Last mounted at, Free when last seen, and on a card, Last checked. The card page now takes
    "connected" from the live drive list (its card data was as of the last load, so it still said connected).
  - Pure rules `unplugged`, `driveHref` in `web/src/lib/driveList.ts`; detection in `DriveActivityProvider`; the strip
    and edge in `web/src/components/Unplugged.tsx`.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 230 web tests pass (3 new); build committed. Not seen in a browser.
    Committed with rv-39 (same sidebar and CSS lines).

- [x] 39. Sidebar colours: one meaning per channel, with a Key
  - ID: rv-39 (user ask, 2026-11-19; D-122)
  - The selected row, the "New" glow and busy rows all used the theme accent, so a warm palette (Forest · Ember) made
    them read as warnings. Now: role = dot + pill (unchanged); the page you are on = a fixed blue (`select`, a new
    scheme token beside the status colours, same in every palette); just plugged in = green ring, "New" chip and
    banner, and the row lands on the list (drops in with a lifted shadow that settles, if plugged in under 10 s
    ago); read or written by a task = a neutral shimmer, with ↑ / ↓ / ↓↑ before the status line (replaces rv-35's
    accent/green tints); unplugged = amber dashes; not connected = dimmed. Filter chips use `select` too. A "Key"
    chip opens a legend drawn with the real styles. Reduced motion stops the shimmer, landing and pulses.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): as rv-38. Not seen in a browser.

- [x] 40. Rename is visible on cards, drives and the sidebar
  - ID: rv-40 (user report, 2026-11-19; D-123)
  - Rename existed only on a set-up drive's page, as a faint ghost button; cards had none. Now one `RenameTitle`
    (`web/src/components/RenameTitle.tsx`) titles both the drive page and a card's page: the name, a bordered
    "Rename" button (double-click the name works too), inline Save / Cancel. The sidebar shows "Rename" on hover
    beside "Ignore" for every set-up drive or card; it opens that page with the name already editing. A card keeps
    its hidden `card:` prefix (`labelToSave`). After a rename, pages reload (epoch) so a card's headline and the
    Cards list pick up the new name; the popup shows the name without the prefix. Same server endpoint
    (`POST /api/volumes/<id>/label`, catalog nickname only; the volume on disk is not renamed).
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 232 web tests pass (2 new); build committed. Not seen in a browser.

- [x] 41. "Recently used" counts device use only
  - ID: rv-41 (user report, 2026-11-19; D-124, revises D-116)
  - Opening a drive's page, clicking its row, renaming it or dismissing "New" used to count as use and jump the drive
    to the top of "Recently used". Now those only mark it seen (`seeDrive`: clears "New", order unchanged). What
    moves a drive: being plugged in, a task reading or writing it (`touchDrive`, at most once a minute per running
    task), and a step run on it from the catalog (`steps.at`). The per-browser store moved to `pv:driveSeen2`, so
    times recorded under the old rule are dropped.
  - Verify: `cd web && npm run typecheck && npm test && npm run build`.
  - Result (2026-11-19): typecheck clean; 234 web tests pass (2 new); build committed. Not seen in a browser.
