"""Catalog repository: volumes, bodies, assets, instances and jobs (system-design sections 3 and 5).

No function here commits. Callers wrap each unit of work in ``with transaction(conn):``. The connection from
``catalog.db.connect`` is in autocommit mode (``isolation_level=None``), so a bare ``with conn:`` does NOT open
a transaction and every write would commit on its own.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

Row = sqlite3.Row


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One unit of work: ``BEGIN IMMEDIATE``, then COMMIT on success or ROLLBACK if the body raises."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def utcnow() -> str:
    """Current UTC time as ISO-8601 with a ``Z`` suffix, second precision."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_key(rel_path: str) -> str:
    """Case-insensitive, normalisation-insensitive key for a relative path (NFC + casefold)."""
    return unicodedata.normalize("NFC", rel_path).casefold()


# --- volumes and bodies -------------------------------------------------------------------------------------


def upsert_volume(
    conn: sqlite3.Connection,
    *,
    volume_id: str,
    role: str,
    label: str,
    fs_type: str | None,
    da_volume_uuid: str | None,
    marker_id: str | None,
    capacity_bytes: int | None,
    mount_path: str | Path | None,
) -> None:
    """Insert a volume, or update every field except ``created_at`` when ``volume_id`` exists.

    ``last_seen_at`` is set to now and ``last_mount_path`` to ``mount_path``.
    """
    now = utcnow()
    mount = None if mount_path is None else str(mount_path)
    conn.execute(
        """
        INSERT INTO volumes (volume_id, role, label, fs_type, da_volume_uuid, marker_id, capacity_bytes,
                             last_mount_path, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (volume_id) DO UPDATE SET
            role=excluded.role, label=excluded.label, fs_type=excluded.fs_type,
            da_volume_uuid=excluded.da_volume_uuid, marker_id=excluded.marker_id,
            capacity_bytes=excluded.capacity_bytes, last_mount_path=excluded.last_mount_path,
            last_seen_at=excluded.last_seen_at
        """,
        (volume_id, role, label, fs_type, da_volume_uuid, marker_id, capacity_bytes, mount, now, now),
    )


def find_volume(
    conn: sqlite3.Connection, *, marker_id: str | None = None, da_volume_uuid: str | None = None
) -> Row | None:
    """Find a volume by marker id first, then by DiskArbitration UUID. Returns None if neither matches."""
    if marker_id is not None:
        row = conn.execute("SELECT * FROM volumes WHERE marker_id = ?", (marker_id,)).fetchone()
        if row is not None:
            return row
    if da_volume_uuid is not None:
        return conn.execute("SELECT * FROM volumes WHERE da_volume_uuid = ?", (da_volume_uuid,)).fetchone()
    return None


def get_or_create_body(conn: sqlite3.Connection, model: str, serial: str | None, nickname: str) -> int:
    """Return the ``body_id`` for ``(model, serial)``, creating it with ``nickname`` if new.

    A missing serial is stored as the empty string. An existing body keeps its nickname.
    """
    serial = serial or ""
    conn.execute(
        "INSERT OR IGNORE INTO bodies (model, serial, nickname) VALUES (?, ?, ?)", (model, serial, nickname)
    )
    row = conn.execute("SELECT body_id FROM bodies WHERE model = ? AND serial = ?", (model, serial)).fetchone()
    return int(row["body_id"])


# --- assets -------------------------------------------------------------------------------------------------


def find_asset(conn: sqlite3.Connection, sha256: str) -> Row | None:
    return conn.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()


def insert_asset(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    size: int,
    partial_sha256: str,
    kind: str,
    group_id: int | None = None,
    group_role: str | None = None,
) -> int:
    """Insert an asset and return its id; return the existing id when ``sha256`` is already present."""
    conn.execute(
        """
        INSERT OR IGNORE INTO assets (sha256, size, partial_sha256, kind, group_id, group_role, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (sha256, size, partial_sha256, kind, group_id, group_role, utcnow()),
    )
    return int(find_asset(conn, sha256)["asset_id"])


def find_candidates(conn: sqlite3.Connection, size: int, partial_sha256: str) -> list[Row]:
    """Assets that might be the same file. Candidates only: identity is the full SHA-256 (vault-rules 5)."""
    return conn.execute(
        "SELECT * FROM assets WHERE size = ? AND partial_sha256 = ? ORDER BY asset_id", (size, partial_sha256)
    ).fetchall()


# --- instances ----------------------------------------------------------------------------------------------


def upsert_instance(
    conn: sqlite3.Connection,
    *,
    volume_id: str,
    rel_path: str,
    size: int,
    mtime_ns: int,
    kind: str,
    state: str,
    asset_id: int | None = None,
    partial_sha256: str | None = None,
    verified_sha256: str | None = None,
) -> int:
    """Insert or update the instance keyed on ``(volume_id, path_key(rel_path))`` and return its id.

    ``verified_at`` is set to now when ``state`` is ``verified``. On update, ``asset_id``,
    ``partial_sha256`` and ``verified_sha256`` keep their old value when the new one is None, and
    ``verified_at`` and ``lr_referenced`` are left alone unless the state is ``verified``.
    """
    now = utcnow()
    verified_at = now if state == "verified" else None
    conn.execute(
        """
        INSERT INTO instances (asset_id, volume_id, rel_path, rel_path_key, size, mtime_ns, partial_sha256,
                               kind, state, verified_at, verified_sha256, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (volume_id, rel_path_key) DO UPDATE SET
            asset_id = COALESCE(excluded.asset_id, asset_id),
            rel_path = excluded.rel_path,
            size = excluded.size,
            mtime_ns = excluded.mtime_ns,
            partial_sha256 = COALESCE(excluded.partial_sha256, partial_sha256),
            kind = excluded.kind,
            state = excluded.state,
            verified_at = COALESCE(excluded.verified_at, verified_at),
            verified_sha256 = COALESCE(excluded.verified_sha256, verified_sha256),
            last_seen_at = excluded.last_seen_at
        """,
        (asset_id, volume_id, rel_path, path_key(rel_path), size, mtime_ns, partial_sha256, kind, state,
         verified_at, verified_sha256, now),
    )
    return int(get_instance(conn, volume_id, rel_path)["instance_id"])


def get_instance(conn: sqlite3.Connection, volume_id: str, rel_path: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM instances WHERE volume_id = ? AND rel_path_key = ?", (volume_id, path_key(rel_path))
    ).fetchone()


def verified_counts_by_role(conn: sqlite3.Connection, asset_id: int) -> dict[str, int]:
    """Count ``verified`` instances of an asset per volume role. CARD volumes are excluded (vault-rules 5)."""
    rows = conn.execute(
        """
        SELECT v.role AS role, COUNT(*) AS n
        FROM instances i JOIN volumes v ON v.volume_id = i.volume_id
        WHERE i.asset_id = ? AND i.state = 'verified' AND v.role != 'CARD'
        GROUP BY v.role
        """,
        (asset_id,),
    ).fetchall()
    return {r["role"]: int(r["n"]) for r in rows}


# --- jobs ---------------------------------------------------------------------------------------------------


def create_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    source_volume_id: str | None,
    target_volume_id: str | None,
    params: dict,
) -> int:
    """Create a job in state ``planned`` and return its id. ``params`` is stored as sorted JSON."""
    cur = conn.execute(
        """
        INSERT INTO jobs (kind, source_volume_id, target_volume_id, state, params_json, created_at)
        VALUES (?, ?, ?, 'planned', ?, ?)
        """,
        (kind, source_volume_id, target_volume_id, json.dumps(params, sort_keys=True), utcnow()),
    )
    return int(cur.lastrowid)


def add_job_items(conn: sqlite3.Connection, job_id: int, items: Iterable[dict]) -> None:
    """Add items in state ``planned``. Keys: ``seq``, ``src_rel``, ``dst_rel``, ``group_id``, ``role``."""
    now = utcnow()
    conn.executemany(
        """
        INSERT INTO job_items (job_id, seq, src_rel, dst_rel, group_id, role, state, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)
        """,
        [
            (job_id, it["seq"], it["src_rel"], it.get("dst_rel"), it.get("group_id"), it.get("role"), now)
            for it in items
        ],
    )


def set_item_state(
    conn: sqlite3.Connection,
    job_id: int,
    seq: int,
    state: str,
    *,
    asset_id: int | None = None,
    error: str | None = None,
) -> None:
    """Set an item's state. ``asset_id`` is kept when None; ``error`` is always overwritten."""
    cur = conn.execute(
        """
        UPDATE job_items SET state = ?, asset_id = COALESCE(?, asset_id), error = ?, updated_at = ?
        WHERE job_id = ? AND seq = ?
        """,
        (state, asset_id, error, utcnow(), job_id, seq),
    )
    if cur.rowcount != 1:
        raise KeyError(f"no job item (job_id={job_id}, seq={seq})")


def pending_items(conn: sqlite3.Connection, job_id: int) -> list[Row]:
    """Items still to copy (``planned`` or ``copying``), ordered by ``seq``."""
    return conn.execute(
        "SELECT * FROM job_items WHERE job_id = ? AND state IN ('planned', 'copying') ORDER BY seq", (job_id,)
    ).fetchall()


def set_job_state(conn: sqlite3.Connection, job_id: int, state: str) -> None:
    """Set a job's state; ``finished_at`` is set for ``done`` and ``failed``."""
    finished = utcnow() if state in ("done", "failed") else None
    cur = conn.execute(
        "UPDATE jobs SET state = ?, finished_at = COALESCE(?, finished_at) WHERE job_id = ?",
        (state, finished, job_id),
    )
    if cur.rowcount != 1:
        raise KeyError(f"no job {job_id}")


# --- shoots and capture groups (ing-6) ----------------------------------------------------------------------


def get_or_create_shoot(conn: sqlite3.Connection, *, name: str, shoot_date: str, folder_rel: str) -> int:
    """Return the ``shoot_id`` for ``(shoot_date, name)``, creating it if new."""
    conn.execute(
        "INSERT OR IGNORE INTO shoots (name, shoot_date, folder_rel, created_at) VALUES (?, ?, ?, ?)",
        (name, shoot_date, folder_rel, utcnow()),
    )
    row = conn.execute("SELECT shoot_id FROM shoots WHERE shoot_date = ? AND name = ?", (shoot_date, name)).fetchone()
    return int(row["shoot_id"])


def find_group(conn: sqlite3.Connection, body_id: int | None, shutter_count: int | None) -> Row | None:
    """The capture group for ``(body_id, shutter_count)``; None when either is unknown or there is no row."""
    if body_id is None or shutter_count is None:
        return None
    return conn.execute(
        "SELECT * FROM capture_groups WHERE body_id = ? AND shutter_count = ?", (body_id, shutter_count)
    ).fetchone()


def insert_group(
    conn: sqlite3.Connection,
    *,
    body_id: int | None,
    shutter_count: int | None,
    capture_time: str | None,
    capture_offset: str | None,
    time_source: str,
    canonical_stem: str,
    original_stem: str,
    original_num: int | None,
    shoot_id: int | None,
) -> int:
    """Insert a capture group and return its id."""
    cur = conn.execute(
        """
        INSERT INTO capture_groups (body_id, shutter_count, capture_time, capture_offset, time_source,
                                    canonical_stem, original_stem, original_num, shoot_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body_id, shutter_count, capture_time, capture_offset, time_source, canonical_stem, original_stem,
         original_num, shoot_id, utcnow()),
    )
    return int(cur.lastrowid)


def get_job(conn: sqlite3.Connection, job_id: int) -> Row | None:
    return conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()


def list_jobs(conn: sqlite3.Connection, limit: int = 20) -> list[Row]:
    return conn.execute(
        """
        SELECT j.*, COUNT(i.seq) AS items,
               SUM(i.state = 'committed') AS committed, SUM(i.state = 'skipped_dup') AS skipped_dup,
               SUM(i.state = 'failed') AS failed, SUM(i.state IN ('planned', 'copying')) AS pending
        FROM jobs j LEFT JOIN job_items i ON i.job_id = j.job_id
        GROUP BY j.job_id ORDER BY j.job_id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def list_volumes(conn: sqlite3.Connection) -> list[Row]:
    return conn.execute("SELECT * FROM volumes ORDER BY role, label").fetchall()


def volume_instances(conn: sqlite3.Connection, volume_id: str) -> dict[str, Row]:
    """Every instance on a volume, keyed by ``rel_path_key``."""
    rows = conn.execute("SELECT * FROM instances WHERE volume_id = ?", (volume_id,)).fetchall()
    return {r["rel_path_key"]: r for r in rows}


def clear_instance_identity(conn: sqlite3.Connection, instance_id: int) -> None:
    """Forget what an instance's bytes were (they changed): unlink the asset and drop the verification."""
    conn.execute(
        "UPDATE instances SET asset_id = NULL, verified_sha256 = NULL, verified_at = NULL WHERE instance_id = ?",
        (instance_id,),
    )


def mark_missing(conn: sqlite3.Connection, instance_ids: Iterable[int]) -> int:
    """Set these instances to ``missing`` (the file was not found); returns how many changed."""
    cur = conn.executemany(
        "UPDATE instances SET state = 'missing' WHERE instance_id = ? AND state != 'missing'",
        [(i,) for i in instance_ids],
    )
    return cur.rowcount


def latest_job(conn: sqlite3.Connection, *, kind: str, source_volume_id: str, state: str) -> Row | None:
    """The newest job of ``kind`` on a source volume in ``state`` (used to resume a paused index or hash)."""
    return conn.execute(
        "SELECT * FROM jobs WHERE kind = ? AND source_volume_id = ? AND state = ? ORDER BY job_id DESC LIMIT 1",
        (kind, source_volume_id, state),
    ).fetchone()


# --- group metadata, geo points and settings (ui-1, migration 2) -----------------------------------------------

_SCALAR_META_KEYS = frozenset(
    {"lat", "lon", "alt", "gps_source", "place_name", "time_shift_s", "creator", "copyright", "title"}
)
_KEYWORD_OPS = frozenset({"keywords_add", "keywords_remove", "keywords_set"})
_MISSING_KINDS = frozenset({"gps", "creator", "keywords", "any"})
_GROUP_META_COLUMNS = (
    "cam_lat, cam_lon, cam_alt, lat, lon, alt, gps_source, place_name, time_shift_s, creator, copyright, "
    "title, keywords_json, dirty, updated_at"
)


def ensure_group_meta(conn: sqlite3.Connection, group_id: int) -> None:
    """Insert an empty ``group_meta`` row for ``group_id`` if there is none."""
    conn.execute("INSERT OR IGNORE INTO group_meta (group_id, updated_at) VALUES (?, ?)", (group_id, utcnow()))


def get_group_meta(conn: sqlite3.Connection, group_id: int) -> Row | None:
    return conn.execute("SELECT * FROM group_meta WHERE group_id = ?", (group_id,)).fetchone()


def set_camera_gps(
    conn: sqlite3.Connection, group_id: int, *, lat: float | None, lon: float | None, alt: float | None
) -> None:
    """Record the camera's GPS in ``cam_*``. Fill the effective geotag only when it is still empty.

    When the row's ``lat`` is NULL and the camera gave a fix, ``lat/lon/alt`` take the camera values and
    ``gps_source`` becomes ``'camera'``. An existing effective geotag is never overwritten. ``dirty`` is untouched.
    """
    ensure_group_meta(conn, group_id)
    conn.execute(
        """
        UPDATE group_meta SET
            cam_lat = ?, cam_lon = ?, cam_alt = ?,
            lat = CASE WHEN lat IS NULL AND ? IS NOT NULL THEN ? ELSE lat END,
            lon = CASE WHEN lat IS NULL AND ? IS NOT NULL THEN ? ELSE lon END,
            alt = CASE WHEN lat IS NULL AND ? IS NOT NULL THEN ? ELSE alt END,
            gps_source = CASE WHEN lat IS NULL AND ? IS NOT NULL THEN 'camera' ELSE gps_source END,
            updated_at = ?
        WHERE group_id = ?
        """,
        (lat, lon, alt, lat, lat, lon, lon, alt, alt, lat, utcnow(), group_id),
    )


def _normalize_keywords(words: Iterable[str]) -> list[str]:
    """Strip, drop empties, de-duplicate case-insensitively (first spelling wins), sort."""
    first: dict[str, str] = {}
    for word in words:
        text = str(word).strip()
        if text and text.casefold() not in first:
            first[text.casefold()] = text
    return sorted(first.values(), key=lambda w: (w.casefold(), w))


def update_group_meta(conn: sqlite3.Connection, group_ids: list[int], *, fields: dict) -> int:
    """Apply user edits to each group's ``group_meta`` row and mark it ``dirty``.

    Scalar keys overwrite. Keywords apply in order: ``keywords_set`` replaces the list, then
    ``keywords_add`` appends, then ``keywords_remove`` drops (matched case-insensitively). Unknown keys raise
    ``ValueError`` before anything is written. Returns the number of distinct groups updated. Runs no transaction;
    callers wrap it in ``transaction``.
    """
    unknown = set(fields) - _SCALAR_META_KEYS - _KEYWORD_OPS
    if unknown:
        raise ValueError(f"unknown group_meta field(s): {', '.join(sorted(unknown))}")
    if not fields:
        return 0
    scalars = {k: v for k, v in fields.items() if k in _SCALAR_META_KEYS}
    keyword_ops = {k: v for k, v in fields.items() if k in _KEYWORD_OPS}
    targets = list(dict.fromkeys(int(g) for g in group_ids))
    now = utcnow()
    for group_id in targets:
        ensure_group_meta(conn, group_id)
        assignments = [f"{col} = ?" for col in scalars]
        params: list = list(scalars.values())
        if keyword_ops:
            row = get_group_meta(conn, group_id)
            current = json.loads(row["keywords_json"] or "[]")
            if "keywords_set" in keyword_ops:
                current = list(keyword_ops["keywords_set"] or [])
            if "keywords_add" in keyword_ops:
                current = current + list(keyword_ops["keywords_add"] or [])
            if "keywords_remove" in keyword_ops:
                drop = {str(w).strip().casefold() for w in keyword_ops["keywords_remove"] or []}
                current = [w for w in current if str(w).strip().casefold() not in drop]
            assignments.append("keywords_json = ?")
            params.append(json.dumps(_normalize_keywords(current)))
        assignments += ["dirty = 1", "updated_at = ?"]
        params += [now, group_id]
        conn.execute(f"UPDATE group_meta SET {', '.join(assignments)} WHERE group_id = ?", params)
    return len(targets)


def upsert_geo_point(
    conn: sqlite3.Connection,
    *,
    utc_time: str,
    lat: float,
    lon: float,
    alt: float | None,
    source: str,
    source_ref: str,
) -> None:
    """Insert a GPS fix, or update the one with the same ``(source, source_ref)``."""
    conn.execute(
        """
        INSERT INTO geo_points (utc_time, lat, lon, alt, source, source_ref)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, source_ref) DO UPDATE SET
            utc_time = excluded.utc_time, lat = excluded.lat, lon = excluded.lon, alt = excluded.alt
        """,
        (utc_time, lat, lon, alt, source, source_ref),
    )


def geo_points_between(conn: sqlite3.Connection, start_utc: str, end_utc: str) -> list[Row]:
    """GPS fixes with ``start_utc <= utc_time <= end_utc`` (ISO-8601 UTC strings), ordered by time."""
    return conn.execute(
        "SELECT * FROM geo_points WHERE utc_time >= ? AND utc_time <= ? ORDER BY utc_time, point_id",
        (start_utc, end_utc),
    ).fetchall()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _group_filter_sql(
    *, volume_id: str | None, shoot_id: int | None, missing: str | None
) -> tuple[str, list]:
    """WHERE clause (without the keyword) for group listings and counts. ``g`` is capture_groups, ``m`` group_meta."""
    clauses: list[str] = []
    params: list = []
    if volume_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM assets a JOIN instances i ON i.asset_id = a.asset_id "
            "WHERE a.group_id = g.group_id AND i.volume_id = ?)"
        )
        params.append(volume_id)
    if shoot_id is not None:
        clauses.append("g.shoot_id = ?")
        params.append(shoot_id)
    gps = "m.lat IS NULL"
    creator = "(m.creator IS NULL OR m.creator = '')"
    keywords = "(m.keywords_json IS NULL OR m.keywords_json = '[]')"
    if missing is not None:
        if missing not in _MISSING_KINDS:
            raise ValueError(f"missing must be one of {sorted(_MISSING_KINDS)}, not {missing!r}")
        if missing == "gps":
            clauses.append(gps)
        elif missing == "creator":
            clauses.append(creator)
        elif missing == "keywords":
            clauses.append(keywords)
        else:
            clauses.append(f"({gps} OR {creator} OR {keywords})")
    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def list_groups(
    conn: sqlite3.Connection,
    *,
    volume_id: str | None = None,
    shoot_id: int | None = None,
    missing: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Row]:
    """One row per capture group, with its shoot, body, ``group_meta`` and one primary file location.

    The location (``rel_path``, ``volume_id``) is the ``group_role='primary'`` asset's instance on ``volume_id``
    when given, else on a WORKING volume, else any; NULL when the primary has no indexed instance yet. ``missing``
    is one of ``gps``, ``creator``, ``keywords``, ``any`` (see ``_group_filter_sql``).
    """
    where, params = _group_filter_sql(volume_id=volume_id, shoot_id=shoot_id, missing=missing)
    if volume_id is not None:
        preference, pref_params = "(i.volume_id = ?) DESC, (v.role = 'WORKING') DESC, i.instance_id", [volume_id]
    else:
        preference, pref_params = "(v.role = 'WORKING') DESC, i.instance_id", []
    return conn.execute(
        f"""
        SELECT g.group_id, g.canonical_stem, g.capture_time, g.capture_offset, g.shoot_id,
               b.nickname AS body_nickname, s.name AS shoot_name, s.shoot_date AS shoot_date,
               {_GROUP_META_COLUMNS},
               loc.rel_path AS rel_path, loc.volume_id AS volume_id
        FROM capture_groups g
        LEFT JOIN bodies b ON b.body_id = g.body_id
        LEFT JOIN shoots s ON s.shoot_id = g.shoot_id
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        LEFT JOIN instances loc ON loc.instance_id = (
            SELECT i.instance_id FROM assets a
            JOIN instances i ON i.asset_id = a.asset_id
            JOIN volumes v ON v.volume_id = i.volume_id
            WHERE a.group_id = g.group_id AND a.group_role = 'primary' AND i.state IN ('indexed', 'verified')
            ORDER BY {preference}
            LIMIT 1
        )
        WHERE {where}
        ORDER BY g.capture_time, g.group_id
        LIMIT ? OFFSET ?
        """,
        [*pref_params, *params, limit, offset],
    ).fetchall()


def missing_counts(conn: sqlite3.Connection, *, volume_id: str | None = None) -> dict:
    """``{"groups", "gps", "creator", "keywords"}``: groups in scope, and how many lack each field."""
    where, params = _group_filter_sql(volume_id=volume_id, shoot_id=None, missing=None)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS groups,
               SUM(m.lat IS NULL) AS gps,
               SUM(m.creator IS NULL OR m.creator = '') AS creator,
               SUM(m.keywords_json IS NULL OR m.keywords_json = '[]') AS keywords
        FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    return {k: int(row[k] or 0) for k in ("groups", "gps", "creator", "keywords")}


def list_shoots(conn: sqlite3.Connection) -> list[Row]:
    """Every shoot with its group count, newest date first."""
    return conn.execute(
        """
        SELECT s.shoot_id, s.name, s.shoot_date, COUNT(g.group_id) AS groups
        FROM shoots s LEFT JOIN capture_groups g ON g.shoot_id = s.shoot_id
        GROUP BY s.shoot_id ORDER BY s.shoot_date DESC, s.name
        """
    ).fetchall()
