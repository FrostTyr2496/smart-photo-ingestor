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

MIGRATIONS: list[str] = [V1]
