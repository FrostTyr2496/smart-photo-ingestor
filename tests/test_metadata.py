"""Metadata reader: normalising synthetic ExifTool rows, the error row, the missing-binary path, and a realfiles check."""

from datetime import datetime
from pathlib import Path

import pytest

import photovault.metadata as md
from photovault.metadata import EXIFTOOL_TAGS, MetadataResult, normalize_exiftool_row, read_metadata

CORPUS = Path(__file__).parent.parent / "testdata" / "corpus"


def test_nikon_nef_row_serial_shutter_subsec_and_exif_time():
    raw = {
        "SourceFile": "/card/DCIM/100NCZ6/DSC_0781.NEF",
        "EXIF:Make": "NIKON CORPORATION",
        "EXIF:Model": "NIKON Z 6",
        "EXIF:SerialNumber": "  0000000  \x00\x00",  # synthetic padded serial
        "MakerNotes:ShutterCount": 84022,
        "EXIF:DateTimeOriginal": "2026:10:27 18:14:42",
        "EXIF:SubSecTimeOriginal": "81",
        "EXIF:OffsetTimeOriginal": "-04:00",
        "EXIF:ISO": 400,
        "EXIF:FNumber": 4.0,
        "EXIF:ExposureTime": 0.004,
        "EXIF:Flash": 16,
        "EXIF:ImageWidth": 6064,
        "EXIF:ImageHeight": 4040,
    }
    meta = normalize_exiftool_row(raw)
    assert meta["Make"] == "NIKON CORPORATION"
    assert meta["Model"] == "NIKON Z 6"
    assert meta["CameraSerialNumber"] == "0000000"
    assert meta["ShutterCount"] == 84022
    assert meta["DateTimeOriginal"] == datetime(2026, 10, 27, 18, 14, 42)
    assert meta["CaptureTimeSource"] == "exif"
    assert meta["SubSecTimeOriginal"] == "81"
    assert meta["OffsetTimeOriginal"] == "-04:00"
    assert meta["ISO"] == 400
    assert meta["ExposureTime"] == 0.004
    assert meta["Flash"] == "No flash, compulsory"
    assert (meta["ImageWidth"], meta["ImageHeight"]) == (6064, 4040)


def test_iphone_mov_quicktime_creation_date_has_local_offset():
    raw = {
        "SourceFile": "/card/DCIM/IMG_0001.MOV",
        "EXIF:Make": "Apple",
        "EXIF:Model": "iPhone 14 Pro",
        "QuickTime:CreationDate": "2026:10:27 13:02:07.45-05:00",
        "QuickTime:ContentIdentifier": "AAAA-BBBB-SYNTHETIC",
    }
    meta = normalize_exiftool_row(raw)
    assert meta["DateTimeOriginal"] == datetime(2026, 10, 27, 13, 2, 7)
    assert meta["CaptureTimeSource"] == "quicktime_local"
    assert meta["OffsetTimeOriginal"] == "-05:00"
    assert meta["SubSecTimeOriginal"] == "45"  # from the fraction
    assert meta["ContentIdentifier"] == "AAAA-BBBB-SYNTHETIC"


def test_dji_jpg_uses_exif_datetime_without_subseconds():
    raw = {
        "SourceFile": "/card/DCIM/DJI_0250.JPG",
        "EXIF:Make": "DJI",
        "EXIF:Model": "FC3411",
        "EXIF:DateTimeOriginal": "2026:10:28 06:41:03",
    }
    meta = normalize_exiftool_row(raw)
    assert meta["Model"] == "FC3411"
    assert meta["CaptureTimeSource"] == "exif"
    assert meta["DateTimeOriginal"] == datetime(2026, 10, 28, 6, 41, 3)
    assert "SubSecTimeOriginal" not in meta
    assert "OffsetTimeOriginal" not in meta


def test_gopro_mp4_quicktime_create_date_is_utc():
    raw = {
        "SourceFile": "/card/DCIM/100GOPRO/GX010020.MP4",
        "QuickTime:CreateDate": "2026:10:27 23:02:07",
    }
    meta = normalize_exiftool_row(raw)
    assert meta["CaptureTimeSource"] == "quicktime_utc"
    assert meta["DateTimeOriginal"] == datetime(2026, 10, 27, 23, 2, 7)
    assert "OffsetTimeOriginal" not in meta  # UTC by spec, no local offset stated


def test_precedence_prefers_exif_over_quicktime():
    raw = {
        "EXIF:DateTimeOriginal": "2026:10:27 18:00:00",
        "QuickTime:CreationDate": "2026:10:27 19:00:00-04:00",
    }
    assert normalize_exiftool_row(raw)["CaptureTimeSource"] == "exif"


def test_exif_create_date_is_last_resort():
    raw = {"EXIF:CreateDate": "2026:10:27 10:10:10"}
    meta = normalize_exiftool_row(raw)
    assert meta["CaptureTimeSource"] == "exif"
    assert meta["DateTimeOriginal"] == datetime(2026, 10, 27, 10, 10, 10)


def test_error_only_row_normalises_to_empty():
    assert normalize_exiftool_row({"SourceFile": "/x/y.NEF", "ExifTool:Error": "File format error"}) == {}
    assert normalize_exiftool_row({}) == {}


def test_empty_strings_are_omitted():
    meta = normalize_exiftool_row({"EXIF:Make": "", "EXIF:Artist": "   ", "EXIF:Model": "Z 6"})
    assert "Make" not in meta
    assert "Artist" not in meta
    assert meta["Model"] == "Z 6"


def test_tag_list_includes_the_new_fields():
    for tag in ("SubSecTimeOriginal", "OffsetTimeOriginal", "ImageUniqueID", "ContentIdentifier", "CreationDate"):
        assert tag in EXIFTOOL_TAGS


def test_missing_exiftool_puts_every_path_in_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(md, "_find_exiftool", lambda: None)
    paths = [tmp_path / "a.NEF", tmp_path / "b.JPG"]
    result = read_metadata(paths, workers=2)
    assert isinstance(result, MetadataResult)
    assert result.meta == {}
    assert result.errors == {p: "exiftool not found" for p in paths}


def test_unreadable_file_goes_to_errors_with_real_exiftool(tmp_path):
    """Runs the real binary on a non-image: the path must land in errors and nothing may raise."""
    if md._find_exiftool() is None:
        pytest.skip("exiftool binary not installed")
    bad = tmp_path / "not_a_photo.JPG"
    bad.write_bytes(b"this is not an image")
    result = read_metadata([bad], workers=1)
    assert bad not in result.meta
    assert bad in result.errors
    assert result.errors[bad]


def test_empty_input_returns_empty_result(monkeypatch):
    monkeypatch.setattr(md, "_find_exiftool", lambda: None)
    result = read_metadata([])
    assert result.meta == {} and result.errors == {}


@pytest.mark.realfiles
def test_real_nef_reads_model_serial_shutter_and_time():
    nefs = sorted(CORPUS.glob("**/*.NEF")) if CORPUS.exists() else []
    if not nefs:
        pytest.skip("no NEF files under testdata/corpus")
    result = read_metadata(nefs[:5], workers=2)
    assert result.meta, f"nothing read; errors: {result.errors}"
    for meta in result.meta.values():
        assert meta.get("Model")
        assert meta.get("CameraSerialNumber")
        assert meta.get("ShutterCount") is not None
        assert meta.get("DateTimeOriginal") is not None
