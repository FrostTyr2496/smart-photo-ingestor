"""Metadata reading with ExifTool: batch reader, normalisation and capture-time precedence.

Ported from the v0 archive (`photo_ingest/analyzer.py`, branch `archive/v0-cli-2026-11-11`). The Pillow fallback,
the SQLite cache and the analytics code were dropped. See system-design §3 (metadata) and
`docs/legacy/handoff-v0.md` "Metadata reading".
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from exiftool import ExifToolHelper

logger = logging.getLogger(__name__)

# =============================================================================
# Metadata reading strategy (research + benchmarks, 2026-10-28; Nikon Z 6 NEF/JPG)
# =============================================================================
# ExifTool is the only reader. Pillow was dropped: it cannot read Nikon MakerNotes (no CameraSerialNumber,
# ShutterCount or clean lens name), and it opens a NEF as its 120x160 embedded thumbnail, so ImageWidth/
# ImageHeight are wrong for RAW. ExifTool returns all of these correctly.
#   - rawpy is NOT needed: it decodes pixels, not metadata.
# Throughput (258 real files on an external SSD, selected tags, wall clock):
#   one process, all tags 61 files/s | one process, selected tags 88/s |
#   4 processes 286/s | 8 processes 455/s | 12 processes no further gain (10 CPUs).
#   Chunk size (25 vs 100 vs all) made no difference; process startup is the fixed cost,
#   so keep exiftool processes alive for the whole run (never one process per file).
# DO NOT use `-fast2`: it is ~2x faster but skips MakerNotes, silently dropping
#   ShutterCount (verified on NEF). `-fast` is safe but gave no speedup here.
# Read-only: exiftool is only ever asked to read (no -overwrite/-delete), so this is safe on
#   card and drive sources.
# ExifTool key names (PyExifTool default common_args is `-G -n`): group0 prefixes
#   (`EXIF:`, `MakerNotes:`, `Composite:`, `File:`, `QuickTime:`) and numeric values (-n), so enum tags
#   such as Flash/MeteringMode arrive as ints and are mapped via the tables below.
# =============================================================================

# Explicit tag list (bare names; ExifTool returns every group's copy, e.g. EXIF:ISO and MakerNotes:ISO).
# Asking for specific tags is ~30% faster than reading everything.
EXIFTOOL_TAGS = [
    "Make", "Model", "LensMake", "LensModel", "LensID", "SerialNumber", "ShutterCount",
    "DateTimeOriginal", "CreateDate", "ISO", "FNumber", "ExposureTime", "FocalLength",
    "FocalLengthIn35mmFormat", "ExposureProgram", "MeteringMode", "Flash", "WhiteBalance",
    "ColorSpace", "ExposureCompensation", "Software", "Artist", "Copyright",
    "GPSLatitude", "GPSLongitude", "GPSAltitude", "ImageWidth", "ImageHeight",
    "SubSecTimeOriginal", "OffsetTimeOriginal", "ImageUniqueID", "ContentIdentifier", "CreationDate",
]
EXIFTOOL_CHUNK_SIZE = 200  # files per exiftool call; size bounds memory and progress granularity
EXIFTOOL_MAX_WORKERS = 8  # benchmark showed no gain past 8 (disk/CPU bound)

# EXIF enum code -> label.
EXPOSURE_PROGRAMS = {
    0: "Not defined", 1: "Manual", 2: "Normal program",
    3: "Aperture priority", 4: "Shutter priority", 5: "Creative program",
    6: "Action program", 7: "Portrait mode", 8: "Landscape mode",
}
METERING_MODES = {
    0: "Unknown", 1: "Average", 2: "Center-weighted average",
    3: "Spot", 4: "Multi-spot", 5: "Pattern", 6: "Partial",
}
FLASH_MODES = {
    0: "No flash", 1: "Flash fired", 5: "Flash fired, no return",
    7: "Flash fired, return detected", 9: "Flash fired, compulsory",
    16: "No flash, compulsory", 24: "No flash, auto", 25: "Flash fired, auto",
}
WHITE_BALANCE_MODES = {0: "Auto", 1: "Manual"}
COLOR_SPACES = {1: "sRGB", 65535: "Uncalibrated"}

# Capture-time sources (system-design §6 "Time sources"). Checked in this order.
TIME_SOURCE_EXIF = "exif"
TIME_SOURCE_QT_LOCAL = "quicktime_local"
TIME_SOURCE_QT_UTC = "quicktime_utc"

_EXIF_DT_RE = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?"
)


@dataclass
class MetadataResult:
    """Output of `read_metadata`: normalised metadata per readable path, and a message per unreadable path."""

    meta: dict[Path, dict] = field(default_factory=dict)
    errors: dict[Path, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _find_exiftool() -> str | None:
    """Locate the exiftool binary. Checks PATH, then Homebrew locations, because launchd/Hazel jobs run with a
    minimal PATH that omits /opt/homebrew/bin."""
    found = shutil.which("exiftool")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool"):
        if Path(candidate).exists():
            return candidate
    return None


def _clean_text(value: Any) -> str:
    """Strip whitespace and NUL padding (Nikon pads some strings with \\x00)."""
    return str(value).strip("\x00 \t\r\n")


def _parse_exif_datetime(value: Any) -> datetime | None:
    """Parse 'YYYY:MM:DD HH:MM:SS[.frac][offset]'. Returns the naive wall-clock time, the subseconds (str,
    digits only) and the offset normalised to '+HH:MM' / '-HH:MM' (or 'Z' -> '+00:00')."""
    if value in (None, ""):
        return None
    match = _EXIF_DT_RE.match(str(value).strip())
    if not match:
        return None
    y, mo, d, h, mi, s, frac, offset = match.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None


def _split_offset(value: Any) -> str | None:
    """Return the trailing offset of an ExifTool date string as '+HH:MM', or None."""
    if value in (None, ""):
        return None
    match = _EXIF_DT_RE.match(str(value).strip())
    if not match or not match.group(8):
        return None
    raw = match.group(8)
    if raw == "Z":
        return "+00:00"
    digits = raw[1:].replace(":", "")
    return f"{raw[0]}{digits[:2]}:{digits[2:4]}"


def _subseconds(value: Any, fraction_source: Any = None) -> str | None:
    """Two-digit subseconds: from an explicit SubSecTimeOriginal, else from the fractional seconds of a date."""
    if value not in (None, ""):
        digits = re.sub(r"\D", "", str(value))
        if digits:
            return digits[:2].ljust(2, "0")
    if fraction_source not in (None, ""):
        match = _EXIF_DT_RE.match(str(fraction_source).strip())
        if match and match.group(7):
            return match.group(7)[:2].ljust(2, "0")
    return None


def normalize_exiftool_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one ExifTool `-G -n` result row to the metadata schema.

    Returns {} when the row carries no usable data (e.g. an error-only row), so callers treat it as unreadable.
    Empty strings are omitted, so presence checks count only files that really have a value.

    Capture time (first source present wins; `CaptureTimeSource` says which):
      `EXIF:DateTimeOriginal` -> exif; `QuickTime:CreationDate` (has its own offset) -> quicktime_local;
      `QuickTime:CreateDate` (UTC by spec) -> quicktime_utc; `EXIF:CreateDate` -> exif.
    `DateTimeOriginal` is the naive wall-clock time (for quicktime_utc it is UTC). `OffsetTimeOriginal` is set
    only when the source states an offset. `SubSecTimeOriginal` is two digits.
    """

    def first(*keys):
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                return value
        return None

    def as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    metadata: dict[str, Any] = {}

    for out_key, keys in (
        ("Make", ("EXIF:Make",)),
        ("Model", ("EXIF:Model",)),
        ("LensMake", ("EXIF:LensMake",)),
        ("LensModel", ("EXIF:LensModel", "Composite:LensID")),
        # Nikon: EXIF:SerialNumber (also MakerNotes:SerialNumber). Needed for dedupe tier 2.
        ("CameraSerialNumber", ("EXIF:SerialNumber", "MakerNotes:SerialNumber")),
        ("Software", ("EXIF:Software",)),
        ("Artist", ("EXIF:Artist",)),
        ("Copyright", ("EXIF:Copyright",)),
    ):
        value = first(*keys)
        if value is not None:
            text = _clean_text(value)
            if text:
                metadata[out_key] = text

    shutter_count = as_float(first("MakerNotes:ShutterCount"))
    if shutter_count is not None:
        metadata["ShutterCount"] = int(shutter_count)

    _apply_capture_time(raw, first, metadata)

    iso = as_float(first("EXIF:ISO", "MakerNotes:ISO"))
    if iso is not None:
        metadata["ISO"] = int(iso)
    f_number = as_float(first("EXIF:FNumber"))
    if f_number is not None:
        metadata["FNumber"] = f_number
    focal = as_float(first("EXIF:FocalLength"))
    if focal is not None:
        metadata["FocalLength"] = focal
    focal35 = as_float(first("EXIF:FocalLengthIn35mmFormat"))
    if focal35 is not None:
        metadata["FocalLength35mm"] = int(focal35)
    # Decimal seconds (0.004), not "1/250": decimals sort and average directly.
    exposure = as_float(first("EXIF:ExposureTime"))
    if exposure:
        metadata["ExposureTime"] = exposure

    # With -n these enums arrive as ints.
    for out_key, key, table in (
        ("ExposureProgram", "EXIF:ExposureProgram", EXPOSURE_PROGRAMS),
        ("MeteringMode", "EXIF:MeteringMode", METERING_MODES),
        ("WhiteBalance", "EXIF:WhiteBalance", WHITE_BALANCE_MODES),
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            metadata[out_key] = table.get(int(value), f"Unknown ({int(value)})")
    flash = raw.get("EXIF:Flash")
    if isinstance(flash, (int, float)):
        metadata["Flash"] = FLASH_MODES.get(int(flash), f"Flash mode {int(flash)}")
    # On NEFs ColorSpace exists only in MakerNotes; on JPEGs it is in EXIF.
    color_space = as_float(first("EXIF:ColorSpace", "MakerNotes:ColorSpace"))
    if color_space is not None:
        metadata["ColorSpace"] = COLOR_SPACES.get(int(color_space), f"Unknown ({int(color_space)})")
    bias = as_float(first("EXIF:ExposureCompensation"))
    if bias is not None:
        metadata["ExposureBias"] = f"{bias:+.1f} EV"

    content_id = first("QuickTime:ContentIdentifier", "MakerNotes:ContentIdentifier")
    if content_id is not None:
        text = _clean_text(content_id)
        if text:
            metadata["ContentIdentifier"] = text

    lat = as_float(first("Composite:GPSLatitude", "EXIF:GPSLatitude"))
    lon = as_float(first("Composite:GPSLongitude", "EXIF:GPSLongitude"))
    if lat is not None and lon is not None:
        metadata["GPSLatitude"] = lat
        metadata["GPSLongitude"] = lon
        altitude = as_float(first("Composite:GPSAltitude", "EXIF:GPSAltitude"))
        if altitude is not None:
            metadata["GPSAltitude"] = altitude

    # NOTE: for a NEF, EXIF:ImageWidth/Height is the raw sensor area (Z 6: 6064x4040) while the paired JPEG is
    # the output size (6048x4024). Do not compare them for equality.
    width = as_float(first("EXIF:ImageWidth", "File:ImageWidth"))
    height = as_float(first("EXIF:ImageHeight", "File:ImageHeight"))
    if width is not None:
        metadata["ImageWidth"] = int(width)
    if height is not None:
        metadata["ImageHeight"] = int(height)

    return metadata


def _apply_capture_time(raw: dict[str, Any], first: Callable[..., Any], metadata: dict[str, Any]) -> None:
    """Set DateTimeOriginal, CaptureTimeSource, OffsetTimeOriginal and SubSecTimeOriginal from the first usable
    source. Leaves them unset when no source parses."""
    exif_sub = first("EXIF:SubSecTimeOriginal")
    exif_offset = first("EXIF:OffsetTimeOriginal")

    candidates = (
        ("EXIF:DateTimeOriginal", TIME_SOURCE_EXIF),
        ("QuickTime:CreationDate", TIME_SOURCE_QT_LOCAL),
        ("QuickTime:CreateDate", TIME_SOURCE_QT_UTC),
        ("EXIF:CreateDate", TIME_SOURCE_EXIF),
    )
    for key, source in candidates:
        value = raw.get(key)
        when = _parse_exif_datetime(value)
        if when is None:
            continue
        metadata["DateTimeOriginal"] = when
        metadata["CaptureTimeSource"] = source
        if source == TIME_SOURCE_EXIF:
            offset = _clean_text(exif_offset) if exif_offset not in (None, "") else _split_offset(value)
            sub = _subseconds(exif_sub, value)
        elif source == TIME_SOURCE_QT_LOCAL:
            offset = _split_offset(value)
            sub = _subseconds(None, value)
        else:  # quicktime_utc: the value is UTC, so no local offset is stated
            offset = None
            sub = _subseconds(None, value)
        if offset:
            metadata["OffsetTimeOriginal"] = offset
        if sub:
            metadata["SubSecTimeOriginal"] = sub
        return


def read_metadata(
    paths: list[Path],
    *,
    workers: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> MetadataResult:
    """Read and normalise metadata for `paths` with long-lived ExifTool processes.

    Every input path ends up in exactly one of `meta` or `errors`. A missing exiftool binary puts every path into
    `errors` with "exiftool not found" and does not raise. `progress(done, total)` is called after each chunk.
    The paths are read only; nothing is written.
    """
    paths = [Path(p) for p in paths]
    result = MetadataResult()
    total = len(paths)
    if total == 0:
        return result

    executable = _find_exiftool()
    if executable is None:
        result.errors = {p: "exiftool not found" for p in paths}
        return result

    wanted_workers = max(1, min(workers, EXIFTOOL_MAX_WORKERS))
    # Shrink chunks for small runs so every worker gets one. Floor of 10 keeps per-call overhead negligible.
    chunk_size = max(10, min(EXIFTOOL_CHUNK_SIZE, -(-total // wanted_workers)))
    chunks = [paths[i : i + chunk_size] for i in range(0, total, chunk_size)]
    worker_count = min(wanted_workers, len(chunks))

    work: queue.Queue[list[Path]] = queue.Queue()
    for chunk in chunks:
        work.put(chunk)
    out: queue.Queue = queue.Queue()
    start_errors: list[str] = []

    def worker() -> None:
        try:
            with ExifToolHelper(executable=executable, check_execute=False) as et:
                while True:
                    try:
                        chunk = work.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        rows = et.get_tags([str(p) for p in chunk], EXIFTOOL_TAGS)
                        chunk_error = None
                    except Exception as exc:  # one bad chunk must not stop the run
                        rows = []
                        chunk_error = f"exiftool chunk failed: {exc}"
                    by_source = {row.get("SourceFile"): row for row in rows}
                    parsed: dict[Path, dict] = {}
                    errors: dict[Path, str] = {}
                    for path in chunk:
                        row = by_source.get(str(path))
                        meta = normalize_exiftool_row(row) if row else {}
                        if meta:
                            parsed[path] = meta
                        else:
                            errors[path] = chunk_error or "exiftool returned no usable metadata"
                    out.put((parsed, errors))
        except Exception as exc:  # exiftool failed to start; leftover chunks go to other workers
            logger.debug("exiftool worker stopped: %s", exc)
            start_errors.append(str(exc))
        finally:
            out.put(None)  # sentinel: this worker is finished

    threads =[threading.Thread(target=worker, daemon=True) for _ in range(worker_count)]
    for thread in threads:
        thread.start()

    finished = 0
    done = 0
    while finished < worker_count:
        item = out.get()
        if item is None:
            finished += 1
            continue
        parsed, errors = item
        result.meta.update(parsed)
        result.errors.update(errors)
        done += len(parsed) + len(errors)
        if progress and (parsed or errors):
            progress(done, total)
    for thread in threads:
        thread.join()

    # Paths nobody reported: every worker failed to launch exiftool, so their chunks were never read.
    reason = f"exiftool did not start: {start_errors[0]}" if start_errors else "not read"
    for path in paths:
        if path not in result.meta and path not in result.errors:
            result.errors[path] = reason
    return result
