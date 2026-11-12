"""Volume probe, card detection, markers and registration (system-design sections 3 and 7).

The only write in this module is `write_marker`, which refuses CARD volumes (vault-rules 1).
Nothing here deletes or renames anything on a volume.
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from photovault.catalog import repo

MARKER_NAME = ".photovault-id"
ROLES = frozenset({"CARD", "WORKING", "MASTER", "LEGACY"})


class CardWriteRefused(Exception):
    """Raised when something tries to write to a volume that looks like a camera card."""


class UnknownVolume(Exception):
    """Raised when a volume is not in the catalog and the caller gave no role to register it with."""


@dataclass(frozen=True)
class VolumeProbe:
    mount: Path
    name: str
    fs_type: str
    da_volume_uuid: str | None
    capacity_bytes: int | None
    internal: bool
    marker_id: str | None
    looks_like_card: bool


def looks_like_card(mount: Path) -> bool:
    """A DCIM directory at the volume root."""
    return (Path(mount) / "DCIM").is_dir()


def read_marker(mount: Path) -> str | None:
    """Return the ``volume_id`` in ``<mount>/.photovault-id``, or None when there is no marker.

    A marker that exists but cannot be parsed raises ``ValueError``: it is never treated as "no marker".
    """
    path = Path(mount) / MARKER_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError(f"cannot read volume marker {path}: {exc}") from exc
    try:
        volume_id = json.loads(text)["volume_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"volume marker {path} is not valid JSON with a volume_id") from exc
    if not isinstance(volume_id, str) or not volume_id:
        raise ValueError(f"volume marker {path} has an empty or non-string volume_id")
    return volume_id


def write_marker(mount: Path, volume_id: str) -> None:
    """Write ``<mount>/.photovault-id`` atomically (temp file, fsync, rename).

    Raises ``CardWriteRefused`` on a card and ``FileExistsError`` if a marker with a different id exists.
    Writing the same id again is a no-op.
    """
    mount = Path(mount)
    if looks_like_card(mount):
        raise CardWriteRefused(f"{mount} looks like a camera card (it has DCIM); markers are never written to cards")
    existing = read_marker(mount)
    if existing == volume_id:
        return
    if existing is not None:
        raise FileExistsError(f"{mount / MARKER_NAME} already holds a different volume id; refusing to replace it")
    payload = json.dumps(
        {"volume_id": volume_id, "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    final = mount / MARKER_NAME
    tmp = mount / f".{MARKER_NAME}.tmp"
    with open(tmp, "x", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.rename(tmp, final)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def probe(mount: Path, *, run=subprocess.run) -> VolumeProbe:
    """Describe the volume mounted at ``mount`` using ``diskutil info -plist`` (read-only).

    Missing plist keys give None (UUID, capacity) or False (internal); a missing name falls back to the mount
    directory name and a missing filesystem type to an empty string.
    """
    mount = Path(mount)
    result = run(["diskutil", "info", "-plist", str(mount)], capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        raise RuntimeError(f"diskutil info failed for {mount}: {(stderr or '').strip() or result.returncode}")
    try:
        info = plistlib.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(f"could not parse diskutil output for {mount}: {exc}") from exc
    capacity = info.get("TotalSize", info.get("Size"))
    return VolumeProbe(
        mount=mount,
        name=info.get("VolumeName") or mount.name,
        fs_type=str(info.get("FilesystemType") or "").lower(),
        da_volume_uuid=info.get("VolumeUUID"),
        capacity_bytes=int(capacity) if capacity is not None else None,
        internal=bool(info.get("Internal", False)),
        marker_id=read_marker(mount),
        looks_like_card=looks_like_card(mount),
    )


def register_volume(
    conn: sqlite3.Connection, probe: VolumeProbe, *, role: str | None = None, label: str | None = None
) -> str:
    """Return the ``volume_id`` for a probed volume, adding it to the catalog if needed.

    Does not commit; callers wrap it in ``repo.transaction``. Matching is by marker, then by
    ``da_volume_uuid``. A match only refreshes ``last_seen_at`` and ``last_mount_path``, so a renamed
    volume keeps its id and label. No match:
    - a card gets a new CARD volume labelled ``card:<name>``;
    - anything else needs ``role`` (else ``UnknownVolume``). WORKING and MASTER need a marker already
      written; LEGACY needs a ``da_volume_uuid`` and never gets a marker.
    """
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(ROLES)}")
    row = repo.find_volume(conn, marker_id=probe.marker_id, da_volume_uuid=probe.da_volume_uuid)
    if row is not None:
        if role is not None and role != row["role"]:
            raise ValueError(f"volume {row['label']!r} is already registered as {row['role']}, not {role}")
        conn.execute(
            "UPDATE volumes SET last_seen_at = ?, last_mount_path = ? WHERE volume_id = ?",
            (repo.utcnow(), str(probe.mount), row["volume_id"]),
        )
        return row["volume_id"]

    if probe.looks_like_card:
        if role not in (None, "CARD"):
            raise ValueError(f"{probe.mount} looks like a camera card, so it cannot be registered as {role}")
        role = "CARD"
    elif role is None:
        raise UnknownVolume(f"{probe.mount} ({probe.name}) is not in the catalog; pass a role to register it")

    if role in ("WORKING", "MASTER"):
        if probe.marker_id is None:
            raise ValueError(f"{role} volumes need a marker; write it with write_marker first")
        volume_id = probe.marker_id
    else:
        if role == "LEGACY" and probe.da_volume_uuid is None:
            raise ValueError(f"{probe.mount} has no volume UUID, so a LEGACY volume could not be matched again")
        volume_id = str(uuid.uuid4())

    default_label = f"card:{probe.name}" if role == "CARD" else probe.name
    repo.upsert_volume(
        conn,
        volume_id=volume_id,
        role=role,
        label=label or default_label,
        fs_type=probe.fs_type or None,
        da_volume_uuid=probe.da_volume_uuid,
        marker_id=probe.marker_id if role in ("WORKING", "MASTER") else None,
        capacity_bytes=probe.capacity_bytes,
        mount_path=probe.mount,
    )
    return volume_id
