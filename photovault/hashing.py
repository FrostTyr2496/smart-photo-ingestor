"""Hashing: full SHA-256 of file bytes and the cheap partial hash used to narrow candidates.

Identity is the full SHA-256 (vault-rules 5). The partial hash only groups or narrows candidates, so it may
collide; a full hash is always needed before a file is treated as a duplicate. See system-design §3 (hashing).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 8 * 1024 * 1024
"""Read size for streaming hashes (8 MiB)."""

_WINDOW = 64 * 1024
_HALF_WINDOW = 32 * 1024
_SMALL_FILE_LIMIT = 192 * 1024
_PARTIAL_TAG = b"pv1"


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of the whole file, read in CHUNK-sized reads."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def partial_sha256(path: Path) -> str:
    """Return a hex digest over the file size and three 64 KiB windows (or all bytes when small).

    Layout for files larger than 192 KiB: ``b"pv1"`` + size (8-byte big-endian) + the first 64 KiB + the 64 KiB
    starting at ``size // 2 - 32 KiB`` (clamped to 0) + the last 64 KiB. Files of 192 KiB or less hash their whole
    content after the same prefix.
    """
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(_PARTIAL_TAG)
    digest.update(size.to_bytes(8, "big"))
    with open(path, "rb") as fh:
        if size <= _SMALL_FILE_LIMIT:
            while True:
                block = fh.read(CHUNK)
                if not block:
                    break
                digest.update(block)
        else:
            windows = (
                (0, _WINDOW),
                (max(0, size // 2 - _HALF_WINDOW), _WINDOW),
                (size - _WINDOW, _WINDOW),
            )
            for offset, length in windows:
                fh.seek(offset)
                digest.update(fh.read(length))
    return digest.hexdigest()
