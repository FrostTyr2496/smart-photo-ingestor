"""Verified copy engine (ing-2): the only module that writes media bytes (vault-rules 3).

`copy_verified` writes `.<name>.pvpartial`, syncs, re-reads it uncached, compares SHA-256, then renames.
It never overwrites an existing file and never writes xattrs (no AppleDouble files on exFAT, vault-rules 8).
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from photovault.hashing import CHUNK, partial_sha256

F_NOCACHE = getattr(fcntl, "F_NOCACHE", 48)


@dataclass(frozen=True)
class CopyResult:
    dst: Path
    size: int
    sha256: str
    partial_sha256: str
    sync_method: str


class CopyError(Exception):
    """A copy could not be verified. No file is left at `dst`."""


def _sync(fd: int) -> str:
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        return "F_FULLFSYNC"
    except (OSError, AttributeError):
        os.fsync(fd)
        return "fsync"


def _hash_uncached(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.fcntl(fd, F_NOCACHE, 1)
        except OSError:
            pass
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            digest.update(block)
    finally:
        os.close(fd)
    return digest.hexdigest()


def copy_verified(src: Path, dst: Path, *, mtime_ns: int | None = None) -> CopyResult:
    """Copy `src` to `dst` with a verified read-back. Raises `FileExistsError` if `dst` exists."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        raise FileExistsError(f"destination exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.pvpartial"
    if tmp.exists():
        tmp.unlink()

    src_stat = os.stat(src)
    digest = hashlib.sha256()
    size = 0
    try:
        out = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            with open(src, "rb") as fh:
                while True:
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    digest.update(block)
                    view = memoryview(block)
                    while view:
                        written = os.write(out, view)
                        view = view[written:]
                    size += len(block)
            sync_method = _sync(out)
        finally:
            os.close(out)

        expected = digest.hexdigest()
        actual = _hash_uncached(tmp)
        if actual != expected:
            raise CopyError(f"verify mismatch for {src} -> {dst}: wrote {expected}, read back {actual}")
        os.utime(tmp, ns=(src_stat.st_atime_ns, mtime_ns if mtime_ns is not None else src_stat.st_mtime_ns))
        if dst.exists():
            raise FileExistsError(f"destination appeared during copy: {dst}")
        os.rename(tmp, dst)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise

    try:
        dfd = os.open(dst.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return CopyResult(dst=dst, size=size, sha256=expected, partial_sha256=partial_sha256(dst), sync_method=sync_method)
