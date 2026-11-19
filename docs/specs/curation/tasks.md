# Spec: curation — approved engine fixes, shoots, culling, Lightroom sync, safe dedupe and reorganisation

Source: the user's roadmap of 2026-11-18 (D-069). Each task is self-contained so it can be handed to a fresh
session. A session starts with `START_HERE.md`, reads only its task below and the `system-design.md` sections the
task cites, follows `docs/steering/task-execution.md`, and commits one task per commit after `/close-the-loop`.

Rules for every task:
- **Approved (D-069)** means the user approved the change in the roadmap; build it.
- **Proposal first** means the task's first deliverable is a review in `docs/reviews/` naming the exact rule,
  schema, naming or signature change. Stop and ask the user, and write no code until they approve.
- Vault rules hold. No writes to the mounts folder by agents; real-drive runs are **[user]** steps that print the
  command. Nothing here weakens rule 3 (one writer for media bytes) or rule 5 (identity is the SHA-256).
- Privacy: example shoot names in code, tests and docs are invented (`Harbour Moonrise`), never real trips.
- UI work: `cd web && npm run typecheck && npm test && npm run build`; commit the build.

## Wave 1: approved engine fixes (no schema change)

Existing tasks, now unblocked (D-069): **ing-13** (`docs/specs/ingest/tasks.md`) and **idx-10**
(`docs/specs/index/tasks.md`: repeatable `--was-mounted-as <old_mount>`, `off_volume` / `paths_elsewhere` in the
result). Build them as written there.

- [x] 1. Ownerless `running` jobs become `paused`
  - ID: idx-9 (approved, D-069)
  - At the start of any job-taking command and in `activity.snapshot`, a job in state `running` whose owner pid
    is not alive (per `photovault/activity.py`) is set to `paused`, with a `warning` event naming the job. A job
    whose pid is alive is never touched. The UI's "Marked running, but stopped" list then empties on its own.
  - Verify: `uv run pytest tests/test_activity.py -q` with a test for each case.
  - Result (2026-11-18): `activity.pause_ownerless` (used by `snapshot` and by `cli.main` before ingest, resume, index, hash and cards) pauses running jobs no live process owns (mount match or `resume <id>`) and emits a `warning`; tests in `tests/test_activity.py`.

- [x] 2. Drive usage: more camera stems, and no ExifTool on a no-op re-index
  - ID: idx-14 (approved, D-069)
  - `photovault/usage.py` `_CAMERA_STEM` also matches DJI Fly and export stems (`dji_fly_…`, `dji_export_…`) and
    PhotoVault's own stem (`M\d{4}_\d{8}-\d{6}…`, `system-design.md` §6).
  - When a re-index finds no `video` instance to (re)classify and `volume_usage` already holds
    `video_no_camera` for the drive, reuse the stored count instead of running ExifTool over every movie.
  - Verify: `uv run pytest tests/test_usage.py -q` (stem cases; a re-index test that asserts ExifTool is not called).
  - Result (2026-11-18): `_CAMERA_STEM` matches `dji_fly_`, `dji_export_` and `M\d{4}_\d{8}-\d{6}` stems; `index_volume` passes the stored `video_no_camera` to `usage_rows` when no video was added, changed or gone; `tests/test_index.py` asserts ExifTool sees no movie on a no-op re-index.

- [ ] 3. **[stronger]** Camera bracket flag and steps, and the production segmenter
  - ID: seq-1b (approved, D-069: eval-1's four answers; `docs/reviews/2026-11-11-eval-1-sequencing.md`)
  - Read `ShootingMode` and `ExposureBracketValue` in `photovault/metadata.py` (`EXIFTOOL_TAGS` and
    `normalize_exiftool_row`). If storing them needs a catalog column, stop: that is a schema proposal.
  - Bracket steps accepted: 1/3, 2/3, 1, 2, 3 and 6 EV.
  - Amend seq-2 (`docs/specs/sequences/tasks.md`): Approach 4 (DP search segmentation) is the production
    segmenter; the camera's bracket flag wins when present; `detect_runs` stays for night runs.
  - Verify: `uv run pytest tests/test_metadata.py tests/test_sequences*.py -q`.

- [ ] 4. Auto-rotate suggestions from metadata only
  - ID: rv-11 (approved, D-069; `docs/specs/revamp/tasks.md` rv-11)
  - The signal is EXIF `Orientation` plus MakerNotes `RollAngle` / `PitchAngle`. No pixels (rule 9 unchanged).
  - A suggestion rotates the preview in the UI and writes `tiff:Orientation` to the XMP sidecar, never the file.
    If keeping an accepted rotation needs a catalog column, it waits for cu-0.
  - Verify: unit tests for the angle → orientation mapping; `npm test`; **[user]** visual check.

## Wave 2: related photos and shoots

- [x] 5. **[stronger]** Related photos: a gap ladder with Auto, and a scope per field
  - ID: rv-16 (approved, D-069; amended by D-070, which replaces the "Also apply Title, Keywords & Rating" toggle;
    refines rv-9, D-065). No schema change. The phone-track parts are sp-6.
  - API: each `GET /api/groups` row gains `utc_time` (`geo.group_utc` with the `default_offset` setting, clock shift
    applied; null without a capture time), `body_id` and `sequence_id`.
  - `web/src/lib/photos.ts` `relatedSession`:
    - Gaps are measured on `utc_time`, not the naive capture time.
    - Gap ladder: 1 min, 5 min, 15 min, 1 h, 3 h, Same day, and **Auto** (the default). `relatedCounts` returns the
      session size for each numeric step.
    - Auto skips steps whose session is empty. It takes the step just before the largest jump `c[i+1] / c[i]` when
      that jump is ≥ 1.5. With no such jump it takes the largest non-empty step up to 15 min, else 15 min. The chip
      reads "Auto · 5 min".
    - The chain breaks at a `shoot_id` change and at a change of named non-DCIM parent folder.
    - Frames sharing a non-null `sequence_id` join or leave the session together; a bracket is never split.
  - `PhotoInspector.tsx`, an "Applies to" block. Each row has a scope picker and a live count:
    - Place (GPS, place name, city / state / country): the session (Auto by default).
    - Description (title, keywords; rating once cu-0 lands): Selection only by default; it can widen to 1 min, 5 min,
      15 min and so on.
    - Credit (creator, copyright): Same day by default.
    - Clock shift: **same camera body only**, never another camera or the phone. Options: Selection only, Same camera ·
      this session, Same camera · same day (the default).
    - The ticked related list (outlined in the grid) is the Place scope. Unticking a photo removes it from every
      scope. Each scope's choice is saved with `usePref`.
  - Verify: `cd web && npm test` (photos.test.ts: each ladder step, Auto picking before the jump and its fallback, a
    UTC-shifted pair, shoot/folder breaks, a sequence kept whole, clock shift never crossing bodies);
    `uv run pytest tests/test_web_api.py -q -k groups`; **[user]** visual check.
  - Result (2026-11-18): `uv run pytest -q` → 528 passed, 9 skipped; `cd web && npm test` → 43 passed; typecheck and build clean. The visual check is still the user's.

- [ ] 6. **[stronger]** Schema V6 proposal: ratings, picks, labels, harvested shoot names and tiers
  - ID: cu-0 (**proposal first**; catalog change, rule 6)
  - Propose in one migration: `group_meta.rating INTEGER` (0..5), `group_meta.pick INTEGER NOT NULL DEFAULT 0`
    (-1 | 0 | 1), `group_meta.color_label TEXT`, and wherever the review finds best, a harvested shoot name and a
    lifecycle tier (`raw_selected`, `raw_unselected`, `processed`, `exported`, `social`) per instance or group.
  - Also (approved, D-070): `geo_points.local_offset TEXT` (the fix's own UTC offset, `+HH:MM`) and
    `geo_points.kind TEXT` (`photo` | `video`), for sp-2 and sp-7. If sp-2 starts before cu-0 is approved, these two
    land alone as V6 and cu-0 becomes V7.
  - Review file `docs/reviews/<date>-cu-0-schema-v6.md` with the SQL, the §5 text, and how cu-1, cu-3, lr-1..lr-3
    use each column. The user approves before any migration.

- [ ] 7. **[stronger]** Harvest curation from legacy folder paths
  - ID: cu-1 (after cu-0 approval; catalog writes only, never a drive)
  - `photovault/harvest.py` reads `instances.rel_path` on every volume:
    - Shoot names: drop container and camera tokens (`DCIM`, `100NCZ_6`, `Card Export`, `Card A`,
      `Camera Imports`, `Imports`, `Exports`, `Raw`, bare `YYYY` / `YYYYMMDD`); a remaining human folder name
      (`20261027 - Harbour Moonrise`) becomes the suggested shoot title, ahead of a GPS city name.
    - Tiers: `Selected - Raw/`, `Selects/`, `Keepers/` → pick 1 and `raw_selected`; `Processed/` or
      `.psd/.psb/-Edit/-HDR/-Pano` → `processed`; `Instagram/`, `Social Media/`, `-Tall/-Canvas/-Tight.jpg` →
      `social`; other files under `Exports/<Shoot>/` → `exported`; other originals → `raw_selected` if pick 1 or
      rating ≥ 1, else `raw_unselected`. Derivatives bind to the shoot even without a shutter count.
    - Inheritance: copies of one asset (SHA-256) or one capture group across folders and drives share the richest
      name, pick 1 and tier, so nothing is lost when duplicates are later removed.
  - CLI `harvest [--dry-run]`; idempotent; a report of what it would set.
  - Verify: `uv run pytest tests/test_harvest.py -q` on synthetic trees.

- [ ] 8. Manual shoot controls
  - ID: cu-2 (after sp-4, which is shelved, D-067)
  - `#/shoots`: "Merge selected shoots" (multi-day trips split by sleep gaps). Inspector: a Shoot picker that
    assigns to an existing shoot, a new one, or none.
  - Verify: API tests for merge / assign; `npm test`; **[user]** visual check.

- [ ] 9. Shoots view with five lifecycle tiers
  - ID: cu-3 (after cu-1, cu-2)
  - A Shoots mode in `#/photos`: per shoot, file counts and bytes for Raw Selected, Raw Unselected, Selected
    Processed, Exported Processed and Social Media, a stacked storage bar, and one-click tier filters
    (`SelectTile`, rv-13).
  - Verify: `npm test`; **[user]** visual check.

- [ ] 9b. **[stronger]** Processed images without EXIF find their originals (proposal first)
  - ID: dv-0 (user ask, 2026-11-18, D-071; **proposal first**: a link table is a schema change and joins cu-0's V6)
  - Case: an astro stack or blend goes Lightroom → stacker → Photoshop → TIFF → Lightroom and loses its EXIF (no
    capture time, GPS or camera). It ends up under Undated, and no time-based match can reach it.
  - Signals, metadata only (rule 9), strongest first: (1) the Lightroom catalog, read-only (rule 7): stacks
    (`AgLibraryFolderStackImage`) with originals, plus Lightroom's own `captureTime` for the TIFF; (2) XMP embedded in the TIFF
    that survived: `crs:RawFileName`, `xmpMM:DerivedFrom`, `photoshop:DocumentAncestors`, and `xmpMM:History` (tools
    and dates); (3) the filename: original stems or ranges inside the name, and more suffixes (`-Edit-2`, `_stacked`,
    `-Blend`); (4) the folder: next to the raws, or a folder that names a shoot (cu-1); (5) the file's dates as an
    upper bound (it was captured before it was processed); (6) pixel dimensions that point to a camera body.
  - Proposal: `group_links (child_group_id, parent_group_id, kind ('edit'|'stack'|'blend'|'pano'), source
    ('lrcat'|'xmp'|'name'|'folder'|'user'), confidence)`. One derivative can have many parents (sky frames +
    foreground). A linked derivative takes its parents' time range (`time_source = 'derived'`), location, place and shoot,
    shows on their day with a "stack of N" badge, and gets a "Find originals" helper in the inspector for the rest
    (ranked candidates, often a night run from seq-2).
  - Open for the review: Lightroom ignores `.xmp` sidecars for TIFF/PSD/JPEG and reads only embedded XMP, and
    embedding changes the file's bytes and hash (rules 3, 5). So derived metadata for these files stays in the
    catalog until the user picks a way to get it into Lightroom.

## Wave 3: culling and Lightroom Classic

- [ ] 10. Keep Lightroom 15 `.acr` sidecars with their raws
  - ID: lr-0 (approved, D-069; small)
  - Add `"acr"` to `SIDECAR_EXTS` in `photovault/scan.py` (and `_CAMERA_SIBLINGS` in `usage.py` if it applies).
  - Verify: `uv run pytest tests/test_scan.py -q` with a raw + `.acr` pair.

- [ ] 11. Keyboard culling in Photos
  - ID: lr-1 (after cu-0 is approved and migrated)
  - `P` pick, `X` reject, `U` unflag, `0`–`5` stars, an auto-advance toggle (Shift+key advances once); filter
    pills Picked, ≥ N ★, Unrated, Rejected. Saved through `POST /api/groups/meta`.
  - Verify: API tests for the new fields; `npm test`; **[user]** visual check.

- [ ] 12. **[stronger]** Read ratings, picks and labels from Lightroom
  - ID: lr-2 (after cu-0)
  - `lrcat.py` joins `Adobe_images` (rating, pick, colorLabels) on `rootFile = AgLibraryFile.id_local`, read-only,
    never while a `.lrcat.lock` exists (rule 7). `meta scan` also reads `xmp:Rating` and `xmp:Label` from sidecars.
    A Lightroom value fills an empty PhotoVault field and never overwrites one the user set.
  - Verify: `uv run pytest tests/test_lrcat.py tests/test_meta*.py -q` on a synthetic catalog.

- [ ] 13. **[stronger]** Merge into existing XMP sidecars instead of refusing them
  - ID: lr-3 (after cu-0; protocol of `xmp.write_sidecar` changes, see `system-design.md` §3)
  - Parse an existing `.xmp` with `ElementTree`, update only PhotoVault's tags (`xmp:Rating`, `xmp:Label`, GPS,
    place / city / state / country, creator, copyright, title, keywords, `tiff:Orientation`) and keep everything
    else, including all `crs:*` develop and mask settings, byte-for-byte where untouched. `SidecarConflict` stays
    for a sidecar that changed since it was read. Propose the §3 signature change in the task's review first.
  - Verify: round-trip tests on a sidecar with `crs:` settings and masks.

## Wave 4: settings and rename

- [ ] 14. **[stronger]** Settings architecture spike
  - ID: set-1 (design only)
  - Split scoped settings (a drive or card gear drawer; shoot and photo settings in `#/shoots` and the inspector)
    from a global `#/settings` with four tabs: Storage & safe-to-format policy; Import & naming template (live
    filename preview, noon boundary); Cameras & devices (body nicknames, rental flag, per-body clock drift);
    Metadata, iPhone GPS & Lightroom catalogs. Today's page holds creator, copyright, default offset, match window,
    Mac folder and GPS reference folders.
  - Deliverable: `docs/reviews/<date>-set-1-settings.md` with the map of every setting, where it lives, and the
    follow-up tasks; the user picks.

Rename the volume on disk is **vn-2** (`docs/specs/backup/tasks.md`), unchanged; rv-14 covered the nickname.

## Wave 5: proposals that change vault rules or the naming spec

- [ ] 15. **[stronger]** Proposal: duplicate quarantine on a drive
  - ID: dd-0 (**proposal first**; rule 1 and rule 4 exception, user approval)
  - The goal is to free the duplicate space the index found (Flats 1 is 98 % full; `docs/private/` has the
    numbers). Draft the exact rule text. A user-confirmed run moves redundant **verified** copies on one WORKING
    or LEGACY drive into `<mount>/.PhotoVault-Duplicates-Trash/<rel_path>` with a same-volume `os.rename`, writes
    an undo manifest, and marks those instances `missing`. Never deletes.
  - Required guards to write into the proposal: harvest (cu-1) has run; the keeper is elected
    `lr_referenced = 1` > canonical shoot path > shortest path; an `lr_referenced = 1` instance is never moved; the
    keeper is re-hashed uncached and must match before any move; a dry-run preview in the CLI and the drive page
    ("Quarantine N duplicate files (X GB)") comes first; an undo command restores from the manifest.
  - Deliverable: `docs/reviews/<date>-dd-0-quarantine.md`. Implementation is dd-1, only after approval.

- [ ] 16. **[stronger]** Proposal: one shoot folder layout, and reorganising into it
  - ID: ro-0 (**proposal first**; naming spec §6 change, and a rule exception for Mode B)
  - Layout: `<working_subdir>/<YYYY>/<YYYYMMDD>[ - <Shoot Name>]/` with `Raw/<Body>/` (originals + `.xmp` / `.acr`;
    `lights, darks, flats, bias, foreground, brackets` under `--astro-layout`), `Processed/`, `Exports/`, `Social/`.
  - Mode A (LEGACY → MASTER / WORKING): copy unique verified assets once through `copy_verified` into the layout,
    with merged XMP. Uses the existing writer; needs only the §6 change.
  - Mode B (same-volume moves on WORKING / MASTER after a shoot rename): dry-run diff, `lr_referenced` check
    (block, or relink via vn-3), `os.rename` per capture group with its sidecars, size + partial-hash check,
    `instances.rel_path` updated in one transaction per group. Needs a rule exception.
  - Deliverable: `docs/reviews/<date>-ro-0-layout.md`. Implementation is ro-1 (Mode A) and ro-2 (Mode B) after
    approval.
