# Spec: index (Phase 3) — legacy drives, Lightroom coexistence, dedupe report

Goal: teach the catalog what is already on Flats 1 and the 4 TB, now called PP 1 (D-006, D-041), without
writing a byte to them, find the duplicates, and plan consolidation without breaking Lightroom.

Rules:
- `docs/steering/task-execution.md` applies.
- Commit as `feat(idx-N): …  [idx-N]`.
- LEGACY volumes are read-only and get **no marker** (vault-rules 1).

- [x] 1. Index a volume (stat + metadata + partial hash)
  - ID: idx-1
  - Blocks on: ing-9
  - Create `photovault/index.py` with `index_volume(conn, cfg, mount, *, emit, should_stop=lambda: False) -> int`
    (returns the job_id). It:
    - walks the volume;
    - upserts `instances` with `state=indexed`, `partial_sha256`, `kind`, size and mtime, committing in
      batches of 500;
    - skips files whose instance already matches `(size, mtime ± 2 s)`;
    - marks instances not seen this run as `missing`;
    - records a `jobs` row of kind `index_volume`.
  - Metadata: read it for media kinds and upsert `capture_groups`/`bodies` when serial and shutter count are
    present. Skip the naming fields: `canonical_stem` = the original stem for legacy groups.
  - Add `photovault index <mount>` to `cli.py`.
  - Create `tests/test_index.py` covering: index a tmp tree; re-index is a no-op; a deleted file → `missing`;
    a modified file → re-indexed; `should_stop` → paused, then resume.
  - Verify: `uv run pytest tests/test_index.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_index.py -q` → 4 passed; full suite 262 passed. Also adds
    additive helpers to `photovault/catalog/repo.py` (`volume_instances`, `clear_instance_identity`,
    `mark_missing`, `latest_job`). Resume = run the same command again (the paused job is reused). Group
    membership is recorded as `committed` index job items for idx-2 to link assets (D-035).
  - _Design:_ system-design §5, §7; audit §6 Phase 3

- [x] 2. Baseline full hash (resumable, throttled)
  - ID: idx-2
  - Blocks on: idx-1
  - Add `baseline_hash(conn, cfg, mount, *, emit, max_mbps: float | None = None, should_stop=...) -> int` to
    `photovault/index.py`. It:
    - processes instances on the volume with `asset_id IS NULL` and state `indexed`, ordered by `rel_path`;
    - uses `sha256_file`, then `insert_asset` and links the instance (`state=verified`, `verified_sha256`);
    - throttles by sleeping so the average stays at or below `max_mbps`;
    - commits per file, so it is resumable at any point.
  - Add `photovault hash <mount> [--max-mbps N]` to the CLI.
  - Create `tests/test_baseline_hash.py` covering: hashes and links; identical files on two tmp "volumes" →
    one asset with two instances; resume after a stop; the throttle maths (monkeypatch `time.sleep`).
  - Verify: `uv run pytest tests/test_baseline_hash.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_baseline_hash.py -q` → 7 passed; full suite 269 passed.
    MB is 10^6 bytes. A file changed since `index` is skipped with a warning. Also edits
    `photovault/ingest.py`: a card file is skipped as a duplicate only when its verified copies already meet
    the policy, so a frame known only on a LEGACY drive is still imported (D-036).
  - _Design:_ audit §2.2 (I/O budget)

- [x] 3. Read-only Lightroom catalog reader
  - ID: idx-3
  - Blocks on: idx-1
  - Create `photovault/lrcat.py`:
    - `referenced_paths(lrcat: Path) -> set[str]`:
      - Raise `CatalogInUse` if `<lrcat>.lock` exists.
      - Open `sqlite3.connect(f"file:{lrcat}?mode=ro&immutable=1", uri=True)`.
      - Join `AgLibraryFile(folder, baseName, extension, sidecarExtensions)` → `AgLibraryFolder(id_local, rootFolder, pathFromRoot)`
        → `AgLibraryRootFolder(id_local, absolutePath)`, and return the absolute paths. Each sidecar extension
        in `sidecarExtensions` (comma-separated) adds `<base>.<ext>`.
    - `mark_lr_referenced(conn, volume_id, mount, paths) -> int` sets `instances.lr_referenced=1`.
  - **Schema check:** the table and column names above come from knowledge of Lightroom Classic and are
    unverified against your catalogs. idx-5 verifies them on a *copy* of a real catalog. If they differ, stop
    and report.
  - Add `photovault lrcat mark <lrcat> --volume <mount>` to the CLI.
  - Create `tests/test_lrcat.py`: build a minimal synthetic lrcat with those three tables in `tmp_path`;
    assert the paths, the sidecars and the lock refusal.
  - Verify: `uv run pytest tests/test_lrcat.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_lrcat.py -q` → 8 passed; full suite 277 passed, 2 skipped.
    Refuses on a lock file or a running Lightroom process (`pgrep -x`), both as `CatalogInUse`; a wrong schema
    raises `LrcatSchemaError`. Marking is additive (D-038). The schema is still unverified against a real
    catalog (idx-5). Also edits `photovault/cli.py` (the `lrcat mark` command and its error mapping).
  - _Design:_ vault-rules 7

- [x] 4. Duplicate and coverage report
  - ID: idx-4
  - Blocks on: idx-2
  - Create `photovault/report.py` with `duplicates(conn, *, volume_id=None) -> list[Cluster]` and
    `write_markdown(clusters, out: Path) -> None`.
    - A cluster is one asset with ≥ 2 instances. It lists each instance's volume label, `rel_path`, size,
      `lr_referenced`, plus the bytes reclaimable (size × (instances − 1)).
    - The report also includes "only on one volume" counts per volume.
  - It is read-only. The output path must be outside `/Volumes`. Default: `<home>/reports/`.
  - Add `photovault report duplicates [--volume M] [--out F]` to the CLI.
  - Create `tests/test_report.py`.
  - Verify: `uv run pytest tests/test_report.py -q`
  - Result (2026-11-11): `uv run pytest tests/test_report.py -q` → 9 passed; full suite 286 passed, 2 skipped.
    Adds `coverage(conn)` (per-volume files, not-hashed, mismatch, only-here), `write_markdown(..., summaries=)`
    (optional kwarg, so the task signature still works), `default_report_path` and `check_output_path`. Rules in
    D-040. The CLI warns `unhashed_files` when indexed files have no hash yet. Also edits `photovault/cli.py`.

- [ ] 5. **[user]** Index and hash Flats 1
  - ID: idx-5
  - Blocks on: idx-4
  - Guide for the user: `docs/user/INDEX_WORKFLOW.md` (added with this task). Flats 1 runs as it is, with no
    cleanup or dedupe first (D-041).
  - The user:
    1. Closes Lightroom.
    2. Runs `photovault volumes assign "/Volumes/Flats 1" --role LEGACY --label FLATS1`.
    3. Runs `photovault index "/Volumes/Flats 1"`.
    4. Runs `photovault hash "/Volumes/Flats 1"` (overnight).
    5. Copies one `.lrcat` to `/tmp` and runs `photovault lrcat mark <copy> --volume "/Volumes/Flats 1"`.
    6. Runs `photovault report duplicates`.
  - The agent compares the duplicate total with the survey estimate (~113 GB) and puts findings in
    `docs/private/`.
  - Verify: the report file exists and `photovault jobs` shows the jobs `done`.

- [ ] 6. **[user]** Re-survey and index the 4 TB SSD ("PP 1")
  - ID: idx-6
  - Blocks on: idx-5
  - The 4 TB is plugged in and is called PP 1 (D-041). It was mounted as `Backup` on 2026-11-11 until the user
    renames it.
  - The agent re-runs the read-only survey (as in the 2026-11-11 audit) into `docs/private/STORAGE_LAYOUT.md`.
  - The user then runs the idx-5 commands for it with `--label "PP 1"`. The user may run its `index` and `hash`
    in a second terminal while idx-5 runs (the catalog is safe for that; see the guide).
  - Before you rename the drive (D-042): a Lightroom catalog stores absolute paths with the mount name it had
    when the folders were added, so renaming the volume breaks the catalog's links to everything on it, and
    `lrcat mark` finds no paths under the new name. Run `lrcat mark` for PP 1 while its mount name still matches
    the catalog, and leave the rename until idx-7 decides how Lightroom is handled. The catalog label "PP 1"
    does not depend on the mount name.

- [ ] 7. **[stronger]** Consolidation plan (design only, user approval required)
  - ID: idx-7
  - Blocks on: idx-6
  - Write `docs/reviews/<date>-consolidation.md`:
    - how legacy content reaches the WORKING/MASTER structure without breaking either Lightroom catalog;
    - options: re-import into one new catalog; copy plus Lightroom "Find missing folder"; or keep the legacy
      layout under `Legacy/` on MASTER;
    - what happens to duplicates (nothing is deleted until the master holds verified copies and the user
      approves);
    - which new tasks this creates.
  - Real paths stay in `docs/private/`.
  - Verify: the user approves; a decision row is recorded.

- [ ] 8. **[stronger]** `--force-exit`: quit Lightroom on the user's command before reading a catalog
  - ID: idx-8
  - Blocks on: idx-3
  - Why: idx-3 refuses while Lightroom is running (D-038). The user asked for a flag that closes Lightroom for
    them instead of making them quit it by hand (D-039). This kills nothing and deletes nothing: it asks
    Lightroom to quit, the way the user would.
  - In `photovault/lrcat.py` add `quit_lightroom(*, run=subprocess.run, sleep=time.sleep, timeout_s: float = 60.0) -> bool`:
    - Return `False` at once if `lightroom_running()` is false; run no command.
    - Otherwise run `osascript -e 'tell application "Adobe Lightroom Classic" to quit'`. This is a graceful
      quit: Lightroom saves the catalog and may show its own dialog (for example the backup prompt).
    - Poll `lightroom_running()` every 1 s until it is false or `timeout_s` passes. Return `True` if it quit.
    - On timeout raise `CatalogInUse("Lightroom did not quit in <n> s; it may be showing a dialog. Quit it yourself and retry")`.
    - If `osascript` fails (macOS Automation permission denied, or the app name differs), raise `CatalogInUse`
      with its stderr and the hint to quit Lightroom by hand.
    - Never send a signal (no `kill`, `pkill`, `SIGTERM`, `SIGKILL`) and never delete or edit the lock file
      (vault-rules 4, 7).
  - In `photovault/cli.py` add `--force-exit` to `lrcat mark` (off by default). When set, call
    `quit_lightroom()` before `referenced_paths`, and emit a `warning` event (`code="lightroom_quit"`) saying
    Lightroom is being asked to quit. The flag is the user's command, so there is no extra prompt (the menu-bar
    app uses `--json`). It does not bypass the lock check: a lock file still present after a clean quit is stale,
    and `CatalogInUse` still applies (D-038).
  - Create tests in `tests/test_lrcat.py`, all with `run` and `sleep` faked (the tests never start or quit a real
    process): not running → no command run; running, then exits after two polls → returns `True`, and the
    command was the `osascript` quit; never exits → `CatalogInUse` and no command other than `osascript`
    and `pgrep` was run; `osascript` returns non-zero → `CatalogInUse` carrying stderr; CLI with
    `--force-exit` quits then marks; CLI without the flag refuses and runs no `osascript`; a lock file left after the
    quit still refuses.
  - Verify: `uv run pytest tests/test_lrcat.py -q`
  - Unverified until a real run: the Lightroom application name for `osascript`, and whether macOS asks the
    terminal for Automation permission on first use. The user checks this on the first real use (idx-5 step 1
    could use the flag); report back before relying on it.
  - _Design:_ vault-rules 7; D-038

- [ ] 10. **[stronger]** `lrcat mark`: handle catalogs written under an older mount name
  - ID: idx-10
  - Blocks on: idx-3, **user approval** (it adds a CLI flag and fields to the `lrcat mark` result and warning
    events, and CLAUDE.md puts protocol and CLI changes behind the user)
  - Why (review of 2026-11-11, a gap rather than a bug): `keys_on_volume` keeps only catalog paths that start
    with the volume's current mount, which is correct (D-038). A catalog written while a drive had another name
    (PP 1 was `Backup`, D-041; Flats 1 may have been renamed too) then marks little or nothing. The only
    warning fires when zero paths match, so a partial miss is silent, and idx-7 would read the unmarked files as
    not referenced by Lightroom (vault-rules 7). Rejected: matching catalog paths to a volume by relative path,
    because Flats 1 and PP 1 share event folder names and it would flag the wrong drive.
  - In `photovault/lrcat.py` add two pure helpers (no change to the §3 signatures of `referenced_paths` and
    `mark_lr_referenced`):
    - `remap_paths(paths, old_mounts, new_mount) -> set[str]` rewrites a leading `old_mount` to `new_mount` on a
      path-segment boundary (`/Volumes/Backup` does not match `/Volumes/Backup2`), comparing with the
      `rel_path_key` rules (case-insensitive, NFC);
    - `off_volume_roots(mount, paths) -> Counter[str]` counts the paths that are not under `mount`, by their first
      two path segments (for example `/Volumes/<name>`).
  - In `photovault/cli.py` add a repeatable `--was-mounted-as <old mount path>` to `lrcat mark`. The remap runs
    before `keys_on_volume`. The `result` event gains `off_volume` (the top five roots and counts), and a new
    `warning` (`code="paths_elsewhere"`) names them and suggests the flag whenever any paths are off the volume.
    The existing `no_paths_on_volume` warning stays. Marking stays additive, and nothing is written to the
    `.lrcat`.
  - Tests, in `tests/test_lrcat.py`, on a synthetic catalog whose root is `/Volumes/Backup/...` and a volume
    mounted at a `tmp_path`: without the flag `on_volume` is 0 and `paths_elsewhere` names `/Volumes/Backup`;
    with the flag the expected files are marked; the `Backup2` boundary; case and NFC variants; a catalog that
    mixes this drive and another leaves the other drive's paths unmapped and unmarked.
  - Verify: `uv run pytest tests/test_lrcat.py -q`
  - _Design:_ vault-rules 7; D-038, D-041, D-042

- [x] 11. **[stronger]** `hash` survives a failing drive
  - ID: idx-11
  - Blocks on: idx-2, **user approval** (CLI flags, warning codes and result fields; given 2026-11-12, D-044)
  - Why: the PP 1 hash (a spinning HDD) hit `EIO` on files in one folder. `baseline_hash` only warned and left the
    row `indexed`, so every resume read the bad sectors again, and the terminal was silent between 100-file
    progress lines.
  - In `photovault/index.py` `baseline_hash(..., skip_prefixes=(), retry_unreadable=False)`:
    - `errno.EIO` marks the instance `mismatch` (asset still NULL) and warns `read_error`; later runs skip it
      unless `retry_unreadable`.
    - Any `OSError` first checks that the mount still exists. If it is gone, the job pauses (`drive_gone`) and
      nothing is marked. Other errnos are `skipped`, as before.
    - `DIR_ERROR_LIMIT = 3` read errors in one folder defer the rest of it to the next run (`bad_sector_dir`).
      `RUN_ERROR_LIMIT = 10` consecutive read errors pause the job (`too_many_read_errors`).
    - `skip_prefixes` match on path segments with the `rel_path_key` rules.
    - A heartbeat every `HEARTBEAT_S = 5` s, between files, names the file being read.
    - The result gains `unreadable`, `deferred` and `excluded`.
  - CLI: `hash --skip-prefix FOLDER` (repeatable; an absolute path must lie under the mount, else exit 2) and
    `hash --retry-unreadable`. `report duplicates` passes `report.unreadable(conn)` to `write_markdown`, and its
    result gains `unreadable` and `unreadable_no_copy`.
  - `photovault/report.py`: `unreadable(conn)` lists each unreadable file with a rescue hint, checked in order:
    a verified copy on another volume with the same size and partial hash ("likely copy", a candidate only,
    vault-rules 5), else the same capture group and role ("same frame"), else "no known copy".
  - Tests: `tests/test_baseline_hash.py` (EIO, retry, other errno, folder breaker, run breaker, drive gone,
    heartbeat), `tests/test_cli.py` (`--skip-prefix`), `tests/test_report.py` (rescue hints, Markdown section).
  - Verify: `uv run pytest tests/test_baseline_hash.py tests/test_report.py tests/test_cli.py -q`
  - Result (2026-11-12): Verify → 37 passed; full suite 328 passed, 4 skipped. The heartbeat cannot fire during
    one blocked read (no threads), and Ctrl-C waits for the current file. Also edits
    `docs/user/INDEX_WORKFLOW.md` (an "If a drive shows I/O errors" section). The real resume on PP 1 is the
    user's.
  - _Design:_ system-design §5 (the `mismatch` note); vault-rules 1, 2, 5; D-044

- [ ] 12. **[stronger]** `hash`: bound one stuck read and stop at once on Ctrl-C
  - ID: idx-12
  - Blocks on: idx-11, **user approval** (a new timeout option and a new warning code)
  - Why (limits left by idx-11): `sha256_file` is a blocking read, so (1) the 5 s heartbeat cannot fire while one
    read is stuck on a bad sector, and (2) Ctrl-C (`should_stop`) is only checked between files, so a pause waits for
    that read to return. On a failing HDD one read can take minutes, and a kernel read in uninterruptible sleep
    cannot be cancelled from Python.
  - Design to settle first (propose, then get approval): read each file in a worker thread or a child process,
    and give up waiting after `--read-timeout SECONDS` (default off or generous). On a timeout, mark the file
    `mismatch` like an `EIO`, emit `read_timeout`, and count it toward the folder and run breakers. A thread stuck in
    the kernel cannot be killed, so after a timeout the run should pause and say the drive needs attention, not
    start another read on it. Ctrl-C then pauses within about a second of the signal even while a read is stuck.
    A heartbeat thread, or the wait loop itself, prints the file being read every 5 s.
  - Tests: a fake `sha256_file` that blocks on an event: the timeout marks the file and pauses; `should_stop`
    set during the block returns within the poll interval; the normal path is unchanged.
  - Verify: `uv run pytest tests/test_baseline_hash.py -q`
  - _Design:_ D-044, idx-11
