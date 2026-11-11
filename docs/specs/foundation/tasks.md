# Spec: foundation (Phase 1)

Goal: build the pieces every later phase stands on: config, the catalog, volume identity, metadata reading,
and the CLI/protocol skeleton. None of it touches a real drive.

Rules:
- `docs/steering/task-execution.md` applies.
- One task per session, one commit per task: `feat(fnd-N): …  [fnd-N]`.
- IDs are stable.
- Unmarked tasks are written for Haiku; **[stronger]** and **[user]** are marked.

- [x] 0. Scaffold, hooks and process (done by the planning session)
  - ID: fnd-0
  - What exists:
    - the `pyproject.toml` (uv, Python ≥ 3.12, pytest) and the package stub `photovault/__init__.py`;
    - the hooks `.claude/hooks/guard_volumes.py` and `.claude/hooks/preflight.sh`, wired in
      `.claude/settings.json`, plus the `/close-the-loop` skill;
    - the steering docs, the redacted EXIF fixture `tests/fixtures/meta/cards.jsonl.gz`,
      `tests/test_guard_volumes.py` and `tests/test_smoke.py`.
  - Verify: `uv run pytest -q`
  - Result (2026-11-11): `uv run pytest -q` → 51 passed. The hook was pipe-tested with the configured command:
    rm on /Volumes → exit 2, ls /Volumes → exit 0.

- [x] 1. Config loader
  - ID: fnd-1
  - Create `photovault/config.py` with these frozen dataclasses:
    - `IngestConfig(working_subdir: str = "Photography/Imports")`
    - `NamingConfig(template: str = "M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}", day_boundary_hour: int = 12)`
    - `PolicyConfig(required: dict[str, int] = {"WORKING": 1})`
    - `Config(home: Path, catalog_path: Path, ingest, naming, policy, bodies: dict[str, str], exiftool_workers: int = 4)`
  - Functions: `default_home()`, `load_config(path=None)` and `ConfigError`, as in system-design §3.
  - Read TOML with stdlib `tomllib`. The sections are `[ingest] [naming] [policy] [bodies] [performance]`.
    `catalog_path` defaults to `home / "catalog.sqlite"`.
  - Default `bodies`:
    `{"NIKON Z 6": "Z6", "NIKON Z 6_2": "Z6II", "NIKON Z 8": "Z8", "FC3411": "AIR2S", "iPhone 14 Pro": "IP14P", "HERO10 Black": "HERO10"}`.
    A config file's `[bodies]` table merges over these defaults.
  - Validation raises `ConfigError` naming the bad key, when:
    - `day_boundary_hour` is outside 0–23;
    - a policy role is not one of WORKING/MASTER/LEGACY, or a count is < 1;
    - the template lacks `{body}` or `{ident}`;
    - `exiftool_workers` is outside 1–8;
    - the config has an unknown top-level section.
  - Create `config/photovault.example.toml`: every key, commented, with defaults. No serials.
  - Create `tests/test_config.py` covering:
    - defaults with no file;
    - `PHOTOVAULT_HOME` honoured;
    - a file overriding each section;
    - a bodies merge;
    - each validation error.
  - Verify: `uv run pytest tests/test_config.py -q`
  - _Design:_ system-design §3 (config), §6, §9; decisions D-003, D-004, D-005
  - Result (2026-11-11): `uv run pytest tests/test_config.py -q` → 26 passed; `uv run pytest -q` → 77 passed.
    Extra checks, from the agent's reading (D-014): unknown keys inside a known section raise too; `[policy]
    required` replaces the default table rather than merging; `catalog_path` is always `home/catalog.sqlite`
    (no key). The example test uncomments `# key = value` lines and checks they load to the defaults.

- [ ] 2. Catalog connection and migration runner
  - ID: fnd-2
  - Create `photovault/catalog/__init__.py` (empty), `photovault/catalog/migrations.py` and
    `photovault/catalog/db.py`.
  - In `migrations.py`, `MIGRATIONS: list[str] = [V1]`. `V1` is the SQL of system-design §5 **verbatim**.
  - In `db.py`:
    - `connect(path)` creates the parent dirs and sets the PRAGMAs `journal_mode=WAL`, `foreign_keys=ON`,
      `busy_timeout=5000` and `synchronous=FULL`, with `row_factory=sqlite3.Row`.
    - `migrate(conn)` reads `PRAGMA user_version`. Each pending migration *n* runs as one script:
      `BEGIN; <sql>; PRAGMA user_version=n; COMMIT;`. On error it rolls back and re-raises.
    - `snapshot(conn, dest)` runs `VACUUM INTO`, and raises `FileExistsError` if `dest` exists.
  - Create `tests/test_catalog_db.py` covering:
    - a fresh DB migrates to 1, and all 11 tables exist;
    - a second `migrate` is a no-op;
    - WAL is on;
    - a foreign-key violation raises (an instance with an unknown `volume_id`);
    - a bad role is rejected by CHECK;
    - the snapshot opens and has the same tables.
  - Verify: `uv run pytest tests/test_catalog_db.py -q`
  - _Design:_ system-design §3 (catalog/db), §5

- [ ] 3. Catalog repository: volumes, bodies, assets, instances, jobs
  - ID: fnd-3
  - Blocks on: fnd-2
  - Create `photovault/catalog/repo.py`. No function commits; the docstring says callers use `with conn:`.
  - Utilities: `utcnow() -> str`, `path_key(rel_path) -> str` (NFC + casefold).
  - Volumes and bodies:
    - `upsert_volume(conn, *, volume_id, role, label, fs_type, da_volume_uuid, marker_id, capacity_bytes, mount_path) -> None`
    - `find_volume(conn, *, marker_id=None, da_volume_uuid=None) -> Row | None`
    - `get_or_create_body(conn, model, serial, nickname) -> int`
  - Assets:
    - `find_asset(conn, sha256) -> Row | None`
    - `insert_asset(conn, *, sha256, size, partial_sha256, kind, group_id=None, group_role=None) -> int`
      (returns the existing id when the sha256 is already present)
    - `find_candidates(conn, size, partial_sha256) -> list[Row]`
  - Instances:
    - `upsert_instance(conn, *, volume_id, rel_path, size, mtime_ns, kind, state, asset_id=None, partial_sha256=None, verified_sha256=None) -> int`
      (keyed on `(volume_id, path_key(rel_path))`; sets `verified_at` when the state is `verified`)
    - `get_instance(conn, volume_id, rel_path) -> Row | None`
    - `verified_counts_by_role(conn, asset_id) -> dict[str, int]` (excludes CARD)
  - Jobs:
    - `create_job(conn, *, kind, source_volume_id, target_volume_id, params: dict) -> int`
    - `add_job_items(conn, job_id, items: Iterable[dict]) -> None` (dict keys: `seq`, `src_rel`, `dst_rel`,
      `group_id`, `role`)
    - `set_item_state(conn, job_id, seq, state, *, asset_id=None, error=None) -> None`
    - `pending_items(conn, job_id) -> list[Row]` (states `planned`/`copying`, ordered by seq)
    - `set_job_state(conn, job_id, state) -> None` (sets `finished_at` for done/failed)
  - Create `tests/test_catalog_repo.py`: one test per function, plus case-insensitive instance uniqueness
    (`DCIM/A.NEF` vs `dcim/a.nef` → same row).
  - Verify: `uv run pytest tests/test_catalog_repo.py -q`
  - _Design:_ system-design §3, §5

- [ ] 4. Volumes: probe, card detection, markers, registration
  - ID: fnd-4
  - Blocks on: fnd-3
  - Create `photovault/volumes.py` with everything in system-design §3 (volumes), plus the exceptions
    `CardWriteRefused` and `UnknownVolume`.
  - `probe` runs `diskutil info -plist <mount>` through the injected `run` and parses it with `plistlib`. Keys:
    - `VolumeName` → `name`;
    - `FilesystemType` → `fs_type`, lowercased;
    - `VolumeUUID` → `da_volume_uuid`;
    - `TotalSize` (else `Size`) → `capacity_bytes`;
    - `Internal` → `internal`.
    Missing keys → None/False. `marker_id` comes from `read_marker`.
  - `write_marker`:
    - writes `/.photovault-id` atomically (temp file, then rename);
    - refuses (`CardWriteRefused`) when `looks_like_card(mount)` is true;
    - refuses if a marker already exists with a different id.
  - `register_volume`:
    - match by marker, then by `da_volume_uuid`. On a match, update `last_seen_at` and `last_mount_path` and
      return the id;
    - no match and a card → create a CARD volume (label `card:<name>`);
    - no match and not a card → requires `role`, else raises `UnknownVolume`. WORKING and MASTER require a
      marker (the caller writes it first). LEGACY never gets one.
  - Create `tests/test_volumes.py`:
    - fake plists built with `plistlib.dumps` and fake UUIDs, and `tmp_path` dirs as mounts;
    - cases: card detection, marker round trip, refusal on a card, matching by UUID, a rename keeps the id,
      `UnknownVolume`.
  - Verify: `uv run pytest tests/test_volumes.py -q`
  - _Design:_ system-design §3 (volumes), §7; vault-rules 1

- [x] 5. Metadata reader (port from the archive)
  - ID: fnd-5
  - Approves the dependency: run `uv add PyExifTool`.
  - Create `photovault/metadata.py` by porting from `git show archive/v0-cli-2026-11-11:photo_ingest/analyzer.py`:
    - `_find_exiftool` (keep the PATH-then-Homebrew order);
    - the strategy comment block (keep the `-fast2` warning);
    - the tables, `normalize_exiftool_row` and `_exiftool_read_chunks`.
  - Drop the Pillow fallback, the SQLite cache and the analytics code.
  - Extend `EXIFTOOL_TAGS` with `SubSecTimeOriginal`, `OffsetTimeOriginal`, `ImageUniqueID`,
    `ContentIdentifier` and `CreationDate`.
  - `normalize_exiftool_row` additionally outputs:
    - `SubSecTimeOriginal` (2-digit str);
    - `OffsetTimeOriginal` (e.g. `-05:00`);
    - `ContentIdentifier`;
    - `CaptureTimeSource`.
  - Capture-time precedence, and the `CaptureTimeSource` value for each:

    | Source | `CaptureTimeSource` |
    |---|---|
    | `EXIF:DateTimeOriginal` | `exif` |
    | `QuickTime:CreationDate` (has its own offset) | `quicktime_local` |
    | `QuickTime:CreateDate` (UTC by spec) | `quicktime_utc` |
    | `EXIF:CreateDate` | `exif` |

  - `read_metadata(paths, *, workers, progress)` returns `MetadataResult`. Files ExifTool can't read go to
    `errors` with a message.
  - With no exiftool binary: every path goes into `errors` with "exiftool not found", and nothing raises.
  - Create `tests/test_metadata.py` covering:
    - normalising synthetic raw rows for a Nikon NEF (serial, shutter count, subsec), an iPhone MOV
      (`QuickTime:CreationDate` with offset), a DJI JPG and a GoPro MP4 (`QuickTime:CreateDate` → `quicktime_utc`);
    - the empty/error row → `{}`;
    - the exiftool-missing path (monkeypatch `_find_exiftool`);
    - a `@pytest.mark.realfiles` test that reads `testdata/corpus/**/*.NEF` when present and asserts Model,
      CameraSerialNumber, ShutterCount and DateTimeOriginal (skips if absent).
  - Verify: `uv run pytest tests/test_metadata.py -q`
  - _Design:_ system-design §3 (metadata); `docs/legacy/handoff-v0.md` "Metadata reading"
  - Result (2026-11-11): `uv run pytest tests/test_metadata.py -q` → 12 passed, 1 skipped (realfiles: no corpus
    yet, skips as designed); `uv run pytest -q` → 89 passed, 1 skipped. `uv add PyExifTool` → pyexiftool 0.5.6.
    Left out of scope: the Pillow fallback, the SQLite cache, the analytics code. The old `DateTime` key is
    replaced by `DateTimeOriginal` + `CaptureTimeSource` (D-016).

- [ ] 6. Protocol emitter and CLI skeleton
  - ID: fnd-6
  - Blocks on: fnd-1, fnd-4
  - Create `photovault/protocol.py`:
    - `Emitter` per system-design §10.
    - JSON mode writes one line per event and flushes.
    - Human mode prints `phase current/total message` for progress, and `key: value` lines for a result.
  - Create `photovault/cli.py` with `main(argv: list[str] | None = None) -> int`, using argparse:
    - the global flag `--json`;
    - `catalog init` (connect + migrate, emits a result `{version}`);
    - `volumes probe <mount>` (read-only, emits the probe);
    - `volumes list` (all catalog volumes).
  - Exit codes per §10.
  - Add `[project.scripts] photovault = "photovault.cli:main"` to `pyproject.toml`.
  - Create `tests/test_cli.py`: `main([...])` with `capsys`, `PHOTOVAULT_HOME=tmp_path`, and
    `volumes.probe` monkeypatched; parse the JSON lines and assert the events and exit codes.
  - Verify: `uv run pytest tests/test_cli.py -q`
  - _Design:_ system-design §3 (protocol), §10

- [x] 7. **[user + agent]** Local sample corpus for real-file tests
  - ID: fnd-7
  - User:
    - Copy a small set of real files (≈ 1–2 GB) into `testdata/corpus/` (gitignored), keeping each source's
      folder structure, with `cp` or Finder from the cards and drives.
    - Wanted:
      - Z6 and Z6II NEF+JPG pairs; ~~Z8 NEF+JPG pairs~~ (struck: no Z8 owned yet);
      - one NEF with a Lightroom `.xmp`, plus a `-Edit.tif` and a `-HDR.dng`;
      - ~10 consecutive night frames with a framing shot in the middle, and a same-second burst;
      - DJI `DJI_xxxx.JPG` + `.MP4` + `.SRT`, plus one `PANORAMA/100_xxxx/` folder (3 DNGs);
      - GoPro MP4 + LRV + THM;
      - iPhone HEIC + AAE + Live MOV.
  - Agent:
    - Create `tests/realfiles/conftest.py` with a session `corpus` fixture: `Path("testdata/corpus")`;
      skip the module if it is missing or empty.
    - Create `tests/realfiles/test_corpus_metadata.py`: every media file in the corpus gets a capture time
      from `read_metadata`, and every Nikon file gets a serial and a shutter count.
    - Write the corpus inventory (filenames and what each one exercises) to `docs/private/CORPUS.md`, not to
      git.
  - Verify: `uv run pytest tests/realfiles -q -m realfiles`
  - _Design:_ vault-rules 10; audit §6 Phase 0
  - Result (2026-11-11): `uv run pytest tests/realfiles -q -m realfiles` → 2 passed on the 10-file corpus
    (`testdata/corpus/flats1/`); `uv run pytest -q` → 92 passed. Inventory: `docs/private/CORPUS.md`.
    Left out of scope (partial user copy): Z6II pair, night run and burst, `-Edit.tif`/`-HDR.dng` in the corpus
    (they are in `testdata/x9_sample/`, not copied), GoPro MP4+LRV+THM, iPhone HEIC+AAE. Z8 struck: not owned.
    Layout choice (D-017): `testdata/corpus/<source>/<original path>`.
