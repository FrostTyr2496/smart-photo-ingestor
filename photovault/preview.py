"""Embedded-preview luminance, used in Phase 4 to confirm dark/bias and flat sequence candidates (seq-3).

This is the only place the engine looks at pixels (vault-rules 9), and only the small JPEG preview that the camera
embeds in a RAW file: `exiftool -b -PreviewImage`, then `-JpgFromRaw` as a fallback. The RAW itself is never
decoded. ExifTool is only ever asked to read (no write flags), so this is safe on card and drive sources.

Error choices:
- `luma_of_jpeg` raises `ValueError` when the bytes are not a decodable image (a specific, catchable error).
- `extract_preview` returns None when no preview exists, or when exiftool is missing or fails.
- `preview_luma` never raises on bad data: any failure becomes None, meaning "unknown, do not confirm".
"""

from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

from photovault.metadata import _find_exiftool

logger = logging.getLogger(__name__)

THUMB_SIZE = 64  # longest side of the thumbnail the luminance is measured on
PREVIEW_TAGS = ("PreviewImage", "JpgFromRaw")  # tried in this order
EXIFTOOL_TIMEOUT_S = 30
JPEG_MAGIC = b"\xff\xd8"

# Thresholds (sequences spec, seq-3). Luma is 0-255 (L channel of the thumbnail).
DARK_LUMA_MAX = 8.0  # a dark/bias candidate is confirmed when mean luma is below this
FLAT_LUMA_MIN = 100.0  # a flat candidate is confirmed when mean luma is above this ...
FLAT_STDDEV_MAX = 25.0  # ... and the luma standard deviation is below this ("low variance")


def luma_stats(data: bytes) -> tuple[float, float]:
    """Return `(mean, stddev)` of L (0-255) over a 64-px thumbnail of the JPEG in `data`.

    Raises `ValueError` if `data` is empty or cannot be decoded as an image.
    """
    if not data:
        raise ValueError("empty image data")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.draft("L", (THUMB_SIZE, THUMB_SIZE))  # JPEG decodes at reduced size; a no-op for other formats
            gray = img.convert("L")
            gray.thumbnail((THUMB_SIZE, THUMB_SIZE))
            stat = ImageStat.Stat(gray)
            return float(stat.mean[0]), float(stat.stddev[0])
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a decodable image: {exc}") from exc


def luma_of_jpeg(data: bytes) -> float:
    """Mean L of a 64-px thumbnail of the JPEG in `data`, from 0 (black) to 255 (white).

    Raises `ValueError` if `data` is not a decodable image.
    """
    return luma_stats(data)[0]


def is_dark(luma: float) -> bool:
    """True when the luma is dark enough to confirm a dark/bias candidate (luma < 8)."""
    return luma < DARK_LUMA_MAX


def is_flat(luma: float, stddev: float) -> bool:
    """True when the preview is bright and even enough to confirm a flat candidate (luma > 100, low variance)."""
    return luma > FLAT_LUMA_MIN and stddev < FLAT_STDDEV_MAX


def extract_preview(path: Path) -> bytes | None:
    """Return the embedded JPEG preview of `path`, or None when there is none.

    Runs `exiftool -b -PreviewImage`, then `-JpgFromRaw` if the first gave nothing usable. Read-only. A missing
    exiftool binary, a failing run or a timeout also give None (logged at debug level).
    """
    executable = _find_exiftool()
    if executable is None:
        logger.debug("exiftool not found; no preview for %s", path)
        return None
    target = str(Path(path).resolve())  # absolute, so a name can never be read as an option
    for tag in PREVIEW_TAGS:
        try:
            proc = subprocess.run(
                [executable, "-b", f"-{tag}", target],
                capture_output=True,
                timeout=EXIFTOOL_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("exiftool -%s failed for %s: %s", tag, path, exc)
            continue
        if proc.returncode == 0 and proc.stdout.startswith(JPEG_MAGIC):
            return proc.stdout
    return None


def preview_luma(path: Path) -> float | None:
    """Mean luma (0-255) of the embedded preview of `path`, or None if it has no usable preview."""
    data = extract_preview(path)
    if data is None:
        return None
    try:
        return luma_of_jpeg(data)
    except ValueError as exc:
        logger.debug("unusable preview in %s: %s", path, exc)
        return None
