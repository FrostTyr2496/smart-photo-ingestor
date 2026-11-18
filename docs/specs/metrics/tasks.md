# Spec: metrics — lifetime I/O metrics per drive (D-054)

Goal: now that the engine reads and writes real drives, keep a lifetime record of what it did: bytes and files
read and written per drive and per day, how long the drive was busy (so MB/s per drive), read errors and verify
failures. It answers "how much has PhotoVault read off Flats 1, how fast is PP 1, how many EIOs has it had".

Rules for every task:
- Metrics live in `<home>/metrics.sqlite`, **not** in the catalog (D-054). The catalog stays the contract
  (rule 6); metrics are telemetry that may be deleted at any time without losing anything that matters.
- Recording never breaks or slows a run: every metrics failure is swallowed, and writes are batched (a flush
  at most every 5 s and at exit, so a crash loses at most 5 s of counts).
- No §3 signature changes: counting happens inside `hashing.sha256_file`, `hashing.partial_sha256`,
  `copy_engine.copy_verified` and `copy_engine._hash_uncached` through a process-wide recorder that is a no-op
  unless the CLI installs one. Tests never see a recorder unless they install one.
- Metrics hold real paths and drive names, so they stay in the user's home and out of git (rule 10).
- No new dependency.

## Shared definitions

**Operations** (`op`): `read_full` (whole-file read: hash, the source side of a copy), `read_partial` (the three
64 KiB windows of a partial hash), `read_verify` (the uncached read-back after a copy), `write` (bytes written by
`copy_verified`, including its sync).

**Drive key** (`volume_key`): the catalog `volume_id` of the registered volume whose `last_mount_path` is the
longest prefix of the file's path; else `path:/Volumes/<name>` for an unregistered mount; else `local`. A path
key is resolved once per directory and the volume list reloads at most every 30 s on a miss, so a card
registered mid-run is picked up.

**Store** (`metrics.sqlite`, `PRAGMA user_version = 1`):
```sql
CREATE TABLE daily (day TEXT NOT NULL, volume_key TEXT NOT NULL, op TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'live' CHECK (source IN ('live','backfill')),
  files INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, busy_s REAL NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, volume_key, op, source));
CREATE TABLE drives (volume_key TEXT PRIMARY KEY, label TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE incidents (incident_id INTEGER PRIMARY KEY, ts TEXT NOT NULL, volume_key TEXT NOT NULL,
  op TEXT NOT NULL, kind TEXT NOT NULL, errno INTEGER, path TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE runs (run_id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, command TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, exit_code INTEGER, files INTEGER NOT NULL DEFAULT 0,
  bytes_read INTEGER NOT NULL DEFAULT 0, bytes_written INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- live_since
```
`day` is the local date. `busy_s` is wall time spent inside the primitive; MB/s = bytes / busy_s over `live`
rows only (1 MB = 10^6 bytes, as D-037). An incident `kind` is the errno name (`EIO`, `ENOENT`, …) or
`verify_mismatch`. A `runs` row is written only for a command that did I/O.

**Backfill** (`metrics backfill`): rebuilds the `backfill` rows from the catalog, for work done before live
counting began (`meta.live_since`, set by the first live flush or by the first backfill): `read_full` = verified
instances that are not the target of a committed `card_ingest` item, by `verified_at`; `write` = committed
`card_ingest` items, by `updated_at`; `read_partial` = instances with a partial hash, by `last_seen_at`;
`errors` = unreadable instances (`mismatch`, no asset), by `last_seen_at`. A file hashed twice counts once, so
backfill is a lower bound. Re-running replaces the backfill rows.

## Tasks

- [x] **met-1 [stronger]** Lifetime metrics store, recording and CLI report (D-054).
  - New `photovault/metrics.py`: `Recorder` (thread-safe, batched, never raises), `install`/`current`, the
    `measure(op, path)` context manager the primitives use, `open_store`, `report(home, days=0)`,
    `backfill(home, catalog_path)`.
  - `hashing.py`, `copy_engine.py`: count bytes, busy time and errors through `metrics.measure`; a verify
    mismatch records a `verify_mismatch` incident. Signatures unchanged.
  - `cli.py`: `main` installs a recorder for every command except `serve` and `metrics`, and closes it with the
    exit code; new `metrics` (lifetime report, `--days N` adds a per-day table) and `metrics backfill`.
  - Tests `tests/test_metrics.py`: recording through the real primitives into `tmp_path`, drive-key resolution,
    flush batching, a failing store never breaks a hash, EIO and verify-mismatch incidents, backfill
    idempotence and the `live_since` cut-off, the CLI report.
  - Verify: `uv run pytest tests/test_metrics.py -q`
  - _Design:_ this spec, "Shared definitions".
  - Result (2026-11-18): `uv run pytest tests/test_metrics.py -q` → 19 passed; full suite 489 passed, 4 skipped.
    A copy is timed per side: the source's reads count `read_full` on the source drive, and writes plus the sync
    count `write` on the target. A run's `files` and a drive's `files_read` count `read_full` + `read_partial`
    calls (file reads, not distinct files). Read MB/s uses `read_full` + `read_verify` live rows. `metrics.py`
    reads the catalog with its own read-only SQL (D-054). Not run on a real drive yet.
- [ ] **met-2** Metrics in the web UI: `GET /api/metrics` (the `report` dict) and a Metrics view (totals, a
  row per drive with MB/s, recent incidents). Blocks on: met-1.
  - Verify: `uv run pytest tests/test_web_api.py -q`
- [ ] **met-3 [user decision]** Off-Mac copy. Proposal: `metrics export` writes a JSON rollup (no paths, no
  incidents' paths) to a folder the user names, e.g. iCloud Drive. Never place the live `metrics.sqlite` in
  iCloud Drive: syncing an open SQLite file corrupts it. Waits for the user.
- [ ] **met-4** Feed drive health (bk-1): a drive's recent `EIO` incidents and its live read MB/s become `warn`
  findings. Blocks on: met-1, bk-1.
