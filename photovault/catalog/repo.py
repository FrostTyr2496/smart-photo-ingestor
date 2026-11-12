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
