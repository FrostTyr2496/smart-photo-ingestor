# Spec: index (Phase 3) — legacy drives, Lightroom coexistence, dedupe report

Goal: teach the catalog what is already on Flats 1 and the 4 TB (D-006: the 4 TB arrives this phase) without
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

- [ ] 3. Read-only Lightroom catalog reader
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
  - _Design:_ vault-rules 7

- [ ] 4. Duplicate and coverage report
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

- [ ] 5. **[user]** Index and hash Flats 1
  - ID: idx-5
  - Blocks on: idx-4
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

- [ ] 6. **[user]** Plug in, re-survey and index the 4 TB SSD
  - ID: idx-6
  - Blocks on: idx-5
  - The agent re-runs the read-only survey (as in the 2026-11-11 audit) into `docs/private/STORAGE_LAYOUT.md`.
  - The user then runs the idx-5 commands for the 4 TB with `--label 4TB_SSD`.
  - Verify: as idx-5.

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
