"""Metadata completion (ui-3): camera GPS and clock-matched geo points, GPS reference folders, XMP sidecars.

Reads are read-only on every volume. The only file this module writes is an XMP sidecar on a WORKING volume,
through `xmp.write_sidecar`, which never overwrites a file it did not write last (vault-rules 1, 3, 5).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from photovault import metadata, scan, xmp
from photovault.catalog import repo
from photovault.config import Config
from photovault.geo import group_utc, offset_tz, utc_text
from photovault.timemodel import BodyClocks
from photovault.index import META_KINDS
from photovault.protocol import Emitter

BATCH = 200  # files per read_metadata call; bounds memory and progress granularity


def _chunks(items: list, size: int = BATCH):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _gps(meta: dict) -> tuple[float, float, float | None] | None:
    lat, lon = meta.get("GPSLatitude"), meta.get("GPSLongitude")
    if lat is None or lon is None:
        return None
    return lat, lon, meta.get("GPSAltitude")


def scan_volume(conn: sqlite3.Connection, cfg: Config, mount: Path, volume_id: str, emit: Emitter, *,
                rescan: bool = False) -> dict:
    """Read the primary file of each group on this volume and store its camera GPS.

    Groups that already have a ``group_meta`` row are skipped unless ``rescan``. With GPS: ``set_camera_gps`` and a
    ``catalog`` geo point at the group's instant. Every scanned group also gets its ``subsec`` (sh-0b). Without: an empty ``group_meta`` row. Returns
    ``{scanned, with_gps, errors}``. Unreadable files get no row, so the next scan retries them.
    """
    mount = Path(mount)
    rows = conn.execute(
        """
        SELECT g.group_id, g.body_id, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s,
               i.rel_path, m.group_id AS has_meta
        FROM instances i
        JOIN assets a ON a.asset_id = i.asset_id AND a.group_role = 'primary'
        JOIN capture_groups g ON g.group_id = a.group_id
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE i.volume_id = ? AND i.state IN ('indexed', 'verified')
        ORDER BY g.group_id, i.instance_id
        """,
        (volume_id,),
    ).fetchall()
    todo: dict[int, sqlite3.Row] = {}
    for r in rows:
        if r["group_id"] in todo or (not rescan and r["has_meta"] is not None):
            continue
        todo[r["group_id"]] = r
    work = list(todo.values())
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    clocks = BodyClocks.load(conn)
    counts = {"scanned": 0, "with_gps": 0, "errors": 0}
    emit.emit("progress", phase="meta", current=0, total=len(work), message=f"reading {mount}")

    for chunk in _chunks(work):
        paths = [mount / r["rel_path"] for r in chunk]
        result = metadata.read_metadata(paths, workers=cfg.exiftool_workers)
        with repo.transaction(conn):
            for r, path in zip(chunk, paths):
                gid = int(r["group_id"])
                counts["scanned"] += 1
                meta = result.meta.get(path)
                if meta is None:
                    counts["errors"] += 1
                    emit.emit("warning", code="read_error",
                              message=f"{r['rel_path']}: {result.errors.get(path, 'unreadable')}")
                    continue
                repo.set_subsec(conn, gid, meta.get("SubSecTimeOriginal"))
                gps = _gps(meta)
                if gps is None:
                    repo.ensure_group_meta(conn, gid)
                    continue
                lat, lon, alt = gps
                repo.set_camera_gps(conn, gid, lat=lat, lon=lon, alt=alt)
                counts["with_gps"] += 1
                when = group_utc(r, default_offset, clocks)
                if when is not None:
                    repo.upsert_geo_point(conn, utc_time=utc_text(when), lat=lat, lon=lon, alt=alt,
                                          source="catalog", source_ref=f"group:{gid}")
        emit.emit("progress", phase="meta", current=counts["scanned"], total=len(work), message="")
    return counts


def _file_utc(meta: dict, default_offset: str) -> datetime | None:
    """UTC of a reference file's capture time: its own offset, else the default. QuickTime UTC times are UTC."""
    naive = meta.get("DateTimeOriginal")
    if not isinstance(naive, datetime):
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    if meta.get("CaptureTimeSource") == "quicktime_utc":
        offset = "+00:00"
    else:
        offset = meta.get("OffsetTimeOriginal") or default_offset
    return naive.replace(tzinfo=offset_tz(offset)).astimezone(UTC)


def scan_refs(conn: sqlite3.Connection, cfg: Config, folder: Path, label: str, emit: Emitter) -> dict:
    """Store the GPS fixes of a reference folder (a phone export, a drone card) as geo points under ``label``.

    Every media file (raw, JPEG, HEIC, video, derivative) with GPS and a capture time becomes one point with
    ``source=label`` and ``source_ref`` = its path relative to ``folder``. Read-only. Returns
    ``{files, with_gps, stored, errors}``.
    """
    folder = Path(folder)
    entries = [e for e in scan.walk(folder) if e.kind in META_KINDS]
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    counts = {"files": 0, "with_gps": 0, "stored": 0, "errors": 0}
    emit.emit("progress", phase="meta", current=0, total=len(entries), message=f"reading {folder}")

    for chunk in _chunks(entries):
        result = metadata.read_metadata([e.abs_path for e in chunk], workers=cfg.exiftool_workers)
        with repo.transaction(conn):
            for e in chunk:
                counts["files"] += 1
                meta = result.meta.get(e.abs_path)
                if meta is None:
                    counts["errors"] += 1
                    emit.emit("warning", code="read_error",
                              message=f"{e.rel_path}: {result.errors.get(e.abs_path, 'unreadable')}")
                    continue
                gps = _gps(meta)
                if gps is None:
                    continue
                counts["with_gps"] += 1
                when = _file_utc(meta, default_offset)
                if when is None:
                    continue
                lat, lon, alt = gps
                repo.upsert_geo_point(conn, utc_time=utc_text(when), lat=lat, lon=lon, alt=alt, source=label,
                                      source_ref=e.rel_path)
                counts["stored"] += 1
        emit.emit("progress", phase="meta", current=counts["files"], total=len(entries), message="")
    return counts


def _sidecar_meta(row: sqlite3.Row, default_offset: str) -> dict:
    """The fields `xmp.render_xmp` takes, from a dirty group's ``group_meta`` row."""
    meta: dict = {"lat": row["lat"], "lon": row["lon"], "alt": row["alt"], "place_name": row["place_name"],
                  "city": row["city"], "state": row["state"], "country": row["country"],
                  "creator": row["creator"], "copyright": row["copyright"], "title": row["title"],
                  "keywords": json.loads(row["keywords_json"] or "[]")}
    if int(row["time_shift_s"] or 0):
        when = group_utc(row, default_offset)
        if when is not None:
            zone = offset_tz(row["capture_offset"] or default_offset or "+00:00")
            meta["date_original"] = when.astimezone(zone).isoformat(timespec="seconds")
    return meta


def write_sidecars(conn: sqlite3.Connection, cfg: Config, mount: Path, volume_id: str, emit: Emitter, *,
                   dry_run: bool = False) -> dict:
    """Write XMP sidecars for dirty groups whose primary file is on this WORKING volume.

    Raw primaries get ``<stem>.xmp`` next to the file; other kinds are counted as ``catalog_only`` and stay dirty.
    A sidecar whose SHA-256 is not the last one PhotoVault wrote is a conflict: it is left alone, a warning is
    emitted and the group stays dirty. Refused unless the volume is WORKING (ValueError). Returns
    ``{dirty, written, conflicts, catalog_only, would_write}``; ``would_write`` is set only by ``dry_run``.
    """
    mount = Path(mount)
    vol = conn.execute("SELECT role, label FROM volumes WHERE volume_id = ?", (volume_id,)).fetchone()
    if vol is None:
        raise ValueError(f"volume {volume_id} is not in the catalog")
    if vol["role"] != "WORKING":
        raise ValueError(f"sidecars are written only on WORKING volumes; {vol['label']} is {vol['role']}")

    rows = conn.execute(
        """
        SELECT m.*, g.capture_time, g.capture_offset, i.rel_path, i.kind AS kind
        FROM group_meta m
        JOIN capture_groups g ON g.group_id = m.group_id
        JOIN assets a ON a.group_id = m.group_id AND a.group_role = 'primary'
        JOIN instances i ON i.asset_id = a.asset_id AND i.volume_id = ? AND i.state IN ('indexed', 'verified')
        WHERE m.dirty = 1
        ORDER BY m.group_id, i.instance_id
        """,
        (volume_id,),
    ).fetchall()
    first_per_group: dict[int, sqlite3.Row] = {}
    for r in rows:
        first_per_group.setdefault(r["group_id"], r)  # one row per group: its first instance on this volume
    unique = list(first_per_group.values())
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    counts = {"dirty": len(unique), "written": 0, "conflicts": 0, "catalog_only": 0, "would_write": 0}

    for row in unique:
        gid = int(row["group_id"])
        if row["kind"] != "raw":
            counts["catalog_only"] += 1
            continue
        media = mount / row["rel_path"]
        sidecar = xmp.sidecar_path(media)
        rel = sidecar.relative_to(mount).as_posix()
        if dry_run:
            counts["would_write"] += 1
            continue
        text = xmp.render_xmp(_sidecar_meta(row, default_offset))
        last = conn.execute(
            "SELECT sha256 FROM sidecars WHERE volume_id = ? AND rel_path = ? ORDER BY sidecar_id DESC LIMIT 1",
            (volume_id, rel),
        ).fetchone()
        try:
            sha = xmp.write_sidecar(sidecar, text, expected_sha256=last["sha256"] if last else None)
        except xmp.SidecarConflict as exc:
            counts["conflicts"] += 1
            emit.emit("warning", code="sidecar_conflict", message=f"{rel}: {exc}; left as is")
            continue
        with repo.transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO sidecars (group_id, volume_id, rel_path, sha256, mtime_ns, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (gid, volume_id, rel, sha, sidecar.stat().st_mtime_ns, repo.utcnow()),
            )
            conn.execute("UPDATE group_meta SET dirty = 0, updated_at = ? WHERE group_id = ?", (repo.utcnow(), gid))
        counts["written"] += 1
    emit.emit("progress", phase="meta", current=len(unique), total=len(unique), message="sidecars")
    return counts
