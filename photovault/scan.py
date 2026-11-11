"""Scanner and file kinds (system-design §8).

`walk` is read-only: it never writes, renames or deletes anything under `root`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

RAW_EXTS = frozenset({"nef", "nrw", "dng", "cr2", "cr3", "arw", "raf", "orf", "rw2"})
JPEG_EXTS = frozenset({"jpg", "jpeg"})
HEIC_EXTS = frozenset({"heic", "heif"})
VIDEO_EXTS = frozenset({"mov", "mp4"})
DERIVATIVE_EXTS = frozenset({"tif", "tiff", "psd", "psb"})
DERIVATIVE_STEM_SUFFIXES = ("-edit", "-hdr", "-pano", "-enhanced-nr")
SIDECAR_EXTS = frozenset({"xmp", "srt", "aae"})
PROXY_EXTS = frozenset({"lrv", "lrf"})

SKIP_DIR_NAMES = frozenset(
    {".Trashes", ".Spotlight-V100", ".fseventsd", "System Volume Information", "LOST.DIR"}
)
SKIP_DIR_SUFFIXES = (".lrdata",)


@dataclass(frozen=True)
class FileEntry:
    rel_path: str
    abs_path: Path
    size: int
    mtime_ns: int
    kind: str


def classify(name: str) -> str:
    """Return the §8 kind for a file name, judged by its lowercase extension and stem.

    Precedence: sidecar and proxy extensions, then derivative (by extension or by the
    stem suffix rule), then raw, jpeg, heic and video. Anything else is `ignored`.
    """
    path = Path(name)
    ext = path.suffix.lower().removeprefix(".")
    stem = path.stem.lower()
    if ext in SIDECAR_EXTS:
        return "sidecar"
    if ext in PROXY_EXTS:
        return "proxy"
    if ext in DERIVATIVE_EXTS or stem.endswith(DERIVATIVE_STEM_SUFFIXES):
        return "derivative"
    if ext in RAW_EXTS:
        return "raw"
    if ext in JPEG_EXTS:
        return "jpeg"
    if ext in HEIC_EXTS:
        return "heic"
    if ext in VIDEO_EXTS:
        return "video"
    return "ignored"


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIR_NAMES or name.endswith(SKIP_DIR_SUFFIXES)


def _raise(err: OSError) -> None:
    raise err


def walk(root: Path, *, include_ignored: bool = False) -> Iterator[FileEntry]:
    """Yield regular, non-empty, non-dot files under `root` in sorted `rel_path` order.

    Directories in SKIP_DIR_NAMES, `*.lrdata` and dot-directories are not descended.
    Files of kind `ignored` are yielded only when `include_ignored` is true.
    """
    found: list[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_raise):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                continue
            kind = classify(name)
            if kind == "ignored" and not include_ignored:
                continue
            found.append(
                FileEntry(
                    rel_path=path.relative_to(root).as_posix(),
                    abs_path=path,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    kind=kind,
                )
            )
    found.sort(key=lambda entry: entry.rel_path)
    yield from found
