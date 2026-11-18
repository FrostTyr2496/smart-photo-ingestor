"""Drive usage (idx-13, D-063): how much of a drive is photography and how much is not.

The catalog tracks photo and video files one by one (`instances`). Everything else on a drive is only
counted: `category` sorts each file that `scan.classify` calls `ignored` into a bucket, and the index stores
one `volume_usage` row per bucket. Camera videos and other videos (a movie library) share the `video` kind,
so `non_camera_videos` splits them by cheap signals first and ExifTool Make/Model last.

Read-only on the volume, like the rest of indexing: it stats files and reads video metadata.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from photovault.scan import FileEntry

# Buckets that count as photography, beside the tracked instances.
PHOTO_CATEGORIES = ("lightroom", "photo_extras")
# Buckets that are not photography. `video_no_camera` holds tracked `video` instances that are not camera
# footage; the rest are files the catalog does not track.
NON_PHOTO_CATEGORIES = ("video_no_camera", "video_other", "audio", "image_other", "documents", "archives",
                        "software", "other")
DISK_USED = "disk_used"  # the drive's used bytes from statvfs at the index, files = 0; absent for a Mac folder

_EXTS: dict[str, frozenset[str]] = {
    "lightroom": frozenset({"lrcat", "lrprev", "lrtemplate", "lrsmcol", "lrplugin", "lrdevelop", "lrlibrary"}),
    "photo_extras": frozenset({"thm", "scr", "ais", "dsc", "gis", "dop", "pp3", "cos", "cop", "on1", "afphoto",
                               "xcf", "pto", "insp", "insv", "gpr"}),
    # `ts` is an MPEG transport stream here, not TypeScript: by size it is far more often video on a media drive.
    "video_other": frozenset({"mkv", "avi", "wmv", "m4v", "webm", "mpg", "mpeg", "flv", "ts", "m2ts", "mts",
                              "vob", "3gp", "ogv", "divx", "rmvb", "asf"}),
    "audio": frozenset({"mp3", "m4a", "aac", "flac", "wav", "aif", "aiff", "ogg", "opus", "wma", "alac", "m4b",
                        "mid", "midi", "ape"}),
    "image_other": frozenset({"png", "gif", "bmp", "webp", "svg", "ico", "tga", "jp2", "avif", "jxl"}),
    "documents": frozenset({"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "md", "pages",
                            "numbers", "key", "epub", "mobi", "csv", "odt", "ods", "odp", "tex", "html", "htm"}),
    "archives": frozenset({"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "dmg", "iso", "img",
                           "sparseimage", "pkg", "vmdk", "vdi", "qcow2", "vhd", "vhdx", "bak"}),
    "software": frozenset({"exe", "msi", "dll", "sys", "so", "dylib", "jar", "apk", "ipa", "py", "js",
                           "c", "cpp", "h", "java", "go", "rs", "rb", "php", "sh", "class", "o", "a"}),
}

# A camera-style stem: an optional short prefix ending `_` (letters first, digits allowed, as in a Nikon custom
# prefix), up to four letters, then 4+ digits, e.g. DSC_3460, DJI_0882, GX010004, IMG_E0772, C0001,
# PXL_20240101_123456789, VID_20240101_123456, Z61_0001.
_CAMERA_STEM = re.compile(r"(?:[A-Za-z][A-Za-z0-9]{0,4}_)?[A-Za-z]{0,4}\d{4,}(?:[_-]\d+)*")
# Sidecars a camera or phone writes beside a clip. Not `.srt`: a movie's subtitles share its stem.
_CAMERA_SIBLINGS = frozenset({"lrv", "thm", "aae", "xmp"})


def category(name: str) -> str:
    """The usage bucket for a file `scan.classify` calls `ignored`, by its name."""
    lower = name.lower()
    if ".lrcat" in lower:  # the catalog and its -wal, -shm, .lock and -data companions
        return "lightroom"
    ext = PurePosixPath(lower).suffix.removeprefix(".")
    for cat, exts in _EXTS.items():
        if ext in exts:
            return cat
    return "other"


def camera_stem(stem: str) -> bool:
    return _CAMERA_STEM.fullmatch(stem) is not None


def tree_size(folders: Iterable[Path]) -> tuple[int, int]:
    """Files and bytes under `folders` (Lightroom `*.lrdata` previews), by `lstat`; unreadable parts are skipped."""
    files = size = 0
    stack = list(folders)
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                st = os.lstat(entry.path)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(Path(entry.path))
            elif stat.S_ISREG(st.st_mode):
                files += 1
                size += st.st_size
    return files, size


def non_camera_videos(entries: list[FileEntry],
                      read_meta: Callable[[list[Path]], dict[Path, dict]]) -> list[FileEntry]:
    """The `video` entries that are not camera footage.

    A video is camera footage when any of these holds, checked in order (the last one reads the file):
    its folder holds camera photos (a raw or heic, or a jpeg or derivative with a camera-style name); a camera
    sidecar shares its stem; its own stem is camera-style; a folder on its path is `DCIM`; ExifTool finds a
    Make or Model. `read_meta(paths)` returns metadata per readable path; an unreadable video is not camera.
    """
    photo_dirs: set[str] = set()
    siblings: set[tuple[str, str]] = set()
    for e in entries:
        p = PurePosixPath(e.rel_path)
        if e.kind in ("raw", "heic") or (e.kind in ("jpeg", "derivative") and camera_stem(p.stem)):
            photo_dirs.add(str(p.parent))
        if p.suffix.lower().removeprefix(".") in _CAMERA_SIBLINGS:
            siblings.add((str(p.parent), p.stem.split(".")[0].lower()))
    unsure: list[FileEntry] = []
    for e in entries:
        if e.kind != "video":
            continue
        p = PurePosixPath(e.rel_path)
        if (str(p.parent) in photo_dirs or (str(p.parent), p.stem.lower()) in siblings or camera_stem(p.stem)
                or any(part.upper() == "DCIM" for part in p.parts[:-1])):
            continue
        unsure.append(e)
    if not unsure:
        return []
    meta = read_meta([e.abs_path for e in unsure])
    return [e for e in unsure if not (meta.get(e.abs_path, {}).get("Make") or meta.get(e.abs_path, {}).get("Model"))]


def disk_used(mount: Path) -> int | None:
    """Used bytes on the drive at `mount`, or None when `mount` is a folder (the Mac folder, D-047), not a drive."""
    if not os.path.ismount(mount):
        return None
    try:
        return shutil.disk_usage(mount).used
    except OSError:
        return None


def usage_rows(entries: list[FileEntry], lrdata: list[Path], mount: Path,
               read_meta: Callable[[list[Path]], dict[Path, dict]]) -> dict[str, tuple[int, int]]:
    """`{category: (files, bytes)}` for one complete walk of a volume (`scan.walk_all`)."""
    rows: dict[str, list[int]] = {}

    def add(cat: str, files: int, size: int) -> None:
        r = rows.setdefault(cat, [0, 0])
        r[0] += files
        r[1] += size

    for e in entries:
        if e.kind == "ignored":
            add(category(PurePosixPath(e.rel_path).name), 1, e.size)
    if lrdata:
        add("lightroom", *tree_size(lrdata))
    for e in non_camera_videos(entries, read_meta):
        add("video_no_camera", 1, e.size)
    used = disk_used(mount)
    if used is not None:
        add(DISK_USED, 0, used)
    return {k: (v[0], v[1]) for k, v in rows.items()}


def summarize(tracked_bytes: int, tracked_files: int, rows: dict[str, tuple[int, int]],
              capacity: int | None) -> dict | None:
    """The drive summary's `usage`: photography, non-photo by category, unaccounted and free (bytes).

    `tracked_*` are the volume's instances that are not `missing`. Returns None before the first complete
    index of the volume with idx-13 (no rows yet).
    """
    if not rows:
        return None
    no_cam_files, no_cam_bytes = rows.get("video_no_camera", (0, 0))
    photo_files = tracked_files - no_cam_files + sum(rows.get(c, (0, 0))[0] for c in PHOTO_CATEGORIES)
    photo_bytes = tracked_bytes - no_cam_bytes + sum(rows.get(c, (0, 0))[1] for c in PHOTO_CATEGORIES)
    non_photo = [{"category": c, "files": rows[c][0], "bytes": rows[c][1]}
                 for c in NON_PHOTO_CATEGORIES if c in rows and rows[c][1] > 0]
    non_photo.sort(key=lambda r: -r["bytes"])
    photo_parts = [{"category": "tracked", "files": tracked_files - no_cam_files, "bytes": tracked_bytes - no_cam_bytes}]
    photo_parts += [{"category": c, "files": rows[c][0], "bytes": rows[c][1]} for c in PHOTO_CATEGORIES if c in rows]
    counted = photo_bytes + sum(r["bytes"] for r in non_photo)
    used = rows.get(DISK_USED, (0, None))[1]
    return {
        "photo": {"files": photo_files, "bytes": photo_bytes, "parts": photo_parts},
        "non_photo": {"files": sum(r["files"] for r in non_photo), "bytes": sum(r["bytes"] for r in non_photo),
                      "parts": non_photo},
        "unaccounted": max(used - counted, 0) if used is not None else None,
        "used": used,
        "free": max(capacity - used, 0) if used is not None and capacity else None,
        "capacity": capacity if used is not None else None,
    }
