"""Real-file checks: every media file gets a capture time, and every Nikon file gets a serial and a shutter count.

Needs the local corpus (see conftest.py). Run with `uv run pytest tests/realfiles -q -m realfiles`.
"""

import pytest

from photovault.metadata import read_metadata

pytestmark = pytest.mark.realfiles

RAW_OR_JPEG_SUFFIXES = {".nef", ".jpg", ".jpeg"}


def _is_nikon(meta: dict) -> bool:
    return str(meta.get("Make", "")).upper().startswith("NIKON")


def test_every_media_file_has_a_capture_time(media_files):
    result = read_metadata(media_files, workers=4)
    missing = [p.name for p in media_files if "DateTimeOriginal" not in result.meta.get(p, {})]
    unread = {p.name: msg for p, msg in result.errors.items()}
    assert not missing, f"no capture time for {missing}; ExifTool errors: {unread}"


def test_every_nikon_file_has_serial_and_shutter_count(media_files):
    nikon_raw_or_jpeg = [p for p in media_files if p.suffix.lower() in RAW_OR_JPEG_SUFFIXES]
    if not nikon_raw_or_jpeg:
        pytest.skip("no NEF/JPG files in the corpus")
    result = read_metadata(nikon_raw_or_jpeg, workers=4)
    nikon = {p: m for p, m in result.meta.items() if _is_nikon(m)}
    assert nikon, "no Nikon files read; check the corpus and the exiftool install"
    no_serial = [p.name for p, m in nikon.items() if not m.get("CameraSerialNumber")]
    no_count = [p.name for p, m in nikon.items() if m.get("ShutterCount") is None]
    assert not no_serial, f"Nikon files without a serial: {no_serial}"
    assert not no_count, f"Nikon files without a shutter count: {no_count}"
