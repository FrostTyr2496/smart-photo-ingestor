# PhotoVault system design

The authoritative map for every task. It covers modules, signatures, the catalog schema, naming, file kinds and
the protocol. The reasoning behind it is in `docs/ARCHITECTURE_AUDIT_AND_ROADMAP.md`, and the decisions are in
`docs/decisions.md`. Where this file and the audit differ, this file wins. Change a signature or the schema only
as task-execution §6 describes.

## 1. Shape

- **Engine:** the Python package `photovault/`, run as a CLI (`photovault …`). The macOS app (Phase 5) launches
  it per job and reads its output as JSON-lines (§10). No server, no daemon.
- **Catalog:** SQLite at `<home>/catalog.sqlite`. The engine is the only writer; the app reads it read-only.
  - `home` = `$PHOTOVAULT_HOME`, or else `~/Library/Application Support/PhotoVault`.
  - Tests always set `PHOTOVAULT_HOME` to a `tmp_path`.
- **Volumes:** each volume has one role:
  - `CARD`: camera media. Read-only, ephemeral.
  - `WORKING`: the 1 TB exFAT SSD.
  - `MASTER`: the 12 TB exFAT drive.
  - `LEGACY`: an existing drive (Flats 1, PP 1) indexed in place, read-only, no marker.

## 2. Module map

| Module | Owns | Task |
|---|---|---|
| `photovault/config.py` | `Config` dataclasses, `load_config`, `default_home` | fnd-1 |
| `photovault/catalog/db.py`, `migrations.py` | `connect`, `migrate`, `snapshot`; `MIGRATIONS` | fnd-2 |
| `photovault/catalog/repo.py` | all SQL reads and writes (no SQL elsewhere) | fnd-3, extended by later tasks |
| `photovault/volumes.py` | probing (`diskutil`), card detection, marker files, registration | fnd-4 |
| `photovault/metadata.py` | ExifTool batch reader and normalisation (ported from the archive) | fnd-5 |
| `photovault/protocol.py`, `cli.py` | JSON-lines `Emitter`; argparse CLI | fnd-6, ing-9 |
| `photovault/hashing.py` | `sha256_file`, `partial_sha256` | ing-1 |
| `photovault/copy_engine.py` | `copy_verified` (the only media writer) | ing-2 |
| `photovault/scan.py` | `walk`, `walk_all`, `classify`, `FileEntry` | ing-3, idx-13 |
| `photovault/usage.py` | `category`, `non_camera_videos`, `usage_rows`, `summarize` (drive usage) | idx-13 |
| `photovault/groups.py` | `build_groups`, `GroupPlan`, `Member` | ing-4 |
| `photovault/naming.py` | `shoot_date`, `canonical_stem`, `dest_dir`, `windows_safe`, `check_unique` | ing-5 |
| `photovault/ingest.py` | `plan_card_ingest`, `run_job`, `JobSummary` | ing-6, ing-7 |
| `photovault/status.py` | `card_status`, `CardStatus` | ing-8 |
| `photovault/index.py` | `index_volume`, `baseline_hash` (+ `skip_prefixes`, `retry_unreadable`, idx-11) | idx-1, idx-2, idx-11 |
| `photovault/lrcat.py` | `referenced_paths`, `mark_lr_referenced` (read-only Lightroom catalog reader) | idx-3 |
| `photovault/report.py` | `duplicates`, `coverage`, `unreadable`, `write_markdown` (duplicate and coverage report) | idx-4, idx-11 |
| `photovault/metrics.py` | `Recorder`, `install`, `measure`, `report`, `backfill`: lifetime I/O per drive in `<home>/metrics.sqlite`, not the catalog (D-054). Reads the catalog read-only with its own SQL (drive keys, backfill); it never writes it | met-1 |
| `photovault/activity.py` | `ActivityEmitter` (an `Emitter` subclass writing `<home>/activity/<pid>.json`), `snapshot` (runs not started by the UI, plus `recent`), the run history `<home>/activity/history.jsonl` (`append_history`, `read_history`) | ui-11, rv-30 |
| `photovault/sequences.py` | `detect_runs`, `classify_run`, `detect_bursts` | seq-1, seq-2 |
| `photovault/sequence_eval.py` | `segment_flat`, `segment_exposure`, `segment_burst`, `segment_search`, `score` (evaluation harness, not used by the engine) | eval-1 |
| `photovault/preview.py` | `preview_luma` | seq-3 |
| `photovault/live_harness.py` | `load_registry`, `iter_entries`, `iter_all`, `open_rb` (read-only live sources, test-only) | live-1 |

## 3. Seams (exact signatures; later tasks depend on them)

```python
# config.py
def default_home() -> Path
def load_config(path: Path | None = None) -> Config        # None → <home>/config.toml if it exists, else defaults
class ConfigError(ValueError)

# catalog/db.py
def connect(path: Path) -> sqlite3.Connection               # WAL, foreign_keys=ON, busy_timeout=5000, synchronous=FULL, Row factory
def migrate(conn: sqlite3.Connection) -> int                # returns PRAGMA user_version after applying MIGRATIONS
def snapshot(conn: sqlite3.Connection, dest: Path) -> None  # VACUUM INTO dest (dest must not exist)

# volumes.py
@dataclass(frozen=True) class VolumeProbe: mount: Path; name: str; fs_type: str; da_volume_uuid: str | None;
    capacity_bytes: int | None; internal: bool; marker_id: str | None; looks_like_card: bool
def probe(mount: Path, *, run=subprocess.run) -> VolumeProbe
def looks_like_card(mount: Path) -> bool                    # a DCIM directory at the volume root
def read_marker(mount: Path) -> str | None                  # /.photovault-id → volume_id
def write_marker(mount: Path, volume_id: str) -> None       # raises CardWriteRefused on a card
def register_volume(conn, probe: VolumeProbe, *, role: str | None = None, label: str | None = None) -> str

# metadata.py
@dataclass class MetadataResult: meta: dict[Path, dict]; errors: dict[Path, str]
def read_metadata(paths: list[Path], *, workers: int = 4, progress=None) -> MetadataResult
def normalize_exiftool_row(raw: dict) -> dict

# hashing.py
def sha256_file(path: Path) -> str
def partial_sha256(path: Path) -> str

# copy_engine.py
@dataclass(frozen=True) class CopyResult: dst: Path; size: int; sha256: str; partial_sha256: str; sync_method: str
class CopyError(Exception)
def copy_verified(src: Path, dst: Path, *, mtime_ns: int | None = None) -> CopyResult

# scan.py
@dataclass(frozen=True) class FileEntry: rel_path: str; abs_path: Path; size: int; mtime_ns: int; kind: str
def classify(name: str) -> str                              # §8 kinds
def walk(root: Path, *, include_ignored: bool = False) -> Iterator[FileEntry]
def walk_all(root: Path) -> tuple[list[FileEntry], list[Path]]  # ignored kinds kept + the *.lrdata folders skipped (idx-13)

# groups.py
@dataclass class Member: entry: FileEntry; role: str
@dataclass class GroupPlan: key: str; members: list[Member]; primary: Member; model: str | None;
    serial: str | None; shutter_count: int | None; capture_time: datetime | None; subsec: str | None;
    offset: str | None; time_source: str; original_stem: str; original_num: int | None; folder_hint: str | None
def build_groups(entries: list[FileEntry], meta: dict[Path, dict]) -> list[GroupPlan]

# naming.py
def shoot_date(capture: datetime, boundary_hour: int = 12) -> date
def windows_safe(name: str) -> str
def canonical_stem(group: GroupPlan, body: str, template: str) -> str
def dest_dir(cfg: Config, shoot: date, shoot_name: str, body: str) -> PurePosixPath   # relative to the volume root
def check_unique(rel_paths: Iterable[str]) -> None          # raises NameCollision (case-insensitive, NFC)

# ingest.py
def plan_card_ingest(conn, cfg: Config, *, source: Path, target: Path, shoot_name: str | None, emit: Emitter) -> int
def run_job(conn, cfg: Config, job_id: int, *, emit: Emitter, should_stop=lambda: False) -> JobSummary

# status.py
def card_status(conn, cfg: Config, mount: Path) -> CardStatus  # read-only

# protocol.py
class Emitter:  def __init__(self, stream=sys.stdout, json_mode: bool = True); def emit(self, event: str, **fields) -> None
```

Repository functions (`catalog/repo.py`) don't commit. Callers wrap each unit of work in
`with repo.transaction(conn):` (the connection is autocommit, so a bare `with conn:` opens no transaction; D-025).
Timestamps written by the engine are UTC ISO-8601 (`repo.utcnow()`). Capture times are naive local time plus
`capture_offset` when known.

## 4. Card ingest data flow

1. `probe(source)` → it must be a card (or `allow_non_card=True` later); register it as CARD.
   `probe(target)` → it must carry a marker and have role WORKING.
2. `walk(source)` → `FileEntry` list, with ignored kinds dropped (§8).
3. `read_metadata` for the media files → `build_groups` → for each group, `canonical_stem` and `dest_dir`
   (the shoot date uses the day boundary) → `check_unique`.
4. Persist: `capture_groups` (upsert on `(body_id, shutter_count)`, reusing the stored `canonical_stem`),
   one `jobs` row, and one `job_items` row per member file (`state=planned`). The source instances are
   recorded as `state=indexed`.
5. `run_job` processes each item in its own transaction:
   - Look up candidates by `(size, partial_sha256)`.
   - If there is a candidate, hash the source fully. If it matches an asset whose verified non-CARD
     instances already meet `policy.required` (D-036), the item is `skipped_dup`.
   - Otherwise `copy_verified` into `target/dst_rel`, insert or find the asset, mark the target instance
     `verified`, link the source instance to the asset, and set the item to `committed`.
   - Before (re)running, `_prepare_resume` re-plans `failed` items, re-plans `committed` items whose target
     file is gone or a different size (the target instance becomes `missing`), and `sweep_partials` removes
     the engine's own leftover `.pvpartial` names. An unexpected exception leaves the item `planned` and the
     job `paused` (D-034).
6. `card_status(source)` → green when every media file on the card meets `policy.required`.

## 5. Catalog schema v1 (migration 1, authoritative)

```sql
CREATE TABLE volumes (
  volume_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('CARD','WORKING','MASTER','LEGACY')),
  label TEXT NOT NULL, fs_type TEXT, da_volume_uuid TEXT UNIQUE, marker_id TEXT UNIQUE,
  capacity_bytes INTEGER, last_mount_path TEXT, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE bodies (
  body_id INTEGER PRIMARY KEY, model TEXT NOT NULL, serial TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL,
  UNIQUE (model, serial)
);
CREATE TABLE shoots (
  shoot_id INTEGER PRIMARY KEY, name TEXT NOT NULL, shoot_date TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'general',
  folder_rel TEXT, created_at TEXT NOT NULL, UNIQUE (shoot_date, name)
);
CREATE TABLE sequences (
  sequence_id INTEGER PRIMARY KEY, shoot_id INTEGER REFERENCES shoots, kind TEXT NOT NULL,
  kind_source TEXT NOT NULL DEFAULT 'auto' CHECK (kind_source IN ('auto','user')), frame_count INTEGER,
  exposure_s REAL, iso INTEGER, fnumber REAL, focal_mm REAL, median_gap_s REAL,
  start_time TEXT, end_time TEXT, confidence REAL
);
CREATE TABLE capture_groups (
  group_id INTEGER PRIMARY KEY, body_id INTEGER REFERENCES bodies, shutter_count INTEGER,
  capture_time TEXT, capture_offset TEXT, time_source TEXT NOT NULL, canonical_stem TEXT NOT NULL,
  original_stem TEXT NOT NULL, original_num INTEGER, shoot_id INTEGER REFERENCES shoots,
  sequence_id INTEGER REFERENCES sequences, created_at TEXT NOT NULL,
  UNIQUE (body_id, shutter_count)
);
CREATE TABLE assets (
  asset_id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
  partial_sha256 TEXT NOT NULL, kind TEXT NOT NULL, group_id INTEGER REFERENCES capture_groups,
  group_role TEXT, first_seen_at TEXT NOT NULL
);
CREATE INDEX assets_size_partial ON assets (size, partial_sha256);
CREATE TABLE instances (
  instance_id INTEGER PRIMARY KEY, asset_id INTEGER REFERENCES assets,          -- NULL until hashed
  volume_id TEXT NOT NULL REFERENCES volumes, rel_path TEXT NOT NULL, rel_path_key TEXT NOT NULL,
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, partial_sha256 TEXT, kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('indexed','verified','mismatch','missing')),
  verified_at TEXT, verified_sha256 TEXT, lr_referenced INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT NOT NULL,
  UNIQUE (volume_id, rel_path_key)
);
CREATE INDEX instances_asset ON instances (asset_id, state);
-- state 'mismatch' with asset_id NULL: the full read failed with EIO during `hash` (idx-11, D-044); not re-read
-- unless `hash --retry-unreadable`, and `index` leaves it alone while size and mtime are unchanged.
CREATE TABLE sidecars (                                                         -- mutable XMP, versioned
  sidecar_id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES capture_groups,
  volume_id TEXT NOT NULL REFERENCES volumes, rel_path TEXT NOT NULL, sha256 TEXT NOT NULL,
  mtime_ns INTEGER NOT NULL, seen_at TEXT NOT NULL, UNIQUE (volume_id, rel_path, sha256)
);
CREATE TABLE jobs (
  job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('card_ingest','index_volume','baseline_hash','replicate','fixity_audit','card_clear')),
  source_volume_id TEXT REFERENCES volumes, target_volume_id TEXT REFERENCES volumes,
  state TEXT NOT NULL CHECK (state IN ('planned','running','paused','done','failed')),
  params_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE job_items (
  job_id INTEGER NOT NULL REFERENCES jobs, seq INTEGER NOT NULL, src_rel TEXT NOT NULL, dst_rel TEXT,
  group_id INTEGER REFERENCES capture_groups, role TEXT, asset_id INTEGER REFERENCES assets,
  state TEXT NOT NULL CHECK (state IN ('planned','copying','committed','skipped_dup','failed')),
  error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (job_id, seq)
);
```

`rel_path_key` = `unicodedata.normalize("NFC", rel_path).casefold()`. `rel_path` uses `/` separators and is
relative to the volume root.

## 5b. Catalog schema v2 (migration 2)

Approved with D-045 (ui spec). Appended after v1; v1 is unchanged. The SQL is the `V2` string in
`photovault/catalog/migrations.py`:

```sql
CREATE TABLE group_meta (
  group_id INTEGER PRIMARY KEY REFERENCES capture_groups,
  cam_lat REAL, cam_lon REAL, cam_alt REAL,          -- read from the file; never edited by the user
  lat REAL, lon REAL, alt REAL,                      -- effective geotag
  gps_source TEXT CHECK (gps_source IN ('camera','match','map','search','neighbour')),
  place_name TEXT,
  time_shift_s INTEGER NOT NULL DEFAULT 0,
  creator TEXT, copyright TEXT, title TEXT,
  keywords_json TEXT NOT NULL DEFAULT '[]',
  dirty INTEGER NOT NULL DEFAULT 0,                  -- 1 = sidecar needs (re)writing
  updated_at TEXT NOT NULL
);
CREATE TABLE geo_points (
  point_id INTEGER PRIMARY KEY, utc_time TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, alt REAL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, UNIQUE (source, source_ref)
);
CREATE INDEX geo_points_time ON geo_points (utc_time);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

`group_meta` holds the user's edits per capture group. `cam_*` is what the camera wrote. `lat/lon/alt` is the
effective geotag. `dirty = 1` means the XMP sidecar on WORKING must be rewritten. `geo_points` is the time-indexed
pool of GPS fixes (`utc_time` is UTC ISO-8601). `settings` holds user defaults (creator, copyright, default_offset,
match_window_s).

## 5c. Catalog schema v3 (migration 3)

Approved by the user on 2026-11-18 (idx-13, D-063). The SQL is the `V3` string in `photovault/catalog/migrations.py`:

```sql
CREATE TABLE volume_usage (
  volume_id TEXT NOT NULL REFERENCES volumes, category TEXT NOT NULL,
  files INTEGER NOT NULL, bytes INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (volume_id, category)
);
```

The files on a volume that the catalog does not track one by one, counted by `photovault/usage.py`. A complete
`index` replaces the volume's rows; a paused run writes none. Categories: `lightroom` (`*.lrcat*`, `*.lrprev` and
the `*.lrdata` folders) and `photo_extras` count as photography; `video_no_camera` (tracked `video` instances with no
camera signal), `video_other`, `audio`, `image_other`, `documents`, `archives`, `software` and `other` do not.
`disk_used` (files = 0) holds the drive's used bytes from `statvfs` at the index; a Mac folder has none. The drive
summary reports used − counted as "unaccounted" (system and hidden folders, unreadable files).

## 5d. Catalog schema v4 (migration 4)

Approved by the user on 2026-11-18 (rv-7, D-064). The SQL is the `V4` string in `photovault/catalog/migrations.py`:

```sql
CREATE INDEX assets_group ON assets (group_id, group_role);
```

An index only, no new column. Without it, finding each capture group's primary file scanned every asset per group,
so the Photos list (`repo.list_groups`) never finished on a catalog of 60k groups.

## 5e. Catalog schema v5 (migration 5)

Approved by the user on 2026-11-18 (sp-2, D-066). The SQL is the `V5` string in `photovault/catalog/migrations.py`:

```sql
ALTER TABLE geo_points ADD COLUMN place_name TEXT;  -- and city, state, country TEXT; accuracy_m REAL
ALTER TABLE group_meta ADD COLUMN city TEXT;        -- and state, country TEXT
```

Phone photos imported from the Photos library (`source = 'iphone'`, `source_ref` = the asset's UUID) carry the
place names Photos already stored, so a suggestion can offer a name, not only coordinates. A frame's `city`,
`state` and `country` go to its XMP as `photoshop:City`, `photoshop:State` and `photoshop:Country`; `place_name`
stays `Iptc4xmpCore:Location`.

## 5f. Catalog schema v6 (migration 6)

Approved by the user on 2026-11-18 (sp-2, D-070). The SQL is the `V6` string in `photovault/catalog/migrations.py`:

```sql
ALTER TABLE geo_points ADD COLUMN local_offset TEXT;  -- the fix's own UTC offset, '+HH:MM'
ALTER TABLE geo_points ADD COLUMN kind TEXT;          -- 'photo' | 'video'
```

Phone fixes imported by `photovault meta phone` carry the offset the photo was taken in (for time-zone suggestions,
sp-7) and whether it is a photo or a movie. `source` is `iphone`, `ipad` or `glasses` (D-071). Other sources leave
both columns NULL. The cu-0 proposal becomes V8 (D-080).

## 5g. Catalog schema v7 (migration 7)

Approved by the user on 2026-11-18 (sh-0, D-080; review `docs/reviews/2026-11-18-sh-0-shoot-tagging.md`). The SQL is
the `V7` string in `photovault/catalog/migrations.py`:
- `trips` (name, start and end date) and `shoots.trip_id`: the trip → shoot level.
- `shoots.local_offset`: the shoot's wall-clock offset.
- `shoots.name_source`: folder | phone | user | fallback.
- `shoot_plan_decisions`: the user's dry-run decisions, kept across re-plans. Kinds: accept, split, merge, skip,
  rename, reject_name, move. A review action that touches several shoots ("Accept trip") is stored as one row per
  shoot sharing `payload.batch` (sh-2).
- `shoot_apply_log`: each group's previous `shoot_id` per apply, for undo. `job_id` holds the apply id
  (`apply-<UTC YYYYMMDD-HHMMSS>`), not a `jobs` row: Apply and Undo run as tasks (sh-4).
- `body_clock`: a correction in seconds per body over a UTC range, with source phone | gps | user, and `accepted`.
- `capture_groups.subsec`.

**Time model (sh-0 §0).** Every comparison uses the **instant**:
`capture_time` − the camera's offset (`capture_offset`, else the `default_offset` setting) + `group_meta.time_shift_s`
+ the accepted `body_clock` correction covering it. The instant is computed, never stored. Display, shoot dates (noon
boundary) and "Same day" use the **wall clock**: the instant in the shoot's `local_offset` when the photo is in a shoot
that has one, else `capture_time` as the camera wrote it. A phone-derived offset reaches the wall clock only through a
reviewed shoot (decision 2), never silently.

## 5h. Catalog schema v8 (migration 8)

Approved with D-096 (safe card clear, cc-1). The SQL is the `V8` string in `photovault/catalog/migrations.py`. It only
widens the `jobs.kind` CHECK to include `card_clear` (shown in the v1 listing above); SQLite cannot alter a CHECK, so
the table is rebuilt (create `jobs_new`, copy every row, drop, rename) and `db.migrate` runs migrations with foreign
keys off and checks them before COMMIT. No row changes. A `card_clear` job has `source_volume_id` = the card and no
target; its `job_items` use `src_rel` (the card file), `state` `committed` once deleted or `failed` with `error`. The
cu-0 proposal (formerly V8) becomes V9.

## 6. Naming (D-004, D-005)

- **File stem:** the template `M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}`, with derivative suffixes kept.
  - `num`: the trailing digits of the original stem, last 4 (`DSC_0781` → 781, `IMG_4821` → 4821,
    `GX010020` → 20). It is 0 when there are none.
  - `date`: `YYYYMMDD` of the capture time. `time`: `HHMMSS`. `subsec`: 2 digits, right-padded with 0;
    `00` when unknown.
  - `body`: the nickname from `[bodies]`, keyed by EXIF Model (`NIKON Z 6` → `Z6`).
  - `ident`: the shutter count as 7 digits when known. Otherwise the original stem without `_` or `-`
    (`IMG4821`, `DJI0250`, `GX010020`).
  - Example: `M0781_20261027-181442-81_Z6_0084022.NEF`. Derivative: `M0850_…_Z6_0084091-Edit.tif`.
- **Extensions:** uppercased for camera originals; derivative extensions kept as written.
- **Folders:** `{ingest.working_subdir}/{YYYY}/{YYYYMMDD} - {Shoot name}/{body}/`, where `YYYY` and `YYYYMMDD`
  come from `shoot_date` (noon boundary by default). Example:
  `Photography/Imports/2026/20261027 - Lighthouse Moonrise/Z6/`.
- **Windows-safe:**
  - Replace `\/:*?"<>|` and control characters with `_`.
  - Strip trailing dots and spaces.
  - Append `_` to the reserved names `CON PRN AUX NUL COM1–9 LPT1–9`.
  - Each path component is at most 120 characters.
- **Time sources:** `exif`, `quicktime_local`, `quicktime_utc` or `mtime`. Files dated by `mtime` are
  reported in the plan.
- **Backlog:** re-ordered `num` (a per-shoot sequence instead of the camera number).

## 7. Volume identity

- **WORKING and MASTER:** the marker file `/.photovault-id`, JSON `{"volume_id": "<uuid4>", "created_at": …}`.
- **CARD and LEGACY:** matched by the DiskArbitration `VolumeUUID` from `diskutil info -plist`. Nothing is
  ever written to them. An in-camera format gives a card a new UUID, and therefore a new CARD volume. That is
  expected.
- Volume names are never identities.

## 8. File kinds (`scan.classify`, by lowercase extension)

| Kind | Extensions | Imported | Group role |
|---|---|---|---|
| raw | nef nrw dng cr2 cr3 arw raf orf rw2 | yes | primary |
| jpeg | jpg jpeg | yes | primary if there is no raw, else `jpeg` |
| heic | heic heif | yes | primary |
| video | mov mp4 | yes | primary, or `live_video` beside a heic or jpeg |
| derivative | tif tiff psd psb, or a stem ending `-Edit` / `-HDR` / `-Pano` / `-Enhanced-NR` | yes | `derivative` (attached to its base stem) |
| sidecar | xmp srt aae | yes | `sidecar` / `telemetry` (srt) / `aae` |
| proxy | lrv lrf | yes | `proxy` |
| ignored | thm scr ais dat dsc bin lrprev db ds_store, and anything else | no | — |

`ignored` files are not tracked, but `index` counts them by usage category (§5c). A `video` is camera footage when
its folder holds camera photos, a camera sidecar (`lrv thm aae xmp`, not `srt`) shares its stem, its stem is
camera-style, `DCIM` is on its path, or ExifTool finds a Make or Model; otherwise it counts as `video_no_camera`.
It is still tracked and imported either way.

The walk skips dot-names (including `._*`), zero-byte files, and the directories `.Trashes`,
`.Spotlight-V100`, `.fseventsd`, `System Volume Information`, `LOST.DIR`, `*.lrdata`.

## 9. Policy

`[policy] required = { WORKING = 1 }` applies today (D-003). It becomes `{ WORKING = 1, MASTER = 1 }` after
the full dedupe (rep-5). `card_status` is green when, for every imported-kind file on the card, its asset has
at least the required number of `verified` instances on volumes of each listed role.

## 10. JSON-lines protocol v1

The engine writes one JSON object per line: `{"v":1,"event":E,"ts":"<utc>",…}`. Events:

| Event | Fields |
|---|---|
| `progress` | `phase`, `current`, `total`, `message` |
| `item` | `job_id`, `seq`, `state`, `src`, `dst`, `error?` |
| `warning` | `code`, `message` |
| `result` | command-specific summary |
| `error` | `code`, `message` |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | error |
| 2 | usage |
| 3 | `card-status` red |
| 4 | job paused, resumable |

## 11. Build order

The lanes are in `docs/phase-roadmap.md`. Each line below reads "task ← the tasks it waits on".

- fnd-1, fnd-2, fnd-5, ing-1, ing-3: no prerequisites.
- fnd-3 ← fnd-2
- fnd-4 ← fnd-3
- fnd-6 ← fnd-1, fnd-4
- ing-2 ← ing-1
- ing-4 ← ing-3, fnd-5
- ing-5 ← ing-4, fnd-1
- ing-6 ← fnd-3, fnd-4, fnd-5, fnd-6, ing-3, ing-4, ing-5
- ing-7 ← ing-2, ing-6
- ing-8 ← ing-7
- ing-9 ← ing-8
- ing-10 ← ing-9
- ing-11 [user] ← ing-10
