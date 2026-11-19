# Spec: backup — a card's workflow onto working and backup drives, drive health, volume names (D-050..D-052)

Goal: from the Cards view, one **Run workflow** button takes a card from red to green. It checks the card,
checks every drive involved, lets the user tick which working and backup (MASTER) drives to copy onto, shows a
dry run, and after a confirm copies the card onto a working drive while a second job copies each verified file
from that working drive onto the backup drive, trailing the import (D-050). Copies over 1 GB first need a fresh
drive health check (D-051). Separately, a drive shows its real volume name on hover when it differs from its
nickname, and can be renamed to match (D-052).

Rules for every task:
- vault-rules apply. Targets are WORKING and MASTER only; CARD and LEGACY stay read-only (rule 1). The one
  exception is a volume rename (D-052, rule 1 amended).
- All media bytes go through `copy_engine.copy_verified` (rule 3); nothing is overwritten or deleted (rule 4).
- No schema change: the backup job uses the existing `replicate` job kind; health reports live in `settings`.
- No new dependency: health reads `diskutil info -plist` with `plistlib`; no `smartctl`.
- Tests use `tmp_path`, fakes for `subprocess.run`, `volumes.probe` and `os.statvfs`; never `/Volumes`.
- Real runs (the 12 TB assign, the first backup, a real rename) are **[user]** steps.

## Shared definitions

**Backup job** (kind `replicate`): source = a mounted WORKING volume, target = a mounted MASTER volume (or a
second WORKING volume). Items are the assets of one card whose source has a `verified` instance and whose
target has none. `dst_rel` = the source instance's `rel_path` (the backup drive mirrors the working layout).
Each copy reads the source, and the copy's SHA-256 must equal the asset's catalog hash, not only the
re-read. A mismatch marks the source instance `mismatch` and fails the item.

**Trailing (`--follow <ingest job id>`):** the backup job copies each ingest item once it is `committed`,
polls every 2 s for more, and finishes when the ingest job is `done` and the queue is empty. If the ingest
pauses or fails, the backup copies what is committed, then pauses with `upstream_paused`. One writer per
drive: never two copy jobs onto the same target at once (refused at plan time).

**Health report** (one per drive, stored as `settings` key `health:<volume_id>`, JSON with `taken_at`):
level `ok` | `warn` | `stop`, plus findings, each with a corrective action written for the user.
- `stop`: SMART status `Failing`; a target that is read-only, missing its marker or short of space (planned
  bytes + 5 %); a short read probe hit `EIO`.
- `warn`: unreadable or `mismatch` instances recorded on this drive (idx-11), a paused `drive_gone` job on it,
  more than 90 % full after the copy, read probe under 20 MB/s, or a spinning disk on exFAT (no journal:
  unplugging mid-write can corrupt the file system).
- `info`: SMART `Not Supported` (most USB enclosures), bus protocol, SSD or HDD, file system.
- Corrective actions, for example: "Run First Aid in Disk Utility on <drive> (`diskutil verifyVolume`), then
  check again"; "Plug <drive> straight into the Mac, not a hub; use its own power supply"; "Copy what is only
  on <drive> elsewhere, then replace it"; "Free <n> GB or pick another drive".

**Gate (D-051):** an `ingest` or `backup run` that plans over 1 GB (10^9 bytes) needs a health report under
30 minutes old for the source and every target. `stop` refuses. `warn` needs `--accept-warnings` (the UI asks
for a tick per warning). A dry run is never gated; it reports what the gate would say.

## Tasks

- [ ] 1. **[stronger]** Drive health check
  - ID: bk-1 (D-051)
  - `photovault/health.py`: `check_drive(conn, mount, *, planned_bytes=0, run=subprocess.run) -> dict`
    (read-only). Uses `diskutil info -plist` (`SMARTStatus`, `SolidState`, `BusProtocol`, `FilesystemType`,
    `WritableVolume`), `os.statvfs`, catalog counts (unreadable/mismatch instances, paused `drive_gone` jobs),
    and a read probe of up to 256 MB of existing files (`"rb"`, F_NOCACHE). Saves the report to `settings`.
  - CLI: `drives health <mount> [--planned-bytes N]`. Web: `POST /api/run` `drive_health`, `GET
    /api/drives/<volume_id>/health`.
  - Verify: `uv run pytest tests/test_health.py -q`

- [ ] 2. **[stronger]** The 1 GB gate
  - ID: bk-2 (after bk-1; D-051)
  - `ingest` (run, not `--dry-run`) and `backup run` call `health.gate(conn, volume_ids, planned_bytes)`;
    `stop` → error `health_stop`, `warn` without `--accept-warnings` → error `health_warn`, each listing the
    findings and actions. Dry runs emit the gate's verdict as a `warning` event and never fail on it.
  - Verify: `uv run pytest tests/test_health.py tests/test_ingest.py -q`

- [x] 3. **[stronger]** Backup job (WORKING → MASTER, trailing an import)
  - ID: bk-3 (D-050)
  - `photovault/backup.py`: `plan_backup(conn, cfg, *, card_volume_id, source, target, follow_job=None,
    emit) -> job_id` and `run_backup(conn, cfg, job_id, emit, should_stop)`, per the shared definitions.
    Resume like `ingest.run_job` (sweep `.pvpartial`, re-check committed items). An existing file at
    `dst_rel` with the same hash is registered `verified` (`skipped_dup`); different bytes fail the item.
  - CLI: `backup --card <volume_id> --from <mount> --to <mount> [--follow <job>] [--dry-run]
    [--accept-warnings]`; `resume <job>` handles `replicate` jobs.
  - Fault tests (as ing-10): target unplugged mid-file, source bytes differ from the catalog hash, ingest paused
    upstream, a second job onto the same target refused, a concurrent ingest writing the catalog.
  - **Amended (D-101):** a second source kind, a folder on any drive (LEGACY included, read only):
    `plan_backup(..., source_folder=<rel>)` / CLI `backup --from-folder <mount>/<folder> --to <mount> [--dry-run]`.
    Items are the folder's files; `dst_rel` mirrors `<drive label>/<folder rel>` under `working_subdir`'s parent
    `Photography/Backups/`. Files not yet hashed on the source are hashed during the copy (read once). Targets:
    WORKING/MASTER on exFAT or APFS only (NTFS refused). Web: `backup` command with both forms.
  - Verify: `uv run pytest tests/test_backup.py -q`
  - Result (2026-11-18): `uv run pytest tests/test_backup.py -q` -> 12 passed; `uv run pytest -q` -> 736 passed, 9 skipped;
    web 138 passed, build done. Also touched: `copy_engine.copy_verified(expect_sha256=)` + `SourceMismatch` (the copy must
    equal the catalog hash and leaves no partial), `activity.job_counts` for `replicate`, `backup` in `JOB_COMMANDS`, and the
    Import dialog's Second drive (the dry run checks it; Apply starts `backup --follow`). `--accept-warnings` waits for bk-2. D-104.

- [ ] 4. **[stronger]** Card workflow in the web UI
  - ID: bk-4 (after bk-2, bk-3; card-3)
  - A **Run workflow** button on a connected card's tile and detail view, with a timeline like the drive
    workflow: Check card → Check drives (health of the card and each ticked drive) → Choose drives → Dry run →
    Confirm → Import and back up (two progress bars at once) → Check card again (the tiers update).
  - Choose drives: mounted WORKING and MASTER drives as checkboxes with free space and the planned size; at
    least one WORKING drive is required (D-003). A drive without room, or with a `stop` report, is disabled
    with the reason; LEGACY drives are listed greyed ("read-only: legacy drives are never written to").
  - The dry run shows per target: files to copy, files already there, bytes, time estimate, and the health
    verdict. "Start" stays disabled until each warning is ticked. The import runs as today; the backup job
    starts at once with `--follow`.
  - Verify: `uv run pytest tests/test_web_api.py -q`; the user does the visual check.

- [ ] 5. **[user]** Plug in the 12 TB and set it up as the backup drive
  - ID: bk-5 (replaces rep-1)
  - `uv run photovault --json drives health "/Volumes/<12TB name>"`, then
    `uv run photovault --json volumes assign "/Volumes/<12TB name>" --role MASTER --label <nickname>`.
    Paste back the health report. If it already holds an older archive, say so: that content is indexed
    later under rep-0/gap-1, not by this spec.

- [ ] 6. **[user]** First card backup (acceptance)
  - ID: bk-6 (after ing-11, bk-4, bk-5)
  - Run the card workflow in the browser for one card onto X9 Pro and the 12 TB. Paste back the dry-run plan,
    the health reports, both jobs' timings and the card's tiers afterwards (expected: green).

- [ ] 7. Volume name on hover
  - ID: vn-1 (D-052)
  - `GET /api/drives` adds `volume_name` (the mount's folder name; the Mac folder has none). Where a drive's
    nickname differs, its name shows the volume name on hover ("Volume name: X9 Pro") in the drive list, the
    drive view header and the Cards view.
  - Verify: `uv run pytest tests/test_web_api.py -q`

- [ ] 8. **[stronger]** Rename a volume to its nickname
  - ID: vn-2 (after vn-1; D-052)
  - `volumes.rename(conn, mount, new_name, *, run=subprocess.run)` runs `diskutil rename`. Any role,
    including CARD and LEGACY (rule 1 exception). Refused while a job on the volume is `running`, `planned` or
    `paused` (job params hold the mount path), and while Lightroom is running. Names are checked first: exFAT
    and FAT are 11 characters at most, no `/ \ : * ? " < > |`; APFS 255 bytes. Updates nothing else in the
    catalog (volumes are keyed by UUID and marker, not path).
  - Lightroom detection: count instances with `lr_referenced` on the volume, and read (read-only, lock checked)
    each `.lrcat` the catalog knows of for an `AgLibraryRootFolder.absolutePath` under the old mount. If any
    are found, the confirm names the catalogs and folder counts and says that Lightroom will show them as
    missing until relinked (vn-3).
  - CLI: `volumes rename <mount> --to <name> [--yes]`. Web: a "Rename volume to match" button in the drive's
    Settings dialog, shown when the names differ, with the warning dialog.
  - Verify: `uv run pytest tests/test_volumes.py tests/test_web_api.py -q`

- [ ] 9. **[stronger]** Lightroom relink after a rename (design spike, user approval required)
  - ID: vn-3 (D-052; overlaps idx-10)
  - Write `docs/reviews/<date>-lightroom-relink.md`: (a) Lightroom's own "Update Folder Location" (the user
    does one per root folder; guided steps generated by the engine); (b) the engine rewrites
    `AgLibraryRootFolder.absolutePath` in a copy of the catalog, checks it, then swaps it in after a backup
    copy; this needs a rule-7 change. Test (b) on catalogs copied read-only from attached drives into
    `testdata/lrcat/` (gitignored), never on the originals. Recommend one.
  - Verify: the user approves the review; its implementation becomes vn-5.

- [ ] 10. **[user]** Real rename and relink test
  - ID: vn-4 (after vn-2, vn-3, and only on a drive whose every file is green)
  - Rename a fully backed-up drive that a Lightroom catalog references, confirm Lightroom shows it missing,
    relink with the vn-3 method, confirm the photos open. Paste back what happened.
