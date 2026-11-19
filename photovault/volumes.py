"""Volume probe, card detection, markers and registration (system-design sections 3 and 7).

The only write in this module is `write_marker`, which refuses CARD volumes (vault-rules 1).
Nothing here deletes or renames anything on a volume.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import threading
import time
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


# ``diskutil info`` takes 0.15-0.4 s per volume and its answer (file system, UUID, size) does not change while a
# volume stays mounted, so the default runner's answers are kept for a while, keyed by mount path and device id
# (a remount or a different disk at the same path gets a new device id). Markers and DCIM are always read fresh.
DISKUTIL_TTL_S = 600.0
_DISKUTIL_CACHE: dict[tuple[str, int], tuple[float, subprocess.CompletedProcess]] = {}
_DISKUTIL_LOCK = threading.Lock()


def _diskutil_info(path: Path, run) -> subprocess.CompletedProcess:
    argv = ["diskutil", "info", "-plist", str(path)]
    if run is not subprocess.run:  # an injected runner (tests) is never cached
        return run(argv, capture_output=True, check=False)
    try:
        key = (str(path), os.stat(path).st_dev)
    except OSError:
        return run(argv, capture_output=True, check=False)
    now = time.monotonic()
    with _DISKUTIL_LOCK:
        hit = _DISKUTIL_CACHE.get(key)
    if hit is not None and now - hit[0] < DISKUTIL_TTL_S:
        return hit[1]
    result = run(argv, capture_output=True, check=False)
    if result.returncode == 0:
        with _DISKUTIL_LOCK:
            _DISKUTIL_CACHE[key] = (now, result)
    return result


def clear_probe_cache() -> None:
    with _DISKUTIL_LOCK:
        _DISKUTIL_CACHE.clear()


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


def is_folder_volume(path: Path) -> bool:
    """A folder on the Mac used as a drive (D-047): a directory inside the home folder that is not a mount point."""
    path = Path(path).expanduser()
    try:
        home = Path.home().resolve()
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved != home and resolved.is_relative_to(home) and path.is_dir() and not os.path.ismount(path)


def _containing_mount(path: Path) -> Path:
    p = Path(path).resolve()
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


def probe_folder(folder: Path, *, run=subprocess.run) -> VolumeProbe:
    """Describe a Mac folder used as a drive. It has no volume UUID of its own (the disk's UUID would match every
    other folder on it), so it is found again only by its marker; it is never a card."""
    folder = Path(folder).expanduser()
    fs_type = ""
    result = _diskutil_info(_containing_mount(folder), run)
    if result.returncode == 0:
        try:
            fs_type = str(plistlib.loads(result.stdout).get("FilesystemType") or "").lower()
        except Exception:  # noqa: BLE001 - the filesystem name is only shown to people
            fs_type = ""
    try:
        capacity: int | None = shutil.disk_usage(folder).total
    except OSError:
        capacity = None
    return VolumeProbe(mount=folder, name=folder.name, fs_type=fs_type, da_volume_uuid=None, capacity_bytes=capacity,
                       internal=True, marker_id=read_marker(folder), looks_like_card=False)


def probe(mount: Path, *, run=subprocess.run) -> VolumeProbe:
    """Describe the volume mounted at ``mount`` using ``diskutil info -plist`` (read-only).

    A folder inside the home folder that is not a mount point is probed as a folder volume (``probe_folder``).
    Missing plist keys give None (UUID, capacity) or False (internal); a missing name falls back to the mount
    directory name and a missing filesystem type to an empty string.
    """
    mount = Path(mount)
    if is_folder_volume(mount):
        return probe_folder(mount, run=run)
    result = _diskutil_info(mount, run)
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
            hint = " (to change a LEGACY drive to WORKING or MASTER, pass --change-role)" if row["role"] == "LEGACY" else ""
            raise ValueError(f"volume {row['label']!r} is already registered as {row['role']}, not {role}{hint}")
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


class RoleChangeRefused(ValueError):
    """Raised when a LEGACY volume cannot be turned into a WORKING or MASTER one."""


def change_role(conn: sqlite3.Connection, probe: VolumeProbe, role: str) -> str:
    """Turn a registered LEGACY volume into WORKING or MASTER (im-2, D-088). Does not commit.

    The volume keeps its ``volume_id`` so every indexed instance keeps its row. The marker written to the drive
    holds that same id and ``volumes.marker_id`` is set to it. Nothing on the drive moves or is renamed.
    Refused for CARD volumes, folder volumes, unregistered volumes, a volume that is not LEGACY, a volume with a
    job that is running, planned or paused, and a drive whose marker holds another id.
    """
    if role not in ("WORKING", "MASTER"):
        raise RoleChangeRefused(f"a LEGACY drive can only become WORKING or MASTER, not {role!r}")
    if probe.looks_like_card:
        raise RoleChangeRefused(f"{probe.mount} looks like a camera card; cards never change role")
    if probe.da_volume_uuid is None:
        raise RoleChangeRefused(f"{probe.mount} has no volume UUID (a folder?), so it cannot change role")
    row = conn.execute("SELECT * FROM volumes WHERE da_volume_uuid = ?", (probe.da_volume_uuid,)).fetchone()
    if row is None:
        raise RoleChangeRefused(f"{probe.mount} is not in the catalog; register it first")
    if row["role"] == "CARD":
        raise RoleChangeRefused(f"{row['label']!r} is a CARD volume; cards never change role")
    if row["role"] != "LEGACY":
        raise RoleChangeRefused(f"{row['label']!r} is {row['role']}, not LEGACY; only LEGACY drives change role")
    busy = conn.execute(
        "SELECT job_id, kind, state FROM jobs WHERE (source_volume_id = ? OR target_volume_id = ?) "
        "AND state IN ('running','planned','paused') LIMIT 1", (row["volume_id"], row["volume_id"])).fetchone()
    if busy is not None:
        raise RoleChangeRefused(
            f"job {busy['job_id']} ({busy['kind']}) is {busy['state']} on {row['label']!r}; finish or cancel it first")
    volume_id = row["volume_id"]
    if probe.marker_id is not None and probe.marker_id != volume_id:
        raise RoleChangeRefused(f"{probe.mount / MARKER_NAME} already holds a different volume id; refusing to replace it")
    other = conn.execute("SELECT 1 FROM volumes WHERE marker_id = ? AND volume_id != ?",
                         (volume_id, volume_id)).fetchone()
    if other is not None:
        raise RoleChangeRefused("another volume already uses this id as its marker")
    write_marker(probe.mount, volume_id)
    conn.execute(
        "UPDATE volumes SET role = ?, marker_id = ?, last_seen_at = ?, last_mount_path = ? WHERE volume_id = ?",
        (role, volume_id, repo.utcnow(), str(probe.mount), volume_id),
    )
    return volume_id
