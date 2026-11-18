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

- [ ] 1. Ownerless `running` jobs become `paused`
  - ID: idx-9 (approved, D-069)
  - At the start of any job-taking command and in `activity.snapshot`, a job in state `running` whose owner pid
    is not alive (per `photovault/activity.py`) is set to `paused`, with a `warning` event naming the job. A job
    whose pid is alive is never touched. The UI's "Marked running, but stopped" list then empties on its own.
  - Verify: `uv run pytest tests/test_activity.py -q` with a test for each case.

- [ ] 2. Drive usage: more camera stems, and no ExifTool on a no-op re-index
  - ID: idx-14 (approved, D-069)
  - `photovault/usage.py` `_CAMERA_STEM` also matches DJI Fly and export stems (`dji_fly_…`, `dji_export_…`) and
    PhotoVault's own stem (`M\d{4}_\d{8}-\d{6}…`, `system-design.md` §6).
  - When a re-index finds no `video` instance to (re)classify and `volume_usage` already holds
    `video_no_camera` for the drive, reuse the stored count instead of running ExifTool over every movie.
  - Verify: `uv run pytest tests/test_usage.py -q` (stem cases; a re-index test that asserts ExifTool is not called).

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

- [ ] 5. Related photos: tighter sessions, and field-scoped edits
  - ID: rv-16 (approved, D-069; refines rv-9, D-065)
  - `web/src/lib/photos.ts` `relatedSession`: default gap 15 min (not 1 h); apply each group's `time_shift_s`
    before measuring gaps; break the chain at a `shoot_id` change and at a named non-DCIM parent folder change.
  - `PhotoInspector.tsx`: related photos receive session fields (GPS, place / city / state / country, creator,
    copyright, clock shift) by default. Title, keywords and rating go to them only with a toggle "Also apply
    Title, Keywords & Rating to related", off by default.
  - Verify: `cd web && npm test` (photos.test.ts cases for each break); **[user]** visual check.

- [ ] 6. **[stronger]** Schema V6 proposal: ratings, picks, labels, harvested shoot names and tiers
  - ID: cu-0 (**proposal first**; catalog change, rule 6)
  - Propose in one migration: `group_meta.rating INTEGER` (0..5), `group_meta.pick INTEGER NOT NULL DEFAULT 0`
    (-1 | 0 | 1), `group_meta.color_label TEXT`, and wherever the review finds best, a harvested shoot name and a
    lifecycle tier (`raw_selected`, `raw_unselected`, `processed`, `exported`, `social`) per instance or group.
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
