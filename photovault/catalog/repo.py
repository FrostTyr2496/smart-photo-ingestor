"""Catalog repository: volumes, bodies, assets, instances and jobs (system-design sections 3 and 5).

No function here commits. Callers wrap each unit of work in ``with transaction(conn):``. The connection from
``catalog.db.connect`` is in autocommit mode (``isolation_level=None``), so a bare ``with conn:`` does NOT open
a transaction and every write would commit on its own.
"""

from __future__ import annotations

import json
import re
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


def verified_counts_by_role(conn: sqlite3.Connection, asset_id: int,
                            ignore_volumes: frozenset[str] | set[str] = frozenset()) -> dict[str, int]:
    """Count ``verified`` instances of an asset per volume role. CARD volumes are excluded (vault-rules 5).
    `ignore_volumes` leaves those volumes out (test card mode, D-111)."""
    skip = f" AND i.volume_id NOT IN ({','.join('?' * len(ignore_volumes))})" if ignore_volumes else ""
    rows = conn.execute(
        f"""
        SELECT v.role AS role, COUNT(*) AS n
        FROM instances i JOIN volumes v ON v.volume_id = i.volume_id
        WHERE i.asset_id = ? AND i.state = 'verified' AND v.role != 'CARD'{skip}
        GROUP BY v.role
        """,
        (asset_id, *sorted(ignore_volumes)),
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


def promote_group(conn: sqlite3.Connection, group_id: int, *, canonical_stem: str, shoot_id: int) -> None:
    """Give a group made by ``index`` (no shoot yet) its canonical stem and shoot at its first card ingest (ing-12).

    A group that already has a shoot is left alone."""
    conn.execute("UPDATE capture_groups SET canonical_stem = ?, shoot_id = ? WHERE group_id = ? AND shoot_id IS NULL",
                 (canonical_stem, shoot_id, group_id))


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
    subsec: str | None = None,
) -> int:
    """Insert a capture group and return its id."""
    cur = conn.execute(
        """
        INSERT INTO capture_groups (body_id, shutter_count, capture_time, capture_offset, time_source,
                                    canonical_stem, original_stem, original_num, shoot_id, created_at, subsec)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body_id, shutter_count, capture_time, capture_offset, time_source, canonical_stem, original_stem,
         original_num, shoot_id, utcnow(), subsec),
    )
    return int(cur.lastrowid)


def set_subsec(conn: sqlite3.Connection, group_id: int, subsec: str | None) -> None:
    """Store a group's EXIF SubSecTimeOriginal (sh-0b); a missing value leaves what is there."""
    if subsec:
        conn.execute("UPDATE capture_groups SET subsec = ? WHERE group_id = ?", (str(subsec), group_id))


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


def set_job_result(conn: sqlite3.Connection, job_id: int, result: dict) -> None:
    """Keep a run's final numbers in the job's ``params_json`` under ``result`` (no schema change).

    A resumed job keeps one row, so its ``result`` is the newest run's.
    """
    row = conn.execute("SELECT params_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return
    params = json.loads(row["params_json"] or "{}")
    params["result"] = result
    conn.execute("UPDATE jobs SET params_json = ? WHERE job_id = ?", (json.dumps(params, sort_keys=True), job_id))


def list_volumes(conn: sqlite3.Connection) -> list[Row]:
    return conn.execute("SELECT * FROM volumes ORDER BY role, label").fetchall()


def get_volume(conn: sqlite3.Connection, volume_id: str) -> Row | None:
    return conn.execute("SELECT * FROM volumes WHERE volume_id = ?", (volume_id,)).fetchone()


def set_volume_label(conn: sqlite3.Connection, volume_id: str, label: str) -> bool:
    """Rename a volume in the catalog only (its nickname). Nothing on the drive changes. False if it is unknown."""
    cur = conn.execute("UPDATE volumes SET label = ? WHERE volume_id = ?", (label, volume_id))
    return cur.rowcount > 0


def meta_scan_status(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Per volume: ``(groups, scanned)``, counting the same groups ``meta scan`` reads (their primary file is indexed
    or verified there). A group is scanned once it has a ``group_meta`` row."""
    rows = conn.execute(
        """
        SELECT i.volume_id, COUNT(DISTINCT g.group_id) AS groups, COUNT(DISTINCT m.group_id) AS scanned
        FROM instances i
        JOIN assets a ON a.asset_id = i.asset_id AND a.group_role = 'primary'
        JOIN capture_groups g ON g.group_id = a.group_id
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE i.state IN ('indexed', 'verified')
        GROUP BY i.volume_id
        """
    ).fetchall()
    return {r["volume_id"]: (r["groups"], r["scanned"]) for r in rows}


def volume_state_counts(conn: sqlite3.Connection, volume_id: str) -> list[Row]:
    """Per instance state: file count, bytes, and how many are linked to an asset (hashed)."""
    return conn.execute(
        """
        SELECT state, COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes, SUM(asset_id IS NOT NULL) AS hashed
        FROM instances WHERE volume_id = ? GROUP BY state
        """,
        (volume_id,),
    ).fetchall()


def volume_kind_counts(conn: sqlite3.Connection, volume_id: str) -> list[Row]:
    return conn.execute(
        "SELECT kind, COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes FROM instances "
        "WHERE volume_id = ? GROUP BY kind ORDER BY files DESC",
        (volume_id,),
    ).fetchall()


def replace_volume_usage(conn: sqlite3.Connection, volume_id: str, rows: dict[str, tuple[int, int]]) -> None:
    """Replace a volume's usage rows (idx-13) with `{category: (files, bytes)}` from one complete walk."""
    conn.execute("DELETE FROM volume_usage WHERE volume_id = ?", (volume_id,))
    now = utcnow()
    conn.executemany(
        "INSERT INTO volume_usage (volume_id, category, files, bytes, updated_at) VALUES (?, ?, ?, ?, ?)",
        [(volume_id, cat, files, size, now) for cat, (files, size) in sorted(rows.items())],
    )


def volume_usage(conn: sqlite3.Connection, volume_id: str) -> dict[str, tuple[int, int]]:
    rows = conn.execute("SELECT category, files, bytes FROM volume_usage WHERE volume_id = ?", (volume_id,))
    return {r["category"]: (r["files"], r["bytes"]) for r in rows}


def volume_runs(conn: sqlite3.Connection, volume_id: str, limit: int = 20) -> list[Row]:
    """Index and hash runs on a volume, newest first."""
    return conn.execute(
        """
        SELECT job_id, kind, state, params_json, created_at, finished_at FROM jobs
        WHERE source_volume_id = ? AND kind IN ('index_volume', 'baseline_hash')
        ORDER BY job_id DESC LIMIT ?
        """,
        (volume_id, limit),
    ).fetchall()


def volume_last_steps(conn: sqlite3.Connection, volume_id: str) -> dict[str, dict | None]:
    """When each workflow step last ran on a volume: ``{step: {"at", "state"} | None}``.

    Hash also has ``pending`` (indexed files with no full hash); a ``done`` hash with any is ``incomplete``.

    Index and hash come from their newest job. Scan metadata and sidecars have no job rows (no schema change), so
    they use the newest ``group_meta.updated_at`` (also moved by a metadata edit) and ``sidecars.seen_at``."""
    out: dict[str, dict | None] = {"index": None, "hash": None, "scan": None, "sidecars": None}
    for kind, step in (("index_volume", "index"), ("baseline_hash", "hash")):
        row = conn.execute(
            "SELECT state, COALESCE(finished_at, created_at) AS at FROM jobs "
            "WHERE source_volume_id = ? AND kind = ? ORDER BY job_id DESC LIMIT 1", (volume_id, kind)).fetchone()
        if row:
            out[step] = {"at": row["at"], "state": row["state"]}
    # A finished hash is only complete when no indexed file still waits for one (ui-13): an index after the hash,
    # or a hash that ran over an empty index, leaves files unhashed.
    if out["hash"] is not None:
        pending = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE volume_id = ? AND state = 'indexed' AND asset_id IS NULL",
            (volume_id,)).fetchone()[0]
        out["hash"]["pending"] = pending
        if pending and out["hash"]["state"] == "done":
            out["hash"]["state"] = "incomplete"
    scan = conn.execute(
        """
        SELECT MAX(m.updated_at) AS at FROM group_meta m
        JOIN assets a ON a.group_id = m.group_id AND a.group_role = 'primary'
        JOIN instances i ON i.asset_id = a.asset_id WHERE i.volume_id = ?
        """, (volume_id,)).fetchone()
    if scan and scan["at"]:
        out["scan"] = {"at": scan["at"], "state": "done"}
    side = conn.execute("SELECT MAX(seen_at) AS at FROM sidecars WHERE volume_id = ?", (volume_id,)).fetchone()
    if side and side["at"]:
        out["sidecars"] = {"at": side["at"], "state": "done"}
    return out


# Copies (ui-8). Identity is the full hash (vault-rules 5): only `verified` instances linked to an asset count, and
# CARD volumes never count as a copy (a card is formatted after import), the same rule as the duplicate report.
_COPIES_ELSEWHERE = """(SELECT COUNT(DISTINCT o.volume_id) FROM instances o JOIN volumes ov ON ov.volume_id = o.volume_id
    WHERE o.asset_id = i.asset_id AND o.state = 'verified' AND o.volume_id != i.volume_id AND ov.role != 'CARD')"""
_COPIES_HERE = """(SELECT COUNT(*) FROM instances d WHERE d.asset_id = i.asset_id AND d.volume_id = i.volume_id
    AND d.state = 'verified' AND d.instance_id != i.instance_id)"""
# Those other volumes as ``label<GS>role`` joined by ``<US>``, so the UI can name them.
_COPY_NAMES = """(SELECT GROUP_CONCAT(x.label || char(29) || x.role, char(31)) FROM (SELECT DISTINCT ov.label, ov.role
    FROM instances o
    JOIN volumes ov ON ov.volume_id = o.volume_id WHERE o.asset_id = i.asset_id AND o.state = 'verified'
    AND o.volume_id != i.volume_id AND ov.role != 'CARD' ORDER BY ov.label) x)"""
COPY_FILTERS = ("elsewhere", "only_here", "dup_here")


def volume_copy_summary(conn: sqlite3.Connection, volume_id: str) -> dict:
    """Where this volume's hashed files also exist: on other (non-CARD) volumes, only here, or twice here.

    File counts are instances on this volume; ``dup_here.extra_bytes`` is what the extra copies on this volume take.
    ``by_volume`` lists each other volume with how many of this volume's files it also holds.
    """
    r = conn.execute(
        f"""
        SELECT COUNT(*) AS hashed,
               COALESCE(SUM(e > 0), 0) AS elsewhere, COALESCE(SUM(CASE WHEN e > 0 THEN size END), 0) AS elsewhere_bytes,
               COALESCE(SUM(e = 0), 0) AS only_here, COALESCE(SUM(CASE WHEN e = 0 THEN size END), 0) AS only_here_bytes
        FROM (SELECT i.size, {_COPIES_ELSEWHERE} AS e FROM instances i
              WHERE i.volume_id = ? AND i.state = 'verified' AND i.asset_id IS NOT NULL)
        """,
        (volume_id,),
    ).fetchone()
    dup = conn.execute(
        """
        SELECT COUNT(*) AS assets, COALESCE(SUM(n), 0) AS files, COALESCE(SUM(size * (n - 1)), 0) AS extra_bytes
        FROM (SELECT COUNT(*) AS n, MAX(size) AS size FROM instances
              WHERE volume_id = ? AND state = 'verified' AND asset_id IS NOT NULL
              GROUP BY asset_id HAVING COUNT(*) >= 2)
        """,
        (volume_id,),
    ).fetchone()
    by_volume = conn.execute(
        """
        SELECT v.volume_id, v.label, v.role, COUNT(*) AS files, COALESCE(SUM(x.size), 0) AS bytes
        FROM (SELECT DISTINCT i.instance_id, i.size, o.volume_id AS vid FROM instances i
              JOIN instances o ON o.asset_id = i.asset_id AND o.state = 'verified' AND o.volume_id != i.volume_id
              WHERE i.volume_id = ? AND i.state = 'verified' AND i.asset_id IS NOT NULL) x
        JOIN volumes v ON v.volume_id = x.vid
        WHERE v.role != 'CARD'
        GROUP BY v.volume_id ORDER BY files DESC, v.label
        """,
        (volume_id,),
    ).fetchall()
    return {
        "hashed": r["hashed"],
        "elsewhere": {"files": r["elsewhere"], "bytes": r["elsewhere_bytes"]},
        "only_here": {"files": r["only_here"], "bytes": r["only_here_bytes"]},
        "dup_here": {"assets": dup["assets"], "files": dup["files"], "extra_bytes": dup["extra_bytes"]},
        "by_volume": [dict(v) for v in by_volume],
    }


UNKNOWN_CAMERA = "unknown"
# A file's camera: the body of its capture group, known once the file is hashed and its metadata read.
_FILE_BODY = """(SELECT g.body_id FROM assets a JOIN capture_groups g ON g.group_id = a.group_id
    WHERE a.asset_id = i.asset_id)"""


def _volume_file_filter(volume_id: str, *, state: str | None, copies: str | None, other_volume_id: str | None,
                        camera: str | None) -> tuple[str, list]:
    """The ``FROM … WHERE …`` shared by a page of a volume's files and their count."""
    sql = " FROM instances i WHERE i.volume_id = ?"
    args: list = [volume_id]
    if state is not None:
        sql += " AND i.state = ?"
        args.append(state)
    if copies is not None or other_volume_id is not None:
        sql += " AND i.state = 'verified' AND i.asset_id IS NOT NULL"
    if copies == "elsewhere":
        sql += f" AND {_COPIES_ELSEWHERE} > 0"
    elif copies == "only_here":
        sql += f" AND {_COPIES_ELSEWHERE} = 0"
    elif copies == "dup_here":
        sql += f" AND {_COPIES_HERE} > 0"
    elif copies is not None:
        raise ValueError(f"copies must be one of: {', '.join(COPY_FILTERS)}")
    if other_volume_id is not None:
        sql += (" AND EXISTS (SELECT 1 FROM instances o WHERE o.asset_id = i.asset_id AND o.volume_id = ? "
                "AND o.state = 'verified')")
        args.append(other_volume_id)
    if camera == UNKNOWN_CAMERA:
        sql += f" AND {_FILE_BODY} IS NULL"
    elif camera is not None:
        sql += f" AND {_FILE_BODY} = ?"
        args.append(int(camera))
    return sql, args


def volume_files(conn: sqlite3.Connection, volume_id: str, *, state: str | None, limit: int, offset: int,
                 copies: str | None = None, other_volume_id: str | None = None,
                 camera: str | None = None) -> list[Row]:
    """One page of a volume's files. Each row has ``copies`` (other non-CARD volumes with a verified copy),
    ``dup_here`` (other verified copies on this volume) and ``body_id`` (its camera, or None). ``copies`` filters
    by one of ``COPY_FILTERS``; ``other_volume_id`` keeps files with a verified copy on that volume (both imply
    ``verified``); ``camera`` is a body_id as text, or ``UNKNOWN_CAMERA``."""
    where, args = _volume_file_filter(volume_id, state=state, copies=copies, other_volume_id=other_volume_id,
                                      camera=camera)
    sql = ("SELECT i.rel_path, i.size, i.mtime_ns, i.kind, i.state, i.asset_id, i.asset_id IS NOT NULL AS hashed, "
           f"i.verified_sha256, {_COPIES_ELSEWHERE} AS copies, {_COPIES_HERE} AS dup_here, {_FILE_BODY} AS body_id, "
           f"{_COPY_NAMES} AS copy_names"
           + where + " ORDER BY i.rel_path LIMIT ? OFFSET ?")
    return conn.execute(sql, args + [limit, offset]).fetchall()


def volume_files_count(conn: sqlite3.Connection, volume_id: str, *, state: str | None = None,
                       copies: str | None = None, other_volume_id: str | None = None,
                       camera: str | None = None) -> int:
    """How many files `volume_files` would list across all pages, with the same filters."""
    where, args = _volume_file_filter(volume_id, state=state, copies=copies, other_volume_id=other_volume_id,
                                      camera=camera)
    return conn.execute("SELECT COUNT(*)" + where, args).fetchone()[0]


def volume_camera_counts(conn: sqlite3.Connection, volume_id: str) -> list[Row]:
    """Files per camera on a volume (``body_id`` None for files with no known camera), most files first."""
    return conn.execute(
        f"SELECT {_FILE_BODY} AS body_id, COUNT(*) AS files, COALESCE(SUM(i.size), 0) AS bytes "
        "FROM instances i WHERE i.volume_id = ? GROUP BY 1 ORDER BY files DESC",
        (volume_id,),
    ).fetchall()


def asset_locations(conn: sqlite3.Connection, asset_id: int) -> list[Row]:
    """Every verified copy of one asset, on any volume (CARD included, so the UI can label it), by volume and path."""
    return conn.execute(
        """
        SELECT i.volume_id, v.label, v.role, i.rel_path, i.size, i.state, i.lr_referenced, i.verified_at
        FROM instances i JOIN volumes v ON v.volume_id = i.volume_id
        WHERE i.asset_id = ? AND i.state = 'verified'
        ORDER BY v.role = 'CARD', v.label, i.rel_path
        """,
        (asset_id,),
    ).fetchall()


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
    {"lat", "lon", "alt", "gps_source", "place_name", "city", "state", "country", "time_shift_s", "creator",
     "copyright", "title"}
)
_KEYWORD_OPS = frozenset({"keywords_add", "keywords_remove", "keywords_set"})
_MISSING_KINDS = frozenset({"gps", "creator", "keywords", "any"})
_GROUP_META_COLUMNS = (
    "m.cam_lat, m.cam_lon, m.cam_alt, m.lat, m.lon, m.alt, m.gps_source, m.place_name, m.city, m.state, m.country, "
    "m.time_shift_s, m.creator, m.copyright, m.title, m.keywords_json, m.dirty, m.updated_at"
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
    place_name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    accuracy_m: float | None = None,
    local_offset: str | None = None,
    kind: str | None = None,
) -> str:
    """Insert a GPS fix, or update the one with the same ``(source, source_ref)``.

    Returns ``'inserted'``, ``'updated'`` (a stored value changed) or ``'unchanged'``.
    """
    values = (utc_time, lat, lon, alt, place_name, city, state, country, accuracy_m, local_offset, kind)
    cols = ["utc_time", "lat", "lon", "alt", "place_name", "city", "state", "country", "accuracy_m",
            "local_offset", "kind"]
    row = conn.execute(f"SELECT {', '.join(cols)} FROM geo_points WHERE source = ? AND source_ref = ?",
                       (source, source_ref)).fetchone()
    if row is None:
        conn.execute(
            f"INSERT INTO geo_points ({', '.join(cols)}, source, source_ref) VALUES ({', '.join('?' * 13)})",
            (*values, source, source_ref))
        return "inserted"
    if tuple(row) == values:
        return "unchanged"
    sets = ", ".join(f"{c} = ?" for c in cols)
    conn.execute(f"UPDATE geo_points SET {sets} WHERE source = ? AND source_ref = ?", (*values, source, source_ref))
    return "updated"


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


UNDATED = "undated"  # the ``day`` of groups with no capture time


_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _group_filter_sql(
    *, volume_id: str | None, shoot_id: int | None, missing: str | None, day: str | None = None,
    after: str | None = None, before: str | None = None, conn: sqlite3.Connection | None = None,
) -> tuple[str, list]:
    """WHERE clause (without the keyword) for group listings and counts. ``g`` is capture_groups, ``m`` group_meta.

    ``day`` is the wall date (``YYYY-MM-DD``): the camera's capture date, except for groups in a shoot with a
    ``local_offset`` (needs ``conn``), whose date is the instant in that offset (sh-0b); or ``UNDATED``. ``after`` and
    ``before`` (``YYYY-MM-DDTHH:MM:SS``, inclusive) bound the capture time; undated groups never match them.
    """
    clauses: list[str] = []
    params: list = []
    for value, op in ((after, ">="), (before, "<=")):
        if value is not None:
            if not _TIME_RE.fullmatch(value):
                raise ValueError(f"after/before must be YYYY-MM-DDTHH:MM:SS, not {value!r}")
            clauses.append(f"substr(g.capture_time, 1, 19) {op} ?")
            params.append(value)
    if day is not None:
        if day == UNDATED:
            clauses.append("g.capture_time IS NULL")
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            wall_ids = offset_shoot_wall_dates(conn) if conn is not None else {}
            if wall_ids:
                clauses.append(
                    "((substr(g.capture_time, 1, 10) = ? AND (g.shoot_id IS NULL OR g.shoot_id NOT IN "
                    "(SELECT shoot_id FROM shoots WHERE local_offset IS NOT NULL))) "
                    "OR g.group_id IN (SELECT value FROM json_each(?)))")
                params += [day, json.dumps([gid for gid, d in wall_ids.items() if d == day])]
            else:
                clauses.append("substr(g.capture_time, 1, 10) = ?")
                params.append(day)
        else:
            raise ValueError(f"day must be YYYY-MM-DD or {UNDATED!r}, not {day!r}")
    if volume_id is not None:
        # A group is on the volume when a hashed file of it is, or when the volume's index recorded one (files
        # not hashed yet have no asset link). Uncorrelated IN lists, so each is built once per query.
        clauses.append(
            "(g.group_id IN (SELECT a.group_id FROM assets a JOIN instances i ON i.asset_id = a.asset_id "
            "WHERE i.volume_id = ?) OR g.group_id IN (SELECT ji.group_id FROM job_items ji "
            "JOIN jobs j ON j.job_id = ji.job_id WHERE j.kind = 'index_volume' AND j.source_volume_id = ?))"
        )
        params += [volume_id, volume_id]
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
    day: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Row]:
    """One row per capture group, with its shoot, body, ``group_meta`` and one primary file location.

    The location (``rel_path``, ``volume_id``) is the ``group_role='primary'`` asset's instance on ``volume_id``
    when given, else on a WORKING volume, else any; NULL when the primary has no indexed instance yet. ``missing``
    is one of ``gps``, ``creator``, ``keywords``, ``any`` (see ``_group_filter_sql``).
    """
    where, params = _group_filter_sql(volume_id=volume_id, shoot_id=shoot_id, missing=missing, day=day, after=after,
                                      before=before, conn=conn)
    if volume_id is not None:
        preference, pref_params = "(i.volume_id = ?) DESC, (v.role = 'WORKING') DESC, i.instance_id", [volume_id]
    else:
        preference, pref_params = "(v.role = 'WORKING') DESC, i.instance_id", []
    return conn.execute(
        f"""
        SELECT g.group_id, g.canonical_stem, g.capture_time, g.capture_offset, g.shoot_id,
               g.body_id AS body_id, g.sequence_id AS sequence_id,
               g.shutter_count, g.original_stem, g.original_num, g.subsec,
               s.local_offset AS shoot_local_offset,
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


def indexed_primary_files(conn: sqlite3.Connection, group_ids: Iterable[int]) -> dict[int, list[dict]]:
    """Where each group's primary file was indexed, for groups whose files are not hashed yet (no asset link).

    ``index`` records each member of a group as a job item (``src_rel``, ``group_id``, ``role``) of its
    ``index_volume`` job; the hash links the asset later. Returns ``{group_id: [{"volume_id", "rel_path"}, ...]}``
    for instances still present (not ``missing``), WORKING volumes first. One pass over ``job_items`` for all ids.
    """
    ids = sorted({int(g) for g in group_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT ji.group_id, j.source_volume_id AS volume_id, ji.src_rel AS rel_path, v.role
        FROM job_items ji
        JOIN jobs j ON j.job_id = ji.job_id AND j.kind = 'index_volume'
        JOIN volumes v ON v.volume_id = j.source_volume_id
        WHERE ji.group_id IN ({marks}) AND ji.role = 'primary'
        ORDER BY (v.role = 'WORKING') DESC, ji.job_id DESC
        """,
        ids,
    ).fetchall()
    out: dict[int, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["volume_id"], path_key(r["rel_path"]))
        if key in seen:
            continue
        seen.add(key)
        inst = conn.execute("SELECT state FROM instances WHERE volume_id = ? AND rel_path_key = ?", key).fetchone()
        if inst is None or inst["state"] == "missing":
            continue
        out.setdefault(int(r["group_id"]), []).append({"volume_id": r["volume_id"], "rel_path": r["rel_path"]})
    return out


def offset_shoot_wall_dates(conn: sqlite3.Connection) -> dict[int, str]:
    """``{group_id: wall date}`` for groups in a shoot that has a ``local_offset`` (sh-0b): the instant in that offset.
    Empty until a reviewed shoot sets one, so the common case costs one small query."""
    from photovault import timemodel

    rows = conn.execute(
        """
        SELECT g.group_id, g.body_id, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s,
               s.local_offset AS shoot_local_offset
        FROM capture_groups g JOIN shoots s ON s.shoot_id = g.shoot_id AND s.local_offset IS NOT NULL
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE g.capture_time IS NOT NULL
        """).fetchall()
    if not rows:
        return {}
    default = get_setting(conn, "default_offset", "+00:00")
    clocks = timemodel.BodyClocks.load(conn)
    out: dict[int, str] = {}
    for r in rows:
        info = timemodel.instant_info(r, default, clocks)
        wall = timemodel.wall_clock(r, info.when if info else None)
        if wall:
            out[int(r["group_id"])] = wall[:10]
    return out


def list_photo_days(conn: sqlite3.Connection, *, volume_id: str | None = None, missing: str | None = None,
                    shoot_id: int | None = None) -> list[dict]:
    """Days with photos, newest first: ``{"day", "groups", "bodies", "shoots"}``. ``day`` is the wall date
    ``YYYY-MM-DD`` (the camera's capture date, or the instant's date in the shoot's ``local_offset``, sh-0b), or
    ``UNDATED`` (listed last); ``bodies`` the camera nicknames that shot that day, sorted; ``shoots`` the shoots with
    frames that day, ``{"shoot_id", "name", "groups"}``, most frames first (sh-4)."""
    where, params = _group_filter_sql(volume_id=volume_id, shoot_id=shoot_id, missing=missing)
    wall_ids = offset_shoot_wall_dates(conn)
    rows = conn.execute(
        f"""
        SELECT g.group_id, substr(g.capture_time, 1, 10) AS day, b.nickname AS body, g.shoot_id,
               (SELECT name FROM shoots WHERE shoot_id = g.shoot_id) AS shoot_name
        FROM capture_groups g
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        LEFT JOIN bodies b ON b.body_id = g.body_id
        WHERE {where} AND g.shoot_id IN (SELECT shoot_id FROM shoots WHERE local_offset IS NOT NULL)
        """,
        params,
    ).fetchall() if wall_ids else []
    days: dict[str | None, dict] = {}

    def add(day: str | None, n: int, bodies: set[str]) -> None:
        d = days.setdefault(day, {"groups": 0, "bodies": set(), "shoots": {}})
        d["groups"] += n
        d["bodies"] |= bodies

    def add_shoot(day: str | None, sid: int | None, name: str | None, n: int) -> None:
        if sid is None or day not in days:
            return
        cur = days[day]["shoots"].setdefault(sid, {"shoot_id": sid, "name": name or f"shoot {sid}", "groups": 0})
        cur["groups"] += n

    for r in rows:
        day = wall_ids.get(int(r["group_id"]), r["day"])
        add(day, 1, {r["body"]} if r["body"] else set())
        add_shoot(day, r["shoot_id"], r["shoot_name"], 1)
    skip = " AND (g.shoot_id IS NULL OR g.shoot_id NOT IN (SELECT shoot_id FROM shoots WHERE local_offset IS NOT NULL))" if wall_ids else ""
    for r in conn.execute(
        f"""
        SELECT substr(g.capture_time, 1, 10) AS day, COUNT(*) AS groups,
               group_concat(DISTINCT b.nickname) AS bodies
        FROM capture_groups g
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        LEFT JOIN bodies b ON b.body_id = g.body_id
        WHERE {where}{skip}
        GROUP BY day
        """,
        params,
    ):
        add(r["day"], r["groups"], {b for b in (r["bodies"] or "").split(",") if b})
    for r in conn.execute(
        f"""
        SELECT substr(g.capture_time, 1, 10) AS day, g.shoot_id, s.name, COUNT(*) AS groups
        FROM capture_groups g
        JOIN shoots s ON s.shoot_id = g.shoot_id
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE {where}{skip}
        GROUP BY day, g.shoot_id
        """,
        params,
    ):
        add_shoot(r["day"], r["shoot_id"], r["name"], r["groups"])
    ordered = sorted((d for d in days if d), reverse=True) + ([None] if None in days else [])
    return [{"day": d or UNDATED, "groups": days[d]["groups"], "bodies": sorted(days[d]["bodies"]),
             "shoots": sorted(days[d]["shoots"].values(), key=lambda x: (-x["groups"], x["name"]))} for d in ordered]


def list_body_clock(conn: sqlite3.Connection) -> list[Row]:
    """Every ``body_clock`` row with its body's nickname, newest range first."""
    return conn.execute(
        "SELECT c.*, b.nickname AS body FROM body_clock c JOIN bodies b ON b.body_id = c.body_id "
        "ORDER BY c.start_utc DESC, c.clock_id DESC").fetchall()


def add_body_clock(conn: sqlite3.Connection, *, body_id: int, start_utc: str, end_utc: str, correction_s: int,
                   source: str, accepted: bool = True) -> int:
    """Insert a ``body_clock`` row and return its id."""
    cur = conn.execute(
        "INSERT INTO body_clock (body_id, start_utc, end_utc, correction_s, source, accepted, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body_id, start_utc, end_utc, correction_s, source, 1 if accepted else 0, utcnow()))
    return int(cur.lastrowid)


def delete_body_clock(conn: sqlite3.Connection, clock_id: int) -> bool:
    return conn.execute("DELETE FROM body_clock WHERE clock_id = ?", (clock_id,)).rowcount > 0


def list_shoots(conn: sqlite3.Connection) -> list[Row]:
    """Every shoot with its group count, newest date first."""
    return conn.execute(
        """
        SELECT s.shoot_id, s.name, s.shoot_date, COUNT(g.group_id) AS groups
        FROM shoots s LEFT JOIN capture_groups g ON g.shoot_id = s.shoot_id
        GROUP BY s.shoot_id ORDER BY s.shoot_date DESC, s.name
        """
    ).fetchall()
