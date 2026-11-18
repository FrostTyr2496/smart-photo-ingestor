"""Mounted-drive listing shared by `drives` (cli) and the web API (ui-4).

`scan_mounts` is the loop `cmd_drives` always ran: every directory under `root` (default: the mounts folder)
that is not a symlink or a system mount, probed read-only. Each row carries the keys `drives` prints plus a few
more the web API needs. Only mounted volumes appear here; the web layer adds remembered-but-unplugged ones.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from photovault import volumes
from photovault.catalog import repo
from photovault.protocol import human_bytes

VOLUMES_ROOT = Path("/" + "Volumes")
SKIP_MOUNTS = {"Recovery", "Macintosh HD"}
# The keys `photovault drives` prints (its output is unchanged).
CLI_DRIVE_KEYS = ("mount", "role", "label", "fs", "size", "free")
# The Mac counts as a working drive only inside this folder (D-047); the `mac_folder` setting overrides it.
DEFAULT_MAC_FOLDER = "~/Sandbox"
MAC_LABEL = "This Mac"


def mac_folder(conn: Any) -> Path | None:
    """The configured Mac folder, expanded, or None when it does not exist (or is not a folder inside home)."""
    path = Path(repo.get_setting(conn, "mac_folder", DEFAULT_MAC_FOLDER) or DEFAULT_MAC_FOLDER).expanduser()
    return path if volumes.is_folder_volume(path) else None


def scan_mounts(conn: Any, root: Path, emit: Any, *, include_mac: bool = True) -> list[dict]:
    """Probe each mounted drive under `root`, then the Mac folder (D-047) when it exists. Probe failures become
    `warning` events and are skipped."""
    rows: list[dict] = []
    for mount in sorted(Path(root).iterdir()):
        if mount.is_symlink() or not mount.is_dir() or mount.name in SKIP_MOUNTS:
            continue
        try:
            p = volumes.probe(mount)
        except (RuntimeError, OSError, ValueError) as exc:
            emit.emit("warning", code="probe", message=f"{mount}: {exc}")
            continue
        row = repo.find_volume(conn, marker_id=p.marker_id, da_volume_uuid=p.da_volume_uuid)
        try:
            free = human_bytes(shutil.disk_usage(mount).free)
        except OSError:
            free = "?"
        role = row["role"] if row else ("CARD (new)" if p.looks_like_card else "unassigned")
        rows.append({
            "mount": str(mount), "role": role, "label": row["label"] if row else "", "fs": p.fs_type,
            "size": human_bytes(p.capacity_bytes or 0), "free": free,
            "volume_id": row["volume_id"] if row else None, "mounted": True,
            "looks_like_card": p.looks_like_card,
            "last_seen_at": row["last_seen_at"] if row else None, "local": False,
        })
    folder = mac_folder(conn) if include_mac else None
    if folder is not None:
        rows.append(_mac_row(conn, folder, emit))
    return rows


def _mac_row(conn: Any, folder: Path, emit: Any) -> dict:
    p = volumes.probe_folder(folder)
    row = repo.find_volume(conn, marker_id=p.marker_id) if p.marker_id else None
    try:
        free = human_bytes(shutil.disk_usage(folder).free)
    except OSError:
        free = "?"
    return {
        "mount": str(folder), "role": row["role"] if row else "unassigned", "label": row["label"] if row else MAC_LABEL,
        "fs": p.fs_type, "size": human_bytes(p.capacity_bytes or 0), "free": free,
        "volume_id": row["volume_id"] if row else None, "mounted": True, "looks_like_card": False,
        "last_seen_at": row["last_seen_at"] if row else None, "local": True,
    }
