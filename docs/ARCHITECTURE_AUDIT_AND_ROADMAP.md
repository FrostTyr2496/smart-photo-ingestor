# Architecture Audit & Roadmap — smart-photo-ingestor

Date: 2026-11-11 · Scope: `photo_ingest/` (9,257 lines, 21 modules), `tests/` (460 tests), `.kiro/specs/`,
and a read-only survey of the mounted drives (details in `docs/private/STORAGE_LAYOUT.md`, which is
local-only because this repo is public). No code was changed for this audit.

> **Status (2026-11-11, after the audit):** the v0 code now lives on branch `archive/v0-cli-2026-11-11`;
> `main` was reset for the new direction. Answers to §8 and later choices are in `docs/decisions.md`
> (D-001…). The authoritative module map, catalog schema and naming spec are now
> `docs/steering/system-design.md`; where this audit and system-design differ, system-design wins.
> Module references below (`analyzer.py`, `deduplication.py`, …) refer to the archived code.

Severity legend: 🔴 can lose or silently corrupt media / gives a false "safe" signal · 🟠 wrong result or
won't scale to the 16 TB goal · 🟡 cleanup, UX, dead weight.

---

## 0. TL;DR

1. **The current code is a decent single-shot CLI importer, not the foundation of a vault.** Its best parts
   are the ExifTool batch metadata reader (`analyzer.py`), the Pydantic config, the scanner, and the
   project knowledge in `docs/handoff.md`. The parts the end goal depends on — catalog, dedupe, copy
   engine, job orchestration — have to be **rewritten, not hardened**: the data model has no notion of
   drives, of one asset living in many places, of sidecars, or of sequences.
2. **Three 🔴 issues exist today:** `--move` from a card deletes sources **without verification**
   (cross-volume `shutil.move`); copies are written straight to the final filename with no temp/rename
   or fsync, and the "verification" re-read is very likely served from the page cache; dedupe records are
   only written at the very end of a run, so a crash/unplug mid-import leaves the catalog unaware of what
   was copied and the re-run writes `_1` duplicates next to possibly truncated files.
3. **Astro frames are not at risk of being dropped as duplicates** (exact SHA-256 never matches noisy
   sub-exposures; `SIMILAR` files are still imported). The real astro problems are: a night session is
   **split across two date folders at midnight**, nothing groups lights/darks/flats/bias/foreground, and
   every JPEG is fully decoded for a perceptual hash that is useless for this content.
4. **Hashing speed is not the bottleneck — I/O is.** Measured on this M1 Max: SHA-256 **2,180 MB/s on one
   core** (ARMv8 SHA extensions via OpenSSL), faster than any of your drives. Switching to BLAKE3/xxHash
   buys nothing; what matters is **reading each byte once** (today a card file is read up to 5×) and
   **never re-hashing what the catalog already knows**.
5. **Sidecars are not handled at all.** `.xmp`, `.SRT`, `.LRF`, `.AAE` are never scanned, so they are left
   behind (and orphaned under `--move`); the `_1` collision rename can split a NEF from its JPG/XMP.
6. **Verdict: Path A (Hybrid), with a rewritten engine core and a hard contract** — a headless Python
   engine driven over JSON-lines on stdio, a SQLite catalog (WAL) as the system of record, and a thin
   SwiftUI menu-bar app for DiskArbitration, progress and notifications. Re-evaluate Path B (pure Swift)
   at a defined gate after the catalog schema stabilises. Reasoning in §3.

---

## 1. Module-by-module audit

Line counts from `wc -l`. "Fate" = what to do with it in the roadmap.

| Module | Lines | Assessment | Fate |
|---|---:|---|---|
| `analyzer.py` | 1018 | **Best code in the repo.** Long-lived ExifTool workers, chunked reads, SQLite cache, explicit tag list, `normalize_exiftool_row`, documented benchmarks and the `-fast2` trap. Weaknesses: tag list lacks `SubSecTimeOriginal`, `OffsetTimeOriginal`, `QuickTime:CreateDate` (so **videos fall back to mtime for their date**), `ImageUniqueID`, Nikon bracketing/shooting-mode tags; the cache is keyed by `path+mtime` (meaningless across cards that share a volume name); the analytics half (`_analyze_metadata`, ~400 lines) is a different concern bolted on. | **Keep** the reader; split it into `metadata/` and extend tags. Move analytics to the `analyze` tool. |
| `config.py` | 436 | Solid Pydantic models, env expansion, validation, config discovery. `dedupe_store` defaults to a relative `.hashes.sqlite`. | **Keep**, add volume roles/policies; catalog path → `~/Library/Application Support/…`. |
| `file_scanner.py` | 403 | Clean `os.walk` with hidden-dir pruning, zero-byte skip, error collection (F11). But it is **extension-filtered to raw/jpeg/video only**, so sidecars are invisible, and it has no notion of DCIM structure or file groups. | **Keep core**, add sidecar-aware grouping. |
| `device_detector.py` | 270 | Works for Make/Model → code. Z6 vs Z6II are distinguishable by Model (`NIKON Z 6` vs `NIKON Z 6_2`); two bodies of the same model would need the serial, which it ignores. | **Keep**, add a `bodies` table keyed by serial. |
| `deduplication.py` | 527 | See §2.2. Full SHA-256 of every file, sequential; the "size tolerance" (±1 byte per MB) is meaningless because SHA must match anyway; a perceptual-hash pass decodes every JPEG and then **loads the whole perceptual table per candidate (O(N²))**; the `path+mtime` shortcut returns `DUPLICATE` without checking that the earlier copy still exists. No tier 2 (serial/shutter count). | **Rewrite.** |
| `database.py` | 505 | `file_records` has `UNIQUE(sha256_hash)` + `INSERT OR REPLACE` → **one location per content, last writer wins**; no volume identity, no instances, no job journal; new connection per call; no WAL / `busy_timeout`; `migrate_database()` is a stub; `get_files_by_perceptual_hash` ignores its threshold. | **Rewrite** as Catalog v2 (§4). |
| `file_operations.py` | 699 | Good: in-run collision reservation under a lock; raw-backup sequencing fixed (F9). Bad: see §2.4 — unverified cross-volume move, no temp file + rename, no fsync, cached re-read verification, collision `_1` suffix applied per file (breaks pairs), date taken from naive EXIF time with **calendar-midnight boundary**. | **Rewrite** the copy engine; keep the planning idea. |
| `ingest_command.py` | 603 | Clear 8-phase orchestration, typed source error, hash cache to avoid re-reads (F9), no-op re-runs (F10). But it is **one in-memory batch**: nothing is persisted until phase 8, a single `--event` name is applied to a multi-day card, and duplicates are skipped from the plan so they never appear in the "is this card fully backed up?" accounting. | **Rewrite** as a resumable job state machine. |
| `dry_run.py` | 303 | Fine as a preview. The plan it previews is recomputed rather than being the exact plan that gets executed. | **Keep the concept**: plan → persist → preview → execute the same plan. |
| `output_generator.py` | 454 | Manifest/Summary/Notes per event, never overwrites (F10). Useful human-readable artifacts. | **Keep**; feed it from the catalog. |
| `llm_client.py` | 419 | Timeouts (text 60 s, vision 120 s) and exponential retries, **no circuit breaker**. With Ollama hung (not refused), one vision call costs up to 4 × 120 s + 7 s backoff ≈ **8 min**, and it is called per sampled image. | **Quarantine** to an optional enrichment queue; add breaker. |
| `visual_analyzer.py` | 813 | Random sampling (`random.sample`, non-deterministic); RAW "preview" uses Pillow, which sees the NEF's **160×120 thumbnail**, so LLaVA is shown a postage stamp (the `-JpgFromRaw` path is a TODO). | **Quarantine**; fix preview; sample one frame per sequence. |
| `location_service.py` | 288 | Sends GPS coordinates to **public Nominatim** with a placeholder User-Agent (`github.com/example`); in-memory cache only. Privacy and ToS concern; not in the ingest path. | **Opt-in only**; prefer offline reverse geocoding later. |
| `analyze_command.py` / `output_manager.py` / `cli.py` | 389 / 691 / 387 | Working `analyze` UX. Known CLI issues tracked in handoff F2/F12 (`analyze` exits 1 on empty folder, `--config` saves default every time, dead flags). | **Keep** `analyze` as a separate read-only tool; add `engine` subcommands. |
| `exif_processor.py` | 512 | **Dead code** — imported by nothing in the package. | **Delete.** |
| `output_formatter.py` | 226 | **Dead code** — imported by nothing in the package. | **Delete.** |
| `exceptions.py` / `settings.py` | 191 / 102 | Fine. | Keep. |

Rough split of the 9,257 lines: **keep ≈ 25 %** (metadata reader, config, scanner, devices, outputs),
**rewrite ≈ 25 %** (dedupe, DB, file ops, ingest orchestration), **quarantine ≈ 28 %** (LLM/visual/
location/analytics), **delete ≈ 8 %** (dead modules), remainder CLI/plumbing.

**Tests.** 460 tests, 17 stale failures (listed in `docs/handoff.md`). They lean heavily on mocks (several
failures are `Mock` objects where code iterates), with almost no real-file fixtures and **no fault-injection
tests** (unplug mid-copy, disk full, bit flip, crash between copy and DB write) — which are exactly the tests
a vault needs. `pytest -m offline` (5 tests) is a good pattern to keep.

**Spec drift.** `.kiro/specs/tasks.md` marks 1–13 done and 14–20 open; in reality 14 is implemented and
several "done" tasks are partial. Requirement "handle volume mounting and unmounting gracefully" (req. 125)
has no implementation. The steering doc's tier-2 dedupe and LLM circuit breaker don't exist.

---

## 2. The four flagged risks

### 2.1 Astrophotography / burst trap

What actually happens today if a card holds 200 tracked light frames + 30 lens-cap darks:

| Stage | Behaviour | Risk |
|---|---|---|
| SHA-256 dedupe | Sub-exposures differ by sensor noise → hashes never match. | None. No false `DUPLICATE`. |
| Perceptual dedupe | Only `.jpg/.jpeg/.png/.tif/.heic` are dHashed; **NEFs are skipped**. Each JPEG is fully decoded (≈24 MP) just to compute an 8×8 dHash. Dark-frame JPEGs collapse to near-all-zero hashes, tracked sky frames to near-identical ones. The comparison only runs when a size-tolerant match exists **in previous runs' records**, and `SIMILAR` files are still imported. | No data loss; wasted CPU; misleading `SIMILAR` labels on a later night's darks; O(N²) scan as the DB grows. |
| Date folder | `archive/YYYY/YYYY-MM-DD_Event/Camera/` from naive `DateTimeOriginal`, calendar day. | 🟠 **An Orion session from 21:00–02:00 lands in two folders.** Observed on your data: the 2026-10-06/07 night runs 23:57 → 01:28 (and is split across two cards); a 2024 legacy night-shoot folder spans 23:11 → 05:14. |
| Event name | One `--event` for the whole card. | 🟠 A card with five shoots gets one name across all date folders. |
| Grouping | Nothing distinguishes lights / darks / flats / bias / foreground / brackets / timelapse. | 🟠 Stacking prep (Siril/PixInsight/Sequator) is fully manual. |
| LLM | **Not in the ingest path** (ingest makes at most one text call for Notes when `llm.enabled`). `analyze --peek` randomly samples frames per device/folder, sends a 160×120 thumbnail for NEFs, and with Ollama hung each call can take ~8 min with no breaker. | 🟡 for ingest, 🟠 for `analyze --peek` on an astro card (hours, garbage descriptions of black frames). |

Design for sequences is in §5.

### 2.2 Dedupe speed & Nikon rollover

**How it works today (`deduplication.py` + `ingest_command._check_duplicates`):**

1. `path + mtime` already in DB → `DUPLICATE` (no check that the earlier copy still exists).
2. Query DB rows with size within ±(size/1 MB) bytes.
3. **Always** compute full SHA-256 (+ dHash for JPEGs), sequentially, one file at a time.
4. Exact SHA match → `DUPLICATE`; else dHash within 5 bits of *any* stored hash → `SIMILAR`; else `NEW`.
5. In-run duplicates detected via a `seen_sha` dict (F9).

**Answers:**

- **Tiered deterministic checks?** No. There is no serial/shutter-count tier and no partial hash; step 2
  doesn't save any work because step 3 always runs.
- **Will SHA-256 bottleneck on 16 TB?** The hash itself won't: 2.18 GB/s/core measured here. The **reads**
  will. Rough budget for a one-time full fixity baseline: 4 TB SSD over 10 Gb/s USB at ~0.9 GB/s ≈ 75 min;
  12 TB spinning disk at ~180 MB/s ≈ **18–19 h**. That is acceptable **exactly once**, as a resumable
  background job, and never again. BLAKE3/xxHash would not change these numbers; SHA-256 has the advantage
  of hardware acceleration in both Python (`hashlib`) and Swift (`CryptoKit`), so hashes stay comparable if
  the engine is ever ported. **Recommendation: SHA-256 for content identity; no second algorithm.**
- **Read amplification today:** in BOTH mode (raw backup + organised) a card file is read for the dedupe
  hash, the raw-backup copy, the raw-backup verify, the organised copy and the organised verify — **5 reads
  of the source**. Target: **1 read of the source** (hash while copying) + 1 uncached read of each destination.
- **Nikon rollover:** confirmed live on your cards — `Card A` has `DSC_0001` in **13 different DCIM folders**,
  449 repeated NEF stems, and mixes `101NZ6_2` and `277–289NCZ_6` folders (two bodies writing to one card).
  Today rollover can't cause a false duplicate (identity is the hash, never the filename), but on the
  destination side same-named files on the same day/device get a `_1`, `_2` suffix **per file**, which can
  pair `DSC_0001_1.NEF` with `DSC_0001.JPG`. Naming must be deterministic and group-wide (§5.3).
- **Proposed tiers** (each tier only *narrows candidates*; nothing is ever declared a duplicate — and no
  copy is skipped — without a full-hash match against a **verified** instance):

  | Tier | Key | Cost | Used for |
  |---|---|---|---|
  | 0 | `(volume_id, rel_path, size, mtime)` already indexed | stat only | Skip re-indexing unchanged files on known drives |
  | 1 | `size` | stat | Candidate filter |
  | 2 | `(camera_serial, shutter_count)` for Nikon; `(model, DateTimeOriginal, SubSecTimeOriginal, OffsetTime)` for iPhone/DJI | metadata (already read) | Capture identity / grouping NEF↔JPG; cross-drive "same shot" detection |
  | 3 | `partial_sha256` = SHA-256 of first 64 KiB ‖ middle 64 KiB ‖ last 64 KiB ‖ size | ~192 KiB read | Cheap cross-drive candidate match on legacy drives |
  | 4 | full SHA-256 | full read | Confirmation; fixity baseline |

  On **card import** tiers 3–4 are free, because the file is read once anyway while copying. Tiers 1–3 matter
  for **indexing legacy drives** (4 TB, Flats 1) and **reconciliation** with the 12 TB, where they let us
  find "probably already have it" without reading terabytes — but the bytes still get a full hash before
  anything is treated as redundant.

  Caveat on tier 2: ShutterCount is per actuation, so a NEF and its JPG share it (that's the point — it's
  the *capture* key, not the *file* key), and modes like multiple exposure or in-camera HDR can produce extra
  files per actuation. Treat `(serial, shutter_count)` as a grouping key, never as proof of identical bytes.

### 2.3 Sidecar atomicity (.NEF + .xmp)

- **Not handled.** `FileScanner` keeps only extensions in `file_types.raw/jpeg/video`, so `.xmp`, `.SRT`,
  `.LRF`, `.AAE`, `.THM` are never seen. Copy mode leaves them behind; `--move` strands them in the source
  folder. Collision renames are per file (`_1`), so a group can be split.
- **Reality check from your drives:** cameras don't write XMP, so **cards have zero `.xmp`** (confirmed on both
  cards) and the X9 Pro shoot has none either. Even Flats 1 has only **3 `.xmp` across 16,266 NEFs** — your
  edits live in the Lightroom catalogs. So XMP atomicity is a correctness rule but a small-volume problem;
  **not breaking Lightroom's file references** is the big one. The sidecar sets that actually occur are:
  NEF+JPG, DJI `MP4+SRT` (+ junk `THM/SCR/AIS`), GoPro `MP4+LRV+THM`, iPhone `MOV(+XMP)`, and DJI
  panorama/hyperlapse DNG folders that must move as a unit.
- **XMP is mutable.** Lightroom rewrites `.xmp` whenever metadata is saved to file. Content-hash dedupe of
  XMP is therefore wrong: it should be tracked as a **versioned sidecar of a capture group**, compared by
  `(group, path)` + latest hash + mtime, and newer-wins (with the older kept) on reconcile.
- **Derivatives:** the X9 Pro shoot keeps `DSC_0850-Edit.tif` and `DSC_0855-HDR.dng` next to the raws — Lightroom
  derivatives share the stem. A group model must allow `role=derivative` members, which are new content
  (never deduped against the raw) but belong with it.

### 2.4 Copy verification & offline vault state

| Question | Today | Problem |
|---|---|---|
| Is the destination checksummed after copy? | Yes for copy mode: `shutil.copy2` then SHA-256 of source and destination. | 🔴 The destination is read back immediately after writing, so it is **almost certainly served from the unified buffer cache**, not from the drive — this verifies RAM, not the SSD. Needs `fcntl(F_NOCACHE)` (or `F_FULLFSYNC` + purge) before the read-back. |
| Is a partial copy distinguishable? | No. `copy2` writes to the final name; no fsync. | 🔴 An unplug leaves a truncated `DSC_0001.NEF` at the canonical path; the re-run then writes `DSC_0001_1.NEF` beside it. Needs write to `.<name>.partial` → `F_FULLFSYNC` → verify → `rename()`. |
| Move mode | `shutil.move` across volumes = copy + **unlink with no verification**. Raw-backup move is also unverified. | 🔴 Source deleted on the strength of an unverified copy. Moving off removable media should simply not exist; "clear the card" is a separate, explicit, catalog-gated step (format in camera). |
| Is progress durable? | Dedupe records are written in phase 8, after all copies. | 🔴 Crash/unplug mid-run → catalog has no record of anything copied. |
| Does the DB know which drives hold a verified copy? | No. `source_path`/`dest_path` are absolute strings including `/Volumes/<name>`; two cards are both named `NIKON Z 6_2`; one row per hash (`INSERT OR REPLACE`). | 🟠 Cannot answer "is this card safe to format?" or "what still needs to go to the 12 TB?". |

---

## 3. Path A vs Path B — verdict

### What has to be built either way

Regardless of language, the following does not exist yet and is the bulk of the work: the catalog
(volumes / assets / instances / groups / sequences / jobs), the verified single-read copy engine, the
resumable job journal, sidecar grouping, sequence classification, legacy-drive indexing, reconciliation
with the 12 TB, and the macOS shell (DiskArbitration, menu bar, notifications). What exists and is worth
keeping is roughly: the ExifTool reader + normalisation (~450 lines), config, scanner, outputs, and the
`analyze` tool. So "keep the Python engine" really means "keep its metadata layer and test harness, rewrite
its core".

### Comparison

| Concern | Path A — Swift shell + Python engine | Path B — pure Swift |
|---|---|---|
| Time to a trustworthy card import | **Shortest**: reuse the ExifTool reader, config, test harness, fixtures; rewrite core in a language the repo, tests and tooling already use. | Longer: everything including metadata normalisation and tests is new. |
| Nikon MakerNotes (serial, ShutterCount) | ExifTool — proven on your NEFs. | `ImageIO` exposes a Nikon maker dictionary (incl. serial/shutter-count keys) but coverage for Z6/Z6II NEF is **unverified**; realistic plan is to bundle ExifTool anyway (Perl script; macOS still ships `/usr/bin/perl` but it is deprecated). |
| Video / DJI / iPhone metadata | ExifTool covers QuickTime, DJI, HEIC/AAE. | AVFoundation + ImageIO cover most; DJI `.SRT` is a trivial parser. |
| Hashing / copy performance | `hashlib` SHA-256 2.18 GB/s/core; `os`/`fcntl` give `F_NOCACHE`, `F_FULLFSYNC`. I/O-bound either way. | CryptoKit SHA-256 equally hardware-accelerated; same I/O bound. **No performance reason to switch.** |
| Volume detection, menu bar, notifications, TCC (removable-volume access) | Swift side handles all of it. The engine runs as a child process of the app, so macOS attributes file access to the app. | Native, one process. |
| Packaging | Needs a Python runtime: for a personal tool, a `uv`/venv-managed engine at a fixed path is fine; bundling (python-build-standalone in the `.app`) is a one-time chore. No notarisation needed for personal use. | Single `.app`. Cleanest. |
| Failure isolation | A crashing engine can't take down the UI; the journal makes jobs resumable. | One process — needs the same journaling discipline. |
| Two languages | Yes: protocol must be versioned and tested. | No. |
| AI-assisted iteration (you + Gemini + Claude) | Python domain logic is quick to iterate and test with real fixtures. | Swift is fine too, but SwiftUI + concurrency + GRDB is more ceremony per experiment. |

### Recommendation: **Path A, with discipline**

1. **Engine = headless Python CLI** (`photo-ingest engine …`) speaking **JSON-lines over stdin/stdout**
   (one request → a stream of `progress`/`item`/`result` events). **Not** a FastAPI server or resident daemon:
   the app spawns a job, the job ends, nothing listens on a port, nothing needs a launchd lifecycle.
2. **The SQLite catalog is the contract.** Schema-versioned with migrations, WAL mode. The engine is the only
   writer; the Swift app opens it **read-only** (GRDB) to render state instantly, even while no job runs.
3. **SwiftUI menu-bar app** owns: DiskArbitration mount/unmount callbacks, volume-role assignment on first
   sight, starting/stopping jobs, progress, "card safe to format" badge, notifications, reconcile prompt
   when the 12 TB appears.
4. **Decision gate for Path B** after Phase 5 (§6): if the catalog schema has been stable for a month and
   Python packaging/startup is the main pain, port the engine to Swift against the **same schema and the same
   golden fixture corpus** (known files → known hashes/groups/sequences). The hash algorithm (SHA-256) and
   schema are chosen so the port is mechanical.

Why not B now: the risky unknowns are **domain correctness** (group/sidecar rules, astro classification,
reconciliation semantics, Lightroom coexistence), not UI or language. Solving those in the stack that
already reads your NEFs correctly, with a test harness, is the faster and safer path. Why not "harden the
existing engine": its central data structure (one row per hash, absolute paths, end-of-run writes) is the
wrong shape for a multi-drive vault, and patching it would cost more than replacing it.

---

## 4. Catalog v2 — multi-drive vault data model

Location: `~/Library/Application Support/PhotoVault/catalog.sqlite` (internal SSD), WAL, `foreign_keys=ON`,
`busy_timeout`. Snapshotted (`VACUUM INTO`) to the working SSD after every job and to the 12 TB on every
reconcile — **the catalog is itself an asset that must be backed up**.

```sql
-- Physical/logical storage the vault knows about.
CREATE TABLE volumes (
  volume_id      TEXT PRIMARY KEY,           -- our UUID
  role           TEXT NOT NULL,              -- CARD | WORKING | MASTER | LEGACY | PHONE_EXPORT
  label          TEXT NOT NULL,              -- '1TB_WORK', '4TB_SSD', '12TB_MASTER', 'card:Z6II#…'
  fs_type        TEXT,                       -- apfs | exfat | hfs
  da_volume_uuid TEXT,                       -- DiskArbitration VolumeUUID (changes when a card is reformatted)
  da_media_uuid  TEXT,
  marker_id      TEXT,                       -- contents of /.photovault-id (never written on CARD volumes)
  capacity_bytes INTEGER,
  last_mount_path TEXT, last_seen_at TEXT,
  UNIQUE(marker_id), UNIQUE(da_volume_uuid)
);

-- One row per unique byte content.
CREATE TABLE assets (
  asset_id       INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL UNIQUE,
  size           INTEGER NOT NULL,
  partial_sha256 TEXT NOT NULL,              -- tier-3 key
  kind           TEXT NOT NULL,              -- raw | jpeg | heic | video | derivative | other
  group_id       INTEGER REFERENCES capture_groups,
  group_role     TEXT,                       -- primary_raw | jpeg | video | proxy(LRF) | telemetry(SRT) | aae | derivative
  first_seen_at  TEXT NOT NULL
);
CREATE INDEX assets_size_partial ON assets(size, partial_sha256);

-- A capture (one shutter actuation / one phone shot) and everything that belongs to it.
CREATE TABLE capture_groups (
  group_id        INTEGER PRIMARY KEY,
  body_id         INTEGER REFERENCES bodies,
  shutter_count   INTEGER,                   -- Nikon
  capture_time    TEXT,                      -- DateTimeOriginal + SubSec, local
  capture_offset  TEXT,                      -- OffsetTimeOriginal when present
  canonical_stem  TEXT NOT NULL,             -- deterministic name stem shared by all members
  original_stem   TEXT,                      -- e.g. DSC_0001
  sequence_id     INTEGER REFERENCES sequences,
  shoot_id        INTEGER REFERENCES shoots,
  UNIQUE(body_id, shutter_count)
);

CREATE TABLE bodies (                        -- cameras/phones/drones by serial
  body_id INTEGER PRIMARY KEY, make TEXT, model TEXT, serial TEXT, nickname TEXT,  -- 'Z6', 'Z6II', 'AIR2S', 'IP14P'
  UNIQUE(model, serial)
);

-- Where bytes physically live. One asset -> many instances.
CREATE TABLE instances (
  instance_id   INTEGER PRIMARY KEY,
  asset_id      INTEGER NOT NULL REFERENCES assets,
  volume_id     TEXT NOT NULL REFERENCES volumes,
  rel_path      TEXT NOT NULL,               -- relative to volume root, NFC-normalised
  size          INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  state         TEXT NOT NULL,               -- indexed | verified | mismatch | missing | superseded
  verified_at   TEXT, verified_sha256 TEXT,  -- uncached full read result
  last_seen_at  TEXT NOT NULL,
  UNIQUE(volume_id, rel_path)
);
CREATE INDEX instances_asset ON instances(asset_id, state);

-- Mutable sidecars (Lightroom XMP): versioned, not content-deduped.
CREATE TABLE sidecars (
  sidecar_id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES capture_groups,
  volume_id TEXT NOT NULL REFERENCES volumes, rel_path TEXT NOT NULL,
  sha256 TEXT NOT NULL, mtime_ns INTEGER NOT NULL, seen_at TEXT NOT NULL,
  UNIQUE(volume_id, rel_path, sha256)
);

CREATE TABLE shoots (                         -- human-level grouping ("Orion over the lighthouse")
  shoot_id INTEGER PRIMARY KEY, name TEXT, shoot_date TEXT,  -- shoot_date uses the day-boundary rule (§5.2)
  start_time TEXT, end_time TEXT, kind TEXT,                 -- general | astro | aerial | phone
  folder_rel TEXT                                            -- e.g. 2026/20261027 - Lighthouse Moonrise
);

CREATE TABLE sequences (
  sequence_id INTEGER PRIMARY KEY, shoot_id INTEGER REFERENCES shoots,
  kind TEXT NOT NULL,                        -- light | dark | flat | bias | dark_flat | foreground | bracket | burst | timelapse | pano | unknown
  kind_source TEXT NOT NULL,                 -- auto | user
  frame_count INTEGER, exposure_s REAL, iso INTEGER, fnumber REAL, focal_mm REAL,
  median_gap_s REAL, start_time TEXT, end_time TEXT, confidence REAL
);

-- Durable, resumable work.
CREATE TABLE jobs (
  job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,  -- card_ingest | index_volume | baseline_hash | replicate | fixity_audit
  source_volume_id TEXT, target_volume_id TEXT, state TEXT NOT NULL, -- planned|running|paused|done|failed
  created_at TEXT, finished_at TEXT, params_json TEXT
);
CREATE TABLE job_items (
  job_id INTEGER REFERENCES jobs, seq INTEGER, src_rel TEXT, dst_rel TEXT,
  asset_id INTEGER, state TEXT NOT NULL,     -- planned|copying|written|verified|committed|skipped_dup|failed
  error TEXT, PRIMARY KEY(job_id, seq)
);

-- Policy: which roles must hold a verified copy.
CREATE TABLE replication_policy (role TEXT PRIMARY KEY, required INTEGER NOT NULL);  -- WORKING=1, MASTER=1

CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version, created_by
```

**Derived answers (views):**

- `card_status(volume)` — for every media file on the card: does its asset have ≥ *N* `verified` instances on
  non-CARD volumes? Duplicates count only if their existing instance is `verified`. The card is
  **green** when every file meets policy (WORKING + MASTER), **amber** when every file has ≥ 1 verified copy
  but not on the master yet, **red** otherwise. Only green says "safe to format"; amber says
  "safe-ish (1 copy)" and is the user's call.
- `pending_replication(MASTER)` — assets lacking a verified MASTER instance. When the 12 TB mounts, the app
  offers to run a `replicate` job for exactly this set, copying from whichever online volume has a verified
  instance.
- `fixity_due` — instances whose `verified_at` is older than *X* months; audited in sampled batches when the
  drive is online.

**Volume identity rules.** Non-card volumes get a small `/.photovault-id` file (UUID) on first assignment —
that survives renames and re-mounts. Cards are never written to; they're identified by DiskArbitration
VolumeUUID for the current session and are treated as ephemeral (an in-camera format creates a new volume).
The volume **name** is never an identity (both of your Z6II cards are named `NIKON Z 6_2`).

---

## 5. Astro / sequence handling design

### 5.1 Principles

- Ingest **never** looks at pixels or calls an LLM to decide anything. Classification is metadata-first,
  runs after the verified copy, and only affects *folders and labels*, never whether a byte is kept.
- Perceptual hashing is **removed from the ingest/dedupe path** entirely. If wanted later, it becomes an
  optional "find near-duplicates" report on JPEG/HEIC exports, using an indexed (BK-tree) search.

### 5.2 Shoot day & sessions

- `shoot_date = (capture_time − day_boundary).date()` with `day_boundary = 12:00` by default: anything from
  noon to noon the next day belongs to the evening's date. A 21:00–03:00 Orion session stays in
  `2026-…-27`. Configurable per shoot.
- Sessions are split by gaps (> 90 min without a frame from the same body) and then named by the user
  (one prompt per detected session, pre-filled from the card's date range and, later, location).
- Timezones: store `OffsetTimeOriginal` when present; videos use `QuickTime:CreateDate` (UTC by spec) and are
  converted with the session's offset. *(Whether the Z6 / Z6II write `OffsetTimeOriginal` is unverified —
  check in Phase 1 fixtures.)*

### 5.3 Deterministic naming (rollover-proof)

`{YYYYMMDD}-{HHMMSS}{cc}_{BODY}_{shutter:07d}.{ext}` — e.g. `20261027-181442-81_Z6_0084022.NEF` and the same
stem for `.JPG`/`.xmp`/derivatives (`…_Z6_0084022-Edit.tif`). Phones/drones without a shutter count use the
original number: `…_IP14P_IMG4821.HEIC`, `…_AIR2S_DJI0001.MP4`. The original filename is stored in the
catalog (`original_stem`). Because the stem comes from the capture group, all members rename together, and
collisions are impossible unless two groups claim the same `(body, shutter_count)` — which the
`UNIQUE` constraint turns into an explicit error, not a silent `_1`.

> ⚠️ Renaming is only safe **before** Lightroom imports the files. Files already referenced by a Lightroom
> catalog (Flats 1 / 4 TB) must not be renamed or moved outside Lightroom — see §6 Phase 3.

### 5.4 Sequence detection (per body, sorted by shutter count / capture time)

**Calibrate expectations to the real data** (see STORAGE_LAYOUT.md): the night runs on the current cards
are short (5–19 frames), irregular (median gaps 11–45 s), mix exposures within a run (30 s ×5 then 10 s ×5),
and are interrupted by single framing shots (0.5 s @ ISO 204,800). No dark/flat/bias frames or folders
exist on any surveyed drive. DJI panoramas and hyperlapses arrive as DNG sequences in their own
subfolders. So: detection must tolerate gaps and single outliers, DJI folder structure is a strong signal,
and **user relabelling is the primary path for calibration frames**, not an afterthought.

1. Start a run when consecutive frames share `(ExposureTime, ISO, FNumber, FocalLength)` within tolerance
   and the gap is ≤ `exposure + max(5 s, 0.5 × exposure)`.
2. Label runs:

| Kind | Heuristic (metadata) | Optional cheap confirmation |
|---|---|---|
| light | exposure ≥ 1 s, ≥ 5 frames, regular cadence, night time | — |
| dark | same exposure/ISO as an adjacent light run, typically at the session end | mean luminance of the ExifTool `PreviewImage` (640×424) ≈ 0 |
| bias | shortest shutter (≤ 1/4000), same ISO as lights, ≥ 10 frames | preview ≈ 0 |
| flat | short exposure, ≥ 10 frames, same focal/aperture as lights, daytime/dawn or panel | preview bright and uniform |
| dark_flat | same exposure as a flat run, dark preview | preview ≈ 0 |
| foreground | long exposure, lower ISO or different aperture, adjacent to a light run | — |
| bracket | 3/5/7/9 frames, same aperture, stepped exposure (Nikon bracketing tags) | — |
| burst | ≥ 3 frames within 1 s (use `SubSecTimeOriginal`) | — |
| timelapse | ≥ 30 frames, constant interval ≥ 2 s, short exposures | — |

   The "confirmation" column reads a 640-px embedded preview (~50 KB); for a whole night it is seconds
   of work, and it is the only pixel access in the pipeline.
3. Low-confidence runs are shown in the app for one-click relabelling (`kind_source = user`); the label is
   then sticky.
4. On-disk layout for an astro shoot (Siril/PixInsight-friendly):

```
2026/20260114 - Orion over the Lighthouse/
  Z6II/
    lights/   foreground/   darks/   flats/   bias/   brackets/
    _shoot.json   manifest.csv   Summary.md
```

   General shoots keep `2026/20261027 - Lighthouse Moonrise/Z6/…` (the `YYYYMMDD - Shoot` habit already
   used on the drives).

### 5.5 LLM (optional enrichment, never in the import path)

Runs only after a job is `done`, from a background queue, one representative per sequence (first light, first
foreground), using `-JpgFromRaw`/`-OtherImage` previews. Circuit breaker: after 2 consecutive
failures/timeouts, disable for the session; per-call timeout 60 s, no retries during batch.

---

## 6. Phased implementation plan

Each phase ends with something usable and a test gate. Phases 0–2 are the critical path to "plug in a card,
get a trustworthy green light".

### Phase 0 — Safety freeze & groundwork (1–2 sessions)
- Disable `--move` when the source is on a removable/external volume (or remove `--move` entirely).
- Delete `exif_processor.py`, `output_formatter.py`; fix or delete the 17 stale tests.
- Build a **golden fixture corpus** (`tests/fixtures/`, git-LFS or local-only): ~20 real files —
  Z6, Z6II and ~~Z8~~ NEF+JPG pairs, a NEF with LR `.xmp`, an LR `-Edit.tif`/`-HDR.dng`, a short night run with
  a framing shot in the middle, a same-second burst, a DJI JPG+MP4+SRT and a 3-frame PANORAMA DNG folder,
  GoPro MP4+LRV+THM, iPhone HEIC+AAE+Live MOV — with expected hashes, groups and sequence labels.
- Gate: `pytest -m offline` + new fixture tests green.

### Phase 1 — Catalog v2 (2–3 sessions)
- New `catalog/` package: schema above, migration runner (`schema_version`), WAL, single-writer
  connection, typed repository functions. Retire `database.py` and the old `.hashes.sqlite` (it's tiny; no
  migration needed).
- Volume registry: role assignment, `/.photovault-id` markers for non-card volumes, DiskArbitration UUIDs
  read via `diskutil info -plist` in Python for now.
- Extend `EXIFTOOL_TAGS`: `SubSecTimeOriginal`, `OffsetTimeOriginal`, `QuickTime:CreateDate`,
  `ImageUniqueID`, Nikon shooting-mode/bracket tags; verify on fixtures.
- Gate: index the fixture corpus twice → identical catalog; volume renamed → same `volume_id`.

### Phase 2 — Verified copy engine + card ingest job (3–4 sessions)
- Single-read pipeline: read source in 8 MiB blocks → SHA-256 + partial hash on the fly → write
  `.name.partial` on target → `F_FULLFSYNC` → uncached re-read (`F_NOCACHE`) → compare → `rename()` →
  `instances.state=verified` → `job_items.state=committed`, **one item per transaction**.
- Groups & sidecars: scanner emits capture groups (stem + DCIM folder + metadata), copies them as a unit,
  deterministic naming (§5.3), shoot-day boundary (§5.2).
- Duplicates resolved against **verified instances only**; they appear in the card report as "already safe".
- `card_status` report + CLI `engine card-status`. JSON-lines protocol v1 (`ingest`, `status`, `cancel`).
- Fault-injection tests: kill mid-copy, unplug simulation (target disappears), disk full, flipped byte on
  read-back, re-run after crash → no duplicates, no truncated canonical files.
- Gate: import `Card A` (read-only source) to the X9 Pro twice: second run is a no-op; card status green
  for WORKING; byte-for-byte spot check.

### Phase 3 — Legacy indexing & Lightroom coexistence (2–3 sessions)
- `index_volume` job for Flats 1 and the 4 TB: **read-only**, stat + metadata + partial hash first, then a
  resumable, throttled full-hash `baseline_hash` job (overnight).
- Read Lightroom `.lrcat` files **read-only** (they are SQLite: `AgLibraryRootFolder`, `AgLibraryFolder`,
  `AgLibraryFile`) to mark which instances Lightroom references — those are never moved/renamed by the
  engine.
- Reports: duplicate clusters with bytes reclaimable, files present only on one drive, orphan XMPs.
  **No deletes in this phase.** Consolidation to the master is done by *copying* into the new structure and
  re-pointing Lightroom via its own "find missing folder" or a fresh import — documented as a manual,
  per-catalog step.

### Phase 4 — Sequences & shoots (2 sessions)
- Sequence detector + preview-luminance confirmation; astro folder layout; relabel API; `_shoot.json`.
- Gate: fixture night session classified as expected; midnight-spanning session lands in one folder.

### Phase 5 — SwiftUI menu-bar app (3–5 sessions)
- DiskArbitration (`DARegisterDiskAppearedCallback` / `…Disappeared`) → classify volume (DCIM present → card;
  marker → known drive; else ask once).
- Spawns `engine` with JSON-lines; progress UI; cancel; notifications; card badge (green/amber/red);
  "Eject" only when no job holds the volume.
- Reads the catalog read-only via GRDB.
- **Decision gate:** revisit Path B here.

### Phase 6 — 12 TB master reconcile & fixity (2–3 sessions)
- On MASTER mount: `pending_replication` → `replicate` job (verified copy engine) → catalog snapshot to
  master → `fixity_audit` of a rotating sample (e.g. 1 % per mount, oldest `verified_at` first).
- Report: assets with fewer than policy copies, instances `missing` or `mismatch`.

### Phase 7 — Enrichment (optional)
- LLM notes/descriptions per sequence, offline reverse geocoding, Lightroom keyword export.

---

## 7. Practical recommendations about the drives (from the survey)

- **Bodies are a Z6 and a Z6II** (each with a distinct serial), plus DJI Air 2S, GoPro HERO10 and iPhone 14 Pro.
  ~~A Z8 (45 MP NEFs) would make the larger-file case real~~ (no Z8 owned yet). The hash budget in §2.2 still holds — it's I/O-bound.
- **Flats 1 already holds ≈ 113 GB of likely duplicate imports** (same name + size in ≥ 2 folders; 2/2 sampled
  pairs byte-identical) and two Lightroom catalogs. Only 3 `.xmp` files exist across 16,266 NEFs: **edits live
  in the `.lrcat` files**, which makes Phase 3's "never move files Lightroom references" rule the critical one.
- **Card A's Z6 content (Jan–Mar 2026) appears already imported** on Flats 1,
  and the NIKON Z 6_2 card holds about a year of shooting. Neither card has been formatted
  in a long time — the "safe to format" report is the first feature that will pay off.
- **X9 Pro (planned working SSD) is exFAT and nearly empty (6.6 GB).** exFAT has no journaling (unplug
  during writes can corrupt the allocation table), stores xattrs as `._` AppleDouble files, and has coarse
  timestamps. If this drive is Mac-only, **reformat it to APFS before it becomes the working SSD** (copy the
  one shoot off first). If it must stay cross-platform, keep exFAT and rely on the catalog's hashes, never mtimes.
- **Flats 1 is 98 % full (25 GB free).** APFS degrades badly near full; avoid writing to it until it is
  indexed and consolidated.
- **Card A is completely full and holds two bodies' folders** (`101NZ6_2` and `277–289NCZ_6`) — exactly the
  scenario where rollover names collide. It is a good first real-world test for Phase 2 (read-only source).
- **The 4 TB SSD was not mounted during this audit** (only Flats 1, X9 Pro, two cards and a Kiro disk image
  were present), so it is not in the storage survey. Re-run the survey when it's connected.

See [`STORAGE_LAYOUT.md`](STORAGE_LAYOUT.md) for the full per-drive survey.

---

## 8. Open questions — answered 2026-11-11

1. **4 TB SSD:** not available now; it gets indexed in Phase 3 when plugged in (D-006).
2. **Filesystem:** working SSD and master stay **exFAT** so they can be reused on Windows (D-002). The engine
   must therefore write Windows-safe, case-insensitively unique names, write no xattrs, and never rely on
   mtime equality or on `F_FULLFSYNC` being honoured (it falls back to `fsync` and records which).
3. **Safe-to-format policy:** for now **one verified copy on the working SSD** is enough (green). After the
   full dedupe, the policy moves to dual copy (working + master) (D-003). It is a config value, not code.
4. **Lightroom XMP:** still open — but with 3 `.xmp` across 16k NEFs it doesn't block anything.
5. **Naming:** renaming is fine; keep the original camera number visibly as an `M`-prefixed token so files
   stay readable in Lightroom, e.g. `M0781_20261027-181442-81_Z6_0084022.NEF`. A re-ordered per-shoot
   sequence for that number is a later feature (D-004, D-005).
