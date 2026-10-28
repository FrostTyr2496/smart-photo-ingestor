# Photo Ingest Tool — Investigation Handoff

Purpose: jumpstart execution sessions. Each finding below is self-contained — a small
agent can pick one up, read the cited file/lines, make the fix, and verify. Findings
are ordered by severity. Line numbers are from the investigation and may drift as the
code changes; always re-read the cited region before editing.

## How this was investigated

- Based entirely on reading source: the `photo_ingest/` package, the spec under
  `.kiro/specs/photo-ingest-tool/`, and the installed library source in `.venv`
  (Pillow 11.3.0, Click 8.3.0).
- Shell execution was unavailable during the investigation (every command returned
  empty output with exit 1), so **nothing was run**: the test suite is unverified and
  `git status` was not checked. Treat all runtime claims as "derived from reading code,"
  and confirm by running before relying on them.
- No files were changed during the investigation.

## Project state at a glance

- Repo: `github.com/FrostTyr2496/smart-photo-ingestor`, branch `main`.
- Last commit (reflog): "Implement IngestCommand with operation modes" (2026-07-06).
- Spec `tasks.md` marks tasks 1–13 done, 14–20 open. In reality **task 14 (IngestCommand)
  is implemented** but still unchecked, and several "done" tasks are only partial.
- Environment: macOS, Apple Silicon. `exiftool` binary IS installed at
  `/opt/homebrew/bin/exiftool`. Venv has `PyExifTool` and `Pillow`, but **not** `rawpy`.
- Two `photo-ingest` entry points exist on PATH: `.venv/bin/photo-ingest` (editable
  install) and `/opt/homebrew/bin/photo-ingest` (Homebrew Python 3.13). PATH decides
  which runs — pick one owner before automating.

## Verify the two worst symptoms yourself

```bash
# throwaway config pointed at /tmp
mkdir -p /tmp/pi-check
cat > /tmp/pi-check/ingest.yaml <<'EOF'
archive_root: /tmp/pi-check/archive
dedupe_store: /tmp/pi-check/hashes.sqlite
EOF

# Expect: success summary printed, THEN "Ingest failed" and exit=1  (Finding 2)
photo-ingest --config /tmp/pi-check/ingest.yaml ingest --source <folder-with-photos> --event Check; echo "exit=$?"

# Expect: JSON parse error because DEBUG/status lines pollute stdout  (Finding 3)
photo-ingest analyze --json --exif-only <folder-with-photos> | python3 -m json.tool
```

---

# Findings / Task backlog

Severity: 🔴 data-loss or broken core, 🟠 correctness, 🟡 UX / polish.

## ✅ F1 — Archive files silently overwrite each other — FIXED (2026-10-27)
- Where: `photo_ingest/file_operations.py`
  - `_plan_organized_path` builds `archive_root/YYYY/YYYY-MM-DD_Event/CameraCode/filename`.
  - `_process_one` copied with `shutil.copy2(...)` and moved with `shutil.move(...)`,
    neither checked if the destination existed.
- Problem (was): two files with the same filename + capture date + device code mapped to
  the same destination path. The second copy overwrote the first with no warning. The
  post-copy SHA-256 check passed because it compared against the just-written file, so
  the loss was invisible. In `--move` mode the source was gone too. Common trigger: two
  cards each with `DSC_0001.JPG`, or a camera that rolls its counter.
- Fix applied: added `_resolve_collision(dest_path, reserved)` — when the planned path
  already exists on disk or was already reserved by another in-run operation, it appends
  a numeric suffix (`DSC_0001.JPG` → `DSC_0001_1.JPG`, `_2`, ...). `_execute_organized_operations`
  now holds a `reserved_paths` set guarded by the existing `lock`; `_process_one` reserves
  a collision-free path atomically (check + reserve under the lock, so parallel workers
  can't pick the same name), logs when a rename happens, and uses that path for the
  copy/move and the integrity check. Covers both copy and move modes and both the
  parallel and serial execution paths (both go through `_process_one`).
- Verified: ingested two same-named, different-content files on the same date/device;
  both survived with distinct names and intact bytes in copy mode (`files_copied: 2`)
  and move mode (`files_moved: 2`), no errors.
- Not covered here: the move + raw-backup sequencing bug (F9) is separate and still open.

## ✅ F2 — A normal ingest exits 1 (reports failure after succeeding) — FIXED (2026-10-27)
- Where: `photo_ingest/cli.py` ingest command — exit on error
  (`if result.errors: ctx.exit(1)`), inside the `try/except Exception` that wraps the
  run. `file_operations.py` `create_raw_backup` returns `errors=["Raw backup is disabled"]`
  when raw backup is off (the default).
- Problem chain (was):
  1. Default ingest planned `OperationType.BOTH` (raw + organized), so the raw-backup
     step always ran even when raw backup was disabled.
  2. Disabled raw backup returned an error string, which landed in `result.errors`.
  3. CLI printed the success summary, then called `ctx.exit(1)`.
  4. Click's `Exit` subclasses `RuntimeError` (confirmed in `.venv` click/exceptions.py),
     so the surrounding `except Exception` caught it and printed "Ingest failed."
  - `analyze` has the same `ctx.exit()`-inside-`except Exception` shape (cli.py L176, L181, L184) — NOT changed here; still open if it surfaces.
- Fix applied:
  1. `file_operations.py` `plan_operations` — a default run (not `--raw-only`/`--organized-only`)
     now plans `ORGANIZED_ONLY` instead of `BOTH` when `config.raw_backup.enabled` is False,
     so the disabled raw-backup step never runs and never emits the spurious error.
  2. `cli.py` `ingest` — the broad `except Exception` now re-raises
     `click.exceptions.Exit`/`Abort` first, so a legitimate `ctx.exit(1)` propagates
     instead of being reported as "Ingest failed."
- Verified: `pytest tests/test_file_operations.py -k "plan and not ollama and not llm"`
  → 7 passed, including a new `test_plan_operations_default_demotes_to_organized_when_raw_disabled`
  asserting the demotion. Confirmed `click.exceptions.Exit`/`Abort` exist in the installed Click.
- Not covered here: full end-to-end ingest repro was not run (shell intermittently hangs
  on plain `pytest`/bare Python imports — the flaky-shell caveat below). The `analyze`
  command's identical exit-in-except shape was left untouched. F9 (move + raw-backup
  sequencing) is separate and still open.

## 🔴 F3 — `--json` output is not machine-parseable
- Where:
  - `photo_ingest/analyzer.py` L166 and L176 — unconditional `print("DEBUG: ...")`
    to stdout on every run that scans photos (fires even with `-q`).
  - `photo_ingest/llm_client.py` L367 — `print("DEBUG: ...")` on summary failure.
  - `photo_ingest/visual_analyzer.py` L133, L213 — `print("DEBUG: ...")`.
  - CLI status messages (`📁 Analyzing...`, etc.) also go to stdout unless `-q`.
- Problem: JSON on stdout is interleaved with debug + status text, so piping to a JSON
  parser fails.
- Fix direction: replace the `print("DEBUG: ...")` calls with `logger.debug(...)`; route
  all human/status output to stderr (or suppress when `--json`). Keep stdout pure JSON.
- Verify: `analyze --json --exif-only <dir> | python3 -m json.tool` succeeds.

## 🟠 F4 — Config auto-discovery is implemented but never used
- Where: `photo_ingest/config.py` — `DEFAULT_CONFIG_LOCATIONS = ["./ingest.yaml",
  "~/.photo-ingest.yaml"]` (L290–292) and `_resolve_config_path` (L326) support these.
  But `cli.py` only uses `--config` or the saved default, then errors at
  cli.py L320 ("No configuration file found").
- Problem: running `ingest` in a folder containing `ingest.yaml` still fails. The spec
  (Req 2.1) promises `--config`, `./ingest.yaml`, `~/.photo-ingest.yaml`.
- Fix direction: resolve config in one place with order: `--config` →
  `./ingest.yaml` → `~/.photo-ingest.yaml` → saved default. Reuse
  `ConfigManager._resolve_config_path`.
- Verify: `cd` into a dir with `ingest.yaml`, run `ingest` with no `--config`, confirm
  it loads.

## 🟠 F5 — Dedupe DB path is relative → breaks under automation
- Where: `photo_ingest/config.py` L242 — `dedupe_store` defaults to `.hashes.sqlite`,
  resolved against the current working directory.
- Problem: a launchd/Hazel job without an explicit working directory runs in `/`, where
  the DB can't be written, and dedupe state scatters per-cwd.
- Fix direction: resolve relative `dedupe_store` against the config file's directory (or
  a fixed app-data dir like `~/.photo-ingest/`). Do the same for other relative paths.
- Verify: run ingest from two different cwds with one config; confirm the same DB file
  is used.

## 🟠 F6 — Exposure + GPS EXIF fields are never read
- Where: `photo_ingest/analyzer.py`
  - `_extract_detailed_exif` L243: iterates `exif_dict.items()` at L280 — that is only
    IFD0. Confirmed against Pillow 11.3 `Image.getexif()`: ISO, FNumber, FocalLength,
    LensModel, DateTimeOriginal live in the Exif sub-IFD, reachable only via
    `exif.get_ifd(0x8769)` (ExifTags.IFD.Exif).
  - GPS check at L255 does `if 'GPSInfo' in exif_dict:` using a **string** key; the keys
    are integers, so this never matches → `files_with_gps` is always 0.
  - `_extract_basic_exif` L180 has the same IFD0-only limitation.
- Impact: lens + exposure columns in `manifest.csv` and ISO/aperture/focal ranges in
  `Summary.md` come out empty for real camera files; GPS count always 0. Make/Model/
  DateTime are in IFD0 so folder routing still works.
- Fix direction: merge the Exif sub-IFD (`get_ifd(ExifTags.IFD.Exif)`) and GPS IFD
  (`get_ifd(ExifTags.IFD.GPSInfo)`) into the tag loop; fix the GPS key to the integer/
  enum. Better: use `EXIFProcessor` (see F7).
- Verify: extract EXIF from a known camera JPEG; assert ISO/FNumber/FocalLength/
  LensModel are populated and GPS count is nonzero for geotagged files.

## 🟠 F7 — ExifTool reader exists but is dead code; RAW support is weak
- Where: `photo_ingest/exif_processor.py` defines `EXIFProcessor` (PyExifTool + Pillow +
  filesystem fallback). It is imported only by tests — not by `ingest_command.py` or
  `analyze_command.py` (both use `PhotoAnalyzer`/Pillow). `visual_analyzer.py`
  `extract_raw_preview` (L136) only works when Pillow can open the RAW; TODO at L161
  notes rawpy is needed.
- Impact: CR3 and other RAW formats Pillow can't open get no EXIF and no peek preview,
  even though `exiftool` is installed on this machine.
- Fix direction: make `EXIFProcessor` the primary metadata reader (keep Pillow fallback)
  once its output keys match what analyzer/manifest expect — **review its internals
  first**, not yet audited. For peek, pull embedded JPEG previews via exiftool instead of
  adding rawpy. PyExifTool is already a dependency; no new packages.
- Verify: analyze a folder with a CR3/NEF; confirm camera + exposure fields populate.

## 🟠 F8 — Dry-run writes to disk and misreports
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

## 🟠 F9 — Duplicate + move handling is incomplete
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
- Fix direction: track hashes computed during the dedupe check and reuse them; store
  SIMILAR too (or make policy explicit); sequence raw-backup vs. organized so move mode
  doesn't strand the organized step; compute manifest hash before the move.
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
  dirs (`.Trashes`, `.Spotlight-V100`) get treated as real photos.
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

---

# Invocation mechanisms today (and gaps)

Current ways to run it:
- `photo-ingest ...` (editable install in `.venv/bin`).
- `/opt/homebrew/bin/photo-ingest` (separate Homebrew-Python copy — may run different code).
- `python -m photo_ingest ...` / `python -m photo_ingest.cli ...` (docs use the latter).
- Config: only `--config` or the saved default (see F4).

Missing for "easy to invoke" / unattended use (spec task 18, not started):
- No wrapper script, launchd plist, macOS notification, or volume-mount trigger. Confirmed
  absent: no `osascript`, `plistlib`, `launchctl`, `xattr`, Spotlight, or notification code.
- README over-claims macOS notifications + Spotlight preservation that don't exist.
- `example_config.yaml` sets `llm.enabled: true` → copying it as-is sends every ingest to
  Ollama. A safe default config should disable the LLM.
- `scripts/` test/demo files are invoked from repo root in the guides but live in `scripts/`.
- `photo_ingest/venv/` is a full virtualenv committed inside the package (gitignored, but
  present) — confirm it isn't swept into a build.

Suggested additions once F1–F6 are fixed, in order:
1. `--version` + a `doctor` command (checks exiftool, config, writable archive/DB,
   Ollama reachability when LLM on).
2. `config create` writes `~/.photo-ingest.yaml` so setup is one step (no `config set`).
3. Pick one install owner (`pipx install --editable` is available here) and remove the
   other after confirming.
4. A card-import shell script (absolute paths) that runs ingest and posts an `osascript`
   notification based on exit code; Hazel calls the script (keeps notifications out of Python).
5. Optional launchd agent watching `/Volumes` for a mounted card with `DCIM/`.

---

# Suggested execution order

1. ✅ F2 DONE (2026-10-27); F3, F4, F5 remaining — small, high-impact, unblock normal + scripted use.
   Files: `cli.py`, `ingest_command.py`, `file_operations.py`, `analyzer.py`, `config.py`.
2. ✅ F1 — the data-loss guard. DONE (2026-10-27). File: `file_operations.py`.
3. F6, then F7 — EXIF correctness (do F6 as a quick fix, or jump to F7 if adopting
   `EXIFProcessor` wholesale). Files: `analyzer.py`, `exif_processor.py`.
4. F11 — scanner hygiene. File: `file_scanner.py`.
5. F8, F9, F10 — dedupe/dry-run/rerun correctness. Files: `ingest_command.py`,
   `deduplication.py`, `output_generator.py`.
6. F12 — polish pass.
7. Then the invocation additions, and sync `tasks.md` + the guides to match the code
   (mark 14 done; downgrade the partial "done" tasks; split remaining work into
   <10-step tasks).

# Verification notes for any session

- Shell may be flaky — confirm with `echo ok` before trusting empty output.
- After any change: run the build/lint and the test suite
  (`.venv/bin/python -m pytest`), and re-check with the F2/F3 repro above.
- When testing ingest, point configs at `/tmp` and use a throwaway `HOME` so you don't
  touch `~/.photo-ingest/` or real archives.
- Clean up any temp files/dirs you create.
