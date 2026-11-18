# Spec: ui — local web UI, drive memory, metadata completion (D-045)

Goal: `uv run photovault serve` opens a local web page (127.0.0.1 only) that lists every drive the catalog
remembers plus what is mounted now, runs the existing engine commands (assign, ingest, index, hash,
card-status), and lets the user find frames missing a geotag, creator/copyright, keywords or a correct clock,
and fix them in bulk. Fixes live in the catalog and are written as XMP sidecars next to the copies on WORKING
only. Original media bytes never change (vault-rules 1, 3, 5).

User decisions (D-045, 2026-11-17): local web UI (not SwiftUI yet); catalog + XMP sidecars on WORKING;
geotag suggestions from GPS-bearing sources (drone, phone) matched by time, shown as a recommendation the user
accepts; map pick and place-name search (no typing coordinates); creator/copyright, shoot name and keywords,
camera clock offset. The new tables, the `meta` and `serve` commands and sidecar writes below are approved.

Rules for every task:
- `docs/steering/task-execution.md` applies. Python ≥ 3.12, stdlib only (no new dependencies). Browser
  libraries load from a CDN at run time (Leaflet 1.9.4 from cdnjs); nothing is vendored.
- Tests use `tmp_path` catalogs (`db.connect(tmp_path/"c.db")` then `db.migrate(conn)`) and never `/Volumes`.
  Do not run any command against `/Volumes`.
- Never call exiftool in unit tests: monkeypatch `photovault.metadata.read_metadata` (it returns a
  `MetadataResult` with `.meta: dict[Path, dict]` and `.errors`); GPS keys are `GPSLatitude`, `GPSLongitude`,
  `GPSAltitude` (floats, signed), capture time keys are whatever `normalize_exiftool_row` emits (read it).
- Do not edit `ingest.py`, `copy_engine.py`, `vault-rules.md`, or migration 1.
- Do not commit. Leave the working tree for review. Finish with `uv run pytest -q` green.

## Shared definitions

`time_shift_s` corrects a body's wrong clock: corrected time = capture_time + time_shift_s.
UTC of a group: parse `capture_groups.capture_time` (ISO). If naive, apply `capture_offset` ("+HH:MM"); if
still naive, apply the `settings` value `default_offset` (default "+00:00"). Then add `time_shift_s`.

---

- [x] 1. Catalog migration 2 and repo functions
  - ID: ui-1
  - Files: `photovault/catalog/migrations.py`, `photovault/catalog/repo.py`, `docs/steering/system-design.md`
    (add a "§5b Catalog schema v2 (migration 2)" section right after §5 with the SQL below),
    `tests/test_meta_repo.py`. Update any existing test that asserts `user_version == 1` to `len(MIGRATIONS)`.
  - Append `V2` to `MIGRATIONS` (do not touch `V1`):
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
  - Repo functions (keyword-only args after `conn`, use `utcnow()`, no commits inside; callers use
    `transaction`):
    - `ensure_group_meta(conn, group_id) -> None` — insert an empty row if absent.
    - `get_group_meta(conn, group_id) -> Row | None`.
    - `set_camera_gps(conn, group_id, *, lat, lon, alt) -> None` — ensure row; set cam_*; if `lat` is
      NULL in the row, also set lat/lon/alt = cam values and gps_source='camera'. Does not set dirty.
    - `update_group_meta(conn, group_ids: list[int], *, fields: dict) -> int` — allowed keys: lat, lon, alt,
      gps_source, place_name, time_shift_s, creator, copyright, title, keywords_add (list[str]),
      keywords_remove (list[str]), keywords_set (list[str]). Unknown key → `ValueError`. Keywords are stored
      as a sorted, de-duplicated JSON list (case-insensitive de-dupe, keep first spelling, strip whitespace,
      drop empties). Sets `dirty=1`, `updated_at`. Ensures rows exist. Returns rows changed.
    - `upsert_geo_point(conn, *, utc_time, lat, lon, alt, source, source_ref) -> None` (INSERT … ON CONFLICT
      (source, source_ref) DO UPDATE).
    - `geo_points_between(conn, start_utc: str, end_utc: str) -> list[Row]` ordered by utc_time.
    - `get_setting(conn, key, default=None) -> str | None`, `set_setting(conn, key, value) -> None`.
    - `list_groups(conn, *, volume_id=None, shoot_id=None, missing: str | None = None, limit=200, offset=0)
      -> list[Row]` — one row per capture group: group_id, canonical_stem, capture_time, capture_offset,
      body nickname (`bodies.nickname`, may be NULL), shoot_id, shoot name, shoot_date, and every group_meta
      column (LEFT JOIN, so NULLs when absent), plus `rel_path` and `volume_id` of one verified-or-indexed
      instance of the group's `group_role='primary'` asset (prefer a WORKING volume; else any). When
      `volume_id` is given, only groups that have an instance on that volume. `missing`: `'gps'` → lat IS
      NULL; `'creator'` → creator IS NULL or ''; `'keywords'` → keywords_json = '[]' or NULL; `'any'` → any of
      the three. Order by capture_time, group_id. Read `assets.group_role` values in `ingest.py`/`index.py`
      to confirm the primary role's spelling before writing the query.
    - `missing_counts(conn, *, volume_id=None) -> dict` → `{"groups": n, "gps": n, "creator": n,
      "keywords": n}` with the same semantics.
    - `list_shoots(conn) -> list[Row]` (shoot_id, name, shoot_date, group count).
  - Verify: `uv run pytest tests/test_meta_repo.py tests/test_catalog_db.py -q`, then `uv run pytest -q`.

- [x] 2. XMP sidecar renderer and writer
  - ID: ui-2 (independent of ui-1; pure module)
  - Files: `photovault/xmp.py`, `tests/test_xmp.py`.
  - `gps_to_xmp(value: float, is_lat: bool) -> str` → XMP GPSCoordinate "DDD,MM.mmmmmmR", e.g.
    `gps_to_xmp(-33.8568, True) == "33,51.408000S"`, `gps_to_xmp(151.2153, False) == "151,12.918000E"`
    (degrees integer, minutes with 6 decimals, ref N/S/E/W).
  - `render_xmp(meta: dict) -> str` where meta may hold: lat, lon, alt, place_name, creator, copyright,
    title, keywords (list[str]), date_original (ISO string with offset, only when a time shift was applied).
    Output a complete `<?xpacket begin…?>` … `<?xpacket end="w"?>` document with one `rdf:Description`,
    namespaces `exif`, `dc`, `photoshop`, `xmp`, `Iptc4xmpCore`. Mapping: lat/lon → `exif:GPSLatitude`,
    `exif:GPSLongitude`, plus `exif:GPSVersionID="2.3.0.0"`; alt → `exif:GPSAltitude` as a rational
    "N/1000" of abs(alt) with `exif:GPSAltitudeRef` "0" (≥0) or "1"; place_name →
    `Iptc4xmpCore:Location`; creator → `dc:creator/rdf:Seq/rdf:li`; copyright →
    `dc:rights/rdf:Alt/rdf:li xml:lang="x-default"`; title → `dc:title` (same Alt form); keywords →
    `dc:subject/rdf:Bag/rdf:li` each; date_original → `exif:DateTimeOriginal` and `photoshop:DateCreated`.
    Omit absent fields. Escape text with `xml.sax.saxutils.escape` (and quote attributes). Output must parse
    with `xml.etree.ElementTree` after stripping the xpacket PIs.
  - `sidecar_path(media_path: Path) -> Path` → same folder, same stem, `.xmp` (Lightroom convention:
    `DSC_1.NEF` → `DSC_1.xmp`).
  - `write_sidecar(path: Path, text: str, *, expected_sha256: str | None) -> str` — refuse (raise
    `SidecarConflict`) if `path` exists and its SHA-256 differs from `expected_sha256` (someone else, e.g.
    Lightroom, owns it; `expected_sha256=None` means "must not exist"). Write to `.<name>.pvtmp` in the same
    folder, `flush` + `os.fsync`, `os.replace` onto `path`. Never write xattrs. Return the new SHA-256.
  - Tests: the two coordinate examples; round-trip parse; keyword escaping (`&`, `<`); conflict raised on a
    foreign file; second write with the right expected hash succeeds.
  - Verify: `uv run pytest tests/test_xmp.py -q`.

- [x] 3. Geo suggestions and the `meta` CLI group
  - ID: ui-3 (after ui-1 and ui-2)
  - Files: `photovault/geo.py`, `photovault/meta.py`, `photovault/cli.py` (add the `meta` group only),
    `tests/test_geo.py`, `tests/test_meta_cli.py`.
  - `geo.group_utc(row, default_offset: str) -> datetime | None` per "Shared definitions".
  - `geo.suggest(conn, group_id, *, window_s=1800, limit=3) -> list[dict]` — candidates from `geo_points`
    within ±window of the group's UTC, excluding points whose `source_ref == f"group:{group_id}"`. Each dict:
    lat, lon, alt, source, source_ref, delta_s (signed, point − frame), confidence (`'high'` ≤120 s,
    `'medium'` ≤900 s, else `'low'`). If two points bracket the frame within the window, the first suggestion
    is a linear interpolation between them (source `'interpolated'`). Sorted by abs(delta_s).
  - `meta.scan_volume(conn, cfg, mount: Path, volume_id: str, emit) -> dict` — for each instance on the
    volume whose asset has a group and whose group has no `group_meta` row (or `--rescan`), read metadata of
    the primary file (`read_metadata`, read-only), call `set_camera_gps` when GPS is present (else
    `ensure_group_meta`), and when GPS is present also `upsert_geo_point(source='catalog',
    source_ref=f"group:{gid}")` with the group's UTC. Emit `progress` events like `index` does. Returns
    counts {scanned, with_gps, errors}.
  - `meta.scan_refs(conn, cfg, folder: Path, label: str, emit) -> dict` — walk the folder (use `scan` module
    classification for media kinds), read metadata, and for every file with GPS and a capture time,
    `upsert_geo_point(source=label, source_ref=<path relative to folder>)`. UTC from the file's offset, else the
    `default_offset` setting. Read-only. For a phone photo export folder or a drone card.
  - `meta.write_sidecars(conn, cfg, mount: Path, volume_id: str, emit, *, dry_run=False) -> dict` — refuse
    unless the volume's role is `WORKING` (exit code as other refusals in cli.py). For each dirty group with an
    instance on the volume whose primary file is a raw kind (check `scan` kinds; JPEG/HEIC/video groups are
    skipped and counted as `catalog_only`): render from group_meta (+ `date_original` when time_shift_s ≠ 0),
    look up the last sidecar row for (volume_id, rel_path) in the existing `sidecars` table to get
    `expected_sha256`, `write_sidecar`, insert a `sidecars` row (sha256, mtime_ns, seen_at), set dirty=0.
    Conflicts are counted and emitted as `warning`, never overwritten.
  - CLI (follow the `cmd_*`/`emit` patterns and exit codes already in `cli.py`):
    `meta scan --volume <mount> [--rescan]`, `meta refs --folder <path> --label <name>`,
    `meta sidecars --volume <mount> [--dry-run]`, `meta missing [--volume <mount>]` (prints
    `missing_counts`), `meta set --group <id>... [--lat --lon --place --creator --copyright --title
    --keyword-add K... --time-shift S]` for scripting. Resolve a mount to a volume_id the same way `index`
    does.
  - Verify: `uv run pytest tests/test_geo.py tests/test_meta_cli.py -q`, then `uv run pytest -q`.
  - Known gap: a later `time_shift_s` does not move that group's own `catalog` geo point until `meta scan --rescan`.

- [x] 4. Local web server and JSON API
  - ID: ui-4 (after ui-3)
  - Files: `photovault/web/__init__.py`, `photovault/web/server.py`, `photovault/cli.py` (add `serve`),
    `tests/test_web_api.py`. Static files are ui-5's; create `photovault/web/static/index.html` as a
    placeholder only if absent.
  - `serve [--port 8765] [--no-open]`: `ThreadingHTTPServer` bound to `127.0.0.1` only; opens the browser
    with `webbrowser.open` unless `--no-open`. One sqlite connection per request (`db.connect(cfg catalog
    path)`). Reject requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` (DNS
    rebinding guard). POST bodies are JSON; responses are JSON with `Content-Type: application/json`.
    Serve `/` and `/static/*` from `photovault/web/static/` (no path traversal: resolve and check parent).
  - Long engine commands run as subprocesses of the existing CLI:
    `[sys.executable, "-m", "photovault", "--json", ...]` (check how `photovault` is runnable as a module;
    add `photovault/__main__.py` calling `cli.main` if missing). A `TaskRunner` keeps tasks in memory:
    id, argv, state (running/done/failed/paused), exit code, and the parsed JSON lines (keep the last 500).
    `POST /api/tasks/<id>/pause` sends SIGINT (the engine pauses resumable jobs on Ctrl-C).
  - Endpoints:
    - `GET /api/drives` → `{"drives": [...]}`: union of mounted drives (same probe logic as `cmd_drives`;
      factor that loop into a function both use) and catalog volumes (`list_volumes`). Each: volume_id
      (or null), label, role, mount (or null), mounted (bool), fs, size, free, last_seen_at,
      looks_like_card. Remembered-but-unplugged drives appear with mounted=false.
    - `GET /api/jobs` → recent jobs (`list_jobs`).
    - `POST /api/run` `{"cmd": "...", "args": {...}}` → `{"task_id": n}`. Allowed cmds and argv mapping
      (whitelist; anything else → 400): `assign` (mount, role, label) → `volumes assign`; `ingest` (source,
      target, name, dry_run) → `ingest`; `resume` (job_id); `card_status` (mount); `index` (mount); `hash`
      (mount); `meta_scan` (mount); `meta_refs` (folder, label); `meta_sidecars` (mount, dry_run). Read
      `build_parser()` in cli.py for each command's exact flags; never build a shell string.
    - `GET /api/tasks` and `GET /api/tasks/<id>?since=<n>` → state + events after index n.
    - `GET /api/shoots` → `list_shoots`.
    - `GET /api/groups?volume_id=&shoot_id=&missing=&limit=&offset=` → `list_groups` rows; keywords as a
      list; plus `missing_counts` for the same filter.
    - `GET /api/groups/<id>/suggestions` → `geo.suggest` (window from setting `match_window_s`, default
      1800).
    - `POST /api/groups/meta` `{"group_ids": [...], "fields": {...}}` → `update_group_meta` in a
      transaction; 400 on `ValueError`.
    - `GET /api/groups/<id>/thumb` → JPEG bytes of the embedded preview of the group's primary file via
      `preview.extract_preview` (read-only), cached under `<PhotoVault home>/thumbs/<group_id>.jpg`; 404
      when the drive is not mounted or no preview exists. Find the file as `<mount>/<rel_path>` using the
      volume's `last_mount_path` only if that path is currently a mounted dir with a matching marker/UUID
      probe (else 404).
    - `GET /api/settings`, `POST /api/settings` (keys: creator, copyright, default_offset, match_window_s;
      others → 400).
  - Tests: start the server on port 0 in a thread against a tmp catalog (seed volumes/groups/group_meta
    directly through repo), hit endpoints with `urllib.request`; check the Host guard, the whitelist (400),
    meta update round-trip, drives listing with a `tmp_path` root standing in for `/Volumes` (make the root
    injectable), and a task run of `catalog init` style harmless command via a fake argv (inject the runner's
    base argv so tests can use `[sys.executable, "-c", "print('{\"event\":\"result\"}')"]`).
  - Verify: `uv run pytest tests/test_web_api.py -q`, then `uv run pytest -q`.

- [x] 5. Front end (visual check pending, user)
  - ID: ui-5 (after ui-4)
  - Files: `photovault/web/static/index.html`, `app.js`, `app.css`. Plain JS (ES modules ok), no build step,
    no framework. Leaflet 1.9.4 CSS/JS from `https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/`.
    OpenStreetMap tiles `https://tile.openstreetmap.org/{z}/{x}/{y}.png` with attribution. Place search:
    `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=` on Enter/button only (never per
    keystroke), at most one request per second.
  - Layout: left sidebar "Drives" (from `/api/drives`, polled every 5 s): mounted ones first, each with role
    badge (CARD/WORKING/LEGACY/MASTER/unassigned), label, free space, and unplugged remembered drives greyed
    with "last seen". Per drive actions by role: unassigned → Assign (role + label form); CARD → "Card
    status", "Import…" (pick a WORKING target from mounted drives, shoot name, Dry run / Import); WORKING
    → "Scan metadata", "Write sidecars"; LEGACY → "Index", "Hash", "Scan metadata". A "Tasks" panel shows
    running/finished tasks with the latest progress event and a Pause button; poll `/api/tasks/<id>?since=`.
    Main area "Metadata": filters (drive, shoot, missing: any/gps/creator/keywords), counts from the API,
    a grid of thumbnails (`/api/groups/<id>/thumb`, lazy-loaded, placeholder on 404) with badges for missing
    fields; click / shift-click / "Select all on page" selection. Editor panel for the selection:
    - Location: suggestions for the first selected frame (source, Δ time, confidence) with "Use for
      selection"; a Leaflet map (click to drop a pin) and a place search box whose results fly the map
      there and fill `place_name`; Save sets lat/lon, place_name and gps_source (`match`, `map` or
      `search`).
    - Creator / copyright (prefilled from settings), title, keywords (chips; add/remove apply to the whole
      selection), clock shift (± h/m/s with a preview of the corrected time of the first frame).
    - "Save" POSTs `/api/groups/meta`; then refresh the grid. A small "Settings" dialog edits creator,
      copyright, default offset and match window. Also an "Add GPS reference folder" action (folder path text
      field + label → `meta_refs`).
  - Light/dark via `prefers-color-scheme`; works at 1280 px wide; no external fonts.
  - Verify: `uv run pytest -q` still green, and `node --check photovault/web/static/app.js` if node exists.
    The user does the visual check.

- [x] 7. Drive summary (visual check pending, user)
  - ID: ui-7 (after ui-6)
  - Clicking a registered drive replaces the photo grid with its files by state, its index and hash runs with
    their final numbers, and Show in Finder (`open`, macOS only, inside the drive's mount). `index` and `hash`
    keep their numbers in `jobs.params_json` under `result` (no schema change); older runs have none.
    `GET /api/volumes/<id>`, `GET /api/volumes/<id>/files?state=&limit=&offset=`, `POST /api/volumes/<id>/reveal`.
  - Verify: `uv run pytest tests/test_web_api.py tests/test_baseline_hash.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_web_api.py -q` → 50 passed (with ui-8..ui-10).

- [x] 8. Copies across drives (visual check pending, user)
  - ID: ui-8 (after ui-7)
  - The drive summary counts hashed files also on other drives, only here, or twice here, per other drive; the
    file list has a Copies column and filter (`copies=`, `other=`), and each file expands to every location
    (`GET /api/assets/<id>/copies`) with Show in Finder. Full hash only; CARD copies never count. Read-only.
  - Verify: `uv run pytest tests/test_web_api.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_web_api.py -q` → 50 passed (with ui-7, ui-9, ui-10).

- [x] 9. Ignore drives (visual check pending, user)
  - ID: ui-9
  - Any sidebar drive (or the setup dialog) has Ignore; ignored drives fold under "Ignored drives (N)". Stored
    as the `ignored_drives` setting (JSON keys: volume_id, else `mount:<name>`); `POST /api/drives/ignore`.
    Display only: the engine ignores it.
  - Verify: `uv run pytest tests/test_web_api.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_web_api.py -q` → 50 passed (with ui-7, ui-8, ui-10).

- [x] 10. One filter bar for drives and cards (visual check pending, user)
  - ID: ui-10 (after ui-8, card-3)
  - The drive and card file lists share one bar: Show (file state on a drive, tier on a card), Copies (on other
    drives, only here, duplicated here, also on a named drive) and Camera, plus "x / N files" (filtered total
    over the whole drive or card). Both endpoints return `total`; drive files gain `camera=`, card files gain
    `copies=` and `other=`. Other drives are always named (copy pills, card reasons and headlines), never only
    counted. Camera on a drive file comes from its asset's capture group; no schema change.
  - Verify: `uv run pytest tests/test_web_api.py -q`
  - Result (2026-11-17): `uv run pytest tests/test_web_api.py -q` → 50 passed; full suite 442 passed, 4 skipped.
