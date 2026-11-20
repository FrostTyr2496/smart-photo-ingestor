"""Catalog schema migrations. Each entry in MIGRATIONS is the SQL for one version (index + 1).

V1 is system-design.md section 5, verbatim. Changing the schema means appending a new migration
(with user approval, per vault-rules 6), never editing an applied one.
"""

V1 = """
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
CREATE TABLE sidecars (                                                         -- mutable XMP, versioned
  sidecar_id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES capture_groups,
  volume_id TEXT NOT NULL REFERENCES volumes, rel_path TEXT NOT NULL, sha256 TEXT NOT NULL,
  mtime_ns INTEGER NOT NULL, seen_at TEXT NOT NULL, UNIQUE (volume_id, rel_path, sha256)
);
CREATE TABLE jobs (
  job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('card_ingest','index_volume','baseline_hash','replicate','fixity_audit')),
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
"""

V2 = """
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
"""

V3 = """
CREATE TABLE volume_usage (                          -- files the catalog does not track one by one (idx-13, D-063)
  volume_id TEXT NOT NULL REFERENCES volumes, category TEXT NOT NULL,
  files INTEGER NOT NULL, bytes INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (volume_id, category)
);
"""

V4 = """
CREATE INDEX assets_group ON assets (group_id, group_role);   -- a group's files, for the Photos view (rv-7, D-064)
"""

V5 = """
ALTER TABLE geo_points ADD COLUMN place_name TEXT;   -- phone fixes carry Photos' place names (sp-2, D-066)
ALTER TABLE geo_points ADD COLUMN city TEXT;
ALTER TABLE geo_points ADD COLUMN state TEXT;
ALTER TABLE geo_points ADD COLUMN country TEXT;
ALTER TABLE geo_points ADD COLUMN accuracy_m REAL;
ALTER TABLE group_meta ADD COLUMN city TEXT;         -- written to XMP as photoshop:City / State / Country
ALTER TABLE group_meta ADD COLUMN state TEXT;
ALTER TABLE group_meta ADD COLUMN country TEXT;
"""

V6 = """
ALTER TABLE geo_points ADD COLUMN local_offset TEXT;   -- the fix's own UTC offset, +HH:MM (sp-2, D-070)
ALTER TABLE geo_points ADD COLUMN kind TEXT;           -- 'photo' | 'video'
"""

V7 = """
CREATE TABLE trips (                                   -- a run of shoots (sh-0, D-080)
  trip_id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  created_at TEXT NOT NULL
);
ALTER TABLE shoots ADD COLUMN trip_id INTEGER REFERENCES trips;
ALTER TABLE shoots ADD COLUMN local_offset TEXT;       -- the place's offset, '+HH:MM'; the shoot's wall clock
ALTER TABLE shoots ADD COLUMN name_source TEXT;        -- folder | phone | user | fallback
CREATE TABLE shoot_plan_decisions (                    -- the user's review decisions, kept across re-plans
  decision_id INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('accept','split','merge','skip','rename','reject_name','move')),
  payload_json TEXT NOT NULL, decided_at TEXT NOT NULL
);
CREATE INDEX shoot_plan_decisions_proposal ON shoot_plan_decisions (proposal_id);
CREATE TABLE shoot_apply_log (                         -- what "Undo apply" restores
  job_id TEXT NOT NULL, group_id INTEGER NOT NULL, prev_shoot_id INTEGER, PRIMARY KEY (job_id, group_id)
);
CREATE TABLE body_clock (                              -- per-body clock corrections for a time range (the instant)
  clock_id INTEGER PRIMARY KEY, body_id INTEGER NOT NULL REFERENCES bodies,
  start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, correction_s INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('phone','gps','user')), accepted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX body_clock_body ON body_clock (body_id, start_utc);
ALTER TABLE capture_groups ADD COLUMN subsec TEXT;     -- EXIF SubSecTimeOriginal, so vault names match ingest
"""

# The `jobs.kind` CHECK cannot be altered in place, so the table is rebuilt the SQLite way (create new, copy, drop,
# rename). `db.migrate` runs migrations with foreign keys off and checks them before COMMIT, so `job_items` keeps
# pointing at `jobs` and no row is lost.
V8 = """
CREATE TABLE jobs_new (
  job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('card_ingest','index_volume','baseline_hash','replicate','fixity_audit','card_clear')),
  source_volume_id TEXT REFERENCES volumes, target_volume_id TEXT REFERENCES volumes,
  state TEXT NOT NULL CHECK (state IN ('planned','running','paused','done','failed')),
  params_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, finished_at TEXT
);
INSERT INTO jobs_new (job_id, kind, source_volume_id, target_volume_id, state, params_json, created_at, finished_at)
  SELECT job_id, kind, source_volume_id, target_volume_id, state, params_json, created_at, finished_at FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
"""

# V9 (sp-9, D-127, review sh-8 section 8): shoot place/subject columns, wider decision kinds, Apple's on-device
# analysis. The `shoot_plan_decisions.kind` CHECK is widened by a table rebuild, as V8 did for `jobs`.
V9 = """
ALTER TABLE shoots ADD COLUMN place TEXT;
ALTER TABLE shoots ADD COLUMN subject TEXT;
ALTER TABLE shoots ADD COLUMN subject_source TEXT CHECK (subject_source IS NULL OR subject_source IN ('user','phone','folder','model'));
ALTER TABLE shoots ADD COLUMN confirmed_at TEXT;       -- NULL = applied but not confirmed through a stack
CREATE TABLE shoot_plan_decisions_new (
  decision_id INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('accept','split','merge','skip','rename','reject_name','move','include','exclude','detail')),
  payload_json TEXT NOT NULL, decided_at TEXT NOT NULL
);
INSERT INTO shoot_plan_decisions_new (decision_id, proposal_id, kind, payload_json, decided_at)
  SELECT decision_id, proposal_id, kind, payload_json, decided_at FROM shoot_plan_decisions;
DROP TABLE shoot_plan_decisions;
ALTER TABLE shoot_plan_decisions_new RENAME TO shoot_plan_decisions;
CREATE INDEX shoot_plan_decisions_proposal ON shoot_plan_decisions (proposal_id);
CREATE TABLE phone_analysis (                          -- Apple's on-device analysis, text only (people stay local)
  point_id INTEGER PRIMARY KEY REFERENCES geo_points, caption TEXT, labels_json TEXT, persons_json TEXT,
  hints_json TEXT                                      -- activities, venues, venue_types, landmarks, bodies_of_water,
                                                       -- areas_of_interest, holidays, events, albums
);
"""

MIGRATIONS: list[str] = [V1, V2, V3, V4, V5, V6, V7, V8, V9]
