"""The preview cache (act-4): embedded JPEG previews kept on the Mac, so photos show while their drive is unplugged.

`<home>/thumbs/<group_id>.jpg` holds the small embedded preview and `<home>/previews/<group_id>.jpg` the largest one
(the viewer, rv-20). `/api/groups/<id>/thumb` fills them one at a time as photos are viewed; `cache_volume` fills the
small ones for a whole drive while it is connected. Drives are only read (ExifTool without write flags, through
`preview`'s tags); the only writes are under `<home>`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from exiftool import ExifTool

from photovault import preview
from photovault.metadata import _find_exiftool

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 100


def cache_path(home: Path, group_id: int, *, large: bool = False) -> Path:
    return Path(home) / ("previews" if large else "thumbs") / f"{int(group_id)}.jpg"


def read_cached(home: Path, group_id: int, *, large: bool = False) -> bytes | None:
    path = cache_path(home, group_id, large=large)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def store(home: Path, group_id: int, data: bytes, *, large: bool = False) -> None:
    """Write one preview atomically (temp file, then rename). Raises OSError."""
    path = cache_path(home, group_id, large=large)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def cached_ids(home: Path, *, large: bool = False) -> set[int]:
    """Group ids with a cached preview (one directory listing)."""
    folder = Path(home) / ("previews" if large else "thumbs")
    try:
        names = os.listdir(folder)
    except OSError:
        return set()
    return {int(n[:-4]) for n in names if n.endswith(".jpg") and n[:-4].isdigit()}


def volume_primaries(conn: sqlite3.Connection, volume_id: str) -> list[tuple[int, str]]:
    """``(group_id, rel_path)`` of each group's primary file on a drive, by group id. Hashed files come through their
    asset; files indexed but not hashed yet through their index job (as `repo.indexed_primary_files`)."""
    rows = conn.execute(
        """
        SELECT a.group_id AS g, i.rel_path AS rel FROM assets a
        JOIN instances i ON i.asset_id = a.asset_id
        WHERE i.volume_id = :v AND i.state IN ('indexed', 'verified') AND a.group_role = 'primary'
          AND a.group_id IS NOT NULL
        UNION ALL
        SELECT ji.group_id AS g, ji.src_rel AS rel FROM job_items ji
        JOIN jobs j ON j.job_id = ji.job_id AND j.kind = 'index_volume'
        WHERE j.source_volume_id = :v AND ji.role = 'primary' AND ji.group_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.group_id = ji.group_id AND a.group_role = 'primary')
        """, {"v": volume_id}).fetchall()
    seen: dict[int, str] = {}
    for r in rows:
        seen.setdefault(int(r["g"]), r["rel"])
    return sorted(seen.items())


class PreviewReader:
    """One persistent ExifTool for many files. `read` gives the first JPEG among `tags`, or None. Without a working
    ExifTool it falls back to `preview.extract_preview` (one process per file)."""

    def __init__(self) -> None:
        self._et: ExifTool | None = None

    def __enter__(self) -> PreviewReader:
        exe = _find_exiftool()
        if exe is not None:
            try:
                self._et = ExifTool(executable=exe)
                self._et.run()
            except Exception as exc:  # noqa: BLE001  (any start failure: fall back to one process per file)
                logger.warning("persistent exiftool did not start (%s); reading previews one file at a time", exc)
                self._et = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self._et is not None:
            try:
                self._et.terminate()
            except Exception:  # noqa: BLE001
                pass

    def read(self, path: Path, tags: tuple[str, ...] = preview.PREVIEW_TAGS) -> bytes | None:
        target = str(Path(path).resolve())  # absolute, so a name can never be read as an option
        if self._et is None:
            return preview.extract_preview(Path(target), tags)
        for tag in tags:
            try:
                data = self._et.execute("-b", f"-{tag}", target, raw_bytes=True)
            except Exception as exc:  # noqa: BLE001  (a bad file must not stop the run)
                logger.debug("exiftool -%s failed for %s: %s", tag, target, exc)
                continue
            if isinstance(data, bytes) and data.startswith(preview.JPEG_MAGIC):
                return data
        return None


def cache_volume(conn: sqlite3.Connection, home: Path, mount: Path, volume_id: str, emit, *,
                 should_stop: Callable[[], bool] = lambda: False,
                 reader: Callable[[], PreviewReader] | None = None) -> dict:
    """Cache the small preview of every photo on a connected drive that has none yet. Returns counts:
    ``photos`` on the drive, ``already`` cached, ``added``, ``no_preview``, ``missing`` (file gone), ``bytes`` added,
    and ``paused`` when `should_stop` ended it early (a re-run carries on)."""
    base = Path(mount).resolve()
    todo = volume_primaries(conn, volume_id)
    have = cached_ids(home)
    counts = {"photos": len(todo), "already": 0, "added": 0, "no_preview": 0, "missing": 0, "bytes": 0,
              "paused": False}
    pending = [(g, rel) for g, rel in todo if g not in have]
    counts["already"] = len(todo) - len(pending)
    with (reader or PreviewReader)() as rd:
        for k, (gid, rel) in enumerate(pending, 1):
            if should_stop():
                counts["paused"] = True
                break
            src = (base / rel).resolve()
            if not src.is_relative_to(base) or not src.is_file():
                counts["missing"] += 1
            else:
                data = rd.read(src)
                if data is None:
                    counts["no_preview"] += 1
                else:
                    store(home, gid, data)
                    counts["added"] += 1
                    counts["bytes"] += len(data)
            if k % PROGRESS_EVERY == 0 or k == len(pending):
                emit.emit("progress", phase="previews", current=k, total=len(pending),
                          message=f"{k:,} of {len(pending):,} photos")
    return counts
