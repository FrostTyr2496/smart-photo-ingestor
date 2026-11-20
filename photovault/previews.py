"""The preview cache (act-4): embedded JPEG previews kept on the Mac, so photos show while their drive is unplugged.

`<home>/thumbs/<group_id>.jpg` holds the small embedded preview and `<home>/previews/<group_id>.jpg` the largest one
(the viewer, rv-20). `/api/groups/<id>/thumb` fills them one at a time as photos are viewed; `cache_volume` fills the
small ones for a whole drive while it is connected. Drives are only read (ExifTool without write flags, through
`preview`'s tags); the only writes are under `<home>`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from exiftool import ExifTool
from PIL import Image

from photovault import preview
from photovault.catalog import repo
from photovault.metadata import _find_exiftool

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 100

# Review thumbs (pv-1, D-127/D-128): small JPEGs made while hashing, kept in a folder on the Mac that the user picks.
REVIEW_SETTING = "review_thumbs"
REVIEW_DEFAULT_FOLDER = "~/Sandbox/PhotoVault Thumbs"
REVIEW_LONG_EDGE = 384
REVIEW_TARGET_BYTES = 20_000
REVIEW_QUALITIES = (70, 62, 55, 48, 40)
MOUNTS_ROOT = Path("/Volumes")


def review_config(conn: sqlite3.Connection) -> dict:
    """The stored setting as ``{"enabled": bool, "folder": str}``, with defaults for anything missing or broken."""
    out = {"enabled": False, "folder": REVIEW_DEFAULT_FOLDER}
    try:
        raw = json.loads(repo.get_setting(conn, REVIEW_SETTING, "") or "{}")
    except ValueError:
        raw = {}
    if isinstance(raw, dict):
        out["enabled"] = raw.get("enabled") is True
        if isinstance(raw.get("folder"), str) and raw["folder"].strip():
            out["folder"] = raw["folder"].strip()
    return out


def review_folder(folder: str, mounts_root: Path = MOUNTS_ROOT) -> Path:
    """Expand `~` and resolve. Raises ValueError for a folder on a mounted drive (Mac only, D-128)."""
    path = Path(folder).expanduser()
    if not path.is_absolute():
        raise ValueError("the folder must be an absolute path or start with ~")
    resolved = path.resolve()
    root = Path(mounts_root).resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise ValueError("review thumbs are kept on the Mac only: pick a folder that is not on a mounted drive")
    return resolved


def review_folder_if_enabled(conn: sqlite3.Connection) -> Path | None:
    cfg = review_config(conn)
    if not cfg["enabled"]:
        return None
    try:
        return review_folder(cfg["folder"])
    except ValueError as exc:
        logger.warning("review thumbs are off: %s", exc)
        return None


def review_ids(folder: Path) -> set[int]:
    try:
        names = os.listdir(folder)
    except OSError:
        return set()
    return {int(n[:-4]) for n in names if n.endswith(".jpg") and n[:-4].isdigit()}


def read_review(folder: Path, group_id: int) -> bytes | None:
    path = Path(folder) / f"{int(group_id)}.jpg"
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def make_review_jpeg(data: bytes) -> bytes:
    """Scale an embedded JPEG to 384 px on the long edge; the quality steps down until it is about 20 KB.
    Raises ValueError when the bytes are not a decodable image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.draft("RGB", (REVIEW_LONG_EDGE * 2, REVIEW_LONG_EDGE * 2))
            img = img.convert("RGB")
            img.thumbnail((REVIEW_LONG_EDGE, REVIEW_LONG_EDGE), Image.Resampling.LANCZOS)
            out = b""
            for q in REVIEW_QUALITIES:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
                out = buf.getvalue()
                if len(out) <= REVIEW_TARGET_BYTES:
                    break
            return out
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a decodable image: {exc}") from exc


class ThumbMaker:
    """Makes `<folder>/<group_id>.jpg` for photos a job reads. Never raises: errors are counted (`counts["errors"]`).
    One ExifTool starts on first use and `close` ends it. Existing thumbs are skipped."""

    def __init__(self, folder: Path, reader: Callable[[], PreviewReader] | None = None) -> None:
        self.folder = Path(folder)
        self.counts = {"added": 0, "existing": 0, "no_preview": 0, "errors": 0, "bytes": 0}
        self._make_reader = reader or PreviewReader
        self._reader: PreviewReader | None = None
        self._have = review_ids(self.folder)

    @classmethod
    def from_settings(cls, conn: sqlite3.Connection) -> ThumbMaker | None:
        folder = review_folder_if_enabled(conn)
        return None if folder is None else cls(folder)

    def has(self, group_id: int) -> bool:
        return int(group_id) in self._have

    def make(self, group_id: int, src: Path, data: bytes | None = None) -> bool:
        """Make the thumb for a group from `src` (or from already-read preview bytes). True when one was written."""
        gid = int(group_id)
        if gid in self._have:
            self.counts["existing"] += 1
            return False
        try:
            if data is None:
                if self._reader is None:
                    self._reader = self._make_reader().__enter__()
                data = self._reader.read(Path(src))
            if data is None:
                self.counts["no_preview"] += 1
                return False
            jpg = make_review_jpeg(data)
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.folder / f".{gid}.jpg.tmp"
            tmp.write_bytes(jpg)
            os.replace(tmp, self.folder / f"{gid}.jpg")
        except Exception as exc:  # noqa: BLE001  (a thumb must never fail the job that asked for it)
            self.counts["errors"] += 1
            logger.warning("review thumb for group %s failed: %s", gid, exc)
            return False
        self._have.add(gid)
        self.counts["added"] += 1
        self.counts["bytes"] += len(jpg)
        return True

    def close(self) -> None:
        if self._reader is not None:
            self._reader.__exit__(None, None, None)
            self._reader = None


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
                 reader: Callable[[], PreviewReader] | None = None, review: ThumbMaker | None = None) -> dict:
    """Cache the small preview of every photo on a connected drive that has none yet. Returns counts:
    ``photos`` on the drive, ``already`` cached, ``added``, ``no_preview``, ``missing`` (file gone), ``bytes`` added,
    and ``paused`` when `should_stop` ended it early (a re-run carries on)."""
    base = Path(mount).resolve()
    todo = volume_primaries(conn, volume_id)
    have = cached_ids(home)
    counts = {"photos": len(todo), "already": 0, "added": 0, "no_preview": 0, "missing": 0, "bytes": 0,
              "paused": False}
    pending = [(g, rel) for g, rel in todo if g not in have or (review is not None and not review.has(g))]
    counts["already"] = sum(1 for g, _ in todo if g in have)
    if review is not None:
        counts["review_added"] = 0
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
                    if gid not in have:
                        store(home, gid, data)
                        counts["added"] += 1
                        counts["bytes"] += len(data)
                    if review is not None and review.make(gid, src, data):
                        counts["review_added"] += 1
            if k % PROGRESS_EVERY == 0 or k == len(pending):
                emit.emit("progress", phase="previews", current=k, total=len(pending),
                          message=f"{k:,} of {len(pending):,} photos")
    if review is not None:
        counts["review_errors"] = review.counts["errors"]
    return counts
