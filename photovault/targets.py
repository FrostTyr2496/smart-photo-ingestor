"""Copy-target recommendations (rec-1, D-103): which connected drives to copy a card onto, and why.

``recommend`` ranks the plugged-in WORKING and MASTER drives for a copy of `planned_bytes` and proposes a pair for
green (D-089): the fastest WORKING drive takes the card read (the import), another drive takes the trailing copy
(bk-3). It reads the catalog, `<home>/metrics.sqlite` (read-only) and `diskutil info -plist` (SSD or HDD); it writes
nothing and never touches a drive's files. A recommendation only fills defaults: the user confirms every copy.
"""

from __future__ import annotations

import os
import plistlib
import sqlite3
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from photovault.catalog import repo
from photovault.protocol import human_bytes

WRITABLE_FS = ("exfat", "apfs")   # D-101: the same rule as `backup.target_volume`
ROLES = ("WORKING", "MASTER")
HEADROOM = 1.05                   # free space must cover the plan plus 5 %
SOLID_TTL_S = 600.0               # `diskutil info` answers are kept for a while, like volumes.probe
_SOLID_CACHE: dict[str, tuple[float, bool | None]] = {}
FS_NAME = {"exfat": "exFAT", "apfs": "APFS", "ntfs": "NTFS", "ufsd_ntfs": "NTFS", "hfs": "Mac OS Extended"}


def clear_cache() -> None:
    _SOLID_CACHE.clear()


def is_solid_state(mount: str, run=subprocess.run) -> bool | None:
    """True for an SSD, False for a spinning disk, None when `diskutil` does not say. Cached per mount."""
    now = time.monotonic()
    hit = _SOLID_CACHE.get(mount)
    if run is subprocess.run and hit is not None and now - hit[0] < SOLID_TTL_S:
        return hit[1]
    solid: bool | None = None
    if run is subprocess.run and not os.path.exists(mount):
        return None
    try:
        out = run(["diskutil", "info", "-plist", mount], capture_output=True, check=False)
        if out.returncode == 0:
            value = plistlib.loads(out.stdout).get("SolidState")
            solid = value if isinstance(value, bool) else None
    except Exception:  # noqa: BLE001 - a drive that cannot be asked is simply unknown
        solid = None
    if run is subprocess.run:
        _SOLID_CACHE[mount] = (now, solid)
    return solid


def _speeds(home: Path | None) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """Measured MB/s read and written, and read errors, per volume key (the volume id), from the metrics store."""
    reads: dict[str, float] = {}
    writes: dict[str, float] = {}
    errors: dict[str, int] = {}
    path = Path(home) / "metrics.sqlite" if home is not None else None
    if path is None or not path.exists():
        return reads, writes, errors
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return reads, writes, errors
    try:
        for key, op, nbytes, busy, errs in conn.execute(
                "SELECT volume_key, op, SUM(bytes), SUM(busy_s), SUM(errors) FROM daily "
                "WHERE op IN ('read_full', 'read_partial', 'read_verify', 'write') GROUP BY volume_key, op"):
            rate = round(nbytes / busy / 1e6, 1) if busy and busy > 0 and nbytes else None
            if op == "write" and rate is not None:
                writes[key] = rate
            elif op == "read_full" and rate is not None:
                reads[key] = rate
            if op != "write" and errs:
                errors[key] = errors.get(key, 0) + int(errs)
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return reads, writes, errors


def card_unbacked_bytes(conn: sqlite3.Connection, card_volume_id: str) -> int:
    """Bytes of the card's files that have no verified copy on a WORKING or MASTER drive yet."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(i.size), 0) FROM instances i WHERE i.volume_id = ? AND i.state != 'missing' AND (
          i.asset_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM instances o JOIN volumes ov ON ov.volume_id = o.volume_id
            WHERE o.asset_id = i.asset_id AND o.state = 'verified' AND ov.role IN ('WORKING', 'MASTER')))
        """, (card_volume_id,)).fetchone()
    return int(row[0])


def _already_there(conn: sqlite3.Connection, card_volume_id: str | None) -> dict[str, int]:
    """Per drive, how many of the card's files already have a verified copy on it."""
    if not card_volume_id:
        return {}
    rows = conn.execute(
        """
        SELECT o.volume_id, COUNT(DISTINCT i.asset_id) FROM instances i
        JOIN instances o ON o.asset_id = i.asset_id AND o.state = 'verified' AND o.volume_id != i.volume_id
        WHERE i.volume_id = ? AND i.state != 'missing' AND i.asset_id IS NOT NULL GROUP BY o.volume_id
        """, (card_volume_id,)).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _free_bytes(mount: str) -> int | None:
    try:
        st = os.statvfs(mount)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


def recommend(
    conn: sqlite3.Connection, home: Path | None, mounted: Mapping[str, str | None], *, planned_bytes: int,
    source_volume_id: str | None, run=subprocess.run, exclude: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """Rank the connected WORKING/MASTER drives for a copy of `planned_bytes`.

    Returns ``{"planned_bytes", "candidates", "pair"}``. Each candidate has ``volume_id``, ``label``, ``role``,
    ``mount``, ``fs``, ``free_bytes``, ``usable``, ``solid_state``, ``write_mbps``, ``read_mbps``, ``read_errors``,
    ``already_has``, ``rank`` (1 = best, usable drives first) and plain-language ``reasons``. ``exclude`` leaves drives out (one that
    already holds the files). ``pair`` is
    ``{"read": volume_id, "copy": volume_id | None, "note": str}``: the best usable WORKING drive takes the card
    read, the best other usable drive takes the trailing copy (None when only one drive qualifies).
    """
    reads, writes, errors = _speeds(home)
    there = _already_there(conn, source_volume_id)
    need = int(planned_bytes * HEADROOM)
    cands: list[dict[str, Any]] = []
    for vol in repo.list_volumes(conn):
        mount = mounted.get(vol["volume_id"])
        if vol["role"] not in ROLES or not mount or vol["volume_id"] in exclude:
            continue
        vid = vol["volume_id"]
        fs = (vol["fs_type"] or "").lower()
        free = _free_bytes(mount)
        solid = is_solid_state(mount, run)
        reasons: list[str] = []
        blocked: str | None = None
        if fs not in WRITABLE_FS:
            blocked = f"{FS_NAME.get(fs, fs.upper() or 'Unknown file system')}: not written to (only exFAT and APFS are)"
        elif free is not None and free < need:
            blocked = f"Only {human_bytes(free)} free; this copy needs {human_bytes(need)}"
        if blocked:
            reasons.append(blocked)
        else:
            reasons.append(f"{FS_NAME.get(fs, fs)}, {human_bytes(free)} free" if free is not None else FS_NAME.get(fs, fs))
        if solid is True:
            reasons.append("SSD")
        elif solid is False:
            reasons.append("Spinning hard disk: slower, and exFAT has no journal if it is unplugged mid-copy"
                           if fs == "exfat" else "Spinning hard disk: slower")
        if vid in writes:
            reasons.append(f"{writes[vid]:g} MB/s write measured")
        elif vid in reads:
            reasons.append(f"{reads[vid]:g} MB/s read measured")
        if errors.get(vid):
            reasons.append(f"{errors[vid]} read error{'s' if errors[vid] != 1 else ''} on record")
        if there.get(vid):
            reasons.append(f"Already holds {there[vid]:,} of this card's files")
        if vol["role"] == "MASTER":
            reasons.append("Backup drive: a copy here turns the card green by itself")
        cands.append({
            "volume_id": vid, "label": vol["label"], "role": vol["role"], "mount": mount, "fs": fs,
            "free_bytes": free, "usable": blocked is None, "solid_state": solid, "write_mbps": writes.get(vid),
            "read_mbps": reads.get(vid), "read_errors": errors.get(vid, 0), "already_has": there.get(vid, 0),
            "reasons": reasons,
        })

    def key(c: dict) -> tuple:
        speed = c["write_mbps"] if c["write_mbps"] is not None else (c["read_mbps"] or 0)
        return (not c["usable"], c["read_errors"] > 0, {True: 0, None: 1, False: 2}[c["solid_state"]], -speed,
                c["role"] != "MASTER", c["label"].lower())

    cands.sort(key=key)
    for n, c in enumerate(cands, 1):
        c["rank"] = n
    usable = [c for c in cands if c["usable"]]
    reader = next((c for c in usable if c["role"] == "WORKING"), None)
    copier = next((c for c in usable if reader is not None and c["volume_id"] != reader["volume_id"]), None)
    if reader is None:
        note = "No connected working drive can take the import."
    elif copier is None:
        note = "Only one connected drive qualifies: the files will be on one drive (yellow) until a second one is connected."
    else:
        note = (f"Import onto {reader['label']}, then copy each file onto {copier['label']}: two drives, so the card turns green.")
    return {"planned_bytes": planned_bytes, "candidates": cands,
            "pair": {"read": reader["volume_id"] if reader else None, "copy": copier["volume_id"] if copier else None,
                     "note": note}}
