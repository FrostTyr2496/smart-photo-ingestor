# Photo Ingest Tool — Investigation Handoff

Purpose: jumpstart execution sessions. Each open finding below is self-contained — a small
agent can pick one up, read the cited file/lines, make the fix, and verify. Line numbers may
drift as the code changes; always re-read the cited region before editing.
Severity: 🔴 data-loss or broken core, 🟠 correctness, 🟡 UX / polish.

## Project state at a glance (2026-10-28)

- Repo: `github.com/FrostTyr2496/smart-photo-ingestor`, branch `main`.
- Spec `tasks.md` marks tasks 1–13 done, 14–20 open. In reality **task 14 (IngestCommand)
  is implemented** but unchecked, and several "done" tasks are only partial.
- Environment: macOS, Apple Silicon. `exiftool` 13.36 at `/opt/homebrew/bin/exiftool`.
  Venv has `PyExifTool` and `Pillow`, **not** `rawpy` (deliberately; see "Metadata reading").
- Two `photo-ingest` entry points exist on PATH: `.venv/bin/photo-ingest` (editable
  install) and `/opt/homebrew/bin/photo-ingest` (Homebrew Python 3.13). PATH decides which
  runs — pick one owner before automating.
- Shooter profile: Nikon Z 6 / Z 6II (NEF + JPG), iPhone possible later. Real test data was a
  Nikon Z 6 + Tamron 150-500mm card on `/Volumes/X9 Pro` (read-only use; copies in `/tmp`).

## Test status

- Must-pass offline suite: `pytest -m offline` (`tests/test_offline_degradation.py`, 5 tests).
  Proves the pipeline works with Ollama off/unreachable and with exiftool missing. This replaces
  the old, wrongly-pathed references to `tests/test_without_llm.py`. (`scripts/test_without_llm.py`
  and `scripts/test_verbose_ollama.py` do exist but are manual demo scripts, not pytest tests.)
- Full suite (`pytest --no-cov`): **438 passed, 17 failed**. The same 17 fail on clean HEAD
  (checked with a worktree), so none come from recent work. Causes:
  - `test_analyzer.py::test_analyze_metadata_resolution_counting`, `::test_analyze_directory_integration`,
    `test_analyze_command.py::test_to_dict_with_visual_analysis`,
    `test_visual_analyzer.py::test_analyze_directory_no_visual_analysis` / `_with_visual_analysis`:
    tests pass `Mock` objects where code iterates / does `in` (scan results, folder summaries), or scan a
    fake `/test` directory. Test fixtures are stale.
  - `test_cli.py::test_analyze_basic`, `::test_analyze_with_options`, `::test_verbosity_options`:
    `analyze` on an empty temp dir exits 1 (the `ctx.exit()`-inside-`except Exception` shape, see F2 notes).
  - `test_cli.py::test_ingest_with_options`: expects the text `DRY RUN MODE`; output now says `DRY RUN complete`.
  - `test_error_handling.py::test_ingest_command_raises_on_missing_source`,
    `test_ingest_command.py::test_missing_source_raises`: expect `FileNotFoundError`; code raises
    `SourceDirectoryError`, which derives from `ConfigurationError`, not `FileNotFoundError`.
  - `test_device_detector.py::test_build_detection_rules`: expects lowercase key `"z6"`; code keys
    `_normalized_identifiers` by the config's device code (`"Z6"`).
  - `test_deduplication.py::test_find_similar_images`: patches `photo_ingest.deduplication.imagehash`, but
    `_find_similar_images` does a local `import imagehash`, so the real module runs and the fake hash string fails.
  - `test_llm_client.py::test_call_vision_api_success` / `_failure`: `time.time` is mocked with a short
    `side_effect` list; the code now calls it more times (`StopIteration`).
  - `test_llm_client.py::test_init_enabled`: expects `OllamaProvider(config)`; code calls `OllamaProvider(config, False)`
    (extra `verbose` arg).
  - `test_raw_backup.py::test_integrity_failure_recorded_in_errors`: asserts `"Integrity check failed"`; code writes
    `"SHA-256 integrity check failed"`.
- Note: `llm_client.py` has per-call timeouts and retries but no circuit breaker, although
  `.kiro/steering/project-context.md` says it should. Not implemented.

## Resolved (condensed)

| # | Problem | Fix (where) | Context worth keeping |
|---|---|---|---|
| F1 | Same name+date+device files silently overwrote each other in the archive | `file_operations.py` `_resolve_collision` + `reserved_paths` set under the lock; numeric suffix `_1`, `_2` | Covers copy/move and parallel/serial paths. Move + raw-backup sequencing is still F9. |
| F2 | Normal ingest printed success then exited 1 | `plan_operations` demotes to `ORGANIZED_ONLY` when raw backup is disabled; `cli.py ingest` re-raises `click.exceptions.Exit/Abort` before the broad `except` | `analyze` has the same `ctx.exit()`-inside-`except Exception` shape (cli.py ~L176–184) and still exits 1 on a folder with zero photos. Not changed. |
| F3 | `--json` stdout polluted by `print("DEBUG…")` and status text | prints → `logger.debug`; status output gated off with `--json` | `ingest --json` not run end to end. |
| F4 | Config auto-discovery never used | `cli.py ingest` calls `ConfigManager._resolve_config_path(None)` (`./ingest.yaml`, `~/.photo-ingest.yaml`) before the saved default | `analyze` does not use it. |
| F5 | Relative `dedupe_store` depended on cwd | `ConfigManager.load_config` resolves it against the config file's directory | Other relative paths unchanged. |
| F6 | Exposure/lens/GPS never read (Pillow `getexif()` returns IFD0 only; GPS key was a string) | `analyzer.py` merges the Exif sub-IFD (`_collect_exif_tags`) and reads the GPS IFD (0x8825) | Pillow remains only as a fallback. |
| F7 | ExifTool unused; RAW had no EXIF/serial; NEF size read as 120x160 thumbnail | `analyzer.py` metadata pass (below) | See "Metadata reading". `EXIFProcessor` (`exif_processor.py`) is still not used by the pipeline. |
| F13 | Real-file test: NUL-padded Pillow strings; wrong NEF size; no serial | NUL stripped; ExifTool now primary; `ExposureTime` stored as float seconds | `files_with_artist` was counting empty strings; now 0 for the test files. |

## Metadata reading (design + findings)

The full strategy and benchmark notes live as a comment block at the top of `photo_ingest/analyzer.py`;
short pointers are in `visual_analyzer.py`, `exif_processor.py`, `config.py`. Summary:
- `PhotoAnalyzer.extract_metadata_batch(paths, progress_cb)` = SQLite cache (`exif_cache`, key path+mtime,
  rows tagged `_schema: analyzer-v1`, written per chunk by `DatabaseManager.cache_exif_many`) → chunked
  ExifTool (worker threads, each owning one long-lived process, explicit `EXIFTOOL_TAGS`, chunk ≤200,
  workers = `parallel_workers` capped at 8) → per-file Pillow fallback. `normalize_exiftool_row()` maps
  rows to the analyzer schema. `prefetch_metadata()` makes later per-file `_extract_*_exif` calls memory hits.
- Used by `ingest_command._extract_metadata` (no cache in dry-run) and `analyze_command` (prefetch before its loops).
- Why ExifTool, not Pillow/rawpy: Pillow cannot read Nikon MakerNotes (no serial, ShutterCount, clean
  lens name) and opens a NEF as its 120x160 thumbnail. rawpy decodes pixels, not metadata, and is not needed
  for previews (`exiftool -b -JpgFromRaw` gives the full-size 6048x4024 JPEG; `-OtherImage` 1620x1080;
  `-PreviewImage` 640x424).
- Benchmarks (258 real NEF+JPG on an external SSD, selected tags): one process 72–88 files/s; 4 processes
  250–286; 8 processes 288–455; 12 no gain (10 CPUs). Chunk size does not matter. Cache re-run 0.03s.
  Python RSS ~38 MB. **Never use `-fast2`**: faster, but it drops Nikon MakerNotes (ShutterCount missing, verified).
- Keys: PyExifTool's default `-G -n` uses group0 names (`EXIF:SerialNumber`, `MakerNotes:ShutterCount`,
  `Composite:LensID`) and numeric enum values, so enums are mapped via the tables in `analyzer.py`.
- NEF `ImageWidth/Height` from ExifTool is the raw sensor area (6064x4040) vs the JPG output (6048x4024):
  do not compare RAW and JPG sizes for equality.
- Exiftool is looked up on PATH then `/opt/homebrew/bin` (launchd/Hazel jobs have a minimal PATH).
- Not covered: scale beyond 258 files / one SSD; card readers and HDDs (the user plans to try a larger
  drive next); cache lookups are one SELECT per file (unmeasured at 100k); stale `exif_cache` rows pile up
  when mtime changes; GPS and non-Nikon bodies untested; JPEG preview extraction via exiftool is still a
  TODO in `visual_analyzer.py`; **`deduplication.py` does not use `CameraSerialNumber` at all** despite the
  project rule (tier 2 is missing); `analyze --json` for `--detailed` was not run.

---

# Open findings

## ✅ FIXED (2026-10-28) F8 — Dry-run writes to disk and misreports
- **Status/changes:** `ingest_command.py`: `_init_components` uses a throwaway temp-dir DB when
  dry-run and the dedupe DB doesn't exist (analyzer cache already skipped in dry-run);
  added `_make_llm_client`; dry-run now calls `preview(..., include_notes=config.llm.enabled,
  duplicates_found=result.duplicates_found)` with `llm_client` wired. `dry_run.py`: `preview`
  takes `duplicates_found` to override the always-0 count from `operations`.
- **Verified:** dry-run on a fresh config left no `h.sqlite` in the target dir;
  `pytest -m offline` 5 passed; `tests/test_ingest_command.py` + `tests/test_dry_run.py`:
  35 passed, 1 failed (`TestSourceValidation::test_missing_source_raises`, fails identically
  before my change, so pre-existing/stale).
- **Not verified/out of scope:** duplicate count and LLM prompt output against a live Ollama or a
  populated DB were not run; if the DB already exists, dry-run still opens it and
  `CREATE IF NOT EXISTS` runs against it.
- Original finding:
- Where: `photo_ingest/ingest_command.py`
  - `_init_components` (L282) always constructs `DatabaseManager`, and
    `DatabaseManager.__init__` (database.py L70) calls `_init_database` (L80) which
    creates the SQLite file + schema. So a first dry-run **creates the dedupe DB**.
  - Duplicate count: `plan_operations` drops DUPLICATE ops before preview, so dry-run
    reports "Duplicates: 0".
  - LLM prompt: `preview.preview(...)` is called at L220 without `include_notes=True`,
    so the prompt that *would* be sent is never shown (dry_run.py `preview` supports it
    at L93–L134; Req 5.5).
- Fix direction: don't create the DB in dry-run (lazy-init only when needed, or open
  read-only); pass real duplicate info into the preview; call with `include_notes=True`
  and wire the LLM client for the prompt preview.
- Verify: dry-run on a fresh config does not create `hashes.sqlite`, shows correct
  duplicate count, and prints the LLM prompt when LLM is enabled.

## ✅ FIXED (2026-10-28) F9 — Duplicate + move handling is incomplete
- **Status/changes:** `ingest_command.py`: `_check_duplicates` hashes each file once and caches
  `(hashes, mtime)` in `self._dedup_cache`; a NEW file matching an earlier NEW file's SHA-256 in the same
  run becomes DUPLICATE; SIMILAR files get their own hashes via `calculate_hashes` (the matched file's
  hashes were returned before). `run` builds manifest records before execution; `_build_manifest_records`
  and `_store_dedup_records` use the cache, so move mode no longer re-reads removed sources.
  `deduplication.py`: `store_file_record` takes optional `file_mtime`. `file_operations.py`:
  `execute_operations` copies raw backups for `BOTH` ops in move mode, so the organized move keeps its source.
- **Verified:** throwaway fixture in `/tmp` (2 identical files + 1 unique), HOME overridden.
  Copy mode: 1 duplicate detected, manifest has real hashes/sizes, dedupe DB has 2 rows.
  Move mode with raw backup on: 2 files moved, raw copies written, manifest hashes/sizes populated,
  DB has 2 rows, duplicate left in source. `pytest tests/test_ingest_command.py tests/test_deduplication.py
  tests/test_file_operations.py`: 70 passed, 2 failed (both pre-existing: `test_missing_source_raises`,
  `test_find_similar_images`, see stale list). `pytest -m offline`: 5 passed.
  Rerun before commit: `pytest tests/test_ingest_command.py tests/test_deduplication.py tests/test_file_operations.py tests/test_dry_run.py`: 91 passed, 2 failed (same two pre-existing).
- **Not verified/out of scope:** SIMILAR files are still not written to the dedupe DB (only NEW is stored;
  policy unchanged). The size pre-filter still hashes every file, now once instead of 3×. Dedupe tier 2
  (`DateTimeOriginal` + `CameraSerialNumber`) is not implemented. Raw-only + move still moves sources into
  the backup. `result.total_size_bytes` stats sources after execution, so it reads 0 in move mode.
  Dedupe records set `operation_type='organized'` for BOTH ops (derived from `dest_path`).
- Original finding:
- Where: `photo_ingest/ingest_command.py` + `deduplication.py` + `file_operations.py`
  - DB records are written only at the end (`_store_dedup_records` L511; only NEW stored,
    L519), so identical files **within one import** are all copied (no in-run dedupe).
  - SIMILAR files are imported but never recorded (only NEW persisted).
  - Each source is hashed up to 3×: dedupe check, dedupe store, manifest build. The
    size pre-filter (`_get_files_by_size` deduplication.py L303) still hashes every file
    via `check_duplicate` (L73) even when no size match exists.
  - With raw backup ON + `--move`: raw step moves sources into the backup dir first, then
    the organized `shutil.move` (file_operations.py L345) fails for every file.
  - With raw backup OFF + `--move`: `build_manifest_record` recomputes hash/size from the
    source (output_generator.py L390 `if not sha256_hash and source_path.exists()`), but
    the source is already moved → blank sha256 and size 0; no dedupe records written.
  - Dedupe tier 2 (`DateTimeOriginal` + `CameraSerialNumber`) is not implemented; the data is now
    available from the metadata pass.
- Fix direction: track hashes computed during the dedupe check and reuse them; store
  SIMILAR too (or make policy explicit); sequence raw-backup vs. organized so move mode
  doesn't strand the organized step; compute manifest hash before the move; add the tier-2 check.
- Verify: import a batch with internal duplicates in both copy and move modes; check
  manifest hashes/sizes and dedupe DB contents.

## 🟠 F10 — Re-running an import writes stray output + risks overwrite
- Where: `photo_ingest/ingest_command.py` `_resolve_event_dir` (L348) falls back to a
  folder named for *today*; `_build_summary_data` sets `event_date=datetime.now()`
  (L442) rather than capture date.
- Problem: a second run where everything is a duplicate still writes a header-only
  `manifest.csv` and a `Summary.md` into a today-named folder, and a same-day rerun can
  overwrite the first run's output files.
- Fix direction: derive the event folder/date from capture dates; skip output
  generation when there are no new files; never overwrite existing manifest/summary.
- Verify: run the same import twice; second run should be a clean no-op (or clearly
  versioned output).

## 🟠 F11 — Scanner imports non-photo junk from cards
- Where: `photo_ingest/file_scanner.py` `_is_supported_file` (L173) matches on extension
  only; `_discover_files` (L155) walks with `rglob("*")` (L160) with no exclusions.
- Problem: AppleDouble sidecars (`._DSC_0001.JPG`, common on FAT/exFAT cards) and hidden
  dirs (`.Trashes`, `.Spotlight-V100`) get treated as real photos. (The test volume has
  `._DSC_0850-Edit.tif`.)
- Fix direction: skip names starting with `._`, skip dot-directories, optionally skip
  zero-/tiny-byte files. Also address the `errors=[]  # TODO: Collect errors` at
  file_scanner.py L141 while here.
- Verify: scan a tree containing `._*` files and `.Trashes/`; confirm they're excluded.

## 🟡 F12 — Smaller correctness + UX issues
- `Summary.md` uses import date, not capture date (ingest_command.py L442). (Related F10.)
- Manifest `raw_backup_path` is populated from each file's own folder and is written even
  when raw backup is off (file_operations.py `plan_operations` L143).
- `analyze --config X` saves X as the new default because it compares a `Path` to a
  `str` (cli.py L87 `config != settings_manager.get_config_path()` is always True),
  silently clobbering any previously saved default.
- Dead/half-wired CLI flags (cli.py L37–L39): `--summary` is unused; `--basic` only
  changes a log message; `--workers` only used without config. `-v` does not enable
  logging for `ingest` (only `analyze` sets up logging).
- `config set` exits 0 even when the config file is invalid.
- No `--version` option anywhere.
- Dry-run size formatter uses integer division (`n //= 1024`, dry_run.py L193) so sizes
  lose precision; there's a no-op `self._console.print  # satisfy linter` at L236.
- `get_files_by_perceptual_hash` (database.py L324) ignores its threshold and matches
  exactly. `migrate_database` (database.py) is a logging placeholder.
- `usage_guide.md` / `SETUP_GUIDE.md` tell users to run `python test_without_llm.py` /
  `python test_verbose_ollama.py` from the repo root; those scripts live in `scripts/`.

---

# Invocation mechanisms today (and gaps)

Current ways to run it:
- `photo-ingest ...` (editable install in `.venv/bin`).
- `/opt/homebrew/bin/photo-ingest` (separate Homebrew-Python copy — may run different code).
- `python -m photo_ingest ...` / `python -m photo_ingest.cli ...` (docs use the latter).
- Config: `--config`, `./ingest.yaml`, `~/.photo-ingest.yaml`, or the saved default (`ingest` only).

Missing for "easy to invoke" / unattended use (spec task 18, not started):
- No wrapper script, launchd plist, macOS notification, or volume-mount trigger. Confirmed
  absent: no `osascript`, `plistlib`, `launchctl`, `xattr`, Spotlight, or notification code.
- README over-claims macOS notifications + Spotlight preservation that don't exist.
- `example_config.yaml` sets `llm.enabled: true` → copying it as-is sends every ingest to
  Ollama. A safe default config should disable the LLM.
- `photo_ingest/venv/` is a full virtualenv committed inside the package (gitignored, but
  present) — confirm it isn't swept into a build.

Suggested additions, in order:
1. `--version` + a `doctor` command (checks exiftool, config, writable archive/DB,
   Ollama reachability when LLM on).
2. `config create` writes `~/.photo-ingest.yaml` so setup is one step (no `config set`).
3. Pick one install owner (`pipx install --editable` is available here) and remove the
   other after confirming.
4. A card-import shell script (absolute paths) that runs ingest and posts an `osascript`
   notification based on exit code; Hazel calls the script (keeps notifications out of Python).
5. Optional launchd agent watching `/Volumes` for a mounted card with `DCIM/`.

---

# Suggested next steps

1. Larger-drive run of the metadata pass (`analyze --json --exif-only`): check throughput, worker count, cache
   re-run, and whether the SSD benchmark numbers hold on the real drive.
2. F11 — scanner hygiene (small; `._*` files appear on the real volume). File: `file_scanner.py`.
3. F10 (F8 and F9 fixed; dedupe tier 2 with `CameraSerialNumber` still missing) — rerun correctness.
   Files: `ingest_command.py`, `deduplication.py`, `output_generator.py`.
4. Clean up the 17 stale tests above (mostly fixtures/strings that drifted from the code).
5. F12 polish pass; then the invocation additions; sync `tasks.md` and the guides to match the code
   (mark 14 done; downgrade partial "done" tasks; split remaining work into <10-step tasks).

# Verification notes for any session

- Confirm the shell works with `echo ok` before trusting empty output (it was flaky earlier).
- After a change: run the targeted tests plus `pytest -m offline`. Compare any failure against the list above
  (or a clean `git worktree` at HEAD) before assuming you broke it.
- When testing ingest, point configs at `/tmp` and use a throwaway `HOME` so you don't touch
  `~/.photo-ingest/` or real archives. Never write to `/Volumes/*` sources.
- Clean up any temp files/dirs you create.
