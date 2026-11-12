# PhotoVault hard rules

Imported by `CLAUDE.md`. These hold for every task. Changing one is a proposal to the user, never an edit.

1. **Sources are read-only.** Nothing (agent, test, or engine) writes, renames, moves or deletes anything on a
   source volume. CARD volumes are read-only to everyone, including the engine. Marker files are written only
   to WORKING and MASTER volumes, never to CARD or LEGACY volumes.
2. **Agents never write to `/Volumes`.** The PreToolUse hook `.claude/hooks/guard_volumes.py` enforces it.
   Real-drive runs (ingest, `volumes assign`, indexing, hashing) are **[user]** steps: print the exact
   command for the user. Tests use `tmp_path`, never `/Volumes`.
   **Exception (D-028, user-approved):** tests marked `@pytest.mark.live` may READ the mounts listed in the live
   registry, only when run with `-m live`, and only through `photovault/live_harness.py` (`"rb"` opens). They
   may scan, read metadata and hash a few files for throughput. They never write to a mount, never open a
   Lightroom catalog, and never put real-drive rows in the user's catalog. A live test may run an ingest
   `--dry-run` into a `tmp_path` target with a `tmp_path` catalog (D-031); it never runs a real ingest or writes
   a marker on a mount. Reports go to `docs/private/`.
3. **One writer for media bytes:** `photovault/copy_engine.py`. It writes `.<name>.pvpartial`, syncs, re-reads
   uncached, compares SHA-256, then renames. It never overwrites an existing file.
4. **No deletes in the engine** (Phases 1–4). Clearing a card means the user formats it in camera after
   `card-status` is green.
5. **Identity is the SHA-256 of the bytes.** Filenames, paths, mtimes, sizes, partial hashes and
   `(serial, shutter count)` only narrow candidates or group files. A file is skipped as a duplicate only
   when its full hash matches an asset that has a `verified` instance on a non-CARD volume.
6. **The catalog is the contract.** Schema changes only through a new migration in
   `photovault/catalog/migrations.py`, together with `system-design.md` §5, after user approval.
7. **Lightroom coexistence.** Never move or rename a file that a Lightroom catalog references. Open `.lrcat`
   files read-only, and never while Lightroom has the catalog open (a `.lrcat.lock` exists).
8. **exFAT targets (D-002).** Names are Windows-safe (`naming.windows_safe`) and unique case-insensitively.
   No xattrs are written, so no AppleDouble `._` files appear. Never trust mtime equality (2 s tolerance), and
   never assume `F_FULLFSYNC` works (fall back to `fsync` and record which ran).
9. **No pixels, perceptual hashes or LLM calls in ingest or dedupe.** The only pixel access is the
   embedded-preview luminance in Phase 4 (seq-3). LLM enrichment is a Phase 7 backlog item.
10. **Privacy: this repo is PUBLIC.** Keep camera serials, volume UUIDs, GPS, real trip or location names and
    real photos out of git. Real-drive facts go in `docs/private/` and sample photos in `testdata/` (both
    gitignored). Committed fixtures are redacted (see `tests/fixtures/meta/cards.jsonl.gz`).
11. **Dependencies:** stdlib, `PyExifTool` (fnd-5), `Pillow` (seq-3), and `pytest` (dev). The system
    `exiftool` binary is installed. Anything else needs the user's approval first. Python ≥ 3.12, managed by
    `uv`.
